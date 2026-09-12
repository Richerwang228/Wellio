import {test, expect, type Page} from '@playwright/test'
import {createFixture} from '../../src/lib/fixtures'
import type {ActionRequest, ActionResult, Locale, Proposal, Snapshot} from '../../src/lib/contracts'

type Reply = (request: ActionRequest, state: Snapshot) => Partial<ActionResult>

async function showToday(page: Page, initial: Snapshot, locale: Locale = 'en', reply?: Reply) {
  const state = structuredClone(initial)
  const actions: ActionRequest[] = []
  await page.setViewportSize({width: 390, height: 790})
  await page.addInitScript(value => localStorage.setItem('wellio.locale.v1', value), locale)
  await page.route('**/api/state', route => route.fulfill({json: state}))
  await page.route(/\/api\/(?:actions|copilotkit\/proposal)$/,  async route => {
    const request = route.request().postDataJSON() as ActionRequest
    actions.push(request)
    await route.fulfill({json: {status: 'succeeded', requestId: request.requestId, snapshot: state, ...reply?.(request, state)}})
  })
  await page.goto('/today')
  await expect(page.locator('.today-plan')).toBeVisible()
  return actions
}

async function expectSavedExercises(page: Page, state: Snapshot, locale: Locale = 'en') {
  const summaries = page.locator('.today-exercises summary')
  await expect(summaries).toHaveCount(state.workout!.exercises.length)
  for (const [index, exercise] of state.workout!.exercises.entries()) {
    await expect(summaries.nth(index)).toBeVisible()
    await expect(summaries.nth(index)).toContainText(exercise.name[locale])
    await expect(summaries.nth(index)).toContainText(`${exercise.sets} × ${exercise.reps}`)
  }
}

async function expectNoTrainingDecision(page: Page) {
  await expect(page.getByRole('button', {name: /^(Start workout|Start current plan|Confirm rest and reschedule|开始训练|按现有计划开始|确认休息并顺延)$/})).toHaveCount(0)
  await expect(page.locator('.today-date-moves')).toHaveCount(0)
  await expect(page.locator('.today-rest')).toHaveCount(0)
}

function restCandidate(state: Snapshot): Proposal {
  return {id: 'review-rest-candidate', scope: 'schedule', status: 'pending',
    reason: {en: 'A rest day fits today’s recovery.', 'zh-CN': '根据今天的恢复情况，建议休息一天。'},
    expected: {meal: state.mealRevision, plan: state.plan.version, workout: state.workout?.version ?? 0, conditions: state.conditions.version, readiness: state.readiness.version},
    contextReadId: 'review-context', readinessSnapshotId: state.readiness.id, restDate: state.dayKey,
    moves: state.plan.sessions.map((session, index) => ({sessionId: session.id, split: session.split, from: session.date, to: state.plan.availableSlots[index + 1].date}))}
}

for (const locale of ['en', 'zh-CN'] as const) {
  const en = locale === 'en'
  test(`${locale}: low recovery keeps the complete saved plan and one review action before a candidate`, async ({page}) => {
    const state = createFixture('low_recovery'); state.capabilities.agent = true
    const actions = await showToday(page, state, locale)
    await expect(page.locator('.today-eyebrow')).toHaveText(en ? 'Saved workout · Awaiting review' : '原已保存计划 · 等待评估')
    await expect(page.locator('.today-plan')).toContainText(en ? 'A recommendation is not ready yet. Your current plan is unchanged.' : '建议尚未生成，当前计划保持不变。')
    await expectSavedExercises(page, state, locale)
    await expect(page.locator('.today-plan .today-button-primary')).toHaveCount(1)
    await expect(page.getByRole('button', {name: en ? 'Prepare a suggestion' : '生成调整建议', exact: true})).toBeEnabled()
    await expectNoTrainingDecision(page)
    expect(actions).toEqual([])
  })

  test(`${locale}: an unconfigured model never fabricates a rest candidate or offers Start`, async ({page}) => {
    const state = createFixture('low_recovery')
    const actions = await showToday(page, state, locale)
    await expectSavedExercises(page, state, locale)
    await expect(page.locator('.today-plan')).toContainText(en ? 'Training advice is unavailable right now. Your current plan is unchanged.' : '训练建议暂不可用，当前计划保持不变。')
    await expect(page.locator('.today-plan .today-button-primary')).toHaveCount(1)
    await expect(page.getByRole('button', {name: en ? 'Suggestions unavailable' : '建议暂不可用', exact: true})).toBeDisabled()
    await expectNoTrainingDecision(page)
    expect(actions).toEqual([])
  })
}

