/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// APEX Control Center — le frontend n'accède JAMAIS à PostgreSQL ni au Core
// directement : tout passe par l'API (apex/api), proxyée ici en dev pour
// éviter tout souci de CORS. En production, VITE_APEX_API_BASE_URL pointe
// vers l'API réelle (voir src/api/client.ts).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api/v1": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    // theme.css est lu tel quel par theme/contrast.test.ts (contrastes mesurés sur le vrai fichier).
    css: { include: [/theme\.css/] },
  },
});
