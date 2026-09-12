/** Shared client/server contract. UI never derives persisted facts from Markdown. */
export type Locale = 'en' | 'zh-CN'
export type LocalizedText = { en: string; 'zh-CN': string }
export type Scenario = 'normal' | 'low_recovery'
export type Quality = 'valid' | 'missing' | 'stale' | 'failed'
export type LoadBasis = 'per_hand' | 'machine_stack' | 'bodyweight'
export type EquipmentStatus = 'available' | 'temporarily_occupied' | 'unavailable'
export interface Nutrients { kcal: number; protein: number; carbs: number; fat: number }
export interface DailyNutritionTotals { consumed: Nutrients | null; remaining: Nutrients | null; expenditure: number; energyDeficit: number | null; mealCount: number; estimated: boolean; intakeStatus: 'recorded_so_far' | 'missing' }
export interface MealNutritionTotals { mealId: string; total: Nutrients; items: { mealItemId: string; total: Nutrients }[] }
export interface Readiness {
  id: string; version: number; dayKey: string; quality: Quality; score: number | null; scoreScale: number;
  guidanceHint: 'keep_plan' | 'consider_rest' | null; observedAt: string;
  restingHeartRate: number | null; baselineHeartRate: number; baselineSleepMinutes: number;
  source: 'mock_watch'; errorCode?: string; reasons?: string[]; sleepRecordId?: string;
  /** Persisted synthetic watch inputs for the live demonstration. */
  demoMode?: 'low' | 'balanced' | 'high'; hrvMs?: number;
}
export interface SleepRecord { id: string; minutes: number; bedtime: string; wakeTime: string; source: 'mock_watch'; deepSleepPercent?: number }
export interface SuggestedLoad { value: number | null; unit: 'kg'; basis: LoadBasis; source: 'mock_history' | 'user' | 'missing'; sourceHistoryId?: string; sourceMessageId?: string; reason: LocalizedText }
export interface Exercise {
  id: string; catalogId: string; name: LocalizedText; equipmentId: string; equipment: LocalizedText;
  sets: number; reps: number; restSeconds: number; suggestedLoad: SuggestedLoad; completed: boolean;
  instructions: LocalizedText; animation?: 'row' | 'pulldown' | 'lateral' | 'squat'; replacesId?: string; equipmentStatus?: EquipmentStatus;
}
export interface Workout {
  id: string; trainingSessionId: string; version: number; dayKey: string | null; name: LocalizedText;
  gymId: 'gym-a' | 'gym-b'; estimatedMinutes: number; status: 'planned' | 'in_progress' | 'completed';
  exercises: Exercise[]; startedAt?: string; endedAt?: string; actualMinutes?: number; source?: 'demo_preset' | 'agent_proposal';
}
export interface PlannedSession { id: string; split: 'Pull' | 'Legs' | 'Push'; templateRef: string; slotId: string | null; date: string | null; workoutId?: string; status: 'pending' | 'in_progress' | 'completed' }
export interface TrainingPlan { id: string; version: number; pendingSessionIds: string[]; sessions: PlannedSession[]; availableSlots: {id: string; date: string}[]; restDates: string[] }
export interface ContextVersions { meal: number; plan: number; workout: number; conditions: number; readiness: number }
export interface Proposal {
  id: string; scope: 'workout' | 'schedule'; status: 'pending' | 'applied' | 'dismissed' | 'stale';
  reason: LocalizedText; expected: ContextVersions; contextReadId: string; readinessSnapshotId: string;
  workout?: Workout; moves?: {sessionId: string; split: 'Pull' | 'Legs' | 'Push'; from: string | null; to: string | null}[];
  restDate?: string; messageId?: string; runId?: string; resetEpoch?: number; unassignedSessionIds?: string[]; checkKey?: string; checkAttemptId?: string;
}
export interface MealItem { id: string; name: LocalizedText; portion: LocalizedText; base: Nutrients; consumedFraction: number; estimated: boolean; originalPortion?: { quantity: number; unit: 'g' | 'ml' | 'piece' | 'serving' }; nutrientUnits?: { energy: 'kcal'; mass: 'g' } }
export interface Meal { id: string; version: number; period: 'breakfast' | 'lunch' | 'dinner' | 'snack'; time: string; items: MealItem[]; operationId?: string }
export interface History {
  weight: { date: string; kg: number }[];
  load: { id: string; date: string; exerciseId: string; name: LocalizedText; equipmentId: string; kg: number; basis: LoadBasis; source: 'mock_history'; sets?: number; reps?: number }[];
  training: { date: string; type: string; minutes: number; workoutId?: string; trainingSessionId?: string }[];
  nutrition: ({date: string; expenditure: number} & Nutrients)[];
}
export type OperationType = 'context' | 'history' | 'equipment' | 'knowledge_search' | 'menu_search' | 'meal_add' | 'meal_update' | 'meal_delete' | 'meal_undo' | 'workout_proposal' | 'workout_progress'
export interface ToolStep { id: string; toolCallId: string; operation: OperationType; status: 'started' | 'succeeded' | 'failed' | 'awaiting_user'; errorCode?: string }
export interface Message {
  id: string; role: 'user' | 'assistant'; content: string | LocalizedText; createdAt: string;
  source: 'user' | 'app_open' | 'agent' | 'fixture'; status: 'streaming' | 'complete' | 'failed' | 'stopped';
  steps: ToolStep[]; phase?: 'thinking' | 'recognizing'; proposalId?: string; operationId?: string;
  mealId?: string; attachmentUrl?: string; errorCode?: string;
}
export interface Advice {
  status: 'pending' | 'valid' | 'stale' | 'failed' | 'unavailable'; training?: LocalizedText; nutrition?: LocalizedText;
  messageId?: string; contextReadId?: string; versions?: ContextVersions; errorCode?: string;
}
export interface ReadinessCheck {
  key: string;
  status: 'idle' | 'pending' | 'completed' | 'failed' | 'stopped' | 'applied' | 'dismissed' | 'unavailable';
  messageId?: string; proposalId?: string; errorCode?: string;
}
export interface Snapshot {
  /** Server schema version; optional for compatibility with the isolated visual preview. */
  schemaVersion?: number;
  sessionId: string; resetEpoch: number; revision: number; conversationId: string; dayKey: string; timeZone: string;
  locale: Locale; scenario: Scenario; profile: {name: string; goal: 'muscle_gain'; targets: Nutrients; dislikes: LocalizedText; dinnerBudget: number; expenditure: number};
  readiness: Readiness; sleep: SleepRecord | null; meals: Meal[]; workout: Workout | null; plan: TrainingPlan;
  proposals: Proposal[]; messages: Message[]; history: History; advice: Advice;
  /** Available when the backend supports the readiness check ledger. */
  readinessCheck?: ReadinessCheck;
  conditions: { version: number; gymId: 'gym-a' | 'gym-b'; availableMinutes: number; dinnerBudget: number; equipmentStatus?: Record<string, EquipmentStatus>; lastChange?: { sourceMessageId: string; requestId: string; version: number } };
  mealRevision: number; capabilities: {agent: boolean; menuSearch: boolean; persistence: 'server' | 'preview'};
}
export type ActionInput =
 | {kind: 'start_workout'; workoutId: string; expectedWorkoutVersion: number}
 | {kind: 'complete_exercise' | 'undo_exercise'; workoutId: string; exerciseId: string; expectedWorkoutVersion: number}
 | {kind: 'finish_workout'; workoutId: string; actualMinutes: number; expectedWorkoutVersion: number; confirmIncomplete: boolean}
 | {kind: 'apply_proposal'; proposalId: string; startAfterApply: boolean}
 | {kind: 'dismiss_proposal'; proposalId: string}
 | {kind: 'undo_meal'; operationId: string}
 | {kind: 'set_locale'; locale: Locale}
 | {kind: 'reset_demo'; scenario: Scenario}
 | {kind: 'set_readiness_demo'; mode: 'low' | 'balanced' | 'high'}
 | {kind: 'check_readiness'; retry?: boolean}
 | {kind: 'request_proposal'; gymId?: 'gym-a' | 'gym-b'}
