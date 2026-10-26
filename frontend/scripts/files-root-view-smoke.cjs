// 根视图语义 + 按钮可发现性：红绿锁 / Root-view semantics and button discoverability: red/green lock
//
// 本脚本锁的是管理员报障的那两件事（开发计划 §零 第 3 条）：
// 1. "把文件移入文件夹以后也没有显示（文件还在外面显示）"——默认视图必须只是根目录（未归档），
//    移入文件夹之后那一行必须从根视图消失；
// 2. "「所属文件夹」那里的按钮不明显，看不出来是个按钮"——行内控件必须是看得出能点的按钮，
//    上传弹窗必须能选目标文件夹且默认就是当前所在文件夹。
//
// This script locks the two things the administrator reported (development plan, section zero, item 3):
// 1. "after moving a file into a folder it still shows up outside" - the default view has to be only
//    the root (unfiled), and a filed row has to leave it;
// 2. "the folder control does not look like a button" - the in-row control has to read as a button,
//    and the upload dialog has to offer a destination folder defaulting to the one you are inside.
//
// 每个关键断言都配一条负对照：把修复还原成旧行为，断言必须**变红**。没有负对照的断言等于没断言。
// Every key assertion ships with a negative control: restoring the old behaviour must make it go red.
// An assertion without one proves nothing.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
const { JSDOM } = require("jsdom");

const dom = new JSDOM('<div id="root"></div>', { url: "http://localhost/files" });
global.window = dom.window;
global.document = dom.window.document;
// 摘掉 JSDOM 自带的空 #root：否则新挂的 #root 变成第二个同 id 元素，文本断言会读到空壳。
// The empty #root JSDOM ships with is detached: otherwise a freshly mounted #root becomes a second
// element with the same id and text assertions read the empty shell.
document.getElementById("root").remove();
Object.defineProperty(global, "navigator", { configurable: true, value: dom.window.navigator });
global.IS_REACT_ACT_ENVIRONMENT = true;
class ResizeObserverStub { observe() {} unobserve() {} disconnect() {} }
global.ResizeObserver = ResizeObserverStub;
dom.window.ResizeObserver = ResizeObserverStub;
const React = require("react");
const { createRoot } = require("react-dom/client");

// 插值桩把变量拼进文案，好让断言验证"那个值确实到了界面上"。
// The stub splices interpolated variables into the copy so an assertion can check that the value
// actually reached the interface.
const t = (key, vars) => (vars
  ? `${key}(${Object.entries(vars).map(([name, value]) => `${name}=${value}`).join(",")})`
  : key);

const ADMIN = { id: 7, username: "admin", handle: "admin", displayName: "Admin", role: "admin" };

const FOLDERS = [
  { id: 1, name: "数学", description: "", sortOrder: 0, resourceCount: 0, childCount: 1 },
  { id: 2, parentId: 1, name: "微积分", description: "", sortOrder: 0, resourceCount: 0, childCount: 0 },
  { id: 3, name: "英语", description: "", sortOrder: 1, resourceCount: 0, childCount: 0 },
];

const CONFIG = {
  categories: [{ id: 1, slug: "guide", name: "Guide", description: "", kind: "guide", sortOrder: 0, isSystem: true, resourceCount: 1 }],
  folders: FOLDERS,
  tagCloud: [],
  stats: { resourceCount: 2, categoryCount: 1 },
  allowedExtensions: [".pdf"],
  convertibleExtensions: [],
  documentPreview: false,
  maxUploadBytes: 1048576,
  kinds: ["guide"],
  visibilities: ["public", "members", "private"],
  statuses: ["published"],
  sorts: ["latest"],
  maxTags: 8,
};

function makeItem(id, title, folderId) {
  return {
    id,
    title,
    preview: "",
    folderId,
    category: { id: 1, slug: "guide", name: "Guide", kind: "guide" },
    uploader: { id: 7, username: "admin", handle: "admin", displayName: "Admin" },
    tags: [],
    originalFilename: "notes.pdf",
    mimeType: "application/pdf",
    extension: ".pdf",
    sizeBytes: 11,
    visibility: "members",
    status: "published",
    version: 1,
    isFeatured: false,
    isPublic: false,
    downloadCount: 0,
    favoriteCount: 0,
    ratingAvg: null,
    ratingCount: 0,
    isFavorited: false,
    myRating: null,
    createdAt: 1700000000000,
    updatedAt: 1700000000000,
  };
}

