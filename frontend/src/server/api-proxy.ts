/** Same-origin frontend boundary. Business API runs in the Python FastAPI service. */
export async function handleBackendRequest(request: Request): Promise<Response> {
  try {
    const base = process.env.WELLIO_API_BASE_URL || 'http://127.0.0.1:8000'
    const target = new URL(base)
    if (!['http:', 'https:'].includes(target.protocol) || target.username || target.password) throw new Error('INVALID_API_BASE_URL')
    const original = new URL(request.url)
    target.pathname = original.pathname; target.search = original.search
    const headers = new Headers(request.headers)
    for (const name of ['host', 'connection', 'transfer-encoding', 'keep-alive', 'upgrade', 'proxy-authorization', 'proxy-authenticate', 'te', 'trailer']) headers.delete(name)
    // FastAPI checks the original browser Origin/Referer against WELLIO_PUBLIC_ORIGIN.
    const init: RequestInit & { duplex?: 'half' } = { method: request.method, headers, signal: request.signal, redirect: 'manual' }
    if (request.method !== 'GET' && request.method !== 'HEAD') { init.body = request.body; init.duplex = 'half' }
    const response = await fetch(target, init)
    const responseHeaders = new Headers(response.headers)
    for (const name of ['connection', 'transfer-encoding', 'content-encoding', 'content-length']) responseHeaders.delete(name)
    return new Response(response.body, { status: response.status, headers: responseHeaders })
  } catch (error) {
    if (request.signal.aborted) throw error
    return Response.json({ status: 'failed', errorCode: 'API_UNAVAILABLE' }, { status: 503, headers: { 'Cache-Control': 'no-store', 'Vary': 'Cookie', 'X-Content-Type-Options': 'nosniff' } })
  }
}
