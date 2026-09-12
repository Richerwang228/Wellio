import { useState } from 'react'
import { Button } from '@heroui/react'
import { useWellio } from '../../lib/wellio-context'
import { useI18n } from '../../lib/i18n'
import { Icon } from '../../components/Icon'
import { Mascot } from '../../components/Mascot'
import type { LoadBasis } from '../../lib/contracts'
import './trends.css'

type Point = { date: string; value: number }
function dayNumber(value: string) { return Date.parse(`${value.slice(0, 10)}T12:00:00Z`) / 86400000 }
function inWindow(value: string, end: string, days: number) {
  const difference = dayNumber(end) - dayNumber(value)
  return difference >= 0 && difference < days
}
function number(value: number) { return Number.isInteger(value) ? String(value) : value.toFixed(1) }

function RecordChart({ points, label, unit, tone }: { points: Point[]; label: string; unit: string; tone: 'weight' | 'strength' }) {
  const { t, date } = useI18n()
  const [chosen, setChosen] = useState<number | null>(null)
  const selected = Math.max(0, Math.min(points.length - 1, chosen ?? points.length - 1))
  const point = points[selected]
  if (!point) return <p className="trends-empty">{t('No records in this period.', '这段时间暂无记录。')}</p>
  const min = Math.min(...points.map(p => p.value)), max = Math.max(...points.map(p => p.value))
  const padding = Math.max(tone === 'weight' ? .1 : .5, (max - min) * .2)
  const x = (index: number) => points.length === 1 ? 175 : 38 + (dayNumber(points[index].date) - dayNumber(points[0].date)) / Math.max(1, dayNumber(points.at(-1)!.date) - dayNumber(points[0].date)) * 265
  const y = (value: number) => 78 - (value - min + padding) / (max - min + 2 * padding) * 64
  const move = (step: number) => setChosen(Math.max(0, Math.min(points.length - 1, selected + step)))
  return <div className="trends-chart-control" role="group" aria-label={t(`${label}. Use left and right arrows to inspect records.`, `${label}，使用左右方向键查看记录。`)} tabIndex={0} onKeyDown={event => {
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); move(event.key === 'ArrowLeft' ? -1 : 1) }
    if (event.key === 'Home' || event.key === 'End') { event.preventDefault(); setChosen(event.key === 'Home' ? 0 : points.length - 1) }
  }}>
    <svg viewBox="0 0 320 106" className={`trends-chart trends-chart-${tone}`} role="img" aria-label={`${label}: ${points.map(p => `${date(p.date, { month: 'short', day: 'numeric' })}, ${number(p.value)} ${unit}`).join('; ')}`}>
      {[...new Set([min, max])].map(value => <g key={value}><line x1="38" x2="303" y1={y(value)} y2={y(value)} className="trends-grid" /><text x="31" y={y(value) + 4} textAnchor="end">{number(value)}</text></g>)}
      {points.length > 1 && <polyline points={points.map((p, i) => `${x(i)},${y(p.value)}`).join(' ')} fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinejoin="round" />}
      <line x1={x(selected)} x2={x(selected)} y1="8" y2="83" className="trends-cursor" />
      {points.map((p, i) => <g key={`${p.date}-${i}`} onClick={() => setChosen(i)}><circle cx={x(i)} cy={y(p.value)} r="13" fill="transparent" /><circle cx={x(i)} cy={y(p.value)} r={i === selected ? 4 : 2.7} fill={i === selected ? 'var(--trends-paper)' : 'currentColor'} stroke="currentColor" strokeWidth="2" /></g>)}
      <text x="38" y="103">{date(points[0].date, { month: 'short', day: 'numeric' })}</text>
      {points.length > 1 && <text x="303" y="103" textAnchor="end">{date(points.at(-1)!.date, { month: 'short', day: 'numeric' })}</text>}
    </svg>
    <div className="trends-readout"><span aria-live="polite">{date(point.date, { month: 'short', day: 'numeric' })} · {number(point.value)} {unit}</span><div className="trends-record-buttons">
      <Button isIconOnly variant="ghost" className="trends-record-button" isDisabled={selected === 0} onPress={() => move(-1)} aria-label={t('Previous record', '上一条记录')}><Icon name="chevron-left" size={16} /></Button>
      <Button isIconOnly variant="ghost" className="trends-record-button" isDisabled={selected === points.length - 1} onPress={() => move(1)} aria-label={t('Next record', '下一条记录')}><Icon name="chevron-right" size={16} /></Button>
    </div></div>
  </div>
}

