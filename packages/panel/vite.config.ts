import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  build: {
    target: "esnext",
    rollupOptions: {
      output: {
        manualChunks: {
          echarts: ["echarts/core", "echarts/charts", "echarts/components", "echarts/renderers"],
          vendor: ["vue", "vue-router", "pinia", "vue-i18n", "reka-ui", "@vueuse/core"],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_API_ORIGIN || "http://127.0.0.1:8000",
        changeOrigin: false,
      },
      "/healthz": {
        target: process.env.VITE_API_ORIGIN || "http://127.0.0.1:8000",
        changeOrigin: false,
      },
    },
  },
});
