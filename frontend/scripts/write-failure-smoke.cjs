// 写操作失败反馈冒烟检查 —— 无测试框架、无新依赖（jsdom + ts.transpileModule）。
// 守住一条约定：用户可见的写操作失败必须给出反馈，绝不能看起来"操作成功"。
//
// 每个用例都跑两遍：一遍跑真实源码（必须出现提示），一遍跑"把失败处理去掉"的同源变体
// （必须不出现提示）。第二遍是防呆：若断言其实是空的，它会在这一步暴露出来，
// 避免出现"永远为真"的假绿用例。
//
// Smoke check for write-failure feedback: a user-visible write that fails must report the
// failure instead of looking like it succeeded. Every case runs twice: once against the real
// source (the message must appear) and once against the same source with the failure handling
// stripped again (the message must not appear). The second pass is the guard against vacuous
// assertions, so a case can never pass by accident.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
const { JSDOM } = require("jsdom");
const dom = new JSDOM('<div id="root"></div>', { url: "http://localhost/d/1?notif=7" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator", { configurable: true, value: dom.window.navigator });
global.IS_REACT_ACT_ENVIRONMENT = true;
// jsdom 没有 ResizeObserver / matchMedia，而 thread-page 会用到；补最小桩，否则渲染直接抛错。
// jsdom ships neither ResizeObserver nor matchMedia, both of which thread-page uses; minimal stubs
// keep the render from throwing.
class ResizeObserverStub { observe() {} unobserve() {} disconnect() {} }
global.ResizeObserver = ResizeObserverStub;
dom.window.ResizeObserver = ResizeObserverStub;
dom.window.matchMedia = dom.window.matchMedia ?? (() => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
const React = require("react");
const { createRoot } = require("react-dom/client");

const t = (key) => key;
const USER = { id: 1, username: "smoke", handle: "smoke", displayName: "Smoke", status: "active" };
const NOTIFICATION = { id: 5, type: "reply", body: "smoke notification", isRead: false, createdAt: 0, actor: null, discussionId: null };
const BOARD = { id: 1, slug: "general", name: "General" };
const DRAFT = {
  title: "", bodyMarkdown: "", bodyFormat: "text", boardSlug: "general", poll: null,
  attachments: [{ id: 11, originalFilename: "notes.txt", isImage: false, downloadUrl: "" }],
};
const THREAD = {
  id: 1, title: "Smoke thread", bodyHtml: "<p>body</p>", bodyMarkdown: "body", bodyFormat: "text",
  createdAt: 0, board: { slug: "general", name: "General" },
  author: { id: 1, username: "smoke", handle: "smoke", displayName: "Smoke" },
  attachments: [], replies: [], poll: null, isLocked: false, isPinned: false, isSaved: false,
  isFollowing: false, saveCount: 0,
  can: { update: false, delete: false, reply: false, moderate: false, save: false, follow: false },
};
const pass = (key) => { throw new Error(`@lako/ui stub must not render in this smoke: ${key}`); };
let markReadFails = false;
let markAllReadFails = false;
let attachmentDelFails = false;
const calls = { markRead: 0, markAllRead: 0, list: 0, del: 0, notifications: 0 };

const inputs = {
  ...React,
  AppShell: ({ children }) => React.createElement("div", null, children),
  ApiError: class extends Error {},
  useAuth: () => ({ user: USER, loading: false }),
  useI18n: () => ({ t, locale: "en" }),
  timeAgo: () => "",
  formatBytes: () => "1 KB",
  // 只用到 post-page.tsx 的外层容器，内部标记/编辑器对本用例无意义，用透明桩。
  // post-page.tsx only needs these as containers here; their innards are irrelevant to this case.
  EditorField: ({ children }) => React.createElement("div", null, children),
  PollFields: () => null,
  PollToggle: () => null,
  SDropdown: () => null,
  pollError: () => null,
  // thread-page.tsx 的其余外部依赖同样只做透明桩。
  // The remaining thread-page.tsx dependencies are transparent stubs too.
  ConfirmDialog: () => null,
  Loading: () => React.createElement("div", { "data-loading": true }, "LOADING"),
  useAuthModal: () => ({ open: () => {} }),
  reducedMotion: () => false,
  MathText: ({ children }) => React.createElement("span", null, children),
  RichBody: ({ html }) => React.createElement("div", { dangerouslySetInnerHTML: { __html: html ?? "" } }),
  useIsomorphicLayoutEffect: React.useLayoutEffect,
  ThreadIcon: () => null,
  AttachmentList: () => null,
  AttachmentPromoteDialog: () => null,
  PollCard: () => null,
  ReportButton: () => null,
  api: {
    messages: { conversations: async () => ({ items: [] }) },
    notifications: {
      list: async () => { calls.list++; return { items: [NOTIFICATION] }; },
      markRead: async () => { calls.markRead++; if (markReadFails) throw new Error("mark read failed"); },
      markAllRead: async () => { calls.markAllRead++; if (markAllReadFails) throw new Error("mark all read failed"); },
    },
    boards: { list: async () => ({ items: [BOARD] }) },
    drafts: { get: async () => DRAFT },
    discussions: {
      get: async () => THREAD,
      // load() 同时取正文与回复；缺任一个都会走进 notFound 分支，让本用例断言失效。
      // load() fetches the thread and its replies together; missing either drops into the
      // notFound branch and silently voids this case's assertions.
      replies: async () => ({ items: [] }),
      list: async () => ({ items: [], total: 0 }),
    },
    attachments: {
      config: async () => ({ allowedExtensions: [".txt", ".png"], maxUploadBytes: 1_000_000 }),
      del: async () => { calls.del++; if (attachmentDelFails) throw new Error("attachment delete failed"); },
    },
  },
};

// 把源码里的 import 去掉，改用注入的桩；并把补丁字符串应用到源码上。
// Strip imports (stubs are injected) and apply the optional patch string to the source.
function compile(file, patch) {
  let source = fs.readFileSync(path.join(__dirname, "../src", file), "utf8").replace(/^import .*;\r?\n/gm, "");
  if (patch) {
    assert.ok(source.includes(patch.from), `negative control anchor not found in ${file}: ${patch.from}`);
    source = source.replace(patch.from, patch.to);
  }
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React } }).outputText;
  const exported = {};
  new Function("exports", "React", ...Object.keys(inputs), output)(exported, React, ...Object.values(inputs));
  return exported;
}

