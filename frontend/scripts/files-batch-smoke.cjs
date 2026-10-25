// 批量操作工具条冒烟检查（管理员/普通用户分级 + 失败可见）。
// Smoke check for the batch toolbar: the admin/ordinary-user split plus visible failures.
//
// 无测试框架、无新依赖，沿用本仓库既有套路（jsdom + ts.transpileModule + 注入桩），
// 并且关键用例都跑两遍：一遍真实源码（按钮必须存在/必须不出现），一遍把负责那一半的代码
// 剥掉的变体（结果必须反过来）。第二遍是防呆，避免出现"永远为真"的假绿断言。
// No test framework and no new dependencies: this follows the repository's existing pattern
// (jsdom + ts.transpileModule + injected stubs), and the key cases run twice: once against the real
// source, once against a variant with that half of the logic stripped, where the outcome must flip.
// The second pass guards against vacuous assertions.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
const { JSDOM } = require("jsdom");
const dom = new JSDOM('<div id="root"></div>', { url: "http://localhost/files" });
global.window = dom.window;
global.document = dom.window.document;
// 把 JSDOM 自带的那个空 #root 摘掉：否则每个用例新挂的 #root 都变成第二个同 id 元素，
// 而 getElementById 只会返回先出现的那个空壳，所有文本断言都会读到空字符串。
// The empty #root JSDOM ships with is detached first: otherwise each case's fresh #root becomes a
// second element with the same id, getElementById keeps returning the earlier empty shell, and
// every text assertion reads an empty string.
document.getElementById("root").remove();
Object.defineProperty(global, "navigator", { configurable: true, value: dom.window.navigator });
global.IS_REACT_ACT_ENVIRONMENT = true;
class ResizeObserverStub { observe() {} unobserve() {} disconnect() {} }
global.ResizeObserver = ResizeObserverStub;
dom.window.ResizeObserver = ResizeObserverStub;
const React = require("react");
const { createRoot } = require("react-dom/client");

// 插值桩把变量拼进文案，好让断言能验证"那个数确实带上了"——例如删除警告里的选中条数。
// The stub splices interpolated variables into the copy so an assertion can check that the number
// actually arrives, as with the selected count in the delete warning.
const t = (key, vars) => (vars
  ? `${key}(${Object.entries(vars).map(([name, value]) => `${name}=${value}`).join(",")})`
  : key);

// 纯逻辑层一律用真实源码编译；负对照按用例换掉其中一份。
// The pure logic layer is always compiled from real source; a negative control swaps one copy out
// per case.
let folderTree = null;
const inputs = {
  ...require("./ui-smoke-inputs.cjs"),
  ...React,
  useI18n: () => ({ t, locale: "en" }),
  flattenFolders: (...args) => folderTree.flattenFolders(...args),
  folderPathLabel: (...args) => folderTree.folderPathLabel(...args),
};

function compile(file, patch) {
  // 统一换行符后再匹配补丁：仓库里的 .tsx 是 CRLF，而补丁锚点用 LF 书写。
  // Line endings are normalised before patching: the repository's .tsx files are CRLF while the
  // patch anchors are written with LF.
  let source = fs.readFileSync(path.join(__dirname, "../src", file), "utf8").replace(/\r\n/g, "\n").replace(/^import .*;\n/gm, "");
  if (patch) {
    assert.ok(source.includes(patch.from), `negative control anchor not found in ${file}: ${patch.from}`);
    source = source.replace(patch.from, patch.to);
  }
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React } }).outputText;
  const exported = {};
  new Function("exports", "React", ...Object.keys(inputs), output)(exported, React, ...Object.values(inputs));
  return exported;
}

// 负对照：把"失败要显示出来"整段剥掉，失败就会静默——正是本仓库反复修过的那类缺陷。
// Negative control: stripping the "show the failure" path makes a failure silent, which is exactly
// the class of defect this repository has repeatedly had to fix.
const SILENT_FAILURE_PATCH = {
  from: `      setFormError(caught instanceof Error && caught.message ? caught.message : t("file.batchFail"));`,
  to: `      void caught;`,
};

// 负对照：把管理类动作的渲染条件去掉（恒为真），于是普通用户也会看到管理按钮——
// 这正是本需求要钉死的失败形态。
// Negative control: the guard around the management actions is removed (always true), so ordinary
// users see management buttons too, which is exactly the failure this requirement pins down.
const ALWAYS_MANAGE_PATCH = {
  from: "        {canManage ? (",
  to: "        {true ? (",
};

const FOLDERS = [
  { id: 1, name: "数学", description: "", sortOrder: 10, resourceCount: 3, childCount: 1 },
  { id: 2, parentId: 1, name: "微积分", description: "", sortOrder: 10, resourceCount: 2, childCount: 0 },
];

const CATEGORIES = [{ id: 5, name: "其他资料" }];

const calls = { download: 0, move: [], copy: [], rename: [], edit: [], remove: [] };

async function mountBar(patch, overrides) {
  const root = document.createElement("div");
  root.id = "root";
  document.body.appendChild(root);
  const Component = compile("file-batch-bar.tsx", patch).FileBatchBar;
  const container = createRoot(root);
  const props = {
    count: 2,
    canManage: false,
    folders: FOLDERS,
    categories: CATEGORIES,
    busy: false,
    onClear: () => {},
    onDownload: async () => { calls.download += 1; },
    onMove: async (folderId) => { calls.move.push(folderId); return { requested: 2, succeeded: 2, reasons: [] }; },
    onCopy: async (folderId) => { calls.copy.push(folderId); return { requested: 2, succeeded: 2, reasons: [] }; },
    onRename: async (input) => { calls.rename.push(input); return { requested: 2, succeeded: 2, reasons: [] }; },
    onEdit: async (input) => { calls.edit.push(input); return { requested: 2, succeeded: 2, reasons: [] }; },
    onDelete: async () => { calls.remove.push(1); return { requested: 2, succeeded: 2, reasons: [] }; },
    ...overrides,
  };
  await React.act(async () => {
    container.render(React.createElement(React.StrictMode, null, React.createElement(Component, props)));
  });
  await React.act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
  return { container, root };
}

