// OIDC 授权接口的响应体收窄工具。
// 响应体来自跨源 fetch，类型上不可信（response.json() 返回 any），
// 因此这里一律按 unknown 处理并做运行时校验，绝不把 any 直接断言成业务类型。
// Narrowing helpers for the OIDC authorize response body.
// The body comes from a cross-origin fetch and is untrusted at the type level
// (response.json() yields any), so it is handled as unknown and validated at runtime instead of
// being asserted into a domain type.

import type { LakoAccount } from "@lako/ui";

/** JSON authorize 的四种成功形态；判别字段是 status。 */
/** The four successful JSON authorize shapes; the discriminant field is status. */
export type AuthorizeResult =
  | { status: "login_required" }
  | { status: "select_account"; account: LakoAccount }
  | { status: "verify_email"; email: string; return_to: string }
  | { status: "code"; redirect: string };

// 读取响应体并转换为 unknown：非 JSON（例如反代返回的 HTML 错误页）返回 null 而不是抛解析异常。
// Read the response body as unknown: non-JSON payloads (for example an HTML error page served by a
// reverse proxy) yield null instead of throwing a parse error.
export async function readOAuthPayload(response: Response): Promise<unknown> {
  try {
    return (await response.json()) as unknown;
  } catch {
    return null;
  }
}

// 把任意值收窄成普通对象；数组与 null 都视为无效。
// Narrow an arbitrary value into a plain record; arrays and null count as invalid.
function asRecord(value: unknown): Record<string, unknown> | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

// 读取字符串字段；不是字符串（含缺失、数字、null）一律当作没有该字段。
// Read a string field; anything that is not a string (missing, number, null) counts as absent.
function readString(source: Record<string, unknown>, key: string): string | undefined {
  const value = source[key];
  return typeof value === "string" ? value : undefined;
}

// 读取"有内容"的字符串字段：缺失或空串都当作没有，保持旧实现 `||` 的回退语义。
// Read a "meaningful" string field: missing and empty both count as absent, which preserves the
// fallback semantics of the previous `||` expression.
function readNonEmptyString(source: Record<string, unknown>, key: string): string | undefined {
  const value = readString(source, key);
  return value === undefined || value === "" ? undefined : value;
}

// 取 OAuth 风格的错误描述：优先 error_description，其次 error；取不到返回 undefined。
// 调用方负责提供兜底文案，这里不返回空串，避免把"没有错误信息"伪装成一句有效提示。
// Pick the OAuth-style error description: error_description first, then error; undefined when absent.
// The caller owns the fallback text, and an empty string is never returned here so that "no detail"
// is never disguised as a usable message.
export function readOAuthErrorMessage(payload: unknown): string | undefined {
  const record = asRecord(payload);
  if (!record) return undefined;
  return readNonEmptyString(record, "error_description") ?? readNonEmptyString(record, "error");
}

// 校验选号阶段返回的账号对象：三个字段必须齐全且都是字符串。
// Validate the account object returned by the selection stage: all three fields must be present
// strings, matching @lako/ui 的 LakoAccount 契约 / the LakoAccount contract of @lako/ui.
function readAccount(value: unknown): LakoAccount | null {
  const record = asRecord(value);
  if (!record) return null;
  const displayName = readString(record, "display_name");
  const username = readString(record, "username");
  const email = readString(record, "email");
  if (displayName === undefined || username === undefined || email === undefined) return null;
  return { display_name: displayName, username, email };
}

// 按 status 判别字段把响应体收窄成 AuthorizeResult。
// 结构不认识时返回 null，由调用方显式报错，避免把畸形响应带进后续阶段
// （旧实现直接断言，畸形响应会一路走到 new URL(undefined) 才抛 TypeError）。
// Narrow the payload into AuthorizeResult by its status discriminant.
// Unknown shapes return null so the caller fails loudly instead of carrying a malformed response
// into the next stage (the previous implementation asserted blindly, so a malformed response only
// blew up later at `new URL(undefined)` with a TypeError).
export function parseAuthorizeResult(payload: unknown): AuthorizeResult | null {
  const record = asRecord(payload);
  if (!record) return null;
  const status = readString(record, "status");
  if (status === "login_required") return { status: "login_required" };
  if (status === "select_account") {
    const account = readAccount(record["account"]);
    return account === null ? null : { status: "select_account", account };
  }
  if (status === "verify_email") {
    const email = readString(record, "email");
    const returnTo = readString(record, "return_to");
    if (email === undefined || returnTo === undefined) return null;
    return { status: "verify_email", email, return_to: returnTo };
  }
  if (status === "code") {
    const redirect = readString(record, "redirect");
    return redirect === undefined ? null : { status: "code", redirect };
  }
  return null;
}
