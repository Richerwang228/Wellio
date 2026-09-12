import {isDemoMode} from '../../lib/runtime-mode'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useNavigate } from '@tanstack/react-router'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Icon } from '../../components/Icon'
import { Mascot } from '../../components/Mascot'
import { api } from '../../lib/api-client'
import type { ActionInput, Attachment, LocalizedText, Message, OperationType, Proposal, Snapshot, ToolStep } from '../../lib/contracts'
import { errorText } from '../../lib/errors'
import { bilingual, useI18n } from '../../lib/i18n'
import { useWellio } from '../../lib/wellio-context'
import './agent.css'

const operationCopy: Record<OperationType, [string, string]> = {
  context: ['Checking recovery and today’s records', '查看恢复情况与今日记录'],
  history: ['Reviewing recent history', '查看近期记录'],
  equipment: ['Checking available equipment', '查看可用器械'],
  knowledge_search: ['Consulting expert sources', '查阅专业资料'],
  menu_search: isDemoMode() ? ['Reading the sample menu', '查看样例菜单'] : ['Finding the restaurant menu', '查找餐厅菜单'],
  meal_add: ['Saving the meal', '保存餐食记录'],
  meal_update: ['Updating the meal', '修改餐食记录'],
  meal_delete: ['Removing the meal record', '删除餐食记录'],
  meal_undo: ['Undoing the meal change', '撤销餐食修改'],
  workout_proposal: ['Preparing a training suggestion', '准备训练调整建议'],
  workout_progress: ['Saving workout progress', '保存训练进度'],
}

function errorCopy<T>(code: string | undefined, t: (en: string, zh: string) => T): T {
  switch (code) {
    case 'VERSION_CONFLICT': return t('The saved information changed. Refresh before trying again.', '已保存的信息有变化，请刷新后再试。')
    case 'TIMEOUT': return t('This took longer than expected. Check the latest saved state before retrying.', '处理超时，请先查看最新保存状态，再重试。')
    case 'INVALID_INPUT': return t('A detail is missing or needs correcting. Review your message and try again.', '有信息缺失或需要修正，请检查消息后再试。')
    case 'NOT_FOUND': return t('This record is no longer available. Refresh to see the latest information.', '这条记录已不可用，请刷新查看最新信息。')
    case 'NOT_CONFIGURED':
    case 'AGENT_UNAVAILABLE':
    case 'PROVIDER_NOT_CONFIGURED': return t('The Agent connection is not available yet. Your draft is kept here.', 'Agent 连接暂不可用，你的草稿会保留在这里。')
    case 'UPLOAD_FAILED': return t('The image could not be uploaded. It is still here so you can try again.', '图片上传失败，已保留图片，可以重试。')
    default: return t(errorText(code ?? 'PROVIDER_ERROR', 'en'), errorText(code ?? 'PROVIDER_ERROR', 'zh-CN'))
  }
}

function codeFromError(error: unknown): string | undefined {
  if (typeof error === 'object' && error !== null && 'code' in error && typeof error.code === 'string') return error.code
  if (error instanceof Error && /^[A-Z_]+$/.test(error.message)) return error.message
  return undefined
}

