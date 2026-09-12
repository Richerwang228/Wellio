import {test, expect} from '@playwright/test'
import {createFixture} from '../../src/lib/fixtures'

for (const locale of ['en', 'zh-CN'] as const) {
  for (const scenario of ['normal', 'low_recovery'] as const) {
    test(`${locale} ${scenario}: Today overview fits regular phone viewports`, async ({page}) => {
      await page.addInitScript(value => localStorage.setItem('wellio.locale.v1', value), locale)
      await page.route('**/api/state', route => route.fulfill({json: createFixture(scenario)}))
      for (const viewport of [{width: 390, height: 844}, {width: 375, height: 812}, {width: 390, height: 790}, {width: 390, height: 740}]) {
        await page.setViewportSize(viewport)
        await page.goto('/today')
        await expect(page.locator('.today-meal-card')).toHaveCount(2)
        await page.evaluate(() => document.fonts.ready)
        const dimensions = await page.locator('.app-main').evaluate(el => ({height: el.clientHeight, content: el.scrollHeight, width: el.clientWidth, contentWidth: el.scrollWidth}))
        expect(dimensions.content, `${viewport.width}×${viewport.height}: content should fit`).toBeLessThanOrEqual(dimensions.height + 1)
        expect(dimensions.contentWidth).toBeLessThanOrEqual(dimensions.width)
        await expect(page.locator('.today-gauge-button')).toBeInViewport({ratio: 1})
        await expect(page.locator('.today-plan-main-action')).toBeInViewport({ratio: 1})
        await expect(page.locator('.today-exercises summary')).toHaveCount(3)
        for (const summary of await page.locator('.today-exercises summary').all()) await expect(summary).toBeInViewport({ratio: 1})
        await expect(page.locator('.today-fuel')).toBeInViewport({ratio: 1})
        await expect(page.locator('.app-nav')).toBeInViewport({ratio: 1})
      }
    })
  }
}

test('small viewports and expanded details stay readable and reachable', async ({page}) => {
  await page.setViewportSize({width: 320, height: 640})
  await page.route('**/api/state', route => route.fulfill({json: createFixture()}))
  await page.goto('/today')
  const firstExercise = page.locator('.today-exercise-details').first()
  await firstExercise.locator('summary').click()
  await expect(firstExercise).toHaveAttribute('open', '')
  await expect(firstExercise).toContainText('Keep your torso steady.')
  const meal = page.locator('.today-meal-card').first()
  await meal.scrollIntoViewIfNeeded()
  await meal.click()
  await expect(page.locator('.today-dialog')).toBeVisible()
  await expect(page.locator('.today-dialog')).toContainText('Chicken rice & vegetables')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})