export function TrendsPage() {
  const { snapshot, loading, error, refresh } = useWellio()
  const { t, text, date, duration, locale } = useI18n()
  const [range, setRange] = useState<7 | 14>(14)
  const [selectedGroup, setSelectedGroup] = useState('')
  if (!snapshot) return <section className="trends-page"><h1>{t('Trends', '趋势')}</h1><p role="status">{loading ? t('Loading your records…', '正在读取记录…') : t('Your records could not be loaded.', '暂时无法读取记录。')}</p>{error && <Button onPress={() => { void refresh() }}>{t('Try again', '重试')}</Button>}</section>
  const weights = [...snapshot.history.weight].sort((a, b) => a.date.localeCompare(b.date))
  const groups = new Map<string, typeof snapshot.history.load>()
  for (const record of snapshot.history.load) {
    const key = `${record.exerciseId}|${record.equipmentId}|${record.basis}`
    groups.set(key, [...(groups.get(key) ?? []), record])
  }
  const groupKey = groups.has(selectedGroup) ? selectedGroup : [...groups.keys()][0]
  const load = [...(groups.get(groupKey) ?? [])].sort((a, b) => a.date.localeCompare(b.date))
  const end = weights.at(-1)?.date ?? [...snapshot.history.load].sort((a, b) => a.date.localeCompare(b.date)).at(-1)?.date ?? snapshot.dayKey
  const start = new Date((dayNumber(end) - range + 1) * 86400000).toISOString().slice(0, 10)
  const weightPoints = weights.filter(p => inWindow(p.date, end, range)).map(p => ({ date: p.date, value: p.kg }))
  const loadEnd = load.at(-1)?.date ?? snapshot.dayKey
  const loadPoints = load.filter(p => inWindow(p.date, loadEnd, range)).map(p => ({ date: p.date, value: p.kg }))
  const basisLabel = (basis?: LoadBasis) => basis === 'per_hand' ? t('per hand', '每只') : basis === 'bodyweight' ? t('bodyweight', '自重') : t('machine stack', '机器配重')
  const unit = t('kg', '千克')
  const loadUnit = load[0]?.basis === 'per_hand' ? `${unit} / ${t('hand', '每只')}` : unit
  const delta = (points: Point[]) => { const value = points.length > 1 ? points.at(-1)!.value - points[0].value : null; return !points.length ? t('No records', '暂无记录') : value === null ? t('One record', '单次记录') : `${value > 0 ? '+' : ''}${number(value)} ${unit}` }
  const sleep = snapshot.sleep
  const time = (value: string) => new Intl.DateTimeFormat(locale, { timeZone: snapshot.timeZone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(value))
  return <section className="trends-page" aria-labelledby="trends-title">
    <header className="trends-header"><div><h1 id="trends-title">{t('Your progress', '身体趋势')}</h1><p>{date(start, { month: 'short', day: 'numeric' })} – {date(end, { month: 'short', day: 'numeric' })}</p></div><div className="trends-range" aria-label={t('History period', '历史范围')}>{([7, 14] as const).map(days => <Button key={days} className={range === days ? 'trends-range-button trends-selected' : 'trends-range-button'} aria-pressed={range === days} onPress={() => setRange(days)}>{t(`${days} days`, `${days} 天`)}</Button>)}</div></header>
    <article className="trends-card trends-weight"><div className="trends-metric"><div><h2>{t('Body weight', '体重趋势')}</h2><strong>{weightPoints.length ? number(weightPoints.at(-1)!.value) : '—'}<small>{unit}</small></strong></div><p><b>{delta(weightPoints)}</b><span>{t(`${range}-day change`, `${range} 天变化`)}</span></p></div><RecordChart key={`weight-${range}-${snapshot.resetEpoch}`} points={weightPoints} label={t('Body weight', '体重趋势')} unit={unit} tone="weight" /></article>
    <article className="trends-card trends-strength"><div className="trends-exercise-heading"><h2><label htmlFor="trends-exercise">{t('Strength', '力量进步')}</label></h2><select id="trends-exercise" value={groupKey ?? ''} onChange={event => setSelectedGroup(event.target.value)} aria-label={t('Exercise and equipment', '动作与器械')} disabled={!groups.size}>{groups.size ? [...groups.entries()].map(([key, records]) => <option value={key} key={key}>{text(records[0].name)} · {records[0].equipmentId.startsWith('gym-a') ? 'Gym A' : 'Gym B'} · {basisLabel(records[0].basis)}</option>) : <option value="">{t('No exercise records', '暂无动作记录')}</option>}</select></div><div className="trends-metric"><div><strong>{loadPoints.length ? number(loadPoints.at(-1)!.value) : '—'}<small>{loadUnit}</small></strong>{!load.length && <span className="trends-equipment">{t('Record a workout to get started', '完成训练后积累记录')}</span>}</div><p><b>{delta(loadPoints)}</b><span>{t(`${loadPoints.length} ${loadPoints.length === 1 ? 'record' : 'records'}`, `${loadPoints.length} 次记录`)}</span></p></div><RecordChart key={`${groupKey}-${range}-${snapshot.resetEpoch}`} points={loadPoints} label={t('Training load', '训练重量')} unit={loadUnit} tone="strength" /></article>
    <article className="trends-sleep"><Mascot pose="sleep" className="trends-sleep-mascot" alt="" /><div><h2>{t('Last night’s sleep', '昨晚睡眠')}</h2><p>{sleep ? `${date(sleep.bedtime, { month: 'numeric', day: 'numeric' })} ${time(sleep.bedtime)} – ${date(sleep.wakeTime, { month: 'numeric', day: 'numeric' })} ${time(sleep.wakeTime)}` : t('No sleep record available', '暂无睡眠记录')}</p></div><strong className="trends-sleep-duration" aria-label={sleep ? duration(sleep.minutes) : t('Unknown duration', '时长未知')}>{sleep ? <>{Math.floor(sleep.minutes / 60)}<small>{t('h', '时')}</small>{sleep.minutes % 60 ? <>{sleep.minutes % 60}<small>{t('min', '分')}</small></> : null}</> : '—'}</strong></article>
  </section>
}
