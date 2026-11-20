// Independently authored real-core browser review.
import { expect, test } from "@playwright/test";
const ORIGIN = "https://localhost:9445";
test("independent real core: records, Portal status counts, complete sources and evidence retain exact meaning", async ({
  page,
}) => {
  await page.goto(ORIGIN + "/dashboard/");
  await page
    .getByLabel("Dashboard read-only credential")
    .fill("browser-fixture-credential-0000000000000000");
  await page.getByRole("button", { name: "Open your vault" }).click();
  await expect(page.locator(".record-card")).toHaveCount(9);
  const before = await page.evaluate(async () => {
    const response = await fetch("/dashboard/api/v1/assertions/query", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Dashboard-Request": "1",
      },
      body: JSON.stringify({
        mode: "all",
        filters: { statuses: [] },
        limit: 50,
      }),
    });
    return response.json();
  });
  expect(
    new Set(before.items.map((item: { status: string }) => item.status)),
  ).toEqual(new Set(["current", "uncertain", "disputed", "superseded"]));
  const primary = before.items.find(
    (item: { kind: string; status: string }) =>
      item.kind === "configuration" && item.status === "current",
  );
  await page.goto(ORIGIN + "/dashboard/assertions/" + primary.id);
  await expect(page.locator(".assertion-content")).toContainText(
    "Nightly backup",
  );
  await expect(
    page.getByText(
      "Usage accounting is unavailable; zero counters are not authoritative.",
    ),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Sources", exact: true }).click();
  const sources = new Set<string>();
  for (const count of [20, 20, 3]) {
    await expect(page.locator(".child-list .sub-card")).toHaveCount(count);
    for (const href of await page
      .locator(".child-list h3 a")
      .evaluateAll((links) =>
        links.map((link) => link.getAttribute("href")!),
      )) {
      expect(sources.has(href)).toBe(false);
      sources.add(href);
    }
    if (sources.size < 43)
      await page
        .getByRole("button", { name: "Next page", exact: true })
        .click();
  }
  expect(sources.size).toBe(43);
  await page
    .getByRole("button", { name: "Relationships", exact: true })
    .click();
  await expect(page.getByText("depends on", { exact: true })).toBeVisible();
  await expect(page.locator(".child-list")).toContainText(primary.id);
  await page
    .getByRole("button", { name: "Temporal evidence", exact: true })
    .click();
  await page.getByRole("button", { name: /homelab \/ backup/ }).click();
  await expect(page.locator(".resolution")).toContainText("nightly");
  await expect(page.locator(".raw-evidence details")).toHaveCount(1);
  await page.locator(".raw-evidence summary").click();
  await expect(page.locator(".raw-evidence")).toContainText("snapshot");
  await expect(page.locator(".raw-evidence")).toContainText("day");
  await page.getByRole("link", { name: "Portal", exact: true }).click();
  await page.getByRole("link", { name: "backups", exact: true }).click();
  await expect(page.locator(".record-card")).toHaveCount(4);
  await page
    .getByLabel("Evidence status", { exact: true })
    .selectOption("superseded");
  await expect(page.locator(".record-card")).toHaveCount(1);
  await expect(page.locator(".record-card")).toContainText("superseded");
  await page.getByLabel("Evidence status", { exact: true }).selectOption("");
  await page.getByLabel("Record section").selectOption("Rejected options");
  await expect(page.locator(".record-card")).toHaveCount(1);
  await expect(page.locator(".record-card")).toContainText("Rejected option");
  await page.getByLabel("Record section").selectOption("Plans");
  await expect(page.locator(".record-card")).toHaveCount(1);
  await expect(page.locator(".record-card")).toContainText("proposed");
});
