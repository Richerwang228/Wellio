import {isDemoMode} from './runtime-mode'
import {useEffect, type ReactNode} from 'react'
import {CopilotKit, useAgent, useCopilotKit} from '@copilotkit/react-core/v2'
import {isPreviewMode, registerChatRecovery, registerChatTransport} from './api-client'
import {createCopilotTransport, installCopilotRecovery} from './copilot-transport'

function TransportBridge({children}:{children:ReactNode}){
 const {copilotkit}=useCopilotKit()
 const {agent,isReady}=useAgent({agentId:'wellio',updates:[]})
 useEffect(()=>{
  const recovery=installCopilotRecovery(copilotkit)
  const unregister=registerChatRecovery(recovery.retry)
  return()=>{unregister();recovery.dispose()}
 },[copilotkit])
 useEffect(()=>{
  if(!isReady)return
  const transport=createCopilotTransport(agent)
  const unregister=registerChatTransport(transport.chat)
  return()=>{unregister();void transport.dispose()}
 },[agent,isReady])
 return children
}

export function WellioCopilotProvider({children}:{children:ReactNode}){
 if(isDemoMode() || (import.meta.env.DEV&&isPreviewMode()))return children
 return <CopilotKit runtimeUrl="/api/copilotkit" agent="wellio" credentials="same-origin" useSingleEndpoint={false} enableInspector={false} showDevConsole={false}><TransportBridge>{children}</TransportBridge></CopilotKit>
}
