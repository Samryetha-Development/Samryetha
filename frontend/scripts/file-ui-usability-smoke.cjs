// 文件服务界面可用性冒烟检查（Stage 4：文件夹行形态 + 行内编辑入口 + 可编辑属性；
// 并在验收缺陷修复中补齐 status/visibility/取消删除 的覆盖）。
// Smoke check for file-service interface usability (stage 4: folder rows as a distinct shape, the
// in-row edit entry point, and the editable attributes; the acceptance-defect fix adds coverage for
// status, visibility and cancelling a delete).
//
// 无测试框架、无新依赖，沿用本仓库既有套路（jsdom + ts.transpileModule + 注入桩）。
// No test framework and no new dependencies: this follows the repository's existing pattern
// (jsdom + ts.transpileModule + injected stubs).
//
// 「负对照」的改法（本次）：此前负对照靠**源码逐字片段**做补丁——`source.includes(patch.from)`
// 一旦对不上就断言 "negative control anchor not found"，一次正常重构（改缩进、拆函数、改文案）
// 都会让它变红，而红的原因与被测行为毫无关系。现在负对照一律**行为级**：不再改源码字符，
// 而是改**输入或桩的行为**（换身份、换一行要删的资料、让服务端在弹窗打开后被动过、
// 让接口成功或失败），断言必须跟着输入走。判据很直白：负对照那一遍失败，说明这份断言真的
// 盯着被测行为；两遍都"恰好成立"就说明它是恒真的假绿。
// How negative controls work now: they used to be source-literal patches, where a single
// `source.includes(patch.from)` mismatch failed with "negative control anchor not found" - so any
// ordinary refactor (re-indentation, extracting a function, reworded copy) turned it red for a reason
// that had nothing to do with the behaviour under test. Every negative control is now behavioural: no
// source characters are touched; instead the *inputs or the stub's behaviour* change (a different
// identity, a different row to delete, the server moving after the dialog opened, the endpoint
// succeeding or failing) and the assertion has to follow. The test is simple: if the negative-control
// pass fails, the assertion really is watching the behaviour; if both passes hold no matter the
// input, the assertion was vacuously green.
//
// 本脚本覆盖：
// ① 文件夹在 GUI 里必须"看起来就是文件夹"（独立行形态、图标、类型标识、条目数、可进入、可返回）；
// ② 文件名与"分区属性"必须可改，且真的进得了保存载荷；
// ③ 编辑入口必须与"下载/收藏/删除"并列在行内操作区；
// ④ 状态与可见性必须真的进保存载荷，且未知取值不得被伪装成某个合法状态；
// ⑤ 保存成功后弹窗必须回填**服务端归一化结果**；
// ⑥ 陈旧的弹窗不得静默覆盖别人刚做的改动；
// ⑦ 两段式删除：武装、可取消、只删被武装的那一行。
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
// 把 JSDOM 自带的空 #root 摘掉：否则每个用例新挂的 #root 会变成第二个同 id 元素，
// getElementById 恒返回先出现的空壳，所有文本断言都会读到空字符串（假绿）。
// The empty #root JSDOM ships with is detached first: otherwise each case's fresh #root becomes a
// second element with the same id, getElementById keeps returning the earlier empty shell, and every
// text assertion reads an empty string.
document.getElementById("root").remove();
// 把真实样式表注入 jsdom：F1 的回归锁要看的是 `<td>` 的**计算 display**，没有样式表就只有浏览器
// 默认值，测不出"某条规则有没有把它踢出 table-cell"。
// The real stylesheet is injected into jsdom: the F1 regression lock reads the `<td>`'s *computed
// display*, and without a stylesheet there is only the browser default, which cannot tell whether some
// rule knocked the cell out of table-cell.
const STYLESHEET = dom.window.document.createElement("style");
STYLESHEET.textContent = fs.readFileSync(path.join(__dirname, "../src/globals.css"), "utf8")
  .split("\n")
  .filter((line) => !/^\s*@import/.test(line))
  .join("\n");
dom.window.document.head.appendChild(STYLESHEET);
const React = require("react");
const { createRoot } = require("react-dom/client");

// 插值桩把变量拼进文案，好让断言能验证插值确实到了界面上。
// The stub splices interpolated variables into the copy so an assertion can check that the value
// actually reached the interface.
const t = (key, vars) => (vars
  ? `${key}(${Object.entries(vars).map(([name, value]) => `${name}=${value}`).join(",")})`
  : key);

const ADMIN = { id: 7, username: "admin", handle: "admin", displayName: "Admin", role: "admin" };
const PLAIN_USER = { id: 99, username: "bob", handle: "bob", displayName: "Bob", role: "member" };

const FOLDERS = [
  { id: 1, name: "数学", description: "数学类资料", sortOrder: 0, resourceCount: 3, childCount: 1 },
  { id: 2, parentId: 1, name: "微积分", description: "", sortOrder: 0, resourceCount: 2, childCount: 0 },
  { id: 3, name: "英语", description: "", sortOrder: 1, resourceCount: 0, childCount: 0 },
];

const CONFIG = {
  categories: [
    { id: 1, slug: "guide", name: "Guide", description: "", kind: "guide", sortOrder: 0, isSystem: true, resourceCount: 1 },
    { id: 2, slug: "exam", name: "Exam", description: "", kind: "exam", sortOrder: 1, isSystem: false, resourceCount: 0 },
  ],
  folders: FOLDERS,
  tagCloud: [],
  stats: { resourceCount: 1, categoryCount: 2 },
  allowedExtensions: [".pdf"],
  maxUploadBytes: 1024,
  kinds: ["guide", "exam"],
  visibilities: ["public", "members", "private"],
  statuses: ["published", "archived"],
  sorts: ["latest"],
  maxTags: 8,
};

const ITEM = {
  id: 42,
  title: "高等数学提纲",
  preview: "",
  folderId: null,
  category: { id: 1, slug: "guide", name: "Guide", kind: "guide" },
  uploader: { id: 7, username: "admin", handle: "admin", displayName: "Admin" },
  tags: ["微积分"],
  originalFilename: "notes.pdf",
  mimeType: "application/pdf",
  extension: ".pdf",
  sizeBytes: 11,
  visibility: "members",
  status: "published",
  version: 1,
  isFeatured: false,
  downloadCount: 0,
  favoriteCount: 0,
  ratingAvg: null,
  ratingCount: 0,
  isFavorited: false,
  myRating: null,
  createdAt: 0,
  updatedAt: 0,
};

const ITEM_TWO = { ...ITEM, id: 43, title: "线性代数提纲" };

const DETAIL = {
  ...ITEM,
  descriptionMarkdown: "覆盖极限、导数与应用题。",
  can: { update: true, delete: true },
};

const calls = { list: [], get: [], update: [], del: [] };
let currentUser = ADMIN;
let serverDetail = DETAIL;
let serverItems = [ITEM];
let getOverride = null;
let updateFails = false;
let updatePayload = null;
const SERVER_REASON = "description is longer than the allowed limit";

class ApiErrorStub extends Error {
  constructor(status, payload) {
    super(payload?.message ?? "failed");
    this.status = status;
  }
}

