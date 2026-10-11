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
// 会话桩：2026-10-09 起文件服务列表页对未登录访客整页设登录门槛，而本脚本要覆盖的是
// "已登录状态下 StrictMode 双渲染、加载恢复、过期响应"，所以这里必须是已登录身份；
// 否则整页会被门槛替换（textContent 只剩 file.signInToView / file.signIn），
// 断言再也测不到原意图——这正是 PR #96 首次 CI 失败的直接原因。
// Session stub: since 2026-10-09 the file-service list page shows a page-level sign-in gate to
// anonymous visitors, while this script's intent is StrictMode double rendering, loading recovery
// and stale responses for a signed-in user, so this has to be a signed-in identity. Otherwise the
// gate replaces the whole page (textContent left as just file.signInToView / file.signIn) and the
// assertions no longer exercise the original intent, which is exactly why PR #96's CI failed first.
const SIGNED_IN_USER = { id: 7, username: "smoke", handle: "smoke", displayName: "Smoke", role: "student", status: "active" };
let currentUser = SIGNED_IN_USER;
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
    favorites: async () => ({ items: [], total: 0 }),
    mine: async () => ({ items: [], total: 0 }),
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
    currentUser = SIGNED_IN_USER;
  }
}

// 新增覆盖：匿名的整页登录门槛。原脚本只覆盖已登录渲染，而匿名这条正是 2026-10-09 新增的行为，
// 必须在这里显式钉住，否则本机与 CI 的验收口径又会分叉（本机全绿、CI 红）。
// New coverage: the page-level gate for anonymous visitors. The original script only covered the
// signed-in render, while the anonymous branch is exactly what was added on 2026-10-09, so it is
// pinned explicitly here; otherwise the local and CI acceptance criteria diverge again (green
// locally, red in CI).
async function anonymousGate() {
  const component = load("files-page.tsx", false).FilesPage;
  const previousUser = currentUser;
  currentUser = null;
  const root = createRoot(document.getElementById("root"));
  try {
    await React.act(async () => {
      root.render(React.createElement(React.StrictMode, null, React.createElement(component, { id: 1 })));
    });
    await React.act(async () => { await new Promise((resolve) => setTimeout(resolve, 25)); });
    const text = document.getElementById("root").textContent;
    assert.match(text, /file\.signInToView/, "anonymous visitor must get the page-level sign-in gate");
    assert.ok(document.querySelector('a[href="/login"]'), "the gate must offer a /login link");
    assert.doesNotMatch(text, /file\.tabFavorites/, "anonymous visitor must not see the saved tab");
    assert.doesNotMatch(text, /file\.tabMine/, "anonymous visitor must not see the uploads tab");
    assert.doesNotMatch(text, /StrictMode resource/, "anonymous visitor must not get the resource list");
    console.log("ok   FilesPage: anonymous visitors get the page-level gate and no personal tabs");
  } finally {
    await React.act(async () => root.unmount());
    currentUser = previousUser;
  }
}

// 新增覆盖：已登录用户的个人标签页为空时必须是空态文案，绝不能是登录提示。
// 「我的上传」对普通用户恒为空（上传能力在后端属 file.create，仅管理员），所以这条正是
// 2026-10-09 要修的 P0：空态曾对非 "all" 标签页一律回退 file.signInToSave，于是"没数据"
// 被说成"没登录"。这里把它钉在 CI 口径里，避免本机绿而 CI 无从发现。
// New coverage: an empty personal tab for a signed-in user must read as an empty state, never as a
// sign-in hint. "My uploads" is always empty for a plain user (uploading is the backend's
// admin-only file.create), so this is exactly the 2026-10-09 P0: the empty state used to fall back
// to file.signInToSave for every non-"all" tab, reporting "no data" as "not signed in". Pinning it
// here keeps the CI criteria from silently missing it.
async function signedInEmptyPersonalTabs() {
  const component = load("files-page.tsx", false).FilesPage;
  const root = createRoot(document.getElementById("root"));
  const clickTab = async (labelKey) => {
    const tab = [...document.querySelectorAll(".files-tabs button")].find((node) => node.textContent === labelKey);
    assert.ok(tab, `${labelKey} tab must be rendered for a signed-in user`);
    await React.act(async () => { tab.click(); });
    await React.act(async () => { await new Promise((resolve) => setTimeout(resolve, 25)); });
    return document.getElementById("root").textContent;
  };
  try {
    currentUser = SIGNED_IN_USER;
    await React.act(async () => {
      root.render(React.createElement(React.StrictMode, null, React.createElement(component, { id: 1 })));
    });
    await React.act(async () => { await new Promise((resolve) => setTimeout(resolve, 25)); });

    const mine = await clickTab("file.tabMine");
    assert.match(mine, /file\.emptyMine/, "an empty uploads list must read as nothing-yet");
    assert.doesNotMatch(mine, /file\.signInToSave/, "an empty uploads list must not claim signed-out");
    assert.doesNotMatch(mine, /file\.signInToView/, "an empty uploads list must not show the page gate");

    const favorites = await clickTab("file.tabFavorites");
    assert.match(favorites, /file\.emptyFavorites/, "an empty saved list must read as nothing-yet");
    assert.doesNotMatch(favorites, /file\.signInToSave/, "an empty saved list must not claim signed-out");

    console.log("ok   FilesPage: empty personal tabs read as empty, never as signed-out");
  } finally {
    await React.act(async () => root.unmount());
    currentUser = SIGNED_IN_USER;
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
  await anonymousGate();
  await signedInEmptyPersonalTabs();
  dom.window.close();
})().catch((error) => { console.error(error); process.exitCode = 1; });
