import {useEffect,useRef,useState} from 'react'
import {useNavigate} from '@tanstack/react-router'
import {Button} from '@heroui/react'
import {useWellio} from '../../lib/wellio-context'
import {useI18n} from '../../lib/i18n'
import {errorText} from '../../lib/errors'
import {Icon} from '../../components/Icon'
import {Mascot} from '../../components/Mascot'
import type {Exercise} from '../../lib/contracts'
import './workout.css'
export function WorkoutPage(){
 const {snapshot,busy,error,runAction,setDraft,setChatTarget}=useWellio(),{t,text,locale,duration}=useI18n(),navigate=useNavigate()
 const [selected,setSelected]=useState<string|null>(null),[actualMinutes,setActualMinutes]=useState(''),[finishing,setFinishing]=useState(false)
 const dialog=useRef<HTMLDialogElement>(null)
 useEffect(()=>{if(finishing)dialog.current?.showModal();else dialog.current?.close()},[finishing])
 if(!snapshot)return null
 const workout=snapshot.workout,rest=snapshot.plan.restDates.includes(snapshot.dayKey)
 if(!workout||rest||workout.dayKey!==snapshot.dayKey)return <section className="workout-page workout-empty"><Mascot pose="sleep"/><h1>{t('No workout scheduled today','今天没有训练安排')}</h1><p>{t('Your next session is in your Today plan.','可在今日页面查看下一场安排。')}</p><Button onPress={()=>void navigate({to:'/today'})}>{t('Back to Today','返回今日')}</Button></section>
 const completed=workout.exercises.filter(e=>e.completed).length,ended=workout.status==='completed'
 const current=workout.exercises.find(e=>e.id===selected)||workout.exercises.find(e=>!e.completed)||workout.exercises[0]
 const load=(e:Exercise)=>e.suggestedLoad.basis==='bodyweight'?t('Bodyweight','自重'):e.suggestedLoad.value===null?t('Load to confirm','重量待确认'):`${e.suggestedLoad.value} kg · ${e.suggestedLoad.basis==='per_hand'?t('per hand','每只'):t('machine stack','机器配重')}`
 async function toggle(e:Exercise){if(!workout)return;const result=await runAction({kind:e.completed?'undo_exercise':'complete_exercise',workoutId:workout.id,exerciseId:e.id,expectedWorkoutVersion:workout.version},'workout');if(result?.status==='succeeded'&&!e.completed){const next=result.snapshot?.workout?.exercises.find(x=>!x.completed);if(next)setSelected(next.id)}}
 async function finish(){if(!workout)return;const result=await runAction({kind:'finish_workout',workoutId:workout.id,expectedWorkoutVersion:workout.version,actualMinutes:Number(actualMinutes),confirmIncomplete:true},'workout');if(result?.status==='succeeded')setFinishing(false)}
 const adjust=()=>{setChatTarget({workoutId:workout.id,exerciseId:current?.id});setDraft(t('Please adjust the rest of my workout. ','请帮我调整剩余训练。'));void navigate({to:'/agent'})}
 return <section className="workout-page">
  <header className="workout-header"><button className="icon-button" aria-label={t('Back to Today','返回今日')} onClick={()=>void navigate({to:'/today'})}><Icon name="arrow-left"/></button><h1>{t('Workout','训练')}</h1><span className="workout-status">{ended?t('Completed','已结束'):workout.status==='in_progress'?t('In progress','训练中'):t('Planned','待开始')}</span></header>
  <div className="workout-scroll">
   <div className="workout-meta"><h2>{text(workout.name)}</h2><p>{workout.gymId==='gym-a'?'Gym A':'Gym B'} · {duration(ended?workout.actualMinutes||0:workout.estimatedMinutes)}</p><span>{completed}/{workout.exercises.length} {t('exercises complete','个动作已完成')}</span></div>
   {!ended&&current&&<section className="workout-current" aria-label={t('Current exercise','当前动作')}>
    <div className="workout-stage"><Mascot pose={current.animation||'welcome'} alt={current.animation?text(current.name):t('Wellio is keeping you company','Wellio 陪你训练')}/></div>
    <span className="eyebrow">{current.animation?t('YOUR NEXT MOVE','当前动作'):t('YOUR WORKOUT COMPANION','你的训练伙伴')}</span>
    <h2>{text(current.name)}</h2><p className="workout-prescription"><strong>{current.sets} × {current.reps}</strong><span>{load(current)}</span></p>
    <p className="workout-cue">{text(current.instructions)}</p>
    <p className="workout-rest">{t('Rest','组间休息')} {current.restSeconds} {t('sec between sets','秒')}</p>
    {workout.status==='planned'?<Button className="primary-button" isDisabled={busy} onPress={()=>void runAction({kind:'start_workout',workoutId:workout.id,expectedWorkoutVersion:workout.version},'workout')}>{t('Start workout','开始训练')}</Button>:<Button className="primary-button" isDisabled={busy} onPress={()=>void toggle(current)}><Icon name={current.completed?'undo':'check'}/>{current.completed?t('Undo completion','撤回完成'):t('Complete exercise','完成动作')}</Button>}
   </section>}
   {ended&&<div className="workout-finished"><Mascot pose="celebrate"/><h2>{t('Workout saved','训练已保存')}</h2><p>{duration(workout.actualMinutes||0)} · {completed}/{workout.exercises.length} {t('exercises completed','个动作完成')}</p>{completed<workout.exercises.length&&<p>{t('Unfinished exercises stay unmarked.','未完成的动作保持未完成。')}</p>}</div>}
   <section className="workout-list"><div className="section-heading"><h2>{t('Your full session','全部动作')}</h2><span>{workout.exercises.length} {t('exercises','个动作')}</span></div>{workout.exercises.map((e,i)=><div className={`workout-row ${e.id===current?.id&&!ended?'is-current':''}`} key={e.id}><button className="workout-row-select" onClick={()=>setSelected(e.id)} aria-current={e.id===current?.id?'step':undefined}><span className="workout-number">{e.completed?<Icon name="check" size={18}/>:String(i+1).padStart(2,'0')}</span><span><strong>{text(e.name)}</strong><small>{e.sets} × {e.reps} · {load(e)}</small></span><span className="sr-only">{e.completed?t('Completed','已完成'):t('Not completed','未完成')}</span></button><details><summary>{t('Load guidance','重量依据')}</summary><p>{text(e.suggestedLoad.reason)} {e.suggestedLoad.source==='mock_history'&&t('Based on sample history. This is a suggested load, not a measured result.','基于模拟历史记录。这是建议重量，并非实测完成重量。')}</p></details></div>)}</section>
   {error&&<p className="inline-error" role="alert">{errorText(error,locale)}</p>}
  </div>
  {!ended&&<footer className="workout-actions"><Button variant="secondary" onPress={adjust}>{t('Adjust with Agent','请助手调整')}</Button><Button variant="ghost" className="workout-finish-button" isDisabled={busy||workout.status!=='in_progress'} onPress={()=>{setActualMinutes('');setFinishing(true)}}>{t('Finish workout','结束训练')}</Button></footer>}
  <dialog ref={dialog} className="wellio-dialog" onCancel={()=>setFinishing(false)} aria-labelledby="finish-title"><h2 id="finish-title">{t('Finish this session?','结束这场训练？')}</h2><p>{completed}/{workout.exercises.length} {t('exercises are complete. Unfinished exercises will stay unmarked.','个动作已完成，未做完的动作不会自动勾选。')}</p><label htmlFor="actual-minutes">{t('Actual workout time (minutes)','实际训练时长（分钟）')}</label><input autoFocus id="actual-minutes" type="number" inputMode="numeric" min="1" max="300" value={actualMinutes} onChange={e=>setActualMinutes(e.target.value)}/><div className="dialog-actions"><Button variant="secondary" isDisabled={busy} onPress={()=>setFinishing(false)}>{t('Keep training','继续训练')}</Button><Button className="primary-button" isDisabled={busy||!Number.isInteger(Number(actualMinutes))||Number(actualMinutes)<1||Number(actualMinutes)>300} onPress={()=>void finish()}>{t('Save and finish','保存并结束')}</Button></div>{error&&<p role="alert" className="inline-error">{errorText(error,locale)}</p>}</dialog>
 </section>
}
