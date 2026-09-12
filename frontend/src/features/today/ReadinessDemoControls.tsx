import { Icon } from '../../components/Icon'
import { useI18n } from '../../lib/i18n'
import { demoModes, type DemoDailyPlan, type ReadinessDemoMode } from '../../lib/readiness-demo'
import { errorText } from '../../lib/errors'
import './readiness-demo.css'

export function ReadinessDemoControls({ mode, select, exit, compact = false, disabled = false }: { mode: ReadinessDemoMode | null; select: (mode: ReadinessDemoMode) => void; exit?: () => void; compact?: boolean; disabled?: boolean }) {
  const { t } = useI18n()
  const labels = { low: t('Low', '睡眠不足'), balanced: t('Balanced', '一般'), high: t('High', '睡眠充足') }
  return <div className={`today-demo-controls${compact ? ' today-demo-controls-compact' : ''}`}>
    {!compact && <div className="today-demo-heading"><span>{t('Sleep demo', '睡眠数据演示')}</span>{mode && exit && <button type="button" disabled={disabled} onClick={exit}>{t('Exit demo', '退出演示')}<Icon name="x" size={11} /></button>}</div>}
    <div className="today-demo-switch" role="group" aria-label={t('Sleep demo mode', '睡眠演示模式')}>
      {demoModes.map(value => <button key={value} type="button" disabled={disabled} aria-pressed={mode === value} onClick={() => select(value)}>{labels[value]}</button>)}
    </div>
    {compact && exit && <button type="button" disabled={disabled} className="today-demo-exit" onClick={exit} aria-label={t('Exit demo', '退出演示')} title={t('Exit demo', '退出演示')}><Icon name="x" size={16} /></button>}
  </div>
}

export function DemoPlanStatus({ pending, error, retry }: { pending: boolean; error: string | null; retry: () => void }) {
  const { t, locale } = useI18n()
  return <div className="today-demo-status" role={error ? 'alert' : 'status'} aria-busy={pending}>
    <p>{pending ? t('Saving simulated watch data and asking Wellio to review your day…', '正在保存模拟手表数据，并请 Wellio 评估今日安排…') : error ? errorText(error, locale) : t('Choose simulated sleep data for your recovery review.', '选择模拟睡眠数据，评估今天的恢复情况。')}</p>
    {error && <button type="button" className="today-text-link" onClick={retry}>{t('Try again', '重新生成')}<Icon name="arrow-up-right" size={14} /></button>}
  </div>
}

export function DemoWorkoutList({ plan, openDetails }: { plan: DemoDailyPlan; openDetails: () => void }) {
  const { t } = useI18n()
  return <>
    <p className="today-demo-summary">{plan.workout.summary}</p>
    <p className="today-gym">{t(`About ${plan.workout.minutes} min · Suggested session`, `预计 ${plan.workout.minutes} 分钟 · 建议训练`)}</p>
    <ol className="today-exercises today-demo-exercises" aria-label={t('Suggested activities', '建议活动清单')}>
      {plan.workout.items.map((item, index) => <li key={`${plan.mode}-${index}`}>
        <span className="today-exercise-number">{String(index + 1).padStart(2, '0')}</span>
        <details className="today-exercise-details"><summary><strong>{item.name}</strong><span>{item.prescription}</span><Icon name="chevron-right" size={15} /></summary>
          <div className="today-exercise-instructions"><p>{item.detail}</p></div></details>
      </li>)}
    </ol>
    <button type="button" className="today-demo-plan-link" onClick={openDetails}>{t('Why this plan?', '为什么这样安排？')}<Icon name="arrow-up-right" size={15} /></button>
  </>
}
