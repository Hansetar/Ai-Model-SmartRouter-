import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  base: '/admin/',
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 3000,
    proxy: {
      '/v1': 'http://localhost:8000',
      '/admin/api': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks: {
          // 将 echarts 单独拆包（最大的依赖）
          echarts: ['echarts'],
          // 将 react 核心库单独拆包
          'react-vendor': ['react', 'react-dom', 'react-router-dom'],
          // 将 axios 单独拆包
          axios: ['axios'],
        },
      },
    },
  },
});
