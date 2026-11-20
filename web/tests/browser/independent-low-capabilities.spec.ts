import { expect, test } from "@playwright/test";
test("independent: a compatible ten-item core cap supports Explorer and Portal", async ({
  page,
}, info) => {
  test.skip(
    !info.project.name.startsWith("low-capabilities"),
    "Requires isolated ten-item capability fixture",
  );
  const responses: { path: string; status: number }[] = [];
  page.on("response", (response) => {
    if (response.url().includes("/dashboard/api/"))
      responses.push({
        path: new URL(response.url()).pathname,
        status: response.status(),
      });
  });
  await page.goto("https://localhost:9443/dashboard/");
  await page
    .getByLabel("Dashboard read-only credential")
    .fill("browser-fixture-credential-0000000000000000");
  await page.getByRole("button", { name: "Open your vault" }).click();
  await expect(
    page.getByRole("heading", { name: "Knowledge Explorer" }),
  ).toBeVisible();
  await expect(
    page.locator(".record-card"),
    JSON.stringify(responses),
  ).toHaveCount(10);
  await page.goto(
    "https://localhost:9443/dashboard/assertions/00000000-0000-0000-0000-000000000001",
  );
  await expect(page.locator(".assertion-content")).toBeVisible();
  await page.getByRole("button", { name: "Sources", exact: true }).click();
  for (const [n, count] of [10, 10, 10, 10, 3].entries()) {
    await expect(page.locator(".child-list .sub-card")).toHaveCount(count);
    if (n < 4)
      await page
        .getByRole("button", { name: "Next page", exact: true })
        .click();
  }
  await page
    .getByRole("button", { name: "Relationships", exact: true })
    .click();
  await expect(page.locator(".child-list .sub-card")).toHaveCount(10);
  await page
    .getByRole("button", { name: "Temporal evidence", exact: true })
    .click();
  await page.getByRole("button", { name: /homelab \/ backup/ }).click();
  await expect(
    page.getByRole("heading", { name: "Supported value" }),
  ).toBeVisible();
  await expect(page.locator(".raw-evidence details")).toHaveCount(10);
  await page.getByRole("link", { name: "Portal", exact: true }).click();
  await expect(page.locator(".catalog-card")).toHaveCount(2);
  expect(responses.filter((response) => response.status === 422)).toEqual([]);
});
