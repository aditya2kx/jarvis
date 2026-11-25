import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Same origin as in prod, where nginx forwards /v1 to the Tasks API.
  server: { port: 5175, proxy: { "/v1": process.env.API_URL ?? "http://localhost:8091" } },
  test: {
    environment: "jsdom",
    environmentMatchGlobs: [
      ["**/queue.test.ts", "node"],
      ["**/shopCodeUi.test.ts", "node"],
      ["**/api.shopCodes.test.ts", "node"],
    ],
  },
});
