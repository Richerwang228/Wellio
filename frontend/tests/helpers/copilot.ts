import {HttpAgent} from '@ag-ui/client'
import {registerChatTransport} from '../../src/lib/api-client'
import {createCopilotTransport} from '../../src/lib/copilot-transport'
import type {ChatEvent,ChatRequest} from '../../src/lib/contracts'

export function installTestTransport(){
 const transport=createCopilotTransport(new HttpAgent({url:'http://testserver/api/copilotkit/agent/wellio/run'}))
 const unregister=registerChatTransport(transport.chat)
 return async()=>{unregister();await transport.dispose()}
}

export function aguiResponse(events:ChatEvent[],request:ChatRequest){
 const last=events.at(-1)
 const wire:unknown[]=[{type:'RUN_STARTED',runId:request.requestId,threadId:request.conversationId},...events.map(value=>({type:'CUSTOM',name:'wellio',value}))]
 if(last?.type==='error')wire.push({type:'RUN_ERROR',message:last.errorCode,code:last.errorCode})
 else if(last?.type==='done'||last?.type==='check_result')wire.push({type:'RUN_FINISHED',runId:request.requestId,threadId:request.conversationId})
 return new Response(wire.map(event=>'data: '+JSON.stringify(event)+'\n\n').join(''),{headers:{'Content-Type':'text/event-stream'}})
}
