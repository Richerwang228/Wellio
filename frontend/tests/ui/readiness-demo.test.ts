import { describe, expect, it } from 'vitest'
import { createDemoPreset, demoReadings } from '../../src/lib/readiness-demo'

describe('Wellio2 readiness preset integration', () => {
  it.each([
    ['low', 38, '恢复与活动度', 2200],
    ['balanced', 65, '轻量全身训练', 2400],
    ['high', 86, '拉类训练', 2500],
  ] as const)('%s keeps readings, workout and nutrition together', (mode, score, title, kcal) => {
    const plan = createDemoPreset(mode, 'zh-CN')
    expect(plan.source).toBe('preset')
    expect(plan.readings.score).toBe(score)
    expect(plan.workout.title).toBe(title)
    expect(plan.workout.items).toHaveLength(3)
    expect(plan.nutrition.targets.kcal).toBe(kcal)
  })
  it('returns independent plans and supports English', () => {
    const plan = createDemoPreset('low', 'en')
    plan.readings.score = 99
    plan.nutrition.targets.kcal = 1
    expect(demoReadings.low.score).toBe(38)
    expect(createDemoPreset('low', 'en').nutrition.targets.kcal).toBe(2200)
    expect(createDemoPreset('low', 'en').workout.title).toBe('Recovery & mobility')
  })
})
