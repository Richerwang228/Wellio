#!/usr/bin/env node
import { spawn, spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { resolve } from 'node:path'

const root = fileURLToPath(new URL('..', import.meta.url))
const mode = process.argv[2] || 'run'
const urlAt = process.argv.indexOf('--url')
const rawUrl = urlAt >= 0 ? process.argv[urlAt + 1] : process.env.WELLIO_NATIVE_URL || 'http://127.0.0.1:3102/today'
const deviceAt = process.argv.indexOf('--device')
const requestedDevice = deviceAt >= 0 ? process.argv[deviceAt + 1] : undefined
const developerDir = process.env.DEVELOPER_DIR || '/Applications/Xcode.app/Contents/Developer'
const env = { ...process.env, DEVELOPER_DIR: developerDir, WELLIO_NATIVE_URL: rawUrl }
const cap = resolve(root, 'node_modules/@capacitor/cli/bin/capacitor')
let ownedPreview
let stopping = false
async function stop(code) {
  if (stopping) return
  stopping = true
  ownedPreview?.kill('SIGTERM')
  process.exit(code)
}
process.on('SIGINT', () => void stop(130))
process.on('SIGTERM', () => void stop(143))
function read(command, args) {
  const run = spawnSync(command, args, { cwd: root, env, encoding: 'utf8' })
  if (run.error || run.status !== 0) throw new Error(run.stderr?.trim() || run.error?.message || `${command} failed`)
  return run.stdout
}
async function run(command, args) {
  await new Promise((done, reject) => {
    const child = spawn(command, args, { cwd: root, env, stdio: 'inherit' })
    child.once('error', reject)
    child.once('exit', code => code === 0 ? done() : reject(new Error(`${command} exited with ${code}`)))
  })
}
async function isReady(url) {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(2000) })
    return response.ok && (await response.text()).includes('Wellio')
  } catch { return false }
}
try {
  if (!['run', 'sync', 'open'].includes(mode)) throw new Error('Use run, sync or open.')
  if (!rawUrl) throw new Error('Provide a URL after --url.')
  const url = new URL(rawUrl)
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error('Use an HTTP(S) URL without credentials.')
  if (mode !== 'sync' && !existsSync(resolve(developerDir, 'usr/bin/xcodebuild'))) throw new Error('Install Xcode and its iOS Simulator platform first, then run this command again.')
  console.log(`[Wellio] iOS demo URL: ${url.href}`)
  await run(process.execPath, [cap, 'sync', 'ios'])
  if (mode === 'sync') process.exit(0)
  if (mode === 'open') { await run('open', [resolve(root, 'ios/App/App.xcodeproj')]); process.exit(0) }

  read('xcodebuild', ['-version'])
  const inventory = JSON.parse(read('xcrun', ['simctl', 'list', 'devices', 'available', '--json']))
  const phones = Object.values(inventory.devices).flat().filter(device => device.isAvailable && device.name.startsWith('iPhone'))
  const device = requestedDevice ? phones.find(device => device.udid === requestedDevice || device.name === requestedDevice) : phones.find(device => device.state === 'Booted') || phones[0]
  if (!device) throw new Error('No available iPhone simulator. Install an iOS platform in Xcode Settings → Components, then create an iPhone simulator.')

  if (!await isReady(url)) {
    if (urlAt >= 0 || process.env.WELLIO_NATIVE_URL) throw new Error(`Start the Wellio service at ${url.origin} first.`)
    console.log('[Wellio] Starting the UI preview with development demo data.')
    ownedPreview = spawn(process.execPath, [resolve(root, 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', '3102', '--strictPort'], { cwd: root, env: { ...env, VITE_WELLIO_PREVIEW: '1' }, stdio: 'inherit' })
    ownedPreview.once('error', error => { console.error(error.message); void stop(1) })
    let ready = false
    for (let i = 0; i < 45; i++) {
      if (ownedPreview.exitCode !== null) throw new Error('UI preview could not start. Check port 3102.')
      if (await isReady(url)) { ready = true; break }
      await new Promise(done => setTimeout(done, 500))
    }
    if (!ready) throw new Error('UI preview did not become ready.')
  }
  if (device.state !== 'Booted') await run('xcrun', ['simctl', 'boot', device.udid])
  await run('open', ['-a', resolve(developerDir, 'Applications/Simulator.app')])
  await run('xcrun', ['simctl', 'bootstatus', device.udid, '-b'])
  console.log(`[Wellio] Building for ${device.name}…`)
  await run('xcodebuild', ['-project', 'ios/App/App.xcodeproj', '-scheme', 'App', '-configuration', 'Debug', '-sdk', 'iphonesimulator', '-destination', `id=${device.udid}`, '-derivedDataPath', '.native-build', 'CODE_SIGNING_ALLOWED=NO', 'build'])
  const app = resolve(root, '.native-build/Build/Products/Debug-iphonesimulator/App.app')
  await run('xcrun', ['simctl', 'install', device.udid, app])
  await run('xcrun', ['simctl', 'launch', device.udid, 'app.wellio.demo'])
  console.log(`[Wellio] Installed and opened on ${device.name}.`)
  if (ownedPreview) { console.log('[Wellio] Keep this terminal open while demonstrating. Ctrl+C stops the preview.'); await new Promise(() => {}) }
} catch (error) { console.error(`[Wellio] ${error.message}`); await stop(1) }
