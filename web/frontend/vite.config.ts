import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  publicDir: mode === 'mock' ? 'public' : false,
  build: { outDir: '../../work/pr09a/dist', emptyOutDir: true },
  server: { host: '127.0.0.1', strictPort: true },
  preview: { host: '127.0.0.1', strictPort: true },
  define: { __BUILD_ID__: JSON.stringify('web-alpha-0.1.0-pr09a') },
}))
