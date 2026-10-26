// 假设 1 的 DOM 取证：管理员与普通用户渲染 files-page 后，文件夹条到底长什么样。
// DOM evidence for hypothesis 1: what the folder bar actually looks like once files-page renders for
// an administrator versus an ordinary user.
//
// 关键前提：本脚本**不自己编数据**。`useAuth` 用真实 `GET /api/auth/me` 的响应体，
// `api.files.config()` 用真实 `GET /api/files/config` 的响应体（由 .dsh-local/probe-h1-canupload.py
// 起真实 uvicorn 抓下来落盘）。这样 DOM 结论针对的是真实服务端载荷，而不是测试里虚构的形状。
// The premise matters: this script invents no data. `useAuth` is fed the real `GET /api/auth/me`
// body and `api.files.config()` the real `GET /api/files/config` body, both captured from a real
// uvicorn run by .dsh-local/probe-h1-canupload.py. The DOM conclusions therefore describe real server
// payloads rather than a shape made up for the test.
//
// 同时保留负对照：把 FileFolderBar 的 canManage 判断改成恒真，普通用户就必须出现管理按钮 ——
// 否则说明上面的断言根本不会判红。
// A negative control is kept as well: forcing FileFolderBar's canManage branch to always true must
// make the management buttons appear for an ordinary user. Without that, the assertions above would
// not be able to go red at all.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
const { JSDOM } = require("jsdom");

const dom = new JSDOM('<div id="root"></div>', { url: "http://localhost/files" });
global.window = dom.window;
global.document = dom.window.document;
// 摘掉 JSDOM 自带的空 #root，否则新挂的 #root 变成第二个同 id 元素，文本断言会读到空壳。
// The empty #root JSDOM ships with is detached, or a freshly mounted #root becomes a second element
// with the same id and text assertions read the empty shell.
document.getElementById("root").remove();
Object.defineProperty(global, "navigator", { configurable: true, value: dom.window.navigator });
global.IS_REACT_ACT_ENVIRONMENT = true;
class ResizeObserverStub { observe() {} unobserve() {} disconnect() {} }
global.ResizeObserver = ResizeObserverStub;
dom.window.ResizeObserver = ResizeObserverStub;
const React = require("react");
const { createRoot } = require("react-dom/client");

const EVIDENCE = path.resolve(__dirname, "../../.pytmp/probe-h1");
// 证据文件由 .dsh-local/probe-h1-canupload.py 起真实 uvicorn 抓取；缺失时退回一份结构完全一致的
// 内置夹具，好让本脚本单独也能跑（此时会明确打印用的是夹具，不会假装是真实载荷）。
// The evidence files come from a real uvicorn run driven by .dsh-local/probe-h1-canupload.py. When
// they are absent the script falls back to a built-in fixture of the same shape so it can still run
// standalone, and it says so plainly instead of pretending the payload was real.
const FIXTURE_CONFIG = {
  categories: [{ id: 5, slug: "other", name: "Other" }],
  tagCloud: [],
  stats: { resourceCount: 0, categoryCount: 1 },
  sorts: ["latest"],
  statuses: ["published"],
  visibilities: ["members"],
  kinds: ["doc"],
  allowedExtensions: [".txt"],
  convertibleExtensions: [],
  documentPreview: false,
  maxTags: 8,
  maxUploadBytes: 10485760,
  folders: [{ id: 1, parentId: null, name: "Physics", description: "", sortOrder: 0, resourceCount: 0, childCount: 0 }],
};
const FIXTURE_ADMIN = { user: { id: 1, username: "admin", handle: "admin", displayName: "Admin", role: "admin" } };
const FIXTURE_PLAIN = { user: { id: 2, username: "plain", handle: "plain", displayName: "Plain", role: "student" } };
let payloadSource = "real server payloads";
function readJson(name, fallback) {
  try {
    return JSON.parse(fs.readFileSync(path.join(EVIDENCE, name), "utf8"));
  } catch {
    payloadSource = "built-in fixture (real payload dumps not found)";
    return fallback;
  }
}
const configAdmin = readJson("config-admin.json", FIXTURE_CONFIG);
const meAdmin = readJson("me-admin.json", FIXTURE_ADMIN);
const mePlain = readJson("me-plain.json", FIXTURE_PLAIN);

