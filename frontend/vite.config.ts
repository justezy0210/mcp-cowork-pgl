import { fileURLToPath, URL } from "node:url"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"

export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  build: { outDir: "../src/cowork_hub/web/dist", emptyOutDir: true, sourcemap: false },
  server: {
    proxy: {
      "/config.json": { target: "http://127.0.0.1:8080", rewrite: () => "/web/config.json" },
      "/v1/web": "http://127.0.0.1:8080",
    },
  },
})
