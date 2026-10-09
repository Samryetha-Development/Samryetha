// OIDC 授权响应体收窄的冒烟测试。
// 覆盖两层：合法响应必须原样收窄，畸形响应必须被拒绝（返回 null）而不是被当成业务类型放行。
// Smoke test for OIDC authorize response narrowing.
// Two layers are covered: well-formed responses must narrow unchanged, and malformed responses must
// be rejected (null) instead of being let through as a domain type.

import assert from "node:assert/strict";
import { createServer } from "vite";

const server = await createServer({ server: { middlewareMode: true } });
try {
  const { parseAuthorizeResult, readOAuthErrorMessage, readOAuthPayload } = await server.ssrLoadModule("/src/lib/oauth-response.ts");

  // 旧实现的等价物：`return body as AuthorizeResult;`——任何结构都被原样放行。
  // Stand-in for the previous implementation (`return body as AuthorizeResult;`), which let every
  // shape through untouched.
  const legacyParse = (payload) => payload;

  // ---- 1. 合法响应：四种形态原样收窄 ----
  // ---- 1. Valid responses: all four shapes narrow unchanged ----
  const account = { display_name: "Ada", username: "ada", email: "ada@example.edu.cn" };
  const valid = [
    [{ status: "login_required" }, { status: "login_required" }],
    [{ status: "select_account", account }, { status: "select_account", account }],
    [{ status: "verify_email", email: "ada@example.edu.cn", return_to: "/oauth/authorize?x=1" }, { status: "verify_email", email: "ada@example.edu.cn", return_to: "/oauth/authorize?x=1" }],
    [{ status: "code", redirect: "https://forum.test/api/auth/callback?code=abc&state=xyz" }, { status: "code", redirect: "https://forum.test/api/auth/callback?code=abc&state=xyz" }],
  ];
  for (const [input, expected] of valid) {
    assert.deepEqual(parseAuthorizeResult(input), expected, `Should narrow a valid payload: ${JSON.stringify(input)}`);
  }

  // 多余的未知字段被忽略，不会污染收窄结果。
  // Unknown extra fields are ignored and never leak into the narrowed result.
  assert.deepEqual(
    parseAuthorizeResult({ status: "code", redirect: "/cb?code=1", expires_in: 60, scope: "openid" }),
    { status: "code", redirect: "/cb?code=1" },
  );

  // ---- 2. 畸形响应：必须拒绝；旧实现会把它们放行（红） ----
  // ---- 2. Malformed responses: must be rejected; the legacy path let them through (red) ----
  const malformed = [
    null,
    undefined,
    0,
    1,
    true,
    "code",
    [],
    ["code"],
    {},
    { error: "invalid_request" },
    { status: "unknown_status" },
    { status: "login_required_extra" },
    { status: 1 },
    { status: null },
    { status: "select_account" },
    { status: "select_account", account: null },
    { status: "select_account", account: {} },
    { status: "select_account", account: { display_name: "Ada", username: "ada" } },
    { status: "select_account", account: { display_name: "Ada", username: "ada", email: 7 } },
    { status: "select_account", account: { display_name: 1, username: "ada", email: "a@b.c" } },
    { status: "verify_email", email: "ada@example.edu.cn" },
    { status: "verify_email", return_to: "/oauth/authorize?x=1" },
    { status: "verify_email", email: null, return_to: "/oauth/authorize?x=1" },
    { status: "code" },
    { status: "code", redirect: 1 },
    { status: "code", redirect: null },
  ];
  for (const payload of malformed) {
    // 红对照：旧实现把这些畸形结构原样当成 AuthorizeResult 返回。
    // Red contrast: the legacy implementation handed these malformed shapes back as AuthorizeResult.
    assert.equal(
      legacyParse(payload),
      payload,
      `Legacy behaviour should pass the malformed payload straight through: ${JSON.stringify(payload)}`,
    );
    assert.equal(parseAuthorizeResult(payload), null, `Should reject a malformed payload: ${JSON.stringify(payload)}`);
  }

  // ---- 3. 错误描述：与旧 `||` 回退语义等价 ----
  // ---- 3. Error description: equivalent to the previous `||` fallback semantics ----
  assert.equal(readOAuthErrorMessage({ error_description: "bad credentials" }), "bad credentials");
  assert.equal(readOAuthErrorMessage({ error: "invalid_request" }), "invalid_request");
  assert.equal(readOAuthErrorMessage({ error_description: "", error: "invalid_request" }), "invalid_request");
  assert.equal(readOAuthErrorMessage({ error_description: 0, error: "invalid_request" }), "invalid_request");
  assert.equal(readOAuthErrorMessage({ error_description: "  " }), "  ");
  for (const payload of [null, undefined, "boom", 42, {}, { error: 7 }, { error_description: null }]) {
    assert.equal(readOAuthErrorMessage(payload), undefined, `Should yield no detail: ${JSON.stringify(payload)}`);
  }

  // ---- 4. 响应体读取：非 JSON 不再抛解析异常 ----
  // ---- 4. Body reading: a non-JSON body no longer throws a parse error ----
  assert.deepEqual(await readOAuthPayload(new Response(JSON.stringify({ status: "login_required" }), { status: 200 })), { status: "login_required" });
  assert.equal(await readOAuthPayload(new Response("<html>502</html>", { status: 502 })), null);
  assert.equal(await readOAuthPayload(new Response("", { status: 200 })), null);

  // ---- 5. 端到端口径：畸形响应在组件里走"显式报错"，而不是崩在 new URL(undefined) ----
  // ---- 5. End-to-end stance: a malformed body produces an explicit error, not new URL(undefined) ----
  const authorizeFailure = (payload) => {
    const result = parseAuthorizeResult(payload);
    if (result === null) throw new Error("Unexpected authorization response");
    return result;
  };
  assert.throws(() => authorizeFailure({ status: "code" }), /Unexpected authorization response/);
  assert.deepEqual(authorizeFailure({ status: "code", redirect: "/cb?code=1" }), { status: "code", redirect: "/cb?code=1" });

  console.log("ok   oidc authorize response narrowing (valid narrowed, malformed rejected)");
} finally {
  await server.close();
}
