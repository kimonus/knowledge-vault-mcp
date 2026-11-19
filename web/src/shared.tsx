import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ApiError, isAbort } from "./api/client";
import { useMemory } from "./workspace";
import type { Summary } from "./api/client";

export function label(value: string | null | undefined) {
  return value ? value.replaceAll("_", " ") : "Unspecified";
}
export function when(value?: string | null) {
  if (!value) return "Not recorded";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toISOString().replace("T", " ").replace(".000Z", " UTC");
}
export function safeUrl(value: string) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) &&
      !url.username &&
      !url.password
      ? url.href
      : null;
  } catch {
    return null;
  }
}
export function SourceLink({
  value,
  children,
}: {
  value: string;
  children?: ReactNode;
}) {
  const href = safeUrl(value);
  return href ? (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children ?? value}
      <span className="sr-only"> (opens in new tab)</span>
    </a>
  ) : (
    <span>{children ?? value}</span>
  );
}
export function topicKey(value: string) {
  return btoa(String.fromCharCode(...new TextEncoder().encode(value)))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
}
export function topicValue(value: string) {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(
      Uint8Array.from(
        atob(value.replaceAll("-", "+").replaceAll("_", "/")),
        (char) => char.charCodeAt(0),
      ),
    );
  } catch {
    return "";
  }
}
export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return <span className={"badge badge-" + tone}>{children}</span>;
}
export function Status({ value }: { value: Summary["status"] }) {
  return (
    <Badge tone={value}>
      {value === "current" ? "Current evidence" : label(value)}
    </Badge>
  );
}
export function Empty({
  title = "No records in this view",
  children,
}: {
  title?: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <span className="empty-icon" aria-hidden="true">
        ◇
      </span>
      <h3>{title}</h3>
      <p>
        {children ??
          "Adjust your filters or browse another topic. Nothing is hidden by a ranking limit in All mode."}
      </p>
    </div>
  );
}
export function ErrorNotice({
  error,
  retry,
}: {
  error: unknown;
  retry?: () => void;
}) {
  const status = error instanceof ApiError ? error.status : 502;
  const text =
    status === 429
      ? "The read budget is temporarily exhausted. Wait before retrying."
      : status === 404
        ? "This retained record is unavailable. It may have been deleted."
        : status === 422
          ? "This request or continuation is no longer valid. Refresh the view or adjust your filters."
          : status === 401
            ? "Your session needs to be verified again."
            : "Knowledge is temporarily unavailable. Your vault has not been changed.";
  return (
    <div className="notice notice-error" role="alert">
      <strong>
        {status === 429
          ? "Please pause before retrying"
          : "Unable to load this view"}
      </strong>
      <p>
        {text}
        {error instanceof ApiError && error.retryAfter
          ? ` Retry after ${error.retryAfter} seconds.`
          : ""}
      </p>
      {retry && (
        <button className="button secondary" onClick={retry}>
          Try again
        </button>
      )}
    </div>
  );
}
export function Loading() {
  return (
    <div className="loading" role="status">
      <span className="spinner" aria-hidden="true" />
      Loading retained knowledge…
    </div>
  );
}
export function useLoad<T>(loader: (signal: AbortSignal) => Promise<T>) {
  const [state, setState] = useState<{
    data?: T;
    error?: unknown;
    loading: boolean;
  }>({ loading: true });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setState({ loading: true });
    loader(controller.signal)
      .then((data) => {
        if (active) setState({ data, loading: false });
      })
      .catch((error: unknown) => {
        if (active && !isAbort(error)) setState({ error, loading: false });
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [loader, attempt]);
  return { ...state, retry: () => setAttempt((n) => n + 1) };
}
export function Pager({
  next,
  previous,
  index,
  count,
  busy,
}: {
  next?: () => void;
  previous?: () => void;
  index: number;
  count: number;
  busy: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  return (
    <div className="pager" ref={ref}>
      <p aria-live="polite">
        Page {index} <span className="muted">· {count} on this page</span>
      </p>
      <div>
        <button
          className="button secondary"
          disabled={!previous || busy}
          onClick={previous}
        >
          Previous
        </button>
        <button
          className="button secondary"
          disabled={!next || busy}
          onClick={() => {
            next?.();
            ref.current?.scrollIntoView({ block: "nearest" });
          }}
        >
          Next page
        </button>
      </div>
    </div>
  );
}
export function useCursors(version = "default") {
  const initial = {
    version,
    cursor: null as string | null,
    previous: [] as (string | null)[],
    index: 1,
  };
  const [stored, set] = useMemory(
    "pagination:" + version.split(":")[0],
    initial,
  );
  const state = stored.version === version ? stored : initial;
  return {
    cursor: state.cursor,
    index: state.index,
    back: state.previous.length
      ? () =>
          set({
            ...state,
            cursor: state.previous[state.previous.length - 1],
            previous: state.previous.slice(0, -1),
            index: state.index - 1,
          })
      : undefined,
    next: (value: string) =>
      set({
        version,
        cursor: value,
        previous: [...state.previous, state.cursor].slice(-100),
        index: state.index + 1,
      }),
  };
}
export function RecordCard({ item }: { item: Summary }) {
  return (
    <article className="record-card">
      <div className="record-top">
        <div className="badges">
          <Status value={item.status} />
          <Badge
            tone={item.kind === "rejected_option" ? "rejected" : "neutral"}
          >
            {label(item.kind)}
          </Badge>
          {item.workflow_state && (
            <Badge tone="workflow">{label(item.workflow_state)}</Badge>
          )}
        </div>
        <span className="sensitivity">{label(item.sensitivity)}</span>
      </div>
      <h3>
        <Link to={"/assertions/" + item.id}>{item.excerpt}</Link>
      </h3>
      {item.content_truncated && (
        <p className="muted small">
          Excerpt · open the record for its complete content.
        </p>
      )}
      <div className="record-bottom">
        <div className="topics">
          {item.topics.length ? (
            item.topics.map((topic) => (
              <Link key={topic} to={"/portal/topics/" + topicKey(topic)}>
                #{topic}
              </Link>
            ))
          ) : (
            <span className="muted">No topic</span>
          )}
          {item.context_id && (
            <Link to={"/portal/contexts/" + item.context_id}>Context</Link>
          )}
        </div>
        <span className="small muted">
          {label(item.evidence_state)} evidence ·{" "}
          {when(item.observed_at ?? item.created_at)}
        </span>
      </div>
    </article>
  );
}
export function DataGrid({ value }: { value: Record<string, unknown> }) {
  return (
    <dl className="metadata-grid">
      {Object.entries(value).map(([key, val]) => (
        <div key={key}>
          <dt>{label(key)}</dt>
          <dd>
            {val === null || val === undefined || val === "" ? (
              "Not recorded"
            ) : typeof val === "object" ? (
              <pre className="data-text">{JSON.stringify(val, null, 2)}</pre>
            ) : (
              String(val)
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}
