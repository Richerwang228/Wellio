import { useEffect, useRef, useState } from 'react'
import { isDemoMode } from '../../lib/runtime-mode'
import { useWellio } from '../../lib/wellio-context'
import type { Snapshot } from '../../lib/contracts'
import { ApiError } from '../../lib/api-client'
import { demoReadings, type ReadinessDemoMode } from '../../lib/readiness-demo'

export function useReadinessDemo(snapshot: Snapshot | null) {
  const { refresh, runAction, checkReadiness, busy, chatBusy, error: actionError, readinessError } = useWellio()
  const scripted = isDemoMode()
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const selecting = useRef(false)
  const requestedMode = useRef<ReadinessDemoMode | null>(null)
  const generation = useRef(0)
  useEffect(() => {
    generation.current++; selecting.current = false; requestedMode.current = null; setPending(false); setError(null)
    return () => { generation.current++ }
  }, [snapshot?.sessionId, snapshot?.resetEpoch])

  async function select(mode: ReadinessDemoMode) {
    if (!snapshot || busy || chatBusy || selecting.current) return
    const token = generation.current
    requestedMode.current = mode
    selecting.current = true; setPending(true); setError(null)
    try {
      if (scripted) {
        const { setDemoScenario } = await import('../../lib/demo')
        setDemoScenario(mode)
        await refresh()
        return
      }
      // Persist only synthetic watch inputs. The shared CopilotKit recovery
      // check owns advice/proposals, and Apply owns any saved plan changes.
      const result = await runAction({ kind: 'set_readiness_demo', mode }, 'today')
      if (token !== generation.current || !result) return
      if (result.status !== 'succeeded') return
      if (!result.snapshot) { setError('INVALID_MODEL_OUTPUT'); return }
      await checkReadiness({ mode: 'retry' })
    } catch (cause) {
      // Live failures are owned by the shared provider, so its successful
      // retries also clear the error shown here.
      if (scripted && token === generation.current) setError(cause instanceof ApiError ? cause.code : 'NETWORK_ERROR')
    } finally {
      if (token === generation.current) { selecting.current = false; setPending(false) }
    }
  }
  const sharedMode: ReadinessDemoMode = (snapshot?.readiness.score ?? 65) < 50 ? 'low' : (snapshot?.readiness.score ?? 65) < 80 ? 'balanced' : 'high'
  return {
    mode: scripted ? sharedMode : snapshot?.readiness.demoMode ?? null,
    // Offline fixtures predate the optional watch metric fields.
    readings: scripted ? demoReadings[sharedMode] : null,
    pending, error: error ?? actionError ?? readinessError, select,
    retry: () => { if (requestedMode.current) void select(requestedMode.current); else void checkReadiness({ mode: 'retry' }).catch(() => {}) },
    exit: scripted ? () => { void select('balanced') } : undefined,
  }
}
