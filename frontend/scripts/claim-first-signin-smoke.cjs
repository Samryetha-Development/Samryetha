const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
const { JSDOM } = require("jsdom");
const dom = new JSDOM('<div id="root"></div>', { url: "http://localhost/claim?ticket=local-test&returnTo=%2Fsettings" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator", { value: dom.window.navigator, configurable: true });
global.IS_REACT_ACT_ENVIRONMENT = true;
const React = require("react");
const { act } = React;
const { createRoot } = require("react-dom/client");
let createCalls = 0;
const api = { auth: {
  claimInfo: async () => ({ email: null, displayName: "new-student" }),
  claimNew: async ({ ticket }) => {
    assert.equal(ticket, "local-test");
    createCalls++;
    if (createCalls === 1) throw new Error("network unavailable");
    return new Promise(() => {});
  },
} };
const source = fs.readFileSync(path.join(__dirname, "../src/claim-page.tsx"), "utf8").replace(/^import .*;\r?\n/gm, "");
const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React } }).outputText;
const bindings = { ...React, api, ApiError: Error, getClaimReturnPath: value => value, useI18n: () => ({ t: key => key }) };
const moduleExports = {};
new Function("exports", "React", ...Object.keys(bindings), output)(moduleExports, React, ...Object.values(bindings));
const root = createRoot(document.getElementById("root"));
const button = key => [...document.querySelectorAll("button")].find(node => node.textContent === key);
async function click(key) { await act(async () => button(key).click()); }
(async () => {
  await act(async () => root.render(React.createElement(moduleExports.ClaimPage)));
  assert.equal(document.querySelector("input"), null, "new users must not see a legacy password form");
  assert.equal(button("claim.createNew").disabled, false);
  await click("claim.linkExisting");
  assert.ok(document.querySelector('input[type="password"]'));
  assert.match(document.body.textContent, /claim.existingHint/);
  await click("common.back");
  assert.equal(document.querySelector("input"), null);
  await click("claim.createNew");
  assert.equal(createCalls, 1);
  assert.match(document.querySelector('[role="alert"]').textContent, /network unavailable/);
  await click("claim.createNew");
  assert.equal(createCalls, 2);
  assert.equal(button("claim.creating").disabled, true);
  assert.equal(button("claim.linkExisting").disabled, true);
  await act(async () => root.unmount());
  console.log("ok   first sign-in choices, legacy form, retry and pending state");
})().catch(error => { console.error(error); process.exitCode = 1; });
