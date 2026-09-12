import { useEffect, useRef, useState } from 'react'
import { Button } from '@heroui/react'
import { useWellio } from '../../lib/wellio-context'
import { useI18n } from '../../lib/i18n'
import { Mascot } from '../../components/Mascot'
import { Icon } from '../../components/Icon'
import type { Scenario } from '../../lib/contracts'
import './profile.css'

export function ProfilePage() {
  const { snapshot, loading, error, busy, runAction, refresh } = useWellio()
  const { t, text, date, setLocale, locale } = useI18n()
  const [expanded, setExpanded] = useState<'preferences' | 'gyms' | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [confirmation, setConfirmation] = useState<{ scenario: Scenario; reset: boolean } | null>(null)
  const [resetError, setResetError] = useState(false)
  const [resetting, setResetting] = useState(false)
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    if (confirmation && !dialog.current?.open) dialog.current?.showModal()
    if (!confirmation && dialog.current?.open) dialog.current.close()
  }, [confirmation])
  const requestReset = (scenario: Scenario, reset: boolean) => { setResetError(false); setConfirmation({ scenario, reset }) }
  const confirmReset = async () => {
    if (!confirmation || resetting) return
    setResetting(true); setResetError(false)
    try {
      const result = await runAction({ kind: 'reset_demo', scenario: confirmation.scenario }, 'profile')
      if (result?.status === 'succeeded') setConfirmation(null)
      else setResetError(true)
    } catch { setResetError(true) }
    finally { setResetting(false) }
  }
  if (!snapshot) return <section className="profile-page"><h1>{t('Profile', '我的')}</h1><p role="status">{loading ? t('Loading your profile…', '正在读取资料…') : t('Your profile could not be loaded.', '暂时无法读取资料。')}</p>{error && <Button onPress={() => { void refresh() }}>{t('Try again', '重试')}</Button>}</section>
  const weight = [...snapshot.history.weight].sort((a, b) => a.date.localeCompare(b.date)).at(-1)
  const targets = snapshot.profile.targets
  return <section className="profile-page" aria-labelledby="profile-title">
    <header className="profile-heading"><h1 id="profile-title">{t('Profile', '我的')}</h1></header>
    <section className="profile-hero"><Mascot pose="welcome" className="profile-avatar" alt={t('Wellio avocado companion', 'Wellio 牛油果伙伴')} /><h2>{snapshot.profile.name}</h2></section>
    <section className="profile-card profile-facts" aria-label={t('Personal overview', '个人概览')}><div><span>{t('Current goal', '当前目标')}</span><strong>{t('Build muscle', '增肌')}</strong></div><div><span>{t('Body weight', '最近体重')}{weight && <small>{date(weight.date, { month: 'short', day: 'numeric' })}</small>}</span><strong>{weight ? `${weight.kg.toFixed(1)} ${t('kg', '千克')}` : t('Not recorded', '未记录')}</strong></div><div><span>{t('Body fat', '体脂率')}</span><strong>{t('Not set', '未设置')}</strong></div></section>
    <section aria-labelledby="profile-nutrition-title"><h2 className="profile-section-title" id="profile-nutrition-title">{t('Daily nutrition goals', '每日营养目标')}</h2><div className="profile-card profile-nutrition">{[
      [t('Energy', '热量'), targets.kcal, t('kcal', '千卡')], [t('Protein', '蛋白质'), targets.protein, t('g', '克')], [t('Carbs', '碳水'), targets.carbs, t('g', '克')], [t('Fat', '脂肪'), targets.fat, t('g', '克')],
    ].map(([name, value, unit]) => <div key={name}><span>{name}</span><strong>{Number(value).toLocaleString(locale)} <small>{unit}</small></strong></div>)}</div></section>
    <section className="profile-card profile-information" aria-label={t('Preferences and gyms', '偏好与健身房')}>
      <button className="profile-disclosure" type="button" aria-expanded={expanded === 'preferences'} aria-controls="profile-preferences" onClick={() => setExpanded(expanded === 'preferences' ? null : 'preferences')}><span>{t('Food preferences', '饮食偏好')}</span><Icon name={expanded === 'preferences' ? 'minus' : 'plus'} size={16} /></button>
      <div id="profile-preferences" hidden={expanded !== 'preferences'} className="profile-detail"><p>{text(snapshot.profile.dislikes)}</p><p>{t('Dinner budget', '晚餐预算')} · HK${snapshot.profile.dinnerBudget}</p><small>{t('For a different budget today, tell Wellio in Agent.', '本次预算有变化，可在助手对话中告诉 Wellio。')}</small></div>
      <button className="profile-disclosure" type="button" aria-expanded={expanded === 'gyms'} aria-controls="profile-gyms" onClick={() => setExpanded(expanded === 'gyms' ? null : 'gyms')}><span>{t('Saved gyms', '已保存的健身房')}</span><Icon name={expanded === 'gyms' ? 'minus' : 'plus'} size={16} /></button>
      <div id="profile-gyms" hidden={expanded !== 'gyms'} className="profile-detail"><h3>Gym A</h3><p>{t('Dumbbells, bench, cable machine, pull-up bar', '哑铃、训练凳、拉力器、单杠')}</p><h3>Gym B</h3><p>{t('Dumbbells, bench, cable machine', '哑铃、训练凳、拉力器')}</p><small>{t('Choose today’s gym in Today. These equipment lists are read-only.', '在今日选择训练场地，此处只查看器械资料。')}</small></div>
    </section>
    <section className="profile-card profile-language" aria-labelledby="profile-language-label"><h2 id="profile-language-label">{t('Language', '语言')}</h2><div><Button className={locale === 'en' ? 'profile-language-button profile-current' : 'profile-language-button'} aria-pressed={locale === 'en'} isDisabled={busy} onPress={() => { setLocale('en'); void runAction({ kind: 'set_locale', locale: 'en' }, 'profile') }}>{t('English', '英文')}</Button><Button className={locale === 'zh-CN' ? 'profile-language-button profile-current' : 'profile-language-button'} aria-pressed={locale === 'zh-CN'} isDisabled={busy} onPress={() => { setLocale('zh-CN'); void runAction({ kind: 'set_locale', locale: 'zh-CN' }, 'profile') }}>{t('Chinese', '简体中文')}</Button></div></section>
    <section className="profile-card profile-settings"><button type="button" className="profile-disclosure" aria-expanded={settingsOpen} aria-controls="profile-settings" onClick={() => setSettingsOpen(!settingsOpen)}><span>{t('Other settings', '其他设置')}</span><Icon name={settingsOpen ? 'minus' : 'plus'} size={16} /></button>
      <div id="profile-settings" hidden={!settingsOpen} className="profile-detail"><dl><div><dt>{t('Estimated expenditure', '估算总消耗')}</dt><dd>{snapshot.profile.expenditure.toLocaleString(locale)} {t('kcal', '千卡')}</dd></div><div><dt>{t('Date', '日期')}</dt><dd>{date(snapshot.dayKey, { year: 'numeric', month: 'short', day: 'numeric' })}</dd></div><div><dt>{t('Time zone', '时区')}</dt><dd>{t('Hong Kong (UTC+8)', '香港（UTC+8）')}</dd></div></dl><h3>{t('Recovery data source', '恢复数据来源')}</h3><p>{t('Simulated watch input, used to demonstrate how recovery information affects training plans. No watch is connected.', '模拟手表输入，用于演示恢复信息如何影响训练安排。未连接真实手表。')}</p><label className="profile-scenario-label" htmlFor="profile-scenario">{t('Recovery scenario', '恢复场景')}</label><select id="profile-scenario" value={snapshot.scenario} disabled={Boolean(busy) || resetting} onChange={event => { const value = event.target.value as Scenario; if (value !== snapshot.scenario) requestReset(value, false) }}><option value="normal">{t('Normal recovery', '正常恢复')}</option><option value="low_recovery">{t('Low recovery', '低恢复')}</option></select><p className="profile-setting-note">{t('Switching scenarios resets this session’s meals, training progress and conversation. You will confirm first.', '切换场景会重置本次饮食、训练进度和对话，操作前需要确认。')}</p><Button className="profile-reset-button" isDisabled={Boolean(busy) || resetting} onPress={() => requestReset(snapshot.scenario, true)}>{t('Reset this session', '重置本次演示')}</Button></div>
    </section>
    <dialog ref={dialog} className="profile-confirm-dialog" aria-labelledby="profile-confirm-title" aria-describedby="profile-confirm-description" onCancel={event => { if (resetting) event.preventDefault(); else setConfirmation(null) }} onClose={() => { if (!resetting) setConfirmation(null) }}><h2 id="profile-confirm-title">{confirmation?.reset ? t('Reset this session?', '重置本次演示？') : t('Switch recovery scenario?', '切换恢复场景？')}</h2><p id="profile-confirm-description">{t('This will reset this session’s meals, training progress, plans and conversation, then return to Agent. It cannot be undone.', '这将重置本次饮食、训练进度、安排和对话，并返回助手。此操作无法撤销。')}</p><p>{t('Scenario:', '恢复场景：')} {confirmation?.scenario === 'low_recovery' ? t('Low recovery', '低恢复') : t('Normal recovery', '正常恢复')}</p>{resetError && <p className="profile-reset-error" role="alert">{t('The reset could not be confirmed. Your current data is still shown. Please try again.', '尚未确认重置成功，当前仍显示原数据，请重试。')}</p>}<div className="profile-confirm-actions"><Button autoFocus variant="secondary" isDisabled={resetting} onPress={() => setConfirmation(null)}>{t('Keep my progress', '保留当前进度')}</Button><Button isDisabled={resetting} isPending={resetting} onPress={() => { void confirmReset() }}>{resetting ? t('Resetting…', '正在重置…') : t('Confirm reset', '确认重置')}</Button></div></dialog>
  </section>
}
