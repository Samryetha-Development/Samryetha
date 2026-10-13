// 拖拽上传冒烟检查（Stage 1：把文件拖进文件服务页面即上传）。
// Smoke check for drag-and-drop uploads (stage 1: dropping files onto the file-service page
// uploads them).
//
// 无测试框架、无新依赖，沿用本仓库既有套路（jsdom + ts.transpileModule + 注入 api 桩），
// 并且每个用例都跑两遍：一遍跑真实源码（必须出现提示），一遍跑"把这段处理剥掉"的同源变体
// （必须不出现提示）。第二遍是防呆，避免出现"永远为真"的假绿断言。
// No test framework and no new dependencies: this follows the repository's existing pattern
// (jsdom + ts.transpileModule + an injected api stub), and every case runs twice, once against the
// real source (the message must appear) and once against the same source with the handling stripped
// (the message must not appear). The second pass is the guard against vacuous assertions.
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
class ResizeObserverStub { observe() {} unobserve() {} disconnect() {} }
global.ResizeObserver = ResizeObserverStub;
dom.window.ResizeObserver = ResizeObserverStub;
const React = require("react");
const { createRoot } = require("react-dom/client");

const t = (key) => key;

// 拖拽入口对管理员可见（与上传按钮同一口径），所以这里的身份必须是 admin。
// The drag entry point is visible to administrators (the same condition as the upload button), so
// the identity here has to be an admin.
const ADMIN = { id: 1, username: "smoke", handle: "smoke", displayName: "Smoke", role: "admin", status: "active" };
const CONFIG = {
  categories: [{ id: 1, slug: "guide", name: "Guide", kind: "guide", description: "", sortOrder: 10, isSystem: true, resourceCount: 0 }],
  tagCloud: [],
  stats: { resourceCount: 0, categoryCount: 1 },
  allowedExtensions: [".txt", ".png"],
  maxUploadBytes: 1000,
  kinds: ["guide"],
  visibilities: ["public", "members", "private"],
  sorts: ["latest"],
  maxTags: 8,
};

// 服务端拒绝时的真实原因必须原样露到界面上，所以桩里带一句可辨识的文案。
// The server's actual reason must reach the interface verbatim, so the stub carries a
// recognisable message.
const SERVER_REASON = "extension not allowed by the server";
let createFails = false;
const calls = { presign: 0, bytes: 0, create: 0 };

class ApiErrorStub extends Error {
  constructor(status, payload) {
    super(payload?.message ?? "failed");
    this.status = status;
  }
}

let dropUploadModule = null;
const folderTreeModule = (() => {
  const source = fs.readFileSync(path.join(__dirname, "../src/lib/folder-tree.ts"), "utf8");
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React } }).outputText;
  const exported = {};
  new Function("exports", output)(exported);
  return exported;
})();
const inputs = {
  ...React,
  AppShell: ({ children }) => React.createElement("div", null, children),
  Loading: () => React.createElement("div", { "data-loading": true }, "LOADING"),
  useAuth: () => ({ user: ADMIN, loading: false }),
  useI18n: () => ({ t, locale: "en" }),
  timeAgo: () => "",
  formatBytes: () => "1 KB",
  FileUploadDialog: () => null,
  ApiError: ApiErrorStub,
  // 纯逻辑层是真实源码编译出来的，逐用例替换，好让负对照换掉它。
  // The pure logic layer comes from the real source and is swapped per case, so a negative control
  // can replace it.
  planDropEntries: (...args) => dropUploadModule.planDropEntries(...args),
  runDropUploads: (...args) => dropUploadModule.runDropUploads(...args),
  summarizeDrop: (...args) => dropUploadModule.summarizeDrop(...args),
  titleFromFilename: (...args) => dropUploadModule.titleFromFilename(...args),
  // 文件夹树是文件服务页面的另一个真实依赖（Stage 2 起页面渲染时会用到它）。
  // 这里注入真实的纯逻辑层、把文件夹导航条做成透明桩：本脚本测的是拖拽链路，
  // 文件夹导航有它自己的冒烟脚本。缺了这些注入页面会直接 ReferenceError（CI 上会红）。
  // The folder tree is another real dependency of the file-service page (used while rendering since
  // stage 2). The real pure logic layer is injected here and the folder bar is a transparent stub,
  // because this script covers the drag-and-drop path while folder navigation has a smoke script of
  // its own. Without these injections the page throws a ReferenceError outright and turns CI red.
  FileFolderBar: () => null,
  flattenFolders: (...args) => folderTreeModule.flattenFolders(...args),
  folderPathLabel: (...args) => folderTreeModule.folderPathLabel(...args),
  viewFolderId: (...args) => folderTreeModule.viewFolderId(...args),
  // 文件夹行与"返回上一级"两个纯函数（Stage 4 起页面渲染时调用）。
  // The two pure functions behind the folder rows and the step-up row (called while rendering since
  // stage 4).
  foldersAtLevel: (...args) => folderTreeModule.foldersAtLevel(...args),
  parentFolderView: (...args) => folderTreeModule.parentFolderView(...args),
  uploadFileBytes: async () => { calls.bytes += 1; },
  api: {
    files: {
      config: async () => CONFIG,
      list: async () => ({ items: [], total: 0, page: 1, pageSize: 20, sort: "latest" }),
      favorites: async () => ({ items: [], total: 0 }),
      mine: async () => ({ items: [], total: 0 }),
      presign: async (body) => {
        calls.presign += 1;
        return { objectKey: `obj/${body.filename}`, uploadUrl: "/upload", expires: "e", sig: "s", expiresAt: 0,
                 maxUploadBytes: CONFIG.maxUploadBytes, contentType: "text/plain" };
      },
      create: async (body) => {
        calls.create += 1;
        if (createFails && body.originalFilename === "broken.txt") {
          throw new ApiErrorStub(400, { code: "BAD_EXTENSION", message: SERVER_REASON });
        }
        return { id: 1, title: body.title };
      },
    },
  },
};

