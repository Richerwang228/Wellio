import {isDemoMode} from './runtime-mode'
import type {WellioClient,Snapshot,ActionResult,Attachment,ChatEvent} from './contracts'
import {z} from 'zod'
export class ApiError extends Error { constructor(public code:string,public status=0){super(code)} }

export type ChatTransport=WellioClient['chat']
let chatTransport:ChatTransport|undefined
const transportWaiters=new Set<(transport:ChatTransport)=>void>()
let reconnectTransport:((force?:boolean)=>void)|undefined
export function registerChatRecovery(reconnect:(force?:boolean)=>void){
 reconnectTransport=reconnect
 return()=>{if(reconnectTransport===reconnect)reconnectTransport=undefined}
}
export function retryChatConnection(force=false){reconnectTransport?.(force)}
export const REQUEST_TIMEOUTS={read:10_000,write:20_000,upload:30_000,proposal:130_000,discovery:15_000} as const

/** Only the mounted CopilotKit provider installs the production agent transport. */
export function registerChatTransport(transport:ChatTransport){
 chatTransport=transport
 for(const ready of [...transportWaiters])ready(transport)
 return()=>{if(chatTransport===transport)chatTransport=undefined}
}

async function readyTransport(signal:AbortSignal):Promise<ChatTransport>{
 signal.throwIfAborted()
 if(chatTransport)return chatTransport
 retryChatConnection()
 if(chatTransport)return chatTransport
 return new Promise((resolve,reject)=>{
  const cleanup=()=>{clearTimeout(timeout);transportWaiters.delete(ready);signal.removeEventListener('abort',aborted)}
  const ready=(transport:ChatTransport)=>{cleanup();resolve(transport)}
  const aborted=()=>{cleanup();reject(new DOMException('Aborted','AbortError'))}
  const timeout=setTimeout(()=>{cleanup();reject(new ApiError('CONNECTION_UNAVAILABLE',503))},REQUEST_TIMEOUTS.discovery)
  transportWaiters.add(ready);signal.addEventListener('abort',aborted,{once:true})
  if(signal.aborted)aborted()
 })
}

