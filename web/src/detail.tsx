import { useCallback, useState } from "react";
import type { ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { useAuth } from "./auth";
import type {
  Artifact,
  Detail,
  Identity,
  Page,
  Relation,
  Source,
} from "./api/client";
import {
  Badge,
  DataGrid,
  Empty,
  ErrorNotice,
  label,
  Loading,
  Pager,
  SourceLink,
  Status,
  topicKey,
  useCursors,
  useLoad,
  when,
} from "./shared";
import { TemporalView } from "./temporal";

export function ChildPage<T>({
  name,
  resource,
  initial,
  load,
  render,
}: {
  name: string;
  resource: string;
  initial?: Page<T>;
  load: (
    cursor: string | null,
    limit: number,
    signal: AbortSignal,
  ) => Promise<Page<T>>;
  render: (item: T, index: number) => ReactNode;
}) {
  const pages = useCursors("child-" + name + ":" + resource);
  const loader = useCallback(
    (signal: AbortSignal) =>
      !pages.cursor && initial
        ? Promise.resolve(initial)
        : load(pages.cursor, initial?.page_size ?? 50, signal),
    [pages.cursor, initial, load],
  );
  const result = useLoad(loader);
  if (result.loading) return <Loading />;
  if (result.error)
    return <ErrorNotice error={result.error} retry={result.retry} />;
  const page = result.data!;
  return (
    <>
      <div className="child-list">
        {page.items.length ? (
          page.items.map(render)
        ) : (
          <Empty title={"No " + name + " recorded"}>
            No retained {name} are linked to this record.
          </Empty>
        )}
      </div>
      <Pager
        count={page.items.length}
        index={pages.index}
        busy={false}
        previous={pages.back}
        next={
          page.next_cursor ? () => pages.next(page.next_cursor!) : undefined
        }
      />
    </>
  );
}

export function AssertionDetail() {
  const { id = "" } = useParams();
  const { client } = useAuth();
  const load = useCallback(
    (signal: AbortSignal) => client.get("assertions/{id}", id, {}, signal),
    [client, id],
  );
  const result = useLoad(load);
  return (
    <>
      <Link className="back-link" to="/explorer">
        Back to Explorer
      </Link>
      {result.loading ? (
        <Loading />
      ) : result.error ? (
        <ErrorNotice error={result.error} retry={result.retry} />
      ) : (
        <DetailContent key={id} item={result.data!} />
      )}
    </>
  );
}

function DetailContent({ item }: { item: Detail }) {
  const { client } = useAuth();
  const [section, setSection] = useState("Overview");
  const [identity, setIdentity] = useState<Identity | null>(null);
  const id = item.id;
  const sources = useCallback(
    (cursor: string | null, limit: number, signal: AbortSignal) =>
      client.get(
        "assertions/{id}/sources",
        id,
        { limit, ...(cursor ? { cursor } : {}) },
        signal,
      ),
    [client, id],
  );
  const artifacts = useCallback(
    (cursor: string | null, limit: number, signal: AbortSignal) =>
      client.get(
        "assertions/{id}/artifacts",
        id,
        { limit, ...(cursor ? { cursor } : {}) },
        signal,
      ),
    [client, id],
  );
  const identities = useCallback(
    (cursor: string | null, limit: number, signal: AbortSignal) =>
      client.get(
        "assertions/{id}/identities",
        id,
        { limit, ...(cursor ? { cursor } : {}) },
        signal,
      ),
    [client, id],
  );
  const relations = useCallback(
    (cursor: string | null, limit: number, signal: AbortSignal) =>
      client.get(
        "assertions/{id}/relations",
        id,
        { limit, ...(cursor ? { cursor } : {}) },
        signal,
      ),
    [client, id],
  );
  const dates = {
    created_at: when(item.created_at),
    observed_at: when(item.observed_at),
    updated_at: when(item.updated_at),
    valid_from: when(item.valid_from),
    valid_to: when(item.valid_to),
    last_confirmed_at: when(item.last_confirmed_at),
    last_verified_at: when(item.last_verified_at),
    review_after: when(item.review_after),
  };
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">ASSERTION RECORD</p>
          <h1>Knowledge & evidence</h1>
          <p className="record-id">{id}</p>
        </div>
        <Badge tone="neutral">{label(item.sensitivity)} sensitivity</Badge>
      </div>
      <div className="detail-lead">
        <div className="badges">
          <Status value={item.status} />
          <Badge>{label(item.kind)}</Badge>
          <Badge tone="workflow">{label(item.workflow_state)}</Badge>
          <Badge>{label(item.evidence_state)} evidence</Badge>
          <Badge>{label(item.temporal_role)}</Badge>
        </div>
        <p className="assertion-content">{item.content}</p>
        <div className="topics">
          {item.topics.map((topic) => (
            <Link key={topic} to={"/portal/topics/" + topicKey(topic)}>
              #{topic}
            </Link>
          ))}
          {item.context_id && (
            <Link to={"/portal/contexts/" + item.context_id}>Open context</Link>
          )}
        </div>
        <p className="muted small">
          “Current” means active evidence, not established truth. Ingestion
          confirmations are not independent verification.
        </p>
      </div>
      <nav className="detail-tabs" aria-label="Record sections">
        {[
          "Overview",
          "Sources",
          "Artifacts",
          "Relationships",
          "Temporal evidence",
        ].map((name) => (
          <button
            key={name}
            className={section === name ? "active" : ""}
            aria-pressed={section === name}
            onClick={() => {
              setSection(name);
              setIdentity(null);
            }}
          >
            {name}
          </button>
        ))}
      </nav>
      <section className="panel" aria-label={section}>
        {section === "Overview" && (
          <>
            <h2>Record metadata</h2>
            <DataGrid
              value={{
                kind: label(item.kind),
                origin: label(item.origin),
                status: label(item.status),
                confidence: item.confidence,
                evidence_state: label(item.evidence_state),
                temporal_role: label(item.temporal_role),
                workflow_state: label(item.workflow_state),
                revision: item.revision,
                confirmation_count: item.confirmation_count,
                facet: item.facet,
                embedding_state: item.embedding_state,
              }}
            />
            <h2>Recorded times</h2>
            <p className="muted small">
              UTC timestamps describe retained metadata, not a complete edit
              archive.
            </p>
            <DataGrid value={dates} />
            {item.supersedes_id && (
              <p>
                Supersedes{" "}
                <Link to={"/assertions/" + item.supersedes_id}>
                  {item.supersedes_id}
                </Link>
                . Reverse supersession is listed in Relationships.
              </p>
            )}
            <h2>Provenance</h2>
            {item.provenance?.length ? (
              item.provenance.map((entry, index) => (
                <div className="sub-card" key={index}>
                  <h3>{entry.title ?? label(entry.kind)}</h3>
                  <DataGrid value={entry} />
                </div>
              ))
            ) : (
              <p className="muted">No additional provenance is recorded.</p>
            )}
            <h2>Usage availability</h2>
            <p className="muted small">
              Inspection does not record retrieval usage.
            </p>
            {!item.usage?.available && (
              <p className="notice">
                Usage accounting is unavailable; zero counters are not
                authoritative.
              </p>
            )}
            <DataGrid value={item.usage ?? { available: false }} />
          </>
        )}
        {section === "Sources" && (
          <>
            <h2>Sources</h2>
            <ChildPage<Source>
              name="sources"
              resource={id}
              initial={item.source_page}
              load={sources}
              render={(source, index) => (
                <article className="sub-card" key={index}>
                  <h3>
                    <SourceLink value={source.url}>
                      {source.title ?? source.url}
                    </SourceLink>
                  </h3>
                  <p className="muted">
                    {source.publisher ?? "Publisher not recorded"}
                  </p>
                  <p className="small">Retrieved {when(source.retrieved_at)}</p>
                  <p className="locator">{source.url}</p>
                </article>
              )}
            />
          </>
        )}
        {section === "Artifacts" && (
          <>
            <h2>Text artifacts</h2>
            <p className="muted small">
              Stored text is shown as inert content regardless of its media
              type. No previews are fetched.
            </p>
            <ChildPage<Artifact>
              name="artifacts"
              resource={id}
              initial={item.artifact_page}
              load={artifacts}
              render={(artifact) => (
                <ArtifactViewer key={artifact.id} artifact={artifact} />
              )}
            />
          </>
        )}
        {section === "Relationships" && (
          <>
            <h2>Explicit relationships</h2>
            <p className="muted small">
              Only retained direct links are shown. Direction and position are
              preserved; no relationships are inferred.
            </p>
            <ChildPage<Relation>
              name="relationships"
              resource={id}
              load={relations}
              render={(relation, index) => (
                <RelationCard key={index} relation={relation} />
              )}
            />
          </>
        )}
        {section === "Temporal evidence" && (
          <>
            <h2>Recorded state identities</h2>
            <p className="muted small">
              Choose an exact identity to inspect resolver outcomes and
              immutable evidence. Unclassified legacy records have no inferred
              history.
            </p>
            <ChildPage<Identity>
              name="identities"
              resource={id}
              initial={item.identity_page}
              load={identities}
              render={(entry, index) => (
                <button
                  key={index}
                  className="identity-button"
                  onClick={() => setIdentity(entry)}
                >
                  {entry.subject} / {entry.property}
                  <span>
                    {entry.scope} · {entry.state_type}
                  </span>
                </button>
              )}
            />
            {identity && (
              <TemporalView
                key={JSON.stringify(identity)}
                identity={identity}
              />
            )}
          </>
        )}
      </section>
    </>
  );
}

