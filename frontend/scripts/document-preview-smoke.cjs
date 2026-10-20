// Exercise the real Office preview component against HTTP responses and stale requests.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const ts = require("typescript");
const { JSDOM, VirtualConsole } = require("jsdom");
const React = require("react");
const { createRoot } = require("react-dom/client");

async function main() {
  let status = 404;
  let contentType = "application/json";
  let available = true;
  let currentUser = { id: 7 };
  let calls = 0;
  let configCalls = 0;
  const server = http.createServer((request, response) => {
    calls++;
    response.writeHead(status, { "content-type": contentType });
    response.end(status === 200 ? "%PDF-1.4 preview" : '{"error":{"message":"failed"}}');
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const dom = new JSDOM('<div id="root"></div>', {
    url: `http://127.0.0.1:${server.address().port}/files/1`,
    virtualConsole: new VirtualConsole(),
  });
  global.window = dom.window;
  global.document = dom.window.document;
  Object.defineProperty(global, "navigator", { configurable: true, value: dom.window.navigator });
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const nativeFetch = global.fetch;
  global.fetch = (url, options) => nativeFetch(new URL(url, dom.window.location.href), options);
  const created = [];
  const revoked = [];
  const createBlobUrl = URL.createObjectURL;
  const revokeBlobUrl = URL.revokeObjectURL;
  URL.createObjectURL = () => { const url = `blob:preview-${created.length}`; created.push(url); return url; };
  URL.revokeObjectURL = (url) => revoked.push(url);
  let pendingConfig = null;
  const inputs = {
    ...React,
    LakoButton: ({ children, variant, size, ...props }) => React.createElement("button", props, children),
    useAuth: () => ({ user: currentUser }),
    useI18n: () => ({ t: (key) => key }),
    api: { files: { config: async () => {
      configCalls++;
      if (pendingConfig) return new Promise((resolve) => pendingConfig.push(resolve));
      return { documentPreview: available, convertibleExtensions: [".docx"] };
    } } },
  };
  const source = fs.readFileSync(path.join(__dirname, "../src/document-preview.tsx"), "utf8")
    .replace(/^import .*;\r?\n/gm, "");
  const output = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React },
  }).outputText;
  const exported = {};
  new Function("exports", "React", ...Object.keys(inputs), output)(exported, React, ...Object.values(inputs));
  const Component = exported.DocumentPreview;
  const root = createRoot(document.getElementById("root"));
  const render = async (id = 1, extension = ".docx") => {
    await React.act(async () => root.render(React.createElement(React.StrictMode, null,
      React.createElement(Component, { key: id, resourceId: id, extension }))));
    await React.act(async () => { await new Promise((resolve) => setTimeout(resolve, 100)); });
  };
  try {
    await render();
    assert.equal(calls, 1);
    assert.equal(document.querySelector("iframe"), null);
    assert.match(document.body.textContent, /file\.docPreviewFailed/);
    assert.doesNotMatch(document.body.textContent, /file\.docPreviewHint/);
    console.log("ok   Office HTTP 404 leaves loading and offers download/retry without mounting an iframe");
    status = 503;
    await React.act(async () => document.querySelector("button").click());
    await React.act(async () => { await new Promise((resolve) => setTimeout(resolve, 100)); });
    assert.match(document.body.textContent, /file\.previewBusy/);
    console.log("ok   busy conversion offers a retryable state");
    status = 200;
    contentType = "application/pdf";
    await React.act(async () => document.querySelector("button").click());
    await React.act(async () => { await new Promise((resolve) => setTimeout(resolve, 100)); });
    const firstUrl = document.querySelector("iframe").getAttribute("src");
    assert.ok(firstUrl.startsWith("blob:preview-"));
    assert.equal(document.querySelector("a").getAttribute("href"), firstUrl);
    console.log("ok   retry checks a real PDF HTTP response before mounting the iframe");
    currentUser = { id: 8 };
    await render();
    assert.ok(revoked.includes(firstUrl));
    assert.notEqual(document.querySelector("iframe").getAttribute("src"), firstUrl);
    console.log("ok   identity changes recheck authorization and revoke the previous blob URL");
    const beforeUnsupported = calls;
    available = false;
    await render(2);
    assert.equal(calls, beforeUnsupported);
    assert.equal(document.querySelector("iframe"), null);
    assert.match(document.body.textContent, /file\.previewUnsupported/);
    available = true;
    await render(3, ".zip");
    assert.equal(calls, beforeUnsupported);
    console.log("ok   disabled capability and unsupported extension do not request a PDF");
    contentType = "text/html";
    await render(4);
    assert.equal(document.querySelector("iframe"), null);
    assert.match(document.body.textContent, /file\.docPreviewFailed/);
    console.log("ok   unexpected response content is never mounted as a PDF");
    pendingConfig = [];
    await render(5);
    const resolvers = pendingConfig;
    pendingConfig = null;
    available = false;
    await render(6);
    const beforeStale = calls;
    await React.act(async () => resolvers.forEach((resolve) => resolve({ documentPreview: true, convertibleExtensions: [".docx"] })));
    assert.equal(calls, beforeStale);
    assert.match(document.body.textContent, /file\.previewUnsupported/);
    console.log("ok   stale capability responses cannot start a preview for a different resource");
    await React.act(async () => root.unmount());
    assert.deepEqual(new Set(revoked), new Set(created));
    assert.ok(configCalls > calls);
    console.log("ok   all blob URLs are released on replacement/unmount");
  } finally {
    global.fetch = nativeFetch;
    URL.createObjectURL = createBlobUrl;
    URL.revokeObjectURL = revokeBlobUrl;
    dom.window.close();
    await new Promise((resolve) => server.close(resolve));
  }
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
