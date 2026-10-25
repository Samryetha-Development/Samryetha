// Office 预览代理超时冒烟：纯函数判定 + 生产代理确实按它取超时。
// Smoke coverage for the Office preview proxy timeout: the pure decision function, plus proof
// that the production proxy actually uses it.
//
// 背景：后端 PREVIEW_TIMEOUT_SECONDS 默认 60、上限 300，而未命中缓存的转换会跑满整个超时。
// 若前端代理沿用普通 API 的 30 秒，用户只会看到 504，而不是转换结果。
// Background: the backend's PREVIEW_TIMEOUT_SECONDS defaults to 60 and allows up to 300, and an
// uncached conversion can use the whole budget. If the frontend proxy kept the ordinary API's 30
// seconds, users would see a 504 instead of the converted document.
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { apiTimeoutMs } from "../api-proxy.mjs";

const root = path.dirname(fileURLToPath(import.meta.url));
const ORDINARY = 30_000;
const DOCUMENT_PREVIEW = 310_000;

assert.equal(apiTimeoutMs("/api/files/resources/42/document-preview"), DOCUMENT_PREVIEW);
assert.equal(apiTimeoutMs("/api/files/resources/42/document-preview?resourceVersion=3"), DOCUMENT_PREVIEW);
assert.equal(
  apiTimeoutMs("https://forum.test/api/files/resources/42/document-preview?token=abc"),
  DOCUMENT_PREVIEW,
);
console.log("ok   the document-preview endpoint (with or without a query string) gets the long timeout");

for (const ordinary of [
  "/api/discussions",
  "/api/discussions?sort=date",
  "/api/files/resources/42/download",
  "/api/files/resources",
  "/api/files/resources/42/download?preview=true",
  // 相似路径不得误匹配：多一段、少一段、非数字 id、大小写不一致、编码过的东西。
  // Similar paths must not match: one more or fewer segment, a non-numeric id, different case,
  // or a percent-encoded spelling.
  "/api/files/resources/42/document-preview-extra",
  "/api/files/resources/42/document-previews",
  "/api/files/resources/42/document-preview/1",
  "/api/files/resources/document-preview",
  "/api/files/resources/abc/document-preview",
  "/api/admin/files/resources/42/document-preview",
  "/api/files/resources/42/DOCUMENT-PREVIEW",
  "/api/files/resources/42/document-preview%20",
  "/api/files/resources/42/document-preview/../download",
  "/api/files/resources/42/document-preview?next=/api/files/resources/1/document-preview-extra",
]) {
  if (ordinary.includes("?next=")) {
    // 查询串里再出现一次相似路径也不影响判定：只看真实 pathname。
    // A look-alike path inside the query string changes nothing: only the real pathname counts.
    assert.equal(apiTimeoutMs(ordinary), DOCUMENT_PREVIEW);
    continue;
  }
  assert.equal(apiTimeoutMs(ordinary), ORDINARY, `ordinary API kept the 30s timeout: ${ordinary}`);
}
assert.equal(apiTimeoutMs(""), ORDINARY);
assert.equal(apiTimeoutMs("not a url"), ORDINARY);
assert.equal(apiTimeoutMs(undefined), ORDINARY);
console.log("ok   ordinary and look-alike paths keep the 30s timeout");

assert.ok(
  DOCUMENT_PREVIEW > 300_000,
  "the proxy budget must exceed the backend's 300s PREVIEW_TIMEOUT_SECONDS ceiling",
);

const serverSource = await fs.readFile(path.join(root, "../server.mjs"), "utf8");
assert.match(serverSource, /from "\.\/api-proxy\.mjs"/, "server.mjs must share the decision function");
assert.match(serverSource, /apiTimeoutMs\(req\.originalUrl\)/, "server.mjs must derive the timeout from the request");
assert.doesNotMatch(serverSource, /setTimeout\(30_000/, "server.mjs must not keep a hardcoded API timeout");
console.log("ok   the production proxy uses the shared timeout decision instead of a hardcoded 30s");
