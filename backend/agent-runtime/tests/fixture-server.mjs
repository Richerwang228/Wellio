/** Test-only entry; production never selects this model by an environment flag. */
import {MockLanguageModelV3} from 'ai/test';
import {simulateReadableStream} from 'ai';
import {createAgentRuntime} from '../dist/runtime.js';
import {serve} from '../dist/server.js';
const usage={inputTokens:{total:12,noCache:12,cacheRead:undefined,cacheWrite:undefined},outputTokens:{total:20,text:20,reasoning:undefined}};
const model=new MockLanguageModelV3({provider:'wellio-fullstack-fixture',modelId:'sdk6-offline',doStream:async options=>{
  let chunks;
  if(options.toolChoice?.type==='tool'&&options.toolChoice.toolName==='get_day_context'){
    const id=`context-${crypto.randomUUID()}`;
    chunks=[{type:'stream-start',warnings:[]},{type:'tool-call',toolCallId:id,toolName:'get_day_context',input:'{}'},{type:'finish',finishReason:{unified:'tool-calls',raw:undefined},usage}];
  }else if(options.toolChoice?.type==='tool'&&options.toolChoice.toolName==='search_expert_knowledge'){
    chunks=[{type:'stream-start',warnings:[]},{type:'tool-call',toolCallId:`knowledge-${crypto.randomUUID()}`,toolName:'search_expert_knowledge',input:JSON.stringify({query:'Sleep and exercise performance'})},{type:'finish',finishReason:{unified:'tool-calls',raw:undefined},usage}];
  }else if(JSON.stringify(options.prompt.filter(message=>message.role==='user').at(-1)).includes('I only ate half of this item') && !options.prompt.some(message=>message.role==='tool'&&message.content.some(part=>part.type==='tool-result'&&part.toolName==='mutate_meal_log'))){
    const id=`meal-${crypto.randomUUID()}`;
    chunks=[{type:'stream-start',warnings:[]},{type:'tool-call',toolCallId:id,toolName:'mutate_meal_log',input:JSON.stringify({action:'update',mealId:'meal-lunch',mealItemId:'item-lunch',changes:{consumedFraction:0.5}})},{type:'finish',finishReason:{unified:'tool-calls',raw:undefined},usage}];
  }else{
    if(JSON.stringify(options.prompt).includes('WAIT_FOR_CANCEL')) await new Promise((_resolve,reject)=>{
      const abort=()=>reject(options.abortSignal?.reason??new Error('cancelled'));
      if(options.abortSignal?.aborted)abort();else options.abortSignal?.addEventListener('abort',abort,{once:true});
    });
    const answer={markdown:'Your saved day context is available. Review the current plan and recorded meals.',trainingSummary:'The saved workout remains unchanged.',nutritionSummary:'The saved meal log remains unchanged.'};
    const evidence=options.prompt.filter(message=>message.role==='tool').flatMap(message=>message.content).filter(part=>part.type==='tool-result'&&part.toolName==='search_expert_knowledge').at(-1)?.output?.value;
    if(evidence) Object.assign(answer,{evidenceReadId:evidence.evidenceReadId,evidenceChunkIds:[evidence.results[0].chunkId]});
    chunks=[{type:'stream-start',warnings:[]},{type:'text-start',id:'json'},{type:'text-delta',id:'json',delta:JSON.stringify(answer)},{type:'text-end',id:'json'},{type:'finish',finishReason:{unified:'stop',raw:undefined},usage}];
  }
  return {stream:simulateReadableStream({chunks,initialDelayInMs:0,chunkDelayInMs:0})};
}});
const runtime=createAgentRuntime({backendUrl:process.env.WELLIO_API_BASE_URL??'http://127.0.0.1:8000',token:process.env.WELLIO_AGENT_TOKEN??'',model});
const server=await serve(runtime,{port:Number(process.env.WELLIO_AGENT_PORT??0)});
const address=server.address();
process.stdout.write(JSON.stringify({url:`http://127.0.0.1:${address.port}`})+'\n');
const close=async()=>{server.close();await runtime.close();server.closeAllConnections()};
process.once('SIGTERM',()=>{void close()});process.once('SIGINT',()=>{void close()});
