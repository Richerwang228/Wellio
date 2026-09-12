import {afterEach,expect,it,vi} from 'vitest'
import {handleCopilotRequest} from '../src/server/copilot-proxy'

afterEach(()=>{vi.unstubAllGlobals();vi.unstubAllEnvs()})

it('forwards the original session and origin only to the configured Node service',async()=>{
 vi.stubEnv('WELLIO_AGENT_BASE_URL','http://127.0.0.1:8001')
 const upstream=vi.fn().mockResolvedValue(new Response('data: event\n\n',{headers:{'content-type':'text/event-stream'}}))
 vi.stubGlobal('fetch',upstream)
 const signal=new AbortController().signal
 const result=await handleCopilotRequest(new Request('http://localhost:3100/api/copilotkit/agent/wellio/run',{method:'POST',headers:{cookie:'session=signed','origin':'http://localhost:3100','content-type':'application/json',authorization:'Bearer browser-controlled'},body:'{}',signal}))
 expect(result.status).toBe(200)
 expect(upstream.mock.calls[0][0].href).toBe('http://127.0.0.1:8001/api/copilotkit/agent/wellio/run')
 const headers=upstream.mock.calls[0][1].headers as Headers
 expect(headers.get('cookie')).toBe('session=signed')
 expect(headers.get('origin')).toBe('http://localhost:3100')
 expect(headers.has('authorization')).toBe(false)
 expect(result.headers.get('cache-control')).toBe('no-store')
 expect(await result.text()).toBe('data: event\n\n')
})

it.each([{origin:'https://foreign.example'},{'sec-fetch-site':'cross-site'},{referer:'https://foreign.example/page'}] as Record<string,string>[])('rejects a cross-origin request before contacting the runtime: %j',async headers=>{
 const upstream=vi.fn();vi.stubGlobal('fetch',upstream)
 const result=await handleCopilotRequest(new Request('http://localhost:3100/api/copilotkit/proposal',{method:'POST',headers}))
 expect(result.status).toBe(403);expect(upstream).not.toHaveBeenCalled()
})

it('reports a stopped Node service as unavailable',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockRejectedValue(new TypeError('Connection refused')))
 const result=await handleCopilotRequest(new Request('http://localhost:3100/api/copilotkit/info'))
 expect(result.status).toBe(503)
 expect(await result.json()).toMatchObject({errorCode:'API_UNAVAILABLE'})
})
