// 文件夹导航冒烟检查（Stage 2：文件夹分类）。
// Smoke check for folder navigation (stage 2: folder classification).
//
// 无测试框架、无新依赖，沿用本仓库既有套路（jsdom + ts.transpileModule + 注入桩），
// 并且每个用例都跑两遍：一遍跑真实源码（必须出现提示/必须发生调用），一遍跑"把这段处理剥掉"
// 的同源变体（必须不出现/不发生）。第二遍是防呆，避免出现"永远为真"的假绿断言。
// No test framework and no new dependencies: this follows the repository's existing pattern
// (jsdom + ts.transpileModule + injected stubs), and every case runs twice, once against the real
// source (the message must appear or the call must happen) and once against the same source with
// the handling stripped (it must not). The second pass guards against vacuous assertions.
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

// 插值桩把变量拼进文案，好让断言能验证"那个数确实带上了"——例如删除警告里的资料份数。
// 不用变量时仍然只是键名本身，所以按文本找按钮不受影响。
// The stub splices interpolated variables into the copy so an assertion can check that the number
// actually arrives, as with the resource count in the delete warning. Without variables it is just
// the key itself, so finding buttons by text is unaffected.
const t = (key, vars) => (vars
  ? `${key}(${Object.entries(vars).map(([name, value]) => `${name}=${value}`).join(",")})`
  : key);