const t = (key, vars) => (vars
  ? `${key}(${Object.entries(vars).map(([name, value]) => `${name}=${value}`).join(",")})`
  : key);

// 纯逻辑层用真实源码，别用桩替代：文件夹树的层级判定是"该不该显示这一层文件夹"的依据。
// The pure logic layer comes from real source rather than stubs: the folder tree decides which
// level's folders belong on screen. 注意：必须在 inputs 定义之后再编译（否则闭包引用尚未初始化）。
// Note: compilation happens after `inputs` exists, or the closure reads an uninitialised binding.
let folderTree = null;
let dropUpload = null;

let currentUser = null;
const apiCalls = { config: 0, list: 0 };

const inputs = {
  ...require("./ui-smoke-inputs.cjs"),
  ...React,
  useI18n: () => ({ t, locale: "en" }),
  useAuth: () => ({ user: currentUser, loading: false }),
  AppShell: ({ children }) => React.createElement("div", { "data-shell": true }, children),
  Loading: () => React.createElement("div", { "data-loading": true }, "LOADING"),
  timeAgo: () => "",
  formatBytes: () => "8 bytes",
  FileUploadDialog: () => null,
  // 批量工具条与本假设无关，给空桩。
  // The batch toolbar is irrelevant to this hypothesis and gets an empty stub.
  FileBatchBar: () => null,
  FileMetadataDialog: () => null,
  ApiError: class ApiError extends Error {},
  uploadFileBytes: async () => {},
  api: {
    files: {
      config: async () => { apiCalls.config += 1; return configAdmin; },
      list: async () => { apiCalls.list += 1; return { items: [], total: 0 }; },
      favorites: async () => ({ items: [], total: 0 }),
      mine: async () => ({ items: [], total: 0 }),
      get: async () => null,
    },
  },
  flattenFolders: (...args) => folderTree.flattenFolders(...args),
  folderPathLabel: (...args) => folderTree.folderPathLabel(...args),
  foldersAtLevel: (...args) => folderTree.foldersAtLevel(...args),
  parentFolderView: (...args) => folderTree.parentFolderView(...args),
  // 文件夹条另外用到这三个纯函数，缺一个就 ReferenceError。
  // The folder bar also uses these three pure functions; a missing one is an immediate ReferenceError.
  folderById: (...args) => folderTree.folderById(...args),
  folderBreadcrumb: (...args) => folderTree.folderBreadcrumb(...args),
  viewFolderId: (...args) => folderTree.viewFolderId(...args),
  planDropEntries: (...args) => dropUpload.planDropEntries(...args),
  runDropUploads: (...args) => dropUpload.runDropUploads(...args),
  summarizeDrop: (...args) => dropUpload.summarizeDrop(...args),
  titleFromFilename: (...args) => dropUpload.titleFromFilename(...args),
};

function compileModule(file) {
  let source = fs.readFileSync(path.join(__dirname, "../src", file), "utf8")
    .replace(/\r\n/g, "\n").replace(/^import .*;\n/gm, "");
  const output = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React },
  }).outputText;
  const exported = {};
  new Function("exports", "React", ...Object.keys(inputs), output)(exported, React, ...Object.values(inputs));
  return exported;
}

// inputs 就位后再编译纯逻辑层。
// The pure logic layer is compiled once `inputs` is in place.
folderTree = compileModule("lib/folder-tree.ts");
dropUpload = compileModule("lib/drop-upload.ts");

