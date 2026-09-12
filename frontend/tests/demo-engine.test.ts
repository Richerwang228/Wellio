import {describe,it,expect} from 'vitest'
import {createDemoClient,nutritionTotal,matchDemoScript,demoToolSequence,demoTextChunks} from '../src/lib/demo'
import type {ActionInput,ChatEvent,WellioClient} from '../src/lib/contracts'
function setup(){const values=new Map<string,string>();const storage={getItem:(key:string)=>values.get(key)??null,setItem:(key:string,value:string)=>{values.set(key,value)}};return {...createDemoClient({storage,delayMs:0}),storage}}
let serial=0
async function chat(client:WellioClient,message:string,requestId=`chat-${serial++}`){const s=await client.getSnapshot(),events:ChatEvent[]=[];await client.chat({requestId,resetEpoch:s.resetEpoch,conversationId:s.conversationId,message,locale:'zh-CN',attachmentIds:[],source:'user'},e=>events.push(e),new AbortController().signal);return events}
async function action(client:WellioClient,input:ActionInput,requestId=`action-${serial++}`){const s=await client.getSnapshot();return client.action({...input,requestId,resetEpoch:s.resetEpoch,source:'agent'})}
describe('scripted demo state',()=>{
 it('uses three readiness presets, preserves identity and restores storage',async()=>{const d=setup();const first=await d.client.getSnapshot();for(const [mode,score,sleep] of [['low',38,312],['balanced',65,405],['high',86,468]] as const){d.setScenario(mode);const s=await d.client.getSnapshot();expect(s.sessionId).toBe(first.sessionId);expect(s.readiness.score).toBe(score);expect(s.sleep?.minutes).toBe(sleep);expect(s.profile.targets.kcal).toBe(2400);expect(s.resetEpoch).toBeGreaterThan(first.resetEpoch)}const restored=createDemoClient({storage:d.storage});expect(await restored.client.getSnapshot()).toEqual(await d.client.getSnapshot())})
 it('stages recovery then applies schedule and nutrition exactly once',async()=>{const d=setup();d.setScenario('low');await chat(d.client,'昨晚没睡好，今天的训练怎么安排？');let s=await d.client.getSnapshot();expect(s.plan.restDates).toEqual([]);expect(s.profile.targets.kcal).toBe(2400);const p=s.proposals[0];await action(d.client,{kind:'apply_proposal',proposalId:p.id,startAfterApply:false});await action(d.client,{kind:'apply_proposal',proposalId:p.id,startAfterApply:false});s=await d.client.getSnapshot();expect(s.plan.sessions.map(x=>x.date)).toEqual(['2026-09-14','2026-09-16','2026-09-18']);expect(s.plan.restDates).toEqual(['2026-09-12']);expect(s.profile.targets.kcal).toBe(2200)})
 it('only applies high load after confirmation and does not change history',async()=>{const d=setup();d.setScenario('high');const original=await d.client.getSnapshot();await chat(d.client,'可以加点重量吗');let s=await d.client.getSnapshot();expect(s.workout?.exercises[0].suggestedLoad.value).toBe(35);await action(d.client,{kind:'apply_proposal',proposalId:s.proposals[0].id,startAfterApply:false});s=await d.client.getSnapshot();expect(s.workout?.exercises[0].suggestedLoad.value).toBe(37.5);expect(s.profile.targets.kcal).toBe(2500);expect(s.history.load).toEqual(original.history.load)})
 it('preserves completed exercises during reorder and deduplicates requests',async()=>{const d=setup();await chat(d.client,'划船做完了','same');await chat(d.client,'划船做完了','same');await chat(d.client,'拉力器被占了');let s=await d.client.getSnapshot();await action(d.client,{kind:'apply_proposal',proposalId:s.proposals[0].id,startAfterApply:false});s=await d.client.getSnapshot();expect(s.workout?.exercises.filter(x=>x.completed)).toHaveLength(1);expect(s.workout?.exercises.map(x=>x.catalogId)).toEqual(['seated-cable-row','dumbbell-curl','lat-pulldown']);expect(s.messages.filter(x=>x.id==='user-same')).toHaveLength(1)})
 it('recommendation does not log; dinner and half rice are idempotent, undo restores portions',async()=>{const d=setup();await chat(d.client,'用菜单帮我选晚餐');expect(nutritionTotal((await d.client.getSnapshot()).meals).kcal).toBe(1650);await chat(d.client,'我吃了刚才的晚餐');await chat(d.client,'我吃了刚才的晚餐');expect(nutritionTotal((await d.client.getSnapshot()).meals).kcal).toBe(2410);await chat(d.client,'米饭吃了一半');await chat(d.client,'米饭吃了一半');let s=await d.client.getSnapshot();expect(nutritionTotal(s.meals)).toMatchObject({kcal:2310,protein:139});const op=s.messages.find(x=>x.operationId&&x.content.toString().includes('半份'))!.operationId!;await action(d.client,{kind:'undo_meal',operationId:op});s=await d.client.getSnapshot();expect(nutritionTotal(s.meals)).toMatchObject({kcal:2410,protein:141});expect(s.meals).toHaveLength(3)})
 it('does not emit late callbacks or write after reset during thinking',async()=>{const d=setup();const s=await d.client.getSnapshot(),events:ChatEvent[]=[];await d.client.chat({requestId:'reset-during-chat',resetEpoch:s.resetEpoch,conversationId:s.conversationId,message:'我吃了晚餐',locale:'zh-CN',attachmentIds:[],source:'user'},event=>{events.push(event);if(event.type==='phase')d.setScenario('high')},new AbortController().signal);expect((await d.client.getSnapshot()).messages).toHaveLength(0);expect(events.some(e=>e.type==='snapshot')).toBe(false)})
 it('abort during thinking produces no meal mutation',async()=>{const d=setup(),controller=new AbortController(),s=await d.client.getSnapshot();await d.client.chat({requestId:'cancel',resetEpoch:s.resetEpoch,conversationId:s.conversationId,message:'我吃了晚餐',locale:'zh-CN',attachmentIds:[],source:'user'},event=>{if(event.type==='phase')controller.abort()},controller.signal);expect((await d.client.getSnapshot()).meals).toHaveLength(2)})
})

