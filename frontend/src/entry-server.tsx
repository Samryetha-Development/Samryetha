import { renderToString } from "react-dom/server";
import { RootApp } from "./root-app";
import { TasksStandalone } from "./tasks-standalone";

export function render(url: string) {
  const requestUrl = new URL(url, "http://localhost");
  return renderToString(<RootApp pathname={requestUrl.pathname} />);
}

export function renderTasks() {
  return renderToString(<TasksStandalone />);
}
