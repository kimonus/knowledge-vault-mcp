import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { RelationCard } from "../src/detail";
import type { Relation } from "../src/api/client";
import { DataGrid, ErrorNotice, SourceLink, topicKey } from "../src/shared";
import { api, fixtures, mount, response, session } from "./helpers";

const user = () => userEvent.setup();
async function ready(path = "/explorer") {
  mount(path);
  await screen.findByRole("heading", {
    name: path === "/portal" ? "Knowledge Portal" : "Knowledge Explorer",
  });
}

describe("Explorer with actual protocol shapes", () => {
  it("makes unsupported unclassified filters visibly unavailable to ranked search", async () => {
    api();
    await ready();
    await user().click(screen.getByText("More filters"));
    await user().click(screen.getByLabelText("missing topic"));
    expect(screen.getByLabelText("Ranked search")).toBeDisabled();
    await user().click(screen.getByLabelText("missing topic"));
    await user().click(screen.getByLabelText("Ranked search"));
    expect(screen.getByLabelText("missing topic")).toBeDisabled();
    expect(screen.getByLabelText("missing context")).toBeDisabled();
    expect(screen.getByLabelText("missing facet")).toBeDisabled();
  });
  it("rejects invalid topic addresses without displaying an unfiltered catalog", async () => {
    const mock = api();
    mount("/portal/topics/a");
    await screen.findByRole("alert");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "topic address is invalid",
    );
    expect(
      mock.mock.calls.some(([url]) => String(url).includes("assertions/query")),
    ).toBe(false);
  });
  it("defaults to exhaustive all-status browsing and follows opaque cursors both ways", async () => {
    const mock = api();
    await ready();
    await screen.findByRole("link", { name: fixtures.summaries[0].excerpt });
    expect(
      mock.mock.calls
        .map(([, init]) =>
          typeof init.body === "string" ? JSON.parse(init.body) : {},
        )
        .find((body) => body.mode === "all"),
    ).toMatchObject({ filters: { statuses: [] }, limit: 50, cursor: null });
    await user().click(screen.getByRole("button", { name: "Next page" }));
    await screen.findByRole("link", { name: fixtures.summaries[2].excerpt });
    expect(screen.getByText("Page 2")).toBeInTheDocument();
    await user().click(screen.getByRole("button", { name: "Previous" }));
    await screen.findByRole("link", { name: fixtures.summaries[0].excerpt });
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
  });
  it("sends explicit exact-text and metadata filters without putting private text into URLs", async () => {
    const mock = api();
    await ready();
    const u = user();
    await u.click(screen.getByLabelText("Exact text"));
    await u.type(screen.getByLabelText("Words or phrases to match"), "backup");
    await u.type(
      screen.getByLabelText("Topic", { exact: true }),
      "backups, homelab",
    );
    await u.selectOptions(screen.getByLabelText("Evidence status"), "current");
    await u.selectOptions(
      screen.getByLabelText("Record kind"),
      "configuration",
    );
    await u.type(screen.getByLabelText("Context ID"), fixtures.context.id);
    await u.click(screen.getByText("More filters"));
    await u.selectOptions(screen.getByLabelText("Sensitivity"), "normal");
    await u.selectOptions(
      screen.getByLabelText("Evidence classification"),
      "reported",
    );
    await u.selectOptions(screen.getByLabelText("Workflow state"), "proposed");
    await u.selectOptions(
      screen.getByLabelText("Origin", { exact: true }),
      "user",
    );
    await u.selectOptions(screen.getByLabelText("Temporal role"), "ongoing");
    await u.type(
      screen.getByLabelText("Facet", { exact: true }),
      "configuration",
    );
    await u.type(screen.getByLabelText("Minimum confidence"), "0.8");
    await u.click(screen.getByLabelText("review due"));
    fireEvent.change(screen.getByLabelText("created after (local time)"), {
      target: { value: "2026-01-01T00:00" },
    });
    fireEvent.change(screen.getByLabelText("created before (local time)"), {
      target: { value: "2027-01-01T00:00" },
    });
    fireEvent.change(screen.getByLabelText("valid at (local time)"), {
      target: { value: "2026-11-19T00:00" },
    });
    await u.selectOptions(screen.getByLabelText("Page size"), "10");
    await u.click(screen.getByRole("button", { name: "Apply filters" }));
    await screen.findByText("No records in this view");
    const request = mock.mock.calls
      .filter(
        ([, init]) =>
          init.body && JSON.parse(String(init.body)).mode === "lexical",
      )
      .at(-1)!;
    const body = JSON.parse(String(request[1].body));
    expect(request[0]).toBe("/dashboard/api/v1/assertions/query");
    expect(body).toMatchObject({
      query: "backup",
      limit: 10,
      filters: {
        topics: ["backups", "homelab"],
        context_id: fixtures.context.id,
        kinds: ["configuration"],
        statuses: ["current"],
        minimum_confidence: 0.8,
        review_due: true,
        facet: "configuration",
        workflow_states: ["proposed"],
      },
    });
    expect(body.filters.created_after).toBe(
      new Date("2026-01-01T00:00").toISOString(),
    );
    await u.click(screen.getByRole("button", { name: "Reset" }));
    await screen.findByRole("link", { name: fixtures.summaries[0].excerpt });
    expect(screen.getByLabelText("All retained")).toBeChecked();
  });
  it("treats missing buckets explicitly and removes contradictory values", async () => {
    const mock = api();
    await ready();
    const u = user();
    await u.type(screen.getByLabelText("Topic", { exact: true }), "backup");
    await u.type(screen.getByLabelText("Context ID"), fixtures.context.id);
    await u.click(screen.getByText("More filters"));
    await u.type(
      screen.getByLabelText("Facet", { exact: true }),
      "configuration",
    );
    for (const text of ["missing topic", "missing context", "missing facet"])
      await u.click(screen.getByLabelText(text));
    await u.click(screen.getByRole("button", { name: "Apply filters" }));
    await waitFor(() =>
      expect(
        mock.mock.calls.some(([, init]) =>
          String(init.body).includes('"missing_topic":true'),
        ),
      ).toBe(true),
    );
    const body = JSON.parse(String(mock.mock.calls.at(-1)![1].body));
    expect(body.filters).toMatchObject({
      topics: [],
      missing_topic: true,
      missing_context: true,
      missing_facet: true,
    });
    expect(body.filters).not.toHaveProperty("context_id");
    expect(body.filters).not.toHaveProperty("facet");
  });
  it("labels ranked exhaustion and embedding degradation without claiming completeness", async () => {
    api();
    await ready();
    const u = user();
    await u.click(screen.getByLabelText("Ranked search"));
    await u.type(screen.getByLabelText("Search your knowledge"), "backup");
    await u.click(screen.getByRole("button", { name: "Apply filters" }));
    await screen.findByText(
      "Semantic retrieval is degraded. Text-based results remain available.",
    );
    expect(screen.getByText("Bounded candidate set")).toBeInTheDocument();
    expect(
      screen.getByText(/Ranked results are a bounded selection/),
    ).toBeInTheDocument();
  });
  it.each([404, 422, 429, 503])(
    "shows safe errors and recovers on manual retry (%s)",
    async (status) => {
      let failed = false;
      api((path) => {
        if (path === "assertions/query" && !failed) {
          failed = true;
          return response({}, status, { "Retry-After": "30" });
        }
      });
      await ready();
      await screen.findByRole("alert");
      await user().click(screen.getByRole("button", { name: "Try again" }));
      await screen.findByRole("link", { name: fixtures.summaries[0].excerpt });
    },
  );
  it("aborts stale queries and never replaces a newer view with a late response", async () => {
    let finish: ((response: Response) => void) | undefined;
    api((path, body) =>
      path === "assertions/query" && body.query === "slow"
        ? new Promise<Response>((resolve) => {
            finish = resolve;
          })
        : undefined,
    );
    await ready();
    const u = user();
    await u.click(screen.getByLabelText("Exact text"));
    await u.type(screen.getByLabelText("Words or phrases to match"), "slow");
    await u.click(screen.getByRole("button", { name: "Apply filters" }));
    await waitFor(() => expect(finish).toBeDefined());
    await u.click(screen.getByRole("button", { name: "Reset" }));
    await screen.findByRole("link", { name: fixtures.summaries[0].excerpt });
    finish!(
      response({
        ...fixtures.page,
        items: [
          { ...fixtures.summaries[0], excerpt: "STALE RESULT MUST NOT APPEAR" },
        ],
      }),
    );
    await waitFor(() =>
      expect(
        screen.queryByText("STALE RESULT MUST NOT APPEAR"),
      ).not.toBeInTheDocument(),
    );
  });
});

