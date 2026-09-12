import {createHash} from 'node:crypto'
import {BuiltInAgent, CopilotRuntime, createCopilotRuntimeHandler} from '@copilotkit/runtime/v2'
import {StatelessRunner} from './runner.js'
import {RunAgentInputSchema, type BaseEvent, type RunAgentInput} from '@ag-ui/client'
import {executeRun} from './agent.js'
import {BusinessRpc} from './rpc.js'
import {RuntimeError, type JsonObject, type LegacyEvent, type OpenReply, type RuntimeOptions} from './contracts.js'

const MAX_BODY = 128 * 1024
const json = (value: unknown, status = 200) => Response.json(value, {status, headers: {'cache-control': 'no-store', vary: 'Cookie', 'x-content-type-options': 'nosniff'}})
const errorResponse = (error: unknown) => json({status: error instanceof RuntimeError && error.status === 409 ? 'conflict' : 'failed', errorCode: error instanceof RuntimeError ? error.code : 'INTERNAL_ERROR'}, error instanceof RuntimeError ? error.status : 500)
const keyFor = (headers: Headers, threadId: string) => createHash('sha256').update(headers.get('cookie') ?? '').update('\0').update(threadId).digest('hex')

async function bodyJson(request: Request): Promise<unknown> {
  if (request.headers.get('content-type')?.split(';')[0].trim().toLowerCase() !== 'application/json') throw new RuntimeError('UNSUPPORTED_MEDIA_TYPE', 415)
  const declared = request.headers.get('content-length')
  if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > MAX_BODY)) throw new RuntimeError('PAYLOAD_TOO_LARGE', 413)
  const reader = request.body?.getReader()
  if (!reader) throw new RuntimeError('INVALID_INPUT', 400)
  const chunks: Uint8Array[] = []
  let length = 0
  try {
    while (true) {
      const {value, done} = await reader.read()
      if (done) break
      length += value.length
      if (length > MAX_BODY) throw new RuntimeError('PAYLOAD_TOO_LARGE', 413)
      chunks.push(value)
    }
  } finally {reader.releaseLock()}
  try {return JSON.parse(Buffer.concat(chunks).toString('utf8'))} catch {throw new RuntimeError('INVALID_INPUT', 400)}
}

function validateInput(value: unknown): RunAgentInput {
  const parsed = RunAgentInputSchema.safeParse(value)
  if (!parsed.success) throw new RuntimeError('INVALID_INPUT', 400)
  const input = parsed.data
  const request = (input.forwardedProps as JsonObject | undefined)?.wellio as JsonObject | undefined
  if (!request || typeof request !== 'object' || Array.isArray(request) || input.tools.length !== 0 || input.threadId !== request.conversationId || input.runId !== request.requestId || input.resume?.length) throw new RuntimeError('INVALID_INPUT', 400)
  return input
}

