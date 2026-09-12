import { dailyTotals } from '../../lib/format'
import { isDemoMode } from '../../lib/runtime-mode'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { Icon } from '../../components/Icon'
import { Mascot } from '../../components/Mascot'
import { ReadinessWatch } from './ReadinessWatch'
import { DemoPlanStatus, ReadinessDemoControls } from './ReadinessDemoControls'
import { useReadinessDemo } from './useReadinessDemo'
import { useWellio } from '../../lib/wellio-context'
import { useI18n } from '../../lib/i18n'
import { errorText } from '../../lib/errors'
import type { ActionInput, ContextVersions, Exercise, Proposal, Snapshot } from '../../lib/contracts'
import './today.css'

type Panel = 'basis' | 'plan' | 'nutrition' | 'schedule' | null
const bandColors = ['#bd9573', '#9a7eaa', '#7b9fb2', '#809c60']
const bandInk = ['#845f41', '#745483', '#456b82', '#526f37']
const poses = ['recover', 'pace', 'ready', 'energized'] as const

function validReadiness(snapshot: Snapshot) {
  const r = snapshot.readiness
  return r.quality === 'valid' && r.dayKey === snapshot.dayKey && r.score !== null
    && Number.isFinite(r.score) && r.scoreScale > 0 && r.score >= 0 && r.score <= r.scoreScale
}
function versionsMatch(expected: ContextVersions, snapshot: Snapshot) {
  return expected.readiness === snapshot.readiness.version && expected.plan === snapshot.plan.version
    && expected.workout === (snapshot.workout?.version ?? 0)
    && expected.conditions === snapshot.conditions.version && expected.meal === snapshot.mealRevision
}
function currentProposal(proposal: Proposal, snapshot: Snapshot) {
  return proposal.status === 'pending' && proposal.readinessSnapshotId === snapshot.readiness.id
    && versionsMatch(proposal.expected, snapshot)
}