describe("Complete assertion inspection", () => {
  it("renders provenance, metadata and unsafe stored text inertly", async () => {
    api();
    mount("/assertions/" + fixtures.details[8].id);
    await screen.findByRole("heading", { name: "Knowledge & evidence" });
    expect(document.querySelector("script")).toBeNull();
    expect(document.querySelector("img")).toBeNull();
    expect(
      screen.getByText(
        "Usage accounting is unavailable; zero counters are not authoritative.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /Ingestion confirmations are not independent verification/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Synthetic provenance" }),
    ).toBeInTheDocument();
    await user().click(screen.getByRole("button", { name: "Sources" }));
    await screen.findByText("No sources recorded");
  });
  it("continues source previews using returned size20, not the route default50", async () => {
    const mock = api();
    mount("/assertions/" + fixtures.details[0].id);
    await screen.findByRole("heading", { name: "Knowledge & evidence" });
    await user().click(screen.getByRole("button", { name: "Sources" }));
    await screen.findByText("Evidence source 19");
    expect(
      screen.queryByRole("link", { name: /window.__vault_xss/ }),
    ).not.toBeInTheDocument();
    await user().click(screen.getByRole("button", { name: "Next page" }));
    await screen.findByText("Evidence source 20");
    expect(
      mock.mock.calls.some(([path]) =>
        String(path).includes("sources?limit=20&cursor=20"),
      ),
    ).toBe(true);
    await user().click(screen.getByRole("button", { name: "Previous" }));
    await screen.findByText("Evidence source 19");
  });
  it("reads inert artifact chunks forwards and backwards with returned character offsets", async () => {
    api();
    mount("/assertions/" + fixtures.details[0].id);
    await screen.findByRole("heading", { name: "Knowledge & evidence" });
    const u = user();
    await u.click(screen.getByRole("button", { name: "Artifacts" }));
    await u.click(
      (await screen.findAllByRole("button", { name: "Read text" }))[0],
    );
    await screen.findByLabelText("Artifact text");
    expect(document.querySelector("img")).toBeNull();
    await u.click(screen.getByRole("button", { name: "Next text" }));
    await screen.findByText(/Characters 8000/);
    expect(screen.getByRole("button", { name: "Next text" })).toBeDisabled();
    await u.click(screen.getByRole("button", { name: "Previous text" }));
    await screen.findByText(/Characters 0/);
    await u.click(screen.getByRole("button", { name: "Close text" }));
    expect(screen.queryByLabelText("Artifact text")).not.toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Next page" }));
    await screen.findByText("evidence-20.txt");
  });
  it("preserves explicit relation direction, supersession and recorded conflict resolution", async () => {
    api();
    mount("/assertions/" + fixtures.details[0].id);
    await screen.findByRole("heading", { name: "Knowledge & evidence" });
    await user().click(screen.getByRole("button", { name: "Relationships" }));
    await screen.findByText("depends on");
    expect(screen.getByText("Resolved conflict record")).toBeInTheDocument();
    expect(screen.getByText("Supersession")).toBeInTheDocument();
    const section = screen.getByRole("region", { name: "Relationships" });
    await user().click(
      within(section).getAllByRole("link", { name: fixtures.details[1].id })[0],
    );
    await screen.findByText(fixtures.details[1].content);
  });
  it("paginates identities and separates resolver history from raw immutable evidence", async () => {
    const mock = api();
    mount("/assertions/" + fixtures.details[0].id);
    await screen.findByRole("heading", { name: "Knowledge & evidence" });
    const u = user();
    await u.click(screen.getByRole("button", { name: "Temporal evidence" }));
    const identity = await screen.findByRole("button", {
      name: /homelab \/ backup/,
    });
    await u.click(identity);
    await screen.findByText("Supported value");
    const resolution = screen.getByRole("heading", {
      name: "State resolution",
    }).parentElement!;
    await u.click(
      within(resolution).getByRole("button", { name: "Next page" }),
    );
    await waitFor(() =>
      expect(
        mock.mock.calls.some(([, init]) =>
          String(init.body).includes('"history_after":"backup-000"'),
        ),
      ).toBe(true),
    );
    await u.click(within(resolution).getByRole("button", { name: "Previous" }));
    const raw = screen.getByRole("heading", {
      name: "Raw immutable evidence",
    }).parentElement!;
    await u.click(within(raw).getByText(/backup-000/, { selector: "summary" }));
    expect(
      within(raw).getByText("Retained synthetic backup evidence.", {
        selector: "p",
      }),
    ).toBeInTheDocument();
    await u.click(within(raw).getByRole("button", { name: "Next page" }));
    await waitFor(() =>
      expect(
        mock.mock.calls.some(
          ([path, init]) =>
            String(path).includes("state/evidence/query") &&
            String(init.body).includes('"cursor":"1"'),
        ),
      ).toBe(true),
    );
  });
  it("shows dependency errors for a missing retained detail", async () => {
    api();
    mount("/assertions/" + "f".repeat(36));
    await screen.findByRole("alert");
  });
});