test('an active recovery review keeps exercises readable and does not offer a competing Start', async ({page}) => {
  const state = createFixture('low_recovery'); state.capabilities.agent = true
  state.readinessCheck = {key: 'active-review', status: 'pending'}
  const actions = await showToday(page, state)
  await expectSavedExercises(page, state)
  await expect(page.locator('.today-plan .today-button-primary')).toHaveCount(1)
  await expect(page.getByRole('button', {name: 'Preparing a suggestion…', exact: true})).toBeDisabled()
  await expectNoTrainingDecision(page)
  expect(actions).toEqual([])
})

test('a failed review preserves the saved plan, true error and review action without creating a candidate', async ({page}) => {
  const state = createFixture('low_recovery'); state.capabilities.agent = true
  const actions = await showToday(page, state, 'en', () => ({status: 'needs_input', errorCode: 'PROVIDER_NOT_CONFIGURED'}))
  await page.getByRole('button', {name: 'Prepare a suggestion', exact: true}).click()
  await expect(page.locator('.today-error')).toContainText('The AI connection is not configured yet. Your records are still available.')
  await expect(page.locator('.today-error')).not.toContainText('A little more information is needed')
  await expectSavedExercises(page, state)
  await expectNoTrainingDecision(page)
  await expect(page.locator('.today-plan .today-button-primary')).toHaveCount(1)
  await expect(page).toHaveURL(/\/today$/)
  expect(actions.map(action => action.kind)).toEqual(['request_proposal'])
})

test('only the returned rest candidate unlocks explicit confirmation and never starts training', async ({page}) => {
  const state = createFixture('low_recovery'); state.capabilities.agent = true
  const actions = await showToday(page, state, 'en', (request, current) => {
    if (request.kind === 'request_proposal') current.proposals = [restCandidate(current)]
    if (request.kind === 'apply_proposal') {
      current.proposals[0].status = 'applied'
      current.plan.restDates = [current.dayKey]
    }
    current.revision++
    return {}
  })
  await expectNoTrainingDecision(page)
  await page.getByRole('button', {name: 'Prepare a suggestion', exact: true}).click()
  const confirm = page.getByRole('button', {name: 'Confirm rest and reschedule', exact: true})
  await expect(confirm).toBeVisible()
  await expect(page.getByRole('button', {name: 'Prepare a suggestion', exact: true})).toHaveCount(0)
  await expect(page.getByRole('button', {name: 'Start workout', exact: true})).toHaveCount(0)
  await confirm.click()
  await expect(page.getByRole('heading', {name: 'Rest today', exact: true})).toBeVisible()
  await expect(page).toHaveURL(/\/today$/)
  expect(actions.map(action => action.kind)).toEqual(['request_proposal', 'apply_proposal'])
  expect(actions[1]).toMatchObject({proposalId: 'review-rest-candidate', startAfterApply: false, source: 'today'})
})

for (const mode of ['normal', 'dismissed', 'in_progress', 'completed'] as const) {
  test(`${mode}: the existing workout action remains available`, async ({page}) => {
    const state = createFixture(mode === 'normal' ? 'normal' : 'low_recovery')
    if (mode === 'dismissed') state.proposals = [{...restCandidate(state), status: 'dismissed'}]
    if (mode === 'in_progress' || mode === 'completed') state.workout!.status = mode
    const actions = await showToday(page, state, 'en', (request, current) => {
      if (request.kind === 'start_workout') {current.workout!.status = 'in_progress'; current.workout!.version++; current.revision++}
      return {}
    })
    await expectSavedExercises(page, state)
    const label = mode === 'normal' ? 'Start workout' : mode === 'dismissed' ? 'Start current plan' : mode === 'in_progress' ? 'Continue workout' : 'View workout record'
    await expect(page.locator('.today-plan .today-button-primary')).toHaveCount(1)
    await page.getByRole('button', {name: label, exact: true}).click()
    await expect(page).toHaveURL(/\/workout$/)
    expect(actions.map(action => action.kind)).toEqual(mode === 'normal' || mode === 'dismissed' ? ['start_workout'] : [])
  })
}