/** Only actual toolCallIds count. Duplicate lifecycle updates represent one call. */
export function AgentToolProcess({ steps }: { steps: ToolStep[] }) {
  const { t } = useI18n()
  const [expanded, setExpanded] = useState(false)
  const calls = [...new Map(steps.filter(step => step.toolCallId).map(step => [step.toolCallId, step])).values()]
  if (!calls.length) return null
  const failed = calls.some(step => step.status === 'failed')
  const running = calls.find(step => step.status === 'started')
  const waiting = calls.some(step => step.status === 'awaiting_user')
  const summary = failed ? t('A step needs attention', '有一步未完成')
    : running ? `${t(...operationCopy[running.operation])}…`
      : waiting ? t('Waiting for your input', '等待你补充信息')
        : t(`Completed ${calls.length} ${calls.length === 1 ? 'step' : 'steps'}`, `已完成 ${calls.length} 个步骤`)
  return <details className="agent-process" open={failed || expanded} onToggle={event => {
    if (!failed) setExpanded(event.currentTarget.open)
  }}>
    <summary><Icon name={failed ? 'close' : running ? 'loader' : 'check'} size={16} />
      <span>{summary}</span><Icon name="chevron-down" size={16} className="agent-process-chevron" />
    </summary>
    <ol>{calls.map(step => <li key={step.toolCallId} className={`agent-step agent-step-${step.status}`}>
      <Icon name={step.status === 'succeeded' ? 'check' : step.status === 'failed' ? 'close' : 'clock'} size={15} />
      <div><span>{t(...operationCopy[step.operation])}</span>
        <small>{step.status === 'succeeded' ? t('Completed', '已完成')
          : step.status === 'started' ? t('In progress', '处理中')
            : step.status === 'awaiting_user' ? t('Needs your input', '需要补充信息') : errorCopy(step.errorCode, t)}</small>
      </div>
    </li>)}</ol>
  </details>
}

export function AgentMessageBody({ content }: { content: string }) {
  return <div className="agent-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{
    a: ({ children, href }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
    table: ({ children }) => <div className="agent-table-scroll" tabIndex={0}><table>{children}</table></div>,
  }}>{content}</ReactMarkdown></div>
}

function isProposalCurrent(proposal: Proposal, snapshot: Snapshot) {
  const expected = proposal.expected
  return proposal.readinessSnapshotId === snapshot.readiness.id
    && expected.readiness === snapshot.readiness.version
    && expected.plan === snapshot.plan.version
    && expected.conditions === snapshot.conditions.version
    && expected.meal === snapshot.mealRevision
    && expected.workout === (snapshot.workout?.version ?? 0)
}

interface LocalAttachment { file: File; previewUrl: string; purpose: Attachment['purpose']; uploaded?: Attachment }
interface Feedback { kind: 'success' | 'error' | 'info'; content: LocalizedText }