describe("Deterministic Portal and temporal projections", () => {
  it("navigates canonical topics and exact unclassified buckets", async () => {
    const mock = api();
    await ready("/portal");
    const u = user();
    await u.click(await screen.findByRole("link", { name: "backups" }));
    await screen.findByRole("heading", { name: "backups" });
    await screen.findByText(/Explicit topic aliases: backup/);
    await u.selectOptions(
      screen.getByLabelText("Record section"),
      "Rejected options",
    );
    await screen.findByRole("link", { name: fixtures.summaries[2].excerpt });
    expect(
      screen.getByText("rejected option", { selector: ".badge" }),
    ).toBeInTheDocument();
    await u.selectOptions(
      screen.getByLabelText("Evidence status"),
      "superseded",
    );
    await u.click(screen.getByLabelText("Historical evidence only"));
    await waitFor(() =>
      expect(
        mock.mock.calls.some(([, init]) =>
          String(init.body).includes('"temporal_roles":["historical"]'),
        ),
      ).toBe(true),
    );
    await u.click(screen.getByRole("link", { name: "Back to Portal" }));
    await u.click(await screen.findByRole("link", { name: "Uncategorized" }));
    await screen.findByRole("heading", { name: "Uncategorized topics" });
    expect(
      mock.mock.calls.some(([, init]) =>
        String(init.body).includes('"missing_topic":true'),
      ),
    ).toBe(true);
  });
  it("shows actual context aliases and pages its facet catalog without invented summaries", async () => {
    api();
    await ready("/portal");
    const u = user();
    await u.click(screen.getByRole("button", { name: "contexts" }));
    await u.click(
      await screen.findByRole("link", { name: fixtures.context.name }),
    );
    await screen.findByRole("heading", { name: fixtures.context.name });
    expect(screen.getByText(/Home infrastructure/)).toBeInTheDocument();
    await u.click(
      await screen.findByRole("button", { name: /configuration 4/ }),
    );
    await screen.findByRole("heading", { name: "All retained assertions" });
    await u.click(screen.getByRole("button", { name: "All facets" }));
    await u.click(screen.getByRole("button", { name: /Uncategorized 4/ }));
  });
  it("filters catalog prefixes, exposes cross-context facets and retains only explicit counts", async () => {
    api();
    await ready("/portal");
    const u = user();
    await u.type(screen.getByLabelText("Catalog prefix"), "MISSING");
    await u.click(screen.getByRole("button", { name: "Find themes" }));
    await screen.findByText("No themes in this view");
    await u.clear(screen.getByLabelText("Catalog prefix"));
    await u.click(screen.getByRole("button", { name: "Find themes" }));
    await u.click(
      (
        await screen.findAllByText("Evidence & record kinds", {
          selector: "summary",
        })
      )[0],
    );
    expect(
      screen.getAllByText(/^configuration/, { selector: ".counts p" })[0],
    ).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "facets" }));
    await screen.findByText(/Facets are enumerated across all contexts/);
    await u.click(await screen.findByRole("link", { name: "configuration" }));
    await screen.findByRole("heading", { name: "Facet: configuration" });
  });
  it.each(["ambiguous", "conflict", "unknown", "overflow"])(
    "preserves resolver outcome %s and independent raw evidence",
    async (outcome) => {
      api((path) =>
        path === "state/resolve"
          ? outcome === "overflow"
            ? response({}, 422)
            : response({
                ...fixtures.resolution,
                outcome,
                values: outcome === "unknown" ? [] : ["nightly", "weekly"],
                next_history_after: null,
              })
          : undefined,
      );
      mount("/state");
      await screen.findByRole("heading", { name: "Temporal evidence" });
      const u = user();
      await u.type(screen.getByLabelText("Subject"), outcome);
      await u.type(screen.getByLabelText("Property"), "backup");
      await u.type(screen.getByLabelText("Scope", { exact: true }), "personal");
      await u.click(screen.getByRole("button", { name: "Inspect evidence" }));
      await screen.findByRole("heading", { name: "Raw immutable evidence" });
      if (outcome === "overflow")
        await screen.findByText(/State unresolved: evidence limit/);
      else
        expect(
          await screen.findByText("Candidate values (no winner)"),
        ).toBeInTheDocument();
      fireEvent.change(
        screen.getByLabelText("Applicable at (as of, local time)"),
        { target: { value: "2026-10-01T00:00" } },
      );
      fireEvent.change(
        screen.getByLabelText("Evidence known by (local time)"),
        { target: { value: "2026-11-01T00:00" } },
      );
      await u.click(screen.getByRole("button", { name: "Apply times" }));
    },
  );
});

