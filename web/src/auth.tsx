import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { FormEvent, ReactNode } from "react";
import { Client, isAbort } from "./api/client";
import type { Session } from "./api/client";
import { ErrorNotice, Loading } from "./shared";

type Auth = { client: Client; session: Session; logout: () => Promise<void> };
const Context = createContext<Auth | null>(null);
export function useAuth() {
  const value = useContext(Context);
  if (!value) throw new Error("Authentication boundary missing");
  return value;
}

export function AuthBoundary({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<unknown>();
  const [credential, setCredential] = useState("");
  const [signingOut, setSigningOut] = useState(false);
  const generation = useRef(0);
  const invalidate = useCallback(() => {
    generation.current++;
  }, []);
  const privateMode =
    document.getElementById("root")?.dataset.authMode === "privateSession";
  const client = useMemo(
    () =>
      new Client(() => {
        invalidate();
        setSession(null);
        setLoading(false);
        setError(undefined);
        setReason(
          "Your session expired or could not be verified. Private data has been cleared.",
        );
      }),
    [invalidate],
  );
  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      const epoch = generation.current;
      setLoading(true);
      setError(undefined);
      try {
        const current = await client.session(signal);
        if (generation.current === epoch) {
          setSession(current);
          setReason("");
        }
      } catch (failure) {
        if (!isAbort(failure) && generation.current === epoch)
          setError(failure);
      } finally {
        if (!signal?.aborted && generation.current === epoch) setLoading(false);
      }
    },
    [client],
  );
  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => {
      controller.abort();
      invalidate();
      client.clear();
    };
  }, [refresh, client, invalidate]);
  useEffect(() => {
    if (!session) return;
    const expire = () => {
      invalidate();
      client.clear();
      setSession(null);
      setLoading(false);
      setReason("Your session expired. Sign in again to continue.");
    };
    const timer = window.setTimeout(
      expire,
      Math.max(0, session.expires_at * 1000 - Date.now()),
    );
    const focused = () => {
      void refresh();
    };
    window.addEventListener("focus", focused);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("focus", focused);
    };
  }, [session, client, refresh, invalidate]);
  async function login(event: FormEvent) {
    event.preventDefault();
    invalidate();
    const transient = credential;
    setCredential("");
    setLoading(true);
    setError(undefined);
    try {
      await client.login(transient);
      await refresh();
    } catch (failure) {
      setError(failure);
      setLoading(false);
    }
  }
  async function logout() {
    invalidate();
    setSession(null);
    setCredential("");
    setError(undefined);
    setSigningOut(true);
    setLoading(true);
    try {
      await client.logout();
      setReason(
        session?.auth_mode === "cloudflareAccess"
          ? "Local data cleared. Your Access provider session may still be active."
          : "Signed out. A copied cookie remains valid until expiry or operator revocation.",
      );
    } catch {
      setReason(
        "Local data cleared, but server logout could not be confirmed. Reauthenticate before continuing.",
      );
    } finally {
      client.clear();
      setSession(null);
      setLoading(false);
      setSigningOut(false);
      setError(undefined);
    }
  }
  if (loading && !session)
    return (
      <main className="login-screen">
        {signingOut ? (
          <p role="status">Signing out. Private data has been cleared.</p>
        ) : (
          <Loading />
        )}
      </main>
    );
  if (!session)
    return (
      <main className="login-screen">
        <section className="login-panel" aria-labelledby="login-title">
          <div className="brand-mark" aria-hidden="true">
            K
          </div>
          <p className="eyebrow">YOUR PRIVATE WORKSPACE</p>
          <h1 id="login-title">Knowledge Vault</h1>
          <p className="lead">
            A clearer view of what you know, and the evidence behind it.
          </p>
          {reason && (
            <p className="notice" role="status">
              {reason}
            </p>
          )}
          {!!error && <ErrorNotice error={error} />}
          {privateMode ? (
            <form
              onSubmit={(event) => {
                void login(event);
              }}
            >
              <label htmlFor="credential">Dashboard read-only credential</label>
              <input
                id="credential"
                type="password"
                autoComplete="off"
                maxLength={512}
                required
                value={credential}
                onChange={(event) => setCredential(event.target.value)}
              />
              <p className="muted small">
                Use your dashboard credential. Core service and write
                credentials cannot sign in here.
              </p>
              <button className="button primary full" disabled={loading}>
                Open your vault
              </button>
            </form>
          ) : (
            <button
              className="button primary full"
              onClick={() => window.location.assign("/dashboard/")}
            >
              Continue with Cloudflare Access
            </button>
          )}
          <p className="muted small">
            Read-only · No credentials or knowledge are saved in browser
            storage.
          </p>
        </section>
      </main>
    );
  return (
    <Context.Provider value={{ client, session, logout }}>
      {children}
    </Context.Provider>
  );
}
