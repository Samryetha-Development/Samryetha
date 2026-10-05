import assert from "node:assert/strict";
import { createServer } from "vite";

const server = await createServer({ server: { middlewareMode: true } });
try {
  const { getLakoRegisterUrl, getClaimReturnPath } = await server.ssrLoadModule("/src/lib/auth.tsx");
  const origin = "https://samryetha.test";
  const target = "/d/42?view=replies&sort=date#reply-7";
  const claim = new URL("/claim", origin);
  claim.searchParams.set("ticket", "claim-ticket");
  claim.searchParams.set("returnTo", target);
  assert.equal(getClaimReturnPath(claim.searchParams.get("returnTo"), origin), target);
  assert.equal(getClaimReturnPath(`${origin}${target}`, origin), target);
  for (const invalid of [null, "", "https://evil.test/", "//evil.test/", "/\\evil.test/", `${origin}//evil.test/`, "/safe/..//evil.test/", "javascript:alert(1)", "https://["]) {
    assert.equal(getClaimReturnPath(invalid, origin), "/", `Rejected return target: ${invalid}`);
  }

  const params = {
    response_type: "code",
    client_id: "forum",
    redirect_uri: `${origin}/api/auth/callback`,
    state: "state+with&reserved=characters",
    nonce: "nonce",
    code_challenge: "challenge",
    code_challenge_method: "S256",
    scope: "openid profile email",
    prompt: "select_account",
  };
  const registration = new URL(getLakoRegisterUrl("https://auth.samryetha.test", params));
  assert.equal(registration.origin, "https://auth.samryetha.test");
  assert.equal(registration.pathname, "/register");
  const continuation = new URL(registration.searchParams.get("return_to"), registration.origin);
  assert.equal(continuation.pathname, "/oauth/authorize");
  assert.deepEqual(Object.fromEntries(continuation.searchParams), params);
  console.log("ok   registration parameters and safe claim return paths");
} finally {
  await server.close();
}