// 文件夹条用**真实源码**编译：本假设问的就是它的渲染条件。
// The folder bar is compiled from real source: its render condition is exactly what is under test.
function compileWithPatch(file, patch) {
  let source = fs.readFileSync(path.join(__dirname, "../src", file), "utf8")
    .replace(/\r\n/g, "\n").replace(/^import .*;\n/gm, "");
  if (patch) {
    assert.ok(source.includes(patch.from), `negative control anchor not found in ${file}: ${patch.from}`);
    source = source.replace(patch.from, patch.to);
  }
  const output = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React },
  }).outputText;
  const exported = {};
  new Function("exports", "React", ...Object.keys(inputs), output)(exported, React, ...Object.values(inputs));
  return exported;
}

// 负对照：把 FileFolderBar 的 canManage 分支改成恒真。
// Negative control: force FileFolderBar's canManage branch to always true.
const ALWAYS_MANAGE_PATCH = { from: "      {canManage ? (", to: "      {true ? (" };

async function mount(user, patch) {
  currentUser = user;
  const folderBar = compileWithPatch("file-folder-bar.tsx", patch).FileFolderBar;
  const bag = { ...inputs, FileFolderBar: folderBar };
  let source = fs.readFileSync(path.join(__dirname, "../src", "files-page.tsx"), "utf8")
    .replace(/\r\n/g, "\n").replace(/^import .*;\n/gm, "");
  const output = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React },
  }).outputText;
  const exported = {};
  new Function("exports", "React", ...Object.keys(bag), output)(exported, React, ...Object.values(bag));

  const rootElement = document.createElement("div");
  rootElement.id = "root";
  document.body.appendChild(rootElement);
  const container = createRoot(rootElement);
  await React.act(async () => {
    container.render(React.createElement(React.StrictMode, null, React.createElement(exported.FilesPage, {})));
  });
  await React.act(async () => { await new Promise((resolve) => setTimeout(resolve, 40)); });
  return { container, rootElement };
}

function bar(rootElement) {
  return rootElement.querySelector(".files-folders");
}

function report(label, rootElement) {
  const section = bar(rootElement);
  const actions = rootElement.querySelector(".files-folder-actions");
  const folderRows = rootElement.querySelectorAll(".files-row-folder");
  // 位置选择框的 aria-label 随本次重构从"文件夹"改成"当前位置"。
  // The location picker's aria-label moved from "folders" to "location" in this rework.
  const folderSelect = rootElement.querySelector('select[aria-label="file.folderPosition"]');
  const upRow = rootElement.querySelector(".files-folder-up");
  const text = section ? section.textContent.replace(/\s+/g, " ").trim() : null;
  console.log(`--- ${label}`);
  console.log("  section .files-folders      :", Boolean(section));
  console.log("  .files-folder-actions       :", Boolean(actions));
  console.log("  visible bar text            :", JSON.stringify(text));
  console.log("  folder rows .files-row-folder:", folderRows.length);
  console.log("  folder filter <select>      :", Boolean(folderSelect));
  console.log("  up row .files-folder-up     :", Boolean(upRow));
  return { section, actions, folderRows, folderSelect, text };
}