// 假的文件服务：**真的按文件夹过滤**，与后端 repository 的过滤口径一致
// （unfiled => folder_id IS NULL，folderId => folder_id = ?）。这不是为了"让测试好过"，
// 而是因为本用例要断言的是"移动之后那一行会不会消失"，只有在取数尊重归属时这条断言才有意义。
// The fake file service filters by folder for real, matching the backend repository's contract
// (unfiled => folder_id IS NULL, folderId => folder_id = ?). That is not convenience: the case under
// test is "does the row disappear after the move", and the assertion only means something when the
// fetch actually honours the placement.
let serverItems = [];
let calls = { list: [], setFolder: [], created: [], deletedFolders: [] };

function resetServer() {
  serverItems = [makeItem(42, "未归档资料", null), makeItem(43, "已归档资料", 1)];
  calls = { list: [], setFolder: [], created: [], deletedFolders: [] };
}

// 纯逻辑层用真实源码：根视图该显示哪一层文件夹，正是本次改动的判定依据。
// The pure logic layer comes from real source: which level's folders belong on screen is exactly
// what this change is judged by.
let folderTree = null;
// 拖拽上传的纯逻辑层同样用真实源码：页面渲染时会直接调用它们，缺一个就是 ReferenceError。
// The drag-and-drop pure layer also comes from real source: the page calls it while rendering, and a
// missing one is an immediate ReferenceError.
let dropUpload = null;

const inputs = {
  ...require("./ui-smoke-inputs.cjs"),
  ...React,
  useI18n: () => ({ t, locale: "en" }),
  useAuth: () => ({ user: ADMIN, loading: false }),
  AppShell: ({ children }) => React.createElement("div", { "data-shell": true }, children),
  Loading: () => React.createElement("div", { "data-loading": true }, "LOADING"),
  timeAgo: () => "",
  formatBytes: () => "11 bytes",
  FileBatchBar: () => null,
  FileMetadataDialog: () => null,
  // 文件夹条用**真实源码**：本用例要断言"根目录下位置条也在"与"普通用户也有内容"。
  // The folder bar comes from real source: the cases assert that the location strip exists at the root
  // and that it carries content for ordinary users too.
  FileFolderBar: (props) => React.createElement(compile("file-folder-bar.tsx").FileFolderBar, props),
  // 上传弹窗桩：只把"页面交给它的默认目标文件夹"原样写进 DOM，好让用例 7 断言这条接线。
  // A stub for the upload dialog that writes the default destination the page handed it straight into
  // the DOM, which is what case 7 asserts.
  FileUploadDialog: ({ defaultFolderId }) =>
    React.createElement("div", { "data-upload-dialog": true, "data-folder": String(defaultFolderId) }),
  ApiError: class ApiError extends Error {},
  uploadFileBytes: async () => {},
  api: {
    files: {
      config: async () => CONFIG,
      list: async (params = {}) => {
        calls.list.push(params);
        let rows = serverItems;
        if (params.unfiled) rows = rows.filter((row) => (row.folderId ?? null) === null);
        else if (typeof params.folderId === "number") rows = rows.filter((row) => row.folderId === params.folderId);
        return { items: rows, total: rows.length, page: 1, pageSize: 20, sort: "latest" };
      },
      favorites: async () => ({ items: [], total: 0 }),
      mine: async () => ({ items: [], total: 0 }),
      get: async (id) => serverItems.find((row) => row.id === id) ?? null,
      download: async () => ({ downloadUrl: "about:blank", originalFilename: "notes.pdf" }),
      setFavorite: async (id, on) => ({ resourceId: id, isFavorited: on, favoriteCount: 0 }),
      setFolder: async (id, folderId) => {
        calls.setFolder.push([id, folderId]);
        serverItems = serverItems.map((row) => (row.id === id ? { ...row, folderId } : row));
        return { ...serverItems.find((row) => row.id === id) };
      },
      presign: async () => ({
        uploadUrl: "http://localhost/upload?expires=x&sig=y",
        objectKey: "uploads/notes.pdf",
        expires: "x",
        sig: "y",
      }),
      deleteFolder: async (id) => {
        calls.deletedFolders.push(id);
        return { deletedId: id, detachedResources: 0, childCount: 0 };
      },
      create: async (body) => {
        calls.created.push(body);
        return makeItem(99, body.title, body.folderId ?? null);
      },
    },
  },
  flattenFolders: (...args) => folderTree.flattenFolders(...args),
  folderPathLabel: (...args) => folderTree.folderPathLabel(...args),
  foldersAtLevel: (...args) => folderTree.foldersAtLevel(...args),
  parentFolderView: (...args) => folderTree.parentFolderView(...args),
  folderById: (...args) => folderTree.folderById(...args),
  folderBreadcrumb: (...args) => folderTree.folderBreadcrumb(...args),
  viewFolderId: (...args) => folderTree.viewFolderId(...args),
  planDropEntries: (...args) => dropUpload.planDropEntries(...args),
  runDropUploads: (...args) => dropUpload.runDropUploads(...args),
  summarizeDrop: (...args) => dropUpload.summarizeDrop(...args),
  titleFromFilename: (...args) => dropUpload.titleFromFilename(...args),
  dropStatusLabel: (...args) => dropUpload.dropStatusLabel(...args),
};

