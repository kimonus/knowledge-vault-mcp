import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./app";
import { AuthBoundary } from "./auth";
import { Workspace } from "./workspace";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter basename="/dashboard">
      <AuthBoundary>
        <Workspace>
          <App />
        </Workspace>
      </AuthBoundary>
    </BrowserRouter>
  </StrictMode>,
);
