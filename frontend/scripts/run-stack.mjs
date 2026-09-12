/** Run the frontend, FastAPI and CopilotKit service against a configured PostgreSQL. */
import { spawn } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { createServer } from 'node:net'
import { resolve } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'

const mode = process.argv[2] || 'start'
if (!['dev', 'start'].includes(mode)) throw new Error('Expected dev or start')
const appDirectory = resolve(import.meta.dirname, '..'), backendDirectory = resolve(process.env.WELLIO_BACKEND_DIR || resolve(appDirectory, '../wellio-backend'))
const children = new Set()
let stopping = false
async function stop(code = 0) {
  if (stopping) return
  stopping = true
  for (const child of children) child.kill('SIGTERM')
  const timeout = setTimeout(() => { for (const child of children) child.kill('SIGKILL') }, 4000)
  await Promise.all([...children].map(child => child.exitCode !== null || child.signalCode !== null ? Promise.resolve() : new Promise(done => child.once('exit', done))))
  clearTimeout(timeout)
  process.exit(code)
}
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => { void stop() })
function launch(command, args, env, cwd = appDirectory) {
  const child = spawn(command, args, { cwd, env, stdio: 'inherit' })
  children.add(child)
  child.on('error', error => { children.delete(child); console.error(`[wellio] ${command}: ${error.message}`); void stop(1) })
  child.on('exit', code => { children.delete(child); if (!stopping) void stop(code || 1) })
  return child
}
async function freePort() {
  const server = createServer()
  await new Promise((done, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', done) })
  const port = server.address().port
  await new Promise(done => server.close(done))
  return port
}
async function ready(url, headers) {
  for (let attempt = 0; attempt < 150 && !stopping; attempt++) {
    try { if ((await fetch(url, { headers, signal: AbortSignal.timeout(500) })).ok) return } catch {}
    await delay(100)
  }
  throw new Error('Backend did not become ready')
}
try {
  const port = process.env.PORT || process.env.NITRO_PORT || '3100'
  const apiPort = await freePort()
  const agentPort = await freePort()
  if (!process.env.DATABASE_URL) throw new Error('DATABASE_URL_REQUIRED: PostgreSQL must be configured before starting the stack')
  const env = { ...process.env, PORT: port, NITRO_PORT: port, HOST: process.env.HOST || '127.0.0.1', WELLIO_API_BASE_URL: `http://127.0.0.1:${apiPort}`, WELLIO_AGENT_BASE_URL: `http://127.0.0.1:${agentPort}`, WELLIO_AGENT_PORT: String(agentPort), WELLIO_AGENT_TOKEN: process.env.WELLIO_AGENT_TOKEN || randomBytes(32).toString('hex'), COPILOTKIT_TELEMETRY_DISABLED: 'true', WELLIO_PUBLIC_ORIGIN: process.env.WELLIO_PUBLIC_ORIGIN || `http://127.0.0.1:${port},http://localhost:${port}` }
  launch(process.env.WELLIO_PYTHON || resolve(backendDirectory, '.venv/bin/python'), ['-m', 'uvicorn', 'wellio.main:application', '--factory', '--host', '127.0.0.1', '--port', String(apiPort), '--no-proxy-headers'], env, backendDirectory)
  await ready(env.WELLIO_API_BASE_URL + '/healthz')
  const readiness = await fetch(env.WELLIO_API_BASE_URL + '/readyz', { signal: AbortSignal.timeout(12000) })
  if (!readiness.ok) {
    const details = await readiness.json().catch(() => ({}))
    throw new Error(`Demo dependencies are not ready: ${JSON.stringify(details)}`)
  }
  launch(process.execPath, ['dist/server.js'], env, resolve(backendDirectory, 'agent-runtime'))
  await ready(env.WELLIO_AGENT_BASE_URL + '/healthz')
  launch(process.execPath, mode === 'dev' ? ['node_modules/vite/bin/vite.js', '--host', env.HOST, '--port', port, '--strictPort'] : ['.output/server/index.mjs'], env)
  await ready(`http://127.0.0.1:${port}/api/copilotkit/info`)
  console.log(`[wellio] FastAPI and CopilotKit ready; frontend http://127.0.0.1:${port}`)
} catch (error) { console.error(`[wellio] ${error.message}`); await stop(1) }
