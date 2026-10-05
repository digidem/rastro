import { defineConfig, loadEnv } from "vite";
import solidPlugin from "vite-plugin-solid";
import tsconfigPaths from "vite-tsconfig-paths";
import { mockApiPlugin } from "./vite-plugin-mock-api.js";

const SOLID_JS_RE = /solid-js/;
const SOLID_TESTING_RE = /@solidjs\/testing-library/;

// D7: config do upstream mantida, MENOS o hash de git e o plugin de environment
// (offline: nada de dependência de git/CDN no build) e porta 5173 (a 3000
// conflita com outros serviços da bancada).
// Suporte ao modo mock local (--mode mock ou VITE_DEV_MOCK=true) para
// desenvolvimento exclusivo da UI do mapa sem dependência do backend.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const isMock =
    mode === "mock" ||
    env.VITE_DEV_MOCK === "true" ||
    process.env.VITE_DEV_MOCK === "true";

  return {
    plugins: [
      solidPlugin(),
      tsconfigPaths({ root: "./" }),
      ...(isMock ? [mockApiPlugin()] : []),
    ],
    server: { host: process.env.VITE_HOST ?? "127.0.0.1", port: 5173 },
    build: { target: "esnext" },
    test: {
      environment: "jsdom",
      include: ["tests/**/*.test.ts", "src/**/*.test.{ts,tsx}"],
      exclude: ["e2e/**", "node_modules/**"],
      deps: { inline: [SOLID_JS_RE, SOLID_TESTING_RE] },
    },
  };
});
