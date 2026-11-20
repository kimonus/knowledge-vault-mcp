import config from "./playwright.config";
import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  ...config,
  testMatch: "**/independent-low-capabilities.spec.ts",
  outputDir: "test-results/independent-lowcap",
  reporter: [["list"]],
  projects: [
    {
      name: "low-capabilities-chromium",
      use: { ...devices["Desktop Chrome"], channel: "chromium" },
    },
    {
      name: "low-capabilities-firefox",
      use: { ...devices["Desktop Firefox"] },
    },
  ],
  webServer: {
    command:
      "../dashboard/.venv/bin/python tests/browser/independent_lowcap_server.py",
    url: "https://localhost:9443/dashboard/",
    ignoreHTTPSErrors: true,
    reuseExistingServer: false,
    timeout: 30000,
  },
});
