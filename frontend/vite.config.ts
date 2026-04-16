import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    sourcemap: false,
  },
  server: {
    proxy: {
      // Long-lived SSE (/chat); avoid proxy/client timeouts during flow analysis
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        timeout: 0,
        proxyTimeout: 0,
      },
    },
  },
})