export function RelationCard({ relation }: { relation: Relation }) {
  if (relation.type === "link" && relation.edge)
    return (
      <article className="sub-card">
        <Badge>{label(relation.edge.kind)}</Badge>
        <p>
          <Link to={"/assertions/" + relation.edge.source_id}>
            {relation.edge.source_id}
          </Link>{" "}
          <span className="muted">to</span>{" "}
          <Link to={"/assertions/" + relation.edge.target_id}>
            {relation.edge.target_id}
          </Link>
        </p>
        {relation.edge.position != null && (
          <p>Position: {relation.edge.position}</p>
        )}
      </article>
    );
  if (relation.type === "supersession")
    return (
      <article className="sub-card">
        <Badge tone="superseded">Supersession</Badge>
        <p>
          Predecessor{" "}
          <Link to={"/assertions/" + relation.predecessor_id}>
            {relation.predecessor_id}
          </Link>
        </p>
        <p>
          Successor{" "}
          <Link to={"/assertions/" + relation.successor_id}>
            {relation.successor_id}
          </Link>
        </p>
      </article>
    );
  return (
    <article className="sub-card">
      <Badge tone="disputed">
        {relation.conflict?.resolved_at
          ? "Resolved conflict record"
          : "Recorded conflict"}
      </Badge>
      <p>{relation.conflict?.reason}</p>
      <p>
        <Link to={"/assertions/" + relation.conflict?.left_assertion_id}>
          {relation.conflict?.left_assertion_id}
        </Link>{" "}
        and{" "}
        <Link to={"/assertions/" + relation.conflict?.right_assertion_id}>
          {relation.conflict?.right_assertion_id}
        </Link>
      </p>
      <p className="small muted">
        Recorded {when(relation.conflict?.created_at)} · Resolved{" "}
        {when(relation.conflict?.resolved_at)}
      </p>
    </article>
  );
}

