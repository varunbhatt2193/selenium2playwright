import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// `npm run dev` serves the page on :5173 and forwards every /api call to the
// FastAPI server on :8501, so the two halves can be developed against each
// other without a build. In production there is one origin: ui/server.py
// serves dist/ and the API from the same port.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: 'http://127.0.0.1:8501', changeOrigin: true },
      '/ok': { target: 'http://127.0.0.1:8501', changeOrigin: true },
    },
  },
  build: { outDir: 'dist', sourcemap: false },
})
