// Mermaid 图渲染：把服务端产出的 ```mermaid 代码块画成图。
//
// **必须按需加载**：mermaid 解包后 120MB 左右，进主 bundle 会把首屏拖垮。所以这里
// 只在页面上真的出现了 ```mermaid 代码块时才 `import()`，画完即弃（模块本身会被
// Vite 缓存，但不会进主 chunk）。
//
// 失败一律降级成"显示源码"：图挂了不该让整段正文消失，用户至少要能看到自己写的东西。
//
// Browser-only；SSR 时直接返回，不产生任何副作用。

const MERMAID_SELECTOR = 'code[class*="language-mermaid"]';
const MAX_DIAGRAMS = 20;

type MermaidApi = {
  initialize: (config: Record<string, unknown>) => void;
  render: (id: string, source: string) => Promise<{ svg: string }>;
};

// 单例：并发调用只触发一次动态 import。
let loader: Promise<MermaidApi> | null = null;

function loadMermaid(): Promise<MermaidApi> {
  if (!loader) {
    loader = import("mermaid").then((module) => {
      const api = (module.default ?? module) as unknown as MermaidApi;
      const dark = typeof document !== "undefined" && document.documentElement.getAttribute("data-theme") === "dark";
      api.initialize({
        startOnLoad: false,
        // 跟随站点的浅色/深色主题，而不是自带一套配色。
        theme: dark ? "dark" : "neutral",
        securityLevel: "strict",
        fontFamily: "inherit",
        // 图里出现非法语法时抛错，由调用方降级——比画半张图更好排查。
        suppressErrorRendering: true,
      });
      return api;
    });
  }
  return loader;
}

let sequence = 0;

/**
 * 把容器内所有 ```mermaid 代码块替换成渲染后的 SVG。
 * 幂等：已处理过的块带 data-state，重复调用会被跳过。
 */
export async function renderMermaidIn(root: ParentNode): Promise<void> {
  if (typeof window === "undefined") return;
  const blocks = Array.from(root.querySelectorAll<HTMLElement>(MERMAID_SELECTOR)).slice(0, MAX_DIAGRAMS);
  if (blocks.length === 0) return;

  let api: MermaidApi;
  try {
    api = await loadMermaid();
  } catch {
    // mermaid 拉不下来（离线/构建裁剪）：保持源码原样，不报错刷屏。
    return;
  }

  for (const code of blocks) {
    const host = code.closest("pre");
    if (!host || host.dataset.mermaidState) continue;
    const source = code.textContent ?? "";
    if (!source.trim()) continue;
    host.dataset.mermaidState = "pending";
    host.classList.add("mermaid-block");
    try {
      sequence += 1;
      const { svg } = await api.render(`mermaid-${sequence}-${Date.now()}`, source);
      host.innerHTML = svg;
      host.dataset.mermaidState = "ready";
    } catch {
      // 语法错误：留源码 + 标记，样式上给出可读的降级外观。
      host.dataset.mermaidState = "error";
    }
  }
}
