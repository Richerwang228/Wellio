import {useEffect, useRef, useState} from 'react'
import {useNavigate} from '@tanstack/react-router'
import {useI18n} from '../lib/i18n'
import {useWellio} from '../lib/wellio-context'
import {setDemoScenario} from '../lib/demo'
import type {ReadinessDemoMode} from '../lib/readiness-demo'
import './demo-presenter.css'

const questions = [
 {title:['Rest & reschedule','休息与顺延'], before:['Load low readiness first.','先载入低准备度场景。'], prompt:['I slept badly last night. How should I plan recovery and today’s workout?','昨晚没睡好，今天的训练怎么安排？']},
 {title:['Keep today’s plan','按原计划训练'], before:['Start a fresh balanced scenario.','独立切换至中准备度场景。'], prompt:['I’m at Gym B today and have 35 minutes. Let’s follow the original plan.','今天在 Gym B，只有 35 分钟，按原计划练吧。']},
 {title:['Preview a weight increase','预览小幅加重'], before:['Start a fresh high readiness scenario. Apply only after reviewing.','独立切换至高准备度；查看后再 Apply。'], prompt:['I feel great today. Can I increase the weight a little?','今天状态很好，可以稍微加点重量吗？']},
 {title:['Log a completed exercise','记录完成动作'], before:['Balanced scenario; start the workout first.','中准备度场景，先开始训练。'], prompt:['I finished seated cable rows: 35 kg, 3 sets of 12 reps.','坐姿绳索划船做完了，35 公斤，3 组 12 次。']},
 {title:['Work around busy equipment','器械占用时调整'], before:['Log the row first; completed work stays completed.','先记录划船完成；保留已完成动作。'], prompt:['The cable machine is temporarily busy. Put the exercises that don’t need it first.','拉力器暂时被占了，把不用它的动作放前面。']},
 {title:['Choose dinner','挑选晚餐'], before:['Uses the bundled Harbour Bowl sample menu.','使用随包 Harbour Bowl 示例菜单。'], prompt:['Use this Harbour Bowl menu to choose dinner for me. Budget HK$100, no seafood.','用这份 Harbour Bowl 菜单帮我选晚餐，预算 100 港币，不吃海鲜。']},
 {title:['Log dinner','记录晚餐'], before:['Choose the chicken bowl and egg in the previous step.','先选好上一题的鸡肉碗和鸡蛋。'], prompt:['I ate the chicken bowl and egg you recommended. Log it as dinner.','我吃了刚才推荐的鸡肉碗和鸡蛋，帮我记作晚餐。']},
 {title:['Correct a portion','纠正米饭份量'], before:['Log dinner first; the correction updates that meal.','先记录晚餐；本题修改同一餐。'], prompt:['I only ate half the rice at dinner, but finished everything else.','刚才晚餐的米饭只吃了一半，其他都吃完了。']},
] as const

