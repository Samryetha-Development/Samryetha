// 文件详情页的预览与下载行为冒烟：真实组件 + 真实 DOM 事件 + 可控的 HTTP 响应。
// Smoke coverage for the file detail page's preview and download behaviour: the real component,
// real DOM events and controllable HTTP responses.
//
// 覆盖：
// - 权威下载计数（服务端去重后不得本地 +1）
// - 文本非 2xx 不得永久 loading，失败后可重试
// - 文本按上限流式读取并在达到上限时取消剩余读取（含跨块 UTF-8）
// - 图片加载失败不得只留裂图，可重试
// - PDF 响应不是 PDF 时不进 iframe；成功时用 Blob URL 并在卸载时撤销
// - 收藏/评分/下载等无关状态更新不得重建预览或重复申请票据
// - 卸载会中止在途请求
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
const { JSDOM, VirtualConsole } = require("jsdom");
const React = require("react");
const { createRoot } = require("react-dom/client");

const dom = new JSDOM('<div id="root"></div>', { url: "http://localhost/files/1", virtualConsole: new VirtualConsole() });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator", { configurable: true, value: dom.window.navigator });
global.IS_REACT_ACT_ENVIRONMENT = true;

const state = {
  detail: null,
  ticketCalls: 0,
  ticketUrl: "/api/files/preview/bytes",
  ticketFailure: false,
  fetchImpl: null,
  lastSignal: null,
  created: [],
  revoked: [],
};
const originalCreateObjectURL = URL.createObjectURL;
const originalRevokeObjectURL = URL.revokeObjectURL;
URL.createObjectURL = () => { const url = `blob:preview-${state.created.length}`; state.created.push(url); return url; };
URL.revokeObjectURL = (url) => state.revoked.push(url);

function makeDetail(overrides = {}) {
  return {
    id: 1,
    title: "Preview resource",
    preview: "",
    folderId: null,
    category: { id: 1, slug: "guide", name: "Guide", kind: "guide" },
    uploader: { id: 1, username: "alice", handle: "alice", displayName: "Alice" },
    tags: [],
    originalFilename: "notes.zip",
    extension: ".zip",
    mimeType: "application/zip",
    sizeBytes: 8,
    visibility: "public",
    status: "published",
    version: 1,
    isFeatured: false,
    downloadCount: 0,
    favoriteCount: 0,
    ratingAvg: null,
    ratingCount: 0,
    isFavorited: false,
    myRating: null,
    createdAt: 0,
    updatedAt: 0,
    descriptionMarkdown: "Hello",
    can: { update: false, delete: false },
    ...overrides,
  };
}

const ticket = () => ({
  id: state.detail.id,
  title: state.detail.title,
  originalFilename: state.detail.originalFilename,
  sizeBytes: state.detail.sizeBytes,
  downloadUrl: state.ticketUrl,
  expiresAt: 1000,
  objectKey: "notes.zip",
  downloadCount: 0,
});

const api = {
  files: {
    get: async () => state.detail,
    preview: async (id, signal) => {
      state.ticketCalls += 1;
      state.lastSignal = signal ?? null;
      if (state.ticketFailure) throw new Error("ticket request failed");
      return ticket();
    },
    download: async () => ({ ...ticket(), downloadCount: 1 }),
    setFavorite: async (_id, on) => ({ isFavorited: on, favoriteCount: on ? 1 : 0 }),
    setRating: async (_id, score) => ({ myRating: score, ratingAvg: 4, ratingCount: 1 }),
  },
};

const originalFetch = global.fetch;
global.fetch = async (url, options = {}) => {
  if (!state.fetchImpl) throw new Error(`unexpected fetch: ${url}`);
  return state.fetchImpl(String(url), options);
};

const inputs = {
  ...require("./ui-smoke-inputs.cjs"),
  ...React,
  AppShell: ({ children }) => React.createElement("div", null, children),
  Loading: () => React.createElement("div", null, "LOADING"),
  api,
  ApiError: class ApiError extends Error {},
  useAuth: () => ({ user: { id: 7, role: "user" }, loading: false }),
  formatBytes: () => "8 bytes",
  timeAgo: () => "now",
  useI18n: () => ({ t: (key) => key, locale: "en" }),
  MarkdownText: ({ children }) => React.createElement("div", null, children),
  FileMetadataDialog: () => null,
  ConfirmDialog: () => null,
  DocumentPreview: () => null,
};

const flush = async (rounds = 3) => {
  for (let index = 0; index < rounds; index += 1) {
    await React.act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)); });
  }
};

