import {spawn} from 'node:child_process'
import {resolve} from 'node:path'
import {createInterface} from 'node:readline'
import {setTimeout as delay} from 'node:timers/promises'
import type {ActionRequest,ActionResult,Snapshot} from '../../src/lib/contracts'

/** Test real HTTP receipts instead of keeping a second SQLite backend in the UI. */
export async function storage(){
 const directory=resolve(process.env.WELLIO_BACKEND_DIR||'../wellio-backend')
 const child=spawn('uv',['run','--frozen','--project',directory,'python',resolve(directory,'tests/frontend_fixture_server.py')],{cwd:directory,stdio:['ignore','pipe','pipe']})
 const closed=new Promise<void>(done=>child.once('exit',()=>done()))
 let stderr=''
 child.stderr.on('data',chunk=>{stderr+=String(chunk)})
 const close=async()=>{if(child.exitCode===null&&child.signalCode===null)child.kill('SIGTERM');await closed}
 try{
  const url=await new Promise<string>((yes,no)=>{
   const timer=setTimeout(()=>no(new Error('Test backend startup timeout')),15000)
   const lines=createInterface({input:child.stdout})
   lines.on('line',line=>{try{const data=JSON.parse(line);if(data.url){clearTimeout(timer);yes(data.url)}}catch{}})
   child.once('error',error=>{clearTimeout(timer);no(error)})
   child.once('exit',()=>{clearTimeout(timer);no(new Error(stderr||'Test backend exited'))})
  })
  for(let i=0;i<100;i++){try{if((await fetch(url+'/healthz')).ok)break}catch{}await delay(30)}
  const response=await fetch(url+'/api/state')
  if(!response.ok)throw new Error('Fixture session failed')
  const cookie=response.headers.get('set-cookie')!.split(';')[0]
  const seed=await response.json() as Snapshot
  return {seed,close,
   async state(){return await (await fetch(url+'/api/state',{headers:{cookie}})).json() as Snapshot},
   async action(request:ActionRequest){
    const response=await fetch(url+'/api/actions',{method:'POST',headers:{cookie,'content-type':'application/json'},body:JSON.stringify(request)})
    if(!response.ok)throw new Error('Action failed: '+await response.text())
    return await response.json() as ActionResult
   },
  }
 }catch(error){await close();throw error}
}
