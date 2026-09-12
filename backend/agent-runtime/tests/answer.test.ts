import {afterEach, describe, expect, it, vi} from 'vitest'
import {parseAnswer, partialMarkdown, type Evidence} from '../src/answer.js'
import {BusinessRpc} from '../src/rpc.js'

const markdown = '今天已记录 1,200 kcal。训练计划仍等待你点击 Apply。'
const evidence: Evidence = {
  evidenceReadId: 'receipt-current',
  results: [{chunkId: 'chunk-a'}, {chunkId: 'chunk-b'}],
}

describe('answer presentation and evidence boundaries', () => {
  it('keeps the complete answer with missing or nullable optional summaries', () => {
    for (const summaries of [{}, {trainingSummary: null, nutritionSummary: null}]) {
      expect(parseAnswer(JSON.stringify({markdown, ...summaries}))).toEqual({
        markdown, trainingSummary: null, nutritionSummary: null,
      })
    }
  })

  it.each([false, 7, {}, [], '', '   ', 'x'.repeat(2001)])(
    'drops an invalid card summary without discarding the answer (%j)', invalid => {
      expect(parseAnswer(JSON.stringify({markdown, trainingSummary: invalid, nutritionSummary: ' 今日摄入已记录。 '})))
        .toEqual({markdown, trainingSummary: null, nutritionSummary: '今日摄入已记录。'})
      expect(parseAnswer(JSON.stringify({markdown, trainingSummary: ' 今日计划已保存。 ', nutritionSummary: invalid})))
        .toEqual({markdown, trainingSummary: '今日计划已保存。', nutritionSummary: null})
    },
  )

  it('accepts fenced JSON without including private envelope fields in the result', () => {
    const value = {markdown, reasoning: 'PRIVATE_REASONING', arguments: {secret: 'PRIVATE_TOOL_ARGUMENT'}}
    expect(parseAnswer('```json\n' + JSON.stringify(value) + '\n```')).toEqual({
      markdown, trainingSummary: null, nutritionSummary: null,
    })
  })

  it.each(['', '   ', '...', '…', '……', '\n... …\t', null, 1, {}])(
    'rejects empty, placeholder, or non-text markdown before repair (%j)', value => {
      expect(() => parseAnswer(JSON.stringify({markdown: value, trainingSummary: null, nutritionSummary: null})))
        .toThrow('INVALID_MARKDOWN')
    },
  )

  it('fills omitted evidence identifiers only from the actual attached receipt', () => {
    expect(parseAnswer(JSON.stringify({markdown}), evidence)).toEqual({
      markdown, trainingSummary: null, nutritionSummary: null,
      evidenceReadId: 'receipt-current', evidenceChunkIds: ['chunk-a', 'chunk-b'],
    })
    expect(parseAnswer(JSON.stringify({markdown, evidenceChunkIds: ['chunk-b']}), evidence)).toMatchObject({
      evidenceReadId: 'receipt-current', evidenceChunkIds: ['chunk-b'],
    })
    expect(parseAnswer(JSON.stringify({markdown, evidenceReadId: 'receipt-current'}), evidence)).toMatchObject({
      evidenceReadId: 'receipt-current', evidenceChunkIds: ['chunk-a', 'chunk-b'],
    })
  })

  it.each([
    {evidenceReadId: ''}, {evidenceReadId: null}, {evidenceReadId: 'receipt-stale'},
    {evidenceChunkIds: []}, {evidenceChunkIds: null}, {evidenceChunkIds: 'chunk-a'},
    {evidenceChunkIds: ['']}, {evidenceChunkIds: ['invented-chunk']},
    {evidenceChunkIds: ['chunk-a', 'invented-chunk']}, {evidenceChunkIds: [7]},
    {evidenceChunkIds: ['chunk-a', 'chunk-b', 'chunk-a', 'chunk-b', 'chunk-a']},
  ])('never replaces explicit invalid evidence with defaults (%j)', supplied => {
    expect(() => parseAnswer(JSON.stringify({markdown, ...supplied}), evidence)).toThrow('INVALID_EVIDENCE')
  })

  it.each([{evidenceReadId: 'receipt-current'}, {evidenceChunkIds: ['chunk-a']}])(
    'rejects citations when no receipt was attached (%j)', supplied => {
      expect(() => parseAnswer(JSON.stringify({markdown, ...supplied}))).toThrow('INVALID_EVIDENCE')
    },
  )

  it('preserves explicit valid citation choices instead of widening them', () => {
    expect(parseAnswer(JSON.stringify({markdown, evidenceReadId: 'receipt-current', evidenceChunkIds: ['chunk-b']}), evidence))
      .toMatchObject({markdown, evidenceReadId: 'receipt-current', evidenceChunkIds: ['chunk-b']})
  })
})