function transpile(file, patch) {
  // 先统一换行符再匹配补丁：仓库里的 .tsx 是 CRLF，补丁锚点按 LF 写。
  // Line endings are normalised before patching: the .tsx files are CRLF and the anchors are LF.
  let source = fs.readFileSync(path.join(__dirname, "../src", file), "utf8")
    .replace(/\r\n/g, "\n")
    .replace(/^import .*;\n/gm, "");
  const patches = !patch ? [] : Array.isArray(patch) ? patch : [patch];
  for (const entry of patches) {
    assert.ok(source.includes(entry.from), `patch anchor not found in ${file}: ${entry.from}`);
    source = source.replace(entry.from, entry.to);
  }
  return ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React } }).outputText;
}

function compile(file, patch, extra) {
  const exported = {};
  const bag = { ...inputs, ...(extra ?? {}) };
  new Function("exports", "React", ...Object.keys(bag), transpile(file, patch))(exported, React, ...Object.values(bag));
  return exported;
}

folderTree = compile("lib/folder-tree.ts");
dropUpload = compile("lib/drop-upload.ts");

// 负对照一：把默认视图还原成旧行为 "all"——不向服务端发任何文件夹条件。
// 这正是"移入文件夹后仍然在外面显示"的成因，所以下面的根视图断言必须据此变红。
// Negative control one: restore the old default view "all", which sends no folder condition at all.
// That is precisely what caused "still visible outside after moving", so the root-view assertions
// below must go red under it.
const OLD_DEFAULT_VIEW_PATCH = {
  from: `  const [folderView, setFolderView] = useState<FolderView>("root");`,
  to: `  const [folderView, setFolderView] = useState<FolderView>("all");`,
};

// 负对照二：把行内控件的那个"按钮外观"类名拿掉，回到只有 files-move-trigger 的旧样子。
// Negative control two: drop the button-appearance class, back to the old files-move-trigger only.
const PLAIN_TEXT_TRIGGER_PATCH = {
  from: `                            className="files-move-trigger files-folder-trigger"`,
  to: `                            className="files-move-trigger"`,
};

async function mountPage(patch) {
  const rootElement = document.createElement("div");
  rootElement.id = "root";
  document.body.appendChild(rootElement);
  const Component = compile("files-page.tsx", patch).FilesPage;
  const container = createRoot(rootElement);
  await React.act(async () => {
    container.render(React.createElement(React.StrictMode, null, React.createElement(Component, {})));
  });
  await React.act(async () => { await new Promise((resolve) => setTimeout(resolve, 40)); });
  return container;
}

const root = () => document.getElementById("root");
const text = () => root().textContent;
const buttonWith = (label, scope) =>
  [...(scope ?? root()).querySelectorAll("button")].find((element) => element.textContent === label);
const buttonContaining = (label, scope) =>
  [...(scope ?? root()).querySelectorAll("button")].find((element) => element.textContent.includes(label));
const fileRows = () => [...root().querySelectorAll("tbody:not(.files-folder-rows) tr")];
const rowTitles = () => fileRows().map((row) => row.querySelector(".files-row-title")?.textContent ?? "");
const rowWith = (title) => fileRows().find((row) => row.querySelector(".files-row-title")?.textContent === title);

// 主列表请求：只有它带 page 字段（新生专区那条只带 kind/sort/pageSize），据此区分两者。
// The main list request is the only one carrying page (the newcomer strip sends kind/sort/pageSize),
// which is how the two are told apart.
const mainLists = () => calls.list.filter((params) => params.page !== undefined);

