/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        // Docker 内联:VITE_PROXY_TARGET=http://backend:8000;本机默认为 localhost:8000
        // 保留 /api 前缀:面板请求 /api/v1/* 与 /api/analysis_payload/*。
        // ws:true 让 /api/v1/ws/events(WebSocket 实时事件)同样走代理。
        target: process.env.VITE_PROXY_TARGET ?? 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
  },
})
