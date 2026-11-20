import { expect, test } from "@playwright/test";

test("Explorer and Portal inspect actual PR1 services through PR2 BFF and disposable pgvector", async ({
  page,
}) => {
  const traffic: string[] = [];
  page.on("request", (request) => traffic.push(request.url()));
  await page.goto("https://localhost:9445/dashboard/");
  await page
    .getByLabel("Dashboard read-only credential")
    .fill("browser-fixture-credential-0000000000000000");
  await page.getByRole("button", { name: "Open your vault" }).click();
  await expect(page.locator(".record-card")).toHaveCount(9);
  await page
    .getByRole("link", {
      name: "Nightly backup configuration retained from operator evidence.",
    })
    .click();
  await page.getByRole("button", { name: "Sources", exact: true }).click();
  await expect(page.locator(".child-list .sub-card")).toHaveCount(20);
  await page.getByRole("button", { name: "Next page", exact: true }).click();
  await expect(page.locator(".child-list .sub-card")).toHaveCount(20);
  await page.getByRole("button", { name: "Next page", exact: true }).click();
  await expect(page.locator(".child-list .sub-card")).toHaveCount(3);
  await page.getByRole("button", { name: "Artifacts", exact: true }).click();
  await page.getByRole("button", { name: "Read text", exact: true }).click();
  await expect(page.getByLabel("Artifact text")).toContainText("<script>");
  await expect(page.locator(".artifact-text img")).toHaveCount(0);
  await page
    .getByRole("button", { name: "Relationships", exact: true })
    .click();
  await expect(page.getByText("depends on", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "Temporal evidence", exact: true })
    .click();
  await page.getByRole("button", { name: /homelab \/ backup/ }).click();
  await expect(
    page.getByRole("heading", { name: "Raw immutable evidence" }),
  ).toBeVisible();
  await expect(page.locator(".raw-evidence details")).toHaveCount(1);
  await page.getByRole("link", { name: "Portal", exact: true }).click();
  await page.getByRole("link", { name: "backups", exact: true }).click();
  await expect(page.locator(".record-card")).toHaveCount(4);
  await page.getByLabel("Record section").selectOption("Rejected options");
  await expect(page.locator(".record-card")).toHaveCount(1);
  await expect(page.locator(".record-card")).toContainText("Rejected option");
  await page.getByRole("link", { name: "Back to Portal" }).click();
  await page.getByRole("button", { name: "contexts", exact: true }).click();
  await page
    .getByRole("link", { name: "Homelab operations", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Homelab operations" }),
  ).toBeVisible();
  await expect(page.locator(".record-card")).toHaveCount(4);
  expect(
    traffic.every((url) => new URL(url).origin === "https://localhost:9445"),
  ).toBe(true);
});
