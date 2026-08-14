import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('.', import.meta.url))

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist-remote',
    emptyOutDir: true,
    modulePreload: { polyfill: false },
    rollupOptions: { input: resolve(root, 'remote.html') },
  },
})