export type ActionRequest = ActionInput & {requestId: string; resetEpoch: number; source: 'today' | 'agent' | 'workout' | 'profile' | 'app_open'}
export interface ActionResult { requestId: string; resetEpoch?: number; status: 'succeeded' | 'needs_input' | 'conflict' | 'failed'; snapshot?: Snapshot; errorCode?: string; operationId?: string; messageId?: string; nutrition?: { meal: MealNutritionTotals | null; day: DailyNutritionTotals }; proposalId?: string; applyStatus?: 'succeeded' | 'failed'; startStatus?: 'succeeded' | 'failed' | 'not_requested' | 'not_started' }
export interface Attachment { id: string; url: string; name: string; mediaType: string; purpose: 'food' | 'menu' }
export interface ChatRequest { requestId: string; resetEpoch: number; conversationId: string; message: string; locale: Locale; attachmentIds: string[]; purpose?: 'food' | 'menu'; targetMealId?: string; targetMealItemId?: string; targetWorkoutId?: string; targetExerciseId?: string; targetOperationId?: string; source: 'user' | 'app_open'; checkMode?: 'auto' | 'retry' }
export type ChatEvent =
 | {type: 'message'; requestId: string; resetEpoch: number; message: Message}
 | {type: 'phase'; requestId: string; resetEpoch: number; messageId: string; phase: 'thinking' | 'recognizing'}
 | {type: 'tool'; requestId: string; resetEpoch: number; messageId: string; step: ToolStep}
 | {type: 'text'; requestId: string; resetEpoch: number; messageId: string; delta: string}
 | {type: 'snapshot'; requestId: string; resetEpoch: number; snapshot: Snapshot}
 | {type: 'done'; requestId: string; resetEpoch: number; messageId: string}
 | {type: 'check_result'; requestId: string; resetEpoch: number; checkKey: string; outcome: 'reused' | 'in_progress' | 'not_needed' | 'not_available'}
 | {type: 'error'; requestId: string; resetEpoch: number; messageId?: string; errorCode: string}
export interface WellioClient {
  getSnapshot(signal?: AbortSignal): Promise<Snapshot>;
  action(request: ActionRequest, signal?: AbortSignal): Promise<ActionResult>;
  chat(request: ChatRequest, onEvent: (event: ChatEvent) => void, signal: AbortSignal): Promise<void>;
  upload(file: File, purpose: 'food' | 'menu', signal?: AbortSignal): Promise<Attachment>;
}
