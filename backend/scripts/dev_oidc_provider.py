"""Local-only OIDC provider for exercising Samryetha's login flow.

Run with: uv run python scripts/dev_oidc_provider.py
Never expose this server outside localhost or use it in production.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import secrets
import time
from urllib.parse import parse_qs, urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from joserfc import jwt
from joserfc.jwk import RSAKey

ISSUER = "http://localhost:4000/"
CLIENT_ID = "samryetha-local"
CLIENT_SECRET = "samryetha-local-secret"
REDIRECT_URI = "http://localhost:3000/api/auth/callback"

app = FastAPI(title="Samryetha local OIDC provider")
signing_key = RSAKey.generate_key(auto_kid=True)
pending: dict[str, dict[str, str]] = {}
codes: dict[str, dict[str, str]] = {}

profiles = {
    "student": {
        "sub": "local-student",
        "preferred_username": "oidc_student",
        "name": "OIDC Student",
        "email": "oidc-student@example.edu.cn",
        "groups": ["samryetha-users"],
    },
    "admin": {
        "sub": "local-admin",
        "preferred_username": "oidc_admin",
        "name": "OIDC Admin",
        "email": "oidc-admin@example.edu.cn",
        "groups": ["samryetha-users", "samryetha-admins"],
    },
    "denied": {
        "sub": "local-denied",
        "preferred_username": "oidc_denied",
        "name": "OIDC Denied User",
        "email": "oidc-denied@example.edu.cn",
        "groups": ["unrelated-group"],
    },
}


@app.get("/.well-known/openid-configuration")
def discovery() -> dict:
    return {
        "issuer": ISSUER,
        "authorization_endpoint": ISSUER + "authorize",
        "token_endpoint": ISSUER + "token",
        "jwks_uri": ISSUER + "jwks",
        "end_session_endpoint": ISSUER + "logout",
        "response_types_supported": ["code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "code_challenge_methods_supported": ["S256"],
    }


@app.get("/jwks")
def jwks() -> dict:
    return {"keys": [signing_key.as_dict()]}


@app.get("/authorize", response_class=HTMLResponse)
def authorize(
    client_id: str,
    redirect_uri: str,
    response_type: str,
    state: str,
    nonce: str,
    code_challenge: str,
    code_challenge_method: str,
    scope: str = "",
):
    if (
        client_id != CLIENT_ID
        or redirect_uri != REDIRECT_URI
        or response_type != "code"
        or code_challenge_method != "S256"
        or "openid" not in scope.split()
    ):
        return HTMLResponse("Invalid local OIDC request", status_code=400)
    transaction = secrets.token_urlsafe(24)
    pending[transaction] = {
        "redirect_uri": redirect_uri,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "expires_at": str(int(time.time()) + 600),
    }
    tx = html.escape(transaction, quote=True)
    return HTMLResponse(
        f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <title>Samryetha Local Identity</title>
        <style>body{{margin:0;background:#f6f6f3;color:#242421;font:15px system-ui;display:grid;place-items:center;min-height:100vh}}
        main{{width:min(420px,calc(100% - 36px));background:white;border:1px solid #ddd;border-radius:18px;padding:30px;box-shadow:0 18px 50px #0001}}
        h1{{margin:0 0 8px;font-size:24px}}p{{color:#6d6d67;line-height:1.6}}a{{display:block;margin-top:12px;padding:13px 16px;border-radius:999px;text-align:center;text-decoration:none;background:#252522;color:white}}
        a.alt{{background:#e8e8e2;color:#252522}}a.deny{{background:#fff0f0;color:#a22}}small{{display:block;margin-top:22px;color:#999}}</style></head>
        <body><main><h1>本地 OIDC 身份中心</h1><p>选择一个模拟身份，浏览器将通过 Authorization Code + PKCE 返回论坛。</p>
        <a href="/approve?tx={tx}&profile=student">以普通用户登录</a>
        <a class="alt" href="/approve?tx={tx}&profile=admin">以管理员登录</a>
        <a class="deny" href="/approve?tx={tx}&profile=denied">测试无权限用户</a>
        <small>仅监听本机开发端口 :4000，不保存密码。</small></main></body></html>""",
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@app.get("/approve")
def approve(tx: str, profile: str):
    transaction = pending.pop(tx, None)
    selected = profiles.get(profile)
    if transaction is None or selected is None or int(transaction["expires_at"]) <= int(time.time()):
        return HTMLResponse("Local OIDC transaction expired", status_code=400)
    code = secrets.token_urlsafe(32)
    codes[code] = {**transaction, "profile": profile}
    location = transaction["redirect_uri"] + "?" + urlencode({"code": code, "state": transaction["state"]})
    return RedirectResponse(location, status_code=302, headers={"Cache-Control": "no-store"})


@app.post("/token")
async def token(request: Request):
    authorization = request.headers.get("authorization", "")
    expected = "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    if not hmac.compare_digest(authorization, expected):
        return JSONResponse({"error": "invalid_client"}, status_code=401)
    form = {key: values[0] for key, values in parse_qs((await request.body()).decode()).items()}
    grant = codes.pop(form.get("code", ""), None)
    if grant is None or form.get("grant_type") != "authorization_code" or form.get("redirect_uri") != REDIRECT_URI:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    verifier = form.get("code_verifier", "")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    if not hmac.compare_digest(challenge, grant["code_challenge"]):
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    now = int(time.time())
    selected = profiles[grant["profile"]]
    claims = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "iat": now,
        "exp": now + 300,
        "nonce": grant["nonce"],
        "email_verified": True,
        **selected,
    }
    id_token = jwt.encode({"alg": "RS256", "kid": signing_key.kid}, claims, signing_key, algorithms=["RS256"])
    return {"token_type": "Bearer", "expires_in": 300, "access_token": secrets.token_urlsafe(32), "id_token": id_token}


@app.get("/logout")
def logout(post_logout_redirect_uri: str = "http://localhost:3000/", client_id: str = ""):
    if client_id != CLIENT_ID or post_logout_redirect_uri != "http://localhost:3000/":
        return HTMLResponse("Invalid logout request", status_code=400)
    return RedirectResponse(post_logout_redirect_uri, status_code=302)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=4000, log_level="info")
