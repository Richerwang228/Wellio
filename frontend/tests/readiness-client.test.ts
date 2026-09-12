import {aguiResponse,installTestTransport} from './helpers/copilot'
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest'
import type {ReactElement} from 'react'
import type {ChatEvent,ChatRequest,Locale,Message,Snapshot} from '../src/lib/contracts'
import {createFixture} from '../src/lib/fixtures'

// Controlled hook storage runs the real provider, including its mount/visibility callback.
const hooks=vi.hoisted(()=>({cells:[] as unknown[],cursor:0,locale:'en' as Locale,effect:undefined as (()=>void|(()=>void))|undefined,cleanup:undefined as (()=>void)|undefined}))
vi.mock('react',async importOriginal=>({
 ...(await importOriginal<typeof import('react')>()),
 useEffect:(effect:()=>void|(()=>void))=>{hooks.effect??=effect},
 useState:<T,>(initial:T)=>{const i=hooks.cursor++;if(!(i in hooks.cells))hooks.cells[i]=initial;return [hooks.cells[i],(next:T|((old:T)=>T))=>{hooks.cells[i]=typeof next==='function'?(next as (old:T)=>T)(hooks.cells[i] as T):next}]},
 useRef:<T,>(initial:T)=>{const i=hooks.cursor++;if(!(i in hooks.cells))hooks.cells[i]={current:initial};return hooks.cells[i]},
}))
vi.mock('../src/lib/i18n',()=>({useI18n:()=>({locale:hooks.locale,setLocale:(locale:Locale)=>{hooks.locale=locale}})}))
import {api,ApiError} from '../src/lib/api-client'
import {WellioProvider,type useWellio} from '../src/lib/wellio-context'
function render(){hooks.cursor=0;return (WellioProvider({children:null}) as ReactElement<{value:ReturnType<typeof useWellio>}>).props.value}
function deferred<T>(){let resolve!:(value:T|PromiseLike<T>)=>void,reject!:(error:unknown)=>void;const promise=new Promise<T>((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}}
function ready(epoch=1):Snapshot{const s=createFixture();return {...s,resetEpoch:epoch,capabilities:{...s.capabilities,agent:true},readinessCheck:{key:`check-${epoch}`,status:'idle'}}}
function message(id:string,status:Message['status']='streaming'):Message{return {id,role:'assistant',source:'app_open',content:'',status,createdAt:'2026-09-12T08:00:00+08:00',steps:[]}}
const flush=async()=>{for(let i=0;i<30;i++)await Promise.resolve()}
let documentState:EventTarget&{visibilityState:string}
function foreground(){documentState.visibilityState='visible';documentState.dispatchEvent(new Event('visibilitychange'))}
function mount(){const value=render();hooks.cleanup=hooks.effect?.()||undefined;return value}
let uninstall:()=>Promise<void>
beforeEach(()=>{
 uninstall=installTestTransport()
 hooks.cells=[];hooks.cursor=0;hooks.locale='en';hooks.effect=undefined;hooks.cleanup=undefined
 documentState=Object.assign(new EventTarget(),{visibilityState:'visible'})
 vi.stubGlobal('document',documentState)
})
afterEach(async()=>{hooks.cleanup?.();await uninstall();await flush();vi.restoreAllMocks();vi.unstubAllGlobals()})

describe('Readiness automatic trigger',()=>{
 it.each(['no-model','no-ledger','invalid-readiness'] as const)('does not call the model for %s',async reason=>{
  const s=ready()
  if(reason==='no-model')s.capabilities.agent=false
  if(reason==='no-ledger')delete s.readinessCheck
  if(reason==='invalid-readiness')s.readiness.quality='stale'
  vi.spyOn(api,'getSnapshot').mockResolvedValue(s)
  const chat=vi.spyOn(api,'chat').mockResolvedValue(undefined)
  mount();await flush();foreground();foreground();await flush()
  expect(chat).not.toHaveBeenCalled()
  expect(render().readinessBusy).toBe(false)
 })

 it('keeps ordinary refresh and rerender pure, including locale changes',async()=>{
  vi.spyOn(api,'getSnapshot').mockResolvedValue(ready())
  const chat=vi.spyOn(api,'chat').mockResolvedValue(undefined)
  const value=render();await value.refresh();await value.refresh()
  hooks.locale='zh-CN';render()
  expect(chat).not.toHaveBeenCalled()
 })

 it('merges initial/foreground triggers and never automatically retries the same failed key',async()=>{
  const initial=deferred<Snapshot>(),running=deferred<void>(),s=ready()
  const read=vi.spyOn(api,'getSnapshot').mockReturnValueOnce(initial.promise).mockResolvedValue(s)
  const chat=vi.spyOn(api,'chat').mockReturnValue(running.promise)
  mount();foreground();foreground();expect(read).toHaveBeenCalledTimes(1)
  initial.resolve(s);await flush()
  expect(chat).toHaveBeenCalledTimes(1)
  expect(chat.mock.calls[0][0]).toMatchObject({source:'app_open',checkMode:'auto',message:'',attachmentIds:[],locale:'en'})
  expect(render().readinessBusy).toBe(true);expect(render().chatBusy).toBe(false)
  foreground();await flush();expect(chat).toHaveBeenCalledTimes(1)
  running.reject(new ApiError('PROVIDER_ERROR'));await flush()
  expect(render().readinessError).toBe('PROVIDER_ERROR')
  foreground();foreground();await flush()
  expect(chat).toHaveBeenCalledTimes(1)
 })

 it('does not start while hidden, and starts once after a visible event',async()=>{
  documentState.visibilityState='hidden'
  vi.spyOn(api,'getSnapshot').mockResolvedValue(ready())
  const running=deferred<void>(),chat=vi.spyOn(api,'chat').mockReturnValue(running.promise)
  mount();await flush();expect(chat).not.toHaveBeenCalled()
  foreground();await flush();expect(chat).toHaveBeenCalledTimes(1)
 })

 it('reuses a cached result without clearing draft, selected target, or changing an old message',async()=>{
  let saved=ready();saved.messages=[message('earlier','failed')]
  vi.spyOn(api,'getSnapshot').mockImplementation(async()=>saved)
  const chat=vi.spyOn(api,'chat').mockImplementation(async(request,emit)=>{
   saved={...saved,revision:2,readinessCheck:{key:'check-1',status:'completed',messageId:'earlier'}}
   emit({type:'snapshot',requestId:request.requestId,resetEpoch:1,snapshot:saved})
   emit({type:'check_result',requestId:request.requestId,resetEpoch:1,checkKey:'check-1',outcome:'reused'})
  })
  let value=render();value.setDraft('My unsent question');value.setChatTarget({mealId:'meal-lunch'});value=render()
  await value.checkReadiness({mode:'auto'})
  expect(chat).toHaveBeenCalledTimes(1)
  expect(render().draft).toBe('My unsent question')
  expect(render().chatTarget).toEqual({mealId:'meal-lunch'})
  expect(render().snapshot!.messages).toEqual([message('earlier','failed')])
  await render().checkReadiness({mode:'auto'});expect(chat).toHaveBeenCalledTimes(1)
 })

 it('keeps locale changes on the same automatic run and does not change its request language',async()=>{
  let saved=ready();const running=deferred<void>()
  vi.spyOn(api,'getSnapshot').mockImplementation(async()=>saved)
  const chat=vi.spyOn(api,'chat').mockReturnValue(running.promise)
  vi.spyOn(api,'action').mockImplementation(async request=>{saved={...saved,locale:'zh-CN',revision:2};return {requestId:request.requestId,status:'succeeded',snapshot:saved}})
  mount();await flush();hooks.locale='zh-CN';let value=render()
  await value.runAction({kind:'set_locale',locale:'zh-CN'},'profile')
  expect(chat.mock.calls[0][0].locale).toBe('en')
  expect(chat.mock.calls[0][2].aborted).toBe(false)
  foreground();await flush();expect(chat).toHaveBeenCalledTimes(1)
 })
})

describe('Readiness and explicit user work',()=>{
 it('cancels automatic work before a user message and keeps its draft and attachment until user success',async()=>{
  const s=ready(),automatic=deferred<void>(),user=deferred<void>()
  vi.spyOn(api,'getSnapshot').mockResolvedValue(s)
  let oldEmit!:(event:ChatEvent)=>void
  const chat=vi.spyOn(api,'chat').mockImplementation((request,emit)=>{if(request.source==='app_open'){oldEmit=emit;return automatic.promise}return user.promise})
  mount();await flush()
  let value=render();value.setDraft('Log this photo');value.setChatTarget({mealId:'meal-lunch'});value=render()
  const sending=value.sendMessage('Log this photo',[{id:'image-1',url:'/meal.png',name:'meal.png',mediaType:'image/png',purpose:'food'}])
  await flush()
  expect(chat).toHaveBeenCalledTimes(1)
  expect(chat.mock.calls[0][2].aborted).toBe(true)
  automatic.reject(new DOMException('Aborted','AbortError'));await flush()
  expect(chat).toHaveBeenCalledTimes(2)
  expect(chat.mock.calls[1][0]).toMatchObject({source:'user',attachmentIds:['image-1'],targetMealId:'meal-lunch'})
  expect(render().draft).toBe('Log this photo');expect(render().readinessBusy).toBe(false);expect(render().chatBusy).toBe(true)
  oldEmit({type:'message',requestId:chat.mock.calls[0][0].requestId,resetEpoch:1,message:message('late-auto')})
  expect(render().snapshot!.messages).toEqual([])
  user.resolve();await sending
  expect(render().draft).toBe('');foreground();await flush();expect(chat).toHaveBeenCalledTimes(2)
 })

 it('lets a normal Start preempt the automatic stream after checking saved facts',async()=>{
  let saved=ready();const automatic=deferred<void>(),order:string[]=[]
  vi.spyOn(api,'getSnapshot').mockImplementation(async()=>{order.push('read');return saved})
  const chat=vi.spyOn(api,'chat').mockReturnValue(automatic.promise)
  const action=vi.spyOn(api,'action').mockImplementation(async request=>{order.push('start');saved={...saved,revision:2,workout:{...saved.workout!,status:'in_progress'}};return {requestId:request.requestId,status:'succeeded',snapshot:saved}})
  mount();await flush();order.length=0
  const pending=render().runAction({kind:'start_workout',workoutId:saved.workout!.id,expectedWorkoutVersion:1})
  await flush();expect(chat.mock.calls[0][2].aborted).toBe(true)
  expect(action).not.toHaveBeenCalled()
  automatic.reject(new DOMException('Aborted','AbortError'))
  const result=await pending
  expect(order).toEqual(['read','start'])
  expect(action).toHaveBeenCalledTimes(1);expect(result?.snapshot?.workout?.status).toBe('in_progress')
 })

 it('defers all foreground requests during a user chat, then checks the latest eligible data once',async()=>{
  let saved=ready();const user=deferred<void>(),automatic=deferred<void>()
  vi.spyOn(api,'getSnapshot').mockImplementation(async()=>saved)
  const chat=vi.spyOn(api,'chat').mockImplementation(request=>request.source==='user'?user.promise:automatic.promise)
  let value=render();await value.refresh();value=render();const sending=value.sendMessage('Question')
  hooks.cleanup=hooks.effect?.()||undefined;foreground();foreground();await flush()
  expect(chat).toHaveBeenCalledTimes(1)
  saved={...saved,revision:2,readiness:{...saved.readiness,version:2},readinessCheck:{key:'updated-check',status:'idle'}}
  user.resolve();await sending;await flush()
  expect(chat).toHaveBeenCalledTimes(2)
  expect(chat.mock.calls[1][0]).toMatchObject({source:'app_open',resetEpoch:1})
  foreground();await flush();expect(chat).toHaveBeenCalledTimes(2)
 })

 it('discards the old run on reset and starts only the accepted new epoch',async()=>{
  let saved=ready();const runs=[deferred<void>(),deferred<void>()],emitters:((event:ChatEvent)=>void)[]=[]
  vi.spyOn(api,'getSnapshot').mockImplementation(async()=>saved)
  const chat=vi.spyOn(api,'chat').mockImplementation((_request,emit)=>{emitters.push(emit);return runs[emitters.length-1].promise})
  vi.spyOn(api,'action').mockImplementation(async request=>{saved=ready(2);return {requestId:request.requestId,status:'succeeded',snapshot:saved}})
  mount();await flush()
  await render().runAction({kind:'reset_demo',scenario:'low_recovery'},'profile');await flush()
  expect(chat.mock.calls.map(call=>call[0].resetEpoch)).toEqual([1,2])
  emitters[0]({type:'message',requestId:chat.mock.calls[0][0].requestId,resetEpoch:1,message:message('old')})
  runs[0].reject(new ApiError('PROVIDER_ERROR'));await flush()
  expect(render().snapshot!.resetEpoch).toBe(2);expect(render().snapshot!.messages).toEqual([])
  expect(render().readinessError).toBeNull();expect(render().readinessBusy).toBe(true)
 })

 it('does not POST an explicit retry if its prerequisite read fails',async()=>{
  const read=vi.spyOn(api,'getSnapshot').mockResolvedValueOnce(ready()).mockRejectedValue(new ApiError('NETWORK_ERROR'))
  const chat=vi.spyOn(api,'chat').mockResolvedValue(undefined)
  let value=render();await value.refresh();value=render()
  await expect(value.checkReadiness({mode:'retry'})).rejects.toThrow('NETWORK_ERROR')
  expect(read).toHaveBeenCalledTimes(2);expect(chat).not.toHaveBeenCalled()
 })

 it('does not repeat the same automatic key after a failed reset',async()=>{
  const s=ready(),automatic=deferred<void>()
  vi.spyOn(api,'getSnapshot').mockResolvedValue(s)
  const chat=vi.spyOn(api,'chat').mockReturnValue(automatic.promise)
  vi.spyOn(api,'action').mockResolvedValue({requestId:'reset-failed',status:'failed',errorCode:'NETWORK_ERROR'})
  mount();await flush()
  await render().runAction({kind:'reset_demo',scenario:'low_recovery'},'profile')
  foreground();await flush()
  expect(chat).toHaveBeenCalledTimes(1)
  expect(render().snapshot!.resetEpoch).toBe(1)
 })

 it('honors Stop while a user request waits for automatic cancellation and a fresh read',async()=>{
  const s=ready(),automatic=deferred<void>(),reconciliation=deferred<Snapshot>()
  const read=vi.spyOn(api,'getSnapshot').mockResolvedValueOnce(s).mockReturnValueOnce(reconciliation.promise).mockResolvedValue(s)
  const chat=vi.spyOn(api,'chat').mockReturnValue(automatic.promise)
  mount();await flush()
  let value=render();value.setDraft('Keep this question');value=render()
  const sending=value.sendMessage('Keep this question').catch(error=>error)
  await flush();expect(read).toHaveBeenCalledTimes(1)
  automatic.reject(new DOMException('Aborted','AbortError'));await flush()
  expect(read).toHaveBeenCalledTimes(2)
  render().stopChat();await flush()
  expect((await sending).name).toBe('AbortError')
  reconciliation.resolve(s);await flush()
  expect(chat).toHaveBeenCalledTimes(1)
  expect(render().draft).toBe('Keep this question')
  expect(render().chatBusy).toBe(false)
 })

 it('keeps an explicitly stopped check distinct from a connection failure',async()=>{
  const s=ready(),running=deferred<void>()
  vi.spyOn(api,'getSnapshot').mockResolvedValue(s)
  vi.spyOn(api,'chat').mockReturnValue(running.promise)
  const checking=render().checkReadiness({mode:'retry'}).catch(error=>error)
  await flush();render().stopChat();running.reject(new DOMException('Aborted','AbortError'));await checking
  expect(render().readinessError).toBeNull()
  expect(render().readinessBusy).toBe(false)
 })

 it.each(['applied','dismissed','completed','pending'] as const)('does not restart a %s check on explicit retry',async status=>{
  const s=ready();s.readinessCheck!.status=status
  vi.spyOn(api,'getSnapshot').mockResolvedValue(s)
  const chat=vi.spyOn(api,'chat').mockResolvedValue(undefined)
  await render().checkReadiness({mode:'retry'})
  expect(chat).not.toHaveBeenCalled()
 })
})

describe('Readiness network completion',()=>{
 const request:ChatRequest={requestId:'run-check',resetEpoch:1,conversationId:'conversation',message:'',attachmentIds:[],source:'app_open',checkMode:'auto',locale:'en'}
 function response(){const snapshot:ChatEvent={type:'snapshot',requestId:request.requestId,resetEpoch:1,snapshot:{...ready(),conversationId:request.conversationId,readinessCheck:{key:'check-1',status:'completed'}}};const result:ChatEvent={type:'check_result',requestId:request.requestId,resetEpoch:1,checkKey:'check-1',outcome:'reused'};return aguiResponse([snapshot,result],request)}
 it('accepts a checked snapshot and app_open control result without inventing a message',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(response()))
  const emit=vi.fn();await expect(api.chat(request,emit,new AbortController().signal)).resolves.toBeUndefined()
  expect(emit.mock.calls.map(call=>call[0].type)).toEqual(['snapshot','check_result'])
 })
 it('rejects a control result on an ordinary user chat',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(response()))
  await expect(api.chat({...request,source:'user',message:'hello',checkMode:undefined},vi.fn(),new AbortController().signal)).rejects.toThrow('STREAM_PROTOCOL_ERROR')
 })
 it('rejects a control result without its authoritative matching snapshot',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(aguiResponse([{type:'check_result',requestId:request.requestId,resetEpoch:1,checkKey:'check-1',outcome:'reused'}],request)))
  await expect(api.chat(request,vi.fn(),new AbortController().signal)).rejects.toThrow('STREAM_PROTOCOL_ERROR')
 })
})
