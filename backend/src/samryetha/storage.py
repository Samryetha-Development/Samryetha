"""本地磁盘对象存储 + HMAC presign — 镜像 infrastructure/storage/local.ts。

URL pathname 语义与 S3 presigned 对齐；签名 = HMAC-SHA256("{method}|{pathname}|{expires}") hex。
"""

from __future__ import annotations

import hmac
import os
import re
import time
import uuid
from pathlib import Path

ALLOWED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif",
    ".pdf", ".txt", ".md", ".csv",
    ".zip", ".rar", ".7z",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".mp4", ".mov", ".mp3", ".wav",
}

# 扩展名 → 服务端 Content-Type：附件回源时一律按扩展名推导，绝不信任客户端/入库声明的
# mimeType（否则 text/html 之类可被内联渲染 → 存储型 XSS）。镜像 infra/storage/local.ts。
MIME_BY_EXTENSION = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/plain",
    ".csv": "text/csv",
    ".zip": "application/zip",
    ".rar": "application/vnd.rar",
    ".7z": "application/x-7z-compressed",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
}

# presign 时客户端可声明的 Content-Type 白名单（不含 text/html 等可执行/内联类型）。
ALLOWED_MIME_TYPES = set(MIME_BY_EXTENSION.values())


def content_type_for_object_key(object_key: str) -> str:
    """按 objectKey 扩展名推导安全 Content-Type，未知一律 octet-stream。"""
    ext = os.path.splitext(object_key)[1].lower()
    return MIME_BY_EXTENSION.get(ext, "application/octet-stream")


OBJECT_KEY_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/[^/]{1,255}$")

MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def sanitize_filename(name: str) -> str:
    base = re.sub(r'[\\/:*?"<>|]', "_", name)
    base = re.sub(r"\s+", "_", base)[:80]
    return base or "file"


from .errors import internal_error


def _now_sec() -> int:
    return int(time.time())


class Storage:
    def __init__(self, root_dir: str, secret: str) -> None:
        self.root = root_dir
        self.secret = secret

    def _sign(self, method: str, pathname: str, expires: str) -> str:
        return hmac.new(self.secret.encode(), f"{method}|{pathname}|{expires}".encode(), "sha256").hexdigest()

    def _abspath(self, object_key: str) -> str:
        base = os.path.abspath(self.root)
        full = os.path.abspath(os.path.join(base, object_key))
        if not full.startswith(base + os.sep):
            raise PermissionError("Invalid object key")
        return full

    def create_upload_session(self, uploader_id: int, original_filename: str, mime_type: str, size_bytes: int) -> str:
        ext = os.path.splitext(original_filename)[1].lower()
        if ext and ext not in ALLOWED_EXTENSIONS:
            # 复刻 TS bug：白名单外扩展名抛普通 Error → 500
            raise internal_error()
        object_key = f"{uuid.uuid4()}/{sanitize_filename(original_filename)}"
        os.makedirs(os.path.join(self.root, os.path.dirname(object_key)), exist_ok=True)
        return object_key

    def generate_upload_url(self, object_key: str, content_type: str, expires_in_sec: int = 900) -> dict:
        expires = str(_now_sec() + expires_in_sec)
        pathname = f"/api/attachments/upload/{object_key}"
        sig = self._sign("PUT", pathname, expires)
        return {
            "url": f"{pathname}?expires={expires}&sig={sig}",
            "method": "PUT",
            "headers": {"content-type": content_type},
        }

    def generate_download_url(self, object_key: str, expires_in_sec: int = 3600) -> str:
        expires = str(_now_sec() + expires_in_sec)
        pathname = f"/api/attachments/serve/{object_key}"
        sig = self._sign("GET", pathname, expires)
        return f"{pathname}?expires={expires}&sig={sig}"

    def verify_signature(self, method: str, pathname: str, expires: str, sig: str) -> bool:
        if not re.fullmatch(r"\d+", expires) or int(expires) < _now_sec():
            return False
        expected = self._sign(method, pathname, expires)
        try:
            return hmac.compare_digest(expected, sig)
        except Exception:
            return False

    def delete_object(self, object_key: str) -> None:
        try:
            os.remove(self._abspath(object_key))
        except FileNotFoundError:
            pass

    def path_for(self, object_key: str) -> str:
        return self._abspath(object_key)
