import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import type { Locale, LocalizedText, Snapshot } from '../src/lib/contracts'
import { createFixture } from '../src/lib/fixtures'

const state = vi.hoisted(() => ({ locale: 'en' as Locale, snapshot: null as Snapshot | null, runAction: vi.fn(), setLocale: vi.fn() }))
vi.mock('../src/components/Icon', () => ({ Icon: () => <svg aria-hidden="true" /> }))
vi.mock('../src/components/Mascot', () => ({ Mascot: ({ alt }: { alt?: string }) => <img alt={alt} /> }))
vi.mock('../src/lib/wellio-context', () => ({ useWellio: () => ({ snapshot: state.snapshot, loading: false, error: null, busy: false, runAction: state.runAction, refresh: vi.fn() }) }))
vi.mock('../src/lib/i18n', () => ({
  bilingual: (en: string, zh: string): LocalizedText => ({ en, 'zh-CN': zh }),
  useI18n: () => ({ locale: state.locale, setLocale: state.setLocale,
    t: (en: string, zh: string) => state.locale === 'en' ? en : zh,
    text: (value: string | LocalizedText) => typeof value === 'string' ? value : value[state.locale],
    date: (value: string, options?: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat(state.locale, { timeZone: 'Asia/Hong_Kong', month: 'short', day: 'numeric', ...options }).format(new Date(value.length === 10 ? value + 'T12:00:00+08:00' : value)),
    duration: (minutes: number) => state.locale === 'en' ? `${minutes} minutes` : `${minutes} 分钟`,
  }),
}))
import { TrendsPage } from '../src/features/trends/TrendsPage'
import { ProfilePage } from '../src/features/profile/ProfilePage'
beforeEach(() => { state.locale = 'en'; state.snapshot = createFixture(); vi.clearAllMocks() })

describe('Trends data boundaries', () => {
  it('uses the selected exercise’s own latest date when weight data moves ahead', () => {
    state.snapshot!.history.weight.push({ date: '2026-10-01', kg: 70.6 })
    const html = renderToStaticMarkup(<TrendsPage />)
    expect(html).toContain('3 records')
    expect(html).toContain('Sep 10')
    expect(html).toContain('+5 kg')
    expect(html).not.toContain('No records in this period')
  })

  it('does not connect an identically named exercise from a different machine', () => {
    const first = state.snapshot!.history.load[0]
    state.snapshot!.history.load.push({ ...first, id: 'other-machine', equipmentId: 'gym-a-cable', date: '2026-09-11', kg: 100 })
    const html = renderToStaticMarkup(<TrendsPage />)
    expect(html).toContain('3 records')
    expect(html).not.toContain('100 kg')
    expect(html).not.toContain('+70 kg')
  })

  it('keeps a single point without inventing a change', () => {
    state.snapshot!.history.weight = [{ date: '2026-09-11', kg: 70.4 }]
    state.snapshot!.history.load = [state.snapshot!.history.load[0]]
    const html = renderToStaticMarkup(<TrendsPage />)
    expect(html).toContain('One record')
    expect(html).toContain('1 record')
    expect(html).not.toContain('+0 kg')
  })

  it('shows unavailable records instead of zero weight or fabricated sleep', () => {
    state.snapshot!.history.weight = []; state.snapshot!.history.load = []; state.snapshot!.sleep = null
    const html = renderToStaticMarkup(<TrendsPage />)
    expect(html).toContain('No records in this period')
    expect(html).toContain('No sleep record available')
    expect(html).not.toContain('7<')
    expect(html).not.toContain('0 kg')
  })

  it.each(['normal', 'low_recovery'] as const)('uses the same %s sleep record in English and Chinese', scenario => {
    state.snapshot = createFixture(scenario)
    const minutes = scenario === 'normal' ? 450 : 240
    const english = renderToStaticMarkup(<TrendsPage />)
    expect(english).toContain(`${minutes} minutes`)
    expect(english).not.toMatch(/[\u4e00-\u9fff]/)
    state.locale = 'zh-CN'
    const chinese = renderToStaticMarkup(<TrendsPage />)
    expect(chinese).toContain(`${minutes} 分钟`)
    expect(chinese).toContain('上一条记录')
    expect(chinese).not.toContain('Previous record')
  })
})

describe('Profile display and reset boundaries', () => {
  it('renders saved goals, read-only gyms and a reset warning without writing on render', () => {
    const html = renderToStaticMarkup(<ProfilePage />)
    expect(html).toContain('2,400')
    expect(html).toContain('Gym A')
    expect(html).toContain('Gym B')
    expect(html).toContain('These equipment lists are read-only')
    expect(html).toContain('Switching scenarios resets this session')
    expect(html).toContain('Keep my progress')
    expect(html).toContain('Confirm reset')
    expect(state.runAction).not.toHaveBeenCalled()
  })

  it('changes copy while leaving the same snapshot facts intact', () => {
    const before = structuredClone(state.snapshot)
    const english = renderToStaticMarkup(<ProfilePage />)
    state.locale = 'zh-CN'
    const chinese = renderToStaticMarkup(<ProfilePage />)
    expect(english).toContain('Recovery data source')
    expect(english).toContain('Chinese')
    expect(english).not.toMatch(/[\u4e00-\u9fff]/)
    expect(english).not.toContain('Your own pace')
    expect(english).not.toContain('A little care, every day')
    expect(chinese).toContain('恢复数据来源')
    expect(chinese).toContain('英文')
    expect(chinese).toContain('确认重置')
    expect(chinese).not.toContain('Confirm reset')
    expect(state.snapshot).toEqual(before)
    expect(state.runAction).not.toHaveBeenCalled()
  })
})
