/** Same-origin transport to the backend repository's CopilotKit service. */
export async function handleCopilotRequest(request: Request): Promise<Response> {
  const original = new URL(request.url)
  const origin = request.headers.get('origin')
  if (request.headers.get('sec-fetch-site') === 'cross-site' || (origin && origin !== original.origin)) return failure('ORIGIN_NOT_ALLOWED', 403)
  const referer = request.headers.get('referer')
  if (!origin && referer) {
    try { if (new URL(referer).origin !== original.origin) return failure('ORIGIN_NOT_ALLOWED', 403) } catch { return failure('ORIGIN_NOT_ALLOWED', 403) }
  }
  try {
    const target = new URL(process.env.WELLIO_AGENT_BASE_URL || 'http://127.0.0.1:8001')
    if (!['http:', 'https:'].includes(target.protocol) || target.username || target.password) return failure('API_UNAVAILABLE', 503)
    target.pathname = original.pathname; target.search = original.search
    const headers = new Headers(request.headers)
    for (const name of ['host', 'connection', 'transfer-encoding', 'keep-alive', 'upgrade', 'proxy-authorization', 'proxy-authenticate', 'authorization', 'te', 'trailer']) headers.delete(name)
    const init: RequestInit & { duplex?: 'half' } = { method: request.method, headers, signal: request.signal, redirect: 'manual' }
    if (request.method !== 'GET' && request.method !== 'HEAD') { init.body = request.body; init.duplex = 'half' }
    const response = await fetch(target, init)
    const outputHeaders = new Headers(response.headers)
    for (const name of ['connection', 'transfer-encoding', 'content-encoding', 'content-length']) outputHeaders.delete(name)
    outputHeaders.set('Cache-Control', 'no-store'); outputHeaders.set('Vary', 'Cookie')
    return new Response(response.body, { status: response.status, headers: outputHeaders })
  } catch (error) {
    if (request.signal.aborted) throw error
    return failure('API_UNAVAILABLE', 503)
  }
}

function failure(errorCode: string, status: number) {
  return Response.json({ status: 'failed', errorCode }, { status, headers: { 'Cache-Control': 'no-store', Vary: 'Cookie', 'X-Content-Type-Options': 'nosniff' } })
}
