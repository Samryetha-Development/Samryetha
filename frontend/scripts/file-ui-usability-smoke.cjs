// 文件服务界面可用性冒烟检查（Stage 4：文件夹行形态 + 行内编辑入口 + 可编辑属性）。
// Smoke check for file-service interface usability (stage 4: folder rows as a distinct shape, the
// in-row edit entry point, and the editable attributes).
//
// 无测试框架、无新依赖，沿用本仓库既有套路（jsdom + ts.transpileModule + 注入桩），
// 并且每个"必须成立"的用例都跑两遍：一遍跑真实源码（必须出现/必须发生），一遍跑"把这段实现
// 剥掉"的同源变体（必须不出现/不发生）。第二遍是防呆，避免出现"永远为真"的假绿断言。
// No test framework and no new dependencies: this follows the repository's existing pattern
// (jsdom + ts.transpileModule + injected stubs), and every "must hold" case runs twice, once against
// the real source (the thing must appear or must happen) and once against the same source with the
// implementation stripped (it must not). The second pass guards against vacuous assertions.
//
// 本脚本覆盖管理员 2026-10-13 点名的三件事：
// ① 文件夹在 GUI 里必须"看起来就是文件夹"（独立行形态、图标、类型标识、条目数、可进入、可返回）；
// ② 文件名与"分区属性"必须可改，且真的进得了保存载荷；
// ③ 编辑入口必须与"下载/收藏/删除"并列在行内操作区。
// The three things the administrator named on 2026-10-13:
// (1) a folder has to *look* like a folder in the GUI (its own row shape, an icon, a type label, an
// item count, a way in and a way back); (2) the file name and the "section" have to be editable and
// must actually reach the save payload; (3) the edit entry point has to sit beside download, save and
// delete in the row's action group.
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
const React = require("react");
const { createRoot } = require("react-dom/client");

// 插值桩把变量拼进文案，好让断言能验证插值确实到了界面上。
// The stub splices interpolated variables into the copy so an assertion can check that the value
// actually reached the interface.
const t = (key, vars) => (vars
  ? `${key}(${Object.entries(vars).map(([name, value]) => `${name}=${value}`).join(",")})`
  : key);

const ADMIN = { id: 7, username: "admin", handle: "admin", displayName: "Admin", role: "admin" };

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

const DETAIL = {
  ...ITEM,
  descriptionMarkdown: "覆盖极限、导数与应用题。",
  can: { update: true, delete: true },
};

const calls = { list: [], get: [], update: [], del: [] };
let updateFails = false;
let updatePayload = null;
const SERVER_REASON = "description is longer than the allowed limit";

class ApiErrorStub extends Error {
  constructor(status, payload) {
    super(payload?.message ?? "failed");
    this.status = status;
  }
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

// 编辑弹窗按用例替换（负对照要换掉它的实现），所以这里留一个可变的间接层。
// The editor dialog is swapped per case (a negative control replaces its implementation), hence the
// mutable indirection layer.
let dialogModule = null;

const inputs = {
  ...React,
  AppShell: ({ children }) => React.createElement("div", null, children),
  Loading: () => React.createElement("div", { "data-loading": true }, "LOADING"),
  // 账号固定为管理员：本次要验证的是"有权限的人能不能改得动"，权限本身有后端与既有用例把关。
  // The identity is pinned to an admin: what is verified here is that someone with the permission can
  // actually make the change, while the permission rule itself is covered by the backend and the
  // existing cases.
  useAuth: () => ({ user: ADMIN, loading: false }),
  useI18n: () => ({ t, locale: "en" }),
  timeAgo: () => "",
  formatBytes: () => "11 bytes",
  MarkdownText: () => null,
  // ui-commons 的 Dialog 做成透明壳：本脚本要看的是表单里的字段与载荷，不是弹层实现。
  // The ui-commons Dialog is a transparent shell here: what is under test is the form's fields and
  // its payload, not the overlay implementation.
  Dialog: ({ children, title }) => React.createElement("div", null, React.createElement("h2", null, title), children),
  FileUploadDialog: () => null,
  // 文件夹导航条做成透明桩：本脚本看的是列表自己长出来的文件夹行，不是导航条（它有自己的脚本）。
  // The folder bar is a transparent stub: this script looks at the folder rows the list itself grows,
  // not the bar, which has a smoke script of its own.
  FileFolderBar: () => null,
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
  api: {
    files: {
      config: async () => CONFIG,
      list: async (params = {}) => {
        calls.list.push(params);
        return { items: [ITEM], total: 1, page: 1, pageSize: 20, sort: "latest" };
      },
      favorites: async () => ({ items: [], total: 0 }),
      mine: async () => ({ items: [], total: 0 }),
      get: async (id) => { calls.get.push(id); return DETAIL; },
      download: async () => { throw new ApiErrorStub(500, { message: "not used" }); },
      setFavorite: async (id, on) => ({ resourceId: id, isFavorited: on, favoriteCount: 0 }),
      setFolder: async () => DETAIL,
      update: async (id, body) => {
        calls.update.push([id, body]);
        updatePayload = body;
        if (updateFails) throw new ApiErrorStub(422, { code: "TOO_LONG", message: SERVER_REASON });
        return { ...DETAIL, title: body.title ?? DETAIL.title };
      },
      del: async (id) => { calls.del.push(id); },
    },
  },
};

function transpile(file, patch) {
  // 统一换行符后再匹配补丁：仓库里的 .tsx 是 CRLF，而补丁锚点用 LF 书写。
  // Line endings are normalised before patching: the repository's .tsx files are CRLF while the patch
  // anchors are written with LF.
  let source = fs.readFileSync(path.join(__dirname, "../src", file), "utf8").replace(/\r\n/g, "\n").replace(/^import .*;\n/gm, "");
  if (patch) {
    assert.ok(source.includes(patch.from), `negative control anchor not found in ${file}: ${patch.from}`);
    source = source.replace(patch.from, patch.to);
  }
  return ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React } }).outputText;
}

