import {createHash} from 'node:crypto'
import {BuiltInAgent} from '@copilotkit/runtime/v2'
import type {BaseEvent, RunAgentInput} from '@ag-ui/client'
import {generateText, Output, jsonSchema, stepCountIs, streamText, tool, type ModelMessage, type ToolSet} from 'ai'
import {BusinessRpc} from './rpc.js'
import {answerSchema, parseAnswer, partialMarkdown, type Evidence} from './answer.js'
import {PROMPT_VERSION, SYSTEM_PROMPT, TOOL_DESCRIPTIONS} from './prompt.js'
import {RuntimeError, type FinishReply, type JsonObject, type LegacyEvent, type OpenReply, type RuntimeOptions, type StoredReply, type ToolReply} from './contracts.js'

const OPERATIONS: Record<string, string> = {get_day_context: 'context', search_expert_knowledge: 'knowledge_search', get_gym_equipment: 'equipment', query_history: 'history', search_restaurant_menu: 'menu_search', undo_meal_change: 'meal_undo', propose_workout: 'workout_proposal', record_workout_progress: 'workout_progress'}
const TOOL_NAMES = new Set([...Object.keys(OPERATIONS), 'mutate_meal_log'])
const automaticTools = new Set(['get_day_context', 'search_expert_knowledge'])
function modelMessages(opened: OpenReply): ModelMessage[] {
  // Automatic checks and verified UI actions have their own current event.
  // Old conversational requests are not new instructions for those events.
  const source = opened.source ?? opened.request?.source
  const messages = structuredClone(source === 'app_open' || source === 'ui_proposal' ? (opened.messages ?? []).slice(-1) : opened.messages ?? [])
  if (opened.attachments?.length) {
    const last = messages.at(-1)
    if (!last || last.role !== 'user') throw new RuntimeError('AGENT_SERVICE_INVALID_RESPONSE', 502)
    last.content = [{type: 'text', text: typeof last.content === 'string' ? last.content : opened.request!.message},
      ...opened.attachments.map(a => ({type: 'image' as const, image: Buffer.from(a.data, 'base64'), mediaType: a.mediaType}))]
  }
  return messages
}
function readMessages(name: string, toolCallId: string, input: JsonObject, result: unknown): ModelMessage[] {
  return [
    {role: 'assistant', content: [{type: 'tool-call', toolCallId, toolName: name, input}]},
    {role: 'tool', content: [{type: 'tool-result', toolCallId, toolName: name, output: {type: 'json', value: result as any}}]},
  ]
}
/** Retain business receipts, but replace stale read results with just the latest pair. */
function withoutOldReads(messages: ModelMessage[]): ModelMessage[] {
  return messages.flatMap(message => {
    if ((message.role !== 'assistant' && message.role !== 'tool') || typeof message.content === 'string') return [message]
    const content = message.content.filter(part => !('toolName' in part && automaticTools.has(part.toolName)))
    return content.length ? [{...message, content} as ModelMessage] : []
  })
}
export interface ExecuteRun {
  opened: OpenReply; input: RunAgentInput; headers: Headers; signal: AbortSignal;
  onEvent: (event: BaseEvent) => void; onLegacy: (event: LegacyEvent) => void;
  onAgent?: (agent: BuiltInAgent) => void; dispatch?: (agent: BuiltInAgent) => Promise<void>;
}

