import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { App } from "../src/app";
import { AuthBoundary } from "../src/auth";
import { Workspace } from "../src/workspace";
import fixtures from "./fixtures.json" with { type: "json" };

export { fixtures };
export const session = () => ({
  principal_id: "test-browser",
  auth_mode: "privateSession",
  expires_at: Math.floor(Date.now() / 1000) + 1800,
  csrf_token: "synthetic-csrf",
  capabilities: [],
  max_page_size: 50,
});
export const response = (
  data: unknown,
  status = 200,
  headers: Record<string, string> = {},
) =>
  new Response(status === 204 ? null : JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
export type Body = Record<string, unknown>;
export function api(
  override?: (
    path: string,
    body: Body,
    init: RequestInit,
  ) => Response | Promise<Response> | undefined,
) {
  const mock = vi.fn(
    async (input: string | URL | Request, init: RequestInit) => {
      const url = new URL(String(input), "https://dashboard.test");
      const path = url.pathname.replace("/dashboard/api/v1/", "");
      const body: Body =
        typeof init.body === "string" ? JSON.parse(init.body) : {};
      const overridden = override?.(path, body, init);
      if (overridden) return overridden;
      if (path === "session") return response(session());
      if (path.startsWith("session/")) return response(null, 204);
      if (path === "assertions/query" || path === "search") {
        let rows = fixtures.summaries;
        const filters = body.filters as Record<string, string[]> | undefined;
        if (filters?.kinds?.length)
          rows = rows.filter((row) => filters.kinds.includes(row.kind));
        if (filters?.statuses?.length)
          rows = rows.filter((row) => filters.statuses.includes(row.status));
        if (filters?.workflow_states?.length)
          rows = rows.filter((row) =>
            filters.workflow_states.includes(row.workflow_state ?? ""),
          );
        if (body.query)
          rows = rows.filter((row) =>
            row.excerpt
              .toLowerCase()
              .includes(String(body.query).toLowerCase()),
          );
        const start = Number(body.cursor ?? 0);
        const items = rows.slice(start, start + 2);
        const page = {
          ...fixtures.page,
          items:
            path === "search"
              ? items.map((assertion) => ({ assertion, score: 0.7 }))
              : items,
          next_cursor:
            start + items.length < rows.length
              ? String(start + items.length)
              : null,
        };
        return response(
          path === "search"
            ? { ...page, candidate_limited: true, embedding_degraded: true }
            : page,
        );
      }
      if (path === "catalog/query") {
        const dimension = body.dimension;
        let catalog = structuredClone(fixtures.catalog);
        if (dimension === "contexts")
          catalog.items[0] = {
            ...catalog.items[0],
            key: fixtures.context.id,
            label: fixtures.context.name,
          };
        if (dimension === "facets")
          catalog.items[0] = {
            ...catalog.items[0],
            key: "configuration",
            label: "configuration",
          };
        if (body.prefix)
          catalog = {
            ...catalog,
            items: catalog.items.filter((item) =>
              item.label.startsWith(String(body.prefix)),
            ),
          };
        return response(catalog);
      }
      if (path === "topic-aliases")
        return response({
          aliases: { backup: "backups" },
          untrusted_data: true,
        });
      if (path.startsWith("contexts/")) return response(fixtures.context);
      if (path.startsWith("artifacts/"))
        return response({
          ...fixtures.artifact,
          offset: Number(url.searchParams.get("offset") ?? 0),
          next_offset: url.searchParams.get("offset") === "8000" ? null : 8000,
        });
      if (path.startsWith("assertions/")) {
        const parts = path.split("/");
        const id = parts[1];
        const detail = fixtures.details.find((item) => item.id === id);
        if (!detail) return response({}, 404);
        if (parts.length === 2) return response(detail);
        const child = parts[2];
        const start = Number(url.searchParams.get("cursor") ?? 0);
        if (child === "relations")
          return response({
            ...fixtures.page,
            items: [
              {
                type: "link",
                edge: {
                  source_id: id,
                  target_id: fixtures.details[1].id,
                  kind: "depends_on",
                  position: null,
                },
              },
              {
                type: "supersession",
                predecessor_id: fixtures.details[1].id,
                successor_id: id,
              },
              {
                type: "conflict",
                conflict: {
                  id: fixtures.details[2].id,
                  left_assertion_id: id,
                  right_assertion_id: fixtures.details[5].id,
                  reason: "Synthetic conflict",
                  created_at: fixtures.now,
                  resolved_at: fixtures.now,
                },
              },
            ],
            next_cursor: null,
          });
        const pool = fixtures[child as "sources" | "artifacts" | "identities"];
        return response({
          ...fixtures.page,
          items: pool.slice(start, start + 20),
          page_size: 20,
          next_cursor: start + 20 < pool.length ? String(start + 20) : null,
        });
      }
      if (path === "state/resolve")
        return response({
          ...fixtures.resolution,
          next_history_after: body.history_after ? null : "backup-000",
        });
      if (path === "state/evidence/query")
        return response({
          ...fixtures.page,
          items: [fixtures.evidence],
          next_cursor: body.cursor ? null : "1",
        });
      return response({}, 404);
    },
  );
  vi.stubGlobal("fetch", mock);
  return mock;
}
export function mount(path = "/explorer") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthBoundary>
        <Workspace>
          <App />
        </Workspace>
      </AuthBoundary>
    </MemoryRouter>,
    { container: document.getElementById("root")! },
  );
}