function compile(file, patch) {
  const exported = {};
  new Function("exports", "React", ...Object.keys(inputs), transpile(file, patch))(exported, React, ...Object.values(inputs));
  return exported;
}

// 负对照：把"按当前层级取文件夹"整段剥掉，文件夹行就必须整个消失。
// Negative control: stripping the "folders at this level" call makes every folder row disappear.
const NO_FOLDER_ROWS_PATCH = {
  from: `  const levelFolders = tab === "all" ? foldersAtLevel(folders, folderView) : [];`,
  to: `  const levelFolders = [];`,
};

// 负对照：行内操作组里不渲染"编辑"按钮。
// Negative control: the action group renders no edit button.
const NO_EDIT_BUTTON_PATCH = {
  from: `                          {canFileResource(item) ? (
                            <button
                              type="button"
                              className="files-link"
                              disabled={editorLoadingId === item.id}
                              onClick={() => void openEditor(item)}
                            >
                              {editorLoadingId === item.id ? t("common.loading") : t("file.edit")}
                            </button>
                          ) : null}`,
  to: `                          {null}`,
};

// 负对照：保存载荷里回填原文件名，用户输入的名字就进不去后端。
// Negative control: the payload echoes the original title, so the typed name never reaches the
// backend.
const NO_TITLE_PATCH = {
  from: `        title: trimmedTitle,`,
  to: `        title: detail.title,`,
};

// 负对照：保存载荷里整段不发 categoryId，"分区"就永远改不动。
// Negative control: the payload drops categoryId entirely, so the section can never change.
const NO_CATEGORY_PATCH = {
  from: `        ...(config ? { categoryId: Number(categoryId) } : {}),`,
  to: `        /* categoryId withheld */`,
};

// 负对照：把"保存失败要显示出来"整段剥掉，失败就会静默。
// Negative control: stripping the "show the save failure" path makes a failure silent.
const SILENT_FAILURE_PATCH = {
  from: `      if (caught instanceof ApiError && caught.message) setError(caught.message);
      else setError(t("file.metaSaveFail"));`,
  to: `      void caught;`,
};

async function mountPage(pagePatch, dialogPatch) {
  dialogModule = compile("file-metadata-dialog.tsx", dialogPatch);
  const root = document.createElement("div");
  root.id = "root";
  document.body.appendChild(root);
  const Component = compile("files-page.tsx", pagePatch).FilesPage;
  const container = createRoot(root);
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
  updateFails = false;
  updatePayload = null;
  root()?.remove();
}

// ================================================================ 用例 A：文件夹行
// 这一段直接对应管理员的原话"文件夹在 GUI 里面显示的不像文件夹"。
// This block answers the administrator's own words: folders do not look like folders in the GUI.
async function folderRowCase(withNegativeControl) {
  const container = await mountPage(withNegativeControl ? NO_FOLDER_ROWS_PATCH : undefined);
  try {
    if (withNegativeControl) {
      assert.equal(folderRows().length, 0,
        "negative control: with the level lookup stripped there is no folder row at all");
      assert.doesNotMatch(text(), /file\.folderKind/,
        "negative control: the folder type label never reaches the interface either");
      return;
    }

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

    // ⑤ 可返回：进入之后必须有一条明确的"返回上一级"。
    const upRow = root().querySelector("tr.files-row-up");
    assert.ok(upRow, "inside a folder the list must offer an explicit step up");
    assert.match(upRow.textContent, /file\.folderUp/, "the step-up row must be labelled as such");
    await click(buttonContaining("file.folderUp", upRow));
    assert.equal(folderRows().length, 2, "stepping up must restore the parent level");
    assert.ok(calls.list.some((params) => params.folderId === undefined && params.unfiled === undefined),
      "stepping up to the root must drop the folder filter again");
  } finally {
    await React.act(async () => { container.unmount(); });
    reset();
  }
}

