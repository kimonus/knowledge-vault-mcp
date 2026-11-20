import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { auditAccessibility } from "./accessibility";
import fixtures from "../fixtures.json" with { type: "json" };

const CREDENTIAL = "browser-fixture-credential-0000000000000000";
const ORIGIN = "https://localhost:9443";
const ACCESS = "https://localhost:9444";
async function signIn(page: Page) {
  await page.goto("/dashboard/");
  await page.getByLabel("Dashboard read-only credential").fill(CREDENTIAL);
  await page.getByRole("button", { name: "Open your vault" }).click();
  await expect(page.locator(".record-card")).toHaveCount(50);
  await expect(
    page.getByRole("heading", { name: "Knowledge Explorer" }),
  ).toBeVisible();
}
test.beforeEach(async ({ request }) => {
  await request.post(ORIGIN + "/__test__/reset");
  await request.post(ACCESS + "/__test__/reset");
});

test("every assertion remains reachable through keyset pages; ranked exhaustion is explicit", async ({
  page,
}) => {
  const traffic: string[] = [];
  page.on("request", (request) => traffic.push(request.url()));
  await signIn(page);
  const ids = new Set<string>();
  for (let index = 1; index <= 5; index++) {
    await expect(
      page.locator(".pager").getByText(`Page ${index}`, { exact: false }),
    ).toBeVisible();
    for (const href of await page
      .locator(".record-card h3 a")
      .evaluateAll((links) => links.map((link) => link.getAttribute("href")!)))
      ids.add(href);
    if (index < 5)
      await page
        .getByRole("button", { name: "Next page", exact: true })
        .click();
  }
  expect(ids.size).toBe(205);
  await expect(
    page.getByRole("button", { name: "Next page", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "Previous", exact: true }).click();
  await expect(
    page.locator(".pager").getByText("Page 4", { exact: false }),
  ).toBeVisible();
  await page.getByLabel("Ranked search").check();
  await page.getByLabel("Search your knowledge").fill("backup");
  await page.getByRole("button", { name: "Apply filters" }).click();
  await expect(page.locator(".record-card")).toHaveCount(2);
  await expect(page.getByText(/End of the ranked candidate set/)).toBeVisible();
  await expect(page.getByText(/Semantic retrieval is degraded/)).toBeVisible();
  expect(traffic.every((url) => new URL(url).origin === ORIGIN)).toBe(true);
  expect(
    traffic.some(
      (url) =>
        new URL(url).pathname.startsWith("/api/v1/") ||
        new URL(url).pathname.startsWith("/mcp"),
    ),
  ).toBe(false);
  expect(new URL(page.url()).search).toBe("");
});

test("filters, reset, detail and back navigation preserve in-memory views", async ({
  page,
}) => {
  await signIn(page);
  await page.getByLabel("Topic", { exact: true }).fill("backups");
  await page
    .getByLabel("Evidence status", { exact: true })
    .selectOption("superseded");
  await page.getByRole("button", { name: "Apply filters" }).click();
  await expect(page.locator(".record-card")).not.toHaveCount(0);
  await page.locator(".record-card h3 a").first().click();
  await expect(
    page.getByRole("heading", { name: "Knowledge & evidence" }),
  ).toBeVisible();
  await page.goBack();
  await expect(page.getByLabel("Topic", { exact: true })).toHaveValue(
    "backups",
  );
  await expect(page.getByLabel("Evidence status", { exact: true })).toHaveValue(
    "superseded",
  );
  await page.getByRole("button", { name: "Reset", exact: true }).click();
  await expect(page.locator(".record-card")).toHaveCount(50);
  await page.getByLabel("Exact text").check();
  await page
    .getByLabel("Words or phrases to match")
    .fill("no-such-synthetic-phrase");
  await page.getByRole("button", { name: "Apply filters" }).click();
  await expect(
    page.getByRole("heading", { name: "No records in this view" }),
  ).toBeVisible();
  expect(new URL(page.url()).search).toBe("");
  await page.screenshot({
    path: "/tmp/knowledge-vault-pr3-explorer.png",
    fullPage: true,
  });
});

test("complete child pages, inert artifacts and relationships remain reachable", async ({
  page,
}) => {
  const remote: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).origin !== ORIGIN) remote.push(request.url());
  });
  await signIn(page);
  await page.goto("/dashboard/assertions/" + fixtures.details[0].id);
  await expect(
    page.getByRole("heading", { name: "Knowledge & evidence" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Sources", exact: true }).click();
  await expect(
    page.getByRole("link", { name: /^Evidence source 19/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Next page", exact: true }).click();
  await expect(
    page.getByRole("link", { name: /^Evidence source 20/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Next page", exact: true }).click();
  await expect(
    page.getByRole("link", { name: /^Evidence source 42/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Artifacts", exact: true }).click();
  await page
    .getByRole("button", { name: "Read text", exact: true })
    .first()
    .click();
  await expect(page.getByLabel("Artifact text")).toContainText("<script>");
  await page.getByRole("button", { name: "Next text", exact: true }).click();
  await expect(page.getByText(/Characters 8000/)).toBeVisible();
  await page
    .getByRole("button", { name: "Previous text", exact: true })
    .click();
  await page.getByRole("button", { name: "Close text", exact: true }).click();
  await page.getByRole("button", { name: "Next page", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "evidence-21.txt" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Relationships", exact: true })
    .click();
  await expect(
    page.getByText("Recorded conflict", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Supersession", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Next page", exact: true }).click();
  await page.getByRole("button", { name: "Next page", exact: true }).click();
  await expect(page.locator(".child-list .sub-card")).toHaveCount(3);
  await page
    .getByRole("button", { name: "Temporal evidence", exact: true })
    .click();
  await page.getByRole("button", { name: "Next page", exact: true }).click();
  await expect(page.getByRole("button", { name: /homelab-034/ })).toBeVisible();
  await page.getByRole("button", { name: "Previous", exact: true }).click();
  await page.getByRole("button", { name: /homelab \/ backup/ }).click();
  await expect(
    page.getByRole("heading", { name: "Supported value" }),
  ).toBeVisible();
  await expect(page.locator(".resolution")).toContainText("day");
  await page
    .locator(".resolution")
    .getByRole("button", { name: "Next page", exact: true })
    .click();
  await expect(
    page.locator(".resolution").getByRole("link", { name: "backup-001" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => (window as unknown as Record<string, unknown>).__vault_xss,
    ),
  ).toBeUndefined();
  expect(remote).toEqual([]);
});

test("Portal uses actual catalogs and explicit context/facet filters", async ({
  page,
}) => {
  await signIn(page);
  await page.getByRole("link", { name: "Portal", exact: true }).click();
  await expect(page.locator(".catalog-card")).toHaveCount(2);
  await page.getByRole("link", { name: "backups", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "backups", exact: true }),
  ).toBeVisible();
  await page.getByLabel("Record section").selectOption("Rejected options");
  await expect(page.locator(".record-card")).not.toHaveCount(0);
  await expect(page.locator(".record-card").first()).toContainText(
    "Rejected option",
  );
  await page.getByRole("link", { name: "Back to Portal" }).click();
  await page.getByRole("button", { name: "contexts", exact: true }).click();
  await page.getByRole("link", { name: fixtures.context.name }).click();
  await expect(
    page.getByRole("heading", { name: fixtures.context.name }),
  ).toBeVisible();
  await expect(page.getByText(/Home infrastructure/)).toBeVisible();
  await page.getByRole("button", { name: /^configuration / }).click();
  await expect(page.locator(".record-card")).not.toHaveCount(0);
  await page.getByRole("button", { name: "All facets", exact: true }).click();
  await page.screenshot({
    path: "/tmp/knowledge-vault-pr3-portal.png",
    fullPage: true,
  });
  const requests = await page.request.post(
    ORIGIN + "/dashboard/api/v1/catalog/query",
    {
      headers: { Origin: ORIGIN, "X-Dashboard-Request": "1" },
      data: { dimension: "topics", filters: { statuses: [] }, limit: 50 },
    },
  );
  expect(requests.status()).toBe(200);
});

test("overflow remains unresolved while raw evidence paginates beyond1000", async ({
  page,
}) => {
  await signIn(page);
  await page
    .getByRole("link", { name: "Temporal evidence", exact: true })
    .click();
  await page.getByLabel("Subject", { exact: true }).fill("overflow");
  await page.getByLabel("Property", { exact: true }).fill("backup");
  await page.getByLabel("Scope", { exact: true }).fill("personal");
  await page.getByRole("button", { name: "Inspect evidence" }).click();
  await expect(
    page.getByText(/State unresolved: evidence limit/),
  ).toBeVisible();
  for (let index = 1; index <= 21; index++) {
    await expect(page.locator(".raw-evidence .pager")).toContainText(
      `Page ${index}`,
    );
    if (index < 21)
      await page
        .locator(".raw-evidence")
        .getByRole("button", { name: "Next page", exact: true })
        .click();
  }
  await expect(page.locator(".raw-evidence details")).toHaveCount(3);
  await expect(page.getByText(/backup-1002/)).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Supported value" }),
  ).toHaveCount(0);
});

test("CSP, inert sensitive content, cookie isolation and expiry are enforced in a real browser", async ({
  page,
  context,
}) => {
  await signIn(page);
  const response = await page.goto(
    "/dashboard/assertions/" + fixtures.details[8].id,
  );
  expect(response!.headers()["content-security-policy"]).toContain(
    "script-src 'self'",
  );
  expect(response!.headers()["cache-control"]).toBe("no-store");
  await expect(page.locator(".assertion-content")).toContainText("<script>");
  await expect(page.locator(".assertion-content img")).toHaveCount(0);
  await expect(
    page.getByText("sensitive sensitivity", { exact: true }),
  ).toBeVisible();
  await page.evaluate(() => {
    const script = document.createElement("script");
    script.textContent = "window.__inline_xss=1";
    document.body.append(script);
  });
  expect(
    await page.evaluate(
      () => (window as unknown as Record<string, unknown>).__inline_xss,
    ),
  ).toBeUndefined();
  const cookie = (await context.cookies()).find(
    (item) => item.name === "__Host-kv_dashboard",
  )!;
  expect(cookie).toMatchObject({
    httpOnly: true,
    secure: true,
    sameSite: "Strict",
  });
  expect(await page.evaluate(() => document.cookie)).not.toContain(
    "__Host-kv_dashboard",
  );
  expect(
    await page.evaluate(() => [localStorage.length, sessionStorage.length]),
  ).toEqual([0, 0]);
  await context.addCookies([
    { ...cookie, value: cookie.value.slice(0, -2) + "xx" },
  ]);
  await page.getByRole("button", { name: "Sources", exact: true }).click();
  // An empty preview is local; a new server read is required to reauthorize.
  await page.getByRole("link", { name: "Back to Explorer" }).click();
  await expect(page.getByLabel("Dashboard read-only credential")).toBeVisible();
  await expect(page.locator(".assertion-content")).toHaveCount(0);
});

test("service degradation and rate limits show bounded errors and manual retry", async ({
  page,
  request,
}) => {
  await signIn(page);
  await request.post(ORIGIN + "/__test__/scenario/degraded");
  await page.getByRole("button", { name: "Apply filters" }).click();
  await expect(page.getByRole("alert")).toContainText(
    "temporarily unavailable",
  );
  await request.post(ORIGIN + "/__test__/scenario/rate");
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("alert")).toContainText("Retry after 30 seconds");
  await request.post(ORIGIN + "/__test__/scenario/normal");
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.locator(".record-card")).toHaveCount(50);
});

test("private logout clears views and requires another credential exchange", async ({
  page,
}) => {
  await signIn(page);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByLabel("Dashboard read-only credential")).toBeVisible();
  await expect(page.getByLabel("Dashboard read-only credential")).toHaveValue(
    "",
  );
  await page.reload();
  await expect(page.getByLabel("Dashboard read-only credential")).toBeVisible();
});

test("Access verifies browser/proxy identity and handles non-JSON expiry and redirects", async ({
  page,
  context,
}) => {
  await page.goto(ACCESS + "/dashboard/");
  await page.getByRole("button", { name: "Authorize test session" }).click();
  await expect(
    page.getByRole("heading", { name: "Knowledge Explorer" }),
  ).toBeVisible();
  await expect(page.locator(".record-card")).toHaveCount(50);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(
    page.getByText(/provider session may still be active/),
  ).toBeVisible();
  await expect(page.getByLabel("Dashboard read-only credential")).toHaveCount(
    0,
  );
  await page
    .getByRole("button", { name: "Continue with Cloudflare Access" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Knowledge Explorer" }),
  ).toBeVisible();
  await context.clearCookies();
  await page.getByRole("button", { name: "Apply filters" }).click();
  await expect(
    page.getByRole("button", { name: "Continue with Cloudflare Access" }),
  ).toBeVisible();
  await expect(page.getByText("Untrusted edge denial")).toHaveCount(0);
  await page
    .getByRole("button", { name: "Continue with Cloudflare Access" })
    .click();
  await page.getByRole("button", { name: "Authorize test session" }).click();
  await expect(
    page.getByRole("heading", { name: "Knowledge Explorer" }),
  ).toBeVisible();
  await context.addCookies([
    { name: "edge_redirect", value: "yes", url: ACCESS, secure: true },
  ]);
  await page.getByRole("button", { name: "Apply filters" }).click();
  await expect(
    page.getByRole("button", { name: "Continue with Cloudflare Access" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Test Access provider" }),
  ).toHaveCount(0);
});

test("desktop and mobile views pass WCAG checks and keyboard navigation", async ({
  page,
}) => {
  await signIn(page);
  expect((await auditAccessibility(page)).violations).toEqual([]);
  await page.keyboard.press("Tab");
  await page.getByRole("link", { name: "Portal", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Knowledge Portal" }),
  ).toBeVisible();
  expect((await auditAccessibility(page)).violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Navigation", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Close navigation" }),
  ).toHaveAttribute("aria-expanded", "true");
  await page.getByRole("link", { name: "Explorer", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Knowledge Explorer" }))
    .toBeFocused({ timeout: 1000 })
    .catch(async () => {
      await expect(page.locator("#main")).toBeFocused();
    });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect((await auditAccessibility(page)).violations).toEqual([]);
  await page.screenshot({
    path: "/tmp/knowledge-vault-pr3-mobile.png",
    fullPage: true,
  });
});
