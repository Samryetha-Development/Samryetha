// Exercise the real page components with successful API responses under React's
// development StrictMode effect replay. Only external dependencies are stubbed.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
const { JSDOM } = require("jsdom");
const dom = new JSDOM('<div id="root"></div>', { url: "http://localhost/files" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator", { configurable: true, value: dom.window.navigator });
global.IS_REACT_ACT_ENVIRONMENT = true;
const React = require("react");
const { createRoot } = require("react-dom/client");
const t = (key) => key;
let currentUser = null;
const row = {
  id: 1, title: "StrictMode resource", preview: "", category: { id: 1, slug: "guide", name: "Guide", kind: "guide" },
  uploader: { id: 1, username: "alice", handle: "alice", displayName: "Alice" }, tags: [],
  originalFilename: "notes.zip", extension: ".zip", mimeType: "application/zip", sizeBytes: 8,
  visibility: "public", status: "published", version: 1, isFeatured: false, downloadCount: 0,
  favoriteCount: 0, ratingAvg: null, ratingCount: 0, isFavorited: false, myRating: null,
  createdAt: 0, updatedAt: 0, descriptionMarkdown: "Hello", can: { update: false, delete: false },
};
const inputs = {
  ...React,
  AppShell: ({ children }) => React.createElement("div", null, children),
  Loading: () => React.createElement("div", { "data-loading": true }, "LOADING"),
  useAuth: () => ({ user: currentUser, loading: false }),
  useI18n: () => ({ t, locale: "en" }),
  timeAgo: () => "",
  formatBytes: () => "8 bytes",
  FileUploadDialog: () => null,
  MarkdownText: () => null,
  ApiError: class extends Error {},
  api: { files: {
    config: async () => ({ categories: [], tagCloud: [], stats: { resourceCount: 1, categoryCount: 1 }, sorts: [] }),
    list: async () => ({ items: [row], total: 1 }),
    get: async () => row,
  } },
};

function load(file, negativeControl) {
  let source = fs.readFileSync(path.join(__dirname, "../src", file), "utf8").replace(/^import .*;\r?\n/gm, "");
  if (negativeControl) source = source.replace("mountedRef.current = true;", "");
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React } }).outputText;
  const exported = {};
  new Function("exports", "React", ...Object.keys(inputs), output)(exported, React, ...Object.values(inputs));
  return exported;
}

async function render(file, name, negativeControl) {
  const component = load(file, negativeControl)[name];
  const root = createRoot(document.getElementById("root"));
  await React.act(async () => {
    root.render(React.createElement(React.StrictMode, null, React.createElement(component, { id: 1 })));
  });
  await React.act(async () => { await new Promise((resolve) => setTimeout(resolve, 25)); });
  const loading = Boolean(document.querySelector("[data-loading]"));
  if (negativeControl) {
    assert.equal(loading, true, `${name}: regression control must reproduce perpetual loading`);
  } else {
    assert.equal(loading, false, `${name}: successful API response must leave loading`);
    assert.match(document.getElementById("root").textContent, /StrictMode resource/);
  }
  await React.act(async () => { root.unmount(); });
}

async function staleResponse(file, name) {
  const component = load(file, false)[name];
  const savedList = inputs.api.files.list;
  const savedGet = inputs.api.files.get;
  const pending = [];
  let oldIdentity = true;
  const loadData = () => oldIdentity
    ? new Promise((resolve) => pending.push(resolve))
    : Promise.resolve({ ...row, title: "Fresh identity resource" });
  inputs.api.files.get = loadData;
  inputs.api.files.list = async (filters) => {
    if (filters.kind) return { items: [], total: 0 };
    return { items: [await loadData()], total: 1 };
  };
  const root = createRoot(document.getElementById("root"));
  try {
    await React.act(async () => root.render(React.createElement(React.StrictMode, null, React.createElement(component, { id: 1 }))));
    assert.ok(pending.length > 0, "Old identity request must still be pending");
    oldIdentity = false;
    currentUser = { id: 99 };
    await React.act(async () => root.render(React.createElement(React.StrictMode, null, React.createElement(component, { id: 2 }))));
    await React.act(async () => { pending.forEach((resolve) => resolve({ ...row, title: "Stale private resource" })); });
    const text = document.getElementById("root").textContent;
    assert.match(text, /Fresh identity resource/);
    assert.doesNotMatch(text, /Stale private resource/);
  } finally {
    await React.act(async () => root.unmount());
    inputs.api.files.list = savedList;
    inputs.api.files.get = savedGet;
    currentUser = null;
  }
}

(async () => {
  for (const [file, name] of [["files-page.tsx", "FilesPage"], ["file-detail-page.tsx", "FileDetailPage"]]) {
    await render(file, name, false);
    await render(file, name, true);
    await staleResponse(file, name);
    console.log(`ok   ${name}: StrictMode loading recovery and regression control`);
    console.log(`ok   ${name}: stale responses cannot overwrite a new identity/resource`);
  }
  dom.window.close();
})().catch((error) => { console.error(error); process.exitCode = 1; });
