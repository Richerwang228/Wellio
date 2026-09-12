import {afterEach, describe, expect, it, vi} from 'vitest'
import {ProxiedCopilotRuntimeAgent} from '@copilotkit/core'
import {createCopilotTransport} from '../src/lib/copilot-transport'
import {api, ApiError, registerChatTransport} from '../src/lib/api-client'
import {createFixture} from '../src/lib/fixtures'
import type {ChatEvent, ChatRequest, Message} from '../src/lib/contracts'

const request:ChatRequest={requestId:'sdk-request-1',resetEpoch:1,conversationId:'sdk-conversation',message:'Record lunch',locale:'en',attachmentIds:[],source:'user'}
const envelope={requestId:request.requestId,resetEpoch:request.resetEpoch}
const message:Message={id:'reply-sdk',role:'assistant',content:'',createdAt:'2026-09-12T08:00:00Z',source:'agent',status:'streaming',steps:[]}
const start=(input=request)=>({type:'RUN_STARTED',runId:input.requestId,threadId:input.conversationId})
const finish=(input=request)=>({type:'RUN_FINISHED',runId:input.requestId,threadId:input.conversationId})
const custom=(value:unknown)=>({type:'CUSTOM',name:'wellio',value})
const done:ChatEvent={type:'done',...envelope,messageId:message.id}
const snapshot=()=>({...createFixture(),conversationId:request.conversationId})
const sse=(events:unknown[])=>new Response(events.map(event=>`data: ${JSON.stringify(event)}\n\n`).join(''),{headers:{'Content-Type':'text/event-stream'}})
function setup(events:unknown[]){
 const fetch=vi.fn().mockImplementation(async()=>sse(events))
 const agent=new ProxiedCopilotRuntimeAgent({agentId:'wellio',runtimeUrl:'/api/copilotkit',transport:'rest',credentials:'same-origin',fetch})
 const transport=createCopilotTransport(agent)
 return {agent,transport,fetch}
}
function deferred(){let resolve!:()=>void;const promise=new Promise<void>(yes=>{resolve=yes});return {promise,resolve}}
afterEach(()=>{vi.useRealTimers();vi.restoreAllMocks();vi.unstubAllGlobals()})

