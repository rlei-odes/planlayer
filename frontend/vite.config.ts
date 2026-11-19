import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In development, Vite serves the frontend and forwards /api to the backend.
// In the container, the backend serves the built files itself.
export default defineConfig({
  plugins: [react()],
  server: {
    // Always 5173: fail if it is taken rather than moving to the next free port.
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': process.env.PLANLAYER_API_URL ?? 'http://localhost:8000',
    },
  },
})
