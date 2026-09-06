import { StrictMode } from "react";
import { hydrateRoot } from "react-dom/client";
import { RootApp } from "./root-app";
import { installSafariViewportFix } from "./lib/safari-viewport-fix";

installSafariViewportFix();

hydrateRoot(
  document.getElementById("root")!,
  <StrictMode>
    <RootApp pathname={window.location.pathname} />
  </StrictMode>,
);