describe('Real CopilotKit runtime agent transport',()=>{
 it.each(['firstEvent','idle','total'] as const)('bounds the %s wait and frees the SDK for another run',async kind=>{
  vi.useFakeTimers()
  vi.stubGlobal('fetch',vi.fn().mockImplementation(()=>new Promise(()=>{})))
  const {agent,fetch}=setup([])
  const transport=createCopilotTransport(agent,{firstEvent:20,idle:20,total:50,cleanup:10})
  let heartbeat:ReturnType<typeof setInterval>|undefined
  fetch.mockImplementationOnce(async()=>kind==='firstEvent'?new Promise(()=>{}):new Response(new ReadableStream({
   start(stream){
    const write=(value:unknown)=>stream.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(value)}\n\n`))
    write(start())
    if(kind==='total')heartbeat=setInterval(()=>write({type:'CUSTOM',name:'heartbeat',value:{}}),5)
   },
  }),{headers:{'Content-Type':'text/event-stream'}}))
  try{
   const outcome=transport.chat(request,vi.fn(),new AbortController().signal).catch(error=>error)
   await vi.advanceTimersByTimeAsync(kind==='total'?55:25)
   expect(await outcome).toMatchObject({code:'TIMEOUT'})
   expect(agent.abortController.signal.aborted).toBe(true)
   expect(agent.isRunning).toBe(false)
   fetch.mockImplementationOnce(async()=>sse([start(),custom(done),finish()]))
   const next=transport.chat(request,vi.fn(),new AbortController().signal)
   await vi.advanceTimersByTimeAsync(1)
   await expect(next).resolves.toBeUndefined()
  }finally{clearInterval(heartbeat);await transport.dispose()}
 })

 it('finishes after both terminal events even when the HTTP body stays open',async()=>{
  const {agent,transport,fetch}=setup([])
  fetch.mockImplementationOnce(async()=>new Response(new ReadableStream({
   start(stream){stream.enqueue(new TextEncoder().encode([start(),custom(done),finish()].map(event=>`data: ${JSON.stringify(event)}\n\n`).join('')))},
  }),{headers:{'Content-Type':'text/event-stream'}}))
  await transport.chat(request,vi.fn(),new AbortController().signal)
  expect(agent.abortController.signal.aborted).toBe(true)
  expect(agent.isRunning).toBe(false)
 })

 it('releases Stop within its cleanup budget even if a SDK finalizer never answers',async()=>{
  vi.useFakeTimers()
  vi.stubGlobal('fetch',vi.fn().mockImplementation(()=>new Promise(()=>{})))
  const {agent,fetch}=setup([]),gate=deferred(),controller=new AbortController()
  const transport=createCopilotTransport(agent,{cleanup:10})
  fetch.mockImplementationOnce(()=>new Promise(()=>{}))
  const subscription=agent.subscribe({onRunFinalized:()=>gate.promise})
  const outcome=transport.chat(request,vi.fn(),controller.signal).catch(error=>error)
  await vi.advanceTimersByTimeAsync(1)
  controller.abort();await vi.advanceTimersByTimeAsync(11)
  expect(await outcome).toMatchObject({name:'AbortError'})
  await expect(transport.chat(request,vi.fn(),new AbortController().signal)).rejects.toMatchObject({name:'AbortError'})
  gate.resolve();subscription.unsubscribe();await transport.dispose()
 })

 it('runs the SDK with server-bound identifiers and validated Wellio events, without replaying browser state',async()=>{
  const events:ChatEvent[]=[
   {type:'message',...envelope,message},
   {type:'phase',...envelope,messageId:message.id,phase:'thinking'},
   {type:'tool',...envelope,messageId:message.id,step:{id:'step-context',toolCallId:'call-context',operation:'context',status:'succeeded'}},
   {type:'tool',...envelope,messageId:message.id,step:{id:'step-knowledge',toolCallId:'call-knowledge',operation:'knowledge_search',status:'succeeded'}},
   {type:'text',...envelope,messageId:message.id,delta:'Saved lunch.'},
   {type:'snapshot',...envelope,snapshot:snapshot()},done,
  ]
  const {agent,transport,fetch}=setup([start(),{type:'STATE_SNAPSHOT',snapshot:{wellio:snapshot()}},{type:'CUSTOM',name:'unrelated',value:{ignored:true}},...events.map(custom),finish()])
  agent.setState({forgedAuthorization:'never forwarded'})
  agent.setMessages([{id:'forged-source',role:'user',content:'Never replay this browser history'}])
  const emit=vi.fn(),unregister=registerChatTransport(transport.chat)
  try{await api.chat(request,emit,new AbortController().signal)}finally{unregister()}
  expect(fetch).toHaveBeenCalledTimes(1)
  const [url,init]=fetch.mock.calls[0]
  expect(url).toBe('/api/copilotkit/agent/wellio/run')
  expect(init.credentials).toBe('same-origin')
  expect(JSON.parse(init.body)).toMatchObject({runId:request.requestId,threadId:request.conversationId,forwardedProps:{wellio:request},state:{},messages:[],tools:[],context:[]})
  expect(emit.mock.calls.map(call=>call[0])).toEqual(events)
  expect(agent.state).toEqual({wellio:snapshot()})
  expect(agent.isRunning).toBe(false)
  const second={...request,requestId:'sdk-request-2',conversationId:'reset-conversation',resetEpoch:2}
  fetch.mockImplementationOnce(async()=>sse([start(second),custom({...done,requestId:second.requestId,resetEpoch:2}),finish(second)]))
  await transport.chat(second,vi.fn(),new AbortController().signal)
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toMatchObject({threadId:second.conversationId,state:{},messages:[],forwardedProps:{wellio:second}})
  await transport.dispose()
 })

 it('accepts the authoritative app-open check snapshot and matching control result',async()=>{
  const input={...request,message:'',source:'app_open' as const,checkMode:'auto' as const}
  const saved={...snapshot(),readinessCheck:{key:'check-sdk',status:'completed' as const}}
  const values:ChatEvent[]=[{type:'snapshot',...envelope,snapshot:saved},{type:'check_result',...envelope,checkKey:'check-sdk',outcome:'reused'}]
  const {transport}=setup([start(),...values.map(custom),finish()]),emit=vi.fn()
  await transport.chat(input,emit,new AbortController().signal)
  expect(emit.mock.calls.map(call=>call[0])).toEqual(values)
 })

 it.each(['PROVIDER_NOT_CONFIGURED','SESSION_RESET','RUN_IN_PROGRESS'])('preserves preflight RUN_ERROR code %s with no RUN_STARTED',async code=>{
  vi.spyOn(console,'error').mockImplementation(()=>{})
  const {transport}=setup([{type:'RUN_ERROR',message:code,code}]),emit=vi.fn()
  await expect(transport.chat(request,emit,new AbortController().signal)).rejects.toMatchObject({code})
  expect(emit).not.toHaveBeenCalled()
 })

 it('delivers a domain error and rejects the run without manufacturing success',async()=>{
  vi.spyOn(console,'error').mockImplementation(()=>{})
  const error:ChatEvent={type:'error',...envelope,messageId:message.id,errorCode:'TOOL_LIMIT_EXCEEDED'}
  const {transport}=setup([start(),custom(error),{type:'RUN_ERROR',message:'Provider detail',code:'PROVIDER_ERROR'}]),emit=vi.fn()
  await expect(transport.chat(request,emit,new AbortController().signal)).rejects.toThrow('TOOL_LIMIT_EXCEEDED')
  expect(emit).toHaveBeenCalledExactlyOnceWith(error)
 })

 it('retains an HTTP preflight status and public error code',async()=>{
  vi.spyOn(console,'error').mockImplementation(()=>{})
  const {transport,fetch}=setup([])
  fetch.mockResolvedValueOnce(new Response(JSON.stringify({errorCode:'PROVIDER_NOT_CONFIGURED'}),{status:503,headers:{'Content-Type':'application/json'}}))
  await expect(transport.chat(request,vi.fn(),new AbortController().signal)).rejects.toMatchObject({code:'PROVIDER_NOT_CONFIGURED',status:503})
 })

 it.each([
  ['foreign request',[{...done,requestId:'foreign'}]],
  ['foreign epoch',[{...done,resetEpoch:2}]],
  ['missing terminal message ID',[{type:'done',...envelope}]],
  ['unknown event field',[{...done,source:'user'}]],
  ['invalid message',[{type:'message',...envelope,message:{...message,status:'invented'}}]],
  ['invalid tool step',[{type:'tool',...envelope,messageId:message.id,step:{id:'step',toolCallId:'call',operation:'unknown',status:'succeeded'}}]],
  ['foreign snapshot conversation',[{type:'snapshot',...envelope,snapshot:{...snapshot(),conversationId:'foreign'}}]],
  ['foreign nested epoch',[{type:'snapshot',...envelope,snapshot:{...snapshot(),resetEpoch:2}}]],
  ['malformed nested snapshot',[{type:'snapshot',...envelope,snapshot:{...snapshot(),workout:{id:'broken'}}}]],
  ['control result on user run',[{type:'snapshot',...envelope,snapshot:{...snapshot(),readinessCheck:{key:'check-sdk',status:'completed'}}},{type:'check_result',...envelope,checkKey:'check-sdk',outcome:'reused'}]],
  ['data after terminal',[done,{type:'text',...envelope,messageId:message.id,delta:'late'}]],
 ] as [string,unknown[]][])('rejects %s',async(_label,events)=>{
  vi.spyOn(console,'error').mockImplementation(()=>{})
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(null,{status:200})))
  const {transport}=setup([start(),...events.map(custom),finish()])
  await expect(transport.chat(request,vi.fn(),new AbortController().signal)).rejects.toThrow('STREAM_PROTOCOL_ERROR')
 })

 it.each([undefined,'different-check'])('rejects a check result without a matching authoritative snapshot (%s)',async key=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(null,{status:200})))
  const events=key?[custom({type:'snapshot',...envelope,snapshot:{...snapshot(),readinessCheck:{key,status:'completed'}}})]:[]
  const {transport}=setup([start(),...events,custom({type:'check_result',...envelope,checkKey:'check-sdk',outcome:'reused'}),finish()])
  await expect(transport.chat({...request,source:'app_open'},vi.fn(),new AbortController().signal)).rejects.toThrow('STREAM_PROTOCOL_ERROR')
 })

 it.each([
  ['EMPTY_RESPONSE',[start(),finish()]],
  ['INCOMPLETE_RESPONSE',[start(),custom({type:'message',...envelope,message}),finish()]],
  ['INCOMPLETE_RESPONSE',[start(),custom(done)]],
 ] as [string,unknown[]][])('requires both business and SDK terminal events: %s',async(code,events)=>{
  const {transport}=setup(events)
  await expect(transport.chat(request,vi.fn(),new AbortController().signal)).rejects.toThrow(code)
 })

 it('calls real SDK stop and aborts its HTTP stream, awaiting finalization before releasing the next run',async()=>{
  const ready=deferred(),finalizerEntered=deferred(),finalizerGate=deferred(),controller=new AbortController()
  const stopFetch=vi.fn().mockResolvedValue(new Response(null,{status:200}))
  vi.stubGlobal('fetch',stopFetch)
  const {agent,transport,fetch}=setup([])
  fetch.mockImplementationOnce(async(_url:string,init:RequestInit)=>new Response(new ReadableStream({
   start(stream){stream.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(start())}\n\n`));init.signal!.addEventListener('abort',()=>stream.error(new DOMException('Aborted','AbortError')),{once:true});ready.resolve()},
  }),{headers:{'Content-Type':'text/event-stream'}}))
  const subscription=agent.subscribe({onRunFinalized:async()=>{finalizerEntered.resolve();await finalizerGate.promise}})
  let settled=false
  const result=transport.chat(request,vi.fn(),controller.signal).catch(error=>error).finally(()=>{settled=true})
  await ready.promise
  controller.abort()
  await finalizerEntered.promise
  expect(agent.abortController.signal.aborted).toBe(true)
  expect(stopFetch).toHaveBeenCalledWith('http://localhost/api/copilotkit/agent/wellio/stop/sdk-conversation',expect.objectContaining({method:'POST',credentials:'same-origin'}))
  expect(settled).toBe(false)
  await expect(transport.chat(request,vi.fn(),new AbortController().signal)).rejects.toThrow('RUN_IN_PROGRESS')
  finalizerGate.resolve()
  expect(await result).toMatchObject({name:'AbortError'})
  subscription.unsubscribe()
  fetch.mockImplementationOnce(async()=>sse([start(),custom(done),finish()]))
  await expect(transport.chat(request,vi.fn(),new AbortController().signal)).resolves.toBeUndefined()
 })

 it('does not start a cancelled request or a disposed provider transport',async()=>{
  const {transport,fetch}=setup([start(),custom(done),finish()])
  const controller=new AbortController();controller.abort()
  await expect(transport.chat(request,vi.fn(),controller.signal)).rejects.toMatchObject({name:'AbortError'})
  await transport.dispose()
  await expect(transport.chat(request,vi.fn(),new AbortController().signal)).rejects.toMatchObject({name:'AbortError'})
  expect(fetch).not.toHaveBeenCalled()
 })

 it('keeps the SDK instance reserved across provider replacement until the previous bridge finishes disposal',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(null,{status:200})))
  const {agent,transport}=setup([start(),custom(done),finish()])
  const finalizerEntered=deferred(),gate=deferred()
  const subscription=agent.subscribe({onRunFinalized:async()=>{finalizerEntered.resolve();await gate.promise}})
  const pending=transport.chat(request,vi.fn(),new AbortController().signal).catch(error=>error)
  await finalizerEntered.promise
  let disposed=false
  const disposal=transport.dispose().then(()=>{disposed=true})
  const replacement=createCopilotTransport(agent)
  await expect(replacement.chat(request,vi.fn(),new AbortController().signal)).rejects.toThrow('RUN_IN_PROGRESS')
  expect(disposed).toBe(false)
  gate.resolve()
  expect(await pending).toMatchObject({name:'AbortError'})
  await disposal
  subscription.unsubscribe()
  await expect(replacement.chat(request,vi.fn(),new AbortController().signal)).resolves.toBeUndefined()
 })

 it('waits for runtime discovery without falling back to the old chat endpoint',async()=>{
  const fallback=vi.fn();vi.stubGlobal('fetch',fallback)
  const emit=vi.fn(),{transport}=setup([start(),custom(done),finish()])
  const pending=api.chat(request,emit,new AbortController().signal)
  await Promise.resolve()
  expect(fallback).not.toHaveBeenCalled()
  const unregister=registerChatTransport(transport.chat)
  try{await pending}finally{unregister()}
  expect(emit).toHaveBeenCalledExactlyOnceWith(done)
  expect(fallback).not.toHaveBeenCalled()
 })

 it('routes proposal generation to the Node service and business actions to FastAPI',async()=>{
  const result={requestId:'proposal-request',status:'succeeded'}
  const fetch=vi.fn().mockImplementation(async()=>new Response(JSON.stringify(result),{headers:{'Content-Type':'application/json'}}))
  vi.stubGlobal('fetch',fetch)
  await api.action({kind:'request_proposal',requestId:result.requestId,resetEpoch:1,source:'workout'})
  await api.action({kind:'set_locale',locale:'zh-CN',requestId:'locale-request',resetEpoch:1,source:'profile'})
  expect(fetch.mock.calls.map(call=>call[0])).toEqual(['/api/copilotkit/proposal','/api/actions'])
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({kind:'request_proposal',requestId:result.requestId,resetEpoch:1,source:'workout'})
 })
})