// 服务端的标签归一化：小写、去重、单个截断 24 字符、只保留前 8 个。
// 桩必须照抄这套规则，否则"弹窗回填服务端结果"这条断言就测不出真正的差别。
// The server's tag normalisation: lower-cased, de-duplicated, each truncated to 24 characters, at most
// eight kept. The stub copies those rules, or the "dialog shows the server's result" assertion would
// not be measuring a real difference.
const MAX_TAGS = 8;
const MAX_TAG_LENGTH = 24;
function normalizeTagsStub(values) {
  const tags = [];
  for (const raw of values) {
    const tag = String(raw).trim().toLowerCase().slice(0, MAX_TAG_LENGTH);
    if (tag && !tags.includes(tag)) tags.push(tag);
  }
  return tags.slice(0, MAX_TAGS);
}

// 纯逻辑层从真实源码编译：本脚本要测的是"页面真的按当前层级列出文件夹"，
// 把纯函数打桩就等于把被测行为换成了假的。
// The pure logic layer is compiled from the real source: what is under test is that the page really
// lists the folders at the current level, and stubbing the pure functions would replace the very
// behaviour being examined with a fake.
function loadPure(file) {
  const source = fs.readFileSync(path.join(__dirname, "../src", file), "utf8");
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React } }).outputText;
  const exported = {};
  new Function("exports", output)(exported);
  return exported;
}

const folderTree = loadPure("lib/folder-tree.ts");
const dropUpload = loadPure("lib/drop-upload.ts");

// 编辑弹窗。因为 index 与 files-page 之间存在循环引用，这里留一个可变的间接层（不再换实现，
// 只用来打断编译期的循环）。
// The editor dialog. The index and files-page reference each other, so a mutable indirection stays
// here - it no longer swaps implementations, it only breaks the compile-time cycle.
let dialogModule = null;
// 文件夹条的负对照补丁（见 mount 的 options.barPatch）。
// Negative-control patches for the folder bar (see options.barPatch in mount).
let barPatches = null;

const inputs = {
  ...require("./ui-smoke-inputs.cjs"),
  ...React,
  AppShell: ({ children }) => React.createElement("div", null, children),
  Loading: () => React.createElement("div", { "data-loading": true }, "LOADING"),
  // 身份可变：权限口径的**行为级负对照**要换一个非管理员的旁人来验证。
  // The identity is mutable: the behavioural negative control for the permission rule needs an
  // unrelated plain user.
  useAuth: () => ({ user: currentUser, loading: false }),
  useI18n: () => ({ t, locale: "en" }),
  timeAgo: () => "",
  formatBytes: () => "11 bytes",
  MarkdownText: () => null,
  // ui-commons 的 Dialog 做成透明壳：本脚本要看的是表单里的字段与载荷，不是弹层实现。
  // The ui-commons Dialog is a transparent shell here: what is under test is the form's fields and
  // its payload, not the overlay implementation.
  Dialog: ({ children, title }) => React.createElement("div", null, React.createElement("h2", null, title), children),
  FileUploadDialog: () => React.createElement("div", { "data-upload-dialog": true }, "UPLOAD_DIALOG"),
  // 批量操作工具条做成透明桩：本脚本看的是列表与表单，批量操作另有自己的冒烟脚本。
  // The batch toolbar is a transparent stub: this script looks at the list and the forms, and batch
  // operations have a smoke script of their own.
  FileBatchBar: () => null,
  // 文件夹导航条不能再做成空桩：用例 F/G 要断言它挂在哪些标签下、条里有没有内容，
  // 桩掉就只会得到"永远没有导航条"，那种断言连红都判不出来。因此这里注入**真实源码**。
  // The folder bar can no longer be an empty stub: cases F and G assert which tabs it is mounted
  // under and whether it carries content, and stubbing it would only ever produce "there is no bar",
  // an assertion that cannot discriminate. It is compiled from the real source instead.
  FileFolderBar: (props) => React.createElement(compile("file-folder-bar.tsx", barPatches).FileFolderBar, props),
  FileMetadataDialog: (props) => React.createElement(dialogModule.FileMetadataDialog, props),
  ApiError: ApiErrorStub,
  uploadFileBytes: async () => {},
  planDropEntries: dropUpload.planDropEntries,
  runDropUploads: dropUpload.runDropUploads,
  summarizeDrop: dropUpload.summarizeDrop,
  titleFromFilename: dropUpload.titleFromFilename,
  flattenFolders: (...args) => folderTree.flattenFolders(...args),
  folderPathLabel: (...args) => folderTree.folderPathLabel(...args),
  foldersAtLevel: (...args) => folderTree.foldersAtLevel(...args),
  parentFolderView: (...args) => folderTree.parentFolderView(...args),
  // 真实 FileFolderBar 还用到这三个纯函数，缺一个就是 ReferenceError。
  // The real FileFolderBar also calls these three pure functions; a missing one is a ReferenceError.
  folderById: (...args) => folderTree.folderById(...args),
  folderBreadcrumb: (...args) => folderTree.folderBreadcrumb(...args),
  viewFolderId: (...args) => folderTree.viewFolderId(...args),
  api: {
    files: {
      config: async () => CONFIG,
      list: async (params = {}) => {
        calls.list.push(params);
        return { items: serverItems, total: serverItems.length, page: 1, pageSize: 20, sort: "latest" };
      },
      favorites: async () => ({ items: [], total: 0 }),
      mine: async () => ({ items: [], total: 0 }),
      // 取详情：既给"点编辑"用，也给"保存前比对服务端现值"用。返回**当前**服务端状态，
      // 测试因此可以模拟"弹窗打开之后别人动过这份资料"。
      // Fetching the detail serves both the edit click and the pre-save comparison. It returns the
      // *current* server state, which is how a test simulates "someone else touched this resource
      // after the dialog opened".
      get: async (id) => { calls.get.push(id); return getOverride ? getOverride(id) : serverDetail; },
      download: async () => { throw new ApiErrorStub(500, { message: "not used" }); },
      setFavorite: async (id, on) => ({ resourceId: id, isFavorited: on, favoriteCount: 0 }),
      setFolder: async () => serverDetail,
      update: async (id, body) => {
        calls.update.push([id, body]);
        updatePayload = body;
        if (updateFails) throw new ApiErrorStub(422, { code: "TOO_LONG", message: SERVER_REASON });
        // 服务端会归一化并把归一化结果返回；桩照抄，才能验证弹窗回填的是**服务端那一份**。
        // The server normalises and returns the normalised result; the stub copies it so that the
        // assertion can tell whether the dialog shows *the server's* copy.
        const saved = {
          ...DETAIL,
          title: String(body.title ?? DETAIL.title).trim(),
          tags: normalizeTagsStub(body.tags ?? DETAIL.tags),
          descriptionMarkdown: body.descriptionMarkdown ?? DETAIL.descriptionMarkdown,
          category: CONFIG.categories.find((entry) => entry.id === (body.categoryId ?? DETAIL.category.id)) ?? DETAIL.category,
          visibility: body.visibility ?? DETAIL.visibility,
          status: body.status ?? DETAIL.status,
        };
        serverDetail = saved;
        serverItems = serverItems.map((row) => (row.id === id ? { ...row, ...saved } : row));
        return saved;
      },
      del: async (id) => {
        calls.del.push(id);
        serverItems = serverItems.filter((row) => row.id !== id);
      },
    },
  },
};

