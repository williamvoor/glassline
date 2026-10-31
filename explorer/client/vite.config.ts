/// <reference types="vitest/config" />
import {defineConfig} from 'vite';

// No proxy: the browser calls the agent directly with CORS (ADR 0007).
export default defineConfig({
  resolve: {dedupe: ['lit']},
  optimizeDeps: {include: ['vega-embed']},
  build: {target: 'esnext'},
  server: {host: '127.0.0.1', port: 5173, strictPort: true},
  test: {
    environment: 'happy-dom',
    testTimeout: 30_000,
    hookTimeout: 60_000,
  },
});
