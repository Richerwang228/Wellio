import {test,expect,type Page,type Locator} from '@playwright/test'
import {createServer,type ServerResponse} from 'node:http'
import {createFixture} from '../../src/lib/fixtures'
import type {ChatEvent,ChatRequest,Locale,Message,Snapshot,ToolStep} from '../../src/lib/contracts'

type Payload=ChatEvent extends infer E ? E extends ChatEvent ? Omit<E,'requestId'|'resetEpoch'> : never : never

// A real HTTP response stays open between writes. DOM assertions gate each chunk, not timers.
async function controlledStream(page:Page,initial:Snapshot){
 let state=structuredClone(initial),response:ServerResponse|undefined,stateReads=0
 let acceptRequest!:(request:ChatRequest)=>void
 const request=new Promise<ChatRequest>(resolve=>{acceptRequest=resolve})
 const server=createServer(async(req,res)=>{
  res.setHeader('Access-Control-Allow-Origin','http://127.0.0.1:3101')
  res.setHeader('Access-Control-Allow-Credentials','true')
  res.setHeader('Access-Control-Allow-Headers','content-type')
  if(req.method==='OPTIONS'){res.writeHead(204);res.end();return}
  const chunks:Buffer[]=[];for await(const chunk of req)chunks.push(Buffer.from(chunk))
  const body=JSON.parse(Buffer.concat(chunks).toString()) .forwardedProps.wellio as ChatRequest
  res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache'});res.flushHeaders()
  res.write('data: '+JSON.stringify({type:'RUN_STARTED',runId:body.requestId,threadId:body.conversationId})+'\n\n');response=res;acceptRequest(body)
 })
 await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve))
 const address=server.address();if(!address||typeof address==='string')throw new Error('Missing streaming test address')
 await page.route('**/api/state',route=>{stateReads++;return route.fulfill({json:state})})
 await page.route('**/api/copilotkit/agent/wellio/run',route=>route.continue({url:`http://127.0.0.1:${address.port}/chat`}))
 await page.route('**/api/actions',route=>{
  const request=route.request().postDataJSON()
  expect(request.kind).toBe('undo_meal')
  state={...state,revision:state.revision+1}
  return route.fulfill({json:{requestId:request.requestId,resetEpoch:state.resetEpoch,status:'succeeded',snapshot:state}})
 })
 return {
  request,reads:()=>stateReads,
  async send(event:Payload){const identity=await request;response!.write('data: '+JSON.stringify({type:'CUSTOM',name:'wellio',value:{...event,requestId:identity.requestId,resetEpoch:identity.resetEpoch}})+'\n\n')},
  async finish(next:Snapshot,messageId:string){state=structuredClone(next);const identity=await request;response!.end('data: '+JSON.stringify({type:'CUSTOM',name:'wellio',value:{type:'done',messageId,requestId:identity.requestId,resetEpoch:identity.resetEpoch}})+'\n\ndata: '+JSON.stringify({type:'RUN_FINISHED',runId:identity.requestId,threadId:identity.conversationId})+'\n\n')},
  async close(){response?.destroy();server.closeAllConnections();await new Promise<void>(resolve=>server.close(()=>resolve()))},
 }
}

function history(locale:Locale){
 const state=createFixture();state.locale=locale;state.capabilities.agent=true
 state.messages=Array.from({length:30},(_,i):Message=>({id:`history-${i}`,role:i%2?'assistant':'user',source:'fixture',status:'complete',steps:[],createdAt:'2026-09-12T09:00:00+08:00',content:locale==='en'?`History ${i+1}. This is a saved conversation paragraph about meals and training.\n\nThe record stays available while the next reply arrives.`:`历史记录 ${i+1}。这是一段已经保存的饮食与训练对话。\n\n新回复到来时，这条记录仍可阅读。`}))
 return state
}
async function expectBottom(thread:Locator){await expect.poll(()=>thread.evaluate(el=>el.scrollHeight-el.scrollTop-el.clientHeight)).toBeLessThanOrEqual(1)}
async function readOffset(thread:Locator){return thread.evaluate(el=>el.scrollTop)}
async function expectLayout(page:Page){
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
 expect(await page.locator('.agent-thread').evaluate(el=>el.scrollWidth<=el.clientWidth)).toBe(true)
 const nav=await page.locator('.app-nav').boundingBox();expect(nav).not.toBeNull();expect(nav!.y+nav!.height).toBeLessThanOrEqual(700)
 await expect(page.locator('.agent-composer textarea')).toBeVisible()
 expect(await page.locator('.app-main').evaluate(el=>el.scrollTop)).toBe(0)
}

