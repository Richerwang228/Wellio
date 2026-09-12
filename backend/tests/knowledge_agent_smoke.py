"""Opt-in live Agent/knowledge smoke. Reads only the .env API key, never its DB URL."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from uuid import uuid4
import httpx
from dotenv import dotenv_values
from pg_cluster import temporary_postgres
from production_smoke import unused_port
from wellio.knowledge import KnowledgeStore, EmbeddingConfig
BACKEND=Path(__file__).resolve().parents[1]


def main():
    if os.environ.get('WELLIO_KNOWLEDGE_LIVE')!='1':raise SystemExit('Set WELLIO_KNOWLEDGE_LIVE=1 to authorize live API calls')
    key=os.environ.get('OPENROUTER_API_KEY') or dotenv_values(BACKEND/'.env').get('OPENROUTER_API_KEY')
    if not key:raise SystemExit('OPENROUTER_API_KEY_REQUIRED')
    corpus=BACKEND/'.data/knowledge/nutrition-v1/releases/demo-6c1a7913caf33172c82b3482'
    bundle=json.loads((corpus/'bundle.json').read_text());cached=json.loads((corpus/'vectors.json').read_text());runtime=BACKEND/'agent-runtime'
    with tempfile.TemporaryDirectory(prefix='wellio-knowledge-build-',dir=runtime/'node_modules') as build, temporary_postgres() as url, tempfile.TemporaryDirectory(prefix='wellio-knowledge-uploads-') as uploads:
        build=Path(build)
        subprocess.run([str(runtime/'node_modules/.bin/tsc'),'-p',str(runtime/'tsconfig.json'),'--outDir',str(build)],check=True,capture_output=True,text=True)
        (build/'prompts').mkdir();shutil.copyfile(runtime/'src/prompts/wellio.md',build/'prompts/wellio.md');(build/'package.json').write_text('{"type":"module"}')
        # Observe timings/status only; never log authorization headers or model bodies.
        (build/'live-entry.mjs').write_text('''
import {serve} from './server.js';
import {configuredModel} from './model.js';
import {createAgentRuntime} from './runtime.js';
const transport=async(input,init)=>{const started=Date.now();console.log('MODEL_REQUEST_START');try{const response=await fetch(input,init);console.log(JSON.stringify({event:'MODEL_HTTP',status:response.status,ms:Date.now()-started}));return response;}catch(e){console.log(JSON.stringify({event:'MODEL_FETCH_FAILED',name:e.name,code:e.cause?.code,ms:Date.now()-started}));throw e;}};
const runtime=createAgentRuntime({backendUrl:process.env.WELLIO_API_BASE_URL,token:process.env.WELLIO_AGENT_TOKEN,model:configuredModel(process.env,transport)});
const server=await serve(runtime,{port:Number(process.env.WELLIO_AGENT_PORT)});
const close=async()=>{server.close();await runtime.close();server.closeAllConnections()};
process.once('SIGTERM',()=>{void close()});process.once('SIGINT',()=>{void close()});
''')
        store=KnowledgeStore(url);store.initialize();store.import_release(bundle['releaseId'],EmbeddingConfig(),bundle['documents'],bundle['chunks'],[cached['vectors'][c['id']] for c in bundle['chunks']]);store.activate(bundle['releaseId'])
        api_port,agent_port=unused_port(),unused_port();base=f'http://127.0.0.1:{agent_port}';api=f'http://127.0.0.1:{api_port}'
        env={**os.environ,'DATABASE_URL':url,'OPENROUTER_API_KEY':key,'EXA_API_KEY':'','WELLIO_ATTACHMENTS_PATH':uploads,'WELLIO_AGENT_TOKEN':'live-smoke-private-token','WELLIO_API_BASE_URL':api,'WELLIO_AGENT_PORT':str(agent_port),'HOST':'127.0.0.1','WELLIO_PUBLIC_ORIGIN':base,'COPILOTKIT_TELEMETRY_DISABLED':'true'}
        children=[]
        with tempfile.TemporaryFile(mode='w+') as log:
            try:
                for command in [[sys.executable,'-m','uvicorn','wellio.main:application','--factory','--host','127.0.0.1','--port',str(api_port),'--no-proxy-headers'],['node',str(build/'live-entry.mjs')]]:
                    children.append(subprocess.Popen(command,cwd=BACKEND,env=env,stdout=log,stderr=log))
                for ready in [api+'/healthz',base+'/healthz']:
                    for _ in range(100):
                        try:
                            if httpx.get(ready,timeout=.3).status_code==200:break
                        except httpx.HTTPError:pass
                        if any(p.poll() is not None for p in children):raise AssertionError('CHILD_START_FAILED')
                        time.sleep(.1)
                    else:raise AssertionError('CHILD_START_TIMEOUT')
                with httpx.Client(headers={'Origin':base},timeout=130) as client:
                    snapshot=client.get(api+'/api/state').json()
                    request={'requestId':str(uuid4()),'resetEpoch':snapshot['resetEpoch'],'conversationId':snapshot['conversationId'],'source':'user','locale':'zh-CN','message':'解释睡眠不足对运动表现的影响，给一句谨慎建议即可，不需要生成或修改训练计划。','attachmentIds':[]}
                    start=time.perf_counter()
                    try:
                        response=client.post(base+'/api/copilotkit/agent/wellio/run',json={'threadId':request['conversationId'],'runId':request['requestId'],'messages':[],'state':{},'tools':[],'context':[],'forwardedProps':{'wellio':request}})
                    except httpx.HTTPError:
                        log.seek(0);print(log.read().replace(key,'[redacted]')[-2000:],flush=True)
                        try:
                            failed=client.get(api+'/api/state',timeout=3).json()['messages'][-1]
                            print(json.dumps({k:failed.get(k) for k in ('status','errorCode','steps')},ensure_ascii=False),flush=True)
                        except Exception: print('Snapshot unavailable after stream failure',flush=True)
                        raise
                    saved=client.get(api+'/api/state').json();message=saved['messages'][-1]
                    report={'httpStatus':response.status_code,'elapsedMs':round((time.perf_counter()-start)*1000),'messageStatus':message['status'],'errorCode':message.get('errorCode'),'steps':[{k:s.get(k) for k in ('operation','status','errorCode')} for s in message['steps']],'sourceDocumentIds':[s['documentId'] for s in message.get('sources',[])],'knowledgeVersion':message.get('knowledgeVersion'),'markdown':message['content'],'transport':'Live CopilotKit BuiltInAgent + live OpenRouter chat/embeddings + dedicated temporary PostgreSQL; no frontend browser.'}
                    report['passed']=bool(message['status']=='complete' and message.get('sources') and any(s['operation']=='knowledge_search' and s['status']=='succeeded' for s in message['steps']) and saved['advice'].get('evidenceReadId')==message.get('evidenceReadId'))
                    (corpus/'agent-smoke.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
                    print(json.dumps({k:v for k,v in report.items() if k!='markdown'},ensure_ascii=False),flush=True)
                    if not report['passed']:
                        log.seek(0);print(log.read().replace(key,'[redacted]')[-2500:],flush=True)
                        raise AssertionError('LIVE_KNOWLEDGE_AGENT_SMOKE_FAILED')
            finally:
                for child in reversed(children):
                    if child.poll() is None:child.terminate()
                    try:child.wait(timeout=6)
                    except subprocess.TimeoutExpired:child.kill();child.wait()

if __name__=='__main__':main()
