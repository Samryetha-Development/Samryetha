const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
const { JSDOM } = require("jsdom");
const dom = new JSDOM('<div id="root"></div>', { url: "http://localhost/forgot-password" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator", { value: dom.window.navigator, configurable: true });
global.IS_REACT_ACT_ENVIRONMENT = true;
const React = require("react");
const { createRoot } = require("react-dom/client");
const { act } = React;
let calls = 0;
const api = { auth: {
  recoverWithKey: async (body) => {
    calls++;
    assert.equal(body.username, "student");
    assert.equal(body.recoveryKey, "original-key");
    if (calls === 1) throw new Error("network unavailable");
    return { ok: true, recoveryKey: "replacement-key" };
  },
  resetPassword: async ({ token }) => {
    assert.equal(token, "mail-proof");
    return { ok: true, recoveryKey: "email-replacement" };
  },
} };
const bindings = { ...React, api, ApiError: Error, useI18n: () => ({ t: (key) => key }) };
function load(file, extras = {}) {
  const source = fs.readFileSync(path.join(__dirname, "../src", file), "utf8").replace(/^import .*;\r?\n/gm, "");
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React } }).outputText;
  const values = { ...bindings, ...extras };
  const exports = {};
  new Function("exports", "React", ...Object.keys(values), output)(exports, React, ...Object.values(values));
  return exports;
}
const { RecoveryKeyNotice } = load("recovery-key-notice.tsx");
const { ForgotPasswordPage } = load("forgot-password-page.tsx", { RecoveryKeyNotice });
const { ResetPasswordPage } = load("reset-password-page.tsx", { RecoveryKeyNotice });
const root = createRoot(document.getElementById("root"));
async function fill(element, value) {
  await act(async () => {
    const prototype = element.tagName === "TEXTAREA" ? dom.window.HTMLTextAreaElement.prototype : dom.window.HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, "value").set.call(element, value);
    element.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  });
}
async function submit() {
  await act(async () => { document.querySelector("form").dispatchEvent(new dom.window.Event("submit", { bubbles: true, cancelable: true })); });
}
(async () => {
  await act(async () => root.render(React.createElement(ForgotPasswordPage)));
  assert.equal(document.querySelector('input[type="email"]'), null, "email must not be required for key recovery");
  const inputs = [...document.querySelectorAll("input")];
  await fill(inputs[0], "student"); await fill(inputs[1], "original-key");
  await fill(inputs[2], "newpassword123"); await fill(inputs[3], "mismatch");
  await submit(); assert.equal(calls, 0); assert.match(document.querySelector('[role="alert"]').textContent, /passwordMismatch/);
  await fill(inputs[3], "newpassword123"); await submit();
  assert.match(document.querySelector('[role="alert"]').textContent, /network unavailable/);
  await submit(); assert.equal(calls, 2);
  assert.equal(document.querySelector("input[readonly]").value, "replacement-key");
  assert.equal(window.localStorage.length, 0);
  await act(async () => root.render(null));
  window.history.replaceState({}, "", "/reset-password?token=mail-proof");
  await act(async () => root.render(React.createElement(ResetPasswordPage)));
  const passwords = [...document.querySelectorAll("input")];
  await fill(passwords[0], "newpassword123"); await fill(passwords[1], "newpassword123"); await submit();
  assert.equal(window.location.search, "");
  assert.equal(document.querySelector("input[readonly]").value, "email-replacement");
  let activeUser = { id: 1, username: "first" };
  let finishOldRequest;
  api.messages = {
    conversations: async () => ({ items: activeUser.id === 1 ? [1, 2].map((id) => ({ id, otherUser: { displayName: `person ${id}`, username: `person${id}`, handle: `person${id}` }, lastMessage: null, unreadCount: 0 })) : [] }),
    list: async (id) => id === 2 ? new Promise((resolve) => { finishOldRequest = resolve; }) : ({ items: [{ id: 1, body: "private first-account message", senderId: 1, createdAt: 0 }], otherUser: { username: "person1", displayName: "person 1" }, nextCursor: null }),
    markRead: async () => undefined,
  };
  api.notifications = { list: async () => ({ items: [] }) };
  const { InboxPage } = load("inbox-page.tsx", {
    ...require("./ui-smoke-inputs.cjs"),
    AppShell: ({ children }) => React.createElement("div", null, children),
    useAuth: () => ({ user: activeUser, loading: false }),
    timeAgo: () => "",
    ReportButton: () => null,
  });
  await act(async () => root.render(React.createElement(InboxPage)));
  await act(async () => [...document.querySelectorAll("button")].find((button) => button.textContent.includes("person 1")).click());
  assert.match(document.body.textContent, /private first-account message/);
  await act(async () => [...document.querySelectorAll("button")].find((button) => button.textContent.includes("person 2")).click());
  assert.doesNotMatch(document.body.textContent, /private first-account message/, "old messages must disappear immediately while switching");
  activeUser = { id: 2, username: "second" };
  await act(async () => root.render(React.createElement(InboxPage)));
  await act(async () => finishOldRequest({ items: [{ id: 2, body: "late private message", senderId: 1, createdAt: 0 }], otherUser: { username: "old", displayName: "old account" }, nextCursor: null }));
  assert.doesNotMatch(document.body.textContent, /late private message|old account|person 1/);
  await act(async () => root.render(null));
  window.history.replaceState({}, "", "/inbox?to=person1");
  api.messages.send = async ({ username }) => { assert.equal(username, "person1"); return { conversationId: 1 }; };
  api.messages.list = async () => ({ items: [], otherUser: { id: 10, username: "person1", displayName: "person 1" }, nextCursor: null });
  await act(async () => root.render(React.createElement(InboxPage)));
  await fill(document.querySelector(".new-message textarea"), "new conversation");
  await act(async () => document.querySelector(".new-message").dispatchEvent(new dom.window.Event("submit", { bubbles: true, cancelable: true })));
  assert.equal(window.location.search, "");
  assert.equal(document.querySelector(".new-message"), null);
  let reportCount = 0;
  api.moderation = { createReport: async (body) => {
    assert.deepEqual(body, { reportableType: "user", reportableId: 10, reason: "Abusive private message" });
    reportCount++;
  } };
  const { ReportButton } = load("report-button.tsx", {
    Dialog: ({ open, children }) => open ? React.createElement("div", { role: "dialog" }, children) : null,
    useAuth: () => ({ user: activeUser }), useAuthModal: () => ({ openModal() { throw Error("Unexpected login prompt"); } }),
  });
  await act(async () => root.render(React.createElement(ReportButton, { targetType: "user", targetId: 10, onReported: () => reportCount++ })));
  await act(async () => document.querySelector("button").click());
  await fill(document.querySelector("textarea"), "Abusive private message");
  await submit();
  assert.equal(reportCount, 2); assert.equal(document.querySelector('[role="dialog"]'), null);
  const streams = [];
  global.EventSource = class {
    constructor() { this.listeners = new Map(); streams.push(this); }
    addEventListener(name, handler) { this.listeners.set(name, handler); }
    removeEventListener(name) { this.listeners.delete(name); }
    close() { this.closed = true; }
  };
  const { useRealtimeState } = load("lib/realtime.ts");
  function RealtimeProbe({ id }) {
    const state = useRealtimeState(true, id);
    return React.createElement("output", null, state.unreadCounts?.total ?? "none");
  }
  await act(async () => root.render(React.createElement(RealtimeProbe, { id: 1 })));
  const oldListener = streams[0].listeners.get("unread-count");
  const frame = (total) => ({ data: JSON.stringify({ notificationUnreadCount: total, messageUnreadCount: 0, total }) });
  await act(async () => oldListener(frame(4)));
  assert.equal(document.querySelector("output").textContent, "4");
  await act(async () => root.render(React.createElement(RealtimeProbe, { id: 2 })));
  assert.equal(streams[0].closed, true);
  assert.equal(document.querySelector("output").textContent, "none");
  await act(async () => oldListener(frame(99)));
  assert.equal(document.querySelector("output").textContent, "none", "old account stream cannot restore unread state");
  await act(async () => streams[1].listeners.get("unread-count")(frame(7)));
  assert.equal(document.querySelector("output").textContent, "7");
  await act(async () => root.unmount());
  console.log("ok   recovery validation/retry, one-time key display, reset URL cleanup, inbox/realtime account and in-flight request isolation");
})().catch((error) => { console.error(error); process.exitCode = 1; });
