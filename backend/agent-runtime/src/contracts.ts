import type {LanguageModel, ModelMessage} from 'ai'

export type JsonObject = Record<string, unknown>
export interface ChatRequest extends JsonObject {requestId: string; resetEpoch: number; conversationId: string; source: 'user' | 'app_open'; message: string; locale: 'en' | 'zh-CN'; attachmentIds: string[]}
export interface LegacyEvent extends JsonObject {type: string; requestId: string; resetEpoch: number}
export interface StoredReply {httpStatus: number; result: JsonObject}
export interface OpenReply {
  source?: 'user' | 'app_open' | 'ui_proposal';
  terminal?: boolean; events: LegacyEvent[]; reply?: StoredReply;
  runId?: string; messageId?: string; request?: ChatRequest; contextRequired?: boolean;
  preparedIntent?: {kind: string; constraint?: {scope?: string; maxMinutes?: number; mealId?: string; mealItemId?: string; changes?: {consumedFraction?: number}}}; leaseExpiresAt?: number; instructions?: string; tools?: Record<string, JsonObject>; messages?: ModelMessage[];
  knowledgeEnabled?: boolean; knowledgeRequired?: boolean;
  knowledgePolicy?: {required: boolean; reason: string};
  knowledgeUnavailable?: {errorCode: string; markdown: string} | null;
  attachments?: {mediaType: string; data: string}[];
}
export interface ToolReply {result: unknown; events: LegacyEvent[]; contextRequired: boolean; knowledgeRequired?: boolean; knowledgeUnavailable?: {errorCode: string; markdown: string} | null}
export interface FinishReply {events: LegacyEvent[]; reply?: StoredReply; output?: {markdown: string}}
export interface RuntimeOptions {
  backendUrl: string; token: string; model?: LanguageModel;
  fetch?: typeof globalThis.fetch; timeoutMs?: number; maxSteps?: number;
}
export class RuntimeError extends Error {
  constructor(readonly code: string, readonly status = 500) {super(code); this.name = 'RuntimeError'}
}