const buttonWith = (label) => [...document.querySelectorAll("button")].find((el) => el.textContent === label);
const buttonContaining = (label) => [...document.querySelectorAll("button")].find((el) => el.textContent.includes(label));
const text = () => document.getElementById("root").textContent;

const COMPONENTS = { "inbox-page.tsx": "InboxPage", "post-page.tsx": "PostPage", "thread-page.tsx": "ThreadPage" };

async function mount(file, props, patch) {
  const Component = compile(file, patch)[COMPONENTS[file]];
  const root = createRoot(document.getElementById("root"));
  await React.act(async () => {
    root.render(React.createElement(React.StrictMode, null, React.createElement(Component, props)));
  });
  await React.act(async () => { await new Promise((resolve) => setTimeout(resolve, 25)); });
  return root;
}

async function click(element) {
  assert.ok(element, "expected the target element to be rendered");
  await React.act(async () => { element.click(); });
  await React.act(async () => { await new Promise((resolve) => setTimeout(resolve, 25)); });
}

// 每次挂载都清零计数，否则上一遍（真实源码/负对照）的调用会污染下一遍的断言。
// Reset the counters on every mount, otherwise the previous pass pollutes the next one.
function resetCalls() {
  calls.markRead = 0;
  calls.markAllRead = 0;
  calls.list = 0;
  calls.del = 0;
}

