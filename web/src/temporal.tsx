import { useCallback, useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "./auth";
import type { Identity } from "./api/client";
import { ApiError } from "./api/client";
import {
  Badge,
  DataGrid,
  Empty,
  ErrorNotice,
  label,
  Loading,
  Pager,
  useCursors,
  useLoad,
  when,
} from "./shared";

export function StatePage() {
  const [identity, setIdentity] = useState<Identity | null>(null);
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setIdentity({
      subject: String(data.get("subject")),
      property: String(data.get("property")),
      scope: String(data.get("scope")),
      state_type: String(data.get("state_type")) as Identity["state_type"],
    });
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">EVIDENCE THROUGH TIME</p>
          <h1>Temporal evidence</h1>
          <p className="lead">
            Ask what the retained evidence supports, at an exact identity and
            time.
          </p>
        </div>
      </div>
      <form className="filter-panel" onSubmit={submit}>
        <div className="filter-grid">
          {["subject", "property", "scope"].map((key) => (
            <label className="field" key={key}>
              {key[0].toUpperCase() + key.slice(1)}
              <input
                name={key}
                required
                maxLength={200}
                pattern={String.raw`[a-z0-9_.:\/\-]+`}
                placeholder={
                  key === "subject"
                    ? "Exact stored subject"
                    : "Exact stored " + key
                }
              />
            </label>
          ))}
          <label className="field">
            State type
            <select name="state_type">
              <option value="configuration">Configuration</option>
              <option value="preference">Preference</option>
              <option value="fact">Fact</option>
            </select>
          </label>
        </div>
        <p className="muted small">
          Use a stored identity or select one from an assertion’s Temporal
          evidence section. Unknown identities are not inferred.
        </p>
        <button className="button primary">Inspect evidence</button>
      </form>
      {identity && (
        <TemporalView key={JSON.stringify(identity)} identity={identity} />
      )}
    </>
  );
}
export function TemporalView({ identity }: { identity: Identity }) {
  const [applied, setApplied] = useState({ as_of: "", known_at: "" });
  const [draft, setDraft] = useState(applied);
  return (
    <section className="temporal-view">
      <div className="section-heading">
        <h2>
          {identity.subject} / {identity.property}
        </h2>
        <Badge>{identity.state_type}</Badge>
      </div>
      <p className="muted">Scope: {identity.scope}</p>
      <form
        className="temporal-controls"
        onSubmit={(event) => {
          event.preventDefault();
          setApplied({ ...draft });
        }}
      >
        <label className="field">
          Applicable at (as of, local time)
          <input
            type="datetime-local"
            value={draft.as_of}
            onChange={(e) => setDraft((p) => ({ ...p, as_of: e.target.value }))}
          />
        </label>
        <label className="field">
          Evidence known by (local time)
          <input
            type="datetime-local"
            value={draft.known_at}
            onChange={(e) =>
              setDraft((p) => ({ ...p, known_at: e.target.value }))
            }
          />
        </label>
        <button className="button secondary">Apply times</button>
      </form>
      <p className="muted small">
        “As of” is real-world applicability. “Known by” is evidence
        receipt/retirement, not an edit archive. Low-precision dates remain
        explicitly labelled.
      </p>
      <TemporalResults
        key={JSON.stringify(applied)}
        identity={identity}
        times={applied}
      />
    </section>
  );
}
function TemporalResults({
  identity,
  times,
}: {
  identity: Identity;
  times: { as_of: string; known_at: string };
}) {
  const { client } = useAuth();
  const [history, setHistory] = useState<string | null>(null);
  const [prior, setPrior] = useState<(string | null)[]>([]);
  const [index, setIndex] = useState(1);
  const raw = useCursors("evidence:" + JSON.stringify({ identity, times }));
  const loader = useCallback(
    (signal: AbortSignal) =>
      client.post(
        "state/resolve",
        {
          identity,
          as_of: times.as_of ? new Date(times.as_of).toISOString() : null,
          known_at: times.known_at
            ? new Date(times.known_at).toISOString()
            : null,
          history_after: history,
          history_limit: 20,
        },
        signal,
      ),
    [client, identity, times, history],
  );
  const rawLoader = useCallback(
    (signal: AbortSignal) =>
      client.post(
        "state/evidence/query",
        {
          identity,
          known_at: times.known_at
            ? new Date(times.known_at).toISOString()
            : null,
          limit: 50,
          cursor: raw.cursor,
        },
        signal,
      ),
    [client, identity, times, raw.cursor],
  );
  const result = useLoad(loader);
  const evidence = useLoad(rawLoader);
  const resolution = result.data;
  return (
    <>
      <div className="resolution">
        <h3>State resolution</h3>
        {result.loading ? (
          <Loading />
        ) : result.error ? (
          <>
            <ErrorNotice error={result.error} retry={result.retry} />
            {result.error instanceof ApiError &&
              result.error.status === 422 && (
                <p className="notice">
                  State unresolved: evidence limit or request rejection. No
                  partial winner is selected. Raw evidence remains available
                  below.
                </p>
              )}
          </>
        ) : (
          resolution && (
            <>
              <div className="badges">
                <Badge
                  tone={
                    resolution.outcome === "resolved" ? "verified" : "disputed"
                  }
                >
                  {label(resolution.outcome)}
                </Badge>
                <span className="small muted">
                  Resolver {resolution.resolver_version} ·{" "}
                  {resolution.evidence_total} evidence records
                </span>
              </div>
              <h4>
                {resolution.outcome === "resolved"
                  ? "Supported value"
                  : "Candidate values (no winner)"}
              </h4>
              {resolution.values.length ? (
                <ul>
                  {resolution.values.map((value, n) => (
                    <li className="plain-value" key={n}>
                      {value}
                    </li>
                  ))}
                </ul>
              ) : (
                <p>No supported value is available.</p>
              )}
              <DataGrid
                value={{
                  reasons: resolution.reasons,
                  supporting_keys: resolution.supporting_keys,
                  competing_keys: resolution.competing_keys,
                  as_of: when(resolution.as_of),
                  known_at: when(resolution.known_at),
                }}
              />
              <h4>Resolver evidence history</h4>
              <p className="small muted">
                Ordered by evidence key, not guaranteed chronology.
              </p>
              {resolution.history.map((entry) => (
                <article className="sub-card" key={entry.evidence_key}>
                  <Badge tone="workflow">{entry.disposition}</Badge>
                  <p>
                    <Link to={"/assertions/" + entry.assertion_id}>
                      {entry.evidence_key}
                    </Link>
                  </p>
                  <p className="plain-value">{entry.assertion.content}</p>
                  <DataGrid
                    value={{
                      recorded_at: when(entry.recorded_at),
                      ordering_at: when(entry.ordering_at),
                      ordering_basis: entry.ordering_basis,
                      temporal: entry.assertion.temporal,
                    }}
                  />
                </article>
              ))}
              <Pager
                index={index}
                count={resolution.history.length}
                busy={false}
                previous={
                  prior.length
                    ? () => {
                        setHistory(prior[prior.length - 1]);
                        setPrior((p) => p.slice(0, -1));
                        setIndex((n) => n - 1);
                      }
                    : undefined
                }
                next={
                  resolution.next_history_after
                    ? () => {
                        setPrior((p) => [...p, history].slice(-100));
                        setHistory(resolution.next_history_after!);
                        setIndex((n) => n + 1);
                      }
                    : undefined
                }
              />
            </>
          )
        )}
      </div>
      <div className="raw-evidence">
        <h3>Raw immutable evidence</h3>
        <p className="muted small">
          Every retained evidence record is reachable here, including when
          resolution overflows. Retirement and proposal/hypothesis modes do not
          establish a winning state.
        </p>
        {evidence.loading ? (
          <Loading />
        ) : evidence.error ? (
          <ErrorNotice error={evidence.error} retry={evidence.retry} />
        ) : (
          evidence.data && (
            <>
              {evidence.data.items.length ? (
                evidence.data.items.map((entry) => (
                  <details className="sub-card" key={entry.evidence_key}>
                    <summary>
                      {entry.evidence_key} ·{" "}
                      {entry.assertion.temporal?.mode ?? "Unclassified"} ·{" "}
                      {entry.retired ? "Retired" : "Retained"}
                    </summary>
                    <p>
                      <Link to={"/assertions/" + entry.assertion_id}>
                        Open underlying assertion
                      </Link>
                    </p>
                    <p className="plain-value">{entry.assertion.content}</p>
                    <DataGrid
                      value={{
                        recorded_at: when(entry.recorded_at),
                        ...entry.assertion,
                      }}
                    />
                  </details>
                ))
              ) : (
                <Empty title="No retained temporal evidence">
                  No evidence exists for this exact identity and receipt cutoff.
                </Empty>
              )}
              <Pager
                count={evidence.data.items.length}
                index={raw.index}
                busy={false}
                previous={raw.back}
                next={
                  evidence.data.next_cursor
                    ? () => raw.next(evidence.data!.next_cursor!)
                    : undefined
                }
              />
            </>
          )
        )}
      </div>
    </>
  );
}
