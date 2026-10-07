"""本地磁盘对象存储 + HMAC presign — 镜像 infrastructure/storage/local.ts。

URL pathname 语义与 S3 presigned 对齐；签名 = HMAC-SHA256("{method}|{pathname}|{expires}") hex。
"""

from __future__ import annotations

import hmac
import os
import re
import time
import uuid
from typing import TypedDict

ALLOWED_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".avif",
    ".pdf",
    ".txt",
    ".md",
    ".csv",
    ".zip",
    ".rar",
    ".7z",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".mp4",
    ".mov",
    ".mp3",
    ".wav",
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

# presign 时客户端可声明的 Content-Type 白名单已删除：以扩展名为准，
# 服务端一律按 objectKey 扩展名推导（content_type_for_object_key），见 M10。


def content_type_for_object_key(object_key: str) -> str:
    """按 objectKey 扩展名推导安全 Content-Type，未知一律 octet-stream。"""
    ext = os.path.splitext(object_key)[1].lower()
    return MIME_BY_EXTENSION.get(ext, "application/octet-stream")


# 对象键形如 "{uuid}/{文件名}"。文件名段允许任意非 "/" 字符（含 "%"），不额外设限：
# 经实验核实（见 backend/.pytmp-verify/probe_decoding.py 的判定实验），真实 uvicorn 与 httpx 的
# ASGITransport **都只对 URL 路径解码一次**，因此文件名里的字面 "%20" 经一次百分号编码后
# （变成 "%2520"）能被原样还原，签名校验不受影响。
# 唯一会双重解码的是 Starlette 的 TestClient（它执行 unquote(url.path)，而 url.path 已被 httpx
# 解码过）——那是测试工具的假象，不是产品行为，测试应改用 httpx.ASGITransport 而不是收紧产品约束。
# An object key looks like "{uuid}/{filename}". The filename segment accepts any non-"/" character
# including "%", with no extra restriction: an experiment (see the decoding probe in
# backend/.pytmp-verify/probe_decoding.py) confirmed that both real uvicorn and httpx's
# ASGITransport decode a URL path exactly once, so a literal "%20" in a filename survives after a
# single percent-encoding pass (becoming "%2520") and signature verification is unaffected. The only
# component that decodes twice is Starlette's TestClient, which runs unquote(url.path) on a path
# httpx already decoded; that is a test-tool artefact rather than product behaviour, and tests
# should switch to httpx.ASGITransport instead of tightening the product constraint.
OBJECT_KEY_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/[^/]{1,255}$")

MAX_UPLOAD_BYTES = 50 * 1024 * 1024


class UploadTarget(TypedDict):
    url: str
    method: str
    headers: dict[str, str]


def sanitize_filename(name: str) -> str:
    base = re.sub(r'[\\/:*?"<>|]', "_", name)
    base = re.sub(r"\s+", "_", base)[:80]
    return base or "file"


from ..core.errors import bad_request


def _now_sec() -> int:
    return int(time.time())


class Storage:
    def __init__(self, root_dir: str, secret: str) -> None:
        self.root = root_dir
        self.secret = secret

    def _sign(self, method: str, pathname: str, expires: str) -> str:
        return hmac.new(self.secret.encode(), f"{method}|{pathname}|{expires}".encode(), "sha256").hexdigest()

    def _abspath(self, object_key: str) -> str:
        # realpath 解析掉 `..`/符号链接后再用 commonpath 判定：前缀字符串比较会被
        # `uploads-evil` 这类同前缀目录绕过，commonpath 按路径分量比较才可靠。
        base = os.path.realpath(self.root)
        full = os.path.realpath(os.path.join(base, object_key))
        if os.path.commonpath([base, full]) != base:
            raise PermissionError("Invalid object key")
        return full

    def create_upload_session(self, uploader_id: int, original_filename: str, mime_type: str, size_bytes: int) -> str:
        ext = os.path.splitext(original_filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise bad_request("Unsupported file extension")
        object_key = f"{uuid.uuid4()}/{sanitize_filename(original_filename)}"
        # 目录由 upload 路由在落盘前创建（M16：presign 不再预建，避免空目录堆积）。
        return object_key

    def generate_upload_url(self, object_key: str, content_type: str, expires_in_sec: int = 900) -> UploadTarget:
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

    def sign_path(self, method: str, pathname: str, expires_in_sec: int = 900) -> dict[str, str | int]:
        """对任意 pathname 生成签名地址（附件之外的存储用途复用同一套 HMAC 算法）。

        附件走固定的 /api/attachments/... 路径，所以有 generate_upload_url /
        generate_download_url 两个专用方法；文件服务用的是 /api/files/... 路径，
        需要一条通用的签名入口，避免为此再复制一份 HMAC 逻辑。
        The attachments feature uses fixed /api/attachments/... paths and therefore has the
        two dedicated helpers generate_upload_url / generate_download_url. The file service
        uses /api/files/... paths, so a generic signing entry point is added rather than
        duplicating the HMAC logic once more.
        """
        expires = str(_now_sec() + expires_in_sec)
        sig = self._sign(method, pathname, expires)
        return {
            "url": f"{pathname}?expires={expires}&sig={sig}",
            "pathname": pathname,
            "expires": expires,
            "sig": sig,
            "expiresAt": int(expires) * 1000,
        }

    def delete_object(self, object_key: str) -> None:
        try:
            os.remove(self._abspath(object_key))
        except FileNotFoundError:
            pass

    def path_for(self, object_key: str) -> str:
        return self._abspath(object_key)
