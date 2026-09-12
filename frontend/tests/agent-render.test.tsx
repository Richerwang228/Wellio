import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import type { Locale, LocalizedText, Message, Snapshot, ToolStep } from '../src/lib/contracts'
import { createFixture } from '../src/lib/fixtures'

const state = vi.hoisted(() => ({
  locale: 'en' as Locale,
  snapshot: null as Snapshot | null,
  chatBusy: false,
  runAction: vi.fn(),
  sendMessage: vi.fn(),
}))
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => vi.fn() }))
vi.mock('../src/components/Icon', () => ({ Icon: ({ name }: { name: string }) => <svg data-icon={name} aria-hidden="true" /> }))
vi.mock('../src/components/Mascot', () => ({ Mascot: ({ alt }: { alt?: string }) => <span role="img" aria-label={alt} /> }))
vi.mock('../src/lib/api-client', () => ({ api: { upload: vi.fn() } }))
vi.mock('../src/lib/i18n', () => ({
  bilingual: (en: string, zh: string): LocalizedText => ({ en, 'zh-CN': zh }),
  useI18n: () => ({
    locale: state.locale,
    t: (en: string, zh: string) => state.locale === 'en' ? en : zh,
    text: (value: string | LocalizedText) => typeof value === 'string' ? value : value[state.locale],
    date: (value: string) => value,
  }),
}))
vi.mock('../src/lib/wellio-context', () => ({ useWellio: () => ({
  snapshot: state.snapshot, loading: false, error: null, busy: false, chatBusy: state.chatBusy,
  draft: '', setDraft: vi.fn(), chatTarget: {}, setChatTarget: vi.fn(), runAction: state.runAction,
  sendMessage: state.sendMessage, stopChat: vi.fn(), refresh: vi.fn(),
}) }))

import { AgentMessageBody, AgentPage, AgentToolProcess } from '../src/features/agent/AgentPage'

function message(overrides: Partial<Message> = {}): Message {
  return { id: 'message-test', role: 'assistant', content: { en: 'Your current plan.', 'zh-CN': '当前方案。' }, createdAt: '2026-09-12T08:00:00+08:00', source: 'agent', status: 'complete', steps: [], ...overrides }
}

beforeEach(() => {
  state.locale = 'en'
  state.snapshot = createFixture()
  state.chatBusy = false
  vi.clearAllMocks()
})

