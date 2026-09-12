import {createServer, type Server} from 'node:http'
import {pathToFileURL} from 'node:url'
import {createAgentRuntime, type AgentRuntime} from './runtime.js'
import {configuredModel} from './model.js'

export async function serve(runtime: AgentRuntime, {port = 8001, host = '127.0.0.1'} = {}): Promise<Server> {
  const server = createServer(async (incoming, outgoing) => {
    const controller = new AbortController()
    incoming.on('aborted', () => controller.abort())
    outgoing.on('close', () => {if (!outgoing.writableEnded) controller.abort()})
    try {
      const chunks: Buffer[] = []
      let size = 0
      for await (const chunk of incoming) {
        size += chunk.length
        if (size > 128 * 1024) {
          outgoing.writeHead(413, {'content-type': 'application/json', 'cache-control': 'no-store'})
          outgoing.end(JSON.stringify({errorCode: 'PAYLOAD_TOO_LARGE'}))
          return
        }
        chunks.push(chunk)
      }
      const headers = new Headers()
      for (const [name, value] of Object.entries(incoming.headers)) if (value !== undefined) headers.set(name, Array.isArray(value) ? value.join(', ') : value)
      const request = new Request(`http://${incoming.headers.host ?? `${host}:${port}`}${incoming.url}`, {method: incoming.method, headers, signal: controller.signal, ...(['GET', 'HEAD'].includes(incoming.method ?? 'GET') ? {} : {body: Buffer.concat(chunks)})})
      const response = await runtime.handleRequest(request)
      outgoing.writeHead(response.status, Object.fromEntries(response.headers))
      const reader = response.body?.getReader()
      if (reader) {
        try {
          while (true) {
            const {value, done} = await reader.read()
            if (done) break
            if (controller.signal.aborted) {await reader.cancel(); break}
            if (!outgoing.write(value)) await new Promise<void>(resolve => {outgoing.once('drain', resolve); outgoing.once('close', resolve)})
          }
        } finally {reader.releaseLock()}
      }
      outgoing.end()
    } catch {
      if (!outgoing.headersSent) outgoing.writeHead(500, {'content-type': 'application/json', 'cache-control': 'no-store'})
      if (!outgoing.destroyed) outgoing.end(JSON.stringify({errorCode: 'INTERNAL_ERROR'}))
    }
  })
  await new Promise<void>((resolve, reject) => {server.once('error', reject); server.listen(port, host, resolve)})
  return server
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const runtime = createAgentRuntime({backendUrl: process.env.WELLIO_API_BASE_URL ?? 'http://127.0.0.1:8000', token: process.env.WELLIO_AGENT_TOKEN ?? '', model: configuredModel()})
  const port = Number(process.env.WELLIO_AGENT_PORT ?? 8001)
  const host = process.env.HOST ?? '127.0.0.1'
  const server = await serve(runtime, {port, host})
  process.stdout.write(`Wellio agent runtime listening on http://${host}:${port}\n`)
  const shutdown = async () => {server.close(); await runtime.close(); server.closeAllConnections()}
  process.once('SIGTERM', () => {void shutdown()})
  process.once('SIGINT', () => {void shutdown()})
}
