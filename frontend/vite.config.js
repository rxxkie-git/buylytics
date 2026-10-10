import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.VITE_DEV_API_TARGET || "http://127.0.0.1:8000";

  const proxy = {
    target,
    changeOrigin: true,
  };

  const sharedWithSpa = {
    ...proxy,
    bypass: (req) => ((req.headers.accept || "").includes("text/html") ? "/index.html" : undefined),
  };

  return {
    plugins: [react()],
    server: {
      host: "0.0.0.0",
      port: 3000,
      proxy: {
        "/api": proxy,
        "/upload": sharedWithSpa,
        "/upload-product": proxy,
        "/dashboard": sharedWithSpa,
        "/preview": sharedWithSpa,
        "/results": sharedWithSpa,
        "/insights": sharedWithSpa,
        "/product-analysis": sharedWithSpa,
        "/inventory": sharedWithSpa,
      },
    },
    preview: {
      host: "0.0.0.0",
      port: 4173,
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks: {
            react: ["react", "react-dom", "react-router-dom"],
            mui: ["@mui/material", "@mui/icons-material"],
            charts: ["recharts"],
          },
        },
      },
      chunkSizeWarningLimit: 900,
    },
  };
});