const presenterInputs = [
 ['recovery','I slept badly last night. How should I plan recovery and today’s workout?','昨晚没睡好，今天的训练怎么安排？'],
 ['normal','I’m at Gym B today and have 35 minutes. Let’s follow the original plan.','今天在 Gym B，只有 35 分钟，按原计划练吧。'],
 ['increase','I feel great today. Can I increase the weight a little?','今天状态很好，可以稍微加点重量吗？'],
 ['complete','I finished seated cable rows: 35 kg, 3 sets of 12 reps.','坐姿绳索划船做完了，35 公斤，3 组 12 次。'],
 ['reorder','The cable machine is temporarily busy. Put the exercises that don’t need it first.','拉力器暂时被占了，把不用它的动作放前面。'],
 ['menu','Use this Harbour Bowl menu to choose dinner for me. Budget HK$100, no seafood.','用这份 Harbour Bowl 菜单帮我选晚餐，预算 100 港币，不吃海鲜。'],
 ['dinner','I ate the chicken bowl and egg you recommended. Log it as dinner.','我吃了刚才推荐的鸡肉碗和鸡蛋，帮我记作晚餐。'],
 ['half','I only ate half the rice at dinner, but finished everything else.','刚才晚餐的米饭只吃了一半，其他都吃完了。'],
]
it.each(presenterInputs)('recognizes both presenter languages: %s',(id,en,zh)=>{expect(matchDemoScript(en)).toBe(id);expect(matchDemoScript(zh)).toBe(id)})
it('never moves a completed session when applying recovery',async()=>{const d=setup();d.setScenario('low');let s=await d.client.getSnapshot();await action(d.client,{kind:'finish_workout',workoutId:s.workout!.id,expectedWorkoutVersion:s.workout!.version,actualMinutes:35,confirmIncomplete:true});await chat(d.client,'昨晚没睡好，今天的训练怎么安排？');s=await d.client.getSnapshot();expect(s.proposals[0].moves?.some(x=>x.sessionId==='planned-pull-01')).toBe(false);await action(d.client,{kind:'apply_proposal',proposalId:s.proposals[0].id,startAfterApply:false});s=await d.client.getSnapshot();expect(s.plan.sessions[0]).toMatchObject({status:'completed',date:'2026-09-12'})})
it('rejects a stale proposal after workout progress changes',async()=>{const d=setup();await chat(d.client,'只有20分钟');let s=await d.client.getSnapshot();const p=s.proposals[0];await action(d.client,{kind:'complete_exercise',exerciseId:s.workout!.exercises[0].id,workoutId:s.workout!.id,expectedWorkoutVersion:s.workout!.version});const result=await action(d.client,{kind:'apply_proposal',proposalId:p.id,startAfterApply:false});expect(result.errorCode).toBe('PROPOSAL_STALE');s=await d.client.getSnapshot();expect(s.workout?.exercises[0].completed).toBe(true)})

