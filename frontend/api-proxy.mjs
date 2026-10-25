// 生产代理的超时策略：纯函数、无副作用，便于单测和复用。
// Timeout policy for the production proxy: a pure, side-effect-free function the tests can drive
// directly.
//
// 普通 API 沿用 30 秒；Office 文档预览（后端把 docx/pptx 转成 PDF）默认有 60 秒、最多 300 秒的
// 转换预算，代理必须比后端更耐心，否则用户在转换完成前就收到 504。
// Ordinary APIs keep 30 seconds. An Office document preview (the backend converts docx/pptx to PDF)
// has a 60-second budget by default and up to 300 seconds configured, so the proxy has to be more
// patient than the backend, or users see a 504 before the conversion finishes.

export const DEFAULT_API_TIMEOUT_MS = 30_000;
// 300 秒上限 + 10 秒余量：让后端的超时先触发，代理只做兜底。
// The 300-second ceiling plus ten seconds of headroom: the backend's timeout fires first and the
// proxy stays a backstop.
export const DOCUMENT_PREVIEW_TIMEOUT_MS = 310_000;

// 只匹配精确的预览路由，id 必须是数字；查询串不参与判定。
// Matches only the exact preview route with a numeric id; the query string plays no part.
const DOCUMENT_PREVIEW_PATH = /^\/api\/files\/resources\/[0-9]+\/document-preview$/;

export function apiTimeoutMs(url) {
  let pathname;
  try {
    // 用 URL 解析只取 pathname：既丢掉查询串，也让 "./.." 之类的路径先归一化。
    // Parsing with URL keeps only the pathname: it drops the query string and normalises dot
    // segments such as "./.." first.
    pathname = new URL(String(url ?? ""), "http://localhost").pathname;
  } catch {
    return DEFAULT_API_TIMEOUT_MS;
  }
  return DOCUMENT_PREVIEW_PATH.test(pathname) ? DOCUMENT_PREVIEW_TIMEOUT_MS : DEFAULT_API_TIMEOUT_MS;
}