function transpile(file, patch) {
  // 统一换行符后再编译：仓库里的 .tsx 是 CRLF，编译与补丁语义都不该受它影响。
  // Line endings are normalised before compiling: the repository's .tsx files are CRLF and neither the
  // compile nor the patch semantics should depend on that.
  let source = fs.readFileSync(path.join(__dirname, "../src", file), "utf8").replace(/\r\n/g, "\n").replace(/^import .*;\n/gm, "");
  // 负对照可以一次给多段补丁：有些"旧行为"要动两处（开标签与闭标签）才回得去。
  // A negative control may supply several patches at once: some old behaviours need two anchors (an
  // opening and a closing tag) to be restored.
  const patches = !patch ? [] : Array.isArray(patch) ? patch : [patch];
  for (const entry of patches) {
    assert.ok(source.includes(entry.from), `negative control anchor not found in ${file}: ${entry.from}`);
    source = source.replace(entry.from, entry.to);
  }
  return ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React } }).outputText;
}

function compile(file, patch) {
  const exported = {};
  new Function("exports", "React", ...Object.keys(inputs), transpile(file, patch))(exported, React, ...Object.values(inputs));
  return exported;
}

async function mount(options = {}) {
  currentUser = options.user ?? ADMIN;
  serverItems = options.items ?? [ITEM];
  serverDetail = options.detail ?? DETAIL;
  // 文件夹条的负对照补丁单独一份：它渲染的文件与 files-page 不同，混在一起会找不到锚点。
  // The folder bar needs its own negative-control patches: it is a different file from files-page, so
  // mixing the two sets would leave anchors unfound.
  barPatches = options.barPatch ?? null;
  dialogModule = compile("file-metadata-dialog.tsx");
  const rootElement = document.createElement("div");
  rootElement.id = "root";
  document.body.appendChild(rootElement);
  const Component = compile("files-page.tsx", options.patch).FilesPage;
  const container = createRoot(rootElement);
  container.pageComponent = Component;
  await React.act(async () => {
    container.render(React.createElement(React.StrictMode, null, React.createElement(Component)));
  });
  await React.act(async () => { await new Promise((resolve) => setTimeout(resolve, 30)); });
  return container;
}

const root = () => document.getElementById("root");
const text = () => root().textContent;
const buttonWith = (label, scope) =>
  [...(scope ?? root()).querySelectorAll("button")].find((element) => element.textContent === label);
// "返回上一级"按钮内还带一个方向箭头，文本不是恰好等于文案键，所以另给一个包含匹配的取法。
// The step-up button carries a direction arrow alongside its label, so its text is not exactly the
// copy key; a containment lookup covers it.
const buttonContaining = (label, scope) =>
  [...(scope ?? root()).querySelectorAll("button")].find((element) => element.textContent.includes(label));
const folderRows = () => [...root().querySelectorAll("tbody.files-folder-rows tr.files-row-folder")];
const fileRows = () => [...root().querySelectorAll("tbody:not(.files-folder-rows) tr")];
const dialogForm = () => [...root().querySelectorAll("form")].find((form) => form.querySelector("textarea"));
const confirmBlock = (row) => row.querySelector(".files-row-confirm");

async function click(element) {
  assert.ok(element, "expected the target control to be rendered");
  await React.act(async () => {
    element.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true, cancelable: true }));
    await new Promise((resolve) => setTimeout(resolve, 30));
  });
}

async function setValue(element, value, tag) {
  const prototype = tag === "select"
    ? dom.window.HTMLSelectElement.prototype
    : element.tagName === "TEXTAREA"
      ? dom.window.HTMLTextAreaElement.prototype
      : dom.window.HTMLInputElement.prototype;
  await React.act(async () => {
    Object.getOwnPropertyDescriptor(prototype, "value").set.call(element, value);
    element.dispatchEvent(new dom.window.Event(tag === "select" ? "change" : "input", { bubbles: true }));
    await new Promise((resolve) => setTimeout(resolve, 10));
  });
}

async function submit(form) {
  await React.act(async () => {
    form.dispatchEvent(new dom.window.Event("submit", { bubbles: true, cancelable: true }));
    await new Promise((resolve) => setTimeout(resolve, 30));
  });
}

function reset() {
  calls.list = [];
  calls.get = [];
  calls.update = [];
  calls.del = [];
  currentUser = ADMIN;
  serverDetail = DETAIL;
  serverItems = [ITEM];
  getOverride = null;
  updateFails = false;
  updatePayload = null;
  root()?.remove();
}

// ================================================================ 用例 A：文件夹行
// 这一段直接对应管理员的原话"文件夹在 GUI 里面显示的不像文件夹"。
// This block answers the administrator's own words: folders do not look like folders in the GUI.
async function folderRowCase() {
  const container = await mount();
  try {
    // ① 根层级必须列出顶层文件夹（"微积分"是"数学"的子级，不该在这一层出现）。
    const rows = folderRows();
    assert.equal(rows.length, 2, "the root level must list exactly the top-level folders");
    assert.match(rows[0].textContent, /数学/, "the first folder row must name its folder");
    assert.match(rows[1].textContent, /英语/, "the second folder row must name its folder");
    // 只断言"文件夹行"里没有子级：行内"所属文件夹"下拉是整棵树，那里出现"微积分"是正确的。
    // Only the folder rows are checked for the absence of a child: the in-row folder picker offers the
    // whole tree, where "微积分" is supposed to appear.
    assert.ok(!rows.some((row) => /微积分/.test(row.textContent)),
      "a nested folder must not be listed at the root level");

    // ② 三样"这是文件夹"的标识齐备：图标（内联 SVG，不是字形）、"文件夹"字样、条目数。
    for (const row of rows) {
      assert.equal(row.querySelectorAll(".files-folder-icon svg").length, 1,
        "every folder row must carry exactly one folder icon (an inline SVG)");
      assert.ok(row.querySelector(".files-folder-kind"), "every folder row must carry a type label");
      assert.ok(row.querySelector(".files-folder-count"), "every folder row must show its item count");
    }
    assert.match(rows[0].textContent, /file\.folderKind/, "the type label must say it is a folder");
    assert.match(rows[0].textContent, /file\.folderItemCount\(count=3\)/,
      "the item count must be the folder's own resource count");
    assert.match(rows[0].textContent, /file\.folderChildCount\(count=1\)/,
      "a folder with children must say how many it has");

    // ③ 文件夹行整体排在资料行之前（容器先于内容）。
    const tbodies = [...root().querySelectorAll("table.files-table > tbody")];
    assert.ok(tbodies.length >= 2, "the table must keep folder rows and resource rows in separate bodies");
    assert.ok(tbodies[0].classList.contains("files-folder-rows"), "the folder body must come first");
    assert.equal(fileRows().length, 1, "the resource body must still hold the resource row");
    assert.ok(tbodies[0].compareDocumentPosition(tbodies[1]) & dom.window.Node.DOCUMENT_POSITION_FOLLOWING,
      "folder rows must precede resource rows in document order");

    // ④ 可进入：点"进入"必须真的下钻到该文件夹（列表带着 folderId 重新取数，
    //    并且这一层该出现的是它的子文件夹）。
    await click(buttonWith("file.folderEnter", rows[0]));
    assert.ok(calls.list.some((params) => params.folderId === 1),
      "entering a folder must refetch the list filtered by that folder id");
    const entered = folderRows();
    assert.equal(entered.length, 1, "inside a folder the list must show that folder's children");
    assert.match(entered[0].textContent, /微积分/, "the child folder must now be listed");

    // ④b 行为级负对照（容器先于内容、按层级过滤这两件事的反面）：
    //     下钻到一个**没有子文件夹**的层级时，文件夹行必须是 0 行。若实现把"按当前层级取文件夹"
    //     换成"永远列出全部文件夹"（负对照的行为），这里就会冒出 3 行；反过来若文件夹行恒不渲染，
    //     上面 ① 已经变红。两遍一起，才说明这两个断言盯着的是真行为。
    //     Behavioural negative control for "filter by the current level": inside a folder that has no
    //     subfolders there must be no folder row at all. An implementation that lists every folder
    //     everywhere would show three rows here, while one that never renders them already failed (1).
    await click(buttonContaining("file.folderUp", root().querySelector("tr.files-row-up")));
    await click(buttonWith("file.folderEnter", folderRows()[1]));
    assert.ok(calls.list.some((params) => params.folderId === 3),
      "entering the empty folder must still refetch the list for that folder");
    assert.equal(folderRows().length, 0,
      "a folder with no subfolders must list none, proving the rows follow the current level");

    // ⑤ 可返回：进入之后必须有一条明确的"返回上一级"。
    const upRow = root().querySelector("tr.files-row-up");
    assert.ok(upRow, "inside a folder the list must offer an explicit step up");
    assert.match(upRow.textContent, /file\.folderUp/, "the step-up row must be labelled as such");
    await click(buttonContaining("file.folderUp", upRow));
    assert.equal(folderRows().length, 2, "stepping up must restore the parent level");
    // 退回根目录后，取数必须换成根视图的口径（unfiled=true），不再是"什么都不过滤"。
    // 旧断言要求两个参数都不带，那正是"已归档资料还会出现在外面"的根因口径，本次刻意改掉。
    // After stepping back to the root the fetch must use the root view's contract (unfiled=true)
    // rather than "filter nothing". The old assertion demanded neither parameter, which is exactly the
    // contract that let filed resources show up outside their folder - deliberately changed here.
    assert.ok(calls.list.some((params) => params.unfiled === true && params.folderId === undefined),
      "stepping up to the root must switch the list to the root view contract");
  } finally {
    await React.act(async () => { container.unmount(); });
    reset();
  }
}

