// Synthetic Minikube ingress proof. Receives ephemeral configuration over stdin, never argv.
import assert from "node:assert/strict";
import https from "node:https";
import { X509Certificate, createHash } from "node:crypto";
import { chromium, expect } from "@playwright/test";

async function verify() {
  let input = "";
  for await (const chunk of process.stdin) input += chunk;
  const config = JSON.parse(input);
  assert.equal(config.host, "dashboard.example.test");
  assert.equal(config.url, "https://dashboard.example.test/dashboard/");
  assert.match(config.ingress_address, /^192\.168\.\d{1,3}\.\d{1,3}$/);
  const certificate = new X509Certificate(config.ca_certificate);
  const spki = createHash("sha256")
    .update(certificate.publicKey.export({ type: "spki", format: "der" }))
    .digest("base64");
  assert.equal(spki, config.tls_spki_sha256);

  // Verify the actual certificate and hostname first, without ignoring TLS errors.
  async function get(path, host = config.host) {
    return await new Promise((resolve, reject) => {
      const request = https.get(
        {
          hostname: config.host,
          family: 4,
          autoSelectFamily: false,
          lookup: (_name, _options, callback) =>
            callback(null, config.ingress_address, 4),
          servername: config.host,
          ca: config.ca_certificate,
          rejectUnauthorized: true,
          path,
          headers: { Host: host },
          timeout: 5000,
        },
        (response) => {
          response.resume();
          response.on("end", () => resolve(response.statusCode));
        },
      );
      request.on("timeout", () =>
        request.destroy(new Error("TLS probe timeout")),
      );
      request.on("error", reject);
    });
  }
  assert.equal(await get("/dashboard/"), 200);
  for (const path of [
    "/health/ready",
    "/health/live",
    "/mcp",
    "/api/v1/statistics",
  ])
    assert.equal(await get(path), 404);
  assert.equal(await get("/dashboard/", "unconfigured.example.test"), 404);

  const browser = await chromium.launch({
    args: [
      `--host-resolver-rules=MAP ${config.host} ${config.ingress_address}`,
      `--ignore-certificate-errors-spki-list=${spki}`,
      "--no-proxy-server",
    ],
  });
  try {
    // Only this throwaway lab certificate is trusted; no blanket HTTPS error bypass.
    const context = await browser.newContext();
    const page = await context.newPage();
    const traffic = [];
    page.on("request", (request) => traffic.push(new URL(request.url())));
    await page.goto(config.url);
    await page
      .getByLabel("Dashboard read-only credential")
      .fill(config.browser_credential);
    await page.getByRole("button", { name: "Open your vault" }).click();
    await expect(page.locator(".record-card").first()).toBeVisible();
    await expect(page.locator(".record-card").first()).toContainText(
      "Disposable dashboard lifecycle synthetic assertion.",
    );
    const cookies = await context.cookies();
    const session = cookies.find(
      (cookie) => cookie.name === "__Host-kv_dashboard",
    );
    assert.ok(
      session?.secure && session.httpOnly && session.sameSite === "Strict",
    );
    assert.equal(session.path, "/");
    await page.locator(".record-card h3 a").first().click();
    await expect(
      page.getByRole("heading", { name: "Knowledge & evidence" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Sources", exact: true }).click();
    await page.getByRole("link", { name: "Portal", exact: true }).click();
    await page.getByRole("link", { name: "pr4-lab", exact: true }).click();
    await expect(page.locator(".record-card").first()).toBeVisible();
    assert.ok(
      traffic.every((url) => url.origin === "https://dashboard.example.test"),
    );
    assert.ok(traffic.every((url) => url.pathname.startsWith("/dashboard/")));
    assert.ok(
      traffic.some(
        (url) => url.pathname === "/dashboard/api/v1/assertions/query",
      ),
    );
    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(
      page.getByLabel("Dashboard read-only credential"),
    ).toBeVisible();
    await expect(page.locator(".record-card")).toHaveCount(0);
    console.log(
      "PASS: lab TLS, exact Host/path ingress, authenticated browser/BFF/core, Portal and logout",
    );
  } finally {
    await browser.close();
  }
}

try {
  await verify();
} catch {
  // Browser diagnostics can contain credentials or response bodies. Never print them.
  console.error("FAIL: isolated Dashboard ingress/browser verification");
  process.exitCode = 1;
}