async function click(element) {
  assert.ok(element, "expected the target control to be rendered");
  await React.act(async () => {
    element.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true, cancelable: true }));
    await new Promise((resolve) => setTimeout(resolve, 30));
  });
}

async function chooseOption(select, value) {
  await React.act(async () => {
    select.value = value;
    select.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
    await new Promise((resolve) => setTimeout(resolve, 30));
  });
}

async function unmountAll() {
  document.getElementById("root")?.remove();
  resetServer();
}

// ================================================================ 用例 1：默认就是根视图
// 断言：首屏的主列表请求必须带 unfiled=true，且不带 folderId。
// Assertion: the first main list request must carry unfiled=true and no folderId.
async function rootViewIsDefaultCase() {
  resetServer();
  const container = await mountPage();
  try {
    const first = mainLists()[0];
    assert.ok(first, "the page must issue a main list request");
    assert.equal(first.unfiled, true,
      "the default view must ask for unfiled resources, or filed resources show up outside their folder");
    assert.equal(first.folderId, undefined,
      "the default view must not ask for one specific folder");
    // 显示层也要对得上：已归档的那一份不得出现在根视图里。
    // The rendering has to agree: the filed resource must not be listed at the root.
    assert.ok(!rowTitles().includes("已归档资料"),
      "the filed resource must not be listed at the root view");
  } finally {
    await React.act(async () => { container.unmount(); });
    await unmountAll();
  }
}

// ================================================================ 用例 2：负对照（旧默认视图）
// 旧行为下同样的两个断言必须双双变红：没有 unfiled，也没有 folderId——即"什么都不过滤"。
// Under the old behaviour the very same two assertions must both go red: no unfiled and no folderId,
// that is "filter nothing at all".
async function oldDefaultViewCase() {
  resetServer();
  const container = await mountPage(OLD_DEFAULT_VIEW_PATCH);
  try {
    const first = mainLists()[0];
    assert.ok(first, "the page must issue a main list request");
    assert.equal(first.unfiled, undefined,
      "negative control: with the old default the request must carry no unfiled condition");
    assert.equal(first.folderId, undefined);
    // 而且它的取数结果里确实同时含已归档的那一份——这就是被报障的形态。
    // And its result genuinely contains the filed resource too, which is the reported shape.
    assert.ok(rowTitles().includes("已归档资料"),
      "negative control: the old default must leak the filed resource into the root listing");
  } finally {
    await React.act(async () => { container.unmount(); });
    await unmountAll();
  }
}

// ================================================================ 用例 3：移入文件夹后必须离开根视图
// 这是本任务的核心症状锁：移动成功 -> 列表刷新 -> 那一行不再出现在根视图，且给出成功反馈。
// 它在旧默认视图下必须变红（见文件末尾的 assert.rejects），否则这条断言根本不判别东西。
// This is the core symptom lock of the task: a successful move refreshes the list, the row is no
// longer in the root view, and a success notice names the destination. It has to go red under the old
// default view (see assert.rejects at the end of the file), or the assertion discriminates nothing.
async function moveLeavesRootCase(patch) {
  resetServer();
  const container = await mountPage(patch);
  try {
    // 前置条件只断言"未归档那一份在列表里"：这样负对照（旧默认视图）也能走到下面那条靶心断言，
    // 而不是在前置条件上就以另一个理由失败。
    // The precondition only asserts that the unfiled row is listed, so the negative control (the old
    // default view) still reaches the bullseye assertion below instead of failing here for an
    // unrelated reason.
    assert.ok(rowTitles().includes("未归档资料"), "the unfiled resource must be listed at the root");

    const trigger = rowWith("未归档资料").querySelector(".files-folder-trigger");
    await click(trigger);
    const select = root().querySelector('select[aria-label="file.folderMoveTo"]');
    assert.ok(select, "the move dialog must offer a folder picker");
    await chooseOption(select, "1");

    assert.deepEqual(calls.setFolder, [[42, 1]], "the move must hit the server with the chosen folder");
    assert.ok(!rowTitles().includes("未归档资料"),
      "a filed resource must leave the root view: this is the exact defect the administrator reported");
    assert.match(text(), /file\.folderMoved/, "a successful move must be reported with its destination");
    assert.match(text(), /数学/, "the notice must name the destination folder");
  } finally {
    await React.act(async () => { container.unmount(); });
    await unmountAll();
  }
}

