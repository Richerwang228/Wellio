import { createFileRoute } from '@tanstack/react-router'
import { handleCopilotRequest } from '../server/copilot-proxy'

export const Route = createFileRoute('/api/copilotkit/$')({
  server: { handlers: { ANY: ({ request }) => handleCopilotRequest(request) } },
})
