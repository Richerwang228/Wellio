import { defineConfig } from 'vite'
import { tanstackStart } from '@tanstack/react-start/plugin/vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { nitro } from 'nitro/vite'
import { fileURLToPath } from 'node:url'
const outputDir = process.env.WELLIO_BUILD_DIR || (process.env.VITE_WELLIO_MODE === 'demo' ? '.output-demo' : undefined)
export default defineConfig({
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  // CopilotKit v2 imports CSS; let Vite process it during SSR rather than Node.
  ssr: { noExternal: [/^@copilotkit\//] },
  plugins: [tailwindcss(), tanstackStart(), nitro({ preset: 'node-server', ...(outputDir ? { output: { dir: outputDir, serverDir: `${outputDir}/server`, publicDir: `${outputDir}/public` } } : {}) }), react()],
  server: { host: '127.0.0.1', port: 3100 },
})
