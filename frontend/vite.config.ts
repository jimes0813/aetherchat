import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  base: './',
  plugins: [react()],
  build: {
    sourcemap: 'hidden',
    chunkSizeWarningLimit: 400,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (
            id.includes('node_modules/react-dom')
            || id.includes('node_modules/react/')
            || id.includes('node_modules/scheduler')
            || id.includes('node_modules/zustand')
          ) {
            return 'react-vendor';
          }
          if (id.includes('node_modules/lucide-react')) {
            return 'icons';
          }
          return undefined;
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/ws': 'http://localhost:8000',
      '/api': 'http://localhost:8000'
    }
  }
})
