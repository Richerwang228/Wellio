import { createFileRoute } from '@tanstack/react-router'
import { handleBackendRequest } from '../server/api-proxy'

export const Route = createFileRoute('/api/demo-plan')({
  server: { handlers: { POST: ({ request }) => handleBackendRequest(request) } },
})
