/** Documented, deterministic seed data; not a model or external-service response. */
import type {Snapshot,Scenario,Exercise,Workout,LocalizedText} from './contracts'
const l=(en:string,zh:string):LocalizedText=>({en,'zh-CN':zh})
const days=['08-29','08-30','08-31','09-01','09-02','09-03','09-04','09-05','09-06','09-07','09-08','09-09','09-10','09-11'].map(d=>'2026-'+d)
const weights=[70,70.1,69.9,70.2,70.1,70.3,70.2,70.2,70.4,70.3,70.4,70.2,70.5,70.4]
const minutes=[38,0,42,0,35,0,40,0,38,0,42,0,35,0]
const types=['Upper body','Rest','Lower body','Rest','Pull','Rest','Full body','Rest','Upper body','Rest','Lower body','Rest','Pull','Rest']
const nutrition=[[2230,135,265,70,2500],[2288,140,270,72,2420],[2204,138,260,68,2560],[2375,145,280,75,2430],[2178,132,255,70,2520],[2308,140,275,72,2430],[2258,142,265,70,2550],[2196,136,260,68,2410],[2355,145,275,75,2540],[2280,138,270,72,2420],[2250,140,265,70,2570],[2710,135,340,90,2440],[2232,145,260,68,2510],[2288,140,270,72,2420]]
export function initialWorkout():Workout {
 const records:[string,LocalizedText,number,number,'machine_stack'|'per_hand',Exercise['animation']][]=[
  ['seated-cable-row',l('Seated cable row','坐姿绳索划船'),35,12,'machine_stack','row'],
  ['lat-pulldown',l('Lat pulldown','高位下拉'),40,12,'machine_stack','pulldown'],
  ['dumbbell-curl',l('Dumbbell curl','哑铃弯举'),10,10,'per_hand',undefined],
 ]
 const exercises:Exercise[]=records.map(([id,name,value,reps,basis,animation])=>({id:`exercise-${id}`,catalogId:id,name,equipmentId:basis==='per_hand'?'gym-b-dumbbells':'gym-b-cable',equipment:basis==='per_hand'?l('Dumbbells','哑铃'):l('Cable machine','拉力器'),sets:3,reps,restSeconds:75,suggestedLoad:{value,unit:'kg',basis,source:'mock_history',sourceHistoryId:`load-b-0910-${id==='seated-cable-row'?'cable-row':id==='lat-pulldown'?'pulldown':'curl'}`,reason:l('Based on your Sep 10 record on the same equipment.','依据 9 月 10 日相同器械的历史记录。')},completed:false,instructions:id==='seated-cable-row'?l('Keep your torso steady. Pull your elbows back, then return with control.','保持躯干稳定，向后拉肘，再控制还原。'):id==='lat-pulldown'?l('Keep your chest lifted. Draw the bar toward your upper chest with control.','胸部自然抬起，控制横杆向上胸方向下拉。'):l('Keep your elbows near your sides. Curl with control and lower slowly.','手肘靠近身体两侧，控制弯举并缓慢还原。'),animation}))
 return {id:'workout-pull-0912',trainingSessionId:'planned-pull-01',version:1,dayKey:'2026-09-12',name:l('Pull workout','拉类训练'),gymId:'gym-b',estimatedMinutes:35,status:'planned',exercises}
}
export function createFixture(scenario:Scenario='normal'):Snapshot {
 const low=scenario==='low_recovery';const slots=['09-12','09-14','09-16','09-18','09-21','09-23','09-25'].map(d=>({id:'slot-'+d.replace('-',''),date:'2026-'+d}))
 return {sessionId:'preview-session',conversationId:'preview-conversation',resetEpoch:1,revision:1,dayKey:'2026-09-12',timeZone:'Asia/Hong_Kong',locale:'en',scenario,
 profile:{name:'Alex',goal:'muscle_gain',targets:{kcal:2400,protein:140,carbs:280,fat:80},dislikes:l('No seafood','不吃海鲜'),dinnerBudget:100,expenditure:2500},
 readiness:{id:`readiness-${scenario}`,version:1,dayKey:'2026-09-12',quality:'valid',score:low?42:82,scoreScale:100,guidanceHint:low?'consider_rest':'keep_plan',observedAt:'2026-09-12T08:00:00+08:00',restingHeartRate:low?72:60,baselineHeartRate:60,baselineSleepMinutes:450,source:'mock_watch'},
 sleep:{id:`sleep-${scenario}`,minutes:low?240:450,bedtime:low?'2026-09-12T03:00:00+08:00':'2026-09-11T23:30:00+08:00',wakeTime:'2026-09-12T07:00:00+08:00',source:'mock_watch'},
 meals:[{id:'meal-breakfast',version:1,period:'breakfast',time:'08:30',items:[{id:'item-breakfast',name:l('Yogurt & oats','酸奶燕麦'),portion:l('One bowl','一碗'),base:{kcal:600,protein:30,carbs:75,fat:20},consumedFraction:1,estimated:true}]},{id:'meal-lunch',version:1,period:'lunch',time:'12:40',items:[{id:'item-lunch',name:l('Chicken rice & vegetables','鸡肉饭与配菜'),portion:l('One serving','一份'),base:{kcal:1050,protein:60,carbs:135,fat:30},consumedFraction:1,estimated:true}]}],
 workout:initialWorkout(),plan:{id:'split-plan-01',version:1,pendingSessionIds:['planned-pull-01','planned-legs-02','planned-push-03'],sessions:[{id:'planned-pull-01',split:'Pull',templateRef:'split-pull-b',slotId:'slot-0912',date:'2026-09-12',workoutId:'workout-pull-0912',status:'pending'},{id:'planned-legs-02',split:'Legs',templateRef:'split-legs-b',slotId:'slot-0914',date:'2026-09-14',status:'pending'},{id:'planned-push-03',split:'Push',templateRef:'split-push-b',slotId:'slot-0916',date:'2026-09-16',status:'pending'}],availableSlots:slots,restDates:[]},proposals:[],
 messages:[],advice:{status:'unavailable',errorCode:'PROVIDER_NOT_CONFIGURED'},conditions:{version:1,gymId:'gym-b',availableMinutes:35,dinnerBudget:100},mealRevision:1,capabilities:{agent:false,menuSearch:false,persistence:'preview'},
 history:{weight:days.map((date,i)=>({date,kg:weights[i]})),training:days.map((date,i)=>({date,type:types[i],minutes:minutes[i]})),nutrition:days.map((date,i)=>({date,kcal:nutrition[i][0],protein:nutrition[i][1],carbs:nutrition[i][2],fat:nutrition[i][3],expenditure:nutrition[i][4]})),load:[...['2026-08-29','2026-09-06','2026-09-10'].flatMap((date,i)=>[{id:`row-${i}`,date,exerciseId:'seated-cable-row',name:l('Seated cable row','坐姿绳索划船'),equipmentId:'gym-b-cable',kg:i===2?35:30,basis:'machine_stack' as const,source:'mock_history' as const},{id:`pulldown-${i}`,date,exerciseId:'lat-pulldown',name:l('Lat pulldown','高位下拉'),equipmentId:'gym-b-cable',kg:i===2?40:35,basis:'machine_stack' as const,source:'mock_history' as const},{id:`lateral-${i}`,date,exerciseId:'lateral-raise',name:l('Lateral raise','侧平举'),equipmentId:'gym-b-dumbbells',kg:i===2?7.5:5,basis:'per_hand' as const,source:'mock_history' as const}])]},
 }
}
