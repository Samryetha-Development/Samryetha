// SSR 冒烟检查 —— 无测试框架、无新依赖，约 1 秒。
//
// 守住两件事：
//  1. 任一页面在服务端渲染时抛错（比如某个组件把 window 用在了渲染路径上）。
//  2. 弹层在服务端被渲染出来。手写 overlay 是普通 div，会真的进 SSR 输出，所以首屏
//     出现 .dialog-overlay 就意味着某个弹层的初始状态是展开的。
//     注意这条**抓不到** Radix 弹层：它的 Portal 在服务端不产出节点，客户端再追加，
//     既不进输出也不会造成 hydrate 不匹配。它防的是手写弹层（迁移期的存量）与未来误用。
//
// 用法：先 `pnpm build`，再 `node scripts/ssr-smoke.mjs`。
import { render } from "../dist/server/entry-server.js";

const CASES = [
  ["/", () => render("/")],
  ["/feedback", () => render("/feedback")],
  ["/admin", () => render("/admin")],
  ["/settings", () => render("/settings")],
  ["/inbox", () => render("/inbox")],
  ["/profile", () => render("/profile")],
  ["/login", () => render("/login")],
  ["/login zh-CN", () => {
    const html = render("/login", "zh-CN");
    if (!html.includes("欢迎回来")) throw new Error("Chinese locale did not render from the bundled catalog");
    return html;
  }],
  ["/login en", () => {
    const html = render("/login", "en");
    if (!html.includes("Welcome back")) throw new Error("English locale did not render from the bundled catalog");
    return html;
  }],
  ["/login zh-TW fallback", () => {
    const html = render("/login", "zh-TW");
    if (!html.includes("欢迎回来")) throw new Error("Legacy Chinese locale did not fall back to Simplified Chinese");
    return html;
  }],
  ["/login fr fallback", () => {
    const html = render("/login", "fr");
    if (!html.includes("Welcome back")) throw new Error("Unsupported locale did not fall back to English");
    return html;
  }],
];

let failed = 0;
for (const [name, run] of CASES) {
  try {
    const html = run();
    if (typeof html !== "string" || html.length < 200) {
      throw new Error(`输出可疑地短（${html?.length ?? 0} 字符）`);
    }
    if (html.includes("dialog-overlay")) {
      throw new Error("服务端渲染出了 .dialog-overlay —— 对话框的 open 初始值必须是 false");
    }
    console.log(`ok   ${name.padEnd(10)} ${html.length} 字符`);
  } catch (error) {
    failed += 1;
    console.error(`FAIL ${name}: ${error.message}`);
  }
}

if (failed > 0) {
  console.error(`\n${failed} / ${CASES.length} 个路由未通过`);
  process.exit(1);
}
console.log(`\n${CASES.length} 个路由全部通过`);