// ================================================================ 用例 4：行内控件必须看得出是按钮
// 断言：它是一个真正的 <button>，带"按钮外观"类名、带动词、并显示当前归属。
// Assertion: it is a real <button> carrying the button-appearance class, a verb and its current
// location.
async function moveButtonDiscoverabilityCase(patch) {
  resetServer();
  const container = await mountPage(patch);
  try {
    const trigger = rowWith("未归档资料").querySelector(".files-move-trigger");
    assert.ok(trigger, "the row must expose a folder control for an administrator");
    assert.equal(trigger.tagName, "BUTTON", "the folder control must be a real button, not a text node");
    assert.equal(trigger.getAttribute("type"), "button");
    assert.ok(trigger.classList.contains("files-folder-trigger"),
      "the folder control must carry the button-appearance class");
    assert.match(trigger.textContent, /file\.folderMoveTo/, "it must say what it does (move to folder)");
    assert.match(trigger.textContent, /file\.folderRoot/, "it must show the current location");
    assert.ok(trigger.querySelector(".files-folder-trigger-icon"),
      "it must carry a folder icon, so the control reads as a folder action");
    assert.ok(trigger.querySelector(".files-folder-trigger-caret"),
      "it must carry a chevron, so it reads as something that opens a choice");
  } finally {
    await React.act(async () => { container.unmount(); });
    await unmountAll();
  }
}

// ================================================================ 用例 5：按钮外观有真实样式支撑
// 静态读取样式表：那个类名必须真的带来边框、底色与 hover 态，否则"看起来像按钮"只是口号。
// The stylesheet is read statically: that class has to actually bring a border, a fill and a hover
// state, or "it looks like a button" is only a slogan.
function triggerStylesCase() {
  const css = fs.readFileSync(path.join(__dirname, "../src/globals.css"), "utf8");
  const rule = /\.files-folder-trigger\s*\{([^}]*)\}/.exec(css);
  assert.ok(rule, "globals.css must define .files-folder-trigger");
  assert.match(rule[1], /border:/, "the trigger must draw a border");
  assert.match(rule[1], /background:/, "the trigger must paint a fill");
  assert.ok(/\.files-folder-trigger:hover\s*\{/.test(css), "the trigger must define a hover state");
  assert.ok(/\.files-folder-trigger:focus-visible\s*\{/.test(css), "the trigger must define a focus state");
}

// ================================================================ 用例 6：上传可以选目标文件夹
// 断言：弹窗里出现目标文件夹选择框，默认值就是当前所在文件夹，并把 folderId 一起提交。
// Assertion: the dialog offers a destination picker defaulting to the folder you are inside, and the
// folderId is submitted with the upload.
async function uploadTargetCase() {
  let published = null;
  const rootElement = document.createElement("div");
  rootElement.id = "root";
  document.body.appendChild(rootElement);
  const Component = compile("file-upload-dialog.tsx", null, {
    useDialogExit: () => ({ visible: true, close: () => {} }),
  }).FileUploadDialog;
  const container = createRoot(rootElement);
  await React.act(async () => {
    container.render(React.createElement(React.StrictMode, null, React.createElement(Component, {
      config: CONFIG,
      // 2 = 微积分，即"当前所在文件夹"。
      // 2 is 微积分, the folder currently open.
      defaultFolderId: 2,
      onClose: () => {},
      onPublished: (folderId) => { published = folderId; },
    })));
  });
  await React.act(async () => { await new Promise((resolve) => setTimeout(resolve, 30)); });
  try {
    const select = rootElement.querySelector('select[aria-label="file.folderTarget"]');
    assert.ok(select, "the upload dialog must offer a destination folder picker");
    assert.equal(select.value, "2", "the picker must default to the folder currently open");
    const values = [...select.options].map((option) => option.value);
    assert.deepEqual(values, ["", "1", "2", "3"],
      "the picker must offer the root plus every folder");

    // 选文件 -> 提交：folderId 必须跟着上传一起走。
    // Pick a file and submit: the folderId has to travel with the upload.
    const fileInput = rootElement.querySelector('input[type="file"]');
    assert.ok(fileInput, "the dialog must expose the file input");
    const file = new dom.window.File(["hello world"], "notes.pdf", { type: "application/pdf" });
    Object.defineProperty(fileInput, "files", { configurable: true, value: [file] });
    await React.act(async () => {
      fileInput.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
    const submit = [...rootElement.querySelectorAll("button")].find((button) => button.getAttribute("type") === "submit");
    assert.ok(submit, "the dialog must expose a submit button");
    await React.act(async () => {
      submit.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true, cancelable: true }));
      await new Promise((resolve) => setTimeout(resolve, 60));
    });
    assert.equal(calls.created.length, 1, "submitting must publish exactly once");
    assert.equal(calls.created[0].folderId, 2, "the upload must carry the chosen destination folder");
    assert.equal(published, 2, "the page must be told where the resource actually landed");
  } finally {
    await React.act(async () => { container.unmount(); });
    await unmountAll();
  }
}

