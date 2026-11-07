import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In development, Vite serves the frontend and forwards /api to the backend.
// In the container, the backend serves the built files itself.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': process.env.PLANLAYER_API_URL ?? 'http://localhost:8000',
    },
  },
})
