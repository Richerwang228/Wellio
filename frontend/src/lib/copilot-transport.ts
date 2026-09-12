import {HttpAgent, type AbstractAgent, type AgentSubscriber} from '@ag-ui/client'
import {CopilotKitCoreRuntimeConnectionStatus as ConnectionStatus,type CopilotKitCore} from '@copilotkit/core'
import {ApiError, retryChatConnection, validateChatEvent, type ChatTransport} from './api-client'

/** Public SDK config changes restart discovery; connect() only handles the first attempt. */
export function installCopilotRecovery(core:Pick<CopilotKitCore,'runtimeUrl'|'runtimeConnectionStatus'|'setRuntimeUrl'|'subscribe'>){
 const url=core.runtimeUrl||'/api/copilotkit',delays=[500,1_500,3_000]
 let disposed=false,attempt=0,timer:ReturnType<typeof setTimeout>|undefined
 const clear=()=>{clearTimeout(timer);timer=undefined}
 const restart=()=>{if(disposed)return;clear();core.setRuntimeUrl(undefined);core.setRuntimeUrl(url)}
 const changed=()=>{
  clear()
  const status=core.runtimeConnectionStatus
  if(status===ConnectionStatus.Connected){attempt=0;return}
  if(status===ConnectionStatus.Connecting){
   // Initial SDK discovery has no request deadline; bound each attempt locally.
   timer=setTimeout(()=>{if(attempt<delays.length){attempt++;restart()}else core.setRuntimeUrl(undefined)},8_000)
  }else if(status===ConnectionStatus.Error&&attempt<delays.length){
   timer=setTimeout(()=>{attempt++;restart()},delays[attempt])
  }
 }
 const subscription=core.subscribe({onRuntimeConnectionStatusChanged:changed})
 changed()
 return {
  retry:(force=false)=>{
   if(disposed||!force&&(core.runtimeConnectionStatus===ConnectionStatus.Connected||core.runtimeConnectionStatus===ConnectionStatus.Connecting))return
   attempt=0;restart()
  },
  dispose:()=>{disposed=true;clear();subscription.unsubscribe()},
 }
}

// Node enforces 120 seconds for a run. Leave time for its final saved snapshot.
export const STREAM_TIMEOUTS={firstEvent:15_000,idle:45_000,total:135_000,cleanup:2_000} as const

const abortError=()=>new DOMException('Aborted','AbortError')
const errorCode=(value:unknown)=>typeof value==='string'&&/^[A-Z][A-Z0-9_]{0,127}$/.test(value)?value:undefined
type ActiveRun={cancel:()=>void;settled:Promise<void>}
// React can replace a bridge while its previous instance is still finalizing.
const agentRuns=new WeakMap<AbstractAgent,ActiveRun>()
const retiredAgents=new WeakSet<AbstractAgent>()

function transportError(error:unknown):ApiError{
 if(error instanceof ApiError)return error
 const detail=error!==null&&typeof error==='object'?error as {status?:unknown;payload?:unknown}:{}
 const payload=detail.payload!==null&&typeof detail.payload==='object'?detail.payload as {errorCode?:unknown}:{}
 return new ApiError(errorCode(payload.errorCode)||'PROVIDER_ERROR',typeof detail.status==='number'?detail.status:0)
}

