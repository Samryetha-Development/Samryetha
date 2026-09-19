// 主题系统：可扩展的多风格机制。新增风格只需在 THEME_IDS 加一项、
// globals.css 加一段 :root[data-theme="<id>"] token、i18n 加一条标签，
// 组件代码零改动。
// Theme system: extensible multi-style mechanism. Adding a style only needs one
// entry in THEME_IDS, one :root[data-theme="<id>"] token block in globals.css,
// and one i18n label — component code stays untouched.

export type ThemeId = "light" | "dark";
export type ThemeChoice = ThemeId | "system";

// 可用风格 id（可扩展；后续风格在此追加）
// Available style ids (extensible; append future styles here).
export const THEME_IDS: ThemeId[] = ["light", "dark"];

// 本地持久化 key；默认跟随系统。
// localStorage persistence key; default follows the system.
export const THEME_STORAGE_KEY = "samryetha.theme";
export const DEFAULT_THEME_CHOICE: ThemeChoice = "system";

// 深色/浅色的 theme-color meta 值，与 globals.css 的 --bg 保持一致。
// theme-color meta values, kept in sync with --bg in globals.css.
const THEME_COLOR: Record<ThemeId, string> = {
  light: "#f7f8f8",
  dark: "#111416",
};

// 读本地已存选择（非法值回退默认）。
// Read the stored choice (fall back to default on an invalid value).
export function readStoredTheme(): ThemeChoice {
  if (typeof window === "undefined") return DEFAULT_THEME_CHOICE;
  const raw = window.localStorage.getItem(THEME_STORAGE_KEY);
  if (raw === "light" || raw === "dark" || raw === "system") return raw;
  return DEFAULT_THEME_CHOICE;
}

// 写本地选择。
// Persist the choice.
export function storeTheme(choice: ThemeChoice): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(THEME_STORAGE_KEY, choice);
}

// 系统当前是否深色。
// Whether the system currently prefers dark.
export function systemIsDark(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches;
}

// 把选择解析为实际风格（system → 跟随系统）。
// Resolve a choice into the concrete theme id (system follows the OS).
export function resolveTheme(choice: ThemeChoice = readStoredTheme()): ThemeId {
  if (choice === "system") return systemIsDark() ? "dark" : "light";
  return choice;
}

// 把解析结果写到 <html data-theme> 并同步 <meta name="theme-color">。
// Apply the resolved theme to <html data-theme> and sync the theme-color meta.
export function applyTheme(choice: ThemeChoice = readStoredTheme()): ThemeId {
  const id = resolveTheme(choice);
  document.documentElement.dataset.theme = id;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", THEME_COLOR[id]);
  return id;
}

// 监听系统深浅切换（仅 system 态需要实时跟随）。
// Listen for system scheme changes (only relevant for the "system" choice).
export function watchSystemTheme(onChange?: (id: ThemeId) => void): () => void {
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  const handler = () => {
    if (readStoredTheme() === "system") {
      const id = applyTheme("system");
      onChange?.(id);
    }
  };
  mq.addEventListener("change", handler);
  return () => mq.removeEventListener("change", handler);
}