export function ArtifactViewer({ artifact }: { artifact: Artifact }) {
  const { client } = useAuth();
  const [open, setOpen] = useState(false);
  return (
    <article className="sub-card">
      <div className="section-heading">
        <h3>{artifact.filename}</h3>
        <button
          className="button secondary"
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
        >
          {open ? "Close text" : "Read text"}
        </button>
      </div>
      <p className="muted small">
        {artifact.media_type} · {artifact.size_bytes} bytes
      </p>
      {artifact.description && <p>{artifact.description}</p>}
      {open && <ArtifactText id={artifact.id} client={client} />}
    </article>
  );
}
function ArtifactText({
  id,
  client,
}: {
  id: string;
  client: ReturnType<typeof useAuth>["client"];
}) {
  const [offset, setOffset] = useState(0);
  const [previous, setPrevious] = useState<number[]>([]);
  const load = useCallback(
    (signal: AbortSignal) =>
      client.get("artifacts/{id}", id, { offset, limit: 8000 }, signal),
    [client, id, offset],
  );
  const result = useLoad(load);
  return result.loading ? (
    <Loading />
  ) : result.error ? (
    <ErrorNotice error={result.error} retry={result.retry} />
  ) : (
    <>
      <p className="small muted">
        Characters {result.data!.offset}–
        {result.data!.offset + [...result.data!.content].length} of{" "}
        {result.data!.total_chars}
      </p>
      <pre className="artifact-text" tabIndex={0} aria-label="Artifact text">
        {result.data!.content}
      </pre>
      <div className="pager">
        <button
          className="button secondary"
          disabled={!previous.length}
          onClick={() => {
            setOffset(previous[previous.length - 1]);
            setPrevious((items) => items.slice(0, -1));
          }}
        >
          Previous text
        </button>
        <button
          className="button secondary"
          disabled={result.data!.next_offset == null}
          onClick={() => {
            setPrevious((items) => [...items, offset].slice(-100));
            setOffset(result.data!.next_offset!);
          }}
        >
          Next text
        </button>
      </div>
    </>
  );
}
