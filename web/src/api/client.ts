import type { ReadRequests, ReadResponses } from "./schema";

export type Session = {
  principal_id: string;
  auth_mode: "privateSession" | "cloudflareAccess";
  expires_at: number;
  csrf_token: string;
  capabilities: string[];
};
export type Filters = NonNullable<ReadRequests["assertions/query"]["filters"]>;
export type Summary = ReadResponses["assertions/query"]["items"][number];
export type Detail = ReadResponses["assertions/{id}"];
export type Identity = ReadRequests["state/resolve"]["identity"];
export type Source = ReadResponses["assertions/{id}/sources"]["items"][number];
export type Artifact =
  ReadResponses["assertions/{id}/artifacts"]["items"][number];
export type Relation =
  ReadResponses["assertions/{id}/relations"]["items"][number];
export type Catalog = ReadResponses["catalog/query"];
export type Page<T> = {
  items: T[];
  page_size: number;
  next_cursor?: string | null;
  evaluated_at: string;
  consistency?: "live";
};

const GETS = [
  "assertions/{id}",
  "assertions/{id}/sources",
  "assertions/{id}/artifacts",
  "assertions/{id}/identities",
  "assertions/{id}/relations",
  "contexts/{id}",
  "topic-aliases",
  "artifacts/{id}",
] as const;
const POSTS = [
  "assertions/query",
  "catalog/query",
  "search",
  "state/resolve",
  "state/evidence/query",
] as const;
type Get = (typeof GETS)[number];
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    public retryAfter?: string,
  ) {
    super(code);
  }
}
export const isAbort = (error: unknown) =>
  error instanceof DOMException && error.name === "AbortError";

export class Client {
  private pending = new AbortController();
  csrf = "";
  constructor(private expired: () => void) {}
  clear() {
    this.pending.abort();
    this.pending = new AbortController();
    this.csrf = "";
  }
  private failAuth(): never {
    this.clear();
    this.expired();
    throw new ApiError(401, "authentication_required");
  }
  async request<T>(
    path: string,
    body?: unknown,
    signal?: AbortSignal,
  ): Promise<T> {
    const headers: Record<string, string> = {
      Accept: "application/json",
      "X-Requested-With": "XMLHttpRequest",
    };
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      headers["X-Dashboard-Request"] = "1";
      if (this.csrf) headers["X-CSRF-Token"] = this.csrf;
    }
    let response: Response;
    try {
      response = await fetch("/dashboard/api/v1/" + path, {
        method: body === undefined ? "GET" : "POST",
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        credentials: "same-origin",
        cache: "no-store",
        redirect: "error",
        signal: AbortSignal.any([
          this.pending.signal,
          ...(signal ? [signal] : []),
        ]),
      });
    } catch (error) {
      if (isAbort(error)) throw error;
      return this.failAuth();
    }
    if (
      response.redirected ||
      response.status === 401 ||
      response.status === 403
    )
      return this.failAuth();
    if (!response.ok) {
      const retry = response.headers.get("Retry-After") ?? "";
      throw new ApiError(
        response.status,
        "request_failed",
        /^[0-9]{1,5}$/.test(retry) ? retry : undefined,
      );
    }
    if (response.status === 204) return undefined as T;
    if (
      response.headers.get("Content-Type")?.split(";")[0] !== "application/json"
    )
      return this.failAuth();
    const reader = response.body?.getReader();
    if (!reader) throw new ApiError(502, "invalid_response");
    const chunks: Uint8Array[] = [];
    let size = 0;
    try {
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        size += chunk.value.length;
        if (size > 2_000_000) {
          await reader.cancel();
          throw new ApiError(502, "response_too_large");
        }
        chunks.push(chunk.value);
      }
      const bytes = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) {
        bytes.set(chunk, offset);
        offset += chunk.length;
      }
      const value: unknown = JSON.parse(new TextDecoder().decode(bytes));
      if (
        !value ||
        typeof value !== "object" ||
        ("untrusted_data" in value && value.untrusted_data !== true)
      )
        throw new Error("invalid response");
      return value as T;
    } catch (error) {
      if (error instanceof ApiError || isAbort(error)) throw error;
      throw new ApiError(502, "invalid_response");
    } finally {
      reader.releaseLock();
    }
  }
  async session(signal?: AbortSignal) {
    const session = await this.request<Session>("session", undefined, signal);
    if (
      typeof session.expires_at !== "number" ||
      typeof session.csrf_token !== "string" ||
      !["privateSession", "cloudflareAccess"].includes(session.auth_mode)
    )
      throw new ApiError(502, "invalid_session");
    this.csrf = session.csrf_token;
    return session;
  }
  login(credential: string) {
    return this.request<void>("session/login", { credential });
  }
  logout() {
    return this.request<void>("session/logout", {});
  }
  post<O extends keyof ReadRequests>(
    operation: O,
    input: ReadRequests[O],
    signal?: AbortSignal,
  ): Promise<ReadResponses[O]> {
    if (!(POSTS as readonly string[]).includes(operation))
      throw new ApiError(404, "unknown_operation");
    return this.request(operation, input, signal);
  }
  get<O extends Get>(
    operation: O,
    id?: string,
    query: Record<string, string | number> = {},
    signal?: AbortSignal,
  ): Promise<ReadResponses[O]> {
    if (!(GETS as readonly string[]).includes(operation))
      throw new ApiError(404, "unknown_operation");
    if (operation.includes("{id}") && !/^[0-9a-fA-F-]{36}$/.test(id ?? ""))
      throw new ApiError(422, "invalid_identifier");
    const path = operation.replace("{id}", id ?? "");
    const params = new URLSearchParams(
      Object.entries(query).map(([key, val]) => [key, String(val)]),
    );
    return this.request(
      path + (params.size ? "?" + params : ""),
      undefined,
      signal,
    );
  }
}
