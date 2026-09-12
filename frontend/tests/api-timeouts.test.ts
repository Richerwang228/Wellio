import {afterEach,describe,expect,it,vi} from 'vitest'
import {api,REQUEST_TIMEOUTS} from '../src/lib/api-client'
import {createFixture} from '../src/lib/fixtures'

afterEach(()=>{vi.useRealTimers();vi.unstubAllGlobals()})
describe('Bounded business requests',()=>{
 it.each(['read','write','upload'] as const)('times out a stalled %s and permits a new request',async kind=>{
  vi.useFakeTimers()
  let signal:AbortSignal|undefined
  const fetch=vi.fn().mockImplementationOnce((_url:string,init:RequestInit)=>{signal=init.signal!;return new Promise(()=>{})})
   .mockResolvedValueOnce(new Response(JSON.stringify(createFixture()),{headers:{'Content-Type':'application/json'}}))
  vi.stubGlobal('fetch',fetch)
  const pending=kind==='read'?api.getSnapshot():kind==='write'?api.action({kind:'set_locale',locale:'en',requestId:'timeout-action',resetEpoch:1,source:'profile'}):api.upload(new File(['image'],'meal.png',{type:'image/png'}),'food')
  const outcome=pending.catch(error=>error)
  await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUTS[kind])
  expect(await outcome).toMatchObject({code:'TIMEOUT'})
  expect(signal?.aborted).toBe(true)
  await expect(api.getSnapshot()).resolves.toMatchObject({resetEpoch:1})
  expect(fetch).toHaveBeenCalledTimes(2)
 })
 it('includes a stalled JSON response body in the same request budget',async()=>{
  vi.useFakeTimers()
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(new ReadableStream(),{headers:{'Content-Type':'application/json'}})))
  const outcome=api.getSnapshot().catch(error=>error)
  await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUTS.read)
  expect(await outcome).toMatchObject({code:'TIMEOUT'})
 })
 it('releases a cancelled request even if fetch ignores abort',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockImplementation(()=>new Promise(()=>{})))
  const controller=new AbortController(),outcome=api.getSnapshot(controller.signal).catch(error=>error)
  await Promise.resolve();controller.abort()
  expect(await outcome).toMatchObject({name:'AbortError'})
 })
})
