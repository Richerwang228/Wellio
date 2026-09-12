import {describe, expect, it, vi} from 'vitest'
import {generateText, Output, stepCountIs, streamText, tool} from 'ai'
import {z} from 'zod'
import {configuredModel} from '../src/model.js'
import {BusinessRpc} from '../src/rpc.js'

const environment = {OPENROUTER_API_KEY: 'test-only-provider-key'}

describe('OpenRouter provider configuration', () => {
  it('executes streamed OpenRouter tool calling and consumes the real tool result on the next provider request', async () => {
    const requests: {headers: Headers; body: any}[] = []
    const expected = {markdown: 'The saved context is available.', trainingSummary: 'Saved training summary.', nutritionSummary: 'Saved nutrition summary.'}
    const transport = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(input, init)
      requests.push({headers: request.headers, body: await request.json()})
      const first = requests.length === 1
      const delta = first ? {role: 'assistant', tool_calls: [{index: 0, id: 'server-context-call', type: 'function', function: {name: 'get_day_context', arguments: '{}'}}]} : {role: 'assistant', content: JSON.stringify(expected)}
      const chunks = [delta, {}].map((value, index) => ({id: `chat-${requests.length}`, object: 'chat.completion.chunk', created: 0, model: 'deepseek/deepseek-v4.1-flash', choices: [{index: 0, delta: value, finish_reason: index === 1 ? first ? 'tool_calls' : 'stop' : null}]}))
      return new Response(chunks.map(chunk => `data: ${JSON.stringify(chunk)}\n\n`).join('') + 'data: [DONE]\n\n', {headers: {'content-type': 'text/event-stream'}})
    }) as typeof fetch
    const execute = vi.fn(async () => ({contextReadId: 'trusted-read', watch: 'CURRENT_SERVER_CONTEXT'}))
    const generated = streamText({
      model: configuredModel(environment, transport)!, prompt: 'Read my day before answering.', maxRetries: 0,
      tools: {get_day_context: tool({inputSchema: z.object({}).strict(), execute})}, stopWhen: stepCountIs(3),
      prepareStep: ({stepNumber}) => stepNumber === 0 ? {toolChoice: {type: 'tool', toolName: 'get_day_context'}} : {},
      output: Output.object({schema: z.object({markdown: z.string(), trainingSummary: z.string(), nutritionSummary: z.string()}).strict()}),
    })
    for await (const part of generated.fullStream) if (part.type === 'error') throw part.error
    expect(await generated.output).toEqual(expected)
    expect(execute).toHaveBeenCalledTimes(1)
    expect(requests).toHaveLength(2)
    expect(requests[0].body.reasoning).toMatchObject({enabled: false, effort: 'none'})
    expect(requests[0].body.max_tokens).toBe(4096)
    expect(requests[0].body.tool_choice).toEqual({type: 'function', function: {name: 'get_day_context'}})
    expect(requests[0].body.tools[0].function.name).toBe('get_day_context')
    expect(requests[1].body.messages).toContainEqual(expect.objectContaining({role: 'tool', tool_call_id: 'server-context-call', content: expect.stringContaining('CURRENT_SERVER_CONTEXT')}))
    expect(requests[1].body.response_format.type).toBe('json_schema')
    expect(JSON.stringify(requests.map(request => request.body))).not.toContain('test-only-provider-key')
  })

  it.each([undefined, 'explicit-model-id'])('uses the actual OpenRouter chat protocol and never retries HTTP 429 (model %s)', async name => {
    const calls: Request[] = []
    const transport = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(input, init)
      calls.push(request)
      return Response.json({error: {message: 'Private rate-limit detail', type: 'rate_limit_error', code: 'rate_limit_exceeded'}}, {status: 429})
    }) as typeof fetch
    const model = configuredModel({...environment, ...(name ? {WELLIO_AI_MODEL: name} : {})}, transport)!
    await expect(generateText({model, prompt: 'Offline protocol request', maxRetries: 0})).rejects.toThrow()
    expect(calls).toHaveLength(1)
    expect(calls[0].url).toBe('https://openrouter.ai/api/v1/chat/completions')
    expect(calls[0].headers.has('Lovable-API-Key')).toBe(false)
    expect(calls[0].headers.get('authorization')).toBe('Bearer test-only-provider-key')
    expect((await calls[0].json()).model).toBe(name ?? 'deepseek/deepseek-v4.1-flash')
  })

  it.each([
    {}, {OPENROUTER_API_KEY: ''}, {OPENROUTER_API_KEY: '   '}, {OPENROUTER_API_KEY: 'bad\nheader'},
    {LOVABLE_API_KEY: 'legacy-key'}, {OPENAI_API_KEY: 'different-provider-key'},
  ])('does not invent missing or invalid provider settings: %j', patch => {
    expect(configuredModel(patch)).toBeUndefined()
  })
})

describe('Fixed internal RPC allowlist and error isolation', () => {
  it('cannot use a model/user path as an RPC target or forward a supplied bearer token', async () => {
    const transport = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(input, init)
      expect(request.url).toBe('http://127.0.0.1:8000/internal/agent/status')
      expect(request.headers.get('authorization')).toBe('Bearer private-server-token')
      expect(request.headers.has('x-user-extra')).toBe(false)
      return Response.json({active: true})
    }) as typeof fetch
    const rpc = new BusinessRpc('http://127.0.0.1:8000', 'private-server-token', transport)
    await expect(rpc.call('../arbitrary-write', {}, new Headers())).rejects.toThrow('INTERNAL_RPC_METHOD_INVALID')
    expect(transport).not.toHaveBeenCalled()
    await rpc.call('status', {runId: 'trusted-run'}, new Headers({authorization: 'Bearer browser-supplied', 'x-user-extra': 'value'}))
    expect(transport).toHaveBeenCalledTimes(1)
  })

  it('does not expose private upstream bodies or transport failures', async () => {
    const rpc = new BusinessRpc('http://127.0.0.1:8000', 'private-server-token', async () => Response.json({message: 'DATABASE_PASSWORD'}, {status: 500}))
    await expect(rpc.call('status', {}, new Headers())).rejects.toThrow('AGENT_SERVICE_ERROR')
    const offline = new BusinessRpc('http://127.0.0.1:8000', 'private-server-token', async () => {throw new Error('SECRET_URL_TOKEN')})
    await expect(offline.call('status', {}, new Headers())).rejects.toThrow('AGENT_SERVICE_UNAVAILABLE')
  })
})
