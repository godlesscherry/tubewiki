import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Backend runs on 8787; everything under /api and /assets is proxied there.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8787',
      '/assets': 'http://127.0.0.1:8787',
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
