import { StrictMode } from "react";
import { hydrateRoot } from "react-dom/client";
import { TasksStandalone } from "./tasks-standalone";
import { installSafariViewportFix } from "./lib/safari-viewport-fix";

installSafariViewportFix();

hydrateRoot(
  document.getElementById("root")!,
  <StrictMode>
    <TasksStandalone />
  </StrictMode>,
);
