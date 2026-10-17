// Verify native date-picker commits, timezone conversion, and private-page races.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
const { JSDOM } = require("jsdom");
const dom = new JSDOM('<div id="root"></div>', { url: "http://localhost/my-posts" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator", { configurable: true, value: dom.window.navigator });
global.IS_REACT_ACT_ENVIRONMENT = true;
const React = require("react");
const { createRoot } = require("react-dom/client");
let currentUser = { id: 1 };
const post = {
  id: 1, title: "Private scheduled post", preview: "Private text", scheduledAt: Date.now() + 86400_000,
  boardSlug: "general", canModify: false, status: "pending", lastError: null,
};
const inputs = {
  ...React,
  useI18n: () => ({ t: (key) => key, locale: "en" }),
  useAuth: () => ({ user: currentUser, loading: false }),
  AppShell: ({ children }) => React.createElement("div", null, children),
  Dialog: ({ children, actions }) => React.createElement("div", null, children, actions),
  ConfirmDialog: ({ trigger }) => trigger,
  ScheduleDialog: () => null,
  formatDateL: () => "date",
  ApiError: class extends Error {},
  api: {
    scheduledPosts: { list: async () => ({ items: [post], nextCursor: null }) },
    ownPosts: async () => ({ items: [], nextCursor: null }),
  },
};
function load(file) {
  const source = fs.readFileSync(path.join(__dirname, "../src", file), "utf8").replace(/^import .*;\r?\n/gm, "");
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React } }).outputText;
  const exported = {};
  new Function("exports", "React", ...Object.keys(inputs), output)(exported, React, ...Object.values(inputs));
  return exported;
}
inputs.usePagedList = load("lib/use-paged-list.ts").usePagedList;
inputs.ListPagination = load("list-pagination.tsx").ListPagination;

async function nativeDateCommit() {
  const { ScheduleDialog } = load("schedule-dialog.tsx");
  const root = createRoot(document.getElementById("root"));
  const confirmed = [];
  try {
    await React.act(async () => root.render(React.createElement(ScheduleDialog, {
      open: true, allowLock: true, pending: false, error: null, onOpenChange() {},
      onConfirm: (...args) => confirmed.push(args),
    })));
    const input = document.querySelector('input[type="datetime-local"]');
    const date = new Date(Date.now() + 2 * 86400_000);
    const pad = (n) => String(n).padStart(2, "0");
    const value = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T09:30`;
    // Native pickers may update their DOM value before committing a change event.
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set.call(input, value);
    const confirm = [...document.querySelectorAll("button")].find((node) => node.textContent === "schedule.confirm");
    await React.act(async () => confirm.click());
    assert.deepEqual(confirmed, [[new Date(value).getTime(), true]], "must submit the displayed time in the device timezone");
    console.log("ok   ScheduleDialog: native picker commit uses the displayed time");
  } finally {
    await React.act(async () => root.unmount());
  }
}

async function privatePage() {
  const { MyPostsPage } = load("my-posts-page.tsx");
  const root = createRoot(document.getElementById("root"));
  const render = async () => React.act(async () => root.render(React.createElement(React.StrictMode, null,
    React.createElement(MyPostsPage, { onNotify() {} }))));
  const original = inputs.api.scheduledPosts.list;
  try {
    await render();
    assert.match(document.body.textContent, /Private scheduled post/);
    assert.match(document.body.textContent, /schedule.locked/);
    assert.doesNotMatch(document.body.textContent, /schedule.changeTime|schedule.cancel/);
    assert.doesNotMatch(document.body.textContent, /common.loading/);
    console.log("ok   MyPostsPage: StrictMode loads and locked tasks have no mutation controls");

    let resolveOld;
    inputs.api.scheduledPosts.list = () => new Promise((resolve) => { resolveOld = resolve; });
    currentUser = { id: 2 };
    await render();
    const published = [...document.querySelectorAll("button")].find((node) => node.textContent === "myPosts.published");
    await React.act(async () => published.click());
    await React.act(async () => resolveOld({ items: [post], nextCursor: null }));
    assert.match(document.body.textContent, /myPosts.noPublished/);
    assert.doesNotMatch(document.body.textContent, /Private scheduled post/);
    console.log("ok   MyPostsPage: stale scheduled responses cannot overwrite the published tab");
  } finally {
    inputs.api.scheduledPosts.list = original;
    await React.act(async () => root.unmount());
  }
}

async function composerPoll() {
  const poll = { question: "Which?", allowMultiple: true, options: ["First", "Second"] };
  let draftPoll = poll;
  let fail = true;
  let scheduled = 0;
  const submitted = [];
  const stamp = Date.now() + 86400_000;
  Object.assign(inputs, {
    SDropdown: () => null,
    EditorField: ({ value, children }) => React.createElement("div", null, value, children),
    PollToggle: () => null,
    PollFields: () => null,
    pollError: load("poll-editor.tsx").pollError,
    ScheduleDialog: ({ open, onConfirm }) => open ? React.createElement("button", {
      onClick: () => onConfirm(stamp, false),
    }, "Confirm test schedule") : null,
    formatBytes: () => "1 KB",
  });
  inputs.api.boards = { list: async () => ({ items: [{ slug: "general", name: "General" }] }) };
  inputs.api.attachments = { config: async () => ({ allowedExtensions: [], maxUploadBytes: 1000 }) };
  inputs.api.drafts = { get: async () => ({
    id: 1, title: "Poll post", bodyMarkdown: "Post body", bodyFormat: "text",
    boardSlug: "general", attachments: [], poll: draftPoll,
  }) };
  inputs.api.scheduledPosts.create = async (body) => {
    submitted.push(body);
    if (fail) throw new Error("temporary failure");
  };
  const { PostPage } = load("post-page.tsx");
  const click = async (label) => {
    const button = [...document.querySelectorAll("button")].find((node) => node.textContent === label);
    assert.ok(button, label);
    await React.act(async () => button.click());
  };
  for (const invalid of [false, true]) {
    draftPoll = invalid ? { ...poll, question: "" } : poll;
    const root = createRoot(document.getElementById("root"));
    try {
      await React.act(async () => root.render(React.createElement(React.StrictMode, null,
        React.createElement(PostPage, { draftId: 1, onDraftSaved() {}, onPublished() {}, onScheduled: () => { scheduled++; } }))));
      await click("⌄");
      await click("schedule.title");
      const before = submitted.length;
      await click("Confirm test schedule");
      if (invalid) {
        assert.equal(submitted.length, before, "invalid polls must never be scheduled");
        assert.match(document.body.textContent, /poll.incomplete/);
      } else {
        assert.deepEqual(submitted[0].poll, poll, "scheduling must preserve all poll fields");
        assert.equal(submitted[0].scheduledAt, stamp);
        assert.equal(submitted[0].canModify, false);
        assert.match(document.body.textContent, /schedule.createFail/);
        assert.match(document.body.textContent, /Post body/);
        assert.equal(scheduled, 0, "failed scheduling must keep the composer open");
        fail = false;
        await click("Confirm test schedule");
        assert.equal(scheduled, 1);
        assert.deepEqual(submitted[1].poll, poll);
      }
    } finally {
      await React.act(async () => root.unmount());
    }
  }
  console.log("ok   PostPage: poll scheduling, failure recovery, retry, and invalid poll rejection");
}

(async () => { await nativeDateCommit(); await privatePage(); await composerPoll(); })().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
