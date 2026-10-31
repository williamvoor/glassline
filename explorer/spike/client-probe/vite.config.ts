import {defineConfig} from 'vite';
export default defineConfig({
  resolve: {dedupe: ['lit']},
  optimizeDeps: {include: ['vega-embed']},
  build: {target: 'esnext'},
  server: {host: '127.0.0.1', port: 5199, strictPort: true},
});
