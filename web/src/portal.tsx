import { useCallback, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useAuth } from "./auth";
import { useMemory } from "./workspace";
import type { Catalog, Filters } from "./api/client";
import { BrowseResults, Field } from "./explorer";
import {
  Badge,
  Empty,
  ErrorNotice,
  label,
  Loading,
  Pager,
  topicKey,
  topicValue,
  useCursors,
  useLoad,
} from "./shared";

type Dimension = "topics" | "contexts" | "facets";
export function Portal() {
  const [dimension, setDimension] = useState<Dimension>("topics");
  const [prefix, setPrefix] = useState("");
  const [applied, setApplied] = useState("");
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">KNOWLEDGE, CONNECTED BY CONTEXT</p>
          <h1>Knowledge Portal</h1>
          <p className="lead">
            Follow the topics and contexts already present in your vault.
          </p>
        </div>
        <span className="readonly-label">Deterministic views</span>
      </div>
      <div className="portal-intro">
        <div>
          <h2>Start with a theme</h2>
          <p>
            These are projections of retained records, not generated summaries.
            Topic aliases are explicit; relationships are never inferred.
          </p>
        </div>
        <p className="small">
          A record may appear under several topics. Counts overlap and should
          not be added together.
        </p>
      </div>
      <form
        className="catalog-toolbar"
        onSubmit={(event) => {
          event.preventDefault();
          setApplied(prefix);
        }}
      >
        <div className="tabs" role="group" aria-label="Catalog dimension">
          {(["topics", "contexts", "facets"] as const).map((value) => (
            <button
              type="button"
              key={value}
              aria-pressed={value === dimension}
              className={value === dimension ? "active" : ""}
              onClick={() => setDimension(value)}
            >
              {label(value)}
            </button>
          ))}
        </div>
        <label className="sr-only" htmlFor="prefix">
          Catalog prefix
        </label>
        <input
          id="prefix"
          value={prefix}
          maxLength={80}
          onChange={(event) => setPrefix(event.target.value)}
          placeholder="Filter by case-sensitive prefix"
        />
        <button className="button secondary">Find themes</button>
      </form>
      {dimension === "facets" && (
        <p className="notice">
          Facets are enumerated across all contexts here. Open a context to
          inspect its own facet catalog.
        </p>
      )}
      <CatalogGrid
        key={dimension + applied}
        dimension={dimension}
        prefix={applied}
      />
    </>
  );
}
function CatalogGrid({
  dimension,
  prefix,
}: {
  dimension: Dimension;
  prefix: string;
}) {
  const { client } = useAuth();
  const pages = useCursors("catalog:" + dimension + prefix);
  const loader = useCallback(
    (signal: AbortSignal) =>
      client.post(
        "catalog/query",
        {
          dimension,
          filters: { statuses: [] },
          prefix: prefix || null,
          limit: 50,
          cursor: pages.cursor,
          unscoped: dimension === "facets",
        },
        signal,
      ),
    [client, dimension, prefix, pages.cursor],
  );
  const result = useLoad(loader);
  return result.loading ? (
    <Loading />
  ) : result.error ? (
    <ErrorNotice error={result.error} retry={result.retry} />
  ) : (
    <>
      {result.data!.items.length ? (
        <div className="catalog-grid">
          {result.data!.items.map((item) => (
            <CatalogCard
              key={item.key ?? "__missing"}
              item={item}
              dimension={dimension}
            />
          ))}
        </div>
      ) : (
        <Empty title="No themes in this view">
          Try a different prefix or catalog. Unclassified records remain
          accessible through uncategorized groups.
        </Empty>
      )}
      <Pager
        index={pages.index}
        count={result.data!.items.length}
        busy={false}
        previous={pages.back}
        next={
          result.data!.next_cursor
            ? () => pages.next(result.data!.next_cursor!)
            : undefined
        }
      />
      <p className="small muted">
        Live catalog · {result.data!.grouping_version} grouping version · counts
        may change between requests.
      </p>
    </>
  );
}
function CatalogCard({
  item,
  dimension,
}: {
  item: Catalog["items"][number];
  dimension: Dimension;
}) {
  const path =
    item.key === null
      ? "/portal/unclassified/" + dimension
      : dimension === "contexts"
        ? "/portal/contexts/" + item.key
        : "/portal/topics/" + topicKey(item.key);
  // Facets are selected in memory; their keys do not become URL search/filter parameters.
  const [, selectFacet] = useMemory<string | undefined>(
    "globalFacet",
    undefined,
  );
  return (
    <article className="catalog-card">
      <p className="eyebrow">
        {dimension === "topics"
          ? "TOPIC"
          : dimension === "contexts"
            ? "CONTEXT"
            : "FACET"}
      </p>
      <h2>
        <Link
          to={
            dimension === "facets" && item.key !== null
              ? "/portal/unclassified/facets"
              : path
          }
          onClick={() => {
            if (dimension === "facets") selectFacet(item.key ?? undefined);
          }}
        >
          {item.label}
        </Link>
      </h2>
      <p className="catalog-count">
        <strong>{item.assertion_count}</strong> retained{" "}
        {item.assertion_count === 1 ? "assertion" : "assertions"}
      </p>
      <div className="status-counts">
        {Object.entries(item.by_status)
          .filter(([, count]) => count > 0)
          .map(([status, count]) => (
            <span key={status}>
              {status === "current" ? "Current evidence" : label(status)}{" "}
              <b>{count}</b>
            </span>
          ))}
      </div>
      {(item.stored_labels?.length ?? 0) > 1 && (
        <p className="small muted">
          Stored labels: {item.stored_labels?.join(", ")}
        </p>
      )}
      <details>
        <summary>Evidence & record kinds</summary>
        <div className="counts">
          {Object.entries(item.by_kind).map(([kind, count]) => (
            <p key={kind}>
              {label(kind)} <strong>{count}</strong>
            </p>
          ))}
          {Object.entries(item.by_evidence_state).map(([kind, count]) => (
            <p key={kind}>
              {label(kind)} evidence <strong>{count}</strong>
            </p>
          ))}
          {Object.entries(item.by_workflow_state).map(([kind, count]) => (
            <p key={kind}>
              {label(kind)} workflow <strong>{count}</strong>
            </p>
          ))}
        </div>
      </details>
    </article>
  );
}