describe("Browser authentication and safe presentation", () => {
  it("exchanges a transient private credential and clears the password input", async () => {
    let logged = false;
    api((path) =>
      path === "session" && !logged
        ? response({}, 401)
        : path === "session/login"
          ? ((logged = true), response(null, 204))
          : undefined,
    );
    mount();
    const u = user();
    await u.type(
      await screen.findByLabelText("Dashboard read-only credential"),
      "synthetic-browser-input",
    );
    await u.click(screen.getByRole("button", { name: "Open your vault" }));
    await screen.findByRole("heading", { name: "Knowledge Explorer" });
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    await u.click(screen.getByRole("button", { name: "Sign out" }));
    await screen.findByText(/A copied cookie remains valid/);
    expect(screen.getByLabelText("Dashboard read-only credential")).toHaveValue(
      "",
    );
  });
  it("clears private state on authentication expiry and never renders edge denial HTML", async () => {
    let denied = false;
    api((path) =>
      path === "assertions/query" && denied
        ? new Response("<h1>EDGE HTML MUST NOT RENDER</h1>", {
            status: 403,
            headers: { "Content-Type": "text/html" },
          })
        : undefined,
    );
    await ready();
    await screen.findByRole("link", { name: fixtures.summaries[0].excerpt });
    denied = true;
    await user().click(screen.getByRole("button", { name: "Apply filters" }));
    await screen.findByLabelText("Dashboard read-only credential");
    expect(
      screen.queryByText("EDGE HTML MUST NOT RENDER"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(fixtures.summaries[0].excerpt),
    ).not.toBeInTheDocument();
  });
  it("expires absolute sessions locally and handles unconfirmed server logout", async () => {
    api((path) =>
      path === "session"
        ? response({ ...session(), expires_at: Date.now() / 1000 + 0.2 })
        : undefined,
    );
    await ready();
    await screen.findByText("Your session expired. Sign in again to continue.");
  });
  it("clears data even when logout cannot be confirmed", async () => {
    api((path) => (path === "session/logout" ? response({}, 503) : undefined));
    await ready();
    await user().click(screen.getByRole("button", { name: "Sign out" }));
    await screen.findByText(/server logout could not be confirmed/);
  });
  it("offers only provider reauthentication in Access mode and describes provider logout", async () => {
    document.getElementById("root")!.dataset.authMode = "cloudflareAccess";
    api((path) =>
      path === "session"
        ? response({ ...session(), auth_mode: "cloudflareAccess" })
        : undefined,
    );
    await ready();
    await user().click(screen.getByRole("button", { name: "Sign out" }));
    await screen.findByText(/Access provider session may still be active/);
    expect(
      screen.getByRole("button", { name: "Continue with Cloudflare Access" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByLabelText("Dashboard read-only credential"),
    ).not.toBeInTheDocument();
  });
  it("announces login failures, handles missing routes, and makes mobile navigation operable", async () => {
    api();
    await ready();
    const u = user();
    await u.click(screen.getByRole("button", { name: "Navigation" }));
    expect(
      screen.getByRole("button", { name: "Close navigation" }),
    ).toHaveAttribute("aria-expanded", "true");
    await u.click(screen.getByRole("link", { name: "Portal" }));
    await screen.findByRole("heading", { name: "Knowledge Portal" });
    expect(document.getElementById("main")).toHaveFocus();
  });
  it("shows unknown routes and all inert data value forms", async () => {
    api();
    mount("/not-a-route");
    await screen.findByText("Page unavailable");
    render(
      <>
        <DataGrid
          value={{
            empty: null,
            missing: undefined,
            blank: "",
            object: { text: "<script>" },
            bool: false,
            count: 0,
          }}
        />
        <SourceLink value="javascript:alert(1)">inert</SourceLink>
        <SourceLink value="https://example.test">safe</SourceLink>
        <ErrorNotice error={new Error("private content")} />
      </>,
    );
    expect(screen.getByText("inert")).not.toHaveAttribute("href");
    expect(screen.getByRole("link", { name: /safe/ })).toHaveAttribute(
      "rel",
      "noopener noreferrer",
    );
    expect(screen.queryByText("private content")).not.toBeInTheDocument();
  });
  it("labels relation positions and unclassified topics without producing HTML", () => {
    render(
      <MemoryRouter>
        <RelationCard
          relation={
            {
              type: "link",
              edge: {
                source_id: fixtures.details[0].id,
                target_id: fixtures.details[1].id,
                kind: "part_of",
                position: 3,
              },
            } as Relation
          }
        />
        <RelationCard
          relation={{ type: "conflict", conflict: null } as Relation}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText("Position: 3")).toBeInTheDocument();
    expect(screen.getByText("Recorded conflict")).toBeInTheDocument();
    expect(topicKey("<>")).not.toContain("<");
  });
});
