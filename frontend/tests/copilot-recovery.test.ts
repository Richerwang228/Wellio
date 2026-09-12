import {afterEach,describe,expect,it,vi} from 'vitest'
import {CopilotKitCore,CopilotKitCoreRuntimeConnectionStatus as Status} from '@copilotkit/core'
import {installCopilotRecovery} from '../src/lib/copilot-transport'
import {registerChatRecovery,retryChatConnection} from '../src/lib/api-client'

const info=()=>new Response(JSON.stringify({version:'1.71.1',agents:{wellio:{description:'Wellio'}}}),{headers:{'Content-Type':'application/json'}})
function setup(){
 vi.useFakeTimers()
 vi.stubGlobal('window',{location:{origin:'http://localhost'}})
 vi.spyOn(console,'error').mockImplementation(()=>{})
 vi.spyOn(console,'warn').mockImplementation(()=>{})
 const core=new CopilotKitCore({runtimeUrl:'/api/copilotkit',runtimeTransport:'rest',deferInitialConnection:true})
 const recovery=installCopilotRecovery(core)
 return {core,recovery}
}
afterEach(()=>{vi.useRealTimers();vi.restoreAllMocks();vi.unstubAllGlobals()})
describe('CopilotKit discovery recovery through public SDK methods',()=>{
 it('recovers from a first /info 503 and discovers the SDK agent without remounting the page',async()=>{
  const fetch=vi.fn().mockResolvedValueOnce(new Response('unavailable',{status:503})).mockImplementation(async()=>info())
  vi.stubGlobal('fetch',fetch)
  const {core,recovery}=setup()
  try{
   core.connect();await vi.advanceTimersByTimeAsync(1)
   expect(core.runtimeConnectionStatus).toBe(Status.Error)
   core.connect();await vi.advanceTimersByTimeAsync(1)
   expect(fetch).toHaveBeenCalledTimes(1)
   await vi.advanceTimersByTimeAsync(500)
   expect(core.runtimeConnectionStatus).toBe(Status.Connected)
   expect(core.getAgent('wellio')).toBeDefined()
   expect(fetch).toHaveBeenCalledTimes(2)
  }finally{recovery.dispose()}
 })
 it('stops after three automatic retries and allows the existing Refresh action to reconnect',async()=>{
  const fetch=vi.fn().mockImplementation(async()=>new Response('unavailable',{status:503}))
  vi.stubGlobal('fetch',fetch)
  const {core,recovery}=setup(),unregister=registerChatRecovery(recovery.retry)
  try{
   core.connect();await vi.advanceTimersByTimeAsync(60_000)
   expect(fetch).toHaveBeenCalledTimes(4)
   expect(core.runtimeConnectionStatus).toBe(Status.Error)
   fetch.mockImplementation(async()=>info())
   retryChatConnection();await vi.advanceTimersByTimeAsync(1)
   expect(core.runtimeConnectionStatus).toBe(Status.Connected)
   expect(fetch).toHaveBeenCalledTimes(5)
   retryChatConnection();await vi.advanceTimersByTimeAsync(60_000)
   expect(fetch).toHaveBeenCalledTimes(5)
  }finally{unregister();recovery.dispose()}
 })
 it('bounds stalled initial discovery and permits manual recovery after its budget',async()=>{
  const fetch=vi.fn().mockImplementation(()=>new Promise(()=>{}))
  vi.stubGlobal('fetch',fetch)
  const {core,recovery}=setup()
  try{
   core.connect();await vi.advanceTimersByTimeAsync(40_000)
   expect(fetch).toHaveBeenCalledTimes(4)
   expect(core.runtimeConnectionStatus).toBe(Status.Disconnected)
   fetch.mockImplementation(async()=>info())
   recovery.retry();await vi.advanceTimersByTimeAsync(1)
   expect(core.runtimeConnectionStatus).toBe(Status.Connected)
  }finally{recovery.dispose()}
 })
})
