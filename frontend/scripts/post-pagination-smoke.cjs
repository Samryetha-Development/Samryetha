// Exercise the real list hook and pages in StrictMode with paged API responses.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
const { JSDOM } = require("jsdom");
const dom = new JSDOM('<div id="root"></div>', { url: "http://localhost/", pretendToBeVisual: true });
global.window = dom.window;
global.document = dom.window.document;
global.Event = dom.window.Event;
global.requestAnimationFrame = (callback) => setTimeout(callback, 0);
global.cancelAnimationFrame = clearTimeout;
Object.defineProperty(global, "navigator", { configurable: true, value: dom.window.navigator });
global.IS_REACT_ACT_ENVIRONMENT = true;
const React = require("react");
const { createRoot } = require("react-dom/client");
let currentUser = { id: 1, username: "reader", settings: { reduce_motion: true } };
const posts = Array.from({ length: 47 }, (_, index) => ({
  id: index + 1, title: `Post ${index + 1}`, preview: "Preview", board: { id: 1, slug: index < 5 ? "quiet" : "busy", name: "Board" },
  author: { id: 2, username: "author", displayName: "Author", handle: "author" }, createdAt: 1, lastActivityAt: 1,
  replyCount: 0, isPinned: false, isLocked: false,
}));
const calls = [];
let failNext = false;
let slowBoard = null;
let resolveSlow;
function page(items, { cursor, limit = 20 } = {}) {
  const offset = Number(cursor || 0);
  return { items: items.slice(offset, offset + limit), nextCursor: offset + limit < items.length ? String(offset + limit) : null };
}
const inputs = {
  ...React,
  useAuth: () => ({ user: currentUser, loading: false }),
  useAuthModal: () => ({ openModal() {} }),
  useI18n: () => ({ locale: "en", t: (key, values) => key === "pagination.page" ? `Page ${values.page}` : key }),
  formatDateL: () => "date",
  reducedMotion: () => true,
  useTabIndicator: () => ({ ready: false, x: 0, width: 0 }),
  usePresence: () => null,
  useRealtimeState: () => ({ presence: null, unreadCounts: null }),
  useSse() {},
  AppShell: ({ children, search, nav }) => React.createElement("div", null, nav, search, children),
  TopNav: ({ onViewChange }) => React.createElement("div", null, ["latest", "followed", "boards"].map((view) =>
    React.createElement("button", { key: view, onClick: () => onViewChange(view) }, view))),
  SearchIcon: () => null,
  Loading: () => React.createElement("span", null, "Loading"),
  ThreadRow: ({ thread }) => React.createElement("article", { "data-id": thread.id }, thread.title),
  ReportButton: () => null,
  MarkdownText: ({ source }) => React.createElement("span", null, source),
  initials: () => "R",
  ApiError: class extends Error {},
  api: {
    boards: { list: async () => ({ items: ["quiet", "busy"].map((slug) => ({ slug, name: slug, todayActivity: 0, memberCount: 1 })) }) },
    notifications: { unreadCount: async () => ({ unreadCount: 0 }) },
    discussions: { feed: async (options) => {
      calls.push({ kind: "feed", ...options });
      if (failNext) { failNext = false; throw new Error("temporary failure"); }
      if (slowBoard === options.board) return new Promise((resolve) => { resolveSlow = resolve; });
      return page(options.board ? posts.filter((post) => post.board.slug === options.board) : posts, options);
    } },
    search: async (query, options) => {
      calls.push({ kind: "search", query, ...options });
      // Matches deep body text, absent from the title/preview: must not be filtered again in the browser.
      return { items: posts.slice(options.offset, options.offset + options.limit), total: posts.length };
    },
    users: {
      get: async () => ({ displayName: "Reader", username: "reader", stats: { discussions: 47, replies: 0, followers: 0 }, bio: "", joinedAt: 1 }),
      posts: async (_, options) => { calls.push({ kind: "posts", ...options }); return page(posts, options); },
      replies: async (_, options) => { calls.push({ kind: "replies", ...options }); return page([], options); },
      saved: async (_, options) => { calls.push({ kind: "saved", ...options }); return page(posts, options); },
    },
  },
};
function load(file) {
  const source = fs.readFileSync(path.join(__dirname, "../src", file), "utf8").replace(/^import .*;\r?\n/gm, "");
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React } }).outputText;
  const exported = {};
  new Function("exports", "React", ...Object.keys(inputs), output)(exported, React, ...Object.values(inputs));
  return exported;
}
Object.assign(inputs, load("lib/use-animated-tabs.ts"), load("lib/use-paged-list.ts"), load("list-pagination.tsx"));
const { DiscussionApp } = load("discussion-app.tsx");
const { ProfilePage } = load("profile-page.tsx");
let root;
async function mount(Component) {
  root = createRoot(document.getElementById("root"));
  await React.act(async () => root.render(React.createElement(React.StrictMode, null, React.createElement(Component))));
}
async function unmount() { await React.act(async () => root.unmount()); }
async function click(label) {
  const button = [...document.querySelectorAll("button")].find((node) => node.textContent === label);
  assert.ok(button, `missing ${label}`);
  await React.act(async () => button.click());
}
async function inputValue(input, value) {
  await React.act(async () => {
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set.call(input, String(value));
    input.dispatchEvent(new window.Event("input", { bubbles: true }));
  });
}
async function size(value) {
  const { usePageSizePreference } = inputs;
  let preference;
  function Preference() { preference = usePageSizePreference(); return null; }
  const node = document.createElement("div"); document.body.appendChild(node);
  const preferenceRoot = createRoot(node);
  await React.act(async () => preferenceRoot.render(React.createElement(Preference)));
  await React.act(async () => preference.setPageSize(value));
  await React.act(async () => preferenceRoot.unmount()); node.remove();
}
function ids() { return [...document.querySelectorAll("article[data-id]")].map((node) => Number(node.dataset.id)); }
async function board(slug) {
  await React.act(async () => document.querySelector(`[data-filter="${slug}"]`).click());
}

