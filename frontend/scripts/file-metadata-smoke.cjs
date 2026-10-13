// 元数据编辑冒烟检查（Stage 3：标签与简介可编辑；Stage 4 扩为六个字段）。
// Smoke check for metadata editing (stage 3: editable tags and description; stage 4 widened it to
// six fields).
//
// 无测试框架、无新依赖，沿用本仓库既有套路（jsdom + ts.transpileModule + 注入桩），
// 并且每个用例都跑两遍：一遍跑真实源码（必须发生调用/必须出现提示），一遍跑"把这段处理剥掉"
// 的同源变体（必须不发生/不出现）。第二遍是防呆，避免出现"永远为真"的假绿断言。
// No test framework and no new dependencies: this follows the repository's existing pattern
// (jsdom + ts.transpileModule + injected stubs), and every case runs twice, once against the real
// source (the call must happen or the message must appear) and once against the same source with the
// handling stripped (it must not). The second pass guards against vacuous assertions.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
const { JSDOM } = require("jsdom");
const dom = new JSDOM('<div id="root"></div>', { url: "http://localhost/files/1" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator", { configurable: true, value: dom.window.navigator });
global.IS_REACT_ACT_ENVIRONMENT = true;
// 把 JSDOM 自带的空 #root 摘掉：否则每个用例新挂的 #root 会变成第二个同 id 元素，
// getElementById 恒返回先出现的空壳，所有文本断言都会读到空字符串（假绿）。
// The empty #root JSDOM ships with is detached first: otherwise each case's fresh #root becomes a
// second element with the same id, getElementById keeps returning the earlier empty shell, and every
// text assertion reads an empty string.
document.getElementById("root").remove();
const React = require("react");
const { createRoot } = require("react-dom/client");

// 插值桩把变量拼进文案，好让断言能验证插值确实到了界面上。
// The stub splices interpolated variables into the copy so an assertion can check that the value
// actually reached the interface.
const t = (key, vars) => (vars
  ? `${key}(${Object.entries(vars).map(([name, value]) => `${name}=${value}`).join(",")})`
  : key);

let saveFails = false;
const calls = { update: [] };
const SERVER_REASON = "description is longer than the allowed limit";

class ApiErrorStub extends Error {
  constructor(status, payload) {
    super(payload?.message ?? "failed");
    this.status = status;
  }
}

const DETAIL = {
  id: 42,
  title: "高等数学提纲",
  preview: "",
  folderId: null,
  category: { id: 1, slug: "guide", name: "Guide", kind: "guide" },
  uploader: { id: 1, username: "smoke", handle: "smoke", displayName: "Smoke" },
  tags: ["微积分", "期末"],
  originalFilename: "notes.pdf",
  mimeType: "application/pdf",
  extension: ".pdf",
  sizeBytes: 11,
  visibility: "members",
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
  descriptionMarkdown: "覆盖极限、导数与应用题。",
  can: { update: true, delete: false },
};

// Stage 4 起弹窗自己拉一次 /api/files/config 来填分类/可见性/状态的选项（仓库约定：
// 前端不手抄后端白名单）。缺了它这三个下拉会停在"选项加载失败"，弹窗也就发不出 categoryId。
// Since stage 4 the dialog fetches /api/files/config itself to fill the category, visibility and
// status options (the repository's rule: the client never hand-copies a backend allow-list). Without
// it those three selects sit on "options failed to load" and the dialog cannot send a categoryId.
const CONFIG = {
  categories: [
    { id: 1, slug: "guide", name: "Guide", description: "", kind: "guide", sortOrder: 0, isSystem: true, resourceCount: 3 },
    { id: 2, slug: "exam", name: "Exam", description: "", kind: "exam", sortOrder: 1, isSystem: false, resourceCount: 0 },
  ],
  folders: [],
  tagCloud: [],
  stats: { resourceCount: 3, categoryCount: 2 },
  allowedExtensions: [".pdf"],
  maxUploadBytes: 1024,
  kinds: ["guide", "exam"],
  visibilities: ["public", "members", "private"],
  statuses: ["published", "archived"],
  sorts: ["latest"],
  maxTags: 8,
};

const inputs = {
  ...React,
  Dialog: ({ children, title }) => React.createElement("div", null, React.createElement("h2", null, title), children),
  useI18n: () => ({ t, locale: "en" }),
  ApiError: ApiErrorStub,
  api: {
    files: {
      config: async () => CONFIG,
      update: async (id, body) => {
        calls.update.push([id, body]);
        if (saveFails) throw new ApiErrorStub(422, { code: "TOO_LONG", message: SERVER_REASON });
        // 模拟服务端归一化：去重、转小写——界面必须用响应体回填，而不是回填本地输入。
        // The server's normalisation is simulated (de-duplicate, lower-case): the interface has to
        // write the response back rather than echo the local input.
        const normalised = [...new Set((body.tags ?? []).map((tag) => tag.toLowerCase()))];
        return {
          ...DETAIL,
          title: body.title ?? DETAIL.title,
          tags: normalised,
          descriptionMarkdown: body.descriptionMarkdown ?? "",
        };
      },
    },
  },
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

// 负对照：不拆分标签输入，整串当作一个标签发出去——用户在界面里明明写了两个标签，
// 存进去却是一个。这正是"标签编辑看起来能用、实际不对"的失败形态。
// Negative control: the tag input is not split, so the whole string goes out as one tag. The user
// typed two tags and one was stored, which is exactly how "tag editing looks like it works but does
// not" shows up.
const NO_SPLIT_PATCH = {
  from: `        tags: tags.split(/[,，、;；]+/).map((entry) => entry.trim()).filter(Boolean),`,
  to: `        tags: [tags],`,
};

// 负对照：把"保存失败要显示出来"整段剥掉，失败就会静默。
// Negative control: stripping the "show the save failure" path makes a failure silent.
const SILENT_FAILURE_PATCH = {
  from: `      if (caught instanceof ApiError && caught.message) setError(caught.message);
      else setError(t("file.metaSaveFail"));`,
  to: `      void caught;`,
};

let saved = [];
async function mountDialog(patch) {
  const root = document.createElement("div");
  root.id = "root";
  document.body.appendChild(root);
  const Component = compile("file-metadata-dialog.tsx", patch).FileMetadataDialog;
  const container = createRoot(root);
  await React.act(async () => {
    container.render(React.createElement(React.StrictMode, null, React.createElement(Component, {
      detail: DETAIL,
      onClose: () => {},
      onSaved: (value) => { saved.push(value); },
    })));
  });
  await React.act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
  return container;
}

const text = () => document.getElementById("root").textContent;
const inputsList = () => [...document.querySelectorAll("#root input, #root textarea")];
const buttonWith = (label) => [...document.querySelectorAll("#root button")].find((element) => element.textContent === label);

async function setValue(element, value) {
  const prototype = element.tagName === "TEXTAREA" ? dom.window.HTMLTextAreaElement.prototype : dom.window.HTMLInputElement.prototype;
  await React.act(async () => {
    Object.getOwnPropertyDescriptor(prototype, "value").set.call(element, value);
    element.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
    await new Promise((resolve) => setTimeout(resolve, 10));
  });
}

async function submit() {
  await React.act(async () => {
    document.querySelector("#root form").dispatchEvent(new dom.window.Event("submit", { bubbles: true, cancelable: true }));
    await new Promise((resolve) => setTimeout(resolve, 30));
  });
}

function reset() {
  calls.update = [];
  saved = [];
  saveFails = false;
  document.getElementById("root")?.remove();
}

// ---------------------------------------------------------------- 用例 1：预填
// 打开编辑必须看到当前值，否则"保存"会把用户没看见的内容覆盖掉。
// Opening the editor has to show the current values, or "save" would overwrite content the user
// never saw.
async function prefillCase() {
  const container = await mountDialog();
  try {
    const [titleInput, tagsInput, descriptionInput] = inputsList();
    assert.ok(titleInput && tagsInput && descriptionInput,
      "the dialog must render a title input, a tag input and a description input");
    assert.equal(titleInput.value, "高等数学提纲", "the title input must be prefilled with the current title");
    assert.equal(tagsInput.value, "微积分, 期末", "the tag input must be prefilled with the current tags");
    assert.equal(descriptionInput.value, "覆盖极限、导数与应用题。", "the description must be prefilled");
    // 分区、可见性与状态三个下拉的选项来自 config，必须能被选。
    const selects = [...document.querySelectorAll("#root select")];
    assert.equal(selects.length, 3, "the dialog must offer category, visibility and status selects");
    assert.ok(selects.every((element) => !element.disabled),
      "the selects must be enabled once the config has loaded");
    assert.equal(selects[0].value, "1", "the category select must be preselected to the current category");
    assert.equal(selects[1].value, "members", "the visibility select must be preselected to the current visibility");
    assert.equal(selects[2].value, "published", "the status select must be preselected to the current status");
  } finally {
    await React.act(async () => { container.unmount(); });
    reset();
  }
}

// ---------------------------------------------------------------- 用例 2：保存载荷
async function saveCase(withNegativeControl) {
  const container = await mountDialog(withNegativeControl ? NO_SPLIT_PATCH : undefined);
  try {
    const [titleInput, tagsInput, descriptionInput] = inputsList();
    // 与后端同一套分隔符：半角逗号、全角逗号、顿号、分号都算分隔，首尾空白要去掉。
    // The same separators the server accepts, with surrounding whitespace trimmed.
    await setValue(titleInput, "  线性代数提纲  ");
    await setValue(tagsInput, " 线性代数 ，复习、期末； ");
    await setValue(descriptionInput, "新的简介。");
    await submit();

    if (withNegativeControl) {
      assert.deepEqual(calls.update, [[42, {
        title: "线性代数提纲",
        tags: [" 线性代数 ，复习、期末； "],
        descriptionMarkdown: "新的简介。",
        categoryId: 1,
        visibility: "members",
        status: "published",
      }]], "negative control: without splitting, the whole string goes out as a single tag");
      return;
    }
    assert.deepEqual(calls.update, [[42, {
      title: "线性代数提纲",
      tags: ["线性代数", "复习", "期末"],
      descriptionMarkdown: "新的简介。",
      categoryId: 1,
      visibility: "members",
      status: "published",
    }]], "saving must send the trimmed title, one tag per entry, the description and the section");
    assert.equal(saved.length, 1, "the caller must be handed the saved resource");
    // 回填的是**响应体**（服务端归一化后的结果），不是本地输入。
    // What comes back is the *response* (the server's normalised result), not the local input.
    assert.match(text(), /file\.metaSaved/, "a successful save must be reported");
  } finally {
    await React.act(async () => { container.unmount(); });
    reset();
  }
}

// ---------------------------------------------------------------- 用例 2b：分区/可见性/状态进入载荷
// 管理员点名要改的"分区属性"必须真的进得了保存载荷——只渲染一个下拉、值却发不出去，
// 正是"看起来能改、实际改不了"的失败形态。
// The "section" the administrator named has to reach the save payload: rendering a select whose
// value never goes out is exactly the "looks editable, is not" failure.
async function sectionCase() {
  const container = await mountDialog();
  try {
    const [, , descriptionInput] = inputsList();
    await setValue(descriptionInput, "覆盖极限、导数与应用题。");
    const selects = [...document.querySelectorAll("#root select")];
    // 直接改 select 的 value 并派发 change：与用户在下拉里选一项等价。
    // Setting the select's value and dispatching change is equivalent to picking an option.
    for (const [element, value] of [[selects[0], "2"], [selects[1], "public"], [selects[2], "archived"]]) {
      await React.act(async () => {
        Object.getOwnPropertyDescriptor(dom.window.HTMLSelectElement.prototype, "value").set.call(element, value);
        element.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
        await new Promise((resolve) => setTimeout(resolve, 10));
      });
    }
    await submit();
    assert.equal(calls.update.length, 1, "the save must have been sent");
    const [, body] = calls.update[0];
    assert.equal(body.categoryId, 2, "the chosen section (category) must reach the payload");
    assert.equal(body.visibility, "public", "the chosen visibility must reach the payload");
    assert.equal(body.status, "archived", "the chosen status must reach the payload");
  } finally {
    await React.act(async () => { container.unmount(); });
    reset();
  }
}

// ---------------------------------------------------------------- 用例 2c：文件名必填
// 空文件名会被服务端 422；就地拦下并说清楚，而不是白跑一趟请求。
// A blank title comes back as a 422; catching it in place with a plain explanation beats a wasted
// round trip.
async function blankTitleCase() {
  const container = await mountDialog();
  try {
    const [titleInput] = inputsList();
    await setValue(titleInput, "   ");
    await submit();
    assert.deepEqual(calls.update, [], "a blank title must not be sent to the server at all");
    const alert = document.querySelector("#root [role=\"alert\"]");
    assert.ok(alert, "a blank title must be reported");
    assert.match(alert.textContent, /file\.titleRequired/, "the message must say the title is required");
  } finally {
    await React.act(async () => { container.unmount(); });
    reset();
  }
}

// ---------------------------------------------------------------- 用例 3：清空
// 空标签与空简介必须作为"清空"发出去（而不是当成"没填"被省略）。
// Empty tags and an empty description must go out as a clear rather than being omitted as "unset".
async function clearCase() {
  const container = await mountDialog();
  try {
    const [, tagsInput, descriptionInput] = inputsList();
    await setValue(tagsInput, "");
    await setValue(descriptionInput, "");
    await submit();
    assert.deepEqual(calls.update, [[42, {
      title: "高等数学提纲",
      tags: [],
      descriptionMarkdown: "",
      categoryId: 1,
      visibility: "members",
      status: "published",
    }]], "clearing both fields must send empty values rather than dropping the keys");
  } finally {
    await React.act(async () => { container.unmount(); });
    reset();
  }
}

// ---------------------------------------------------------------- 用例 4：失败必须可见
async function failureVisibleCase(withNegativeControl) {
  saveFails = true;
  const container = await mountDialog(withNegativeControl ? SILENT_FAILURE_PATCH : undefined);
  try {
    await submit();
    if (withNegativeControl) {
      assert.equal(document.querySelector("#root [role=\"alert\"]"), null,
        "negative control: a swallowed failure shows no alert at all");
      assert.doesNotMatch(text(), new RegExp(SERVER_REASON),
        "negative control: with the failure handling stripped the server reason never reaches the UI");
      return;
    }
    const alert = document.querySelector("#root [role=\"alert\"]");
    assert.ok(alert, "a failed save must show an alert");
    assert.match(alert.textContent, new RegExp(SERVER_REASON),
      "the server's own reason must be shown rather than a generic message");
  } finally {
    await React.act(async () => { container.unmount(); });
    reset();
  }
}

(async () => {
  await prefillCase();
  await saveCase(false);
  await saveCase(true);
  await sectionCase();
  await blankTitleCase();
  await clearCase();
  await failureVisibleCase(false);
  await failureVisibleCase(true);
  console.log("file-metadata-smoke: ok");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
