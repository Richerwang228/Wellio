import {MockLanguageModelV3} from 'ai/test'
import {simulateReadableStream} from 'ai'
import type {LanguageModelV3CallOptions, LanguageModelV3StreamPart, LanguageModelV3StreamResult} from '@ai-sdk/provider'

export const answer = {markdown: 'Your saved day context is available. Review the current plan and recorded meals.', trainingSummary: 'The saved workout remains unchanged.', nutritionSummary: 'The saved meal log remains unchanged.'}
const usage = {inputTokens: {total: 12, noCache: 12, cacheRead: undefined, cacheWrite: undefined}, outputTokens: {total: 20, text: 20, reasoning: undefined}}
export const streamed = (chunks: LanguageModelV3StreamPart[]): LanguageModelV3StreamResult => ({stream: simulateReadableStream({chunks, initialDelayInMs: 0, chunkDelayInMs: 0})})
export const toolCall = (name = 'get_day_context', input: unknown = {}, id = name + '-call') => streamed([
  {type: 'stream-start', warnings: []}, {type: 'tool-input-start', id, toolName: name}, {type: 'tool-input-delta', id, delta: JSON.stringify(input)},
  {type: 'tool-input-end', id}, {type: 'tool-call', toolCallId: id, toolName: name, input: JSON.stringify(input)}, {type: 'finish', finishReason: {unified: 'tool-calls', raw: undefined}, usage},
])
export const output = (value: unknown = answer) => streamed([
  {type: 'stream-start', warnings: []}, {type: 'text-start', id: 'raw-json'}, {type: 'text-delta', id: 'raw-json', delta: JSON.stringify(value)}, {type: 'text-end', id: 'raw-json'},
  {type: 'finish', finishReason: {unified: 'stop', raw: undefined}, usage},
])
export const markdownOutput = (text: string) => streamed([
  {type: 'stream-start', warnings: []}, {type: 'text-start', id: 'markdown'}, {type: 'text-delta', id: 'markdown', delta: text}, {type: 'text-end', id: 'markdown'},
  {type: 'finish', finishReason: {unified: 'stop', raw: undefined}, usage},
])
export type ModelStep = (options: LanguageModelV3CallOptions) => LanguageModelV3StreamResult | Promise<LanguageModelV3StreamResult>
export function scriptedModel(steps: ModelStep[]) {
  let next = 0
  return new MockLanguageModelV3({provider: 'wellio-offline', modelId: 'sdk6-fixture', doStream: options => {
    const step = steps[next++]
    if (!step) throw new Error('Unexpected provider call')
    return step(options)
  }})
}
export function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  const promise = new Promise<T>(yes => {resolve = yes})
  return {promise, resolve}
}
export function gatedStep() {
  const entered = deferred<LanguageModelV3CallOptions>()
  const released = deferred<LanguageModelV3StreamResult>()
  const step: ModelStep = options => {
    entered.resolve(options)
    return new Promise((resolve, reject) => {
      const abort = () => reject(options.abortSignal?.reason ?? new Error('aborted'))
      if (options.abortSignal?.aborted) {abort(); return}
      options.abortSignal?.addEventListener('abort', abort, {once: true})
      void released.promise.then(resolve, reject).finally(() => options.abortSignal?.removeEventListener('abort', abort))
    })
  }
  return {step, entered: entered.promise, release: released.resolve}
}
