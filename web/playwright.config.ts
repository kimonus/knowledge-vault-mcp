import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/browser",
  testMatch: "**/*.spec.ts",
  workers: 1,
  fullyParallel: false,
  timeout: 30000,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: "https://localhost:9443",
    ignoreHTTPSErrors: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      testIgnore: [
        "**/real-core.spec.ts",
        "**/independent-low-capabilities.spec.ts",
      ],
      use: { ...devices["Desktop Chrome"], channel: "chromium" },
    },
    {
      name: "firefox",
      grepInvert:
        /desktop and mobile views pass WCAG checks and keyboard navigation/,
      testIgnore: [
        "**/real-core.spec.ts",
        "**/independent-low-capabilities.spec.ts",
      ],
      use: { ...devices["Desktop Firefox"] },
    },
    {
      // Keep the unchanged Axe sequence in a fresh Firefox process: repeated controlled
      // Access→Axe runs crash natively, while fresh-process scans pass. No rules/retries change.
      name: "firefox-accessibility",
      testMatch: "**/dashboard.spec.ts",
      grep: /desktop and mobile views pass WCAG checks and keyboard navigation/,
      use: { ...devices["Desktop Firefox"] },
    },
    {
      name: "real-core",
      testMatch: "**/real-core.spec.ts",
      use: {
        ...devices["Desktop Chrome"],
        channel: "chromium",
        baseURL: "https://localhost:9445",
      },
    },
    {
      name: "real-core-firefox",
      testMatch: "**/real-core.spec.ts",
      use: { ...devices["Desktop Firefox"], baseURL: "https://localhost:9445" },
    },
  ],
  webServer: [
    {
      command: "../dashboard/.venv/bin/python tests/browser/server.py",
      url: "https://localhost:9443/dashboard/",
      ignoreHTTPSErrors: true,
      reuseExistingServer: false,
      timeout: 30000,
    },
    {
      command: "../.venv/bin/python tests/browser/real_server.py",
      url: "https://localhost:9445/dashboard/",
      ignoreHTTPSErrors: true,
      reuseExistingServer: false,
      timeout: 120000,
    },
  ],
});