for(const locale of ['en','zh-CN'] as const)test(`390px ${locale}: CopilotKit SSE follows bottom, preserves history reading and retains final tool metadata`,async({page})=>{
 await page.setViewportSize({width:390,height:700})
 await page.addInitScript(value=>localStorage.setItem('wellio.locale.v1',value),locale)
 const en=locale==='en',state=history(locale),stream=await controlledStream(page,state),errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 try{
  await page.goto('/agent');const thread=page.locator('.agent-thread');await expect(page.locator('.agent-turn')).toHaveCount(30);await expectBottom(thread)
  const composer=page.getByRole('textbox',{name:en?'Message Wellio':'给 Wellio 的消息'});await composer.fill(en?'Please review this meal.':'请看看这餐。')
  await page.getByRole('button',{name:en?'Send message':'发送消息',exact:true}).click()
  const request=await stream.request;expect(request).toMatchObject({source:'user',locale,resetEpoch:state.resetEpoch,conversationId:state.conversationId})
  const user:Message={id:'stream-user',role:'user',source:'user',status:'complete',steps:[],createdAt:'2026-09-12T09:10:00+08:00',content:request.message}
  const message:Message={id:'stream-assistant',role:'assistant',source:'agent',status:'streaming',steps:[],createdAt:'2026-09-12T09:10:01+08:00',content:''}
  await stream.send({type:'message',message:user});await stream.send({type:'message',message})
  const reply=page.locator('.agent-turn-assistant').last();await expect(page.locator('.agent-turn')).toHaveCount(32)
  await stream.send({type:'phase',messageId:message.id,phase:'thinking'});await expect(reply.locator('.agent-phase')).toHaveText(en?'Thinking it through…':'正在整理建议…');await expect(reply.locator('.agent-phase')).toBeInViewport();await expectBottom(thread)
  const started:ToolStep={id:'context-started',toolCallId:'context-call-1',operation:'context',status:'started'}
  await stream.send({type:'tool',messageId:message.id,step:started});await expect(reply.locator('.agent-process summary')).toContainText(en?'Checking recovery':'查看恢复情况');await expectBottom(thread)
  // Separate lifecycle event IDs deliberately share one toolCallId.
  const succeeded:ToolStep={...started,id:'context-succeeded',status:'succeeded'}
  await stream.send({type:'tool',messageId:message.id,step:succeeded});await expect(reply.locator('.agent-process summary')).toContainText(en?'Completed 1 step':'已完成 1 个步骤');await expect(reply.locator('.agent-process li')).toHaveCount(1);await expectBottom(thread)
  let content=''
  for(let i=1;i<=3;i++){
   const delta=en?`\n\nReply segment ${i}. ${'This paragraph reviews the saved meal and training context. '.repeat(5)}`:`\n\n回复第 ${i} 段。${'这段内容说明已经保存的餐食和训练上下文。'.repeat(7)}`
   content+=delta;await stream.send({type:'text',messageId:message.id,delta});await expect(reply.locator('.agent-markdown')).toContainText(en?`Reply segment ${i}`:`回复第 ${i} 段`);await expectBottom(thread)
   await expect(reply.locator('.agent-markdown p').last()).toBeInViewport();await expectLayout(page)
  }
  // Simulate an actual user scroll and wait for its native event before delivering more data.
  await thread.evaluate(el=>new Promise<void>(resolve=>{el.addEventListener('scroll',()=>resolve(),{once:true});el.scrollTop=300}))
  const reading=await readOffset(thread);expect(reading).toBe(300)
  const longToken='WELLIO_LONG_RECORD_'.repeat(20)
  const delta=`\n\n${en?'New while reading history':'阅读历史时的新内容'}\n\n${longToken}`;content+=delta
  await stream.send({type:'text',messageId:message.id,delta});await expect(reply.locator('.agent-markdown')).toContainText(longToken)
  const newReply=page.getByRole('button',{name:en?'New reply':'有新回复',exact:true});await expect(newReply).toBeVisible();expect(await readOffset(thread)).toBe(reading);await expectLayout(page)
  const historyStep:ToolStep={id:'history-started',toolCallId:'history-call-1',operation:'history',status:'started'}
  await stream.send({type:'tool',messageId:message.id,step:historyStep});await expect(reply.locator('.agent-process summary')).toContainText(en?'Reviewing recent history':'查看近期记录');expect(await readOffset(thread)).toBe(reading)
  const historyDone:ToolStep={...historyStep,id:'history-succeeded',status:'succeeded'}
  await stream.send({type:'tool',messageId:message.id,step:historyDone});await expect(reply.locator('.agent-process summary')).toContainText(en?'Completed 2 steps':'已完成 2 个步骤');await expect(reply.locator('.agent-process li')).toHaveCount(2);expect(await readOffset(thread)).toBe(reading)
  const marker=en?'Final saved meal details.':'最终已保存的餐食详情。';content+=`\n\n${marker}`
  await stream.send({type:'text',messageId:message.id,delta:`\n\n${marker}`});await expect(reply.locator('.agent-markdown')).toContainText(marker);expect(await readOffset(thread)).toBe(reading)
  // Saved metadata comes only from the final authoritative GET, after the done event.
  const saved:Message={...message,content,status:'complete',steps:[succeeded,historyDone],mealId:'meal-lunch',operationId:'saved-meal-operation'}
  await stream.finish({...state,revision:state.revision+1,messages:[...state.messages,user,saved]},message.id)
  await expect.poll(stream.reads).toBeGreaterThanOrEqual(2)
  await expect(page.getByRole('button',{name:en?'Stop reply':'停止回复',exact:true})).toHaveCount(0)
  await expect(reply.getByRole('button',{name:en?'Modify':'修改',exact:true})).toHaveCount(1);await expect(reply.getByRole('button',{name:en?'Undo':'撤销',exact:true})).toHaveCount(1)
  expect(await readOffset(thread)).toBe(reading);await expect(newReply).toBeVisible()
  await newReply.click();await expectBottom(thread);await expect(newReply).toHaveCount(0);await expect(reply.locator('.agent-markdown p').last()).toBeInViewport();await expect(reply.getByRole('button',{name:en?'Undo':'撤销',exact:true})).toBeInViewport()
  await reply.getByRole('button',{name:en?'Undo':'撤销',exact:true}).click()
  const undoFeedback=reply.getByText(en?'This change was undone.':'这次修改已撤销。',{exact:true})
  await expect(undoFeedback).toBeVisible();await expectBottom(thread);await expect(undoFeedback).toBeInViewport()
  await reply.locator('.agent-process summary').click();await expect(reply.locator('.agent-process li')).toHaveCount(2);await expect(reply.locator('.agent-step-succeeded')).toHaveCount(2)
  await composer.fill(en?'A follow-up draft':'后续消息草稿');await expect(composer).toHaveValue(en?'A follow-up draft':'后续消息草稿');await expect(page.getByRole('button',{name:en?'Send message':'发送消息',exact:true})).toBeEnabled();await expectLayout(page)
  const today=page.getByRole('link',{name:en?'Today':'今日',exact:true});await today.focus();await page.keyboard.press('Enter');await expect(page.locator('.today-page')).toBeVisible();await page.getByRole('link',{name:en?'Agent':'助手',exact:true}).click();await expect(composer).toHaveValue(en?'A follow-up draft':'后续消息草稿');await expect(reply.getByRole('button',{name:en?'Modify':'修改',exact:true})).toHaveCount(1)
  expect(errors).toEqual([])
 }finally{await stream.close()}
})