/** Uses the runtime-discovered SDK instance; no second chat HTTP implementation. */
export function createCopilotTransport(agent:AbstractAgent,limits:Partial<Record<keyof typeof STREAM_TIMEOUTS,number>>={}){
 const budgets={...STREAM_TIMEOUTS,...limits}
 let disposed=false
 let active:ActiveRun|undefined
 const chat:ChatTransport=async(request,onEvent,signal)=>{
  signal.throwIfAborted()
  if(disposed||retiredAgents.has(agent))throw abortError()
  if(agentRuns.has(agent))throw new ApiError('RUN_IN_PROGRESS',409)
  let release!:()=>void
  const settled=new Promise<void>(resolve=>{release=resolve})
  let cancelled=false,stopped=false,initialized=false,started=false,finished=false,terminal=false,count=0,abandoned=false
  let failure:ApiError|undefined,checkKey:string|undefined
  const sdkController=new AbortController()
  let firstTimer:ReturnType<typeof setTimeout>|undefined,idleTimer:ReturnType<typeof setTimeout>|undefined,totalTimer:ReturnType<typeof setTimeout>|undefined,cleanupTimer:ReturnType<typeof setTimeout>|undefined
  let abandon!:()=>void
  const interrupted=new Promise<void>(resolve=>{abandon=resolve})
  let finalizationComplete!:()=>void
  const finalized=new Promise<void>(resolve=>{finalizationComplete=resolve})
  const closeLocal=()=>{
   sdkController.abort()
   if(agent instanceof HttpAgent)agent.abortController.abort()
   void agent.detachActiveRun().catch(()=>{})
   cleanupTimer??=setTimeout(()=>{abandoned=true;disposed=true;retiredAgents.add(agent);abandon()},budgets.cleanup)
  }
  const stop=()=>{
   if(stopped)return
   stopped=true
   // Release the browser reader even when the backend /stop request never answers.
   closeLocal()
   agent.abortRun()
  }
  const cancel=()=>{cancelled=true;stop()}
  const fail=(error:ApiError)=>{failure??=error;stop()}
  const reservation={cancel,settled}
  active=reservation
  agentRuns.set(agent,reservation)
  const subscriber:AgentSubscriber={
   onEvent:()=>{
    clearTimeout(firstTimer);clearTimeout(idleTimer)
    idleTimer=setTimeout(()=>fail(new ApiError('TIMEOUT')),budgets.idle)
   },
   onRunInitialized:()=>{initialized=true},
   // AG-UI 0.0.59 does not await onFinalize. This is the last subscriber;
   // the next task also lets its queued state mutations finish before reuse.
   onRunFinalized:()=>{setTimeout(finalizationComplete,0)},
   onRunStartedEvent:({event})=>{
    if(event.runId!==request.requestId||event.threadId!==request.conversationId){fail(new ApiError('STREAM_PROTOCOL_ERROR'));return}
    started=true
   },
   onCustomEvent:({event})=>{
    if(event.name!=='wellio'||cancelled||failure)return
    try{
     const value=validateChatEvent(event.value)
     if(!started||terminal||value.requestId!==request.requestId||value.resetEpoch!==request.resetEpoch)throw new ApiError('STREAM_PROTOCOL_ERROR')
     if(value.type==='snapshot'){
      if(value.snapshot.resetEpoch!==request.resetEpoch||value.snapshot.conversationId!==request.conversationId)throw new ApiError('STREAM_PROTOCOL_ERROR')
      checkKey=value.snapshot.readinessCheck?.key
     }
     if(value.type==='check_result'&&(request.source!=='app_open'||!checkKey||value.checkKey!==checkKey))throw new ApiError('STREAM_PROTOCOL_ERROR')
     count++
     terminal=value.type==='done'||value.type==='check_result'||value.type==='error'
     onEvent(value)
     if(value.type==='error')failure=new ApiError(value.errorCode)
    }catch(error){fail(error instanceof ApiError?error:new ApiError('STREAM_PROTOCOL_ERROR'))}
   },
   onRunFinishedEvent:({event})=>{
    if(event.runId!==request.requestId||event.threadId!==request.conversationId){fail(new ApiError('STREAM_PROTOCOL_ERROR'));return}
    finished=true
    closeLocal()
   },
   // Preflight failures may be RUN_ERROR without RUN_STARTED or a CUSTOM event.
   onRunErrorEvent:({event})=>{failure??=new ApiError(errorCode(event.code)||'PROVIDER_ERROR');closeLocal()},
   onRunFailed:({error})=>{if(!cancelled&&!(finished&&terminal))failure??=transportError(error)},
  }
  try{
   agent.threadId=request.conversationId
   // Persisted history/context come from the server session, never browser state.
   agent.setMessages([])
   agent.setState({})
   firstTimer=setTimeout(()=>fail(new ApiError('TIMEOUT')),budgets.firstEvent)
   totalTimer=setTimeout(()=>fail(new ApiError('TIMEOUT')),budgets.total)
   const parameters={runId:request.requestId,forwardedProps:{wellio:request},tools:[],context:[]}
   const running=agent instanceof HttpAgent?agent.runAgent({...parameters,abortController:sdkController},subscriber):agent.runAgent(parameters,subscriber)
   signal.addEventListener('abort',cancel,{once:true})
   if(signal.aborted||disposed)cancel()
   try{await Promise.race([running,interrupted])}catch(error){if(!cancelled&&!(finished&&terminal))failure??=transportError(error)}
   if(initialized)await Promise.race([finalized,interrupted])
   if(cancelled||signal.aborted)throw abortError()
   if(failure)throw failure
   if(!terminal)throw new ApiError(count?'INCOMPLETE_RESPONSE':'EMPTY_RESPONSE')
   if(!started||!finished)throw new ApiError('INCOMPLETE_RESPONSE')
  }finally{
   clearTimeout(firstTimer);clearTimeout(idleTimer);clearTimeout(totalTimer);clearTimeout(cleanupTimer)
   signal.removeEventListener('abort',cancel)
   if(agentRuns.get(agent)===reservation)agentRuns.delete(agent)
   active=undefined
   release()
   if(abandoned)retryChatConnection(true)
  }
 }
 return {chat,dispose:async()=>{disposed=true;const running=active;if(running){running.cancel();await running.settled}}}
}
