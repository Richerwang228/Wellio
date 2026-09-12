import { spawn } from 'node:child_process'
import { resolve } from 'node:path'
const mode = process.argv[2]
if (!['e2e-server', 'production', 'python'].includes(mode)) throw new Error('Invalid backend check mode')
const app = resolve(import.meta.dirname, '..'), backend = resolve(process.env.WELLIO_BACKEND_DIR || resolve(app, '../wellio-backend'))
const script = mode === 'e2e-server' ? 'tests/serve_frontend.py' : 'tests/production_smoke.py'
const args = mode === 'python' ? ['pytest', '-q', resolve(backend, 'tests')] : ['python', resolve(backend, script), app]
const child = spawn('uv', ['run', '--frozen', '--project', backend, ...args], { cwd: backend, env: process.env, stdio: 'inherit' })
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal))
child.on('error', error => { console.error(error.message); process.exitCode = 1 })
child.on('exit', code => { process.exitCode = code ?? 1 })