async function mount(Component, overrides) {
  state.detail = makeDetail(overrides);
  state.ticketCalls = 0;
  state.ticketFailure = false;
  state.lastSignal = null;
  state.ticketUrl = "/api/files/preview/bytes";
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await React.act(async () => root.render(React.createElement(Component, { id: state.detail.id })));
  await flush();
  return {
    host,
    unmount: async () => { await React.act(async () => root.unmount()); host.remove(); },
  };
}

const byText = (host, text) => [...host.querySelectorAll("button")].find((node) => node.textContent === text);
const byTextStart = (host, prefix) => [...host.querySelectorAll("button")].find((node) => node.textContent.startsWith(prefix));
const bodyText = () => document.body.textContent;

function pdfResponse(body = "%PDF-1.4 preview") {
  return new Response(body, { status: 200, headers: { "content-type": "application/pdf" } });
}

function textResponse(body) {
  return new Response(body, { status: 200, headers: { "content-type": "text/plain; charset=utf-8" } });
}

async function scenario(name, run) {
  await run();
  console.log(`ok   ${name}`);
}

async function main() {
  const source = fs.readFileSync(path.join(__dirname, "../src/file-detail-page.tsx"), "utf8")
    .replace(/^import .*;\r?\n/gm, "");
  const output = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React },
  }).outputText;
  const exported = {};
  new Function("exports", "React", ...Object.keys(inputs), output)(exported, React, ...Object.values(inputs));
  const Component = exported.FileDetailPage;
  const anchorClick = dom.window.HTMLAnchorElement.prototype.click;
  dom.window.HTMLAnchorElement.prototype.click = () => {};

  try {
    await scenario("repeated downloads render the authoritative ticket count", async () => {
      state.fetchImpl = async () => { throw new Error("downloads must not fetch a preview"); };
      const view = await mount(Component, { extension: ".zip", originalFilename: "notes.zip" });
      try {
        const button = byText(view.host, "file.download");
        assert.ok(button, "the real detail component must render its download action");
        const count = () => [...view.host.querySelectorAll("dt")]
          .find((node) => node.textContent === "file.tableDownloads").nextElementSibling.textContent;
        await React.act(async () => { button.click(); });
        assert.equal(count(), "1", "the first download uses the ticket's authoritative count");
        await React.act(async () => { button.click(); });
        assert.equal(count(), "1", "a repeated download must not locally increment the server's deduplicated count");
        assert.equal(state.ticketCalls, 0, "a non-previewable resource must not request a preview ticket");
      } finally {
        await view.unmount();
      }
    });

    await scenario("a text preview with a non-2xx response fails visibly and can be retried", async () => {
      let status = 500;
      state.fetchImpl = async () => (status === 200 ? textResponse("hello world") : new Response("nope", { status }));
      const view = await mount(Component, { extension: ".txt", originalFilename: "notes.txt" });
      try {
        assert.match(bodyText(), /file\.previewFailed/, "a failed text fetch must not show common.loading forever");
        assert.doesNotMatch(bodyText(), /common\.loading/);
        assert.ok(byText(view.host, "common.retry"), "a failed preview must offer a retry");
        status = 200;
        await React.act(async () => { byText(view.host, "common.retry").click(); });
        await flush();
        assert.equal(view.host.querySelector("pre").textContent, "hello world");
        assert.doesNotMatch(bodyText(), /file\.previewFailed/);
        assert.equal(state.ticketCalls, 2, "the retry must ask for a fresh ticket");
      } finally {
        await view.unmount();
      }
    });

    await scenario("text previews stream up to the limit, keep split UTF-8 and cancel the rest", async () => {
      const encoded = new TextEncoder().encode("中");
      let produced = 0;
      let cancelled = false;
      const stream = new ReadableStream({
        pull(controller) {
          if (produced === 0) { produced += 1; controller.enqueue(encoded.slice(0, 1)); return; }
          if (produced === 1) { produced += 1; controller.enqueue(encoded.slice(1)); return; }
          if (produced >= 40) { controller.close(); return; }
          produced += 1;
          controller.enqueue(new TextEncoder().encode("a".repeat(1000)));
        },
        cancel() { cancelled = true; },
      });
      state.fetchImpl = async () => new Response(stream, { status: 200, headers: { "content-type": "text/plain" } });
      const view = await mount(Component, { extension: ".txt", originalFilename: "notes.txt" });
      try {
        const pre = view.host.querySelector("pre");
        assert.ok(pre, "the streamed text must render");
        assert.equal(pre.textContent.length, 4000, "the preview stops at the documented character limit");
        assert.ok(pre.textContent.startsWith("中"), "a UTF-8 character split across chunks must survive");
        assert.equal(cancelled, true, "reaching the limit must cancel the remaining body");
        assert.ok(produced < 40, "the reader must not consume the whole body");
      } finally {
        await view.unmount();
      }
    });

    await scenario("a broken image preview fails visibly and retries", async () => {
      state.fetchImpl = async () => { throw new Error("images are rendered directly"); };
      const view = await mount(Component, { extension: ".png", originalFilename: "shot.png" });
      try {
        const image = view.host.querySelector("img");
        assert.ok(image, "a successful image ticket renders an img");
        await React.act(async () => { image.dispatchEvent(new dom.window.Event("error")); });
        assert.match(bodyText(), /file\.previewFailed/, "a broken image must not remain a bare broken image");
        assert.ok(byText(view.host, "common.retry"), "a broken image must offer a retry");
        await React.act(async () => { byText(view.host, "common.retry").click(); });
        await flush();
        assert.ok(view.host.querySelector("img"), "the retry re-requests the ticket and renders the image again");
        assert.equal(state.ticketCalls, 2);
      } finally {
        await view.unmount();
      }
    });

    await scenario("a non-PDF response never reaches the iframe, and a PDF does via a Blob URL", async () => {
      let contentType = "text/html";
      state.fetchImpl = async () => new Response("<html>not a pdf</html>", {
        status: 200,
        headers: { "content-type": contentType },
      });
      const view = await mount(Component, { extension: ".pdf", originalFilename: "paper.pdf" });
      let created = "";
      try {
        assert.equal(view.host.querySelector("iframe"), null, "unexpected content must never be mounted as a PDF");
        assert.match(bodyText(), /file\.previewFailed/);
        contentType = "application/pdf";
        state.fetchImpl = async () => pdfResponse();
        await React.act(async () => { byText(view.host, "common.retry").click(); });
        await flush();
        const frame = view.host.querySelector("iframe");
        assert.ok(frame, "a real PDF is mounted in the iframe");
        assert.match(frame.getAttribute("src"), /^blob:preview-/);
        created = frame.getAttribute("src");
        const openLink = [...view.host.querySelectorAll("a")]
          .find((node) => node.textContent === "file.openPreviewInTab");
        assert.equal(openLink.getAttribute("href"), created);
      } finally {
        await view.unmount();
      }
      assert.ok(state.revoked.includes(created), "unmounting must revoke the PDF Blob URL");
    });

    await scenario("favorite, rating and download updates do not rebuild the preview", async () => {
      state.fetchImpl = async () => { throw new Error("images are rendered directly"); };
      const view = await mount(Component, { extension: ".png", originalFilename: "shot.png" });
      try {
        assert.equal(state.ticketCalls, 1);
        await React.act(async () => { byTextStart(view.host, "file.favorite").click(); });
        await flush(2);
        assert.equal(state.ticketCalls, 1, "favoriting must not request a new preview ticket");
        await React.act(async () => { view.host.querySelector('button[aria-label="5"]').click(); });
        await flush(2);
        assert.equal(state.ticketCalls, 1, "rating must not request a new preview ticket");
        await React.act(async () => { byText(view.host, "file.download").click(); });
        await flush(2);
        assert.equal(state.ticketCalls, 1, "downloading must not request a new preview ticket");
        assert.ok(view.host.querySelector("img"), "the preview survives the unrelated updates");
      } finally {
        await view.unmount();
      }
    });

    await scenario("unmounting aborts an in-flight preview request", async () => {
      let signal = null;
      const createdBefore = state.created.length;
      state.fetchImpl = (_url, options) => new Promise((_resolve, reject) => {
        signal = options.signal;
        options.signal.addEventListener("abort", () => reject(new dom.window.DOMException("Aborted", "AbortError")));
      });
      const view = await mount(Component, { extension: ".txt", originalFilename: "notes.txt" });
      assert.ok(signal, "the text fetch must carry an abort signal");
      assert.equal(signal.aborted, false);
      await view.unmount();
      assert.equal(signal.aborted, true, "unmounting must abort the in-flight request");
      assert.equal(
        state.created.length,
        createdBefore,
        "no Blob URL is created for a response that never arrived",
      );
    });

    assert.deepEqual(new Set(state.revoked), new Set(state.created), "every Blob URL created must be revoked");
  } finally {
    global.fetch = originalFetch;
    URL.createObjectURL = originalCreateObjectURL;
    URL.revokeObjectURL = originalRevokeObjectURL;
    dom.window.HTMLAnchorElement.prototype.click = anchorClick;
    dom.window.close();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