export function createAgentRuntime(options: RuntimeOptions) {
  process.env.COPILOTKIT_TELEMETRY_DISABLED = 'true'
  const rpc = new BusinessRpc(options.backendUrl, options.token, options.fetch)
  // These entries contain only live cancellation handles, never messages/state.
  const active = new Map<string, {controller: AbortController; runId: string; headers: Headers; done: Promise<unknown>}>()

  async function handleRequest(request: Request): Promise<Response> {
    try {
      const path = new URL(request.url).pathname
      if (path === '/healthz' && request.method === 'GET') return json({status: 'ok', runtime: 'copilotkit-builtin-agent', agent: Boolean(options.model)})
      if (path === '/api/copilotkit/info' && request.method === 'GET') {
        const runner = new StatelessRunner(new AbortController().signal, () => {}, () => {})
        const agent = new BuiltInAgent({type: 'aisdk', factory: () => ({fullStream: (async function* () {})()})})
        const official = new CopilotRuntime({agents: {wellio: agent}, runner})
        const response = await createCopilotRuntimeHandler({runtime: official, basePath: '/api/copilotkit', activateChannels: false})(request)
        response.headers.set('cache-control', 'no-store')
        return response
      }
      const stop = /^\/api\/copilotkit\/agent\/wellio\/stop\/([^/]+)$/.exec(path)
      if (stop && request.method === 'POST') {
        if (!request.headers.get('cookie')) throw new RuntimeError('SESSION_REQUIRED', 401)
        const entry = active.get(keyFor(request.headers, decodeURIComponent(stop[1])))
        if (!entry) return json({stopped: false})
        // This entry was enrolled only after Python authenticated the exact cookie.
        // Stop local work immediately, including when the business service is down.
        entry.controller.abort()
        await Promise.allSettled([rpc.call('cancel', {runId: entry.runId}, request.headers, AbortSignal.timeout(3000)), entry.done])
        return json({stopped: true})
      }
      const proposal = path === '/api/copilotkit/proposal'
      if (!proposal && !['/api/copilotkit/agent/wellio/run', '/agent/wellio/run'].includes(path)) return json({errorCode: 'NOT_FOUND'}, 404)
      if (request.method !== 'POST') return new Response(JSON.stringify({errorCode: 'METHOD_NOT_ALLOWED'}), {status: 405, headers: {allow: 'POST', 'content-type': 'application/json', 'cache-control': 'no-store'}})
      if (!request.headers.get('cookie')) throw new RuntimeError('SESSION_REQUIRED', 401)
      const body = await bodyJson(request)
      const input = proposal ? undefined : validateInput(body)
      if (!options.model) throw new RuntimeError('PROVIDER_NOT_CONFIGURED', 503)
      const opened = await rpc.call<OpenReply>('open', proposal ? {action: body} : {request: (input!.forwardedProps as JsonObject).wellio}, request.headers, AbortSignal.timeout(5000))
      if (!Array.isArray(opened.events)) throw new RuntimeError('AGENT_SERVICE_INVALID_RESPONSE', 502)
      if (proposal && opened.terminal) {
        if (!opened.reply) throw new RuntimeError('AGENT_SERVICE_INVALID_RESPONSE', 502)
        return json(opened.reply.result, opened.reply.httpStatus)
      }
      const identity = input ?? {threadId: opened.request!.conversationId, runId: opened.request!.requestId, messages: [], state: {}, tools: [], context: [], forwardedProps: {}}
      const controller = new AbortController()
      const abort = () => controller.abort()
      request.signal.addEventListener('abort', abort, {once: true})
      if (request.signal.aborted) controller.abort()
      const key = keyFor(request.headers, identity.threadId)
      if (!opened.terminal) active.get(key)?.controller.abort()
      let responseResolve!: (response: Response) => void
      const ready = new Promise<Response>(resolve => {responseResolve = resolve})
      const legacy = (event: LegacyEvent) => {
        runner.emit({type: 'CUSTOM', name: 'wellio', value: event} as BaseEvent)
        if (event.type === 'snapshot') runner.emit({type: 'STATE_SNAPSHOT', snapshot: {wellio: event.snapshot}} as BaseEvent)
      }
      const runner = new StatelessRunner(controller.signal, () => {for (const event of opened.events) legacy(event)}, () => controller.abort())
      let drain: Promise<string> | undefined
      const dispatch = async (agent: BuiltInAgent) => {
        // Official CopilotRuntime owns agent resolution, cloning, dispatch and SSE.
        // The outer boundary removes every untrusted browser state/history/tool.
        const official = new CopilotRuntime({agents: {wellio: agent}, runner})
        const handler = createCopilotRuntimeHandler({runtime: official, basePath: '/api/copilotkit', activateChannels: false})
        const safeInput = {...identity, state: {}, messages: [], tools: [], context: [], forwardedProps: {}}
        const dispatched = new Request(new URL('/api/copilotkit/agent/wellio/run', request.url), {method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(safeInput)})
        const response = await handler(dispatched)
        response.headers.set('cache-control', 'no-store')
        response.headers.set('vary', 'Cookie')
        if (proposal) drain = response.text()
        else responseResolve(response)
        if (!response.ok) throw new RuntimeError('RUNTIME_DISPATCH_FAILED', 502)
        await runner.result
      }
      const running = (async () => {
        try {
          if (opened.terminal) {
            await dispatch(new BuiltInAgent({type: 'aisdk', factory: () => ({fullStream: (async function* () {})()})}))
            return undefined
          }
          return await executeRun(options, rpc, {opened, input: identity, headers: request.headers, signal: controller.signal, onEvent: () => {}, onLegacy: legacy, dispatch})
        } catch (error) {
          const code = error instanceof RuntimeError ? error.code : controller.signal.aborted ? 'RUN_STOPPED' : 'MODEL_ERROR'
          legacy({type: 'error', requestId: identity.runId, resetEpoch: opened.request?.resetEpoch ?? Number(((identity.forwardedProps as JsonObject).wellio as JsonObject)?.resetEpoch), messageId: opened.messageId, errorCode: code})
          if (!controller.signal.aborted) runner.emit({type: 'RUN_ERROR', threadId: identity.threadId, runId: identity.runId, code, message: code} as BaseEvent)
          if (proposal) throw error
          responseResolve(errorResponse(error))
          return undefined
        } finally {
          runner.complete()
          if (active.get(key)?.controller === controller) active.delete(key)
          request.signal.removeEventListener('abort', abort)
        }
      })()
      if (!opened.terminal) active.set(key, {controller, runId: opened.runId!, headers: request.headers, done: running})
      if (proposal) {
        const reply = await running
        await drain
        if (!reply) throw new RuntimeError('AGENT_SERVICE_INVALID_RESPONSE', 502)
        return json(reply.result, reply.httpStatus)
      }
      const response = await ready
      if (!response.body || !response.ok) return response
      // A reader cancellation must also stop the SDK; official SSE encoding stays
      // intact, while business finalization uses its independent bounded signal.
      const source = response.body.getReader()
      const responseBody = new ReadableStream<Uint8Array>({
        async pull(destination) {const {value, done} = await source.read(); if (done) destination.close(); else destination.enqueue(value)},
        async cancel() {controller.abort(); await source.cancel()},
      })
      return new Response(responseBody, {status: response.status, headers: response.headers})

    } catch (error) {return errorResponse(error)}
  }
  return {
    handleRequest,
    async close() {
      const runs = [...active.values()]
      for (const entry of runs) entry.controller.abort()
      await Promise.allSettled(runs.map(entry => entry.done))
      active.clear()
    },
  }
}
export type AgentRuntime = ReturnType<typeof createAgentRuntime>
