import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({
  root: process.cwd(), base: './', plugins: [react()],
  build: { outDir: '/tmp/iife-dist', emptyOutDir: true, modulePreload: false, cssCodeSplit: false,
    rollupOptions: { output: { format: 'iife', inlineDynamicImports: true, entryFileNames: 'app.js', assetFileNames: 'app.[ext]' } } },
});
