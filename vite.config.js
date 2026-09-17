import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import localWirelessProxy from './scripts/local-wireless-proxy.js'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), localWirelessProxy()],
  server: {
    watch: {
      ignored: ['**/.venv/**', '**/venv/**', '**/__pycache__/**'],
    },
  },
})