async function main() {
  console.log("payload source:", payloadSource);
  console.log("admin role from /api/auth/me:", meAdmin.user && meAdmin.user.role);
  console.log("plain role from real /api/auth/me:", mePlain.user && mePlain.user.role);
  console.log("folders in real /api/files/config:", Array.isArray(configAdmin.folders) ? configAdmin.folders.length : "NOT AN ARRAY");

  // 用例 1：管理员（真实 me 载荷 + 真实 config 载荷）。
  // Case 1: the administrator, with the real me payload and the real config payload.
  const adminRun = await mount(meAdmin.user, null);
  const adminView = report("admin", adminRun.rootElement);
  assert.ok(adminView.section, "the folder bar root must be mounted for an administrator");
  assert.ok(adminView.actions, "the folder actions row must exist for an administrator");
  assert.match(adminView.text, /file\.folderNew/, "an administrator must see the create-folder control");
  assert.ok(adminView.folderSelect, "the folder filter must exist on the All tab");
  assert.equal(adminView.folderRows.length, configAdmin.folders.length,
    "every folder in config must appear as a folder row at the top level");
  await React.act(async () => { adminRun.container.unmount(); adminRun.rootElement.remove(); });

  // 用例 2：普通用户（真实 me 载荷，config 完全相同）。
  // Case 2: an ordinary user, same real me payload shape and the very same config.
  const plainRun = await mount(mePlain.user, null);
  const plainView = report("plain", plainRun.rootElement);
  assert.ok(plainView.section, "the folder bar root still mounts for an ordinary user");
  assert.equal(plainView.actions, null,
    "an ordinary user must not receive the folder management row");
  assert.doesNotMatch(plainView.text ?? "", /file\.folderNew/,
    "an ordinary user must not see the create-folder control");
  assert.ok(plainView.folderSelect, "an ordinary user still gets the folder filter");
  assert.equal(plainView.folderRows.length, configAdmin.folders.length,
    "an ordinary user still sees the folder rows the config carries");
  // 这一条是为了回答"存在但空白"：普通用户看到的文件夹条正文到底是什么。
  // This answers "present but blank": what text an ordinary user's folder bar actually contains.
  console.log("  => plain-user bar text is empty ?", (plainView.text ?? "") === "");
  await React.act(async () => { plainRun.container.unmount(); plainRun.rootElement.remove(); });

  // 用例 3：假设 3 —— 切到"我的上传"个人标签后，文件夹条是否还在。
  // Case 3: hypothesis 3 - after switching to the personal "my uploads" tab, is the folder bar still
  // there.
  const adminRun2 = await mount(meAdmin.user, null);
  const tabButtons = Array.from(adminRun2.rootElement.querySelectorAll('[role="tab"]'));
  console.log("--- tabs rendered:", tabButtons.map((node) => node.textContent).join(" | "));
  const mineTab = tabButtons.find((node) => node.textContent === "file.tabMine");
  assert.ok(mineTab, "the personal tab must be rendered for a signed-in administrator");
  await React.act(async () => { mineTab.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true })); });
  await React.act(async () => { await new Promise((resolve) => setTimeout(resolve, 40)); });
  const mineView = report("admin + tab=file.tabMine", adminRun2.rootElement);
  console.log("  => folder bar present under the personal tab ?", Boolean(mineView.section));
  // 修复后的契约：个人标签下条仍然在，而且必须有内容（否则 .files-folders:empty 会整块隐藏）。
  // The contract after the fix: the bar stays under a personal tab and must carry content, or
  // .files-folders:empty hides the whole block.
  assert.ok(mineView.section, "the folder bar must stay mounted under a personal tab");
  assert.ok((mineView.text ?? "").length > 0,
    "the folder bar must carry content under a personal tab, or :empty hides it entirely");
  assert.equal(mineView.actions, null,
    "management actions must not render under a personal tab");
  await React.act(async () => { adminRun2.container.unmount(); adminRun2.rootElement.remove(); });

  // 用例 4：负对照 —— canManage 恒真时，普通用户必须出现管理按钮。
  // Case 4: the negative control - with canManage always true, the ordinary user must see the row.
  const controlRun = await mount(mePlain.user, ALWAYS_MANAGE_PATCH);
  const controlView = report("plain + ALWAYS_MANAGE_PATCH", controlRun.rootElement);
  assert.ok(controlView.actions,
    "negative control must reproduce the management row for an ordinary user");
  assert.match(controlView.text, /file\.folderNew/,
    "negative control must reproduce the create-folder control");
  await React.act(async () => { controlRun.container.unmount(); controlRun.rootElement.remove(); });

  console.log("\nprobe-folder-dom: ok");
  console.log("api.files.config calls:", apiCalls.config, " api.files.list calls:", apiCalls.list);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
