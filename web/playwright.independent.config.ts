import config from "./playwright.config";
import { defineConfig } from "@playwright/test";
export default defineConfig({
  ...config,
  outputDir: "test-results/independent-browser",
  reporter: [["list"]],
  testMatch: "**/independent-review.spec.ts",
  projects: config.projects!.filter(
    (project) => !project.name!.startsWith("real-core"),
  ),
  webServer: [
    Array.isArray(config.webServer) ? config.webServer[0] : config.webServer!,
  ],
});