export function AgentPage() {
  const { snapshot, loading, error, busy, chatBusy, readinessBusy, readinessError, checkReadiness, draft, setDraft, chatTarget, setChatTarget, runAction, sendMessage, stopChat, refresh } = useWellio()
  const { t, text, date } = useI18n()
  const navigate = useNavigate()
  const threadRef = useRef<HTMLDivElement>(null)
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const nearBottom = useRef(true)
  const uploadAbort = useRef<AbortController | null>(null)
  const currentEpoch = useRef(snapshot?.resetEpoch)
  currentEpoch.current = snapshot?.resetEpoch
  const [newReply, setNewReply] = useState(false)
  const [celebrating, setCelebrating] = useState(false)
  const previousReplyStatus = useRef<string | undefined>(undefined)
  const [attachment, setAttachment] = useState<LocalAttachment | null>(null)
  const [uploading, setUploading] = useState(false)
  const [sending, setSending] = useState(false)
  const [composing, setComposing] = useState(false)
  const [pendingAction, setPendingAction] = useState<string | null>(null)
  const [feedback, setFeedback] = useState<Record<string, Feedback>>({})
  const [composerNotice, setComposerNotice] = useState<Feedback | null>(null)
  const messages = snapshot?.messages ?? []
  const lastMessage = messages.at(-1)
  const active = chatBusy || sending || uploading
  const locked = busy || active || pendingAction !== null

  useEffect(() => {
    if (previousReplyStatus.current === 'streaming' && lastMessage?.status === 'complete' && lastMessage?.operationId) setCelebrating(true)
    previousReplyStatus.current = lastMessage?.status
  }, [lastMessage?.id, lastMessage?.status])
  useEffect(() => {
    if (!celebrating) return
    const timer = setTimeout(() => setCelebrating(false), 1300)
    return () => clearTimeout(timer)
  }, [celebrating])

  useEffect(() => () => { uploadAbort.current?.abort() }, [])
  useEffect(() => {
    return () => { if (attachment?.previewUrl) URL.revokeObjectURL(attachment.previewUrl) }
  }, [attachment?.previewUrl])
  useEffect(() => {
    uploadAbort.current?.abort()
    setAttachment(null)
    setFeedback({})
    setComposerNotice(null)
    setNewReply(false)
    nearBottom.current = true
  }, [snapshot?.resetEpoch])
  useEffect(() => {
    const thread = threadRef.current
    if (!thread) return
    if (nearBottom.current) {
      const frame = requestAnimationFrame(() => { thread.scrollTop = thread.scrollHeight; setNewReply(false) })
      return () => cancelAnimationFrame(frame)
    } else setNewReply(true)
  }, [messages.length, lastMessage?.content, lastMessage?.status, lastMessage?.steps, lastMessage?.phase, feedback])
  useEffect(() => {
    const input = composerRef.current
    if (!input) return
    input.style.height = 'auto'
    input.style.height = `${Math.min(input.scrollHeight, 136)}px`
  }, [draft])

  function prepareDraft(value: string, target?: { mealId?: string; workoutId?: string; exerciseId?: string }) {
    setDraft(value)
    setChatTarget(target ?? {})
    setComposerNotice(null)
    composerRef.current?.focus()
  }

  async function execute(messageId: string, input: ActionInput) {
    if (locked) return
    setPendingAction(messageId)
    setFeedback(previous => { const next = { ...previous }; delete next[messageId]; return next })
    const epoch = currentEpoch.current
    try {
      const result = await runAction(input, 'agent')
      if (epoch !== currentEpoch.current) return
      if (result?.status === 'succeeded') {
        if (input.kind === 'apply_proposal') setCelebrating(true)
        const content = input.kind === 'undo_meal' ? bilingual('This change was undone.', '这次修改已撤销。')
          : input.kind === 'dismiss_proposal' ? bilingual('Original plan kept.', '已保留原计划。')
            : bilingual('Changes saved.', '调整已保存。')
        setFeedback(previous => ({ ...previous, [messageId]: { kind: 'success', content } }))
      } else {
        setFeedback(previous => ({ ...previous, [messageId]: { kind: 'error', content: errorCopy(result?.errorCode, bilingual) } }))
      }
    } catch (cause) {
      if (epoch === currentEpoch.current) setFeedback(previous => ({ ...previous, [messageId]: { kind: 'error', content: errorCopy(codeFromError(cause), bilingual) } }))
    } finally { setPendingAction(null) }
  }

  async function prepareRetry(message: Message) {
    if (locked) return
    if (message.source === 'app_open') {
      await checkReadiness({ mode: 'retry' }).catch(() => {})
      return
    }
    const index = messages.findIndex(item => item.id === message.id)
    const original = messages.slice(0, index + 1).reverse().find(item => item.role === 'user')
    if (!await refresh()) {
      setComposerNotice({ kind: 'error', content: bilingual('Could not check saved records. Try refreshing before resending.', '未能核对已保存记录，请刷新后再发送。') })
      return
    }
    if (original) {
      prepareDraft(text(original.content), message.mealId ? { mealId: message.mealId } : undefined)
      setComposerNotice({ kind: 'info', content: original.attachmentUrl
        ? bilingual('Review the saved records and add the image again before sending.', '请核对已保存记录，重新添加图片后再发送。')
        : bilingual('Your request is ready to review. Saved changes are kept; resend only what is still needed.', '原请求已放入输入框。已保存修改会保留，请只发送仍需处理的内容。') })
    } else setComposerNotice({ kind: 'info', content: bilingual('Tell me what you would like to try again.', '请告诉我你想重新处理什么。') })
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (locked || (!draft.trim() && !attachment) || !snapshot?.capabilities.agent) return
    const epoch = currentEpoch.current
    const value = draft.trim() || (attachment?.purpose === 'menu'
      ? t('What would you recommend from this menu?', '帮我看看这份菜单，推荐一餐。')
      : t('Please log this meal.', '帮我记录这餐。'))
    setComposerNotice(null)
    setSending(true)
    nearBottom.current = true
    try {
      let uploaded = attachment?.uploaded
      if (attachment && (!uploaded || uploaded.purpose !== attachment.purpose)) {
        setUploading(true)
        uploadAbort.current = new AbortController()
        uploaded = await api.upload(attachment.file, attachment.purpose, uploadAbort.current.signal)
        if (epoch !== currentEpoch.current) return
        setAttachment(previous => previous ? { ...previous, uploaded } : previous)
        setUploading(false)
      }
      await sendMessage(value, uploaded ? [uploaded] : undefined)
      if (epoch === currentEpoch.current) {
        setAttachment(null)
        setDraft('')
        setChatTarget({})
      }
    } catch (cause) {
      if (epoch === currentEpoch.current && !(cause instanceof Error && cause.name === 'AbortError')) {
        setComposerNotice({ kind: 'error', content: errorCopy(codeFromError(cause), bilingual) })
      }
    } finally { setUploading(false); setSending(false) }
  }

  function messageActions(message: Message) {
    const proposal = snapshot?.proposals.find(item => item.id === message.proposalId)
    const stale = proposal?.status === 'stale' || (proposal?.status === 'pending' && snapshot && !isProposalCurrent(proposal, snapshot))
    const actionFeedback = feedback[message.id]
    const isPending = pendingAction === message.id
    return <>
      {message.proposalId && !proposal && <p className="agent-status">{t('This suggestion is no longer available. Ask for an updated plan.', '这份建议已不可用，请重新获取方案。')}</p>}
      {proposal?.status === 'pending' && !stale && <div className="agent-proposal-preview">
        <p className="agent-proposal-label">{t('Proposed changes · Not applied', '调整预览 · 尚未应用')}</p>
        {proposal.scope === 'workout' && proposal.workout ? <dl>
          {(!snapshot?.workout || text(snapshot.workout.name) !== text(proposal.workout.name)) && <div><dt>{t('Session', '训练')}</dt><dd>{snapshot?.workout ? <><span>{text(snapshot.workout.name)}</span><Icon name="arrow-right" size={13}/></> : null}<strong>{text(proposal.workout.name)}</strong></dd></div>}
          <div><dt>{t('Duration', '时长')}</dt><dd>{snapshot?.workout && <><span>{snapshot.workout.estimatedMinutes} {t('min', '分钟')}</span><Icon name="arrow-right" size={13}/></>}<strong>{proposal.workout.estimatedMinutes} {t('min', '分钟')}</strong></dd></div>
          {isDemoMode() && snapshot?.workout && proposal.workout.exercises.map(ex=>ex.id).join() !== snapshot.workout.exercises.map(ex=>ex.id).join() && <div><dt>{t('Remaining order', '剩余动作顺序')}</dt><dd><strong>{proposal.workout.exercises.filter(ex=>!ex.completed).map(ex=>text(ex.name)).join(' → ')}</strong></dd></div>}
          {proposal.workout.exercises.map(exercise => {
            const before = snapshot?.workout?.exercises.find(item => item.id === exercise.id || item.id === exercise.replacesId)
            const prescription = (item: typeof exercise) => `${item.sets} × ${item.reps}${item.suggestedLoad.value !== null ? ` · ${item.suggestedLoad.value} kg${item.suggestedLoad.basis === 'per_hand' ? t('/hand', '/每只') : ''}` : ''}`
            if (before && text(before.name) === text(exercise.name) && prescription(before) === prescription(exercise)) return null
            return <div key={exercise.id}><dt>{text(exercise.name)}</dt><dd>{before ? <><span>{prescription(before)}</span><Icon name="arrow-right" size={13}/></> : <span>{t('New', '新增')}</span>}<strong>{prescription(exercise)}</strong></dd></div>
          })}
          {snapshot?.workout?.exercises.filter(before => !proposal.workout!.exercises.some(item => item.id === before.id || item.replacesId === before.id)).map(before => <div key={`removed-${before.id}`}><dt>{text(before.name)}</dt><dd><span>{before.sets} × {before.reps}</span><Icon name="arrow-right" size={13}/><strong>{t('Removed', '移除')}</strong></dd></div>)}
        </dl> : <dl>{proposal.moves?.map(move => <div key={move.sessionId}><dt>{move.split}</dt><dd><span>{move.from ? date(move.from) : t('Unscheduled', '待安排')}</span><Icon name="arrow-right" size={13}/><strong>{move.to ? date(move.to) : t('Unscheduled', '待安排')}</strong></dd></div>)}</dl>}
      </div>}
      {proposal?.status === 'pending' && !stale && <div className="agent-actions">
        <button className="agent-button agent-button-primary" disabled={locked} onClick={() => void execute(message.id, { kind: 'apply_proposal', proposalId: proposal.id, startAfterApply: false })}>
          {isPending ? t('Saving…', '正在保存…') : proposal.scope === 'schedule' ? t('Apply rest day', '应用休息安排') : t('Apply changes', '应用调整')}
        </button>
        <button className="agent-button agent-button-text" disabled={locked} onClick={() => void execute(message.id, { kind: 'dismiss_proposal', proposalId: proposal.id })}>{t('Keep original plan', '保留原计划')}</button>
      </div>}
      {stale && <div className="agent-status">
        <p>{t('The context has changed. This suggestion can no longer be applied.', '依据已更新，这份建议无法再应用。')}</p>
        <button className="agent-button agent-button-text" disabled={locked} onClick={() => prepareDraft(t('Please review my plan using the latest information.', '请根据最新情况重新看看我的方案。'), snapshot?.workout ? { workoutId: snapshot.workout.id } : undefined)}>{t('Review my plan', '重新查看方案')}</button>
      </div>}
      {proposal && ['applied', 'dismissed'].includes(proposal.status) && <>
        <p className="agent-status"><Icon name="check" size={16} />{proposal.status === 'dismissed' ? t('Original plan kept', '已保留原计划') : proposal.scope === 'schedule' ? t('Rest day and upcoming sessions saved', '休息与后续安排已保存') : t('Workout updated', '训练已更新')}</p>
        <div className="agent-actions"><button className="agent-button agent-button-text" onClick={() => void navigate({ to: proposal.scope === 'workout' && snapshot?.workout ? '/workout' : '/today' })}>
          <Icon name="arrow-left" size={16} />{proposal.scope === 'workout' && snapshot?.workout ? t('Return to workout', '返回训练') : t('View today & upcoming sessions', '查看今日与后续安排')}
        </button></div>
      </>}
      {message.role === 'assistant' && message.status !== 'streaming' && message.operationId && message.mealId && <div className="agent-actions agent-meal-actions">
        {snapshot?.meals.some(meal => meal.id === message.mealId) && <button className="agent-button agent-button-text" disabled={locked} onClick={() => prepareDraft(t('I’d like to correct this meal: ', '我想修改这餐：'), { mealId: message.mealId })}><Icon name="pencil" size={15} />{t('Modify', '修改')}</button>}
        <button className="agent-button agent-button-text" disabled={locked || actionFeedback?.kind === 'success'} onClick={() => void execute(message.id, { kind: 'undo_meal', operationId: message.operationId! })}><Icon name="undo-2" size={15} />{isPending ? t('Undoing…', '正在撤销…') : t('Undo', '撤销')}</button>
      </div>}
      {isDemoMode() && message.role === 'assistant' && message.status === 'complete' && !message.proposalId && !message.operationId && <div className="agent-actions"><button type="button" className="agent-button agent-button-text" onClick={() => void navigate({to: '/today'})}>{t('View today', '查看今日')}</button>{snapshot?.workout && !snapshot.plan.restDates.includes(snapshot.dayKey) && <button type="button" className="agent-button agent-button-text" onClick={() => void navigate({to: '/workout'})}>{t('View workout', '查看训练')}</button>}</div>}
      {actionFeedback && <p className={`agent-feedback agent-feedback-${actionFeedback.kind}`} role={actionFeedback.kind === 'error' ? 'alert' : 'status'}>{text(actionFeedback.content)}</p>}
      {message.role === 'assistant' && (message.status === 'failed' || message.status === 'stopped') && <div className="agent-message-failure">
        <p>{message.status === 'stopped' ? t('Reply stopped. Any saved changes are kept.', '回复已停止，已保存的修改会保留。') : errorCopy(message.errorCode, t)}</p>
        <button className="agent-button agent-button-text" disabled={locked} onClick={() => void prepareRetry(message)}><Icon name="rotate-ccw" size={15} />{t('Review & retry', '核对后重试')}</button>
      </div>}
    </>
  }

  return <section className={`agent-page${composing ? ' agent-page-composing' : ''}`} aria-label={t('Wellio Agent', 'Wellio 助手')}>
    <header className="agent-header"><h1>Wellio</h1></header>
    <div className="agent-thread" ref={threadRef} role="log" aria-label={t('Conversation with Wellio', '与 Wellio 的对话')} aria-live="polite" aria-relevant="additions text" onScroll={event => {
      const element = event.currentTarget
      nearBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 72
      if (nearBottom.current) setNewReply(false)
    }}>
      {snapshot && <div className="agent-day">{date(snapshot.dayKey, { month: 'long', day: 'numeric' })}</div>}
      {loading && !snapshot && <p className="agent-empty" role="status">{t('Loading your conversation…', '正在加载对话…')}</p>}
      {error && <div className="agent-feedback agent-feedback-error" role="alert"><p>{errorCopy(typeof error === 'string' ? error : undefined, t)}</p><button className="agent-button agent-button-text" onClick={() => void refresh()}>{t('Refresh', '刷新')}</button></div>}
      {readinessError && <div className="agent-feedback agent-feedback-error" role="alert"><p>{errorCopy(readinessError, t)}</p><button type="button" className="agent-button agent-button-text" disabled={locked || readinessBusy} onClick={() => void checkReadiness({ mode: 'retry' }).catch(() => {})}>{t('Retry recovery check', '重试恢复检查')}</button></div>}
      {(readinessBusy || snapshot?.readinessCheck?.status === 'pending') && !readinessError && <p className="agent-status" role="status">{t('Recovery check in progress…', '正在检查恢复情况…')}{readinessBusy ? <button type="button" className="agent-button agent-button-text" onClick={stopChat}>{t('Stop check', '停止检查')}</button> : <button type="button" className="agent-button agent-button-text" onClick={() => void refresh()}>{t('Refresh', '刷新')}</button>}</p>}
      {!loading && !messages.length && snapshot && <section className="agent-day-context" aria-label={t('Today’s context', '今日上下文')}>
        <h2>{t('Let’s work with your day', '从今天的安排开始')}</h2>
        <div className="agent-context-facts">
          {snapshot.readiness.quality === 'valid' && snapshot.readiness.score !== null && snapshot.readiness.scoreScale > 0 && <span>{t('Readiness', '准备度')} <strong>{(snapshot.readiness.score / snapshot.readiness.scoreScale * 10).toFixed(1)}<small>/10</small></strong></span>}
          <span>{t('Today’s plan', '今日安排')} <strong>{snapshot.plan.restDates.includes(snapshot.dayKey) ? t('Rest day', '休息日') : snapshot.workout ? text(snapshot.workout.name) : t('Not set yet', '尚未安排')}</strong></span>
        </div>
        {snapshot.advice.status === 'valid' && (snapshot.advice.training || snapshot.advice.nutrition) ? <div className="agent-context-advice">
          <span className="agent-author">Wellio · {t('Today’s suggestion', '今日建议')}</span>
          <div className="agent-bubble"><AgentMessageBody content={text(snapshot.advice.training || snapshot.advice.nutrition!)} /></div>
        </div> : <p>{t('Tell me what you’d like to change. We can review your workout or choose your next meal using these records.', '告诉我你想调整什么，我们可以结合这些记录讨论训练，或选择下一餐。')}</p>}
        <div className="agent-context-actions">
          <button type="button" className="agent-button agent-context-action" disabled={locked} onClick={() => prepareDraft(t('Review my readiness and today’s workout. What would you change, and why?', '结合准备度看看今天的训练，你建议怎么调整，为什么？'), snapshot.workout ? { workoutId: snapshot.workout.id } : undefined)}><Icon name="dumbbell" size={19} /><span>{t('Review today’s plan', '看看今日计划')}</span><Icon name="arrow-right" size={17} /></button>
          <button type="button" className="agent-button agent-context-action" disabled={locked} onClick={() => prepareDraft(t('Suggest my next meal using today’s recovery and what I have eaten so far.', '结合今天的恢复和已摄入的营养，推荐下一餐。'))}><Icon name="utensils" size={19} /><span>{t('Choose my next meal', '选下一餐吃什么')}</span><Icon name="arrow-right" size={17} /></button>
        </div>
      </section>}
      {messages.map(message => <article key={message.id} className={`agent-turn agent-turn-${message.role}`}>
        <div className="agent-author">{message.role === 'user' ? t('You', '你') : 'Wellio'}
          {message.source === 'app_open' && <span>{t('Recovery check', '恢复检查')}</span>}
        </div>
        <div className="agent-bubble">
          {message.attachmentUrl && <a className="agent-sent-image" href={message.attachmentUrl} target="_blank" rel="noopener noreferrer"><img src={message.attachmentUrl} alt={t('Image attached to this message', '这条消息附带的图片')} loading="lazy" /></a>}
          {message.role === 'assistant' && <AgentToolProcess steps={message.steps} />}
          {message.status === 'streaming' && message.phase && <p className="agent-phase" role="status"><Icon name="loader" size={15} />{message.phase === 'recognizing' ? t('Recognizing the food…', '正在识别食物…') : t('Thinking it through…', '正在整理建议…')}</p>}
          <AgentMessageBody content={text(message.content)} />
          {messageActions(message)}
        </div>
        <time className="agent-message-time" dateTime={message.createdAt}>{date(message.createdAt, { hour: '2-digit', minute: '2-digit' })}</time>
      </article>)}
    </div>
    {newReply && <button className="agent-new-reply" onClick={() => {
      if (threadRef.current) threadRef.current.scrollTop = threadRef.current.scrollHeight
      nearBottom.current = true; setNewReply(false)
    }}>{t('New reply', '有新回复')}<Icon name="arrow-down" size={16} /></button>}
    <div className="agent-companion-row">
      <div className="agent-followups" hidden={!messages.length}>
        <button className="agent-button agent-quick" disabled={locked} onClick={() => prepareDraft(t('Recommend a meal based on today’s recovery and what I’ve eaten.', '结合今天的恢复情况和已吃的食物，推荐一餐。'))}><Icon name="utensils" size={17} />{t('Recommend a meal', '推荐一餐')}</button>
        <button className="agent-button agent-quick" disabled={locked} onClick={() => prepareDraft(t('Help me adjust today’s training plan.', '帮我调整今天的训练计划。'), snapshot?.workout ? { workoutId: snapshot.workout.id } : undefined)}><Icon name="dumbbell" size={17} />{t('Adjust my plan', '调整计划')}</button>
      </div>
      <div className="agent-companion" data-thinking={active || readinessBusy || undefined}><span className="agent-thinking-dots" aria-hidden="true"><i/><i/><i/></span><Mascot pose={active || readinessBusy ? 'thinking' : celebrating ? 'celebrate' : 'welcome'} className="agent-mascot" alt={t('Wellio, your avocado companion', '牛油果伙伴 Wellio')} /></div>
    </div>
    <form className="agent-composer" onSubmit={event => void submit(event)}>
      {(chatTarget?.mealId || chatTarget?.workoutId) && <div className="agent-target"><span>{chatTarget.mealId ? t('Editing the selected meal', '修改所选餐食') : t('Discussing the current workout', '讨论当前训练')}</span><button type="button" className="agent-icon-button" aria-label={t('Clear selected context', '取消关联记录')} onClick={() => setChatTarget({})}><Icon name="x" size={16} /></button></div>}
      {attachment && <div className="agent-pending-attachment">
        <img src={attachment.previewUrl} alt={t('Selected image preview', '所选图片预览')} />
        <div className="agent-attachment-detail"><span>{attachment.file.name}</span><label>{t('Use image as', '图片用途')}<select value={attachment.purpose} disabled={active} onChange={event => setAttachment(previous => previous ? { ...previous, purpose: event.target.value as Attachment['purpose'], uploaded: undefined } : previous)}>
          <option value="food">{t('Food · log a meal', '食物 · 记录一餐')}</option><option value="menu">{t('Menu · get suggestions', '菜单 · 获取建议')}</option>
        </select></label></div>
        <button type="button" className="agent-icon-button" disabled={active} aria-label={t('Remove image', '移除图片')} onClick={() => setAttachment(null)}><Icon name="x" size={18} /></button>
      </div>}
      {composerNotice && <p className={`agent-feedback agent-feedback-${composerNotice.kind}`} role={composerNotice.kind === 'error' ? 'alert' : 'status'}>{text(composerNotice.content)}</p>}
      <textarea ref={composerRef} rows={1} value={draft} readOnly={active} onChange={event => setDraft(event.target.value)} onFocus={() => setComposing(true)} onBlur={() => setComposing(false)} aria-label={t('Message Wellio', '给 Wellio 的消息')} placeholder={t('Tell me what’s changed…', '说说今天的变化…')} onKeyDown={event => {
        if (event.key === 'Enter' && !event.shiftKey && (event.metaKey || event.ctrlKey) && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit() }
      }} />
      <div className="agent-composer-footer">
        <input ref={fileRef} className="agent-file-input" type="file" accept="image/jpeg,image/png,image/webp" tabIndex={-1} onChange={event => {
          const file = event.target.files?.[0]
          event.target.value = ''
          if (!file) return
          if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) { setComposerNotice({ kind: 'error', content: bilingual('Choose a JPEG, PNG or WebP image.', '请选择 JPEG、PNG 或 WebP 图片。') }); return }
          if (file.size > 10 * 1024 * 1024) { setComposerNotice({ kind: 'error', content: bilingual('Choose an image of 10 MB or less.', '请选择不超过 10 MB 的图片。') }); return }
          setAttachment({ file, purpose: 'food', previewUrl: URL.createObjectURL(file) })
          setComposerNotice(null)
        }} />
        <button type="button" className="agent-icon-button agent-attach" disabled={locked} aria-label={t('Add an image', '添加图片')} onClick={() => fileRef.current?.click()}><Icon name="image-plus" size={23} /></button>
        {uploading && <span className="agent-upload-status" role="status">{t('Uploading…', '上传中…')}</span>}
        {chatBusy ? <button key="stop" type="button" className="agent-send" aria-label={t('Stop reply', '停止回复')} onClick={event => { event.preventDefault(); stopChat() }}><Icon name="square" size={18} /></button>
          : <button key="send" type="submit" className="agent-send" disabled={locked || (!draft.trim() && !attachment) || !snapshot?.capabilities.agent} aria-label={t('Send message', '发送消息')}><Icon name={active ? 'loader' : 'arrow-up'} size={23} /></button>}
      </div>
      {snapshot && !snapshot.capabilities.agent && <p className="agent-capability-note">{t('Agent connection is not configured yet. Your draft is kept.', 'Agent 连接尚未配置，草稿会保留。')}</p>}
    </form>
  </section>
}
