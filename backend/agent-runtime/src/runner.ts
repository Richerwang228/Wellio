import {AgentRunner, type AgentRunnerRunRequest} from '@copilotkit/runtime/v2'
import type {BaseEvent} from '@ag-ui/client'
import {Observable, type Subscriber} from 'rxjs'

/** A request-scoped official AgentRunner. No thread store or replay history. */
export class StatelessRunner extends AgentRunner {
  private observer?: Subscriber<BaseEvent>
  private started = false
  private resolve!: () => void
  private reject!: (reason: unknown) => void
  readonly result = new Promise<void>((resolve, reject) => {this.resolve = resolve; this.reject = reject})
  constructor(private readonly signal: AbortSignal, private readonly onStart: () => void, private readonly onUnsubscribe: () => void) {super()}
  run({agent, input}: AgentRunnerRunRequest): Observable<BaseEvent> {
    return new Observable(observer => {
      if (this.started) {observer.error(new Error('RUN_ALREADY_STARTED')); return}
      this.started = true
      this.observer = observer
      const abort = () => agent.abortRun()
      this.signal.addEventListener('abort', abort, {once: true})
      const running = agent.runAgent(input, {onEvent: ({event}) => {
        observer.next(event)
        if (event.type === 'RUN_STARTED') this.onStart()
      }})
      if (this.signal.aborted) abort()
      void running.then(this.resolve, this.reject).finally(() => this.signal.removeEventListener('abort', abort))
      return () => {this.onUnsubscribe(); agent.abortRun()}
    })
  }
  emit(event: BaseEvent) {this.observer?.next(event)}
  complete() {this.observer?.complete(); this.observer = undefined}
  connect(): Observable<BaseEvent> {return new Observable(observer => observer.complete())}
  async isRunning() {return this.started && Boolean(this.observer)}
  async stop() {this.onUnsubscribe(); return true}
}