// 用例一：单条通知标记已读失败必须可见（此前是 .catch(() => undefined)，界面毫无反应）。
// Case 1: failing to mark one notification read must be visible (it used to be .catch(() => undefined)).
async function notificationMarkReadFailure(patched) {
  resetCalls();
  markReadFails = true;
  const root = await mount("inbox-page.tsx", null, patched);
  try {
    await click(buttonWith("inbox.notifications"));
    assert.equal(calls.markRead, 0, "mark read must not fire before the user clicks");
    await click(buttonContaining("smoke notification"));
    assert.ok(calls.markRead > 0, "clicking a notification must attempt the mark-read write");
    if (patched) {
      assert.doesNotMatch(text(), /inbox\.markReadFail/, "negative control: the stripped source must stay silent");
    } else {
      assert.match(text(), /inbox\.markReadFail/, "a failed mark-read must surface inbox.markReadFail");
    }
    console.log(`ok   inbox notification mark-read failure ${patched ? "(negative control: stays silent)" : "(surfaces the failure)"}`);
  } finally {
    await React.act(async () => { root.unmount(); });
    markReadFails = false;
  }
}

// 用例二：全部标记已读失败必须可见（此前没有 try/catch，失败只产生未处理的 rejection）。
// Case 2: failing "mark all read" must be visible (it used to have no try/catch at all).
async function markAllReadFailure(patched) {
  resetCalls();
  markAllReadFails = true;
  const root = await mount("inbox-page.tsx", null, patched);
  try {
    await click(buttonWith("inbox.notifications"));
    await click(buttonWith("inbox.markAllRead"));
    assert.ok(calls.markAllRead > 0, "the mark-all button must attempt the write");
    if (patched) {
      assert.doesNotMatch(text(), /inbox\.markAllReadFail/, "negative control: the stripped source must stay silent");
    } else {
      assert.match(text(), /inbox\.markAllReadFail/, "a failed mark-all must surface inbox.markAllReadFail");
    }
    console.log(`ok   inbox mark-all-read failure ${patched ? "(negative control: stays silent)" : "(surfaces the failure)"}`);
  } finally {
    await React.act(async () => { root.unmount(); });
    markAllReadFails = false;
  }
}

// 用例三：移除附件失败必须可见，且附件行必须留在列表里（此前是静默吞掉，文件留在服务端、界面却像删掉了）。
// Case 3: failing to remove an attachment must be visible and the row must stay (it used to be swallowed,
// leaving the file on the server while the UI looked as if it was gone).
async function removeAttachmentFailure(patched) {
  resetCalls();
  attachmentDelFails = true;
  const root = await mount("post-page.tsx", { draftId: 1, onDraftSaved: () => {}, onPublished: () => {} }, patched);
  try {
    const remove = [...document.querySelectorAll("button")].find((el) => el.getAttribute("aria-label") === "post.removeFile");
    assert.ok(remove, "the draft attachment must render a remove button");
    await click(remove);
    assert.ok(calls.del > 0, "clicking remove must attempt the delete write");
    assert.ok(
      [...document.querySelectorAll("button")].some((el) => el.getAttribute("aria-label") === "post.removeFile"),
      "a failed delete must keep the attachment row so the UI matches the server",
    );
    if (patched) {
      assert.doesNotMatch(text(), /post\.removeFail/, "negative control: the stripped source must stay silent");
    } else {
      assert.match(text(), /post\.removeFail/, "a failed attachment delete must surface post.removeFail");
    }
    console.log(`ok   post attachment removal failure ${patched ? "(negative control: stays silent)" : "(surfaces the failure)"}`);
  } finally {
    await React.act(async () => { root.unmount(); });
    attachmentDelFails = false;
  }
}