// 把源码里的 import 去掉（外部依赖一律用注入的桩），可选地应用一段补丁。
// Strips imports (external dependencies are injected stubs) and optionally applies a patch.
function compile(file, patch) {
  // 统一换行符后再匹配补丁：仓库里的 .tsx 是 CRLF，而补丁锚点用 LF 书写，
  // 不归一化的话锚点永远找不到（本脚本开发时正是踩了这一点）。
  // Line endings are normalised before patching: the repository's .tsx files are CRLF while the
  // patch anchors are written with LF, and without normalisation an anchor is never found, which is
  // exactly what this script hit while it was being written.
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

// 负对照：把批量上传里的失败处理整段剥掉，条目就会永久停在"上传中"，
// 界面上再也看不到任何失败原因——这正是"静默吞掉写失败"的形态。
// Negative control: stripping the failure handling out of the batch leaves the entry stuck on
// "uploading" forever and no reason ever reaches the interface, which is exactly the shape of a
// silently swallowed write failure.
const SWALLOW_PATCH = {
  from: `    } catch (caught) {
      current = current.map((entry, at) => (at === index
        ? { ...entry, status: "failed" as DropEntryStatus, failure: "upload" as DropEntryFailure, detail: detailOf(caught) }
        : entry));
    }`,
  to: `    } catch (caught) {
      void caught;
    }`,
};

const SIZE_PATCH = {
  from: "    if (file.size > limits.maxUploadBytes) {",
  to: "    if (false) {",
};

// 负对照：撤掉非管理员的权限闸门，于是普通用户拖入也会一路走到上传链路，
// 界面上既没有无权限提示、也真的发出了请求。
// Negative control: removing the non-admin gate lets a plain user's drop run all the way into the
// upload chain, so the interface shows no permission message and the request is really sent.
const GUARD_PATCH = {
  from: `    if (!canUpload) {
      setActionError(t("file.dropNoPermission"));
      return;
    }`,
  to: "    if (false) { setActionError(t(\"file.dropNoPermission\")); return; }",
};

const dropFile = (name, size) => ({ name, size, type: "text/plain" });

function fireDrop(element, files) {
  const event = new dom.window.Event("drop", { bubbles: true, cancelable: true });
  Object.defineProperty(event, "dataTransfer", { value: { types: ["Files"], files, dropEffect: "none" } });
  element.dispatchEvent(event);
}

async function mountFilesPage(patch) {
  const root = document.createElement("div");
  root.id = "root";
  document.body.appendChild(root);
  const FilesPage = compile("files-page.tsx", patch).FilesPage;
  const container = createRoot(root);
  await React.act(async () => {
    container.render(React.createElement(React.StrictMode, null, React.createElement(FilesPage, {})));
  });
  await React.act(async () => { await new Promise((resolve) => setTimeout(resolve, 25)); });
  return { container, element: root };
}

async function dropOnto(element, files) {
  const main = element.querySelector("main.files-layout");
  assert.ok(main, "the file-service page must render its layout container");
  await React.act(async () => {
    fireDrop(main, files);
    await new Promise((resolve) => setTimeout(resolve, 50));
  });
  return element.textContent;
}

// ---------------------------------------------------------------- 用例 1：本地校验
// 体积与格式的拒绝必须在字节被送出去之前发生，且逐条给出原因。
// A size or format rejection has to happen before any bytes leave, and each one has to carry its
// reason.
async function validationCase(withNegativeControl) {
  dropUploadModule = compile("lib/drop-upload.ts", withNegativeControl ? SIZE_PATCH : undefined);
  const entries = dropUploadModule.planDropEntries(
    [dropFile("ok.txt", 10), dropFile("tool.exe", 10), dropFile("big.png", 5000)],
    { allowedExtensions: [".txt", ".png"], maxUploadBytes: 1000 },
    0,
  );
  assert.equal(entries.length, 3, "every dropped file must get its own entry");
  assert.equal(entries[0].status, "pending", "an allowed file must start pending");
  assert.equal(entries[1].failure, "badExtension", "a disallowed extension must be rejected by name");
  if (withNegativeControl) {
    assert.notEqual(entries[2].failure, "fileTooLarge",
      "negative control: without the size guard the oversized file is no longer rejected");
    return;
  }
  assert.equal(entries[2].failure, "fileTooLarge", "an oversized file must be rejected");
  assert.equal(entries[2].limitBytes, 1000, "the rejection must carry the limit for the message");
  assert.equal(calls.presign, 0, "local validation must not have touched the network yet");
}

// ---------------------------------------------------------------- 用例 2：失败可见
// 拖入三类文件：一份成功、一份被服务端拒绝、一份本地就因格式被拒。
// 成功与两类失败都必须出现在同一份可见清单里。
// Three kinds of file are dropped: one succeeds, one the server refuses and one rejected locally
// for its format. The success and both failures must all appear in the same visible list.
async function failureVisibleCase(withNegativeControl) {
  createFails = true;
  dropUploadModule = compile("lib/drop-upload.ts", withNegativeControl ? SWALLOW_PATCH : undefined);
  const { container, element } = await mountFilesPage();
  try {
    const text = await dropOnto(element, [dropFile("good.txt", 10), dropFile("broken.txt", 10), dropFile("tool.exe", 10)]);
    assert.match(text, /good\.txt/, "every dropped file must be listed by name in the result list");
    assert.match(text, /tool\.exe/, "a locally rejected file must be listed by name too");
    if (withNegativeControl) {
      assert.doesNotMatch(text, /extension not allowed by the server/,
        "negative control: with the failure handling stripped the server reason never reaches the UI");
      assert.match(text, /file\.dropStatusUploading/,
        "negative control: the swallowed failure leaves the file stuck on the uploading label forever");
      return;
    }
    assert.match(text, /file\.dropSummary/, "a settled batch must report its totals");
    assert.match(text, /file\.dropStatusSuccess/, "a successful file must be reported as uploaded");
    assert.match(text, /extension not allowed by the server/,
      "a server refusal must surface the server's own reason");
    assert.match(text, /file\.badExtension/, "a locally rejected format must be reported by name");
    assert.doesNotMatch(text, /file\.dropStatusUploading/, "nothing may be left mid-upload");
    assert.equal(calls.presign, 2, "only the two locally valid files may be presigned");
    assert.equal(calls.bytes, 2, "both valid files must have had their bytes sent");
  } finally {
    await React.act(async () => { container.unmount(); });
    element.remove();
    createFails = false;
    calls.presign = 0;
    calls.bytes = 0;
    calls.create = 0;
  }
}

// ---------------------------------------------------------------- 用例 3：非管理员
// 普通用户拖入必须得到明确的无权限提示，而不是静默无反应。
// A plain user's drop must be answered with an explicit permission message rather than silence.
async function permissionCase(withNegativeControl) {
  const student = { ...ADMIN, role: "student" };
  const saved = inputs.useAuth;
  inputs.useAuth = () => ({ user: student, loading: false });
  dropUploadModule = compile("lib/drop-upload.ts");
  const { container, element } = await mountFilesPage(withNegativeControl ? GUARD_PATCH : undefined);
  try {
    const text = await dropOnto(element, [dropFile("good.txt", 10)]);
    if (withNegativeControl) {
      assert.doesNotMatch(text, /file\.dropNoPermission/,
        "negative control: without the gate a non-admin drop is never told about the missing permission");
      assert.ok(calls.presign > 0,
        "negative control: without the gate the non-admin drop really reaches the upload chain");
      return;
    }
    assert.match(text, /file\.dropNoPermission/, "a non-admin drop must be told it lacks permission");
    assert.equal(calls.presign, 0, "a non-admin drop must never reach the upload chain");
    assert.doesNotMatch(text, /file\.dropStatusSuccess/, "a non-admin drop must never look successful");
  } finally {
    await React.act(async () => { container.unmount(); });
    element.remove();
    inputs.useAuth = saved;
    calls.presign = 0;
    calls.bytes = 0;
    calls.create = 0;
  }
}

(async () => {
  await validationCase(false);
  await validationCase(true);
  await failureVisibleCase(false);
  await failureVisibleCase(true);
  await permissionCase(false);
  await permissionCase(true);
  console.log("file-dragdrop-smoke: ok");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
