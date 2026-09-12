import {createFixture} from '../fixtures'
import {demoReadings, type ReadinessDemoMode} from '../readiness-demo'
import type {ActionRequest, ActionResult, Attachment, ChatEvent, ChatRequest, ContextVersions, LocalizedText, Meal, Message, Nutrients, OperationType, Proposal, Snapshot, ToolStep, WellioClient} from '../contracts'

const l=(en:string,zh:string):LocalizedText=>({en,'zh-CN':zh})
const clone=<T>(value:T):T=>structuredClone(value)
export const demoQuestions = [
 {id:'recovery',label:'低准备度与顺延',prompt:'昨晚没睡好，今天的训练怎么安排？',mode:'low'},
 {id:'normal',label:'按原计划训练',prompt:'今天在 Gym B，只有 35 分钟，按原计划练吧。',mode:'balanced'},
 {id:'increase',label:'预览小幅加重',prompt:'今天状态很好，可以稍微加点重量吗？',mode:'high'},
 {id:'complete',label:'记录划船完成',prompt:'坐姿绳索划船做完了，35 公斤，3 组 12 次。'},
 {id:'reorder',label:'器械暂时占用',prompt:'拉力器暂时被占了，把不用它的动作放前面。'},
 {id:'short',label:'只有20分钟',prompt:'今天只能练20分钟，帮我调整一下。'},
 {id:'menu',label:'挑选晚餐',prompt:'用这份 Harbour Bowl 菜单帮我选晚餐，预算 100 港币，不吃海鲜。'},
 {id:'dinner',label:'记录晚餐',prompt:'我吃了刚才推荐的鸡肉碗和鸡蛋，帮我记作晚餐。'},
 {id:'half',label:'米饭改半份',prompt:'刚才晚餐的米饭只吃了一半，其他都吃完了。'},
 {id:'undo',label:'撤销刚才修改',prompt:'撤销刚才的修改。'},
] satisfies {id:string;label:string;prompt:string;mode?:ReadinessDemoMode}[]
const STORAGE_KEY='wellio-scripted-demo-v1'
interface Envelope {schemaVersion:1;mode:ReadinessDemoMode;snapshot:Snapshot;requests:string[];undo:Record<string,Meal[]>;proposalTargets:Record<string,Nutrients>}
interface Options {storage?:Pick<Storage,'getItem'|'setItem'>;delayMs?:number}
function seed(mode:ReadinessDemoMode, previous?:Snapshot):Envelope {
 const s=createFixture(mode==='low'?'low_recovery':'normal'), r=demoReadings[mode]
 s.schemaVersion=1;s.sessionId=previous?.sessionId??'scripted-demo-session';s.resetEpoch=(previous?.resetEpoch??0)+1;s.conversationId=`demo-${s.resetEpoch}`;s.locale=previous?.locale??'zh-CN'
 s.readiness={...s.readiness,id:`demo-readiness-${mode}`,score:r.score,scoreScale:100,restingHeartRate:r.restingHeartRate}
 s.sleep={...s.sleep!,minutes:r.sleepMinutes};s.capabilities={agent:true,menuSearch:true,persistence:'preview'}
 s.advice={status:'valid',training:l('Ask the Agent to review today’s plan.','向 Agent 询问今日训练建议，确认后才会调整。'),nutrition:l('Your current target is 2400 kcal.','当前目标为 2400 kcal，建议调整需确认。')}
 s.readinessCheck={key:`demo-${s.resetEpoch}`,status:'completed'}
 return {schemaVersion:1,mode,snapshot:s,requests:[],undo:{},proposalTargets:{}}
}
export function nutritionTotal(meals:Meal[]):Nutrients {
 const total:Nutrients={kcal:0,protein:0,carbs:0,fat:0}
 for(const meal of meals)for(const item of meal.items)for(const key of Object.keys(total) as (keyof Nutrients)[])total[key]+=item.base[key]*item.consumedFraction
 return total
}
const versions=(s:Snapshot):ContextVersions=>({meal:s.mealRevision,plan:s.plan.version,workout:s.workout?.version??0,conditions:s.conditions.version,readiness:s.readiness.version})
function propose(e:Envelope,id:string,kind:string):Proposal {
 const s=e.snapshot
 for(const old of s.proposals)if(old.status==='pending')old.status='stale'
 const p:Proposal={id:`proposal-${id}`,scope:kind==='recovery'?'schedule':'workout',status:'pending',reason:l('Review and confirm this adjustment.','请查看调整，确认后才会更新。'),expected:versions(s),contextReadId:`context-${id}`,readinessSnapshotId:s.readiness.id,resetEpoch:s.resetEpoch}
 if(kind==='recovery'){
  const slots=s.plan.availableSlots.filter(slot=>slot.date>s.dayKey && !s.plan.sessions.some(session=>session.status==='completed'&&session.date===slot.date))
  p.restDate=s.dayKey;p.moves=s.plan.sessions.filter(session=>session.status==='pending').map((session,i)=>({sessionId:session.id,split:session.split,from:session.date,to:slots[i]?.date??null}))
  e.proposalTargets[p.id]={kcal:2200,protein:140,carbs:230,fat:80}
 }else if(s.workout){
  p.workout=clone(s.workout);p.workout.source='agent_proposal'
  if(kind==='increase'){const row=p.workout.exercises.find(ex=>ex.catalogId==='seated-cable-row'&&!ex.completed);if(row)row.suggestedLoad={...row.suggestedLoad,value:37.5};e.proposalTargets[p.id]={kcal:2500,protein:140,carbs:305,fat:80}}
  if(kind==='reorder'){p.workout.exercises=[...p.workout.exercises.filter(ex=>ex.completed),...p.workout.exercises.filter(ex=>!ex.completed&&ex.suggestedLoad.basis==='per_hand'),...p.workout.exercises.filter(ex=>!ex.completed&&ex.suggestedLoad.basis!=='per_hand')]}
  if(kind==='short'){p.workout.estimatedMinutes=20;p.workout.exercises=p.workout.exercises.map(ex=>ex.completed?ex:{...ex,sets:2,restSeconds:60})}
 }
 s.proposals.push(p);return p
}
function dinner():Meal{return {id:'demo-dinner',version:1,period:'dinner',time:'19:00',items:[
 {id:'demo-chicken',name:l('Chicken and vegetables','鸡肉与配菜'),portion:l('One serving','一份'),base:{kcal:480,protein:40,carbs:26,fat:24},consumedFraction:1,estimated:true},
 {id:'demo-rice',name:l('Rice','米饭'),portion:l('One serving','一份'),base:{kcal:200,protein:4,carbs:46,fat:0},consumedFraction:1,estimated:true},
 {id:'demo-egg',name:l('Egg','鸡蛋'),portion:l('One egg','一个'),base:{kcal:80,protein:7,carbs:4,fat:4},consumedFraction:1,estimated:true},
]}}
export function matchDemoScript(text:string):string {
 const explicit=text.match(/^\[demo:([a-z]+)\]/)?.[1];if(explicit)return explicit
 if(/撤销|undo/i.test(text))return 'undo'
 if(/一半|半份|half/i.test(text))return 'half'
 if(/做完|完成.*划船|\bfinished\b.*\brows?\b|\bcompleted\b.*\brows?\b/i.test(text))return 'complete'
 if(/吃了|记作|记为|记录.*晚餐|\blog\b.*\bdinner\b|\bate\b/i.test(text))return 'dinner'
 if(/占用|被占|occupied|busy/i.test(text))return 'reorder'
 if(/20\s*分|二十分钟|20\s*min/i.test(text))return 'short'
 if(/菜单|晚餐|推荐一餐|下一餐|吃什么|Harbour|\bmenu\b|\bdinner\b|\bmeal\b/i.test(text))return 'menu'
 if(/加重|加点重量|状态很好|heavier|increase/i.test(text))return 'increase'
 if(/没睡好|休息|睡眠|准备度|recovery|readiness/i.test(text))return 'recovery'
 if(/训练|原计划|35|workout|training/i.test(text))return 'normal'
 return 'fallback'
}
/** Script-local stages describe bundled data work, never a remote search. */
export function demoToolSequence(kind:string):OperationType[] {
 if(['recovery','normal','increase','reorder','short'].includes(kind))return ['context','history','workout_proposal']
 if(kind==='menu')return ['context','menu_search']
 if(kind==='dinner')return ['context','meal_add']
 if(kind==='half')return ['context','meal_update']
 if(kind==='undo')return ['context','meal_undo']
 if(kind==='complete')return ['context','workout_progress']
 return ['context']
}
/** Split by Unicode code points so supplementary characters are never torn apart. */
export function demoTextChunks(text:string,locale:'en'|'zh-CN'):string[] {
 const points=Array.from(text),size=locale==='zh-CN'?4:10,chunks:string[]=[]
 for(let index=0;index<points.length;index+=size)chunks.push(points.slice(index,index+size).join(''))
 return chunks
}
function respond(e:Envelope,r:ChatRequest,m:Message):string {
 const s=e.snapshot,zh=r.locale==='zh-CN',t=(en:string,cn:string)=>zh?cn:en
 let kind=matchDemoScript(r.message)
 if(r.attachmentIds.some(id=>!id.startsWith('demo-sample-'))&&!/演示样例|sample/i.test(r.message))return t('This demo cannot identify an arbitrary upload. Ask “Use the sample menu” to continue with the packaged Harbour Bowl example.','这张上传图片未做识别。可以输入“使用演示样例菜单”，继续演示 Harbour Bowl 菜单。')
 if(kind==='recovery'||kind==='increase'||kind==='normal')kind=e.mode==='low'?'recovery':e.mode==='high'?'increase':'normal'
 if(kind==='recovery'||kind==='increase'||kind==='reorder'||kind==='short'){
  if(s.plan.restDates.includes(s.dayKey))return t('Today is already a rest day. Your pending sessions have been moved.','今天已安排休息，待训练已顺延。')
  const p=propose(e,r.requestId,kind);m.proposalId=p.id
  if(kind==='recovery'){
   const score=(s.readiness.score??0)/s.readiness.scoreScale*10,minutes=s.sleep?.minutes??0
   const moves=p.moves?.map(move=>`${move.split} → ${move.to??t('unscheduled','待安排')}`).join('；')??''
   const target=e.proposalTargets[p.id]
   return t(`Readiness is ${score}/10 after ${Math.floor(minutes/60)} h ${minutes%60} min sleep. Rest today; pending sessions: ${moves}. Suggested target: ${target.kcal} kcal and ${target.protein} g protein. Apply to confirm.`,`今天准备度 ${score}/10，昨晚睡了 ${Math.floor(minutes/60)} 小时 ${minutes%60} 分钟。建议今天休息，待训练顺延为 ${moves}。建议目标为 ${target.kcal} kcal、蛋白质 ${target.protein} g。点击 Apply 后更新。`)
  }
  if(kind==='increase')return t('Readiness is 8.6/10 after 7 h 48 min sleep. Preview row 35 → 37.5 kg; sets, reps and other exercises stay the same. Suggested target: 2500 kcal. Apply to confirm.','今天准备度 8.6/10，昨晚睡了 7 小时 48 分钟。建议划船从 35 kg 调至 37.5 kg，组数、次数及其他动作不变。建议目标为 2500 kcal。点击 Apply 后更新。')
  if(kind==='short')return t('A 20-minute option is ready: two sets per remaining exercise, with 60-second rests. Completed exercises are preserved. Apply to confirm.','已准备 20 分钟方案：未完成动作调整为 2 组，组间休息 60 秒；已完成动作保留。点击 Apply 后更新。')
  return t('Keep completed exercises. Do dumbbell curls first, then return to the cable machine. Apply to update the remaining order.','已完成动作保留。先做哑铃弯举，再回到拉力器做剩余动作。点击 Apply 更新顺序。')
 }
 if(kind==='normal'){const w=s.workout!;return t(`Keep your ${w.estimatedMinutes}-minute plan: ${w.exercises.map(ex=>`${ex.name.en} ${ex.suggestedLoad.value} kg${ex.suggestedLoad.basis==='per_hand'?' per hand':''}, ${ex.sets}×${ex.reps}`).join('; ')}. Current target: ${s.profile.targets.kcal} kcal. Start from the Workout page.`,`按当前 ${w.estimatedMinutes} 分钟计划训练：${w.exercises.map(ex=>`${ex.name['zh-CN']} ${ex.suggestedLoad.value} kg，${ex.sets}×${ex.reps}`).join('；')}。当前目标 ${s.profile.targets.kcal} kcal。可进入训练页开始。`)}
 if(kind==='complete'){
  if(s.plan.restDates.includes(s.dayKey))return t('Today is a rest day. Switch to the balanced scenario to demonstrate training.','今天为休息日，请切换中准备度场景演示训练。')
  const w=s.workout!,ex=w.exercises.find(x=>x.catalogId==='seated-cable-row')!;if(!ex.completed){ex.completed=true;w.version++;w.status='in_progress'}
  return t(`Row recorded. ${w.exercises.filter(x=>x.completed).length}/${w.exercises.length} exercises complete.`,`已记录划船完成：${ex.suggestedLoad.value} kg，${ex.sets}×${ex.reps}。今天完成 ${w.exercises.filter(x=>x.completed).length}/${w.exercises.length} 个动作。`)
 }
 if(kind==='menu'){const now=nutritionTotal(s.meals);return t(`The local Harbour Bowl sample recommends chicken bowl HK$78 + egg HK$10 = HK$88: 760 kcal, 51 g protein. If eaten, today's total would be ${now.kcal+760} kcal, ${now.protein+51} g protein. Nothing logged yet.`,`本地 Harbour Bowl 样例菜单推荐鸡肉碗 HK$78 + 鸡蛋 HK$10，共 HK$88，约 760 kcal、51 g 蛋白质。若全部吃完，今天将到 ${now.kcal+760} kcal、${now.protein+51} g 蛋白质。目前只是推荐，尚未计入摄入。`)}
 if(kind==='dinner'||kind==='half'){
  let meal=s.meals.find(x=>x.id==='demo-dinner')
  if(kind==='half'&&!meal)return t('Log the sample dinner first, then adjust its rice portion.','请先记录样例晚餐，再修改米饭份量。')
  const changed=kind==='dinner'?!meal:meal!.items.find(x=>x.id==='demo-rice')!.consumedFraction!==0.5
  if(changed){const op=`meal-op-${r.requestId}`;e.undo[op]=clone(s.meals);m.operationId=op;if(!meal){meal=dinner();meal.operationId=op;s.meals.push(meal)}else{meal.items.find(x=>x.id==='demo-rice')!.consumedFraction=0.5;meal.version++}s.mealRevision++}
  m.mealId=meal!.id;const total=nutritionTotal(s.meals),mt=nutritionTotal([meal!])
  return t(`Dinner saved: ${mt.kcal} kcal, ${mt.protein} g protein. Today: ${total.kcal} kcal, ${total.protein} g protein.`,`晚餐${kind==='half'?'米饭已改为半份，其他保持不变':'已记录'}：${mt.kcal} kcal、${mt.protein} g 蛋白质。今天累计 ${total.kcal} kcal、${total.protein} g 蛋白质。`)
 }
 if(kind==='undo'){
  const op=r.targetOperationId??Object.keys(e.undo).at(-1);if(!op||!e.undo[op])return t('There is no meal change to undo.','当前没有可撤销的餐食修改。')
  s.meals=clone(e.undo[op]);delete e.undo[op];s.mealRevision++;const n=nutritionTotal(s.meals)
  return t(`Change undone. Today: ${n.kcal} kcal, ${n.protein} g protein.`,`已撤销刚才的餐食操作。今天累计 ${n.kcal} kcal、${n.protein} g 蛋白质。`)
 }
 return t('Try asking about today’s training, “I only have 20 minutes”, or “Help me choose dinner from the sample menu”.','可以问我“今天的训练怎么安排”“今天只能练20分钟”，或“用样例菜单帮我选晚餐”。')
}
export function createDemoClient(options:Options={}) {
 let data=seed('balanced'),loaded=false
 const storage=()=>options.storage??(typeof window!=='undefined'?window.localStorage:undefined)
 const load=()=>{if(loaded)return;loaded=true;try{const raw=storage()?.getItem(STORAGE_KEY);if(raw){const parsed=JSON.parse(raw) as Envelope;if(parsed.schemaVersion===1&&parsed.snapshot?.capabilities?.persistence==='preview'&&parsed.undo&&parsed.proposalTargets&&Array.isArray(parsed.requests)&&['low','balanced','high'].includes(parsed.mode))data=parsed}}catch{/* Broken or unavailable browser storage starts a fresh demo. */}}
 const save=(next:Envelope)=>{storage()?.setItem(STORAGE_KEY,JSON.stringify(next));data=next}
 const reset=(mode:ReadinessDemoMode)=>{load();save(seed(mode,data.snapshot))}
 const action=async(r:ActionRequest,signal?:AbortSignal):Promise<ActionResult>=>{
  load();if(signal?.aborted)throw new DOMException('Stopped','AbortError')
  const fail=(errorCode:string):ActionResult=>({requestId:r.requestId,status:'failed',errorCode})
  if(r.resetEpoch!==data.snapshot.resetEpoch)return fail('SESSION_RESET')
  if(data.requests.includes(r.requestId))return {requestId:r.requestId,status:'succeeded',snapshot:clone(data.snapshot)}
  if(r.kind==='reset_demo'){reset(r.scenario==='low_recovery'?'low':data.mode==='low'?'balanced':data.mode);return {requestId:r.requestId,status:'succeeded',snapshot:clone(data.snapshot)}}
  const e=clone(data),s=e.snapshot,w=s.workout
  if(r.kind==='set_locale')s.locale=r.locale
  else if(r.kind==='check_readiness')s.readinessCheck={key:`demo-${s.resetEpoch}`,status:'completed'}
  else if(r.kind==='request_proposal'){if(e.mode!=='balanced')propose(e,r.requestId,e.mode==='low'?'recovery':'increase')}
  else if(r.kind==='apply_proposal'||r.kind==='dismiss_proposal'){
   const p=s.proposals.find(x=>x.id===r.proposalId);if(!p)return fail('NOT_FOUND')
   if(p.status==='applied')return {requestId:r.requestId,status:'succeeded',snapshot:clone(s)}
   if(p.status!=='pending')return fail('PROPOSAL_STALE')
   if(r.kind==='dismiss_proposal')p.status='dismissed'
   else {
    if(JSON.stringify(p.expected)!==JSON.stringify(versions(s)))return fail('PROPOSAL_STALE')
    if(p.workout){s.workout=clone(p.workout);s.workout.version++;if(r.startAfterApply){s.workout.status='in_progress';s.workout.startedAt=new Date().toISOString()}}
    if(p.moves){for(const move of p.moves){const session=s.plan.sessions.find(x=>x.id===move.sessionId);if(session&&session.status==='pending'){session.date=move.to;session.slotId=s.plan.availableSlots.find(x=>x.date===move.to)?.id??null;if(s.workout?.trainingSessionId===session.id)s.workout.dayKey=move.to}}s.plan.version++}
    if(p.restDate&&!s.plan.restDates.includes(p.restDate))s.plan.restDates.push(p.restDate)
    if(e.proposalTargets[p.id])s.profile.targets=clone(e.proposalTargets[p.id])
    p.status='applied';s.advice={status:'valid',training:p.scope==='schedule'?l('Rest today. Pending training has been moved.','今日休息，后续训练已顺延。'):l('Your training adjustment is applied.','训练调整已应用。'),nutrition:l(`Target: ${s.profile.targets.kcal} kcal.`,`当前目标 ${s.profile.targets.kcal} kcal。`)}
   }
  }else if(r.kind==='undo_meal'){
   if(e.undo[r.operationId]){s.meals=clone(e.undo[r.operationId]);delete e.undo[r.operationId];s.mealRevision++}
  }else if(r.kind==='start_workout'||r.kind==='complete_exercise'||r.kind==='undo_exercise'||r.kind==='finish_workout'){
   if(!w||w.id!==r.workoutId)return fail('NOT_FOUND')
   if(s.plan.restDates.includes(s.dayKey)||w.dayKey!==s.dayKey)return fail('REST_DAY')
   if(r.expectedWorkoutVersion!==w.version)return fail('VERSION_CONFLICT')
   if(r.kind==='start_workout'){w.status='in_progress';w.startedAt??=new Date().toISOString()}
   else if(r.kind==='finish_workout'){
    if(w.exercises.some(x=>!x.completed)&&!r.confirmIncomplete)return {requestId:r.requestId,status:'needs_input',errorCode:'INCOMPLETE_WORKOUT'}
    w.status='completed';w.endedAt=new Date().toISOString();w.actualMinutes=r.actualMinutes
    if(!s.history.training.some(x=>x.workoutId===w.id))s.history.training.push({date:s.dayKey,type:'Pull',minutes:r.actualMinutes,workoutId:w.id,trainingSessionId:w.trainingSessionId})
   }else{const ex=w.exercises.find(x=>x.id===r.exerciseId);if(!ex)return fail('NOT_FOUND');ex.completed=r.kind==='complete_exercise';w.status='in_progress'}
   w.version++;const session=s.plan.sessions.find(x=>x.id===w.trainingSessionId);if(session){session.status=w.status==='completed'?'completed':'in_progress';s.plan.pendingSessionIds=s.plan.sessions.filter(x=>x.status==='pending').map(x=>x.id)}
  }
  s.revision++;e.requests.push(r.requestId);save(e)
  return {requestId:r.requestId,resetEpoch:s.resetEpoch,status:'succeeded',snapshot:clone(s)}
 }
 const chat=async(r:ChatRequest,onEvent:(event:ChatEvent)=>void,signal:AbortSignal)=>{
  load();const epoch=data.snapshot.resetEpoch
  if(r.resetEpoch!==epoch||r.conversationId!==data.snapshot.conversationId)return
  const base={requestId:r.requestId,resetEpoch:epoch},id=`assistant-${r.requestId}`
  if(data.requests.includes(r.requestId)){onEvent({...base,type:'snapshot',snapshot:clone(data.snapshot)});onEvent({...base,type:'done',messageId:id});return}
  const valid=()=>!signal.aborted&&data.snapshot.resetEpoch===epoch
  const pause=async(ms=350)=>{if(!valid())throw new DOMException('Stopped','AbortError');await new Promise<void>((resolve,reject)=>{const stop=()=>{clearTimeout(timer);reject(new DOMException('Stopped','AbortError'))};const timer=setTimeout(()=>{signal.removeEventListener('abort',stop);resolve()},options.delayMs??ms);signal.addEventListener('abort',stop,{once:true})});if(!valid())throw new DOMException('Stopped','AbortError')}
  const user:Message={id:`user-${r.requestId}`,role:'user',content:r.message,createdAt:new Date().toISOString(),source:'user',status:'complete',steps:[]}
  const assistant:Message={id,role:'assistant',content:'',createdAt:new Date().toISOString(),source:'agent',status:'streaming',steps:[]}
  const emit=(event:ChatEvent)=>{if(valid())onEvent(event)}
  emit({...base,type:'message',message:user});emit({...base,type:'message',message:clone(assistant)});emit({...base,type:'phase',messageId:id,phase:r.attachmentIds.length?'recognizing':'thinking'})
  try{
   await pause()
   const operations=demoToolSequence(matchDemoScript(r.message))
   for(let index=0;index<operations.length;index++){
    const step:ToolStep={id:`step-${id}-${index}`,toolCallId:`tool-${id}-${index}`,operation:operations[index],status:'started'}
    assistant.steps.push(step);emit({...base,type:'tool',messageId:id,step:clone(step)});await pause()
    // The final stage succeeds only after its local state commit below.
    if(index<operations.length-1){step.status='succeeded';emit({...base,type:'tool',messageId:id,step:clone(step)})}
   }
   // All mutations are staged and committed together after cancellation checks.
   const expectedRevision=data.snapshot.revision,e=clone(data),answer=respond(e,r,assistant)
   assistant.steps.at(-1)!.status='succeeded'
   if(!valid())return
   if(data.snapshot.revision!==expectedRevision){emit({...base,type:'error',messageId:id,errorCode:'VERSION_CONFLICT'});return}
   // Persist the result before displaying any claim that an operation succeeded.
   assistant.content=answer;assistant.status='complete';e.snapshot.messages.push(user,clone(assistant));e.snapshot.revision++;e.requests.push(r.requestId);save(e)
   emit({...base,type:'tool',messageId:id,step:clone(assistant.steps.at(-1)!)})
   const chunks=demoTextChunks(answer,r.locale)
   for(const chunk of chunks){await pause(40);emit({...base,type:'text',messageId:id,delta:chunk})}
   emit({...base,type:'snapshot',snapshot:clone(data.snapshot)});emit({...base,type:'done',messageId:id})
  }catch(error){if(!signal.aborted&&data.snapshot.resetEpoch===epoch)throw error}
 }
 const client:WellioClient={getSnapshot:async()=>{load();return clone(data.snapshot)},action,chat,upload:async(file,purpose,signal)=>{
  if(signal?.aborted)throw new DOMException('Stopped','AbortError')
  if(file.size>5*1024*1024)throw new Error('IMAGE_TOO_LARGE')
  const url=await new Promise<string>((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result));reader.onerror=()=>reject(reader.error);reader.readAsDataURL(file)})
  const attachment:Attachment={id:`uploaded-${Date.now()}`,url,name:file.name,mediaType:file.type,purpose};return attachment
 }}
 return {client,setScenario:reset}
}
const singleton=createDemoClient()
export const demoClient=singleton.client
export const setDemoScenario=singleton.setScenario