// ================================================================ 用例 A2：文件夹行必须真的跨列
// F1 的回归锁（此前这一条只有一次性的真实浏览器量测，没有可重复的断言）。
// 带 colSpan 的那一格必须是真正的 table-cell：一旦在它上面（或它的样式规则里）写了 display:flex，
// 它就不再是 table-cell，浏览器会替它生成匿名 table-cell 包住它，colSpan 随之失效——底色只覆盖到
// 第 2 列、行内动作掉出动作列。真实 Edge 实测（dev 65988d5）：格右缘 707.8、动作列左缘 1287.7、
// 行内「Open」按钮 x=28.7、行高 108.7；修复后同一量测为 1169.1 / 1169.1 / 1181.1 / 46.7。
// 弹性布局因此下沉到内层 .files-folder-main。
// Regression lock for F1 (previously a one-off browser measurement with no repeatable assertion). The
// cell carrying colSpan has to be a real table-cell: writing display:flex on it (or in its rule) stops
// it being one, the browser wraps it in an anonymous table-cell and the colspan is lost - the fill
// stops inside the second column and the in-row action leaves the action column. Measured in real Edge
// at dev 65988d5: cell edge 707.8, action column 1287.7, in-row "Open" at x=28.7, row height 108.7;
// after the fix the same bench reads 1169.1 / 1169.1 / 1181.1 / 46.7. The flex layout therefore lives
// on an inner .files-folder-main.
async function folderRowSpansColumnsCase() {
  const container = await mount();
  try {
    const cell = folderRows()[0].querySelector("td.files-cell-folder");
    assert.ok(cell, "the folder row must have its spanning cell");
    assert.equal(cell.getAttribute("colspan"), "9",
      "the cell must span the nine columns that are not the action column");
    const main = cell.querySelector(".files-folder-main");
    assert.ok(main, "the flex layout must live on an inner element rather than on the spanning cell");
    assert.equal(dom.window.getComputedStyle(cell).display, "table-cell",
      "a colSpan-bearing cell must stay a table-cell, or the span is silently dropped");
    assert.equal(dom.window.getComputedStyle(main).display, "flex",
      "the inner wrapper must be the flex container");

    // 行为级负对照：给一个 <td> 挂上 `display:flex` 的规则，它就必须不再是 table-cell。
    // 这一遍证明上面那条断言是被**样式**决定的，而不是一个恒为 table-cell 的常量。
    // Behavioural negative control: a <td> with a `display:flex` rule must stop being a table-cell.
    // This proves the assertion above is decided by the stylesheet rather than by a constant.
    const probeStyle = document.createElement("style");
    probeStyle.textContent = ".files-cell-probe { display: flex; }";
    document.head.appendChild(probeStyle);
    const probe = document.createElement("td");
    probe.className = "files-cell-probe";
    document.body.appendChild(probe);
    try {
      assert.equal(dom.window.getComputedStyle(probe).display, "flex",
        "negative control: display:flex on a td really does override its table-cell default");
    } finally {
      probe.remove();
      probeStyle.remove();
    }
  } finally {
    await React.act(async () => { container.unmount(); });
    reset();
  }
}

// ================================================================ 用例 B：行内编辑入口
// 管理员原话："编辑按钮也不在'删除''下载''收藏'那里"。
// The administrator's own words: the edit button is not where delete, download and save are.
async function editEntryCase() {
  const container = await mount();
  try {
    const row = fileRows()[0];
    assert.ok(row, "the resource row must be rendered");
    const group = row.querySelector(".files-row-actions");
    assert.ok(group, "the row must have one action group");
    const labels = [...group.querySelectorAll("button")].map((element) => element.textContent);

    // 四个动作必须在同一组里并列：下载、收藏、编辑、删除。
    assert.deepEqual(labels, ["file.download", "file.favorite", "file.edit", "file.delete"],
      "download, save, edit and delete must sit in one action group, edit included");

    // 点"编辑"必须真的打开编辑器：先取该资料的详情，再渲染编辑表单。
    await click(buttonWith("file.edit", group));
    assert.deepEqual(calls.get, [42], "the edit entry point must fetch the resource detail by id");
    const form = dialogForm();
    assert.ok(form, "clicking edit must open the editor");
    assert.equal(form.querySelectorAll("select").length, 3,
      "the editor must offer the section, visibility and status selects");
  } finally {
    await React.act(async () => { container.unmount(); });
    reset();
  }

  // 行为级负对照：换一个**非管理员**的旁人看别人的资料，入口必须整个消失——
  // 编辑、删除、以及"所属文件夹"下拉都同属 FILE_UPDATE 口径。把权限判断放宽成"人人可见"
  // （负对照的行为），这三个入口就会冒出来，本段变红。
  // Behavioural negative control: an unrelated plain user looking at someone else's resource must see
  // none of these entries - edit, delete and the "folder" picker all follow FILE_UPDATE. Widening the
  // gate to everyone would reveal all three and turn this block red.
  const other = await mount({ user: PLAIN_USER });
  try {
    const row = fileRows()[0];
    assert.ok(row, "the resource row must be rendered for a plain viewer too");
    const group = row.querySelector(".files-row-actions");
    assert.deepEqual([...group.querySelectorAll("button")].map((element) => element.textContent),
      ["file.download", "file.favorite"],
      "a viewer who may not write the resource must not be offered edit or delete");
    assert.equal(row.querySelector(".files-move"), null,
      "a viewer who may not write the resource must not be offered the folder picker");
    assert.equal(buttonWith("file.edit", row), undefined, "no edit control may exist for such a viewer");
  } finally {
    await React.act(async () => { container.unmount(); });
    reset();
  }
}

