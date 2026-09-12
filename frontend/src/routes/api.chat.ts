import { createFileRoute } from '@tanstack/react-router'
import { handleBackendRequest } from '../server/api-proxy'

export const Route = createFileRoute('/api/chat')({
  server: { handlers: { ANY: ({ request }) => handleBackendRequest(request) } },
})