export function DemoPresenter(){
 const {t,locale,setLocale}=useI18n()
 const {snapshot,busy,chatBusy,readinessBusy,stopChat,refresh,setDraft,setChatTarget}=useWellio()
 const navigate=useNavigate()
 const [open,setOpen]=useState(false),[changing,setChanging]=useState(false),[notice,setNotice]=useState('')
 const changeLock=useRef(false)
 useEffect(()=>{setOpen(window.matchMedia('(min-width: 1100px)').matches||new URLSearchParams(window.location.search).get('presenter')==='1')},[])
 const score=snapshot?.readiness.score??65
 const mode:ReadinessDemoMode=score<=40?'low':score>=80?'high':'balanced'
 const locked=busy||chatBusy||readinessBusy||changing||!snapshot
 async function loadScenario(next:ReadinessDemoMode){
  if(locked||changeLock.current)return
  changeLock.current=true;setChanging(true);setNotice('')
  try{
   stopChat()
   await setDemoScenario(next)
   setDraft('');setChatTarget({})
   if(!await refresh())throw new Error('refresh failed')
   setNotice(t('Scenario restored. Ready for a new run.','场景已恢复，可以重新演示。'))
  }catch{setNotice(t('Could not restore the scenario. Please try again.','场景恢复失败，请重试。'))}
  finally{changeLock.current=false;setChanging(false)}
 }
 async function copy(prompt:string){
  try{await navigator.clipboard.writeText(prompt);setNotice(t('Question copied.','问题已复制。'))}
  catch{setNotice(t('Copy is unavailable. Select the question text, or use Fill in.','暂时无法复制，可选中文字复制，或点击填入。'))}
 }
 async function fill(prompt:string){
  if(locked)return
  setChatTarget({});setDraft(prompt)
  await navigate({to:'/agent',search:true})
  setNotice(t('Added to the message box. Press send when ready.','已填入输入框，准备好后手动发送。'))
  if(window.matchMedia('(max-width: 1099px)').matches)setOpen(false)
 }
 const language=locale==='zh-CN'?1:0
 return <aside className="demo-presenter" aria-label={t('Demo presenter','演示提问面板')}>
  <button className="demo-presenter-toggle" aria-expanded={open} aria-controls="demo-presenter-content" onClick={()=>setOpen(!open)}>
   <span><span className="demo-presenter-dot"/> {t('Demo guide','演示提问')}</span><span>{open?t('Collapse −','收起 −'):t('Expand +','展开 +')}</span>
  </button>
  <div id="demo-presenter-content" className="demo-presenter-content" hidden={!open}>
   <header><h2>{t('One question at a time.','一步一步，演示 Wellio。')}</h2><p>{t('Local scripted demo. Copy or fill in a question, then send it yourself.','本地脚本演示。复制或填入问题，再手动发送。')}</p></header>
   <div className="demo-presenter-actions"><button disabled={locked} onClick={()=>setLocale('zh-CN')} aria-pressed={locale==='zh-CN'}>中文</button><button disabled={locked} onClick={()=>setLocale('en')} aria-pressed={locale==='en'}>English</button></div>
   <section className="demo-presenter-scenes" aria-label={t('Readiness scenarios','准备度场景')}>
    <h3>{t('Readiness scenario','准备度场景')}</h3>
    <div className="demo-presenter-modes">{(['low','balanced','high'] as const).map((value,index)=><button key={value} disabled={locked} aria-pressed={mode===value} onClick={()=>void loadScenario(value)}><strong>{['3.8','6.5','8.6'][index]}</strong><span>{[t('Recovery','恢复'),t('Maintain','维持'),t('Increase','加重')][index]}</span></button>)}</div>
    <p>{t('Switching clears this run and starts an independent scenario.','切换会清除当前演示进度，开始独立场景。')}</p>
    <button className="demo-presenter-reset" disabled={locked} onClick={()=>void loadScenario(mode)}>{changing?t('Restoring…','正在恢复…'):t('Reset current scenario','复位当前场景')}</button>
   </section>
   <div className="demo-presenter-notice" role="status" aria-live="polite">{notice||t('Fill in never sends automatically.','填入不会自动发送。')}</div>
   <ol className="demo-presenter-questions">{questions.map((question,index)=><li key={index}>
    <div className="demo-presenter-question-title"><span>{String(index+1).padStart(2,'0')}</span><h3>{question.title[language]}</h3></div>
    <p className="demo-presenter-before">{question.before[language]}</p><p className="demo-presenter-prompt">{question.prompt[language]}</p>
    <div className="demo-presenter-actions"><button onClick={()=>void copy(question.prompt[language])} aria-label={`${t('Copy','复制')} · ${question.title[language]}`}>{t('Copy','复制')}</button><button disabled={locked} onClick={()=>void fill(question.prompt[language])} aria-label={`${t('Fill in','填入')} · ${question.title[language]}`}>{t('Fill in ↗','填入 ↗')}</button></div>
   </li>)}</ol>
   <a className="demo-presenter-menu" href="/demo-menu.svg" target="_blank" rel="noreferrer">{t('View local sample menu ↗','查看本地菜单 ↗')}</a>
  </div>
 </aside>
}