// ================================================================ 用例 C/D：载荷
// 从列表行打开编辑器、改名、换分区、改可见性、改状态、保存——载荷必须真的带上这些值。
// Opening the editor from the row, renaming it, changing section, visibility and status: every one of
// those values has to reach the payload.
async function editPayloadCase(mode) {
  const untouched = mode === "untouched";
  const container = await mount(untouched
    ? { detail: { ...DETAIL, status: "archived", visibility: "private" } }
    : undefined);
  try {
    await click(buttonWith("file.edit", fileRows()[0].querySelector(".files-row-actions")));
    const form = dialogForm();
    assert.ok(form, "the editor must be open");
    const [titleInput, tagsInput] = form.querySelectorAll("input");
    void tagsInput;
    const [categorySelect, visibilitySelect, statusSelect] = form.querySelectorAll("select");

    if (untouched) {
      // 一个字段都不碰就保存：载荷里必须是**这份资料当前的值**。把任何一个字段写死成
      // "published"/"members"/固定标题（负对照的行为），这里立刻变红。
      // Saving without touching a single field: the payload must carry this resource's *current*
      // values. Hard-coding any of them to "published", "members" or a fixed title turns this red.
      await submit(form);
      assert.equal(calls.update.length, 1, "the save must be sent");
      assert.equal(updatePayload.title, "高等数学提纲", "an untouched name must be sent back as stored");
      assert.equal(updatePayload.status, "archived",
        "an untouched status must be sent back as stored, never as a hard-coded default");
      assert.equal(updatePayload.visibility, "private",
        "an untouched visibility must be sent back as stored, never as a hard-coded default");
      assert.equal(updatePayload.categoryId, 1, "an untouched section must be sent back as stored");
      return;
    }

    await setValue(titleInput, "  线性代数提纲  ");
    await setValue(categorySelect, "2", "select");
    await setValue(visibilitySelect, "public", "select");
    await setValue(statusSelect, "archived", "select");
    await submit(form);

    assert.equal(calls.update.length, 1, "saving must send exactly one request");
    assert.equal(updatePayload.title, "线性代数提纲",
      "the typed file name must reach the payload, trimmed");
    assert.equal(updatePayload.categoryId, 2,
      "the chosen section (category) must reach the payload");
    assert.equal(updatePayload.visibility, "public",
      "the chosen visibility must reach the payload");
    assert.equal(updatePayload.status, "archived",
      "the chosen status must reach the payload");
  } finally {
    await React.act(async () => { container.unmount(); });
    reset();
  }
}

// ================================================================ 用例 E：弹窗回填服务端归一化结果
// 服务端会归一化（标题去首尾空白、标签小写去重、只留前 8 个、单个截断 24 字符），
// 保存成功后弹窗必须显示**真正落库的那一份**，否则界面在替库里的数据说谎。
// The server normalises (the title is trimmed, tags are lower-cased, de-duplicated, capped at eight
// and truncated to 24 characters each). After a successful save the dialog must show *what was
// stored*, or the screen misrepresents the database.
async function editWriteBackCase() {
  const container = await mount();
  try {
    await click(buttonWith("file.edit", fileRows()[0].querySelector(".files-row-actions")));
    const form = dialogForm();
    assert.ok(form, "the editor must be open");
    const [titleInput, tagsInput] = form.querySelectorAll("input");

    const rawTitle = "  线性代数  ";
    const rawTags = [
      "Algebra", "algebra", "ALGEBRA", "微积分", "期末", "复习",
      "这是第二十四个字符之后的标签内容abcdefghij", "七", "八", "九", "十", "十一",
    ];
    await setValue(titleInput, rawTitle);
    await setValue(tagsInput, rawTags.join(", "));
    await submit(form);

    assert.equal(calls.update.length, 1, "the save must be sent");
    const expectedTags = normalizeTagsStub(updatePayload.tags);
    // 客户端只按分隔符切开、去空白，不替服务端做归一化：载荷里必须还是用户输入的那个样子。
    // The client only splits on the separators and trims; normalisation is not its job, so the payload
    // still carries what the user typed.
    assert.deepEqual(updatePayload.tags.slice(0, 3), ["Algebra", "algebra", "ALGEBRA"],
      "the payload must carry the tags as typed; normalisation is the server's job");
    assert.equal(updatePayload.tags.length, 12, "every typed tag must reach the payload");
    assert.equal(titleInput.value, "线性代数",
      "the dialog must show the server's normalised name, not the raw input");
    assert.equal(tagsInput.value, expectedTags.join(", "),
      "the dialog must show the server's normalised tag list, not the raw input");
    assert.equal(expectedTags.length, 8, "the server keeps only the first eight tags");
    assert.ok(!expectedTags.some((tag) => /[A-Z]/.test(tag)), "the server lower-cases tags");
    assert.ok(expectedTags.some((tag) => tag.length === MAX_TAG_LENGTH),
      "the server truncates a long tag to 24 characters");
    // 非恒真证明：这一次输入的原样值**必须**与回填结果不同——若相同，上面的相等断言就算
    // 完全不回填也会通过（假绿）。
    // Non-vacuity: the raw input must differ from what was written back, or the equality assertions
    // above would hold even with no write-back at all.
    assert.notEqual(titleInput.value, rawTitle,
      "the raw input must differ from the written-back value, or this case would be vacuous");
    assert.notEqual(tagsInput.value, rawTags.join(", "), "the raw tag list must differ too");
  } finally {
    await React.act(async () => { container.unmount(); });
    reset();
  }
}

