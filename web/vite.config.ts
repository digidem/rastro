import { defineConfig } from "vite";
import solidPlugin from "vite-plugin-solid";
import tsconfigPaths from "vite-tsconfig-paths";

// D7: config do upstream mantida, MENOS o hash de git e o plugin de environment
// (offline: nada de dependência de git/CDN no build) e porta 5173 (a 3000
// conflita com outros serviços da bancada).
export default defineConfig({
  plugins: [solidPlugin(), tsconfigPaths({ root: "./" })],
  server: { port: 5173 },
  build: { target: "esnext" },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.ts"],
    exclude: ["e2e/**", "node_modules/**"],
    deps: { inline: [/solid-js/, /@solidjs\/testing-library/] },
  },
});