export async function executeRun(options: RuntimeOptions, rpc: BusinessRpc, run: ExecuteRun): Promise<StoredReply | undefined> {
  if (!options.model) throw new RuntimeError('PROVIDER_NOT_CONFIGURED', 503)
  const {opened, headers} = run
  if (!opened.runId || !opened.request || !opened.messageId || !opened.tools) throw new RuntimeError('AGENT_SERVICE_INVALID_RESPONSE', 502)
  const backendRunId = opened.runId, request = opened.request
  const source = opened.source ?? request.source
  const eventRun = source === 'app_open' || source === 'ui_proposal'
  const maxSteps = options.maxSteps ?? 8
  let reply: StoredReply | undefined, finished = false, failureCode = 'MODEL_ERROR', stepCount = 0, sequence = 0
  let contextRequired = opened.contextRequired !== false
  const knowledgeEnabled = opened.knowledgeEnabled === true
  let knowledgeRequired = knowledgeEnabled && opened.knowledgeRequired !== false
  let unavailable = opened.knowledgeUnavailable
  let evidence: Evidence | undefined
  let currentContext: ModelMessage[] = [], currentEvidence: ModelMessage[] = []
  const intent = opened.preparedIntent
  const requiredTool = intent?.kind === 'meal' && ['meal_add', 'meal_update', 'meal_delete'].includes(intent.constraint?.scope ?? '') ? 'mutate_meal_log' : intent?.kind === 'undo' ? 'undo_meal_change' : intent?.kind === 'progress' ? 'record_workout_progress' : intent?.kind === 'workout_proposal' || source === 'ui_proposal' ? 'propose_workout' : undefined
  let currentGymId: string | undefined, proposalEquipmentRead = false
  let savedShortProposal = false
  let savedMealChange: 'add' | 'update' | 'delete' | undefined
  const mealReceipt = (limitation?: OpenReply['knowledgeUnavailable']) => JSON.stringify({
    markdown: (request.locale === 'zh-CN'
      ? `${savedMealChange === 'delete' ? '已删除指定的餐食记录' : savedMealChange === 'update' ? '已更新指定的餐食记录' : '已记录这次实际摄入'}，今日摄入已重新计算。${savedMealChange === 'delete' ? '可以使用撤销恢复。' : '可以继续修改或撤销。'}`
      : `${savedMealChange === 'delete' ? 'Removed the selected food record' : savedMealChange === 'update' ? 'Updated the selected food record' : 'Logged this food intake'}. Today's intake has been recalculated. ${savedMealChange === 'delete' ? 'You can undo this change.' : 'You can edit or undo this change.'}`) + (limitation ? `\n\n${limitation.markdown}` : ''),
    trainingSummary: null, nutritionSummary: null,
  })
  let currentDay: {dayKey?: string; readiness?: {quality?: string; dayKey?: string; guidanceHint?: string}; workout?: {status?: string; exercises?: {catalogId: string; equipmentId: string}[]}; plan?: {restDates?: string[]; sessions?: {date?: string; status?: string}[]}; proposals?: {status?: string; scope?: string}[]} = {}
  const canConsiderRest = () => {
    const today = currentDay.plan?.sessions?.filter(session => session.date === currentDay.dayKey) ?? []
    return currentDay.readiness?.quality === 'valid' && currentDay.readiness.dayKey === currentDay.dayKey &&
      currentDay.readiness.guidanceHint === 'consider_rest' && !currentDay.plan?.restDates?.includes(currentDay.dayKey ?? '') &&
      today.length === 1 && today[0].status === 'pending' && (!currentDay.workout || currentDay.workout.status === 'planned')
  }
  const eventInstructions = () => {
    if (!eventRun) return intent?.kind === 'read_only' ? 'This run has read-only fact permissions. There is no meal-write authorization. Never say food was logged, totals changed, or any fact was saved without a succeeded business mutation receipt from this run. If a requested fact write is not authorized, explain that it has not been saved and ask for the missing explicit request; do not simulate its receipt.' : ''
    const pending = currentDay.proposals?.some(proposal => proposal.status === 'pending')
    return `This is the verified ${source} event, not a continuation of old chat requests. The read_only intent forbids changing recorded facts; saving a pending propose_workout candidate is permitted and does not apply it. Never log food, change conditions, start training or record progress in this event. ${source === 'ui_proposal' ? 'The user already clicked Generate adjustment. Create one actual candidate with propose_workout; do not ask which option they want, claim text-only options are saved, or finish before attempting the requested tool.' : 'Give the current readiness check. A normal keep-plan result needs no new proposal.'} ${canConsiderRest() ? 'The current valid readiness has guidanceHint=consider_rest and exactly one unstarted session today. Evaluate the attached sleep/recovery evidence and, when supported, create one scope=schedule rest-and-postponement candidate. This hint is not a clinical threshold. The schedule tool needs only reason, contextReadId and evidence fields: no workout JSON, equipment or load-history reads. The server computes all dates; do not ask the user to choose dates or reduce exercise sets instead.' : 'Preserve started/completed training and already applied rest. For an explicitly requested workout candidate, follow current equipment, time and supported load constraints.'} ${pending ? 'The current context contains a pending proposal; refer only to its actual scope and status.' : 'The current context has no pending proposal. Do not tell the user to Apply the existing saved workout. An unchanged saved workout is already effective and is accessed with the Start workout button.'} Describe a proposal as ready only after propose_workout returns business status succeeded. On needs_input/failed, say no candidate was saved and use the returned reason. Do not invent alternative candidates.`
  }
  const blockedTools = new Set<string>()
  const simpleChat = !opened.attachments?.length && /^(?:你好|您好|嗨|早上好|晚上好|谢谢|多谢|hi|hello|hey|thanks|thank you)[\s!！.。]*$/i.test(request.message.trim())
  const structuredReply = request.source === 'app_open' || opened.source === 'ui_proposal'
  const responsePrompt = structuredReply ? SYSTEM_PROMPT : SYSTEM_PROMPT.replace(/【输出】[\s\S]*?(?=【提交回复前检查】)/, '')
  let requiredToolAttempted = false, draft = ''
  const sendLegacy = (events: LegacyEvent[]) => {for (const event of events) run.onLegacy(event)}
  const envelope = {requestId: request.requestId, resetEpoch: request.resetEpoch, messageId: opened.messageId}
  const budget = Math.max(1, Math.min(options.timeoutMs ?? 115000, (opened.leaseExpiresAt ?? Infinity) - Date.now() - 1000))
  const expiresAt = Date.now() + budget
  // Own a strongly held timer as well as checking the wall clock at every boundary.
  const deadline = new AbortController()
  const timer = setTimeout(() => deadline.abort(new DOMException('Run deadline exceeded', 'TimeoutError')), budget)
  const signal = AbortSignal.any([run.signal, deadline.signal])
  const checkDeadline = () => {
    if (Date.now() >= expiresAt && !deadline.signal.aborted) deadline.abort(new DOMException('Run deadline exceeded', 'TimeoutError'))
    signal.throwIfAborted()
  }
  const publishDraft = (markdown: string) => {
    checkDeadline()
    if (!markdown.startsWith(draft) || markdown.length <= draft.length) return
    sendLegacy([{type: 'text', ...envelope, delta: markdown.slice(draft.length)}])
    draft = markdown
  }
  const invokeTool = async (name: string, input: JsonObject, toolCallId: string): Promise<ToolReply> => {
    checkDeadline()
    const operation = name === 'mutate_meal_log' ? 'meal_' + (['add', 'update', 'delete'].includes(String(input.action)) ? input.action : 'update') : OPERATIONS[name]
    const id = 'tool-' + createHash('sha256').update(backendRunId + '\0' + toolCallId).digest('hex')
    sendLegacy([{type: 'tool', ...envelope, step: {id, toolCallId, operation, status: 'started'}}])
    const result = await rpc.call<ToolReply>('tool', {runId: backendRunId, toolCallId, name, input}, headers, signal)
    checkDeadline()
    contextRequired = result.contextRequired
    knowledgeRequired = knowledgeEnabled && result.knowledgeRequired === true
    unavailable = result.knowledgeUnavailable
    if (name === requiredTool) requiredToolAttempted = true
    if (name === 'get_day_context') {
      currentContext = readMessages(name, toolCallId, input, result.result)
      currentEvidence = []; evidence = undefined
      currentGymId = (result.result as {snapshot?: {conditions?: {gymId?: string}}}).snapshot?.conditions?.gymId
      currentDay = (result.result as {snapshot?: typeof currentDay}).snapshot ?? {}
    }
    const outcome = result.result as {status?: string; result?: {status?: string}}
    if ((outcome.result?.status ?? outcome.status) === 'needs_input') blockedTools.add(name)
    if (name === 'propose_workout' && (outcome.result?.status ?? outcome.status) === 'succeeded') blockedTools.add(name)
    if (name === 'propose_workout' && intent?.kind === 'workout_proposal' && (outcome.result?.status ?? outcome.status) === 'succeeded') savedShortProposal = true
    if (name === 'mutate_meal_log' && intent?.kind === 'meal' && intent.constraint?.scope === `meal_${input.action}` &&
        ['add', 'update', 'delete'].includes(String(input.action)) && (outcome.result?.status ?? outcome.status) === 'succeeded') {
      savedMealChange = input.action as 'add' | 'update' | 'delete'
      blockedTools.add(name)
    }
    if (name === 'search_expert_knowledge') {
      currentEvidence = readMessages(name, toolCallId, input, result.result)
      const value = result.result as Partial<Evidence>
      if (value.evidenceReadId && Array.isArray(value.results)) evidence = value as Evidence
    }
    sendLegacy(result.events.filter(e => !(e.type === 'tool' && (e.step as JsonObject)?.status === 'started')))
    return result
  }
  let toolQueue: Promise<unknown> = Promise.resolve()
  const invoke = (name: string, input: JsonObject, toolCallId: string): Promise<ToolReply> => {
    const work = toolQueue.then(() => invokeTool(name, input, toolCallId))
    toolQueue = work.catch(() => {})
    return work
  }
  const refreshReads = async () => {
    const status = await rpc.call<{active: boolean; contextRequired: boolean; knowledgeRequired?: boolean; knowledgeUnavailable?: OpenReply['knowledgeUnavailable']}>('status', {runId: backendRunId}, headers, signal)
    if (!status.active) throw new RuntimeError('RUN_NOT_ACTIVE', 409)
    contextRequired ||= status.contextRequired
    knowledgeRequired = knowledgeEnabled && status.knowledgeRequired === true
    unavailable = status.knowledgeUnavailable
    if (contextRequired) {
      await invoke('get_day_context', {}, `runtime-context-${sequence++}`)
      if (contextRequired) throw new RuntimeError('CONTEXT_READ_REQUIRED', 409)
    }
    if (knowledgeRequired && !unavailable) {
      const query = eventRun
        ? canConsiderRest() ? '睡眠不足与低恢复时的训练调整、休息和训练顺延；依据已保存营养目标支持恢复，不擅自减少热量。' : '根据当前睡眠与恢复状态维持或调整今天训练，遵循已保存营养目标支持恢复。'
        : request.message.trim().slice(0, 1000) || '根据今天恢复状态，提供训练与营养建议'
      await invoke('search_expert_knowledge', {query}, `runtime-knowledge-${sequence++}`)
      if (knowledgeRequired && !unavailable) throw new RuntimeError('KNOWLEDGE_READ_REQUIRED', 409)
    }
    if ((intent?.kind === 'workout_proposal' || source === 'ui_proposal' && !canConsiderRest()) && !proposalEquipmentRead && !unavailable) {
      if (!currentGymId) throw new RuntimeError('CONTEXT_READ_REQUIRED', 409)
      const equipmentInput = {gymId: currentGymId}, equipmentId = `runtime-equipment-${sequence++}`
      const equipment = await invoke('get_gym_equipment', equipmentInput, equipmentId)
      // Keep the allowed catalog beside the current context for the first proposal.
      currentContext.push(...readMessages('get_gym_equipment', equipmentId, equipmentInput, equipment.result))
      if (currentDay.dayKey && /^\d{4}-\d{2}-\d{2}$/.test(currentDay.dayKey)) {
        const end = new Date(currentDay.dayKey + 'T00:00:00Z')
        const from = new Date(end.getTime() - 30 * 86400000).toISOString().slice(0, 10)
        for (const exercise of currentDay.workout?.exercises ?? []) {
          const input = {metric: 'exercise_load', from, to: currentDay.dayKey, exerciseId: exercise.catalogId, equipmentId: exercise.equipmentId}
          const id = `runtime-history-${sequence++}`
          const history = await invoke('query_history', input, id)
          currentContext.push(...readMessages('query_history', id, input, history.result))
        }
      }
      proposalEquipmentRead = true
    }
  }
  const createTools = (): ToolSet => Object.fromEntries(Object.entries(opened.tools!).filter(([name]) => TOOL_NAMES.has(name)).map(([name, schema]) => {
    // A rest candidate has no exercise payload. Advertise the existing narrow
    // server contract so a readiness check does not plan new weights or dates.
    if (name === 'propose_workout' && eventRun && (source === 'app_open' || canConsiderRest())) {
      const properties: JsonObject = {...schema.properties as JsonObject, scope: {const: 'schedule'}}
      delete properties.workout
      delete properties.keepExerciseIds
      schema = {...schema, properties}
    }
    return [name, tool({
    description: name === 'propose_workout' && intent?.kind === 'workout_proposal'
      ? 'Create the requested shorter workout candidate by selecting keepExerciseIds from the existing workout, in execution order. The server preserves actual exercise identities, completed facts, sets, reps and supported loads and sets the requested time limit. Select fewer unfinished exercises to fit the limit; include all completed ones. Do not send workout JSON, generate replacement IDs or guess weights. Use the current contextReadId and real evidence receipt. The result remains pending until the user clicks Apply.'
      : TOOL_DESCRIPTIONS[name], inputSchema: jsonSchema(schema),
    execute: async (input, {toolCallId}) => (await invoke(name, input as JsonObject, toolCallId)).result,
  })]}))
  const availableTools = (tools: ToolSet) => Object.keys(tools).filter(name => {
    if (automaticTools.has(name)) return false
    if (eventRun && ['mutate_meal_log', 'undo_meal_change', 'record_workout_progress', 'search_restaurant_menu'].includes(name)) return false
    if (source === 'app_open' && (name !== 'propose_workout' || !canConsiderRest() || currentDay.proposals?.some(proposal => proposal.status === 'pending' && proposal.scope === 'schedule'))) return false
    if (source === 'ui_proposal' && canConsiderRest() && ['get_gym_equipment', 'query_history'].includes(name)) return false
    // The server-derived intent already limits writes; omit their large schemas
    // from unrelated turns as well as enforcing that limit in the backend.
    if (intent && name === 'mutate_meal_log' && intent.kind !== 'meal') return false
    if (intent && name === 'undo_meal_change' && intent.kind !== 'undo') return false
    if (intent && name === 'record_workout_progress' && intent.kind !== 'progress') return false
    if (opened.knowledgePolicy?.required === false && name === 'propose_workout') return false
    return true
  })
  const agent = new BuiltInAgent({type: 'aisdk', factory: ({abortSignal}) => ({fullStream: (async function* () {
    const modelSignal = AbortSignal.any([signal, abortSignal])
    try {
      await refreshReads()
      const tools = createTools()
      if (!tools.get_day_context || Object.keys(tools).length !== (knowledgeEnabled ? 9 : 8)) throw new RuntimeError('AGENT_SERVICE_INVALID_RESPONSE', 502)
      let finalText = ''
      const portion = intent?.constraint
      if (intent?.kind === 'meal' && portion?.scope === 'meal_update' && portion.mealId && portion.mealItemId &&
          typeof portion.changes?.consumedFraction === 'number' && Number.isFinite(portion.changes.consumedFraction) &&
          portion.changes.consumedFraction >= 0 && portion.changes.consumedFraction <= 1 && !unavailable) {
        // The verified parser has already resolved the target and exact fraction.
        // Reuse the normal authorized/idempotent tool path, then report its receipt.
        const saved = await invoke('mutate_meal_log', {action: 'update', mealId: portion.mealId, mealItemId: portion.mealItemId,
          changes: {consumedFraction: portion.changes.consumedFraction}}, `runtime-portion-${sequence++}`)
        const receipt = saved.result as {status?: string; result?: {status?: string}}
        if ((receipt.result?.status ?? receipt.status) === 'succeeded') {
          await refreshReads()
          const percentage = Math.round(portion.changes.consumedFraction * 10000) / 100
          finalText = JSON.stringify({markdown: request.locale === 'zh-CN'
            ? `已将这份餐食记录为原份量的 ${percentage}%。今天的摄入总量已重新计算，可以使用撤销恢复。`
            : `Saved this item as ${percentage}% of the original portion. Today's intake totals have been recalculated. You can undo this change.`,
            trainingSummary: null, nutritionSummary: null})
        }
      }
      if (unavailable) finalText = savedMealChange ? mealReceipt(unavailable) : JSON.stringify({markdown: unavailable.markdown, trainingSummary: null, nutritionSummary: null})
      else if (!finalText) {
        const responseSystem = simpleChat ? 'You are Wellio, a friendly fitness companion. Acknowledge this greeting or thanks briefly in the user language. Return the complete reply directly as natural Markdown. Do not return JSON or give unsolicited advice.' : `${responsePrompt}\nPrompt version: ${PROMPT_VERSION}.\n${knowledgeEnabled ? 'Knowledge retrieval is connected. When required, the runtime retrieves evidence directly before your response. Respect applicability and use only the latest evidenceReadId and chunk IDs. Do not invent URLs. The server appends source links.' : 'Current capabilities: knowledge retrieval is not connected. Do not claim retrieved expert knowledge or invent citations.'}\nThe runtime already reads get_day_context and required knowledge before your first step and after state changes. Their latest results are attached. Do not repeat these reads. Avoid historical queries unless necessary; current context and equipment may already contain the needed facts. History ranges include both endpoints, must be 1–31 days, and must end no later than dayKey.\n${structuredReply ? 'Return only JSON: markdown first (complete, concise chat reply), trainingSummary and nutritionSummary (short strings only when updating those cards; otherwise null). Optional evidenceReadId/evidenceChunkIds must match the attached latest evidence.' : 'For this user conversation, return the complete answer directly as natural Markdown, not JSON or card fields. The runtime creates the response envelope and appends verified source links. Do not write evidence IDs or invent links in your answer.'} A fact query or action receipt does not require unsolicited health advice. Aim for a short answer, usually 2–4 sentences; do not duplicate it in summaries. Execute requested actions before claiming success; training proposals still require user Apply. A needs_input tool result requires user clarification: do not retry that action with guesses. For a time-limited proposal, preserve the current training type, gym, completed facts and existing supported sets/reps/load; shorten by selecting fewer unfinished exercises first. Load history for the existing exercises is already attached. Changing sets/reps invalidates the old load evidence; do not guess a new weight. Use null/missing load only to explain a required user confirmation, since such a candidate cannot yet be saved.\nTrusted current-run context: ${opened.instructions ?? ''}`
        const generated = streamText({
          model: options.model!,
          system: responseSystem,
          messages: simpleChat ? [{role: 'user', content: request.message}] : modelMessages(opened), tools,
          // Retry only the provider's initial HTTP call, before a stream/tool
          // result exists. The SDK does not replay our run or executed tools.
          maxOutputTokens: simpleChat ? 256 : 2048, stopWhen: [stepCountIs(maxSteps), () => savedShortProposal || Boolean(savedMealChange)], maxRetries: 1,
          abortSignal: modelSignal, timeout: {stepMs: 45000, chunkMs: 30000},
          prepareStep: async ({messages, stepNumber}) => {
            checkDeadline()
            if (stepNumber > 0) await refreshReads()
            if (unavailable) throw new RuntimeError('KNOWLEDGE_UNAVAILABLE', 503)
            return {system: `${responseSystem}\nCurrent event and receipt rules: ${eventInstructions()}`, messages: simpleChat ? messages : [...withoutOldReads(messages), ...currentContext, ...currentEvidence],
              activeTools: availableTools(tools).filter(name => !blockedTools.has(name)),
              ...(simpleChat ? {activeTools: [], toolChoice: 'none' as const} : {}),
              ...(requiredTool && !requiredToolAttempted ? {activeTools: [requiredTool], toolChoice: {type: 'tool' as const, toolName: requiredTool}} : {})}
          },
          onStepFinish: ({usage, finishReason}) => {stepCount++; console.info('[wellio:model-step]', JSON.stringify({step: stepCount, finishReason, inputTokens: usage.inputTokens, outputTokens: usage.outputTokens, reasoningTokens: usage.reasoningTokens}))},
          onError: ({error}) => {console.warn('[wellio:model-error]', JSON.stringify({name: (error as Error)?.name}))},
        })
        let text = ''
        for await (const part of generated.fullStream) {
          checkDeadline()
          if (part.type === 'error') {
            if (unavailable) break
            throw new RuntimeError('MODEL_ERROR', 502)
          }
          if (part.type === 'abort') throw new RuntimeError(modelSignal.aborted ? 'RUN_STOPPED' : 'TIMEOUT', 499)
          if (part.type === 'start-step') text = ''
          if (part.type === 'text-delta') {
            text += part.text
            if (!savedMealChange && !contextRequired && !knowledgeRequired && (!requiredTool || requiredToolAttempted)) {
              const jsonLike = /^\s*(?:[\[{]|```(?:json)?\s)/i.test(text)
              publishDraft(structuredReply || jsonLike ? await partialMarkdown(text) : text)
            }
          }
        }
        if (savedShortProposal || savedMealChange) await refreshReads()
        // prepareStep can update the receipt while the model stream is running.
        const fallback = unavailable as OpenReply['knowledgeUnavailable']
        // A successful meal mutation needs its real receipt, not another model
        // pass that may mix old conversational quantities with the fresh ledger.
        finalText = savedMealChange ? mealReceipt(fallback) : fallback ? JSON.stringify({markdown: fallback.markdown, trainingSummary: null, nutritionSummary: null})
          : savedShortProposal ? JSON.stringify({markdown: request.locale === 'zh-CN'
            ? `已生成 ${intent?.constraint?.maxMinutes ?? ''} 分钟精简训练候选，保留所选动作原有的组次和已验证负重。请查看候选并点击 Apply 后生效；当前训练计划尚未更改。`
            : `Your ${intent?.constraint?.maxMinutes ?? ''}-minute workout candidate is ready, preserving the selected exercises' existing sets, reps and verified loads. Review it and click Apply to use it. Your current workout is unchanged.`,
            trainingSummary: null, nutritionSummary: null}) : (await generated.text).trim()
      }
      checkDeadline()
      let output: ReturnType<typeof parseAnswer>
      const jsonLike = /^\s*(?:[\[{]|```(?:json)?\s)/i.test(finalText)
      const answerText = !structuredReply && !jsonLike ? JSON.stringify({markdown: finalText, trainingSummary: null, nutritionSummary: null}) : finalText
      try {output = parseAnswer(answerText, unavailable ? undefined : evidence)} catch (error) {
        // Keep invalid evidence and empty/placeholder answers as failures. Only a
        // malformed presentation envelope gets one bounded, tools-free repair.
        if (!finalText || ['INVALID_EVIDENCE', 'INVALID_MARKDOWN'].includes((error as Error).message) || /^\s*(?:\.{2,}|…+)\s*$/.test(finalText) || Date.now() + 1500 >= expiresAt) throw error
        const repaired = await generateText({model: options.model!, maxRetries: 0, maxOutputTokens: 2048,
          abortSignal: AbortSignal.any([modelSignal, AbortSignal.timeout(Math.min(10000, expiresAt - Date.now()))]),
          output: Output.object({schema: answerSchema}),
          system: 'Repair only the JSON presentation envelope. Preserve the existing markdown and any supplied evidence IDs verbatim. Do not add facts or actions. Use null for missing or invalid trainingSummary/nutritionSummary. Return no tools or commentary.', prompt: finalText.slice(0, 16000)})
        output = parseAnswer(JSON.stringify(repaired.output), evidence)
      }
      if (contextRequired) throw new RuntimeError('CONTEXT_READ_REQUIRED', 409)
      if (knowledgeRequired && !unavailable) throw new RuntimeError('KNOWLEDGE_READ_REQUIRED', 409)
      if (requiredTool && !requiredToolAttempted && !unavailable) throw new RuntimeError('REQUESTED_ACTION_NOT_ATTEMPTED', 409)
      if (opened.knowledgePolicy?.required === false && !evidence) {output.trainingSummary = null; output.nutritionSummary = null}
      const result = await rpc.call<FinishReply>('finish', {runId: backendRunId, output}, headers, modelSignal)
      checkDeadline(); finished = true; reply = result.reply
      const markdown = result.output?.markdown ?? output.markdown
      if (markdown.startsWith(draft)) publishDraft(markdown)
      yield {type: 'text-start', id: opened.messageId!}
      yield {type: 'text-delta', id: opened.messageId!, text: markdown}
      yield {type: 'text-end', id: opened.messageId!}
      sendLegacy(result.events.filter(event => event.type !== 'text'))
      yield {type: 'finish', finishReason: 'stop'}
    } catch (error) {
      if (signal.aborted) throw new RuntimeError(run.signal.aborted ? 'RUN_STOPPED' : 'TIMEOUT', 499)
      if (abortSignal.aborted) throw new RuntimeError('RUN_STOPPED', 499)
      if (error instanceof RuntimeError) throw error
      throw new RuntimeError(stepCount >= maxSteps ? 'STEP_LIMIT_EXCEEDED' : 'INVALID_MODEL_OUTPUT', 502)
    }
  })()})})
  agent.threadId = run.input.threadId
  run.onAgent?.(agent)
  let rejectAborted!: (error: RuntimeError) => void
  const aborted = new Promise<never>((_, reject) => {rejectAborted = reject})
  const abort = () => {rejectAborted(new RuntimeError(run.signal.aborted ? 'RUN_STOPPED' : 'TIMEOUT', 499)); agent.abortRun()}
  signal.addEventListener('abort', abort, {once: true})
  if (signal.aborted) abort()
  try {
    const dispatched = run.dispatch ? run.dispatch(agent) : agent.runAgent({runId: run.input.runId, tools: [], context: [], forwardedProps: {}}, {onEvent: ({event}) => run.onEvent(event)})
    await Promise.race([dispatched, aborted])
    if (!finished) throw new RuntimeError(signal.aborted ? 'TIMEOUT' : 'MODEL_ERROR', 502)
    return reply
  } catch (error) {
    failureCode = error instanceof RuntimeError ? error.code : run.signal.aborted ? 'RUN_STOPPED' : signal.aborted ? 'TIMEOUT' : 'MODEL_ERROR'
    throw error instanceof RuntimeError ? error : new RuntimeError(failureCode, 502)
  } finally {
    clearTimeout(timer); signal.removeEventListener('abort', abort)
    if (!finished) {
      const code = ['MODEL_ERROR', 'INVALID_MODEL_OUTPUT', 'TIMEOUT', 'STEP_LIMIT_EXCEEDED', 'RUN_STOPPED'].includes(failureCode) ? failureCode : 'MODEL_ERROR'
      try {sendLegacy((await rpc.cancel(backendRunId, headers, code === 'RUN_STOPPED' ? 'stopped' : 'failed', code)).events)} catch { /* Durable lease recovers an unreachable backend. */ }
    }
  }
}
