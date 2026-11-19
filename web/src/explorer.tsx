import { useCallback } from "react";
import type { FormEvent } from "react";
import { useAuth } from "./auth";
import { useMemory } from "./workspace";
import type { Filters, Page, Summary } from "./api/client";
import {
  Empty,
  ErrorNotice,
  Loading,
  Pager,
  RecordCard,
  useCursors,
  useLoad,
} from "./shared";

const KINDS = [
  "user_fact",
  "preference",
  "external_fact",
  "derived_conclusion",
  "decision",
  "procedure",
  "configuration",
  "considered_option",
  "rejected_option",
  "plan",
  "open_question",
  "artifact_observation",
];
export function Field({
  name,
  title,
  value,
  options,
  onChange,
}: {
  name: string;
  title: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
}) {
  return (
    <label className="field" htmlFor={name}>
      {title}
      <span className="select-wrap">
        <select
          aria-label={title}
          id={name}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        >
          <option value="">All</option>
          {options.map((option) => (
            <option key={option} value={option}>
              {option.replaceAll("_", " ")}
            </option>
          ))}
        </select>
        <span className="select-chevron" aria-hidden="true">
          ⌄
        </span>
      </span>
    </label>
  );
}
export type BrowseSpec = {
  mode: "all" | "lexical" | "ranked";
  query?: string;
  filters: Filters;
  limit: number;
};
export function Explorer() {
  const { session } = useAuth();
  const maximum = Math.min(50, session.max_page_size);
  const [mode, setMode] = useMemory<BrowseSpec["mode"]>("explorerMode", "all");
  const [query, setQuery] = useMemory("explorerQuery", "");
  const [filters, setFilters] = useMemory<Record<string, string>>(
    "explorerFilters",
    {},
  );
  const [limit, setLimit] = useMemory("explorerLimit", maximum);
  const [applied, setApplied] = useMemory<BrowseSpec>("explorerApplied", {
    mode: "all",
    filters: { statuses: [] },
    limit: maximum,
  });
  const [, clearPage] = useMemory<unknown>("pagination:browse-explorer", null);
  const field = (name: string, value: string) =>
    setFilters((current) => ({ ...current, [name]: value }));
  function submit(event: FormEvent) {
    event.preventDefault();
    clearPage(null);
    const next: Filters = {
      statuses: filters.status ? [filters.status as Summary["status"]] : [],
    };
    const arrays = [
      "kind",
      "origin",
      "sensitivity",
      "evidence_state",
      "temporal_role",
      "workflow_state",
    ] as const;
    const map = {
      kind: "kinds",
      origin: "origins",
      sensitivity: "sensitivities",
      evidence_state: "evidence_states",
      temporal_role: "temporal_roles",
      workflow_state: "workflow_states",
    };
    for (const key of arrays)
      if (filters[key]) Object.assign(next, { [map[key]]: [filters[key]] });
    if (filters.topic)
      next.topics = filters.topic
        .split(",")
        .map((text) => text.trim())
        .filter(Boolean)
        .slice(0, 16);
    if (filters.context) next.context_id = filters.context;
    if (filters.facet) next.facet = filters.facet;
    if (filters.confidence)
      next.minimum_confidence = Number(filters.confidence);
    if (filters.missing_topic) {
      next.missing_topic = true;
      next.topics = [];
    }
    if (filters.missing_context) {
      next.missing_context = true;
      delete next.context_id;
    }
    if (filters.missing_facet) {
      next.missing_facet = true;
      delete next.facet;
    }
    if (filters.review_due) next.review_due = true;
    for (const key of ["created_after", "created_before", "valid_at"] as const)
      if (filters[key]) next[key] = new Date(filters[key]).toISOString();
    setApplied({
      mode,
      filters: next,
      limit: Math.min(limit, maximum),
      ...(mode !== "all" ? { query: query.trim() } : {}),
    });
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">EXPLORE YOUR VAULT</p>
          <h1>Knowledge Explorer</h1>
          <p className="lead">
            Every retained assertion, with its context intact.
          </p>
        </div>
        <span className="readonly-label">Read-only</span>
      </div>
      <form className="filter-panel" onSubmit={submit}>
        <fieldset className="mode-switch">
          <legend className="sr-only">Search mode</legend>
          {(["all", "lexical", "ranked"] as const).map((option) => (
            <label key={option} className={mode === option ? "selected" : ""}>
              <input
                type="radio"
                name="mode"
                value={option}
                checked={mode === option}
                disabled={
                  option === "ranked" &&
                  !!(
                    filters.missing_topic ||
                    filters.missing_context ||
                    filters.missing_facet
                  )
                }
                onChange={() => setMode(option)}
              />
              {option === "all"
                ? "All retained"
                : option === "lexical"
                  ? "Exact text"
                  : "Ranked search"}
            </label>
          ))}
        </fieldset>
        {mode !== "all" && (
          <label className="field" htmlFor="query">
            {mode === "lexical"
              ? "Words or phrases to match"
              : "Search your knowledge"}
            <input
              id="query"
              maxLength={2000}
              required
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={
                mode === "lexical"
                  ? 'Words, or "an exact phrase"'
                  : "What are you looking for?"
              }
            />
          </label>
        )}
        <div className="filter-grid">
          <label className="field" htmlFor="topic">
            Topic
            <input
              id="topic"
              maxLength={1296}
              value={filters.topic ?? ""}
              onChange={(e) => field("topic", e.target.value)}
              placeholder="Any topic, comma-separated"
            />
          </label>
          <Field
            name="status"
            title="Evidence status"
            value={filters.status ?? ""}
            options={["current", "uncertain", "disputed", "superseded"]}
            onChange={(v) => field("status", v)}
          />
          <Field
            name="kind"
            title="Record kind"
            value={filters.kind ?? ""}
            options={KINDS}
            onChange={(v) => field("kind", v)}
          />
          <label className="field" htmlFor="context">
            Context ID
            <input
              id="context"
              pattern={String.raw`[0-9a-fA-F\-]{36}`}
              value={filters.context ?? ""}
              onChange={(e) => field("context", e.target.value)}
              placeholder="Any context (UUID)"
            />
          </label>
        </div>
        <details className="advanced">
          <summary>More filters</summary>
          <div className="filter-grid">
            <Field
              name="sensitivity"
              title="Sensitivity"
              value={filters.sensitivity ?? ""}
              options={["normal", "private", "sensitive"]}
              onChange={(v) => field("sensitivity", v)}
            />
            <Field
              name="evidence"
              title="Evidence classification"
              value={filters.evidence_state ?? ""}
              options={["unknown", "reported", "inferred", "verified"]}
              onChange={(v) => field("evidence_state", v)}
            />
            <Field
              name="workflow"
              title="Workflow state"
              value={filters.workflow_state ?? ""}
              options={[
                "proposed",
                "active",
                "completed",
                "cancelled",
                "unresolved",
                "resolved",
              ]}
              onChange={(v) => field("workflow_state", v)}
            />
            <Field
              name="origin"
              title="Origin"
              value={filters.origin ?? ""}
              options={[
                "user",
                "assistant",
                "joint",
                "external_source",
                "artifact",
              ]}
              onChange={(v) => field("origin", v)}
            />
            <Field
              name="temporal-role"
              title="Temporal role"
              value={filters.temporal_role ?? ""}
              options={["unspecified", "ongoing", "historical"]}
              onChange={(v) => field("temporal_role", v)}
            />
            <label className="field">
              Facet
              <input
                value={filters.facet ?? ""}
                maxLength={80}
                onChange={(e) => field("facet", e.target.value)}
              />
            </label>
            <label className="field">
              Minimum confidence
              <input
                type="number"
                min="0"
                max="1"
                step="0.05"
                value={filters.confidence ?? ""}
                onChange={(e) => field("confidence", e.target.value)}
              />
            </label>
            {(["created_after", "created_before", "valid_at"] as const).map(
              (name) => (
                <label className="field" key={name}>
                  {name.replaceAll("_", " ")} (local time)
                  <input
                    type="datetime-local"
                    value={filters[name] ?? ""}
                    onChange={(e) => field(name, e.target.value)}
                  />
                </label>
              ),
            )}
          </div>
          <div className="checkboxes">
            {[
              "missing_topic",
              "missing_context",
              "missing_facet",
              "review_due",
            ].map((name) => (
              <label key={name}>
                <input
                  type="checkbox"
                  disabled={mode === "ranked" && name.startsWith("missing_")}
                  checked={!!filters[name]}
                  onChange={(e) => field(name, e.target.checked ? "yes" : "")}
                />
                {name.replaceAll("_", " ")}
              </label>
            ))}
          </div>
          <p className="muted small">
            Unclassified filters are available in All retained and Exact text
            modes.
          </p>
        </details>
        <div className="filter-footer">
          <p className="muted small">
            All statuses are included by default. Sensitivity is a filter, not
            an access boundary.
          </p>
          <label className="compact-field">
            Page size
            <select
              value={Math.min(limit, maximum)}
              onChange={(e) => setLimit(Number(e.target.value))}
            >
              {[...new Set([10, 25, 50].map((v) => Math.min(v, maximum)))].map(
                (v) => (
                  <option key={v}>{v}</option>
                ),
              )}
            </select>
          </label>
          <button className="button primary">Apply filters</button>
          <button
            className="button text"
            type="button"
            onClick={() => {
              setFilters({});
              setQuery("");
              setMode("all");
              setLimit(maximum);
              setApplied({
                mode: "all",
                filters: { statuses: [] },
                limit: maximum,
              });
              clearPage(null);
            }}
          >
            Reset
          </button>
        </div>
      </form>
      <BrowseResults key={JSON.stringify(applied)} spec={applied} />
    </>
  );
}
export function BrowseResults({
  spec,
  memoryKey = "explorer",
}: {
  spec: BrowseSpec;
  memoryKey?: string;
}) {
  const { client } = useAuth();
  const pages = useCursors("browse-" + memoryKey + ":" + JSON.stringify(spec));
  const loader = useCallback(
    async (signal: AbortSignal) => {
      if (spec.mode === "ranked") {
        const {
          missing_topic: _topic,
          missing_context: _context,
          missing_facet: _facet,
          ...filters
        } = spec.filters;
        void _topic;
        void _context;
        void _facet;
        const result = await client.post(
          "search",
          {
            query: spec.query ?? "",
            filters,
            limit: spec.limit,
            cursor: pages.cursor,
          },
          signal,
        );
        return { ...result, items: result.items.map((hit) => hit.assertion) };
      }
      return client.post(
        "assertions/query",
        { ...spec, mode: spec.mode, cursor: pages.cursor },
        signal,
      );
    },
    [client, spec, pages.cursor],
  );
  const result = useLoad<Page<Summary> & { embedding_degraded?: boolean }>(
    loader,
  );
  const data = result.data;
  return (
    <section aria-labelledby="results-title">
      <div className="section-heading">
        <h2 id="results-title">
          {spec.mode === "ranked"
            ? "Ranked results"
            : spec.mode === "lexical"
              ? "Exact-text matches"
              : "All retained assertions"}
        </h2>
        <span className="small muted">
          {spec.mode === "ranked"
            ? "Bounded candidate set"
            : "Live keyset traversal"}
        </span>
      </div>
      <p className="muted small">
        {spec.mode === "ranked"
          ? "Ranked results are a bounded selection, not a complete list of matching knowledge."
          : "Every matching retained record is reachable. Concurrent edits may change membership; this is not a snapshot."}
      </p>
      {data?.embedding_degraded && (
        <p className="notice" role="status">
          Semantic retrieval is degraded. Text-based results remain available.
        </p>
      )}
      {result.loading ? (
        <Loading />
      ) : result.error ? (
        <ErrorNotice error={result.error} retry={result.retry} />
      ) : (
        data && (
          <>
            {data.items.length ? (
              <div className="record-list">
                {data.items.map((item) => (
                  <RecordCard item={item} key={item.id} />
                ))}
              </div>
            ) : (
              <Empty />
            )}
            <Pager
              index={pages.index}
              count={data.items.length}
              busy={false}
              previous={pages.back}
              next={
                data.next_cursor
                  ? () => pages.next(data.next_cursor!)
                  : undefined
              }
            />
            {!data.next_cursor && data.items.length > 0 && (
              <p className="end-note">
                {spec.mode === "ranked"
                  ? "End of the ranked candidate set. Switch to All retained to browse exhaustively."
                  : "End of this live view."}
              </p>
            )}
          </>
        )
      )}
    </section>
  );
}
