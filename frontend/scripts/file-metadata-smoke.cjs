// 元数据编辑冒烟检查（Stage 3：标签与简介可编辑；Stage 4 扩为六个字段）。
// Smoke check for metadata editing (stage 3: editable tags and description; stage 4 widened it to
// six fields).
//
// 无测试框架、无新依赖，沿用本仓库既有套路（jsdom + ts.transpileModule + 注入桩）。
// No test framework and no new dependencies: this follows the repository's existing pattern
// (jsdom + ts.transpileModule + injected stubs).
//
// 负对照一律**行为级**（2026-10-14 改）：此前这里靠**源码逐字片段**做补丁（`source.includes(patch.from)`
// 对不上就抛 "negative control anchor not found"），一次正常重构就会以"锚点找不到"变红，而红的原因
// 与被测行为无关。现在不再改源码字符，改为改**输入或桩的行为**（换一组标签、让接口成功/失败），
// 断言必须跟着变。
// Every negative control is now behavioural (changed 2026-10-14). They used to be source-literal
// patches (`source.includes(patch.from)`, failing with "negative control anchor not found"), so an
// ordinary refactor turned them red for a reason unrelated to the behaviour under test. No source
// characters are touched any more: the *inputs or the stub's behaviour* change instead (a different set
// of tags, an endpoint that succeeds or fails) and the assertions have to follow.
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
const calls = { update: [], get: [] };
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
      // 弹窗在保存前会取一次详情，用来判断"打开之后有没有被别人改过"（见 file-metadata-dialog.tsx）。
      // 桩必须提供它，否则保存会被正确地拦下（取不到现值就不覆盖），用例读到的会是"没有更新"。
      // The dialog reads the detail once before saving, to tell whether anyone changed it after it was
      // opened (see file-metadata-dialog.tsx). The stub has to provide it: without it the save is
      // correctly refused (no current values, no overwrite) and the cases would read "no update".
      get: async (id) => { calls.get.push(id); return DETAIL; },
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

function compile(file) {
  // 统一换行符后再编译：仓库里的 .tsx 是 CRLF，编译不该受它影响。
  // Line endings are normalised before compiling: the repository's .tsx files are CRLF and the compile
  // should not depend on that.
  const source = fs.readFileSync(path.join(__dirname, "../src", file), "utf8").replace(/\r\n/g, "\n").replace(/^import .*;\n/gm, "");
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React } }).outputText;
  const exported = {};
  new Function("exports", "React", ...Object.keys(inputs), output)(exported, React, ...Object.values(inputs));
  return exported;
}

let saved = [];
async function mountDialog() {
  const root = document.createElement("div");
  root.id = "root";
  document.body.appendChild(root);
  const Component = compile("file-metadata-dialog.tsx").FileMetadataDialog;
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
  calls.get = [];
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
// 同一件事跑两组输入（primary / second），载荷必须**跟着输入走**：换一组标签，发出的标签也必须换。
// 这就是原来那个源码补丁负对照想证明的甄别力——把"拆标签"写成常量、或永远回上一次的结果，
// second 这一遍立刻变红——但换成行为级之后，正常重构不再会让它"红得莫名"。
// One behaviour, two input sets (primary and second): the payload has to *follow the input*, so a
// different tag string must produce different tags. This is the discriminating power the old
// source-patch negative control was after - a hard-coded split, or one that always reuses the previous
// result, turns the second pass red - but as a behavioural control an ordinary refactor can no longer
// make it "red for no reason".
async function saveCase(variant) {
  const container = await mountDialog();
  try {
    const [titleInput, tagsInput, descriptionInput] = inputsList();
    // 与后端同一套分隔符：半角逗号、全角逗号、顿号、分号都算分隔，首尾空白要去掉。
    // The same separators the server accepts, with surrounding whitespace trimmed.
    await setValue(titleInput, "  线性代数提纲  ");
    await setValue(descriptionInput, "新的简介。");

    if (variant === "second") {
      await setValue(tagsInput, " a ; b 、c ，d ");
      await submit();
      assert.deepEqual(calls.update, [[42, {
        title: "线性代数提纲",
        tags: ["a", "b", "c", "d"],
        descriptionMarkdown: "新的简介。",
        categoryId: 1,
        visibility: "members",
        status: "published",
      }]], "the payload must follow the input: every separator splits and every tag is trimmed");
      assert.equal(saved.length, 1, "the caller must be handed the saved resource");
      return;
    }

    await setValue(tagsInput, " 线性代数 ，复习、期末； ");
    await submit();
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
// 行为级负对照：提示必须**跟着接口结果走**。成功那一边不许出现 role="alert"——把失败提示做成
// 无条件显示，这一边立刻变红（这正是原来那个"剥掉失败处理"的源码补丁想证明的甄别力）。
// Behavioural negative control: the notice has to *follow the endpoint's outcome*. The success pass
// must show no role="alert" at all, so an unconditional failure banner turns it red - the same
// discriminating power the old "strip the failure handling" source patch was after.
async function failureVisibleCase(expectFailure) {
  saveFails = expectFailure;
  const container = await mountDialog();
  try {
    await submit();
    if (!expectFailure) {
      assert.equal(calls.update.length, 1, "the save must have been sent");
      assert.equal(document.querySelector("#root [role=\"alert\"]"), null,
        "a successful save must not raise an alert");
      assert.match(text(), /file\.metaSaved/, "and it must still report success");
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
  await saveCase("primary");
  await saveCase("second");
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
