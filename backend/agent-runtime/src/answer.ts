import {parsePartialJson} from 'ai'
import {z} from 'zod'

const summary = z.string().trim().min(1).max(2000).nullable().default(null)
export const answerSchema = z.object({
  markdown: z.string().trim().min(1).max(12000).refine(value => !/^[\s.…)\]_-]*$/.test(value), 'A substantive answer is required'),
  trainingSummary: summary,
  nutritionSummary: summary,
  evidenceReadId: z.string().min(1).optional(),
  evidenceChunkIds: z.array(z.string().min(1)).min(1).max(4).optional(),
}).strict()

export interface Evidence {evidenceReadId: string; results: {chunkId: string}[]}
export function withoutFence(text: string) {
  const trimmed = text.trim()
  return /^```(?:json)?\s*\n([\s\S]*?)\n?```$/i.exec(trimmed)?.[1]?.trim() ?? trimmed
}

/** Defaults affect presentation only. Explicit invalid evidence is never repaired. */
export function parseAnswer(text: string, evidence?: Evidence) {
  const raw = JSON.parse(withoutFence(text))
  if (!raw || typeof raw !== 'object' || Array.isArray(raw) || typeof raw.markdown !== 'string' || !raw.markdown.trim() || /^[\s.…)\]_-]*$/.test(raw.markdown)) throw new Error('INVALID_MARKDOWN')
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    if (Object.hasOwn(raw, 'evidenceReadId') && (!evidence || raw.evidenceReadId !== evidence.evidenceReadId)) throw new Error('INVALID_EVIDENCE')
    if (Object.hasOwn(raw, 'evidenceChunkIds') && (!evidence || !Array.isArray(raw.evidenceChunkIds) || raw.evidenceChunkIds.length < 1 || raw.evidenceChunkIds.length > 4 || !raw.evidenceChunkIds.every((id: unknown) => typeof id === 'string' && evidence.results.some(item => item.chunkId === id)))) throw new Error('INVALID_EVIDENCE')
  }
  // Optional card rendering must not discard an otherwise valid chat answer.
  for (const key of ['trainingSummary', 'nutritionSummary']) {
    if (typeof raw[key] !== 'string' || !raw[key].trim() || raw[key].length > 2000) raw[key] = null
  }
  const value = answerSchema.parse(Object.fromEntries(Object.entries(raw).filter(([key]) => ['markdown', 'trainingSummary', 'nutritionSummary', 'evidenceReadId', 'evidenceChunkIds'].includes(key))))
  if (evidence) {
    value.evidenceReadId ??= evidence.evidenceReadId
    value.evidenceChunkIds ??= evidence.results.slice(0, 4).map(item => item.chunkId)
    if (value.evidenceReadId !== evidence.evidenceReadId || !value.evidenceChunkIds.every(id => evidence.results.some(item => item.chunkId === id))) {
      throw new Error('INVALID_EVIDENCE')
    }
  }
  return value
}

/** Decode only the public markdown field; JSON, reasoning and arguments stay private. */
export async function partialMarkdown(text: string): Promise<string> {
  const raw = text.replace(/^\s*```(?:json)?\s*\n/i, '')
  if (!raw.trimStart().startsWith('{')) return ''
  const {value} = await parsePartialJson(raw)
  if (!value || typeof value !== 'object' || Array.isArray(value)) return ''
  const markdown = value.markdown
  return typeof markdown === 'string' && !/^[\s.…)\]_-]*$/.test(markdown) ? markdown.slice(0, 12000) : ''
}
