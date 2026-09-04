import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Session cookies are SameSite=Strict, so the app and the API must share an
// origin. In production the Pi serves this build itself; in development this
// proxy stands in for that, forwarding /api to the Pi so the browser still
// sees a single origin and sends the cookie.
const DEVICE_API = process.env.VITE_DEVICE_API || 'http://127.0.0.1:5000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: DEVICE_API, changeOrigin: false },
      '/health': { target: DEVICE_API, changeOrigin: false },
      '/ready': { target: DEVICE_API, changeOrigin: false },
    },
    watch: {
      ignored: ['**/.venv/**', '**/venv/**', '**/__pycache__/**'],
    },
  },
})
