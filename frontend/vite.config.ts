import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  base: './',
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/ws': 'http://localhost:8000',
      '/api': 'http://localhost:8000'
    }
  }
})
