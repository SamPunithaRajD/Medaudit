import { defineConfig } from 'vite'; import react from '@vitejs/plugin-react';
// /api is proxied to the FastAPI backend in development so the session cookie is first-party.
const apiTarget = process.env.VITE_PROXY_TARGET || 'http://localhost:8000';
export default defineConfig({ plugins: [react()], build: { rollupOptions: { output: { manualChunks(id) { if (id.includes('node_modules/recharts')) return 'charts'; if (id.includes('node_modules/framer-motion')) return 'motion'; if (id.includes('node_modules/lucide-react')) return 'icons'; if (id.includes('node_modules/jszip')) return 'archive'; if (id.includes('node_modules/')) return 'vendor'; } } } }, server: { port: 5173, proxy: { '/api': { target: apiTarget, changeOrigin: false } } } });