// ================================================================ 用例 F：陈旧弹窗
// 弹窗每次都会把**全量字段**发出去，所以一个陈旧的弹窗不止覆盖标签与简介，还会把可见性与
// 状态一起盖回去（别人刚下架的资料会被静默重新发布）。保护方式是"提示 + 用户确认"，
// 不引入乐观锁（仓库口径是后写覆盖）。
// The dialog sends every field on every save, so a stale one overwrites not just tags and description
// but visibility and status too - a resource somebody else just archived would be silently
// republished. The guard is a notice plus an explicit confirmation, with no optimistic locking, since
// the repository's rule is last-write-wins.
async function staleDialogCase() {
  // (1) 服务端没有动过：第一次提交必须**直接保存**，且不出现任何冲突提示。
  //     把保护做成"永远拦一下"（负对照的行为），这一半立刻变红。
  //     Nothing moved on the server: the first submit must save straight away with no conflict notice.
  //     A guard that always interrupts (the negative-control behaviour) turns this half red.
  const clean = await mount();
  try {
    await click(buttonWith("file.edit", fileRows()[0].querySelector(".files-row-actions")));
    const form = dialogForm();
    await submit(form);
    assert.equal(calls.update.length, 1, "an unchanged server must not block the save");
    assert.equal(root().querySelector(".files-meta-conflict"), null,
      "an unchanged server must not raise a conflict notice");
    assert.ok(root().querySelector(".files-folder-notice"), "and the save must still report success");
  } finally {
    await React.act(async () => { clean.unmount(); });
    reset();
  }

  // (2) 弹窗打开之后别人把资料下架了：第一次提交**不得**静默保存，必须指出对方改了哪些字段；
  //     用户点"仍然保存"之后才真的写下去，且写下的仍是表单里的值（后写覆盖口径不变）。
  //     Somebody archived the resource after the dialog opened: the first submit must not save
  //     silently, it must name the fields that moved. Only "save anyway" writes, and it still writes
  //     the form's values - last-write-wins is unchanged.
  const drifted = await mount();
  try {
    await click(buttonWith("file.edit", fileRows()[0].querySelector(".files-row-actions")));
    const form = dialogForm();
    assert.ok(form, "the editor must be open");
    serverDetail = { ...DETAIL, status: "archived" };
    await submit(form);
    assert.deepEqual(calls.update, [], "a stale dialog must not overwrite silently");
    const notice = root().querySelector(".files-meta-conflict");
    assert.ok(notice, "a stale dialog must say so before overwriting");
    assert.match(notice.textContent, /file\.metaConflictFields\(fields=file\.metaStatusLabel\)/,
      "the notice must name the field the other editor changed");
    assert.equal(notice.getAttribute("role"), "alert", "the notice must be announced, not merely drawn");

    await click(buttonWith("file.metaConflictSave", notice));
    assert.equal(calls.update.length, 1, "confirming must perform exactly one save");
    assert.equal(updatePayload.status, "published",
      "the confirmed save still writes the form's values (last write wins, no optimistic lock)");
  } finally {
    await React.act(async () => { drifted.unmount(); });
    reset();
  }
}

// ================================================================ 用例 I：未知的状态 / 可见性取值
// 后端将来新增一个取值（draft、everyone……）时，下拉必须**原样显示那个值**。此前未知值会回退成
// "已发布 / 登录学生可见"，用户于是凭一个并不成立的状态做判断。这一段把两件事一起钉住：
// 未知值原样显示（正面），已知值仍然走文案映射（行为级负对照——不然"永远显示原值"也能过）。
// When the backend grows a value (draft, everyone ...) the select has to show *that value as it is*.
// Unknown values used to fall back to "Published" / "Members", leaving the user to judge by a state the
// resource does not have. This case pins both directions: an unknown value shows itself, while a known
// one still maps to its copy (the behavioural negative control - otherwise "always show the raw value"
// would pass too).
async function unknownStateValueCase() {
  const selected = (select) => select.options[select.selectedIndex].textContent;

  const unknown = await mount({ detail: { ...DETAIL, status: "draft", visibility: "everyone" } });
  try {
    await click(buttonWith("file.edit", fileRows()[0].querySelector(".files-row-actions")));
    const form = dialogForm();
    assert.ok(form, "the editor must be open");
    const [, visibilitySelect, statusSelect] = form.querySelectorAll("select");
    assert.equal(statusSelect.value, "draft", "the select must keep the server's value");
    assert.equal(selected(statusSelect), "draft",
      "an unknown status must be shown as its own value, never as a legal one");
    assert.equal(visibilitySelect.value, "everyone", "the select must keep the server's value");
    assert.equal(selected(visibilitySelect), "everyone",
      "an unknown visibility must be shown as its own value, never as a legal one");
  } finally {
    await React.act(async () => { unknown.unmount(); });
    reset();
  }

  const known = await mount({ detail: { ...DETAIL, status: "archived", visibility: "private" } });
  try {
    await click(buttonWith("file.edit", fileRows()[0].querySelector(".files-row-actions")));
    const form = dialogForm();
    const [, visibilitySelect, statusSelect] = form.querySelectorAll("select");
    assert.equal(selected(statusSelect), "file.statusArchived",
      "a known status must still be shown through its copy rather than raw");
    assert.equal(selected(visibilitySelect), "file.visibilityPrivate",
      "a known visibility must still be shown through its copy rather than raw");
  } finally {
    await React.act(async () => { known.unmount(); });
    reset();
  }
}

// ================================================================ 用例 G：保存失败必须可见
async function saveFailureCase() {
  // 成功那一边：不许出现 role="alert"。把失败提示做成"永远显示"（负对照的行为）这里就红了。
  // The success half: no role="alert" may appear. An unconditional failure banner turns this red.
  const ok = await mount();
  try {
    await click(buttonWith("file.edit", fileRows()[0].querySelector(".files-row-actions")));
    await submit(dialogForm());
    assert.equal(calls.update.length, 1, "the save must be sent");
    assert.equal(root().querySelector("[role=\"alert\"]"), null,
      "a successful save must not raise an alert");
  } finally {
    await React.act(async () => { ok.unmount(); });
    reset();
  }

  // 失败那一边：必须把服务端给出的具体原因显示出来。
  // The failure half: the server's own reason must be shown.
  const failed = await mount();
  try {
    updateFails = true;
    await click(buttonWith("file.edit", fileRows()[0].querySelector(".files-row-actions")));
    await submit(dialogForm());
    const alert = root().querySelector("[role=\"alert\"]");
    assert.ok(alert, "a failed save must show an alert");
    assert.match(alert.textContent, new RegExp(SERVER_REASON),
      "the server's own reason must be shown rather than a generic message");
  } finally {
    await React.act(async () => { failed.unmount(); });
    reset();
  }
}

// ================================================================ 用例 H：行内删除（两段式）
// 删除是本次补齐的动作（此前文件服务界面完全没有它），所以也要有防止误触的证据。
// Deletion is the action filled in by this change (the file service had none before), so the guard
// against a stray click needs evidence too.
async function deleteCase() {
  const container = await mount({ items: [ITEM, ITEM_TWO] });
  try {
    assert.equal(fileRows().length, 2, "both resources must be listed");

    // ① 武装第一步绝不能删除任何东西（这正是"点击即删"变异体的判别点）。
    await click(buttonWith("file.delete", fileRows()[0].querySelector(".files-row-actions")));
    assert.deepEqual(calls.del, [], "merely arming the delete must not delete anything");
    const armed = confirmBlock(fileRows()[0]);
    assert.ok(armed, "arming the delete must ask for a confirmation");
    assert.match(armed.textContent, /file\.deleteWarning\(title=高等数学提纲\)/,
      "the confirmation must name the resource it is about to delete");

    // ② 「取消」必须真的撤掉确认并什么都不删（覆盖缺口 F9）。
    //    把取消按钮删掉，下面这个 assert.ok 就会报"确认里必须有一条不删任何东西的退路"。
    //     Cancelling must drop the confirmation and delete nothing. Removing the cancel button makes
    //    the assert.ok below fail with that message.
    const cancel = buttonWith("common.cancel", armed);
    assert.ok(cancel, "the confirmation must offer a way out (cancel) that deletes nothing");
    await click(cancel);
    assert.deepEqual(calls.del, [], "cancelling must not delete anything");
    assert.equal(confirmBlock(fileRows()[0]), null, "cancelling must drop the confirmation");

    // ③ 跨行武装互斥：同一时刻只允许一行处于确认态。
    await click(buttonWith("file.delete", fileRows()[1].querySelector(".files-row-actions")));
    assert.ok(confirmBlock(fileRows()[1]), "the second row must now be armed");
    assert.equal(confirmBlock(fileRows()[0]), null, "arming another row must disarm the first");

    // ④ 行为级负对照：确认必须删掉**被武装的那一行**。第二行被武装时确认，
    //    只能删 43；若实现忽略"被武装的 id"、永远删第一行（负对照的行为），这里立刻变红。
    //     Behavioural negative control: confirming must delete *the armed row*. With the second row
    //     armed, only 43 may go; an implementation that ignores the armed id and always deletes the
    //     first row turns this red.
    await click(buttonWith("file.deleteConfirm", fileRows()[1]));
    assert.deepEqual(calls.del, [43], "confirming must delete exactly the armed resource");

    // ⑤ 删掉一行之后剩下的那一行仍然可删（列表已按服务端结果刷新）。
    assert.equal(fileRows().length, 1, "the deleted row must disappear from the list");
    await click(buttonWith("file.delete", fileRows()[0].querySelector(".files-row-actions")));
    await click(buttonWith("file.deleteConfirm", fileRows()[0]));
    assert.deepEqual(calls.del, [43, 42], "the remaining resource must still be deletable");
    assert.equal(fileRows().length, 0, "the list must end up empty");
  } finally {
    await React.act(async () => { container.unmount(); });
    reset();
  }
}

