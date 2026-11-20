import { expect, test } from "@playwright/test";
test("draft filter edits do not issue an unapplied knowledge query", async ({
  page,
  request,
}) => {
  await request.post("https://localhost:9443/__test__/reset");
  await page.goto("https://localhost:9443/dashboard/");
  await page
    .getByLabel("Dashboard read-only credential")
    .fill("browser-fixture-credential-0000000000000000");
  await page.getByRole("button", { name: "Open your vault" }).click();
  await expect(page.locator(".record-card")).toHaveCount(50);
  const queries: string[] = [];
  page.on("request", (request) => {
    if (request.url().endsWith("/dashboard/api/v1/assertions/query"))
      queries.push(request.method());
  });
  await page
    .getByLabel("Topic", { exact: true })
    .pressSequentially("backups", { delay: 50 });
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  console.log("UNAPPLIED_DRAFT_QUERY_COUNT", queries.length);
  expect(queries).toHaveLength(0);
});
