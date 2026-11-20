import { expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

export async function auditAccessibility(page: Page) {
  const firefox = page.context().browser()?.browserType().name() === "firefox";
  if (firefox) {
    // The Dashboard is a single-frame application. An unexpected frame must fail the audit.
    expect(page.frames()).toHaveLength(1);
    await expect(page.locator("iframe,frame")).toHaveCount(0);
  }
  return new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
}