(async () => {
  try {
    await mount(DiscussionApp);
    assert.equal(ids().length, 20);
    assert.equal(document.querySelector('.page-navigation button').disabled, true);
    await click("pagination.next");
    assert.deepEqual(ids(), posts.slice(20, 40).map((post) => post.id));
    assert.equal(document.querySelector('[aria-current="page"]').textContent, "2");
    await click("pagination.previous");
    assert.equal(ids()[0], 1);
    await size(7);
    assert.equal(ids().length, 7);
    assert.equal(window.localStorage.getItem("samryetha_posts_per_page"), "7");
    await click("pagination.next");
    assert.equal(ids()[0], 8);
    const snapshot = window.history.state.postPagination;
    await unmount();
    assert.equal(snapshot.cursors.length, 2);
    await mount(DiscussionApp);
    assert.equal(document.querySelector('[aria-current="page"]').textContent, "2");
    assert.equal(ids()[0], 8, "returning from detail restores the cursor at a non-default page size");
    const count = calls.length;
    await size(0);
    assert.equal(calls.length, count, "invalid size must not trigger a request");
    await size(51);
    assert.equal(calls.length, count);
    await size(3);
    assert.equal(ids().length, 3);
    assert.equal(document.querySelector('[aria-current="page"]').textContent, "1");
    await click("pagination.next");
    await board("quiet");
    assert.deepEqual(ids(), [1, 2, 3]);
    assert.equal(calls.at(-1).board, "quiet");
    assert.equal(calls.at(-1).cursor, undefined);
    await click("pagination.next");
    assert.deepEqual(ids(), [4, 5]);
    assert.equal([...document.querySelectorAll('.page-navigation button')].at(-1).disabled, true);
    await board("busy");
    assert.deepEqual(ids(), [6, 7, 8], "server-side board filter must precede the limit");
    failNext = true;
    await click("pagination.next");
    assert.match(document.body.textContent, /feed.loadThreadsFail/);
    await click("common.retry");
    assert.deepEqual(ids(), [9, 10, 11], "retry must stay on the failed page");
    slowBoard = "quiet";
    await board("quiet");
    await board("busy");
    await React.act(async () => resolveSlow(page(posts.slice(0, 5), { limit: 3 })));
    assert.deepEqual(ids(), [6, 7, 8], "a late response must not replace the new filter");
    slowBoard = null;
    await inputValue(document.querySelector('input[type="search"]'), "deep body match");
    await React.act(async () => new Promise((resolve) => setTimeout(resolve, 350)));
    assert.equal(calls.at(-1).kind, "search");
    assert.equal(calls.at(-1).board, "busy");
    assert.equal(ids().length, 3, "server search matches must survive even when preview does not match");
    await click("pagination.next");
    assert.equal(calls.at(-1).offset, 3);
    await unmount();
    await mount(DiscussionApp);
    assert.equal(document.querySelector('input[type="search"]').value, "deep body match");
    assert.equal(calls.at(-1).kind, "search");
    assert.equal(calls.at(-1).offset, 3, "search and its page must restore together");
    await click("followed");
    assert.equal(calls.at(-1).feed, "followed");
    assert.equal(calls.at(-1).cursor, undefined);
    await unmount();
    console.log("ok   feed: pages, size persistence, restoration, server filters, retry and stale-response protection");

    window.history.replaceState({}, "", "/profile");
    await mount(ProfilePage);
    assert.equal(ids().length, 3);
    await click("pagination.next");
    assert.equal(ids()[0], 4);
    await click("profile.saved");
    assert.equal(calls.at(-1).kind, "saved");
    assert.equal(calls.at(-1).limit, 3);
    assert.equal(calls.at(-1).cursor, undefined);
    await click("pagination.next");
    assert.equal(ids()[0], 4);
    await click("profile.replies");
    assert.equal(calls.at(-1).kind, "replies");
    assert.equal(calls.at(-1).cursor, undefined);
    assert.equal(ids().length, 0);
    currentUser = { id: 3, username: "other", settings: { reduce_motion: true } };
    await React.act(async () => root.render(React.createElement(React.StrictMode, null, React.createElement(ProfilePage))));
    assert.equal(calls.at(-1).cursor, undefined);
    await unmount();
    console.log("ok   profile: posts/saved/replies pagination and account/tab resets");

    window.localStorage.setItem("samryetha_posts_per_page", "bad");
    window.history.replaceState({}, "", "/");
    await mount(DiscussionApp);
    assert.equal(ids().length, 20, "corrupt preferences fall back to 20");
    await unmount();
    console.log("ok   corrupt size falls back safely");

    const storage = window.Storage.prototype;
    const getItem = storage.getItem;
    const setItem = storage.setItem;
    storage.getItem = () => { throw new Error("Storage blocked"); };
    storage.setItem = () => { throw new Error("Storage blocked"); };
    await mount(DiscussionApp);
    assert.equal(ids().length, 20);
    await size(4);
    assert.equal(ids().length, 4, "size control must remain usable without storage");
    await unmount();
    storage.getItem = getItem;
    storage.setItem = setItem;
    console.log("ok   blocked storage still allows changing the page size");
  } catch (error) {
    console.error(error);
    process.exitCode = 1;
  } finally { dom.window.close(); }
})();
