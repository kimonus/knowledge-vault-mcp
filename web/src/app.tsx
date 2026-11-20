import { useEffect, useRef, useState } from "react";
import {
  Link,
  NavLink,
  Navigate,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import { useAuth } from "./auth";
import { useMemory } from "./workspace";
import { AssertionDetail } from "./detail";
import { Explorer } from "./explorer";
import { Portal, Theme } from "./portal";
import { StatePage } from "./temporal";
import { Empty } from "./shared";

function Icon({ kind }: { kind: "vault" | "list" | "grid" | "clock" }) {
  const paths = {
    vault: "M5 4h14v16H5z M9 4v16 M12 9h4 M12 13h4",
    list: "M8 6h12 M8 12h12 M8 18h12 M3 6h1 M3 12h1 M3 18h1",
    grid: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
    clock: "M12 7v5l3 2 M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0",
  };
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      aria-hidden="true"
    >
      <path d={paths[kind]} />
    </svg>
  );
}
function FacetTheme() {
  const [facet] = useMemory<string | undefined>("globalFacet", undefined);
  return <Theme dimension="facets" facet={facet} />;
}
export function App() {
  const { session, logout } = useAuth();
  const location = useLocation();
  const [mobile, setMobile] = useState(false);
  const main = useRef<HTMLElement>(null);
  useEffect(() => {
    setMobile(false);
    main.current?.focus();
    document.title =
      "Knowledge Vault · " +
      (location.pathname.startsWith("/portal")
        ? "Portal"
        : location.pathname.startsWith("/state")
          ? "Temporal evidence"
          : "Explorer");
  }, [location.pathname]);
  const section = location.pathname.startsWith("/portal")
    ? "Portal"
    : location.pathname.startsWith("/state")
      ? "Temporal evidence"
      : "Explorer";
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside
        className={"sidebar" + (mobile ? " mobile-open" : "")}
        id="workspace-nav"
      >
        <Link className="brand" to="/explorer">
          <span className="brand-logo">
            <Icon kind="vault" />
          </span>
          <span>
            Knowledge<span className="brand-sub">VAULT</span>
          </span>
        </Link>
        <p className="nav-label">WORKSPACE</p>
        <nav aria-label="Main navigation">
          <NavLink to="/explorer">
            <Icon kind="list" />
            Explorer
          </NavLink>
          <NavLink to="/portal">
            <Icon kind="grid" />
            Portal
          </NavLink>
          <NavLink to="/state">
            <Icon kind="clock" />
            Temporal evidence
          </NavLink>
        </nav>
        <div className="sidebar-note">
          <span className="note-label">BUILT ON EVIDENCE</span>
          <p>
            Explore retained records, their sources, and what changed. Nothing
            here writes to your vault.
          </p>
        </div>
        <div className="sidebar-footer">
          <span className="avatar" aria-hidden="true">
            {session.principal_id.slice(0, 1).toUpperCase()}
          </span>
          <div>
            <strong>{session.principal_id}</strong>
            <span>
              {session.auth_mode === "privateSession"
                ? "Private session"
                : "Cloudflare Access"}
            </span>
          </div>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <button
            className="mobile-menu button secondary"
            aria-expanded={mobile}
            aria-controls="workspace-nav"
            onClick={() => setMobile((value) => !value)}
          >
            {mobile ? "Close navigation" : "Navigation"}
          </button>
          <nav aria-label="Breadcrumb">
            <Link to="/explorer">Workspace</Link>
            <span aria-hidden="true">/</span>
            <Link
              to={
                section === "Portal"
                  ? "/portal"
                  : section === "Temporal evidence"
                    ? "/state"
                    : "/explorer"
              }
            >
              {section}
            </Link>
            {location.pathname.startsWith("/assertions/") && (
              <>
                <span aria-hidden="true">/</span>
                <span>Assertion</span>
              </>
            )}
          </nav>
          <div className="topbar-actions">
            <span className="topbar-readonly">Read-only workspace</span>
            <button
              className="button text"
              onClick={() => {
                void logout();
              }}
            >
              Sign out
            </button>
          </div>
        </header>
        <main id="main" ref={main} tabIndex={-1}>
          <Routes>
            <Route path="/" element={<Navigate to="/explorer" replace />} />
            <Route
              path="/login"
              element={<Navigate to="/explorer" replace />}
            />
            <Route path="/explorer" element={<Explorer />} />
            <Route path="/assertions/:id" element={<AssertionDetail />} />
            <Route path="/portal" element={<Portal />} />
            <Route
              path="/portal/contexts/:id"
              element={<Theme dimension="contexts" />}
            />
            <Route
              path="/portal/topics/:key"
              element={<Theme dimension="topics" />}
            />
            <Route
              path="/portal/unclassified/:dimension"
              element={<FacetTheme />}
            />
            <Route path="/state" element={<StatePage />} />
            <Route
              path="*"
              element={
                <Empty title="Page unavailable">
                  <Link to="/explorer">Return to Explorer</Link>
                </Empty>
              }
            />
          </Routes>
        </main>
        <footer className="workspace-footer">
          <span>Knowledge Vault</span>
          <span>Retained evidence, never unquestioned truth.</span>
          <span>Live view · filters stay in memory</span>
        </footer>
      </div>
    </div>
  );
}