async function identitySwitchClosesSensitiveDialogsCase() {
  const accountB = { ...ADMIN, id: 8, username: "admin-b", displayName: "Admin B" };
  const accountC = { ...ADMIN, id: 9, username: "admin-c", displayName: "Admin C" };
  const privateItem = { ...ITEM, title: "账号 A 私有标题", visibility: "private" };
  const privateDetail = { ...DETAIL, title: privateItem.title, visibility: "private", descriptionMarkdown: "账号 A 私有简介" };
  const container = await mount({ items: [privateItem], detail: privateDetail });
  const rerender = async () => {
    await React.act(async () => {
      container.render(React.createElement(React.StrictMode, null, React.createElement(container.pageComponent)));
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
  };
  try {
    await click(buttonWith("file.edit", fileRows()[0].querySelector(".files-row-actions")));
    assert.equal(dialogForm().querySelector("input").value, "账号 A 私有标题");
    assert.equal(dialogForm().querySelector("textarea").value, "账号 A 私有简介");

    currentUser = accountB;
    serverItems = [{ ...ITEM, id: 43, title: "账号 B 普通资料", uploader: accountB }];
    serverDetail = { ...DETAIL, id: 43, title: "账号 B 普通资料", uploader: accountB, descriptionMarkdown: "" };
    await rerender();
    assert.ok(!dialogForm(), "switching accounts must close the previous account's private editor");
    assert.doesNotMatch(text(), /账号 A 私有标题|账号 A 私有简介/);
    assert.match(text(), /账号 B 普通资料/, "the ordinary identity-scoped list load must still refresh");

    await click(buttonWith("file.upload"));
    await click(buttonContaining("file.folderMoveTo", fileRows()[0]));
    assert.ok(root().querySelector("[data-upload-dialog]"), "the upload dialog must be open before switching");
    assert.ok(root().querySelector('[role="dialog"]'), "the move dialog must be open before switching");

    currentUser = accountC;
    serverItems = [];
    await rerender();
    assert.equal(root().querySelector("[data-upload-dialog]"), null, "switching accounts must close upload state");
    assert.equal(root().querySelector('[role="dialog"]'), null, "switching accounts must close move state");
  } finally {
    await React.act(async () => { container.unmount(); });
    reset();
  }
}

async function staleEditorResponseAfterIdentitySwitchCase() {
  const accountB = { ...ADMIN, id: 8, username: "admin-b", displayName: "Admin B" };
  const privateItem = { ...ITEM, title: "账号 A 延迟私有标题", visibility: "private" };
  const privateDetail = { ...DETAIL, title: privateItem.title, visibility: "private", descriptionMarkdown: "账号 A 延迟私有简介" };
  const accountBItem = { ...ITEM, id: 43, title: "账号 B 当前资料", uploader: accountB };
  const accountBDetail = { ...DETAIL, id: 43, title: accountBItem.title, uploader: accountB, descriptionMarkdown: "账号 B 当前简介" };
  const container = await mount({ items: [privateItem], detail: privateDetail });
  const pendingGets = new Map();
  const rerender = async () => {
    await React.act(async () => {
      container.render(React.createElement(React.StrictMode, null, React.createElement(container.pageComponent)));
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
  };
  try {
    getOverride = (id) => new Promise((resolve) => pendingGets.set(id, resolve));
    await click(buttonWith("file.edit", fileRows()[0].querySelector(".files-row-actions")));
    assert.deepEqual(calls.get, [42]);
    assert.ok(buttonWith("common.loading", fileRows()[0]), "account A's editor request must be pending");

    currentUser = accountB;
    serverItems = [accountBItem];
    serverDetail = accountBDetail;
    await rerender();
    await click(buttonWith("file.edit", fileRows()[0].querySelector(".files-row-actions")));
    assert.deepEqual(calls.get, [42, 43], "account B must be able to start a fresh editor request");
    assert.ok(buttonWith("common.loading", fileRows()[0]), "account B's editor request must be pending");

    await React.act(async () => {
      pendingGets.get(42)(privateDetail);
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
    assert.ok(!dialogForm(), "account A's late detail response must not reopen its private editor");
    assert.doesNotMatch(text(), /账号 A 延迟私有标题|账号 A 延迟私有简介/);
    assert.ok(buttonWith("common.loading", fileRows()[0]), "account A's finally must not clear account B's loading state");

    await React.act(async () => {
      pendingGets.get(43)(accountBDetail);
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
    assert.equal(dialogForm().querySelector("input").value, "账号 B 当前资料");
    assert.equal(dialogForm().querySelector("textarea").value, "账号 B 当前简介");
  } finally {
    await React.act(async () => { container.unmount(); });
    reset();
  }
}

// ================================================================ 用例 F：文件夹条在所有标签下都在
// 管理员原话："文件系统的文件夹功能完全不起作用"。排查结论里最可能的一条是"用户停在'我的收藏/
// 我的上传'标签上时，页面上一点文件夹的痕迹都没有"——条本身被 tab === "all" 判断挡住了。
// 这一段把"任何标签下都看得见文件夹线索、但管理动作仍只属于全部资料"钉成断言。
// The administrator's own words: "the file system's folder feature does not work at all". The most
// plausible finding was that on the "my saved" / "my uploads" tab there is no trace of folders,
// because the bar itself was blocked by the tab === "all" check. This case pins down "a folder cue is
// visible under every tab, while management actions still belong to All resources only".
const folderBar = () => root().querySelector(".files-folders");
const tabButton = (label) => [...root().querySelectorAll('[role="tab"]')].find((node) => node.textContent === label);
const selectedTab = () => [...root().querySelectorAll('[role="tab"]')].find((node) => node.getAttribute("aria-selected") === "true");

// 负对照一：把"个人标签下给条内容"的两处来源都拆掉——适用范围提示（在 files-page 里接线）与
// 常驻的"当前位置"行（在 file-folder-bar 里渲染）。只拆提示是不够的：位置行现在是无条件渲染的，
// 拆掉提示之后条里仍有内容，断言不会判红。因此这里同时把位置行退回"只在文件夹内出现"的旧行为。
// Negative control one: remove both sources of content under a personal tab - the scope hint (wired in
// files-page) and the permanent location row (rendered by file-folder-bar). Stripping only the hint is
// not enough: the location row now renders unconditionally, so the bar would still carry content and
// the assertion could not go red. The location row is therefore pushed back to the old "only inside a
// folder" behaviour as well.
const OLD_LOCATION_ROW_PATCH = {
  from: `      <nav className="files-folder-crumb files-folder-location" aria-label={t("file.folderBreadcrumb")}>`,
  to: `      {current ? (<nav className="files-folder-crumb files-folder-location" aria-label={t("file.folderBreadcrumb")}>`,
};
const OLD_LOCATION_ROW_CLOSE = {
  from: `      </nav>`,
  to: `      </nav>) : null}`,
};
const NO_SCOPE_HINT_PATCH = {
  from: `          scopeHint={tab === "all" ? undefined : t("file.folderScopeHint")}`,
  to: `          scopeHint={undefined}`,
};

// 负对照二：把"个人标签下点文件夹先切回全部"那一步去掉。
// Negative control two: stripping the "switch back to All before applying the folder" step.
const NO_TAB_SWITCH_PATCH = {
  from: `            if (tab !== "all") setTab("all");`,
  to: `            void tab;`,
};

async function folderBarAcrossTabsCase(patch, barPatch) {
  const container = await mount(patch ? { patch, barPatch } : {});
  try {
    assert.ok(folderBar(), "the folder bar must be mounted under the All tab");
    assert.ok(folderBar().querySelector(".files-folder-actions"),
      "management actions must render under the All tab for an administrator");

    const mineTab = tabButton("file.tabMine");
    assert.ok(mineTab, "the personal tab must be rendered for a signed-in administrator");
    await click(mineTab);
    assert.equal(selectedTab().textContent, "file.tabMine", "the personal tab must actually be selected");

    // 条必须还在，而且必须有内容 —— 空内容会被 .files-folders:empty 整块隐藏，
    // "看得见文件夹线索"就无从谈起。
    // The bar must still be there and must carry content: an empty one is hidden wholesale by
    // .files-folders:empty, and "a folder cue is visible" would be meaningless.
    assert.ok(folderBar(), "the folder bar must stay mounted under a personal tab");
    assert.ok(folderBar().textContent.trim().length > 0,
      "the folder bar must carry content under a personal tab, or :empty hides it entirely");
    assert.ok(folderBar().textContent.includes("file.folderScopeHint"),
      "the personal tab must explain that folder filtering belongs to All resources");
    assert.equal(folderBar().querySelector(".files-folder-actions"), null,
      "management actions must not render under a personal tab");

    // "去全部资料"必须真的把用户送回去，并在那里恢复管理动作。
    // "Go to All resources" must really take the user back, where the management actions reappear.
    const jump = buttonWith("file.folderScopeJump", folderBar());
    assert.ok(jump, "the personal tab must offer a way back to All resources");
    await click(jump);
    assert.equal(selectedTab().textContent, "file.tabAll", "the jump must land on the All tab");
    assert.ok(folderBar().querySelector(".files-folder-actions"),
      "management actions must come back once the All tab is selected");
  } finally {
    await React.act(async () => { container.unmount(); });
    reset();
  }
}

// 用例 G：在个人标签上点文件夹（面包屑），必须先切回"全部"，否则这次点击没有任何可见结果。
// Case G: clicking a folder (the breadcrumb) on a personal tab has to switch back to All first, or the
// click produces no visible result at all.
async function folderSelectionReturnsToAllCase(patch) {
  const container = await mount(patch ? { patch } : {});
  try {
    // 先在"全部"里进到某个文件夹，让面包屑出现。
    // Enter a folder under All first, so the breadcrumb exists.
    const enter = buttonWith("file.folderEnter", folderRows()[0]);
    assert.ok(enter, "the folder row must offer an enter action");
    await click(enter);
    assert.ok(folderBar().querySelector(".files-folder-crumb"), "entering a folder must show the breadcrumb");

    // 切到个人标签：条仍在，面包屑也在（这一条是"点文件夹"能被点到的前提）。
    // Switch to a personal tab: the bar and its breadcrumb survive, which is what makes the click
    // possible at all.
    await click(tabButton("file.tabMine"));
    assert.equal(selectedTab().textContent, "file.tabMine");
    const crumb = folderBar().querySelector(".files-folder-crumb");
    assert.ok(crumb, "the breadcrumb must survive the tab switch, or there is nothing to click");

    // 点位置条里的"根目录"：必须回到"全部"标签（而不只是悄悄改了文件夹状态）。
    // 位置条现在常驻，根目录是路径起点，取代了旧面包屑里那个"全部"入口。
    // Click "Root" in the location strip: the All tab must come back, not merely a silent state
    // change. The location strip is now permanent and the root is where the path starts, replacing the
    // old breadcrumb's "All" entry.
    const backToAll = buttonWith("file.folderRoot", crumb);
    assert.ok(backToAll, "the location strip must offer the root entry");
    await click(backToAll);
    assert.equal(selectedTab().textContent, "file.tabAll",
      "selecting a folder from a personal tab must land on the All tab");
    assert.equal(folderRows().length, FOLDERS.filter((folder) => folder.parentId === undefined).length,
      "landing on All must show the top-level folders again");
  } finally {
    await React.act(async () => { container.unmount(); });
    reset();
  }
}


(async () => {
  await folderRowCase();
  await folderRowSpansColumnsCase();
  await folderBarAcrossTabsCase();
  await folderSelectionReturnsToAllCase();

  // 负对照必须真的判红：去掉"个人标签下给条内容"后，那条"条里必须有内容"的断言就要失败。
  // The negative control must really go red: without the "give the bar content under a personal tab"
  // wiring, the "the bar must carry content" assertion has to fail.
  await assert.rejects(
    () => folderBarAcrossTabsCase(NO_SCOPE_HINT_PATCH, [OLD_LOCATION_ROW_PATCH, OLD_LOCATION_ROW_CLOSE]),
    /must carry content under a personal tab/,
    "negative control: an empty bar under a personal tab must fail the assertion",
  );
  // 负对照必须真的判红：去掉"先切回全部"后，点面包屑就不能把用户送回去。
  // The negative control must really go red: without the "switch back to All first" step, clicking the
  // breadcrumb cannot take the user back.
  await assert.rejects(
    () => folderSelectionReturnsToAllCase(NO_TAB_SWITCH_PATCH),
    /must land on the All tab/,
    "negative control: skipping the tab switch must fail the assertion",
  );
  await editEntryCase();
  await editPayloadCase("edited");
  await editPayloadCase("untouched");
  await editWriteBackCase();
  await staleDialogCase();
  await unknownStateValueCase();
  await saveFailureCase();
  await deleteCase();
  await identitySwitchClosesSensitiveDialogsCase();
  await staleEditorResponseAfterIdentitySwitchCase();
  console.log("file-ui-usability-smoke: ok");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
