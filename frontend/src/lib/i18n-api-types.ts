// 翻译站 API 类型定义。
// GET {I18N_API_ORIGIN}/catalog/:locale → CatalogResponse
// 供前端 fetch 和翻译站 server（另一 agent 实现）共同使用。

import type { Locale } from "./i18n";
import type { I18nKey } from "./locales/en";

/** GET /catalog/:locale 响应体 */
export type CatalogResponse = {
  locale: Locale;
  /** 翻译字典：key → 译文，缺失 key 由前端 fallback 英文 */
  translations: Partial<Record<I18nKey, string>>;
};

/** 翻译站健康检查响应 GET /health */
export type HealthResponse = {
  ok: true;
  locales: Locale[];
};
