"""算术验证码（captcha）— 登录/注册防暴力破解。

无外部依赖、无状态：HMAC-SHA256 签名 token，5 分钟有效。
question 形如 "3 + 5 = ?"，答案嵌在签名 token 里（不落库）。
注意：token 不是一次性的——同一 token 在有效期内可重复提交，防重放依赖调用方限流。
局限：算术题对确定型机器人可解，作为基础人机校验 + 配合全局限流使用；
更强防护（图像 OCR 抗性 / 第三方 reCAPTCHA）可后续替换。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

CAPTCHA_TTL_SEC = 300  # 5 分钟有效
CAPTCHA_MAX_DIGIT = 9  # 加数范围 1..9，答案 <= 18


def _sign(payload: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def generate_challenge(secret: str) -> dict:
    """返回 {token, question}。token 内签名 answer+expires，客户端提交时回传。"""
    a = secrets.randbelow(CAPTCHA_MAX_DIGIT) + 1
    b = secrets.randbelow(CAPTCHA_MAX_DIGIT) + 1
    answer = str(a + b)
    expires = int(time.time()) + CAPTCHA_TTL_SEC
    sig = _sign(f"{answer}|{expires}", secret)
    raw = f"{answer}.{expires}.{sig}"
    token = base64.urlsafe_b64encode(raw.encode("ascii")).rstrip(b"=").decode("ascii")
    return {"token": token, "question": f"{a} + {b} = ?"}


def verify_challenge(token: str, answer: str, secret: str) -> bool:
    """校验 token 签名、有效期与答案（常数时间比较）。"""
    if not token or not answer:
        return False
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode("ascii")
        ans, exp_s, sig = raw.split(".", 2)
        expires = int(exp_s)
    except Exception:
        return False
    if expires < int(time.time()):
        return False
    if not hmac.compare_digest(_sign(f"{ans}|{exp_s}", secret), sig):
        return False
    return hmac.compare_digest(str(answer).strip(), ans)