// ================================================================ 用例 B：行内编辑入口
// 管理员原话："编辑按钮也不在'删除''下载''收藏'那里"。
// The administrator's own words: the edit button is not where delete, download and save are.
async function editEntryCase(withNegativeControl) {
  const container = await mountPage(withNegativeControl ? NO_EDIT_BUTTON_PATCH : undefined);
  try {
    const row = fileRows()[0];
    assert.ok(row, "the resource row must be rendered");
    const group = row.querySelector(".files-row-actions");
    assert.ok(group, "the row must have one action group");
    const labels = [...group.querySelectorAll("button")].map((element) => element.textContent);

    if (withNegativeControl) {
      assert.ok(!labels.includes("file.edit"),
        "negative control: without the button in the source, no edit control is rendered");
      return;
    }

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
}

// ================================================================ 用例 C/D：文件名与分区进载荷
// 从列表行打开编辑器、改名、换分区、保存——载荷必须真的带上这两个值。
// Opening the editor from the row, renaming, changing the section and saving: both values have to
// reach the payload.
async function editPayloadCase(dialogPatch, expectation) {
  const container = await mountPage(undefined, dialogPatch);
  try {
    await click(buttonWith("file.edit", fileRows()[0].querySelector(".files-row-actions")));
    const form = dialogForm();
    assert.ok(form, "the editor must be open");
    const [titleInput] = form.querySelectorAll("input");
    const [categorySelect] = form.querySelectorAll("select");
    await setValue(titleInput, "  线性代数提纲  ");
    await setValue(categorySelect, "2", "select");
    await submit(form);

    if (expectation === "noTitle") {
      assert.equal(calls.update.length, 1, "the save must still be sent");
      assert.equal(updatePayload.title, "高等数学提纲",
        "negative control: echoing the stored title means the typed name never reaches the server");
      return;
    }
    if (expectation === "noCategory") {
      assert.equal(calls.update.length, 1, "the save must still be sent");
      assert.ok(!("categoryId" in updatePayload),
        "negative control: withholding categoryId means the section can never be changed");
      return;
    }

    assert.equal(calls.update.length, 1, "saving must send exactly one request");
    assert.equal(updatePayload.title, "线性代数提纲",
      "the typed file name must reach the payload, trimmed");
    assert.equal(updatePayload.categoryId, 2,
      "the chosen section (category) must reach the payload");
  } finally {
    await React.act(async () => { container.unmount(); });
    reset();
  }
}

// ================================================================ 用例 E：保存失败必须可见
async function saveFailureCase(withNegativeControl) {
  const container = await mountPage(undefined, withNegativeControl ? SILENT_FAILURE_PATCH : undefined);
  try {
    updateFails = true;
    await click(buttonWith("file.edit", fileRows()[0].querySelector(".files-row-actions")));
    const form = dialogForm();
    assert.ok(form, "the editor must be open");
    await submit(form);

    if (withNegativeControl) {
      assert.equal(root().querySelector("[role=\"alert\"]"), null,
        "negative control: a swallowed failure shows no alert at all");
      assert.doesNotMatch(text(), new RegExp(SERVER_REASON),
        "negative control: with the failure handling stripped the server reason never reaches the UI");
      return;
    }
    const alert = root().querySelector("[role=\"alert\"]");
    assert.ok(alert, "a failed save must show an alert");
    assert.match(alert.textContent, new RegExp(SERVER_REASON),
      "the server's own reason must be shown rather than a generic message");
  } finally {
    await React.act(async () => { container.unmount(); });
    reset();
  }
}

// ================================================================ 用例 F：行内删除（两段式）
// 删除是本次补齐的动作（此前文件服务界面完全没有它），所以也要有防止误触的证据。
// Deletion is the action filled in by this change (the file service had none before), so the guard
// against a stray click needs evidence too.
async function deleteCase() {
  const container = await mountPage();
  try {
    const group = fileRows()[0].querySelector(".files-row-actions");
    await click(buttonWith("file.delete", group));
    assert.deepEqual(calls.del, [], "merely arming the delete must not delete anything");
    assert.ok(fileRows()[0].querySelector(".files-row-confirm"),
      "arming the delete must ask for a confirmation");
    await click(buttonWith("file.deleteConfirm", fileRows()[0]));
    assert.deepEqual(calls.del, [42], "confirming must delete exactly the armed resource");
  } finally {
    await React.act(async () => { container.unmount(); });
    reset();
  }
}

(async () => {
  await folderRowCase(false);
  await folderRowCase(true);
  await editEntryCase(false);
  await editEntryCase(true);
  await editPayloadCase(undefined, "ok");
  await editPayloadCase(NO_TITLE_PATCH, "noTitle");
  await editPayloadCase(NO_CATEGORY_PATCH, "noCategory");
  await saveFailureCase(false);
  await saveFailureCase(true);
  await deleteCase();
  console.log("file-ui-usability-smoke: ok");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
