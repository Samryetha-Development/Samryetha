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

# presign 时客户端可声明的 Content-Type 白名单已删除：以扩展名为准，
# 服务端一律按 objectKey 扩展名推导（content_type_for_object_key），见 M10。


def content_type_for_object_key(object_key: str) -> str:
    """按 objectKey 扩展名推导安全 Content-Type，未知一律 octet-stream。"""
    ext = os.path.splitext(object_key)[1].lower()
    return MIME_BY_EXTENSION.get(ext, "application/octet-stream")


# 对象键必须能被"恰好一层"百分号编码安全地放进 URL 路径。
# 文件名段因此显式排除 "%"：真实上传地址里的路径段要经过百分号编码，而 ASGI 这一栈
# （uvicorn 先解码一次，Starlette 的 path 转换器再 unquote 一次）会解码两次，
# 于是名字里字面的 "%20" 会被还原成空格、"%23" 会被还原成 "#"，服务端重建出的签名输入
# 与签名时用的输入不再相同，合法文件名直接变成无法上传。名字里不含 "%" 时，
# 编码—解码无论做几次结果都一致，签名校验也就与解码层数无关。
# An object key must survive being placed in a URL path with exactly one level of percent
# encoding. The filename segment therefore excludes "%" explicitly: the real upload URL encodes
# its path segment, and the ASGI stack decodes twice (uvicorn decodes once, then Starlette's path
# converter unquotes again), so a literal "%20" in the name turns back into a space and "%23" into
# "#", leaving the server with a signing input different from the one that was signed and making a
# perfectly legitimate filename impossible to upload. With no "%" in the name, encoding and
# decoding are idempotent at any depth, so signature verification no longer depends on how many
# times the stack decodes.
OBJECT_KEY_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/[^/%]{1,255}$"
)

MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def sanitize_filename(name: str) -> str:
    # "%" 与文件系统保留字符一并替换：前者会让 URL 路径段的解码层数影响签名校验，
    # 后者本就不能出现在路径里。用户可见的原始文件名另存于 original_filename，不受影响。
    # "%" is replaced alongside the filesystem-reserved characters: the former makes the number of
    # URL-decoding passes affect signature verification, and the latter cannot appear in a path at
    # all. The user-visible original filename is stored separately in original_filename and is not
    # affected.
    base = re.sub(r'[\\/:*?"<>|%]', "_", name)
    base = re.sub(r"\s+", "_", base)[:80]
    return base or "file"


from .errors import bad_request


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

    def sign_path(self, method: str, pathname: str, expires_in_sec: int = 900) -> dict:
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