describe('partial markdown streaming', () => {
  it('extracts only decoded markdown across Chinese and escaped token boundaries', async () => {
    const expected = '今天\n训练 "深蹲"，路径 C:\\gym。'
    const wire = '{"reasoning":"PRIVATE_REASONING","markdown":"\\u4eca\\u5929\\n训练 \\"深蹲\\"，路径 C:\\\\gym。","trainingSummary":"PRIVATE_TRAINING","nutritionSummary":"PRIVATE_NUTRITION","arguments":{"secret":"PRIVATE_ARGUMENT"}}'
    for (let boundary = 0; boundary <= wire.length; boundary++) {
      const partial = await partialMarkdown(wire.slice(0, boundary))
      expect(expected.startsWith(partial), `unexpected markdown at wire offset ${boundary}: ${partial}`).toBe(true)
      expect(partial).not.toContain('PRIVATE_')
    }
    expect(await partialMarkdown(wire)).toBe(expected)
  })

  it('handles fenced fragments without revealing the JSON envelope', async () => {
    expect(await partialMarkdown('```json\n{"markdown":"你好，今天')).toBe('你好，今天')
    expect(await partialMarkdown('```json\n' + JSON.stringify({markdown, trainingSummary: 'PRIVATE_CARD'}) + '\n```')).toBe(markdown)
  })

  it.each([
    '', 'Plain provider prose', '[{"markdown":"not the answer"}]',
    '{"reasoning":"PRIVATE_REASONING"}', '{"arguments":{"markdown":"PRIVATE_NESTED_ARGUMENT"}}',
    '{"markdown":7}', '{"markdown":null}', '{"markdown":"..."}',
  ])('does not publish raw prose, private fields, or placeholder values (%s)', async wire => {
    expect(await partialMarkdown(wire)).toBe('')
  })
})

describe('bounded business RPC with non-cooperative transport', () => {
  afterEach(() => {vi.useRealTimers()})

  const headers = new Headers({cookie: 'wellio_session=test-signed-cookie', origin: 'http://frontend.local'})
  const payload = {runId: 'run-current', toolCallId: 'original-idempotency-key', name: 'mutate_meal_log', input: {action: 'add'}}

  function deferred<T>() {
    let resolve!: (value: T) => void
    const promise = new Promise<T>(yes => {resolve = yes})
    return {promise, resolve}
  }

  it.each([{method: 'finish', timeout: 5000}, {method: 'tool', timeout: 20000}])(
    'rejects a stalled $method transport within its own deadline without replay', async ({method, timeout}) => {
      vi.useFakeTimers()
      const transportResult = deferred<Response>()
      const transport = vi.fn<typeof fetch>(() => transportResult.promise)
      const rpc = new BusinessRpc('http://backend.local', 'TEST_INTERNAL_TOKEN', transport)
      const outcome = rpc.call(method, payload, headers).catch(error => error)
      let settled = false
      void outcome.then(() => {settled = true})
      await vi.advanceTimersByTimeAsync(timeout - 1)
      expect(settled).toBe(false)
      await vi.advanceTimersByTimeAsync(1)
      expect(await outcome).toMatchObject({code: 'AGENT_SERVICE_UNAVAILABLE', status: 502})
      expect(transport).toHaveBeenCalledTimes(1)
      expect(transport.mock.calls[0][1]?.body).toBe(JSON.stringify(payload))
      expect(transport.mock.calls[0][1]?.signal?.aborted).toBe(true)
      transportResult.resolve(Response.json({status: 'committed'}))
      await vi.advanceTimersByTimeAsync(timeout)
      expect(transport).toHaveBeenCalledTimes(1)
      expect(vi.getTimerCount()).toBe(0)
    },
  )

  it('bounds stalled response JSON as well as the initial transport', async () => {
    vi.useFakeTimers()
    const body = deferred<unknown>()
    const response = Response.json({})
    const readBody = vi.spyOn(response, 'json').mockReturnValue(body.promise)
    const transport = vi.fn<typeof fetch>(async () => response)
    const rpc = new BusinessRpc('http://backend.local', 'TEST_INTERNAL_TOKEN', transport)
    const outcome = rpc.call('finish', payload, headers).catch(error => error)
    await vi.advanceTimersByTimeAsync(5000)
    expect(await outcome).toMatchObject({code: 'AGENT_SERVICE_UNAVAILABLE', status: 502})
    expect(readBody).toHaveBeenCalledTimes(1)
    expect(transport).toHaveBeenCalledTimes(1)
    expect(transport.mock.calls[0][1]?.body).toBe(JSON.stringify(payload))
    body.resolve({status: 'committed'})
    await vi.advanceTimersByTimeAsync(5000)
    expect(transport).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('propagates caller cancellation immediately even if transport ignores its signal', async () => {
    vi.useFakeTimers()
    const transportResult = deferred<Response>()
    const transport = vi.fn<typeof fetch>(() => transportResult.promise)
    const rpc = new BusinessRpc('http://backend.local', 'TEST_INTERNAL_TOKEN', transport)
    const controller = new AbortController()
    const reason = new DOMException('Caller stopped the run', 'AbortError')
    const outcome = rpc.call('tool', payload, headers, controller.signal).catch(error => error)
    controller.abort(reason)
    expect(await outcome).toBe(reason)
    expect(transport).toHaveBeenCalledTimes(1)
    expect(transport.mock.calls[0][1]?.body).toBe(JSON.stringify(payload))
    expect(vi.getTimerCount()).toBe(0)
    transportResult.resolve(Response.json({status: 'committed'}))
    await vi.advanceTimersByTimeAsync(20000)
    expect(transport).toHaveBeenCalledTimes(1)
  })
})