const buttons = () => [...document.querySelectorAll("button")];
const buttonWith = (label) => buttons().find((element) => element.textContent === label);
const text = () => document.getElementById("root").textContent;

async function click(element) {
  assert.ok(element, "expected the target button to be rendered");
  await React.act(async () => {
    element.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
}

function reset() {
  calls.download = 0;
  calls.move = [];
  calls.copy = [];
  calls.rename = [];
  calls.edit = [];
  calls.remove = [];
  document.getElementById("root")?.remove();
}

function prepare() {
  folderTree = compile("lib/folder-tree.ts");
}

// ---------------------------------------------------------------- 用例 1：普通用户只能批量下载
// 这是本需求的核心：普通用户看得见「批量下载」，看不见任何管理类动作。
// The core of the requirement: ordinary users see "Download selected" and no management action.
async function plainUserCase(withNegativeControl) {
  prepare();
  reset();
  await mountBar(withNegativeControl ? ALWAYS_MANAGE_PATCH : null, { canManage: false });
  assert.ok(buttonWith("file.batchDownload"), "ordinary users must see batch download");
  for (const label of ["file.batchMove", "file.batchCopy", "file.batchRename", "file.batchEdit", "file.batchDelete"]) {
    const visible = Boolean(buttonWith(label));
    if (withNegativeControl) {
      assert.equal(visible, true, `negative control should have leaked ${label}`);
    } else {
      assert.equal(visible, false, `ordinary users must not see ${label}`);
    }
  }
  // 批量下载对普通用户必须真的能触发。
  // Batch download has to actually fire for an ordinary user.
  await click(buttonWith("file.batchDownload"));
  assert.equal(calls.download, 1);
  // 普通用户的任何一次点击都不该落到管理类动作上。
  // No click by an ordinary user may reach a management action.
  assert.deepEqual([calls.move, calls.copy, calls.rename, calls.edit, calls.remove], [[], [], [], [], []]);
}

// ---------------------------------------------------------------- 用例 2：管理员看到全部动作
async function adminCase() {
  prepare();
  reset();
  await mountBar(null, { canManage: true });
  for (const label of ["file.batchDownload", "file.batchMove", "file.batchCopy", "file.batchRename", "file.batchEdit", "file.batchDelete"]) {
    assert.ok(buttonWith(label), `administrators must see ${label}`);
  }
}

// ---------------------------------------------------------------- 用例 3：批量删除要点两次且写明条数
// 删除不可逆，单次点击即删会让误触毁掉一整批资料。
// Deletion is irreversible, and a single click would let a stray click destroy a whole batch.
async function deleteConfirmCase() {
  prepare();
  reset();
  await mountBar(null, { canManage: true });
  await click(buttonWith("file.batchDelete"));
  assert.equal(calls.remove.length, 0, "clicking Delete must not delete before confirmation");
  const warning = text();
  assert.ok(warning.includes("count=2"), `the confirmation must state how many are deleted: ${warning}`);
  await click(buttonWith("file.batchDeleteConfirm"));
  assert.equal(calls.remove.length, 1, "confirming must run the delete exactly once");
}

// ---------------------------------------------------------------- 用例 4：失败必须可见
// 服务端原因（例如普通用户硬调管理端点拿到的 FORBIDDEN）必须显示出来，不能静默失败。
// The server's reason (the FORBIDDEN an ordinary user gets by calling a management endpoint
// directly, say) has to be shown; a silent failure is not acceptable.
async function failureVisibleCase(withNegativeControl) {
  prepare();
  reset();
  await mountBar(withNegativeControl ? SILENT_FAILURE_PATCH : null, {
    canManage: true,
    onDelete: async () => { throw new Error("Only administrators may batch manage resources."); },
  });
  await click(buttonWith("file.batchDelete"));
  await click(buttonWith("file.batchDeleteConfirm"));
  const alert = document.querySelector('[role="alert"]');
  if (withNegativeControl) {
    assert.equal(alert, null, "negative control should have swallowed the failure");
  } else {
    assert.ok(alert, "a failed batch action must be visible");
    assert.ok(alert.textContent.includes("Only administrators"), `the server reason must survive: ${alert.textContent}`);
  }
}

// ---------------------------------------------------------------- 用例 5：空选择不渲染工具条
// 一个"0 项已选"的条带只会占掉列表的高度。
// A "0 selected" strip would only eat the vertical space the list needs.
async function emptySelectionCase() {
  prepare();
  reset();
  await mountBar(null, { count: 0, canManage: true });
  assert.equal(document.querySelector("#root section.files-batch"), null, "an empty selection must render nothing");
}

// ---------------------------------------------------------------- 用例 6：批量下载成功后的文案
async function downloadNoticeCase() {
  prepare();
  reset();
  await mountBar(null, { canManage: false });
  await click(buttonWith("file.batchDownload"));
  assert.equal(calls.download, 1);
}

async function run() {
  await plainUserCase(false);
  await plainUserCase(true);
  await adminCase();
  await deleteConfirmCase();
  await failureVisibleCase(false);
  await failureVisibleCase(true);
  await emptySelectionCase();
  await downloadNoticeCase();
  document.getElementById("root")?.remove();
  console.log("files-batch-smoke: ok");
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