// ================================================================ 用例 7：页面把当前文件夹交给上传弹窗
// 断言：在某个文件夹里点"上传资料"，弹窗收到的 defaultFolderId 就是该文件夹。
// Assertion: clicking upload while inside a folder hands that folder to the dialog.
async function pagePassesCurrentFolderCase() {
  resetServer();
  const container = await mountPage();
  try {
    // 进入"数学"：点文件夹行上的"进入"。
    const enter = buttonWith("file.folderEnter", root().querySelectorAll("tr.files-row-folder")[0]);
    await click(enter);
    assert.ok(mainLists().some((params) => params.folderId === 1),
      "entering a folder must refetch the list for that folder");

    await click(buttonWith("file.upload"));
    const stub = root().querySelector("[data-upload-dialog]");
    assert.ok(stub, "the upload dialog must open for an administrator");
    assert.equal(stub.getAttribute("data-folder"), "1",
      "the upload dialog must default to the folder currently open");
  } finally {
    await React.act(async () => { container.unmount(); });
    await unmountAll();
  }
}

// ================================================================ 用例 8：删掉当前文件夹后回到上一级
// 断言：删掉一个顶层文件夹之后，视图必须退回**根目录**（unfiled 取数），而不是退回"全部资料"
// 那个什么都不过滤的旧口径。
// Assertion: after deleting a top-level folder the view must fall back to the **root** (an unfiled
// fetch) rather than to the old "everything" contract that filters nothing.
async function deleteFallsBackToRootCase() {
  resetServer();
  const container = await mountPage();
  try {
    // 英语（第三份文件夹，无子文件夹）位于根视图的第二行。
    // 英语 is the second row of the root view and has no subfolders.
    const rows = [...root().querySelectorAll("tr.files-row-folder")];
    assert.equal(rows.length, 2, "the root view must list the two top-level folders");
    await click(buttonWith("file.folderEnter", rows[1]));
    assert.ok(mainLists().some((params) => params.folderId === 3), "entering a folder must fetch that folder");
    const seen = mainLists().length;

    await click(buttonWith("file.folderDelete"));
    await click(buttonWith("file.folderDeleteConfirm"));
    assert.deepEqual(calls.deletedFolders, [3], "confirming must delete the folder that was armed");

    const after = mainLists().slice(seen);
    assert.ok(after.some((params) => params.unfiled === true && params.folderId === undefined),
      "deleting the open folder must fall back to the root view contract");
  } finally {
    await React.act(async () => { container.unmount(); });
    await unmountAll();
  }
}

(async () => {
  await rootViewIsDefaultCase();
  await oldDefaultViewCase();
  await moveLeavesRootCase();
  await moveButtonDiscoverabilityCase();
  triggerStylesCase();
  await uploadTargetCase();
  await pagePassesCurrentFolderCase();
  await deleteFallsBackToRootCase();

  // 负对照必须真的判红：把默认视图还原成旧行为 "all" 之后，"移入文件夹后必须离开根视图"
  // 这条断言必须失败——这正是管理员报障的形态，也是本次修复的靶心。
  // The negative control must really go red: with the old default view "all" restored, the
  // "a filed resource must leave the root view" assertion has to fail. That is exactly the reported
  // symptom and the bullseye of this fix.
  await assert.rejects(
    () => moveLeavesRootCase(OLD_DEFAULT_VIEW_PATCH),
    /must leave the root view/,
    "negative control: restoring the old default view must break the root-view assertion",
  );
  // 负对照必须真的判红：把按钮外观类名拿掉之后，"必须带按钮外观类名"这条断言必须失败。
  // The negative control must really go red: without the button-appearance class the
  // "must carry the button-appearance class" assertion has to fail.
  await assert.rejects(
    () => moveButtonDiscoverabilityCase(PLAIN_TEXT_TRIGGER_PATCH),
    /must carry the button-appearance class/,
    "negative control: the plain-text control must fail the button assertion",
  );
  console.log("files-root-view-smoke: ok");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