const id=z.string().min(1).max(128), version=z.number().int().positive(), localized=z.object({en:z.string(),'zh-CN':z.string()})
const nutrients=z.object({kcal:z.number(),protein:z.number(),carbs:z.number(),fat:z.number()})
const status=z.enum(['available','temporarily_occupied','unavailable'])
const contextVersions=z.object({meal:version,plan:version,workout:z.number().int().nonnegative(),conditions:version,readiness:version})
const toolStep=z.object({id,toolCallId:id,operation:z.enum(['context','history','equipment','knowledge_search','menu_search','meal_add','meal_update','meal_delete','meal_undo','workout_proposal','workout_progress']),status:z.enum(['started','succeeded','failed','awaiting_user']),errorCode:z.string().optional()}).passthrough()
const message=z.object({id,role:z.enum(['user','assistant']),content:z.union([z.string(),localized]),createdAt:z.string(),source:z.enum(['user','app_open','agent','fixture']),status:z.enum(['streaming','complete','failed','stopped']),steps:z.array(toolStep),phase:z.enum(['thinking','recognizing']).optional(),proposalId:id.optional(),operationId:id.optional(),mealId:id.optional(),attachmentUrl:z.string().optional(),errorCode:z.string().optional()}).passthrough()
const exercise=z.object({id,catalogId:id,name:localized,equipmentId:id,equipment:localized,sets:version,reps:version,restSeconds:z.number(),suggestedLoad:z.object({value:z.number().nullable(),unit:z.literal('kg'),basis:z.enum(['per_hand','machine_stack','bodyweight']),source:z.enum(['mock_history','user','missing']),sourceHistoryId:id.optional(),sourceMessageId:id.optional(),reason:localized}),completed:z.boolean(),instructions:localized,animation:z.enum(['row','pulldown','lateral','squat']).optional(),replacesId:id.optional(),equipmentStatus:status.optional()}).passthrough()
const workout=z.object({id,trainingSessionId:id,version,dayKey:z.string().nullable(),name:localized,gymId:z.enum(['gym-a','gym-b']),estimatedMinutes:z.number(),status:z.enum(['planned','in_progress','completed']),exercises:z.array(exercise),startedAt:z.string().optional(),endedAt:z.string().optional(),actualMinutes:z.number().optional(),source:z.enum(['demo_preset','agent_proposal']).optional()}).passthrough()
const readinessCheck=z.object({key:id,status:z.enum(['idle','pending','completed','failed','stopped','applied','dismissed','unavailable']),messageId:id.optional(),proposalId:id.optional(),errorCode:z.string().optional()}).passthrough()
const snapshot=z.object({
 schemaVersion:version.optional(),sessionId:id,resetEpoch:version,revision:version,conversationId:id,dayKey:z.string(),timeZone:z.string(),locale:z.enum(['en','zh-CN']),scenario:z.enum(['normal','low_recovery']),
 profile:z.object({name:z.string(),goal:z.literal('muscle_gain'),targets:nutrients,dislikes:localized,dinnerBudget:z.number(),expenditure:z.number()}).passthrough(),
 readiness:z.object({id,version,dayKey:z.string(),quality:z.enum(['valid','missing','stale','failed']),score:z.number().nullable(),scoreScale:z.number(),guidanceHint:z.enum(['keep_plan','consider_rest']).nullable(),observedAt:z.string(),restingHeartRate:z.number().nullable(),baselineHeartRate:z.number(),baselineSleepMinutes:z.number(),source:z.literal('mock_watch'),errorCode:z.string().optional(),reasons:z.array(z.string()).optional(),sleepRecordId:id.optional(),demoMode:z.enum(['low','balanced','high']).optional(),hrvMs:z.number().nonnegative().optional()}).passthrough(),
 sleep:z.object({id,minutes:z.number(),bedtime:z.string(),wakeTime:z.string(),source:z.literal('mock_watch'),deepSleepPercent:z.number().min(0).max(100).optional()}).nullable(),
 meals:z.array(z.object({id,version,period:z.enum(['breakfast','lunch','dinner','snack']),time:z.string(),items:z.array(z.object({id,name:localized,portion:localized,base:nutrients,consumedFraction:z.number(),estimated:z.boolean(),originalPortion:z.object({quantity:z.number(),unit:z.enum(['g','ml','piece','serving'])}).optional(),nutrientUnits:z.object({energy:z.literal('kcal'),mass:z.literal('g')}).optional()})),operationId:id.optional()})),workout:workout.nullable(),
 plan:z.object({id,version,pendingSessionIds:z.array(id),sessions:z.array(z.object({id,split:z.enum(['Pull','Legs','Push']),templateRef:z.string(),slotId:id.nullable(),date:z.string().nullable(),workoutId:id.optional(),status:z.enum(['pending','in_progress','completed'])})),availableSlots:z.array(z.object({id,date:z.string()})),restDates:z.array(z.string())}),
 proposals:z.array(z.object({id,scope:z.enum(['workout','schedule']),status:z.enum(['pending','applied','dismissed','stale']),reason:localized,expected:contextVersions,contextReadId:id,readinessSnapshotId:id,workout:workout.optional(),moves:z.array(z.object({sessionId:id,split:z.enum(['Pull','Legs','Push']),from:z.string().nullable(),to:z.string().nullable()})).optional(),restDate:z.string().optional(),messageId:id.optional(),runId:id.optional(),resetEpoch:version.optional(),unassignedSessionIds:z.array(id).optional(),checkKey:id.optional(),checkAttemptId:id.optional()})),
 messages:z.array(message),history:z.object({weight:z.array(z.object({date:z.string(),kg:z.number()})),load:z.array(z.object({id,date:z.string(),exerciseId:id,name:localized,equipmentId:id,kg:z.number(),basis:z.enum(['per_hand','machine_stack','bodyweight']),source:z.literal('mock_history'),sets:version.optional(),reps:version.optional()})),training:z.array(z.object({date:z.string(),type:z.string(),minutes:z.number(),workoutId:id.optional(),trainingSessionId:id.optional()})),nutrition:z.array(nutrients.extend({date:z.string(),expenditure:z.number()}))}),
 advice:z.object({status:z.enum(['pending','valid','stale','failed','unavailable']),training:localized.optional(),nutrition:localized.optional(),messageId:id.optional(),contextReadId:id.optional(),versions:contextVersions.optional(),errorCode:z.string().optional()}),readinessCheck:readinessCheck.optional(),
 conditions:z.object({version,gymId:z.enum(['gym-a','gym-b']),availableMinutes:z.number(),dinnerBudget:z.number(),equipmentStatus:z.record(z.string(),status).optional(),lastChange:z.object({sourceMessageId:id,requestId:id,version}).optional()}),mealRevision:version,capabilities:z.object({agent:z.boolean(),menuSearch:z.boolean(),persistence:z.enum(['server','preview'])}),
}).passthrough()
const envelope={requestId:id,resetEpoch:version}
const chatEvent=z.discriminatedUnion('type',[
 z.strictObject({...envelope,type:z.literal('message'),message}),
 z.strictObject({...envelope,type:z.literal('phase'),messageId:id,phase:z.enum(['thinking','recognizing'])}),
 z.strictObject({...envelope,type:z.literal('tool'),messageId:id,step:toolStep}),
 z.strictObject({...envelope,type:z.literal('text'),messageId:id,delta:z.string()}),
 z.strictObject({...envelope,type:z.literal('snapshot'),snapshot}),
 z.strictObject({...envelope,type:z.literal('done'),messageId:id}),
 z.strictObject({...envelope,type:z.literal('check_result'),checkKey:id,outcome:z.enum(['reused','in_progress','not_needed','not_available'])}),
 z.strictObject({...envelope,type:z.literal('error'),messageId:id.optional(),errorCode:z.string().min(1)}),
])