export function TodayPage() {
  const { snapshot, loading, error, busy, chatBusy, readinessBusy, readinessError, checkReadiness,
    runAction, refresh, setDraft, setChatTarget } = useWellio()
  const { locale, t, text, date } = useI18n()
  const demo = useReadinessDemo(snapshot)
  const navigate = useNavigate()
  const [panel, setPanel] = useState<Panel>(null)
  const [actionKind, setActionKind] = useState<ActionInput['kind'] | null>(null)
  const [feedback, setFeedback] = useState<'saved-not-started' | 'needs-input' | null>(null)
  const dialog = useRef<HTMLDialogElement>(null)
  const disabled = busy || chatBusy
  const number = (value: number, digits = 0) => new Intl.NumberFormat(locale, { maximumFractionDigits: digits }).format(value)
  useEffect(() => {
    if (panel && !dialog.current?.open) dialog.current?.showModal()
    if (!panel && dialog.current?.open) dialog.current.close()
  }, [panel])
  useEffect(() => { setFeedback(null); setPanel(current => isDemoMode() && current === 'basis' ? current : null) }, [snapshot?.sessionId, snapshot?.resetEpoch])

  async function act(input: ActionInput) {
    if (disabled) return
    setFeedback(null); setActionKind(input.kind)
    try {
      const result = await runAction(input, 'today')
      if (result?.applyStatus === 'succeeded' && result.startStatus === 'failed') setFeedback('saved-not-started')
      if (result?.status === 'needs_input') setFeedback('needs-input')
      const startsWorkout = input.kind === 'start_workout' || (input.kind === 'apply_proposal' && input.startAfterApply)
      if (startsWorkout && result?.status === 'succeeded' && result.startStatus !== 'failed' && result.snapshot?.workout?.status === 'in_progress') {
        await navigate({ to: '/workout' })
      }
      return result
    } finally { setActionKind(null) }
  }
  function askAgent(message: string) {
    setDraft(message)
    setChatTarget(snapshot?.workout ? { workoutId: snapshot.workout.id } : {})
    setPanel(null)
    void navigate({ to: '/agent' })
  }
  const button = (label: string, onClick: () => void, secondary = false, extraDisabled = false) => (
    <button type="button" className={`today-button ${secondary ? 'today-button-secondary' : 'today-button-primary'}`}
      disabled={disabled || extraDisabled} onClick={onClick}>
      {label}{!secondary && <Icon name="arrow-right" size={18} />}
    </button>
  )
  if (!snapshot) return <section className="today-page">
    <header className="today-header"><h1>{t('Today', '今天')}</h1></header>
    <div className="today-loading"><p role="status">{loading ? t('Getting your day ready…', '正在准备今日数据…') : t('Your plan could not be loaded.', '暂时无法读取今日方案。')}</p>
      {error && button(t('Try again', '重试'), () => { void refresh() })}</div>
  </section>

  const s = snapshot
  const consumed = dailyTotals(s.meals)
  const readinessAvailable = validReadiness(s)
  const score = readinessAvailable ? s.readiness.score! / s.readiness.scoreScale * 10 : null
  const band = score === null ? null : Math.min(3, Math.floor(score / 2.5))
  const bandLabels = [t('Prioritize recovery', '优先恢复'), t('Take it easy', '放慢节奏'), t('Ready to train', '准备好了'), t('Feeling energized', '状态充沛')]
  const savedWorkout = s.workout
  const inProgress = savedWorkout?.status === 'in_progress'
  const completed = savedWorkout?.status === 'completed'
  const restToday = s.plan.restDates.includes(s.dayKey)
  const proposal = [...s.proposals].reverse().find(p => currentProposal(p, s)
    && (p.scope === 'workout' ? !completed && Boolean(p.workout) : !inProgress && !completed && !restToday && p.restDate === s.dayKey))
  const scheduleProposal = proposal?.scope === 'schedule' ? proposal : null
  const workoutProposal = proposal?.scope === 'workout' ? proposal : null
  const workout = workoutProposal?.workout ?? savedWorkout
  const expiredProposal = [...s.proposals].reverse().find(p => p.status === 'stale' || (p.status === 'pending' && !currentProposal(p, s)))
  const dismissed = [...s.proposals].reverse().find(p => p.status === 'dismissed' && p.readinessSnapshotId === s.readiness.id && p.expected.readiness === s.readiness.version)
  const consideringRest = readinessAvailable && s.readiness.guidanceHint === 'consider_rest' && !proposal && !restToday && !inProgress && !completed && !dismissed
  const checkingRecovery = readinessBusy || (!readinessError && s.readinessCheck?.status === 'pending')
  const activeWork = busy || chatBusy || demo.pending || checkingRecovery || (!readinessError && !error && s.advice.status === 'pending')
  const adviceCurrent = s.advice.status === 'valid' && (!s.advice.versions || versionsMatch(s.advice.versions, s))
  const targets = s.profile.targets
  const planSession = s.plan.sessions.find(session => session.date === s.dayKey)
  const nextSession = [...s.plan.sessions].filter(session => session.status === 'pending' && session.date !== s.dayKey)
    .sort((a, b) => (a.date ?? '9999').localeCompare(b.date ?? '9999'))[0]
  const splitName = (split: 'Pull' | 'Legs' | 'Push') => ({ Pull: t('Pull', '拉类训练'), Legs: t('Legs', '腿部训练'), Push: t('Push', '推类训练') })[split]
  const dateOrPending = (value: string | null) => value ? date(value) : t('Unscheduled', '待安排')
  const gymId = workout?.gymId ?? s.conditions.gymId
  const time = (value: string) => new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: s.timeZone }).format(new Date(value))
  const updatedTime = s.readiness.observedAt && Number.isFinite(Date.parse(s.readiness.observedAt)) ? time(s.readiness.observedAt) : null
  const sleepMinutes = s.sleep?.minutes ?? null
  const heartRate = s.readiness.restingHeartRate
  const sleepDuration = sleepMinutes !== null ? t(`${Math.floor(sleepMinutes / 60)}h ${sleepMinutes % 60}m`, `${Math.floor(sleepMinutes / 60)}时${sleepMinutes % 60}分`) : '—'
  const demoStatus = () => <DemoPlanStatus pending={demo.pending} error={demo.error} retry={demo.retry} />
  const demoControls = (compact = false) => <ReadinessDemoControls mode={demo.mode} select={mode => { void demo.select(mode) }} exit={demo.exit} compact={compact} disabled={disabled || demo.pending} />
  const mainError = feedback === 'saved-not-started'
    ? t('Your plan is saved. Try starting the workout again.', '方案已保存，请重试开始训练。')
    : error ? errorText(error, locale)
      : feedback === 'needs-input' ? t('A little more information is needed to finish this plan.', '还需要补充一些信息才能完成方案。')
        : readinessError ? errorText(readinessError, locale) : demo.error ? errorText(demo.error, locale) : null

  function loadText(exercise: Exercise) {
    const load = exercise.suggestedLoad
    if (load.basis === 'bodyweight') return t('Bodyweight', '自重')
    if (load.value === null) return t('Load to confirm', '重量待确认')
    return `${number(load.value, 1)} kg · ${load.basis === 'per_hand' ? t('per hand', '每只') : t('machine stack', '机器配重')}`
  }
  function exerciseList(exercises: Exercise[]) {
    return <ol className="today-exercises" aria-label={t('All exercises', '全部训练动作')}>
      {exercises.map((exercise, index) => <li key={exercise.id} className={exercise.completed ? 'today-exercise-done' : ''}>
        <span className="today-exercise-number">{exercise.completed ? <Icon name="check" size={17} /> : String(index + 1).padStart(2, '0')}</span>
        <details className="today-exercise-details">
          <summary><strong>{text(exercise.name)}</strong><span>{exercise.sets} × {exercise.reps}</span><Icon name="chevron-right" size={15} />
            {exercise.completed && <span className="sr-only">{t('Completed', '已完成')}</span>}
          </summary>
          <div className="today-exercise-instructions"><p><b>{t('Suggested load', '建议重量')}</b> · {loadText(exercise)}</p>
            <p>{text(exercise.instructions)}</p><p>{t('Rest', '组间休息')} {exercise.restSeconds} {t('seconds between sets', '秒')}</p>
            <p className="today-note">{text(exercise.suggestedLoad.reason)}</p></div>
        </details>
      </li>)}
    </ol>
  }
  function dateMoves(p: Proposal) {
    return <div className="today-date-moves">{p.moves?.map(move => <div key={move.sessionId}>
      <strong>{splitName(move.split)}</strong><span>{dateOrPending(move.from)} <Icon name="arrow-right" size={14} /> {dateOrPending(move.to)}</span>
    </div>)}</div>
  }
  function startButton() {
    if (!savedWorkout) return null
    if (completed) return button(t('View workout record', '查看训练记录'), () => { void navigate({ to: '/workout' }) })
    if (inProgress) return button(t('Continue workout', '继续训练'), () => { void navigate({ to: '/workout' }) })
    return button(actionKind === 'start_workout' ? t('Starting…', '正在开始…') : readinessAvailable && !dismissed ? t('Start workout', '开始训练') : t('Start current plan', '按现有计划开始'),
      () => { void act({ kind: 'start_workout', workoutId: savedWorkout.id, expectedWorkoutVersion: savedWorkout.version }) })
  }
  function reviewButton() {
    return button(!s.capabilities.agent ? t('Suggestions unavailable', '建议暂不可用') : activeWork ? t('Preparing a suggestion…', '正在准备建议…') : t('Prepare a suggestion', '生成调整建议'),
      () => { void act({ kind: 'request_proposal' }) }, false, activeWork || !s.capabilities.agent)
  }
  function workoutBody() {
    if (restToday && !inProgress && !completed) return <>
      <div className="today-rest-summary"><Mascot pose="sleep" alt="" /><div>
        <p>{nextSession ? t(`Next: ${splitName(nextSession.split)} · ${dateOrPending(nextSession.date)}`, `下一场：${splitName(nextSession.split)} · ${dateOrPending(nextSession.date)}`) : t('No upcoming session scheduled.', '下一场训练待安排。')}</p></div></div>
      {button(t('Upcoming schedule', '后续安排'), () => setPanel('schedule'), true)}
    </>
    if (scheduleProposal) return <>
      <p className="today-reason">{text(scheduleProposal.reason)}</p>
      <details className="today-schedule-preview"><summary>{t('Review schedule changes', '查看顺延安排')}<Icon name="chevron-down" size={16} /></summary>
        {dateMoves(scheduleProposal)}<p className="today-note">{t('Future dates are tentative. Changes are saved only after you confirm.', '未来日期暂定，确认后才会保存调整。')}</p></details>
      <div className="today-plan-main-action">{button(actionKind === 'apply_proposal' ? t('Saving…', '正在保存…') : t('Confirm rest and reschedule', '确认休息并顺延'),
        () => { void act({ kind: 'apply_proposal', proposalId: scheduleProposal.id, startAfterApply: false }) })}</div>
      {button(t('Keep current plan', '保留原计划'), () => { void act({ kind: 'dismiss_proposal', proposalId: scheduleProposal.id }) }, true)}
    </>
    return <>
      <div className="today-plan-heading">
        <p className="today-gym">{gymId === 'gym-a' ? 'Gym A' : 'Gym B'} · {workout ? t(`About ${workout.estimatedMinutes} min`, `预计 ${workout.estimatedMinutes} 分钟`) : t(`${s.conditions.availableMinutes} min available`, `可用 ${s.conditions.availableMinutes} 分钟`)}</p>
      </div>
      {consideringRest && <p className="today-inline-state" role="status">{!s.capabilities.agent
        ? t('Recovery review unavailable. Saved plan shown.', '恢复建议暂不可用，以下为已保存计划。')
        : activeWork ? t('Reviewing your recovery…', '正在评估恢复情况…') : t('Review your recovery before starting.', '开始前，先评估今天的恢复情况。')}</p>}
      {(completed || inProgress) && <p className="today-inline-state">{completed ? t('Finished', '已结束') : t('In progress', '进行中')} · {savedWorkout!.exercises.filter(e => e.completed).length}/{savedWorkout!.exercises.length} {t('exercises complete', '个动作已完成')}</p>}
      {workoutProposal && <p className="today-inline-state">{t('Suggested changes · Awaiting confirmation', '建议调整 · 等待确认')}</p>}
      {workout ? exerciseList(workout.exercises) : <p className="today-empty">{t('Generate a plan to see today’s exercises.', '生成方案后，在这里查看今日动作。')}</p>}
      <div className="today-plan-main-action">{workoutProposal
        ? button(actionKind === 'apply_proposal' ? t('Saving and starting…', '正在保存并开始…') : inProgress ? t('Apply changes and continue', '确认调整并继续') : savedWorkout ? t('Apply changes and start', '确认调整并开始') : t('Confirm and start', '确认并开始'),
          () => { void act({ kind: 'apply_proposal', proposalId: workoutProposal.id, startAfterApply: true }) })
        : consideringRest ? reviewButton() : savedWorkout ? startButton() : reviewButton()}</div>
      {workoutProposal && button(t('Keep current plan', '保留原计划'), () => { void act({ kind: 'dismiss_proposal', proposalId: workoutProposal.id }) }, true)}
    </>
  }

  const sourceLabel = proposal ? t('Review changes', '待确认') : consideringRest ? t('Awaiting review', '待评估')
    : completed ? t('Completed', '已完成') : inProgress ? t('In progress', '进行中') : t('Saved plan', '已保存')
  let panelTitle = ''
  let panelContent: ReactNode = null
  if (panel === 'basis') {
    panelTitle = t('Your readiness', '准备度依据')
    panelContent = <><p className="today-watch-caption">{t('A closer look at last night.', '从昨晚的睡眠，了解今天的状态。')}</p>
      {demoControls()}
      <ReadinessWatch score={score} color={band === null ? '#809c60' : bandColors[band]} updatedTime={updatedTime}
        sleepMinutes={sleepMinutes} restingHeartRate={heartRate}
        baselineSleepMinutes={s.readiness.baselineSleepMinutes} baselineHeartRate={s.readiness.baselineHeartRate}
        deepSleepPercent={s.sleep?.deepSleepPercent ?? demo.readings?.deepSleepPercent} hrvMs={s.readiness.hrvMs ?? demo.readings?.hrvMs} />
      <p className="today-watch-source">{t('Sample watch data · HealthKit is not connected.', '模拟手表数据 · 尚未连接 HealthKit。')}</p>
      {(demo.pending || demo.error) && demoStatus()}
      <details className="today-watch-explanation"><summary>{t('About these readings', '关于这些数据')}<Icon name="chevron-right" size={13} /></summary>
        <p>{t('The three sleep modes supply simulated watch readings. Readiness is shown out of 100 here and out of 10 on Today. Available measurements come from the shared watch snapshot.', '三种睡眠模式提供模拟手表读数。表盘采用百分制，Today 采用十分制；可用测量值来自共享手表快照。')}</p>
        <p>{isDemoMode() ? t('Choose a scenario, then ask Wellio to review the plan. Apply confirms changes in this browser.', '选择场景后，向 Wellio 询问方案；确认调整会保存在当前浏览器。') : s.capabilities.agent ? t('Selecting a mode saves the simulated watch input and asks the live Agent to review it. Training changes need your confirmation. Nutrition advice is shown separately from saved daily targets.', '切换模式会保存模拟手表输入，并请真实 Agent 评估。训练调整需要你确认；营养建议与已保存的每日目标分别显示。') : t('Simulated watch readings can be saved. Agent is not connected, so new advice is unavailable.', '可以保存模拟手表读数；Agent 尚未连接，暂时无法生成新建议。')}</p>
      </details>
      {!readinessAvailable && <p role="status">{t('Today’s readiness is missing, outdated or unavailable.', '今日准备度数据缺失、过期或暂不可用。')}</p>}
      {s.capabilities.agent && !activeWork && button(t('Review my recovery', '评估我的恢复情况'), () => { setPanel(null); void checkReadiness({ mode: 'retry' }).catch(() => {}) }, true)}
    </>
  } else if (panel === 'plan') {
    panelTitle = t('About this workout', '训练方案依据')
    panelContent = <>
      {proposal ? <><p>{text(proposal.reason)}</p><p className="today-note">{t('This suggestion is not saved until you confirm it.', '此方案将在你确认后保存。')}</p></>
        : adviceCurrent && s.advice.training ? <p>{text(s.advice.training)}</p>
          : <p>{t('This is your saved workout. A new recovery-based recommendation is not available yet.', '这是当前保存的训练计划，尚无新的恢复评估建议。')}</p>}
      {savedWorkout?.source === 'demo_preset' && <p className="today-note">{t('The starting workout is a sample training plan.', '初始训练为演示预设方案。')}</p>}
      {workoutProposal && savedWorkout && <><h3>{t('Changes to your saved plan', '相对原计划的变化')}</h3><p className="today-note">{t('Review the exercise list and load before confirming.', '确认前请核对动作清单与重量。')}</p>
        <ul className="today-change-list">{workoutProposal.workout!.exercises.map(exercise => <li key={exercise.id}>{text(exercise.name)} · {exercise.sets} × {exercise.reps} · {loadText(exercise)}</li>)}</ul>
        {savedWorkout.exercises.some(old => !workoutProposal.workout!.exercises.some(item => item.id === old.id)) && <p>{t('Removed: ', '移除：')}{savedWorkout.exercises.filter(old => !workoutProposal.workout!.exercises.some(item => item.id === old.id)).map(old => text(old.name)).join(' · ')}</p>}
      </>}
      {expiredProposal && !proposal && <p>{t('An earlier suggestion is out of date.', '此前建议已过期。')}</p>}
      {s.capabilities.agent && !completed && button(t('Review with Wellio', '请 Wellio 评估'), () => askAgent(t('Review today’s training using my recovery, available time and equipment.', '结合恢复情况、可用时间和器械，评估今天的训练。')), true)}
    </>
  } else if (panel === 'nutrition') {
    panelTitle = t('Your nutrition targets', '每日营养目标')
    panelContent = <><p>{isDemoMode() ? t('Your current targets. Confirming a plan updates these targets together with your training.', '这是当前已生效目标。确认方案后，营养目标与训练安排会一起更新。') : t('These are your saved daily targets. They have not been recalculated from today’s readiness.', '以下为已保存的每日目标，尚未根据今日准备度重新计算。')}</p>
      <dl className="today-facts">{([
        [t('Energy', '热量'), s.profile.targets.kcal, 'kcal'], [t('Protein', '蛋白质'), s.profile.targets.protein, 'g'],
        [t('Carbs', '碳水'), s.profile.targets.carbs, 'g'], [t('Fat', '脂肪'), s.profile.targets.fat, 'g'],
      ] as const).map(([label, value, unit]) => <div key={label}><dt>{label}</dt><dd>{number(value)} {unit}</dd></div>)}</dl>
      {adviceCurrent && s.advice.nutrition && <><h3>{t('Wellio’s suggestion', 'Wellio 的建议')}</h3><p>{text(s.advice.nutrition)}</p></>}
      <p className="today-note">{t('Discuss your next meal with Wellio and record what you actually eat.', '可以与 Wellio 讨论下一餐，并记录实际吃下的食物。')}</p>
      {s.capabilities.agent && button(t('Discuss with Wellio', '与 Wellio 讨论'), () => askAgent(t('Explain my nutrition targets in relation to today’s recovery and workout.', '结合今天的恢复和训练，解释我的营养目标。')), true)}
    </>
  } else if (panel === 'schedule') {
    panelTitle = t('Upcoming schedule', '后续安排')
    panelContent = <><p className="today-note">{t('Saved schedule · Future dates are tentative', '已保存安排 · 未来日期暂定')}</p>
      <dl className="today-facts">{s.plan.sessions.filter(session => session.status === 'pending').map(session => <div key={session.id}><dt>{splitName(session.split)}</dt><dd>{dateOrPending(session.date)}</dd></div>)}</dl></>
  }

  return <section className="today-page" aria-labelledby="today-title">
    <header className="today-header"><h1 id="today-title">{t('Today', '今天')}</h1><span><Icon name="calendar" size={17} />{date(s.dayKey, { weekday: 'short' })}</span></header>
    <div className="today-content">
      <section className="today-readiness" aria-labelledby="today-readiness-title">
        <div className="today-card-heading"><h2 id="today-readiness-title">{t('Today’s readiness', '今日准备度')}</h2>
          <button type="button" className="today-detail-button today-sample-button" onClick={() => setPanel('basis')} aria-label={t('Readiness details', '查看准备度依据')}><small>{t('Details', '查看依据')}</small><Icon name="arrow-up-right" size={17} /></button></div>
        <div className="today-readiness-stage">
          <button type="button" className="today-gauge-button" onClick={() => setPanel('basis')}
            aria-label={score === null ? t('Readiness unavailable. View details.', '准备度暂不可用，查看依据。') : t(`Readiness ${score.toFixed(1)} out of 10. ${bandLabels[band!]}. View details.`, `准备度 ${score.toFixed(1)} 分，满分10分，${bandLabels[band!]}，查看依据。`)}>
            <svg viewBox="0 0 190 120" aria-hidden="true"><g fill="none" strokeWidth="9" strokeLinecap="round">
              {bandColors.map((color, index) => {
                const point = (fraction: number) => ({ x: 95 + 78 * Math.cos(Math.PI * (1 - fraction)), y: 94 - 78 * Math.sin(Math.PI * (1 - fraction)) })
                const from = point(index / 4 + .018), to = point((index + 1) / 4 - .018)
                return <path key={color} d={`M ${from.x} ${from.y} A 78 78 0 0 1 ${to.x} ${to.y}`} stroke={band === null ? '#cbd3c1' : color} opacity={band === index ? 1 : .3} />
              })}</g>
              {score !== null && <circle cx={95 + 78 * Math.cos(Math.PI * (1 - score / 10))} cy={94 - 78 * Math.sin(Math.PI * (1 - score / 10))} r="7.5" fill={bandInk[band!]} stroke="#faf9f2" strokeWidth="4" />}
            </svg><span className="today-gauge-value"><strong>{score === null ? '—' : score.toFixed(1)}<small>/10</small></strong>
              <span style={{ color: band === null ? '#62705c' : bandInk[band] }}>{band === null ? t('Unavailable', '暂不可用') : bandLabels[band]}</span></span>
          </button>
          <div className="today-pet"><Mascot pose={band === null ? 'welcome' : poses[band]} alt={t('Wellio avocado companion', '牛油果伙伴 Wellio')} /></div>
        </div>
        <div className="today-health-metrics">
          <div><Icon name="moon" size={21} /><span><small>{t('Sleep', '昨晚睡眠')}</small><strong>{sleepDuration}</strong></span></div>
          <div><Icon name="heart" size={21} /><span><small>{t('Resting HR', '静息心率')}</small><strong>{heartRate ?? '—'} <small>bpm</small></strong></span></div>
        </div>
        {demoControls(true)}
        {!isDemoMode() && <p className="today-demo-disclosure">{s.capabilities.agent ? t('Simulated watch input · Live Agent suggestions', '模拟手表输入 · 真实 Agent 建议') : t('Simulated watch input · Agent unavailable', '模拟手表输入 · Agent 暂不可用')}</p>}
        {demo.pending && demoStatus()}
      </section>
      <section className={`today-plan${(scheduleProposal || restToday) && !inProgress && !completed ? ' today-rest' : ''}`} aria-label={t('Today’s training', '今日训练')}>
        <div className="today-card-heading"><h2>{restToday && !inProgress && !completed ? t('Rest today', '今天休息') : scheduleProposal ? t('A day to recover', '今天，留给恢复') : workout ? text(workout.name) : planSession ? splitName(planSession.split) : t('Your workout', '今日训练')}</h2>
          <button type="button" className="today-source-button" onClick={() => setPanel('plan')} aria-label={t('About this workout', '查看训练方案依据')}>{sourceLabel}<Icon name="chevron-right" size={13} /></button></div>
        {workoutBody()}
      </section>
      {mainError && <div className="today-error" role="alert"><p>{mainError}</p>
        {readinessError && !error && button(t('Retry recovery check', '重试恢复评估'), () => { void checkReadiness({ mode: 'retry' }).catch(() => {}) }, true, activeWork)}
      </div>}
      <section className="today-nutrition" aria-labelledby="today-nutrition-title">
        <div className="today-card-heading"><h2 id="today-nutrition-title"><img src="/assets/today/leaf.svg" width="20" height="20" alt="" />{t('Daily nutrition', '每日营养')}</h2>
          <button type="button" className="today-detail-button" onClick={() => setPanel('nutrition')} aria-label={t('Nutrition target details', '查看营养目标依据')}><Icon name="arrow-up-right" size={19} /></button></div>
        {targets ? <><div className="today-energy"><strong>{number(targets.kcal)} <small>kcal</small></strong><span>{t('Saved daily target', '已保存的每日目标')}</span></div>
        <dl className="today-macro-targets">{([
          ['protein', t('Protein', '蛋白质'), targets.protein], ['carbs', t('Carbs', '碳水'), targets.carbs], ['fat', t('Fat', '脂肪'), targets.fat],
        ] as const).map(([key, label, value]) => <div className={`today-macro-${key}`} key={key}><dt><i />{label}</dt><dd>{number(value)} <small>g</small></dd></div>)}</dl></> : demoStatus()}
        {isDemoMode() && <div className="today-demo-intake" aria-label={t('Recorded intake', '已记录摄入')}><span>{t('Eaten', '已摄入')} <strong>{number(consumed.kcal)} kcal</strong></span><span>{t('Protein', '蛋白质')} <strong>{number(consumed.protein)} g</strong></span></div>}
        {adviceCurrent && s.advice.nutrition && <p className="today-nutrition-source"><button type="button" className="today-text-link" onClick={() => setPanel('nutrition')}>{t('View suggestion', '查看建议')}<Icon name="chevron-right" size={12} /></button></p>}
      </section>
    </div>
    <dialog ref={dialog} className={`today-dialog${panel === 'basis' ? ' today-dialog-readiness' : ''}`} onCancel={() => setPanel(null)} onClose={() => setPanel(null)}
      onClick={event => { if (event.target === event.currentTarget) { const rect = event.currentTarget.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) setPanel(null) } }} aria-labelledby="today-panel-title">
      <button type="button" className="today-close" aria-label={t('Close', '关闭')} onClick={() => setPanel(null)}><Icon name="x" size={22} /></button>
      <h2 id="today-panel-title">{panelTitle}</h2>{panelContent}
    </dialog>
  </section>
}

export default TodayPage
