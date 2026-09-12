import { createFileRoute } from '@tanstack/react-router'
import { handleBackendRequest } from '../server/api-proxy'

export const Route = createFileRoute('/api/attachments/$attachmentId')({
  server: { handlers: { ANY: ({ request }) => handleBackendRequest(request) } },
})