export function validateChatEvent(value:unknown):ChatEvent{
 const parsed=chatEvent.safeParse(value)
 if(!parsed.success)throw new ApiError('STREAM_PROTOCOL_ERROR')
 return parsed.data as ChatEvent
}
async function json<T>(url:string,init?:RequestInit,timeoutMs:number=REQUEST_TIMEOUTS.read):Promise<T>{
 init?.signal?.throwIfAborted()
 const controller=new AbortController(),signal=init?.signal
 let timer:ReturnType<typeof setTimeout>|undefined
 let aborted:()=>void=()=>{}
 const interrupted=new Promise<never>((_resolve,reject)=>{
  aborted=()=>{controller.abort();reject(new DOMException('Aborted','AbortError'))}
  signal?.addEventListener('abort',aborted,{once:true})
  timer=setTimeout(()=>{controller.abort();reject(new ApiError('TIMEOUT'))},timeoutMs)
  if(signal?.aborted)aborted()
 })
 try{
  const work=(async()=>{
   const response=await fetch(url,{credentials:'same-origin',...init,signal:controller.signal})
   if(!response.ok){const body=await response.json().catch(()=>({}));throw new ApiError(body.errorCode||'NETWORK_ERROR',response.status)}
   return await response.json() as T
  })()
  return await Promise.race([work,interrupted])
 }finally{clearTimeout(timer);signal?.removeEventListener('abort',aborted)}
}
const httpClient:WellioClient={
 getSnapshot:signal=>json<Snapshot>('/api/state',{signal}),
 action:(request,signal)=>json<ActionResult>(request.kind==='request_proposal'?'/api/copilotkit/proposal':'/api/actions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request),signal},request.kind==='request_proposal'?REQUEST_TIMEOUTS.proposal:REQUEST_TIMEOUTS.write),
 upload:(file,purpose,signal)=>{const body=new FormData();body.append('file',file);body.append('purpose',purpose);return json<Attachment>('/api/attachments',{method:'POST',body,signal},REQUEST_TIMEOUTS.upload)},
 chat:async(request,onEvent,signal)=>(await readyTransport(signal))(request,onEvent,signal),
}
async function client():Promise<WellioClient>{
 if(isDemoMode())return (await import('./demo')).demoClient
 // Legacy isolated visual adapter.
 if(import.meta.env.DEV&&isPreviewMode())return (await import('./preview-client')).previewClient
 return httpClient
}
export function isPreviewMode(){return import.meta.env.DEV&&typeof window!=='undefined'&&(import.meta.env.VITE_WELLIO_PREVIEW==='1'||new URLSearchParams(location.search).get('preview')==='1')}
export const api:WellioClient={getSnapshot:async s=>(await client()).getSnapshot(s),action:async(r,s)=>(await client()).action(r,s),upload:async(f,p,s)=>(await client()).upload(f,p,s),chat:async(r,e,s)=>(await client()).chat(r,e,s)}
