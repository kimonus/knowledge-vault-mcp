import config from "./playwright.config";
import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  ...config,
  outputDir: "test-results/independent-core",
  reporter: [["list"]],
  testMatch: "**/independent/real-core.spec.ts",
  projects: [
    {
      name: "independent-real-core-chromium",
      use: { ...devices["Desktop Chrome"], channel: "chromium" },
    },
    {
      name: "independent-real-core-firefox",
      use: { ...devices["Desktop Firefox"] },
    },
  ],
  webServer: [
    Array.isArray(config.webServer) ? config.webServer[1] : config.webServer!,
  ],
});
