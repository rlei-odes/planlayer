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
      // The browser's Host is kept, so the backend builds addresses the browser can open, such
      // as the OpenID Connect redirect URI (http://localhost:5173/api/auth/oidc/callback).
      '/api': { target: process.env.PLANLAYER_API_URL ?? 'http://localhost:8000', changeOrigin: false },
    },
  },
})
