import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    sourcemap: false,
  },
  server: {
    host: true,
    proxy: {
      // Long-lived SSE under /api/streams/*; avoid proxy/client timeouts
      // during slow tool calls. The ``configure`` hook makes chat replies
      // *type* on phones: we set ``TCP_NODELAY`` on both legs of the
      // proxy so node-http-proxy doesn't batch the small ~30-100 B SSE
      // events while waiting for the next ACK. (The matching kernel-side
      // patch lives in ``orchestrator/api.py``.)
      //
      // IMPORTANT: do NOT call ``res.setHeader()`` / ``res.flushHeaders()``
      // inside the ``proxyRes`` listener. That listener fires *before*
      // http-proxy copies the upstream response headers onto ``res``;
      // flushing here freezes the downstream headers with whatever was
      // set so far, which means the browser sees the Node default
      // ``Content-Type: application/octet-stream`` instead of the
      // ``text/event-stream`` that FastAPI sent. ``EventSource`` then
      // refuses to connect with the error
      //
      //   EventSource's response has a MIME type ("application/octet-stream")
      //   that is not "text/event-stream". Aborting the connection.
      //
      // The upstream FastAPI response already carries ``X-Accel-Buffering:
      // no`` and ``Cache-Control: no-cache, no-store, no-transform``, and
      // http-proxy will copy those over for us. All we need to add at the
      // proxy layer is ``TCP_NODELAY`` for latency.
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        timeout: 0,
        proxyTimeout: 0,
        configure: (proxy) => {
          proxy.on('proxyReq', (proxyReq) => {
            const sock = proxyReq.socket as
              | (import('net').Socket & { setNoDelay?: (on: boolean) => void })
              | null
            sock?.setNoDelay?.(true)
          })
          proxy.on('proxyRes', (proxyRes, _req, res) => {
            const isStream =
              (proxyRes.headers['content-type'] || '').includes('text/event-stream')
            if (!isStream) return
            // Disable Nagle on the downstream socket so each SSE event
            // hits the Wi-Fi router as a separate TCP packet rather than
            // being batched by the kernel. setNoDelay is a *socket
            // option* — it does not touch headers, so it's safe to call
            // before http-proxy copies the upstream Content-Type.
            const downstream = (res as unknown as {
              socket?: import('net').Socket & { setNoDelay?: (on: boolean) => void }
            }).socket
            downstream?.setNoDelay?.(true)
          })
        },
      },
    },
  },
})