describe('Agent rendering boundaries', () => {
  it('renders the real knowledge step and verified reference link', () => {
    state.locale = 'zh-CN'
    const process = renderToStaticMarkup(<AgentToolProcess steps={[{id:'knowledge-1',toolCallId:'knowledge-call',operation:'knowledge_search',status:'started'}]} />)
    expect(process).toContain('查阅专业资料')
    const body = renderToStaticMarkup(<AgentMessageBody content={'参考依据：[Sleep](<https://example.org/sleep>)'} />)
    expect(body).toContain('href="https://example.org/sleep"')
  })

  it('does not show an empty or fabricated process for a model-only reply', () => {
    state.snapshot!.messages = [message({ status: 'streaming', phase: 'recognizing' })]
    const html = renderToStaticMarkup(<AgentPage />)
    expect(html).toContain('Recognizing the food')
    expect(html).not.toContain('class="agent-process"')
    expect(html).not.toContain('Completed 1 step')
    expect(state.runAction).not.toHaveBeenCalled()
    expect(state.sendMessage).not.toHaveBeenCalled()
  })

  it('counts a tool lifecycle once, using its latest status', () => {
    const step: ToolStep = { id: 'event-1', toolCallId: 'call-1', operation: 'context', status: 'started' }
    const html = renderToStaticMarkup(<AgentToolProcess steps={[step, { ...step, id: 'event-2', status: 'succeeded' }]} />)
    expect(html).toContain('Completed 1 step')
    expect(html).not.toContain('Completed 2 steps')
    expect(html).not.toContain('In progress')
  })

  it('keeps failed tool details visible and does not claim completion', () => {
    const html = renderToStaticMarkup(<AgentToolProcess steps={[{ id: 'event-1', toolCallId: 'call-1', operation: 'menu_search', status: 'failed', errorCode: 'TIMEOUT' }]} />)
    expect(html).toContain('open=""')
    expect(html).toContain('A step needs attention')
    expect(html).toContain('Check the latest saved state')
    expect(html).not.toContain('Completed 1 step')
  })

  it('disables a stale proposal by removing its Apply control', () => {
    const snapshot = state.snapshot!
    snapshot.proposals = [{ id: 'proposal-1', scope: 'schedule', status: 'pending', expected: { meal: 1, plan: 0, workout: 1, conditions: 1, readiness: 1 }, reason: { en: 'Rest today', 'zh-CN': '今天休息' }, contextReadId: 'read-1', readinessSnapshotId: snapshot.readiness.id, restDate: snapshot.dayKey }]
    snapshot.messages = [message({ proposalId: 'proposal-1' })]
    const html = renderToStaticMarkup(<AgentPage />)
    expect(html).toContain('This suggestion can no longer be applied')
    expect(html).not.toContain('Apply rest day')
    expect(state.runAction).not.toHaveBeenCalled()
  })

  it('renders a pending rest proposal with Apply but no start-workout instruction', () => {
    const snapshot = state.snapshot!
    snapshot.proposals = [{ id: 'proposal-1', scope: 'schedule', status: 'pending', expected: { meal: 1, plan: 1, workout: 1, conditions: 1, readiness: 1 }, reason: { en: 'Rest today', 'zh-CN': '今天休息' }, contextReadId: 'read-1', readinessSnapshotId: snapshot.readiness.id, restDate: snapshot.dayKey }]
    snapshot.messages = [message({ proposalId: 'proposal-1' })]
    const html = renderToStaticMarkup(<AgentPage />)
    expect(html).toContain('Apply rest day')
    expect(html).toContain('Keep original plan')
    expect(html).not.toContain('Start workout')
    expect(html).not.toContain('Changes saved')
  })

  it('renders Chinese controls and retained-write explanation for a stopped reply', () => {
    state.locale = 'zh-CN'
    state.snapshot!.messages = [message({ status: 'stopped' })]
    const html = renderToStaticMarkup(<AgentPage />)
    expect(html).toContain('推荐一餐')
    expect(html).toContain('调整计划')
    expect(html).toContain('已保存的修改会保留')
    expect(html).toContain('核对后重试')
    expect(html).not.toContain('Recommend a meal')
  })

  it('offers a stop control while streaming and preserves meal correction actions', () => {
    state.chatBusy = true
    state.snapshot!.messages = [message({ mealId: 'meal-lunch', operationId: 'operation-1' })]
    const html = renderToStaticMarkup(<AgentPage />)
    expect(html).toContain('aria-label="Stop reply"')
    expect(html).toContain('Modify')
    expect(html).toContain('Undo')
    expect(html).toMatch(/readonly=""/i)
  })

  it('uses Markdown formatting and does not render raw executable HTML', () => {
    const html = renderToStaticMarkup(<AgentMessageBody content={'**Keep your progress.**\n\n[Source](https://example.com/menu)\n\n<script>alert(1)</script>'} />)
    expect(html).toContain('<strong>Keep your progress.</strong>')
    expect(html).toContain('rel="noopener noreferrer"')
    expect(html).not.toContain('<script>')
  })

  it('keeps Undo for a deleted meal without offering to modify a missing record', () => {
    state.snapshot!.meals = state.snapshot!.meals.filter(meal => meal.id !== 'meal-lunch')
    state.snapshot!.messages = [message({ mealId: 'meal-lunch', operationId: 'delete-operation' })]
    const html = renderToStaticMarkup(<AgentPage />)
    expect(html).toContain('Undo')
    expect(html).not.toContain('>Modify</button>')
  })

  it.each(['failed', 'stopped'] as const)('retains saved-meal Undo when generation is %s', status => {
    state.snapshot!.messages = [message({ status, mealId: 'meal-lunch', operationId: 'saved-operation' })]
    const html = renderToStaticMarkup(<AgentPage />)
    expect(html).toContain('Undo')
    expect(html).toContain('Modify')
    expect(html).toContain('Review &amp; retry')
  })
})


describe('Proposal comparison uses saved structured data', () => {
  it('shows changed and removed exercises before Apply without claiming a save', () => {
    const snapshot = state.snapshot!
    const proposed = structuredClone(snapshot.workout!)
    proposed.estimatedMinutes = 25
    proposed.exercises[0].sets = 2
    proposed.exercises.pop()
    snapshot.proposals = [{ id: 'comparison', scope: 'workout', status: 'pending', expected: { meal: snapshot.mealRevision, plan: snapshot.plan.version, workout: snapshot.workout!.version, conditions: snapshot.conditions.version, readiness: snapshot.readiness.version }, reason: { en: 'A shorter session', 'zh-CN': '缩短训练' }, contextReadId: 'read-1', readinessSnapshotId: snapshot.readiness.id, workout: proposed }]
    snapshot.messages = [message({ proposalId: 'comparison' })]
    const html = renderToStaticMarkup(<AgentPage />)
    expect(html).toContain('Proposed changes · Not applied')
    expect(html).toContain('35 min')
    expect(html).toContain('25 min')
    expect(html).toContain('3 × 12 · 35 kg')
    expect(html).toContain('2 × 12 · 35 kg')
    expect(html).toContain('Removed')
    expect(html).not.toContain('Workout updated')
    expect(snapshot.workout!.exercises[0].sets).toBe(3)
    expect(state.runAction).not.toHaveBeenCalled()
    snapshot.proposals[0].status = 'applied'
    const applied = renderToStaticMarkup(<AgentPage />)
    expect(applied).toContain('Workout updated')
    expect(applied).not.toContain('Proposed changes · Not applied')
  })
  it('uses existing advice in the empty conversation without inventing an AI message', () => {
    state.snapshot!.advice = { status: 'valid', training: { en: 'Keep the saved plan.', 'zh-CN': '保持已保存计划。' } }
    const html = renderToStaticMarkup(<AgentPage />)
    expect(html).toContain('Keep the saved plan.')
    expect(html).toContain('8.2')
    expect(html).toContain('Pull workout')
    expect(state.snapshot!.messages).toEqual([])
    expect(state.sendMessage).not.toHaveBeenCalled()
  })
})
