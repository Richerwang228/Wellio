import {isDemoMode} from './runtime-mode'
import {createContext,useContext,useEffect,useRef,useState,type ReactNode} from 'react'
import {api,ApiError,retryChatConnection} from './api-client'
import {useI18n} from './i18n'
import type {ActionInput,ActionRequest,ActionResult,Attachment,Snapshot,ChatEvent,ChatRequest,Locale} from './contracts'
export interface ChatTarget {mealId?:string;mealItemId?:string;workoutId?:string;exerciseId?:string;operationId?:string}
interface WellioValue {
 snapshot:Snapshot|null;loading:boolean;error:string|null;busy:boolean;chatBusy:boolean;readinessBusy:boolean;readinessError:string|null;
 draft:string;setDraft:(value:string)=>void;chatTarget:ChatTarget;setChatTarget:(value:ChatTarget)=>void;
 runAction:(input:ActionInput,source?:ActionRequest['source'])=>Promise<ActionResult|null>;
 sendMessage:(message:string,attachments?:Attachment[])=>Promise<void>;
 checkReadiness:(options?:{mode:'auto'|'retry'})=>Promise<void>;
 stopChat:()=>void;refresh:()=>Promise<boolean>;
}
interface ActiveRun {
 id:string;sessionId:string;epoch:number;source:ChatRequest['source'];locale:Locale;
 messageIds:Set<string>;controller:AbortController;streaming:boolean;finalSnapshot:boolean;settled:Promise<void>;
}
const Context=createContext<WellioValue|null>(null)
const sameSession=(snapshot:Snapshot|null,identity:{sessionId:string;epoch:number})=>snapshot?.sessionId===identity.sessionId&&snapshot.resetEpoch===identity.epoch
function validReadiness(snapshot:Snapshot){
 const r=snapshot.readiness
 return r.source==='mock_watch'&&r.quality==='valid'&&r.dayKey===snapshot.dayKey&&Boolean(r.id)&&Number.isFinite(r.version)
  &&r.score!==null&&Number.isFinite(r.score)&&r.scoreScale>0&&r.score>=0&&r.score<=r.scoreScale&&Number.isFinite(Date.parse(r.observedAt))
}
// Cancel reconciliation reads promptly; agent streams separately await SDK cleanup.
function abortable<T>(work:Promise<T>,signal:AbortSignal):Promise<T>{
 return new Promise((resolve,reject)=>{
  const aborted=()=>reject(new DOMException('Aborted','AbortError'))
  signal.addEventListener('abort',aborted,{once:true})
  work.then(value=>{signal.removeEventListener('abort',aborted);resolve(value)},error=>{signal.removeEventListener('abort',aborted);reject(error)})
  if(signal.aborted)aborted()
 })
}
export function WellioProvider({children}:{children:ReactNode}){
 const {locale,setLocale}=useI18n()
 const [snapshot,setSnapshot]=useState<Snapshot|null>(null),[loading,setLoading]=useState(true),[error,setError]=useState<string|null>(null),[busy,setBusy]=useState(false),[chatBusy,setChatBusy]=useState(false),[draft,setDraft]=useState(''),[chatTarget,setChatTarget]=useState<ChatTarget>({})
 const [readinessBusy,setReadinessBusy]=useState(false),[readinessError,setReadinessError]=useState<string|null>(null)
 const current=useRef<Snapshot|null>(null),actionLock=useRef(false),readInFlight=useRef<Promise<boolean>|null>(null)
 const readController=useRef<AbortController|null>(null)
 const activeRun=useRef<ActiveRun|null>(null),eventSequence=useRef(0),localeRef=useRef(locale)
 const userReservation=useRef(false),userController=useRef<AbortController|null>(null),checkReservation=useRef(false),queuedCheck=useRef(false),attemptedChecks=useRef(new Set<string>())
 const mounted=useRef(true),lifecycle=useRef(0)
 const uncertainAction=useRef<{fingerprint:string;sessionId:string;request:ActionRequest}|null>(null)
 localeRef.current=locale
 function accept(next:Snapshot,preserveActive=false){
  const old=current.current
  if(!old){
   try{const saved=localStorage.getItem('wellio.locale.v1');if(saved==='en'||saved==='zh-CN')localeRef.current=saved;else{localeRef.current=next.locale;setLocale(next.locale)}}catch{localeRef.current=next.locale;setLocale(next.locale)}
  }
  if(old&&next.sessionId===old.sessionId&&(next.resetEpoch<old.resetEpoch||(next.resetEpoch===old.resetEpoch&&next.revision<old.revision)))return
  const run=activeRun.current
  if(preserveActive&&old&&run?.streaming&&next.sessionId===run.sessionId&&next.resetEpoch===run.epoch){
   const live=old.messages.filter(m=>run.messageIds.has(m.id)),ids=new Set(live.map(m=>m.id))
   next={...next,messages:[...next.messages.filter(m=>!ids.has(m.id)),...live]}
  }
  current.current=next;setSnapshot(next)
  if(old&&(old.sessionId!==next.sessionId||old.resetEpoch!==next.resetEpoch)){
   attemptedChecks.current.clear();queuedCheck.current=false;setReadinessError(null)
   if(run&&!sameSession(next,run)){activeRun.current=null;run.controller.abort()}
  }
 }
 async function refresh(replace=false){
  retryChatConnection()
  if(replace){readController.current?.abort();readInFlight.current=null}
  if(readInFlight.current)return readInFlight.current
  const sequence=eventSequence.current,controller=new AbortController()
  readController.current=controller
  const work=(async()=>{try{
   const next=await abortable(api.getSnapshot(controller.signal),controller.signal),old=current.current
   if(controller.signal.aborted)return false
   const olderMessages=sequence!==eventSequence.current&&old&&old.sessionId===next.sessionId&&old.resetEpoch===next.resetEpoch&&next.revision<=old.revision
   if(!olderMessages)accept(next,true)
   setError(null);return true
  }catch(e){if(!controller.signal.aborted)setError(e instanceof ApiError?e.code:'NETWORK_ERROR');return false}finally{if(readController.current===controller){setLoading(false);readInFlight.current=null;readController.current=null}}})()
  readInFlight.current=work;return work
 }
 function visible(){return typeof document!=='undefined'&&document.visibilityState==='visible'}
 function weakKey(s:Snapshot){return JSON.stringify([s.sessionId,s.resetEpoch,s.dayKey,s.readiness.id,s.readiness.version])}
 async function drainAutoCheck(){
  if(!queuedCheck.current||!mounted.current||!visible()||actionLock.current||userReservation.current||checkReservation.current||activeRun.current)return
  queuedCheck.current=false
  const s=current.current
  if(!s?.capabilities.agent||!s.readinessCheck?.key||s.readinessCheck.status!=='idle'||!validReadiness(s)||attemptedChecks.current.has(weakKey(s)))return
  attemptedChecks.current.add(weakKey(s))
  await startCheck(s,'auto').catch(()=>{})
 }
 async function refreshAndMaybeCheck(){
  const generation=lifecycle.current
  if(!await refresh()||generation!==lifecycle.current||!mounted.current||!visible())return
  queuedCheck.current=true
  await drainAutoCheck()
 }
 useEffect(()=>{
  mounted.current=true
  void refreshAndMaybeCheck()
  const foreground=()=>{if(visible())void refreshAndMaybeCheck()}
  document.addEventListener('visibilitychange',foreground)
  return()=>{mounted.current=false;lifecycle.current++;queuedCheck.current=false;document.removeEventListener('visibilitychange',foreground);readController.current?.abort();readController.current=null;readInFlight.current=null;userController.current?.abort();activeRun.current?.controller.abort()}
 },[])
 async function cancelReadiness(){
  const run=activeRun.current
  if(run?.source!=='app_open')return
  run.controller.abort()
  await run.settled
  if(!await refresh())throw new ApiError('NETWORK_ERROR')
 }
 async function runAction(input:ActionInput,source:ActionRequest['source']='today'){
  if(actionLock.current||!current.current)return null
  actionLock.current=true;setBusy(true);setError(null)
  const intent={sessionId:current.current.sessionId,epoch:current.current.resetEpoch}
  let resetSucceeded=false
  try{
   if(input.kind==='reset_demo'){
    lifecycle.current++;queuedCheck.current=false;userController.current?.abort()
    const run=activeRun.current;activeRun.current=null;eventSequence.current++;run?.controller.abort()
   }else if(input.kind!=='set_locale'&&activeRun.current?.source==='app_open')await cancelReadiness()
   if(!sameSession(current.current,intent)){setError('SESSION_RESET');return null}
   const submitted=current.current!,fingerprint=JSON.stringify([source,Object.entries(input).sort(([a],[b])=>a.localeCompare(b))])
   const pending=uncertainAction.current
   const request:ActionRequest=pending&&pending.fingerprint===fingerprint&&pending.sessionId===submitted.sessionId&&pending.request.resetEpoch===submitted.resetEpoch?pending.request:{...input,source,requestId:crypto.randomUUID(),resetEpoch:submitted.resetEpoch}
   uncertainAction.current={fingerprint,sessionId:submitted.sessionId,request}
   const result=await api.action(request)
   uncertainAction.current=null
   const now=current.current
   const ownReset=input.kind==='reset_demo'&&result.status==='succeeded'&&result.snapshot?.sessionId===submitted.sessionId&&result.snapshot.resetEpoch>submitted.resetEpoch&&now?.resetEpoch===result.snapshot.resetEpoch
   if(!now||now.sessionId!==submitted.sessionId||(now.resetEpoch!==submitted.resetEpoch&&!ownReset)||result.snapshot&&result.snapshot.sessionId!==submitted.sessionId){setError('SESSION_RESET');return null}
   if(result.snapshot)accept(result.snapshot,true)
   if(result.status!=='succeeded')setError(result.errorCode||'PROVIDER_ERROR')
   if(input.kind==='reset_demo'&&result.status==='succeeded'){setDraft('');setChatTarget({});resetSucceeded=true}
   return result
  }catch(e){
   const code=e instanceof ApiError?e.code:'NETWORK_ERROR'
   if(e instanceof ApiError&&e.status>0&&e.status<500)uncertainAction.current=null
   await refresh();setError(code);return null
  }finally{
   setBusy(false);actionLock.current=false
   if(resetSucceeded)void refreshAndMaybeCheck();else void drainAutoCheck()
  }
 }
 function receive(event:ChatEvent){
  const old=current.current,run=activeRun.current
  if(!old||!run||!run.streaming||run.controller.signal.aborted||event.requestId!==run.id||event.resetEpoch!==run.epoch||!sameSession(old,run))return
  eventSequence.current++
  if(event.type==='check_result')return
  if(event.type==='snapshot'){
   run.finalSnapshot=[...run.messageIds].every(id=>event.snapshot.messages.some(m=>m.id===id&&m.status!=='streaming'))
   accept(event.snapshot);return
  }
  if(event.type==='error'){
   if(run.source==='app_open')setReadinessError(event.errorCode);else setError(event.errorCode)
   if(event.messageId)patchMessage(event.messageId,m=>m.status==='stopped'?m:{...m,content:!run.finalSnapshot&&m.role==='assistant'?'':m.content,status:'failed',phase:undefined,errorCode:event.errorCode});return
  }
  if(event.type==='message'){
   if(isDemoMode() && event.message.role==='user')setDraft('')
   run.finalSnapshot=false
   run.messageIds.add(event.message.id)
   const messages=old.messages.some(m=>m.id===event.message.id)?old.messages.map(m=>m.id===event.message.id?event.message:m):[...old.messages,event.message]
   accept({...old,messages});return
  }
  if(event.type!=='done')run.finalSnapshot=false
  patchMessage(event.messageId,m=>event.type==='text'?{...m,content:(typeof m.content==='string'?m.content:m.content[run.locale])+event.delta}:event.type==='phase'?{...m,phase:event.phase}:event.type==='done'?{...m,status:'complete',phase:undefined}:{...m,steps:m.steps.some(s=>s.id===event.step.id)?m.steps.map(s=>s.id===event.step.id?event.step:s):[...m.steps,event.step]})
 }
 function patchMessage(id:string,patch:(m:Snapshot['messages'][number])=>Snapshot['messages'][number]){const old=current.current;if(old)accept({...old,messages:old.messages.map(m=>m.id===id?patch(m):m)})}
 function executeStream(request:ChatRequest,originalDraft?:string,originalTarget?:ChatTarget){
  const run:ActiveRun={id:request.requestId,sessionId:current.current!.sessionId,epoch:request.resetEpoch,source:request.source,locale:request.locale,messageIds:new Set(),controller:new AbortController(),streaming:true,finalSnapshot:false,settled:Promise.resolve()}
  activeRun.current=run;eventSequence.current++
  if(run.source==='app_open'){setReadinessBusy(true);setReadinessError(null)}else setError(null)
  const work=(async()=>{
   try{
    await api.chat(request,receive,run.controller.signal)
    if(run.controller.signal.aborted)throw new DOMException('Aborted','AbortError')
    if(activeRun.current!==run||!sameSession(current.current,run))throw new ApiError('SESSION_RESET')
    if(run.source==='user'){
     setDraft(value=>value===originalDraft?'':value)
     setChatTarget(value=>value===originalTarget?{}:value)
    }
    // Keep the reservation while reconciling, but accept authoritative final metadata.
    run.streaming=false;eventSequence.current++
    if(!run.finalSnapshot)await abortable(refresh(true),run.controller.signal)
   }catch(e){
    const aborted=e instanceof DOMException&&e.name==='AbortError',code=e instanceof ApiError?e.code:'NETWORK_ERROR'
    // The transport has finished its cancellation cleanup. Stop preserving
    // streamed messages before reconciling the server's failed/stopped check.
    run.streaming=false;eventSequence.current++
    if(activeRun.current===run&&sameSession(current.current,run)){
     if(!aborted){if(run.source==='app_open')setReadinessError(code);else setError(code)}
     accept({...current.current!,messages:current.current!.messages.map(m=>run.messageIds.has(m.id)&&(m.status==='streaming'||!run.finalSnapshot&&m.status==='complete')?{...m,content:m.role==='assistant'?'':m.content,status:aborted?'stopped':'failed',phase:undefined,errorCode:aborted?undefined:code}:m)})
     // Use a fresh read even for an aborted stream: its signal is already
     // cancelled, but the durable cancellation result still needs reconciling.
     const reconciled=await refresh(true)
     if(activeRun.current===run&&sameSession(current.current,run)){
      // refresh clears general read errors on success. Preserve the original
      // stream error until a deliberate retry starts.
      if(!aborted){if(run.source==='app_open')setReadinessError(code);else setError(code)}
      else if(run.source==='app_open'&&(!reconciled||current.current?.readinessCheck?.status==='pending'))setReadinessError('RUN_STOPPED')
     }
    }
    throw e
   }finally{
    if(activeRun.current===run){activeRun.current=null;eventSequence.current++}
    if(run.source==='app_open'&&activeRun.current?.source!=='app_open')setReadinessBusy(false)
    void drainAutoCheck()
   }
  })()
  run.settled=work.then(()=>{},()=>{})
  return work
 }
 function startCheck(s:Snapshot,mode:'auto'|'retry'){
  return executeStream({requestId:crypto.randomUUID(),resetEpoch:s.resetEpoch,conversationId:s.conversationId,source:'app_open',checkMode:mode,message:'',attachmentIds:[],locale:localeRef.current})
 }
 async function checkReadiness({mode='retry'}:{mode:'auto'|'retry'}={mode:'retry'}){
  if(mode==='auto'){await refreshAndMaybeCheck();return}
  if(checkReservation.current||actionLock.current||userReservation.current)throw new ApiError('INVALID_INPUT')
  const generation=lifecycle.current
  checkReservation.current=true;setReadinessError(null)
  try{
   if(!await refresh())throw new ApiError('NETWORK_ERROR')
   const s=current.current
   if(!s?.capabilities.agent)throw new ApiError('PROVIDER_NOT_CONFIGURED')
   if(!s.readinessCheck?.key||!validReadiness(s))throw new ApiError('READINESS_UNAVAILABLE')
   if(activeRun.current)return
   if(s.readinessCheck.status==='pending')throw new ApiError('RUN_IN_PROGRESS')
   if(['completed','applied','dismissed'].includes(s.readinessCheck.status))return
   attemptedChecks.current.add(weakKey(s))
   await startCheck(s,'retry')
  }catch(e){if(generation===lifecycle.current&&!(e instanceof DOMException&&e.name==='AbortError'))setReadinessError(e instanceof ApiError?e.code:'NETWORK_ERROR');throw e}
  finally{checkReservation.current=false;void drainAutoCheck()}
 }
 async function sendMessage(message:string,attachments:Attachment[]=[]){
  if(userReservation.current||actionLock.current||!current.current||(!message.trim()&&!attachments.length))throw new ApiError('INVALID_INPUT')
  const intent={sessionId:current.current.sessionId,epoch:current.current.resetEpoch},originalDraft=draft,originalTarget=chatTarget,requestLocale=localeRef.current
  const controller=new AbortController()
  userReservation.current=true;userController.current=controller;setChatBusy(true)
  try{
   if(activeRun.current?.source==='app_open')await abortable(cancelReadiness(),controller.signal)
   if(controller.signal.aborted)throw new DOMException('Aborted','AbortError')
   if(!sameSession(current.current,intent))throw new ApiError('SESSION_RESET')
   const s=current.current!
   await executeStream({requestId:crypto.randomUUID(),resetEpoch:s.resetEpoch,conversationId:s.conversationId,message,locale:requestLocale,attachmentIds:attachments.map(a=>a.id),purpose:attachments[0]?.purpose,targetMealId:originalTarget.mealId,targetMealItemId:originalTarget.mealItemId,targetWorkoutId:originalTarget.workoutId,targetExerciseId:originalTarget.exerciseId,targetOperationId:originalTarget.operationId,source:'user'},originalDraft,originalTarget)
  }finally{userReservation.current=false;if(userController.current===controller)userController.current=null;setChatBusy(false);void drainAutoCheck()}
 }
 return <Context.Provider value={{snapshot,loading,error,busy,chatBusy,readinessBusy,readinessError,draft,setDraft,chatTarget,setChatTarget,runAction,sendMessage,checkReadiness,stopChat:()=>{userController.current?.abort();activeRun.current?.controller.abort()},refresh}}>{children}</Context.Provider>
}
export function useWellio(){const value=useContext(Context);if(!value)throw new Error('WellioProvider missing');return value}
