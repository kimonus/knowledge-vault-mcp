import { describe, expect, it, vi } from "vitest";
import { ApiError, Client, isAbort } from "../src/api/client";
import { label, safeUrl, topicKey, topicValue, when } from "../src/shared";
import { response, session } from "./helpers";

describe("fixed same-origin client", () => {
  it.each([1, 10, 100])(
    "honors advertised page cap %s without applying it to artifact characters",
    async (maximum) => {
      const fetcher = vi
        .fn()
        .mockImplementation(() =>
          Promise.resolve(response({ ...session(), max_page_size: maximum })),
        );
      vi.stubGlobal("fetch", fetcher);
      const client = new Client(vi.fn());
      await client.session();
      fetcher.mockImplementation(() =>
        Promise.resolve(response({ untrusted_data: true })),
      );
      await client.post("catalog/query", { dimension: "topics" });
      expect(JSON.parse(fetcher.mock.calls.at(-1)![1].body).limit).toBe(
        Math.min(50, maximum),
      );
      await client.post("state/resolve", {
        identity: {
          subject: "homelab",
          property: "backup",
          scope: "personal",
          state_type: "configuration",
        },
      });
      expect(JSON.parse(fetcher.mock.calls.at(-1)![1].body).history_limit).toBe(
        Math.min(20, maximum),
      );
      await client.get("assertions/{id}/sources", "0".repeat(36), {
        limit: 20,
      });
      expect(fetcher.mock.calls.at(-1)![0]).toContain(
        "limit=" + Math.min(20, maximum),
      );
      await client.get("artifacts/{id}", "0".repeat(36), { limit: 8000 });
      expect(fetcher.mock.calls.at(-1)![0]).toContain("limit=8000");
    },
  );
  it("aborts old reads immediately and bounds an isolated CSRF-aware logout exchange", async () => {
    const deadline = new AbortController();
    const timeout = vi
      .spyOn(AbortSignal, "timeout")
      .mockReturnValue(deadline.signal);
    const fetcher = vi.fn(
      (_url: string, init: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener("abort", () =>
            reject(new TypeError("transport cancelled")),
          );
        }),
    );
    vi.stubGlobal("fetch", fetcher);
    const expired = vi.fn();
    const client = new Client(expired);
    client.csrf = "synthetic-csrf";
    const read = expect(client.get("topic-aliases")).rejects.toSatisfy(isAbort);
    const logout = expect(client.logout()).rejects.toSatisfy(isAbort);
    expect(timeout).toHaveBeenCalledWith(5000);
    expect(fetcher.mock.calls.at(-1)?.[1].headers).toMatchObject({
      "X-CSRF-Token": "synthetic-csrf",
    });
    expect(client.csrf).toBe("");
    deadline.abort();
    await Promise.all([read, logout]);
    expect(expired).not.toHaveBeenCalled();
  });
  it("discards a late response even when its transport ignores cancellation", async () => {
    let complete!: (value: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            complete = resolve;
          }),
      ),
    );
    const expired = vi.fn();
    const client = new Client(expired);
    const pending = expect(client.session()).rejects.toSatisfy(isAbort);
    client.clear();
    complete(response(session()));
    await pending;
    expect(expired).not.toHaveBeenCalled();
    expect(client.csrf).toBe("");
  });
  it("uses only browser credentials and both JSON/Access CSRF defenses", async () => {
    const fetcher = vi.fn().mockResolvedValue(response(session()));
    vi.stubGlobal("fetch", fetcher);
    const client = new Client(vi.fn());
    await client.session();
    fetcher.mockResolvedValue(response({ items: [], untrusted_data: true }));
    await client.post("assertions/query", {
      mode: "all",
      filters: { statuses: [] },
    });
    const [path, init] = fetcher.mock.calls.at(-1)!;
    expect(path).toBe("/dashboard/api/v1/assertions/query");
    expect(init).toMatchObject({
      credentials: "same-origin",
      redirect: "error",
      cache: "no-store",
      method: "POST",
    });
    expect(init.headers).toMatchObject({
      "X-Dashboard-Request": "1",
      "X-Requested-With": "XMLHttpRequest",
      "X-CSRF-Token": "synthetic-csrf",
    });
    expect(init.headers).not.toHaveProperty("Authorization");
    fetcher.mockResolvedValue(response(null, 204));
    await client.logout();
    await client.login("transient");
    expect(fetcher.mock.calls.at(-1)![1].body).toBe(
      '{"credential":"transient"}',
    );
  });
  it.each([401, 403])(
    "clears all transient authority on status %s",
    async (status) => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(
          new Response("<h1>edge</h1>", {
            status,
            headers: { "Content-Type": "text/html" },
          }),
        ),
      );
      const expired = vi.fn();
      const client = new Client(expired);
      await expect(client.session()).rejects.toMatchObject({ status: 401 });
      expect(expired).toHaveBeenCalledOnce();
      expect(client.csrf).toBe("");
    },
  );
  it("never renders or follows edge HTML/redirects/network failures", async () => {
    const expired = vi.fn();
    const client = new Client(expired);
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockRejectedValue(new TypeError("edge redirect or network error")),
    );
    await expect(client.session()).rejects.toMatchObject({ status: 401 });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("<script>attack()</script>", {
          headers: { "Content-Type": "text/html" },
        }),
      ),
    );
    await expect(client.session()).rejects.toMatchObject({ status: 401 });
    const redirected = response({});
    Object.defineProperty(redirected, "redirected", { value: true });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(redirected));
    await expect(client.session()).rejects.toMatchObject({ status: 401 });
    expect(expired).toHaveBeenCalledTimes(3);
  });
  it("propagates cancellation and safely bounds invalid dependency responses", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new DOMException("abort", "AbortError")),
    );
    const expired = vi.fn();
    const client = new Client(expired);
    await expect(client.session()).rejects.toSatisfy(isAbort);
    expect(expired).not.toHaveBeenCalled();
    for (const result of [
      response({}, 503),
      response({}, 429, { "Retry-After": "30" }),
      response({}, 429, { "Retry-After": "https://evil.test" }),
    ]) {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(result));
      await expect(client.session()).rejects.toBeInstanceOf(ApiError);
    }
    for (const value of [
      "bad-json",
      "null",
      "123",
      '{"untrusted_data":false}',
    ]) {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(
          new Response(value, {
            headers: { "Content-Type": "application/json" },
          }),
        ),
      );
      await expect(client.session()).rejects.toMatchObject({ status: 502 });
    }
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("{}".repeat(1_000_001), {
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    await expect(client.session()).rejects.toMatchObject({ status: 502 });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(null, {
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    await expect(client.session()).rejects.toMatchObject({ status: 502 });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({})));
    await expect(client.session()).rejects.toMatchObject({ status: 502 });
  });
  it("rejects arbitrary proxy operations and invalid IDs before network use", async () => {
    const client = new Client(vi.fn());
    expect(() => client.get("mcp" as "topic-aliases")).toThrow(ApiError);
    expect(() => client.post("../write" as "search", { query: "x" })).toThrow(
      ApiError,
    );
    expect(() => client.get("assertions/{id}", "https://evil.test")).toThrow(
      ApiError,
    );
    const fetcher = vi.fn().mockResolvedValue(response({ aliases: {} }));
    vi.stubGlobal("fetch", fetcher);
    await client.get("topic-aliases");
    expect(fetcher.mock.calls[0][0]).toBe("/dashboard/api/v1/topic-aliases");
    fetcher.mockResolvedValue(response({ items: [] }));
    await client.get("assertions/{id}/sources", "0".repeat(36), {
      limit: 20,
      cursor: "opaque/private",
    });
    expect(fetcher.mock.calls.at(-1)![0]).toContain("cursor=opaque%2Fprivate");
  });
});
describe("inert formatting and exact keys", () => {
  it.each([
    "javascript:alert(1)",
    "data:text/html,attack",
    "file:///tmp/a",
    "https://user:pass@example.test",
    "/relative",
  ])('rejects unsafe locator "%s"', (value) =>
    expect(safeUrl(value)).toBeNull(),
  );
  it("preserves Unicode resource keys without private query strings", () => {
    const value = "копии / 日本語 & <script>";
    expect(topicValue(topicKey(value))).toBe(value);
    expect(topicValue("!invalid")).toBe("");
    expect(safeUrl("https://example.test/a")).toBe("https://example.test/a");
    expect(label(null)).toBe("Unspecified");
    expect(label("rejected_option")).toBe("rejected option");
    expect(when(null)).toBe("Not recorded");
    expect(when("bad-date")).toBe("bad-date");
    expect(when("2026-11-19T12:00:00Z")).toContain("UTC");
    expect(isAbort(new Error())).toBe(false);
  });
});
