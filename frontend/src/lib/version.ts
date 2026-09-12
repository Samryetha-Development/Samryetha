import packageJson from "../../package.json";

// 前端 package.json 是 UI 版本号的单一来源。
// frontend/package.json is the single source of truth for the UI version.
export const APP_VERSION = packageJson.version;
export const COPYRIGHT_HOLDER = "Samryetha Development";
export const COPYRIGHT_NOTICE = `© ${COPYRIGHT_HOLDER}`;