// 用例四：成功路径不能被误伤 —— 标记已读成功后仍必须刷新通知列表。
// 这条守住的是"把浮动 promise 改成显式 void"时的常见事故：void 写错位置会把刷新一起丢掉。
// Case 4: the success path must stay intact — a successful mark-read must still refresh the list.
// This guards the usual accident when a floating promise becomes an explicit void: the refresh
// gets dropped along with it.
async function markReadSuccessRefreshes(patched) {
  resetCalls();
  markReadFails = false;
  const root = await mount("inbox-page.tsx", null, patched);
  try {
    await click(buttonWith("inbox.notifications"));
    const before = calls.list;
    await click(buttonContaining("smoke notification"));
    assert.ok(calls.markRead > 0, "the click must attempt the mark-read write");
    assert.ok(calls.list > before, "a successful mark-read must refresh the notification list");
    assert.doesNotMatch(text(), /inbox\.markReadFail/, "a successful mark-read must not report a failure");
    console.log(`ok   inbox successful mark-read still refreshes the list ${patched ? "(negative control: refresh removed)" : ""}`);
  } finally {
    await React.act(async () => { root.unmount(); });
  }
}

// 用例五：从通知跳进帖子时标记已读失败必须可见（此前同样是 .catch(() => undefined)）。
// 这条同时守住本次改动：失败提示改走页面既有的 flash() 通道后，提示必须照旧出现。
// Case 5: arriving at a thread from a notification must report a failed mark-read (it used to be
// .catch(() => undefined) as well). This also guards this change: after routing the failure through
// the page's existing flash() channel the notice must still appear.
async function threadNotificationMarkReadFailure(patched) {
  resetCalls();
  markReadFails = true;
  const root = await mount("thread-page.tsx", { id: 1, onNotify: () => {}, onDeleted: () => {} }, patched);
  try {
    assert.ok(calls.markRead > 0, "the ?notif= parameter must trigger the mark-read write on mount");
    if (patched) {
      assert.doesNotMatch(text(), /thread\.markReadFail/, "negative control: the stripped source must stay silent");
    } else {
      assert.match(text(), /thread\.markReadFail/, "a failed mark-read must surface thread.markReadFail");
    }
    console.log(`ok   thread notification mark-read failure ${patched ? "(negative control: stays silent)" : "(surfaces the failure)"}`);
  } finally {
    await React.act(async () => { root.unmount(); });
    markReadFails = false;
  }
}

// 停用修复后的"同源变体"：只把反馈那一行还原成静默。
// The "fix disabled" variant: restore just the feedback line to its silent form.
const DISABLED = {
  markRead: { from: 'setNotifsError(t("inbox.markReadFail"));', to: "void 0;" },
  markAllRead: { from: 'setNotifsError(t("inbox.markAllReadFail"));', to: "void 0;" },
  removeAttachment: { from: 'if (mountedRef.current) setError(err instanceof ApiError ? err.message : t("post.removeFail"));', to: "void 0;" },
  threadMarkRead: { from: '      flash(tRef.current("thread.markReadFail"));', to: "      void 0;" },
  // 把 void 刷新那一行停用 = "void 写错位置把刷新丢了"的事故形态。
  // 注意仓库源码是 CRLF 行尾，锚点里不要带裸 \n。
  // Disable the void refresh line: the accident shape where the refresh is lost. The source is
  // CRLF, so the anchor must not carry a bare \n.
  refresh: { from: "      void loadNotifications();", to: "      void 0;" },
};

(async () => {
  await notificationMarkReadFailure(null);
  await notificationMarkReadFailure(DISABLED.markRead);
  await markAllReadFailure(null);
  await markAllReadFailure(DISABLED.markAllRead);
  await removeAttachmentFailure(null);
  await removeAttachmentFailure(DISABLED.removeAttachment);
  await threadNotificationMarkReadFailure(null);
  await threadNotificationMarkReadFailure(DISABLED.threadMarkRead);
  await markReadSuccessRefreshes(null);
  await assert.rejects(
    () => markReadSuccessRefreshes(DISABLED.refresh),
    /must refresh the notification list/,
    "negative control: dropping the refresh must make the success-path case fail",
  );
  console.log("ok   inbox successful mark-read refresh (negative control: dropping the refresh turns it red)");
  console.log("write-failure smoke: all cases passed (both the fixed and the negative-control runs)");
})().catch((error) => {
  console.error(`write-failure smoke FAILED: ${error.message}`);
  process.exitCode = 1;
});
