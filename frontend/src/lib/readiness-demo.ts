import type { Locale, Nutrients } from './contracts'

export type ReadinessDemoMode = 'low' | 'balanced' | 'high'
export interface DemoReadings {
  score: number; sleepMinutes: number; deepSleepPercent: number; restingHeartRate: number; hrvMs: number
}
export const demoModes: ReadinessDemoMode[] = ['low', 'balanced', 'high']
/** Synthetic inputs for product demonstrations, never HealthKit measurements. */
export const demoReadings: Record<ReadinessDemoMode, DemoReadings> = {
  low: { score: 38, sleepMinutes: 312, deepSleepPercent: 9, restingHeartRate: 68, hrvMs: 31 },
  balanced: { score: 65, sleepMinutes: 405, deepSleepPercent: 17, restingHeartRate: 59, hrvMs: 48 },
  high: { score: 86, sleepMinutes: 468, deepSleepPercent: 22, restingHeartRate: 54, hrvMs: 62 },
}
export const demoBaseline = { sleepMinutes: 450, restingHeartRate: 55, deepSleepPercent: 20, hrvMs: 60 }
export interface DemoPlanContent {
  workout: { title: string; minutes: number; summary: string; items: { name: string; prescription: string; detail: string }[] }
  nutrition: { targets: Nutrients; summary: string; notes: string[] }
}
export interface DemoDailyPlan extends DemoPlanContent {
  mode: ReadinessDemoMode; source: 'agent' | 'preset'; readings: DemoReadings
}
export interface DemoPlanRequest { mode: ReadinessDemoMode; locale: Locale; resetEpoch: number; requestId: string }

/** Deliberately labelled examples. Never used to fill in a failed AI response. */
export function createDemoPreset(mode: ReadinessDemoMode, locale: Locale): DemoDailyPlan {
  const t = (en: string, zh: string) => locale === 'en' ? en : zh
  const example = {
    low: {
      workout: { title: t('Recovery & mobility', '恢复与活动度'), minutes: 25,
        summary: t('Keep movement easy after a shorter night.', '昨晚睡眠不足，今天以轻松活动为主。'),
        items: [
          { name: t('Easy walk', '轻松步行'), prescription: t('15 min', '15 分钟'), detail: t('An easy, conversational pace. No load target.', '以能够轻松交谈的速度行走，不设负重目标。') },
          { name: t('Hip mobility', '髋部活动'), prescription: t('5 min', '5 分钟'), detail: t('Slow, comfortable movements within your usual range.', '在平时舒适的活动范围内，缓慢进行。') },
          { name: t('Shoulder mobility', '肩部活动'), prescription: t('5 min', '5 分钟'), detail: t('Gentle shoulder circles and relaxed upper-body movement.', '轻柔绕肩，放松上半身。') },
        ] },
      nutrition: { targets: { kcal: 2200, protein: 140, carbs: 230, fat: 80 },
        summary: t('Regular meals, steady protein and hydration.', '规律进餐，保证蛋白质和日常补水。'),
        notes: [t('Spread the daily protein target across your meals.', '将每日蛋白质目标分配到各餐。'), t('Keep a regular meal schedule on this lighter day.', '轻活动日也保持规律进餐。')] },
    },
    balanced: {
      workout: { title: t('Light full-body workout', '轻量全身训练'), minutes: 30,
        summary: t('A moderate session with room to recover.', '以适中训练量练习，为恢复留出余地。'),
        items: [
          { name: t('Bodyweight squat', '自重深蹲'), prescription: '2 × 10', detail: t('Comfortable depth, with controlled movement.', '选择舒适深度，控制动作。') },
          { name: t('Seated cable row', '坐姿绳索划船'), prescription: '2 × 12', detail: t('Use a familiar, comfortable load; rest 75 seconds between sets.', '选择熟悉且轻松的负重，组间休息 75 秒。') },
          { name: t('Dumbbell curl', '哑铃弯举'), prescription: '2 × 10', detail: t('Keep repetitions controlled and avoid training to failure.', '控制动作，不练至力竭。') },
        ] },
      nutrition: { targets: { kcal: 2400, protein: 140, carbs: 280, fat: 80 },
        summary: t('Fuel a moderate training day.', '为适中强度的训练提供能量。'),
        notes: [t('Include part of your carbohydrate target before training.', '在训练前安排一部分当日碳水目标。'), t('Include protein in your next regular meal after training.', '训练后的下一顿正餐安排蛋白质。')] },
    },
    high: {
      workout: { title: t('Pull workout', '拉类训练'), minutes: 35,
        summary: t('A full session after a better night of sleep.', '昨晚睡眠充足，按完整计划训练。'),
        items: [
          { name: t('Seated cable row', '坐姿绳索划船'), prescription: '3 × 12', detail: t('Use your usual working load; rest 75 seconds between sets.', '使用平时的工作重量，组间休息 75 秒。') },
          { name: t('Lat pulldown', '高位下拉'), prescription: '3 × 12', detail: t('Keep your chest lifted and control the return.', '胸部自然抬起，控制还原。') },
          { name: t('Dumbbell curl', '哑铃弯举'), prescription: '3 × 10', detail: t('Keep your elbows steady. Good readiness does not require a heavier load.', '保持手肘稳定；准备度高不意味着必须加重量。') },
        ] },
      nutrition: { targets: { kcal: 2500, protein: 150, carbs: 295, fat: 80 },
        summary: t('More fuel for the fuller session.', '为完整训练安排更充足的能量。'),
        notes: [t('Distribute carbohydrate intake around the training session.', '围绕训练时段分配碳水摄入。'), t('Spread the protein target across the day; it is a target, not logged intake.', '将蛋白质目标分配到全天；这里是目标，并非已摄入量。')] },
    },
  } satisfies Record<ReadinessDemoMode, DemoPlanContent>
  return { mode, source: 'preset', readings: { ...demoReadings[mode] }, ...example[mode] }
}
