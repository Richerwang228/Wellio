import { useI18n } from '../../lib/i18n'
import './readiness-watch.css'

interface ReadinessWatchProps {
  score: number | null
  color: string
  updatedTime: string | null
  sleepMinutes: number | null
  restingHeartRate: number | null
  baselineSleepMinutes: number
  baselineHeartRate: number
  deepSleepPercent?: number | null
  hrvMs?: number | null
  baselineDeepSleepPercent?: number
  baselineHrvMs?: number
}

export function ReadinessWatch({ score, color, updatedTime, sleepMinutes, restingHeartRate, baselineSleepMinutes, baselineHeartRate, deepSleepPercent, hrvMs, baselineDeepSleepPercent, baselineHrvMs }: ReadinessWatchProps) {
  const { t } = useI18n()
  // Today uses a ten-point scale; the watch presents the same score out of 100.
  const watchScore = score === null ? null : Math.round(score * 10)
  const sleepTime = (minutes: number) => t(`${Math.floor(minutes / 60)}h ${minutes % 60}m`, `${Math.floor(minutes / 60)}时 ${minutes % 60}分`)
  const metrics = [
    { id: 'sleep', label: t('Sleep duration', '睡眠时长'), value: sleepMinutes === null ? '—' : sleepTime(sleepMinutes), baseline: sleepTime(baselineSleepMinutes) },
    { id: 'deep-sleep', label: t('Deep sleep', '深睡比例'), value: deepSleepPercent == null ? '—' : `${deepSleepPercent}%`, baseline: baselineDeepSleepPercent == null ? null : `${baselineDeepSleepPercent}%` },
    { id: 'heart-rate', label: t('Resting heart rate', '静息心率'), value: restingHeartRate === null ? '—' : `${restingHeartRate} bpm`, baseline: `${baselineHeartRate} bpm` },
    { id: 'hrv', label: 'HRV', value: hrvMs == null ? '—' : `${hrvMs} ms`, baseline: baselineHrvMs == null ? null : `${baselineHrvMs} ms` },
  ]

  return <section className="today-watch" aria-label={t('Wellio watch snapshot', 'Wellio 手表数据快照')}>
    <div className="today-watch-crown" aria-hidden="true" />
    <div className="today-watch-side-button" aria-hidden="true" />
    <div className="today-watch-case">
      <div className="today-watch-screen">
        <div className="today-watch-topline"><span>WELLIO</span><span aria-label={t('Data updated at', '数据更新于')}>{updatedTime ?? '—'}</span></div>
        <div className="today-watch-dial">
          <svg viewBox="0 0 200 200" aria-hidden="true">
            <circle className="today-watch-track" cx="100" cy="100" r="85" />
            {watchScore !== null && <circle className="today-watch-progress" cx="100" cy="100" r="85" pathLength="100"
              stroke={color} strokeDasharray={`${watchScore} 100`} transform="rotate(-90 100 100)" />}
          </svg>
          <div className="today-watch-score"><strong>{watchScore ?? '—'}</strong><span>{t('Readiness', '准备度')} <small>/ 100</small></span></div>
        </div>
        <dl className="today-watch-metrics">{metrics.map(metric => <div className="today-watch-metric" data-metric={metric.id} key={metric.id}>
          <dt>{metric.label}</dt><dd>{metric.value}</dd>
          <small>{metric.baseline === null ? t('Not available', '暂无数据') : `${t('Baseline', '基线')} ${metric.baseline}`}</small>
        </div>)}</dl>
      </div>
    </div>
  </section>
}
