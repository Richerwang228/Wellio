import {createOpenRouter} from '@openrouter/ai-sdk-provider'
import {defaultSettingsMiddleware, wrapLanguageModel, type LanguageModel} from 'ai'

export function configuredModel(environment: NodeJS.ProcessEnv = process.env, transport?: typeof fetch): LanguageModel | undefined {
  const name = environment.WELLIO_AI_MODEL?.trim() || 'deepseek/deepseek-v4.1-flash'
  const key = environment.OPENROUTER_API_KEY?.trim()
  if (!key || /[\r\n]/.test(key)) return undefined
  const provider = createOpenRouter({apiKey: key, baseURL: 'https://openrouter.ai/api/v1', ...(transport ? {fetch: transport} : {})})
  return wrapLanguageModel({model: provider.chat(name, {reasoning: {enabled: false, effort: 'none'}, provider: {sort: 'latency', allow_fallbacks: true}}), middleware: defaultSettingsMiddleware({settings: {
    maxOutputTokens: 4096,
  }})})
}