const SECTIONS: Record<string, NonNullable<Filters["kinds"]>> = {
  "All records": [],
  Facts: ["user_fact", "external_fact", "derived_conclusion"],
  Decisions: ["decision"],
  Configuration: ["configuration"],
  Preferences: ["preference"],
  Procedures: ["procedure"],
  Plans: ["plan"],
  Questions: ["open_question"],
  "Considered options": ["considered_option"],
  "Rejected options": ["rejected_option"],
  Artifacts: ["artifact_observation"],
};
export function Theme({
  dimension,
  facet,
}: {
  dimension: Dimension;
  facet?: string;
}) {
  const params = useParams();
  const topic =
    dimension === "topics" && params.key ? topicValue(params.key) : "";
  const id = params.id;
  const missing = params.dimension;
  const { client } = useAuth();
  const [section, setSection] = useState("All records");
  const [status, setStatus] = useState("");
  const [selectedFacet, setFacet] = useState<string | null | undefined>(facet);
  const [historical, setHistorical] = useState(false);
  const contextLoader = useCallback(
    (signal: AbortSignal) =>
      id ? client.get("contexts/{id}", id, {}, signal) : Promise.resolve(null),
    [client, id],
  );
  const context = useLoad(contextLoader);
  const aliasesLoader = useCallback(
    (signal: AbortSignal) =>
      topic
        ? client.get("topic-aliases", undefined, {}, signal)
        : Promise.resolve(null),
    [client, topic],
  );
  const aliases = useLoad(aliasesLoader);
  if (dimension === "topics" && params.key && !topic) {
    return (
      <p role="alert">
        This topic address is invalid. Return to the Portal to choose a stored
        topic.
      </p>
    );
  }
  const title = id
    ? (context.data?.name ?? "Context")
    : topic ||
      (facet ? "Facet: " + facet : "Uncategorized " + (missing ?? dimension));
  const filters: Filters = {
    statuses: status ? [status as "current"] : [],
    kinds: SECTIONS[section],
  };
  if (id) filters.context_id = id;
  if (topic) filters.topics = [topic];
  if (missing && !facet)
    Object.assign(filters, {
      ["missing_" +
      ({ topics: "topic", contexts: "context", facets: "facet" }[missing] ??
        "topic")]: true,
    });
  if (selectedFacet === null) filters.missing_facet = true;
  else if (selectedFacet !== undefined) {
    delete filters.missing_facet;
    filters.facet = selectedFacet;
  }
  if (historical) filters.temporal_roles = ["historical"];
  const explicitAliases = Object.entries(aliases.data?.aliases ?? {})
    .filter(([, canonical]) => canonical === topic)
    .map(([alias]) => alias);
  return (
    <>
      <Link className="back-link" to="/portal">
        Back to Portal
      </Link>
      <div className="page-heading">
        <div>
          <p className="eyebrow">
            {id
              ? "STORED CONTEXT"
              : topic
                ? "STORED TOPIC"
                : "CLASSIFICATION VIEW"}
          </p>
          <h1>{title}</h1>
          <p className="lead">
            A live view of underlying records. No generated summaries or
            inferred links.
          </p>
        </div>
      </div>
      {context.loading ? (
        <Loading />
      ) : context.error ? (
        <ErrorNotice error={context.error} retry={context.retry} />
      ) : (
        <>
          {id && (
            <div className="notice">
              <p>Context ID: {id}</p>
              <p>
                Explicit aliases:{" "}
                {context.data?.aliases?.join(", ") || "None recorded"}
              </p>
            </div>
          )}
          {topic && (
            <p className="notice">
              Explicit topic aliases:{" "}
              {explicitAliases.length
                ? explicitAliases.join(", ")
                : "None recorded"}
              . Canonical grouping preserves the original stored labels.
            </p>
          )}
          {aliases.error && (
            <ErrorNotice error={aliases.error} retry={aliases.retry} />
          )}
          {id && (
            <FacetPicker id={id} selected={selectedFacet} select={setFacet} />
          )}
          <div className="theme-controls">
            <Field
              name="theme-section"
              title="Record section"
              value={section === "All records" ? "" : section}
              options={Object.keys(SECTIONS).filter(
                (key) => key !== "All records",
              )}
              onChange={(value) => setSection(value || "All records")}
            />
            <Field
              name="theme-status"
              title="Evidence status"
              value={status}
              options={["current", "uncertain", "disputed", "superseded"]}
              onChange={setStatus}
            />
            <label className="check-field">
              <input
                type="checkbox"
                checked={historical}
                onChange={(event) => setHistorical(event.target.checked)}
              />
              Historical evidence only
            </label>
          </div>
          <BrowseResults
            key={JSON.stringify(filters)}
            memoryKey="theme"
            spec={{ mode: "all", filters, limit: 50 }}
          />
        </>
      )}
    </>
  );
}
function FacetPicker({
  id,
  selected,
  select,
}: {
  id: string;
  selected: string | null | undefined;
  select: (value: string | null | undefined) => void;
}) {
  const { client } = useAuth();
  const pages = useCursors("facets:" + id);
  const load = useCallback(
    (signal: AbortSignal) =>
      client.post(
        "catalog/query",
        {
          dimension: "facets",
          filters: { statuses: [], context_id: id },
          limit: 50,
          cursor: pages.cursor,
        },
        signal,
      ),
    [client, id, pages.cursor],
  );
  const result = useLoad(load);
  return (
    <section className="facet-picker" aria-label="Context facets">
      <h2>Facets in this context</h2>
      {result.loading ? (
        <Loading />
      ) : result.error ? (
        <ErrorNotice error={result.error} retry={result.retry} />
      ) : (
        <>
          <div className="facet-buttons">
            <button
              className="button secondary"
              aria-pressed={selected === undefined}
              onClick={() => select(undefined)}
            >
              All facets
            </button>
            {result.data!.items.map((item) => (
              <button
                className="button secondary"
                aria-pressed={selected === item.key}
                key={item.key ?? "missing"}
                onClick={() => select(item.key)}
              >
                {item.label} <Badge>{item.assertion_count}</Badge>
              </button>
            ))}
          </div>
          <Pager
            count={result.data!.items.length}
            index={pages.index}
            busy={false}
            previous={pages.back}
            next={
              result.data!.next_cursor
                ? () => pages.next(result.data!.next_cursor!)
                : undefined
            }
          />
        </>
      )}
    </section>
  );
}