it('uses distinct read and mutation stages for the demo operations',()=>{
 expect(demoToolSequence('increase')).toEqual(['context','history','workout_proposal'])
 expect(demoToolSequence('menu')).toEqual(['context','menu_search'])
 expect(demoToolSequence('dinner')).toEqual(['context','meal_add'])
 expect(demoToolSequence('half')).toEqual(['context','meal_update'])
})
it('streams small Unicode-safe chunks and reconstructs both language replies',()=>{
 for(const [locale,text,size] of [['zh-CN','已保存🥑晚餐，今天累计2410 kcal、141 g蛋白质。',4],['en','Dinner saved 🥑. Today: 2410 kcal and 141 g protein.',10]] as const){
  const chunks=demoTextChunks(text,locale);expect(chunks.join('')).toBe(text)
  for(const chunk of chunks){expect(Array.from(chunk).length).toBeLessThanOrEqual(size);expect(chunk).not.toMatch(/[\uD800-\uDBFF]$|^[\uDC00-\uDFFF]/)}
 }
})
it('emits ordered tool updates and displays text only after persistence',async()=>{
 const d=setup(),s=await d.client.getSnapshot(),events:ChatEvent[]=[]
 await d.client.chat({requestId:'stages',resetEpoch:s.resetEpoch,conversationId:s.conversationId,message:'我吃了刚才的晚餐',locale:'zh-CN',attachmentIds:[],source:'user'},event=>{
  events.push(event)
  if(event.type==='text')expect(d.storage.getItem('wellio-scripted-demo-v1')).toContain('demo-dinner')
 },new AbortController().signal)
 expect(events.filter(e=>e.type==='tool').map(e=>e.type==='tool'?`${e.step.operation}:${e.step.status}`:'')).toEqual(['context:started','context:succeeded','meal_add:started','meal_add:succeeded'])
 const text=events.filter(e=>e.type==='text').map(e=>e.type==='text'?e.delta:'');expect(text.length).toBeGreaterThan(5)
 expect(text.join('')).toBe((await d.client.getSnapshot()).messages.at(-1)?.content)
})

it.each(['结合今天的恢复情况和已吃的食物，推荐一餐。','Recommend a meal based on today’s recovery and what I’ve eaten.','选下一餐吃什么'])('prioritizes the meal shortcut over readiness: %s',(prompt)=>{expect(matchDemoScript(prompt)).toBe('menu')})
it('includes the actual exercise prescriptions in English normal replies',async()=>{
 const d=setup(),s=await d.client.getSnapshot()
 await d.client.chat({requestId:'english-normal',resetEpoch:s.resetEpoch,conversationId:s.conversationId,message:'Follow the original 35 minute workout.',locale:'en',attachmentIds:[],source:'user'},()=>{},new AbortController().signal)
 const answer=(await d.client.getSnapshot()).messages.at(-1)?.content
 expect(answer).toContain('Seated cable row 35 kg, 3×12');expect(answer).toContain('Lat pulldown 40 kg, 3×12');expect(answer).toContain('Dumbbell curl 10 kg per hand, 3×10')
})
