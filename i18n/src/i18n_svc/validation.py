"""locale / key 严格校验工具函数。"""

from __future__ import annotations

import re

# locale：BCP-47 子集：lang[-Script][-REGION]（字母数字+连字符，2–35 字符）
_LOCALE_RE = re.compile(r"^[a-zA-Z]{2,8}(-[a-zA-Z0-9]{2,8})*$")

# key：点分命名空间，各段允许字母/数字/下划线/连字符，1–128 字符
_KEY_RE = re.compile(r"^[a-zA-Z0-9_\-]+(\.[a-zA-Z0-9_\-]+)*$")
_KEY_MAX_LEN = 128


def validate_locale(locale: str, supported: list[str]) -> str:
    """检验 locale 格式并确认在白名单中，返回原值或抛 ValueError。"""
    if not _LOCALE_RE.match(locale):
        raise ValueError(f"Invalid locale format: {locale!r}")
    if locale not in supported:
        raise ValueError(f"Unsupported locale: {locale!r}. Supported: {supported}")
    return locale


def validate_key(key: str) -> str:
    """检验 key 格式，返回原值或抛 ValueError。"""
    if not key or len(key) > _KEY_MAX_LEN:
        raise ValueError(f"Key must be 1–{_KEY_MAX_LEN} characters")
    if not _KEY_RE.match(key):
        raise ValueError(f"Invalid key format: {key!r}. Use dot-separated alphanumeric segments")
    return key
