// Exercise actual poll UI behavior when responses replace or remove a poll.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
const { JSDOM } = require("jsdom");
const dom = new JSDOM('<div id="root"></div>', { url: "http://localhost/d/1" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator", { configurable: true, value: dom.window.navigator });
global.IS_REACT_ACT_ENVIRONMENT = true;
const React = require("react");
const { createRoot } = require("react-dom/client");
const { act } = React;
const user = { id: 1, status: "active" };
const fresh = () => ({ question: "Which?", allowMultiple: true, options: [
  { id: 1, label: "First", voteCount: 0 }, { id: 2, label: "Second", voteCount: 0 },
], totalVoters: 0, totalVotes: 0, viewerOptionIds: [], canVote: true });
let poll = fresh();
let locked = false;
let nextDetail;
let voteFailure = false;
let submitted = [];
let voteCalls = 0;
const inputs = {
  ...React,
  api: { discussions: {
    get: async () => nextDetail,
    vote: async (_id, choices) => {
      voteCalls++;
      if (voteFailure) throw new Error("temporary failure");
      submitted = [...choices];
      return { ...poll, viewerOptionIds: choices, totalVoters: 1, totalVotes: choices.length };
    },
  } },
  ApiError: class extends Error {},
  useAuth: () => ({ user }),
  useI18n: () => ({ t: (key) => key }),
};
const source = fs.readFileSync(path.join(__dirname, "../src/poll-card.tsx"), "utf8").replace(/^import .*;\r?\n/gm, "");
const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React } }).outputText;
const exported = {};
new Function("exports", "React", ...Object.keys(inputs), output)(exported, React, ...Object.values(inputs));
const root = createRoot(document.getElementById("root"));
const button = (key) => [...document.querySelectorAll("button")].find((el) => el.textContent === key);
const choices = () => [...document.querySelectorAll('input[type="checkbox"]')];
function view() {
  return React.createElement(React.StrictMode, null, poll ? React.createElement(exported.PollCard, {
    discussionId: 1, poll, locked, onSignIn: () => {}, onChange: (value, nextLocked) => {
      poll = value;
      locked = nextLocked ?? locked;
      root.render(view());
    },
  }) : React.createElement("p", null, "No poll"));
}
async function click(element) { assert.ok(element); await act(async () => { element.click(); }); }
(async () => {
  await act(async () => { root.render(view()); });
  await click(choices()[0]);
  assert.ok(choices()[0].checked);
  // A configuration refresh must discard IDs that now belong to a removed option.
  nextDetail = { poll: { ...fresh(), options: [
    { id: 11, label: "New first", voteCount: 0 }, { id: 12, label: "New second", voteCount: 0 },
  ] }, isLocked: false };
  await click(button("poll.refresh"));
  assert.ok(choices().every((choice) => !choice.checked));
  assert.ok(button("poll.vote").disabled);
  await click(choices()[0]);
  voteFailure = true;
  await click(button("poll.vote"));
  assert.ok(choices()[0].checked, "A failed vote must keep the selected option");
  assert.match(document.body.textContent, /poll.voteFail/);
  voteFailure = false;
  const before = voteCalls;
  await act(async () => { button("poll.vote").click(); button("poll.vote").click(); });
  assert.equal(voteCalls, before + 1, "A double click must submit once");
  assert.deepEqual(submitted, [11]);
  assert.match(document.body.textContent, /poll.saved/);
  // Refreshing a post also propagates its current lock state.
  nextDetail = { poll: { ...poll, canVote: false }, isLocked: true };
  await click(button("poll.refresh"));
  assert.ok(choices().every((choice) => choice.disabled || choice.closest("fieldset").disabled));
  assert.match(document.body.textContent, /poll.closed/);
  // Removing a poll before its first vote must remove the stale card after refresh.
  nextDetail = { poll: null, isLocked: false };
  await click(button("poll.refresh"));
  assert.equal(document.body.textContent, "No poll");
  await act(async () => { root.unmount(); });
  console.log("ok   PollCard: refreshed options, retry, duplicate submit, post lock, and removed poll under StrictMode");
})().catch((error) => { console.error(error); process.exitCode = 1; });
