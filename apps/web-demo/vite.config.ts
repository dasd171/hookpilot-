import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// base './' -> dist/ works from any static host path (or file:// for most browsers).
export default defineConfig({
  base: './',
  plugins: [react()],
  server: { fs: { allow: ['..', '../..'] } },
  build: { outDir: 'dist', sourcemap: false },
  test: { environment: 'node', include: ['tests/**/*.test.ts'] },
} as any);