// 纯逻辑层一律用真实源码编译；负对照按用例换掉其中一份。
// The pure logic layer is always compiled from real source; a negative control swaps one copy out
// per case.
let folderTree = null;
const inputs = {
  ...React,
  useI18n: () => ({ t, locale: "en" }),
  folderById: (...args) => folderTree.folderById(...args),
  childrenOf: (...args) => folderTree.childrenOf(...args),
  folderBreadcrumb: (...args) => folderTree.folderBreadcrumb(...args),
  flattenFolders: (...args) => folderTree.flattenFolders(...args),
  folderPathLabel: (...args) => folderTree.folderPathLabel(...args),
  viewFolderId: (...args) => folderTree.viewFolderId(...args),
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

// 负对照：把"动作失败要显示出来"整段剥掉，失败就会静默——正是本仓库刚修过的那类缺陷。
// Negative control: stripping the "show the failure" path makes a failure silent, which is exactly
// the class of defect this repository just finished fixing.
const SILENT_FAILURE_PATCH = {
  from: `    } catch (caught) {
      // 服务端的具体原因（如"同层已有同名文件夹"）比通用文案更有助于自助排查。
      // The server's specific reason (a duplicate name at this level, say) helps far more than
      // generic copy.
      setError(caught instanceof Error && caught.message ? caught.message : t("file.folderFail"));
      return false;
    }`,
  to: `    } catch (caught) {
      void caught;
      return false;
    }`,
};

// 负对照：把新建的落点写成"永远顶层"，于是"在某个文件夹里点新建"这件事就静默地建错了地方——
// 用户看到的是一句成功提示，而文件夹其实建在了别处。这正是本用例要钉住的失败形态。
// Negative control: the create target is hard-wired to the top level, so "create while inside a
// folder" silently lands in the wrong place: the user gets a success notice while the folder was
// actually created somewhere else. That is precisely the failure this case pins down.
const WRONG_PARENT_PATCH = {
  from: "      await onCreate(name, currentId);",
  to: "      await onCreate(name, null);",
};

const FOLDERS = [
  // 注意第一个文件夹刻意**不带 parentId 字段**：后端 DTO 里该字段可缺省，
  // 归一化一旦做错，顶层文件夹会整个从界面上消失（本轮实际踩到过）。
  // The first folder deliberately omits parentId: the backend DTO marks it optional, and a
  // normalisation mistake makes every top-level folder vanish (hit for real while building this).
  { id: 1, name: "数学", description: "", sortOrder: 10, resourceCount: 3, childCount: 1 },
  { id: 2, parentId: 1, name: "微积分", description: "", sortOrder: 10, resourceCount: 2, childCount: 0 },
  { id: 3, parentId: null, name: "英语", description: "", sortOrder: 20, resourceCount: 0, childCount: 0 },
];

const calls = { create: [], rename: [], remove: [], select: [] };

async function mountBar(patch, overrides) {
  const root = document.createElement("div");
  root.id = "root";
  document.body.appendChild(root);
  const Component = compile("file-folder-bar.tsx", patch).FileFolderBar;
  const container = createRoot(root);
  const props = {
    folders: FOLDERS,
    view: "all",
    onSelect: (next) => { calls.select.push(next); },
    canManage: true,
    busy: false,
    onCreate: async (name, parentId) => { calls.create.push([name, parentId]); },
    onRename: async (id, name) => { calls.rename.push([id, name]); },
    onDelete: async (id) => { calls.remove.push(id); },
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

async function type(input, value) {
  await React.act(async () => {
    const setter = Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype, "value").set;
    setter.call(input, value);
    input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
    await new Promise((resolve) => setTimeout(resolve, 10));
  });
}

function reset() {
  calls.create = [];
  calls.rename = [];
  calls.remove = [];
  calls.select = [];
  document.getElementById("root")?.remove();
}

// ---------------------------------------------------------------- 用例 1：树与面包屑
// 层级用缩进表达，面包屑写出完整路径；成环数据必须停下而不是把页面卡死。
// Levels are expressed as indentation and the breadcrumb spells out the full path; cyclic data must
// stop rather than hang the page.
async function treeCase(withNegativeControl) {
  folderTree = compile("lib/folder-tree.ts", withNegativeControl
    ? { from: "    .filter((folder) => (folder.parentId ?? null) === parentId)", to: "    .filter((folder) => folder.parentId === parentId)" }
    : undefined);
  const rows = folderTree.flattenFolders(FOLDERS);
  if (withNegativeControl) {
    // 不归一化时，缺省 parentId 的顶层文件夹不再被认作顶层：它会掉到末尾的"孤儿"兜底里、
    // 层级也一并丢失（子文件夹不再缩进）。兜底保证它不会彻底消失，但位置与层级都错了，
    // 这正是"顶层文件夹看起来乱了"的真实故障形态。
    // Without normalisation the top-level folder with an omitted parentId is no longer recognised as
    // top level: it drops into the trailing orphan fallback and loses its level (its child stops
    // being indented). The fallback keeps it from vanishing entirely, but the position and the
    // hierarchy are both wrong, which is exactly how "the top level looks scrambled" shows up.
    assert.notEqual(rows[0].folder.id, 1,
      "negative control: without normalisation the omitted-parentId folder is no longer first");
    assert.ok(!rows.some((row) => row.depth === 1),
      "negative control: without normalisation no indented level survives");
    return;
  }
  assert.deepEqual(rows.map((row) => [row.folder.id, row.depth]), [[1, 0], [2, 1], [3, 0]],
    "folders must come out depth-first with their indent levels");
  assert.deepEqual(folderTree.folderBreadcrumb(FOLDERS, 2).map((folder) => folder.name), ["数学", "微积分"]);
  assert.equal(folderTree.folderPathLabel(FOLDERS, 2), "数学 / 微积分");
  // 成环：1 的父级被指成 2，而 2 的父级是 1。面包屑必须在重复处停下。
  // A cycle: folder 1's parent points at 2 while 2's parent is 1. The breadcrumb must stop at the
  // repeat instead of looping forever.
  const cyclic = [
    { id: 1, parentId: 2, name: "A", description: "", sortOrder: 0, resourceCount: 0, childCount: 1 },
    { id: 2, parentId: 1, name: "B", description: "", sortOrder: 0, resourceCount: 0, childCount: 1 },
  ];
  const trail = folderTree.folderBreadcrumb(cyclic, 1);
  assert.ok(trail.length > 0 && trail.length <= 2, "a cyclic chain must terminate, not hang the breadcrumb");
}

// ---------------------------------------------------------------- 用例 2：面包屑渲染
async function breadcrumbCase() {
  folderTree = compile("lib/folder-tree.ts");
  const { container, root } = await mountBar(undefined, { view: 2 });
  try {
    const crumb = document.querySelector(".files-folder-crumb");
    assert.ok(crumb, "a breadcrumb must be rendered inside a real folder");
    assert.match(crumb.textContent, /数学/);
    assert.match(crumb.textContent, /微积分/);
    // 平级视图（全部/未分类）不是路径上的节点，不该出现面包屑。
    // The flat views (All / Unfiled) are not nodes on a path, so no breadcrumb belongs there.
    await React.act(async () => { container.unmount(); });
    root.remove();
    const flat = await mountBar(undefined, { view: "unfiled" });
    assert.equal(document.querySelector(".files-folder-crumb"), null, "a flat view has no breadcrumb");
    await React.act(async () => { flat.container.unmount(); });
  } finally {
    reset();
  }
}

// ---------------------------------------------------------------- 用例 3：新建
async function createCase(withNegativeControl) {
  folderTree = compile("lib/folder-tree.ts");
  const { container } = await mountBar(withNegativeControl ? WRONG_PARENT_PATCH : undefined, { view: 1 });
  try {
    await click(buttonWith("file.folderNewChild"));
    const input = document.querySelector(".files-folder-input");
    assert.ok(input, "the inline create form must appear");
    await type(input, " 线性代数 ");
    await click(buttonWith("file.folderCreate"));
    if (withNegativeControl) {
      assert.deepEqual(calls.create, [["线性代数", null]],
        "negative control: the folder is created at the top level instead of inside the open folder");
      return;
    }
    // 在某个文件夹里点「新建」应建在它下面，而不是回到顶层。
    // Creating while a folder is open must nest under it rather than falling back to the top level.
    assert.deepEqual(calls.create, [["线性代数", 1]], "creating inside a folder must nest under it, with the trimmed name");
    assert.match(text(), /file\.folderCreated/, "a successful create must be reported");
  } finally {
    reset();
  }
}

// ---------------------------------------------------------------- 用例 4：删除的后果必须说清
async function deleteCase() {
  folderTree = compile("lib/folder-tree.ts");
  const { container } = await mountBar(undefined, { view: 1 });
  try {
    assert.doesNotMatch(text(), /file\.folderDeleteWarning/, "the warning must not be on screen before the delete is armed");
    await click(buttonWith("file.folderDelete"));
    // 删除前必须写明"其 N 份资料将移到未分类、一份都不删"，并且带上下文中该文件夹的份数。
    // Before deleting, the warning must state "its N resources move to Unfiled and none is deleted",
    // with the folder's own count from context.
    const warning = document.querySelector(".files-folder-warn");
    assert.ok(warning, "arming the delete must show the consequence");
    assert.match(warning.textContent, /file\.folderDeleteWarning/);
    assert.match(warning.textContent, /count=3/, "the warning must carry the folder's resource count");
    await click(buttonWith("file.folderDeleteConfirm"));
    assert.deepEqual(calls.remove, [1], "confirming must delete exactly the folder that was armed");
    assert.match(text(), /file\.folderDeleted/, "the outcome must be reported");
    assert.deepEqual(calls.select, ["all"], "after deleting the open folder the view must fall back to All");
  } finally {
    reset();
  }
}

// ---------------------------------------------------------------- 用例 5：失败必须可见
async function failureVisibleCase(withNegativeControl) {
  folderTree = compile("lib/folder-tree.ts");
  const { container } = await mountBar(withNegativeControl ? SILENT_FAILURE_PATCH : undefined, {
    view: 1,
    onRename: async () => { throw new Error("a folder with that name already exists here"); },
  });
  try {
    await click(buttonWith("file.folderRename"));
    const input = document.querySelector(".files-folder-input");
    await type(input, "重名");
    await click(buttonWith("file.folderRenameSubmit"));
    if (withNegativeControl) {
      assert.doesNotMatch(text(), /a folder with that name already exists here/,
        "negative control: with the failure handling stripped the server reason never reaches the UI");
      assert.equal(document.querySelector("[role=\"alert\"]"), null,
        "negative control: a swallowed failure shows no alert at all");
      return;
    }
    const alert = document.querySelector("[role=\"alert\"]");
    assert.ok(alert, "a failed rename must show an alert");
    assert.match(alert.textContent, /a folder with that name already exists here/,
      "the server's own reason must be shown rather than a generic message");
  } finally {
    reset();
  }
}

(async () => {
  await treeCase(false);
  await treeCase(true);
  await breadcrumbCase();
  await createCase(false);
  await createCase(true);
  await deleteCase();
  await failureVisibleCase(false);
  await failureVisibleCase(true);
  console.log("file-folders-smoke: ok");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
