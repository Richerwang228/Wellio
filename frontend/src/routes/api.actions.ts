import { createFileRoute } from '@tanstack/react-router'
import { handleBackendRequest } from '../server/api-proxy'

export const Route = createFileRoute('/api/actions')({
  server: { handlers: { ANY: ({ request }) => handleBackendRequest(request) } },
})
