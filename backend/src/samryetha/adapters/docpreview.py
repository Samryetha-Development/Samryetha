"""Office 文档 → PDF 的预览转换。

为什么要先转 PDF / Why convert to PDF first
--------------------------------------------
浏览器只有一个**稳定、无需插件**的文档渲染器：它内建的 PDF 阅读器。把 Office 文档先
转成 PDF，前端就只需要一个 `<iframe>`，不必为 docx/xlsx/pptx 各引一套 JS 渲染器（覆盖
不全、样式漂移），也不必把文件送去第三方在线预览服务——那要求文件有一个**公网可达**的
URL，等于绕过本项目的权限模型。

浏览器内建 PDF 阅读器能力足够：翻页、缩放、目录、文本选择、打印都有，移动端也支持。

转换器可替换 / The converter is swappable
----------------------------------------
本模块定义 `DocumentConverter` 协议和 `LibreOfficeConverter` 实现，由 `build_converter()`
按配置选择。以后要换成 ONLYOFFICE 的 converter 服务，只要再写一个实现并在这里选，前端、
权限、缓存都不动——"谁负责转换"和"怎么展示"是解耦的。

缓存 / Cache
------------
`object_key` 形如 `{uuid}/{filename}`，每次上传都是新的 UUID，所以**对象内容不可变**，
缓存以 `sha256(object_key)` 为键就永远不会过期，不需要失效逻辑。写入用临时文件 + 原子
rename，因此并发转换同一文档最坏只是重复做功，不会产出半个文件。

安全 / Security
---------------
只转换白名单内的后缀；子进程带超时；每次调用使用**独立的 LibreOffice 用户配置目录**
（`-env:UserInstallation`），否则并发调用会争用同一个 profile 而失败。转换不可信文档
本身是有攻击面的，这里靠"超时 + 独立 profile + 不落任何可执行产物"收敛，而不是假装它
不存在。
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

# 允许转 PDF 的后缀：Office 全系（含旧格式与 ODF）+ RTF。
# 这些格式浏览器都不能原生渲染，而 LibreOffice 都能读。
CONVERTIBLE_EXTENSIONS = frozenset({
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".odt", ".ods", ".odp", ".rtf",
})


def is_convertible(extension: str) -> bool:
    return extension.lower() in CONVERTIBLE_EXTENSIONS


class ConversionUnavailable(RuntimeError):
    """没有可用的转换器（未安装或已关闭）。调用方应降级为"下载后查看"。"""


class ConversionFailed(RuntimeError):
    """转换器在跑，但这份文档没转出来（损坏、加密、超时）。"""


class DocumentConverter(Protocol):
    name: str

    def available(self) -> bool: ...

    def convert(self, source: Path, out_dir: Path) -> Path | None: ...


class LibreOfficeConverter:
    """用 `soffice --headless --convert-to pdf` 转换。"""

    name = "libreoffice"

    def __init__(self, binary: str = "soffice", timeout_seconds: int = 60) -> None:
        self._binary = binary
        self._timeout = timeout_seconds

    def available(self) -> bool:
        return shutil.which(self._binary) is not None

    def convert(self, source: Path, out_dir: Path) -> Path | None:
        if not self.available():
            raise ConversionUnavailable(f"{self._binary} not found on PATH")
        # 每次调用一个独立 profile，避免并发转换争用 ~/.config/libreoffice 而报错。
        # A per-call profile: concurrent conversions otherwise fight over one config dir.
        profile = out_dir / "lo-profile"
        profile.mkdir(parents=True, exist_ok=True)
        cmd = [
            self._binary,
            "--headless",
            "--norestore",
            "--nolockcheck",
            "--nodefault",
            f"-env:UserInstallation=file://{profile}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out_dir),
            str(source),
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=self._timeout,
                check=False,
                # 不给它 stdin，避免某些版本在无 tty 环境下等待输入。
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            logger.warning("[docpreview] conversion timed out after %ss: %s", self._timeout, source.name)
            return None
        produced = out_dir / (source.stem + ".pdf")
        if proc.returncode != 0 or not produced.exists():
            logger.warning(
                "[docpreview] conversion failed (rc=%s) for %s: %s",
                proc.returncode,
                source.name,
                (proc.stderr or b"")[-400:].decode("utf-8", "replace"),
            )
            return None
        return produced


def build_converter(mode: str, binary: str, timeout_seconds: int) -> DocumentConverter | None:
    """按配置选转换器。返回 None 表示"不做文档预览"，调用方据此降级。

    mode / 模式:
      - "auto"        : 找得到 soffice 就启用，找不到返回 None（静默降级）
      - "libreoffice" : 强制启用；找不到时返回一个 available() 为 False 的实例，
                        让启动日志能明确说明"配置了但不可用"（不静默通过）
      - "off"         : 关闭
    """
    if mode == "off":
        return None
    converter = LibreOfficeConverter(binary=binary, timeout_seconds=timeout_seconds)
    if mode == "libreoffice":
        return converter
    # auto
    return converter if converter.available() else None


def cache_path(cache_dir: str, object_key: str) -> Path:
    """对象键不可变 → 以它的哈希为键，天然不会读到旧内容。"""
    digest = hashlib.sha256(object_key.encode("utf-8")).hexdigest()
    return Path(cache_dir) / f"{digest}.pdf"


# 失败缓存的有效期：损坏/加密/超时过的文档在一小时内不再重试。
# 没有它，一个转不出来的文档会让**每一次**浏览都白等一个完整超时（最长
# PREVIEW_TIMEOUT_SECONDS），既是糟糕的体验也是一条廉价的资源耗尽路径。
# How long a failed conversion is remembered. Without this, a document that never converts
# makes every single view wait out the full timeout — a bad experience and a cheap way to
# burn the worker.
FAILURE_TTL_SECONDS = 3600


def _failure_marker(destination: Path) -> Path:
    return destination.with_suffix(".failed")


def _recently_failed(destination: Path) -> bool:
    marker = _failure_marker(destination)
    try:
        age = time.time() - marker.stat().st_mtime
    except OSError:
        return False
    return age < FAILURE_TTL_SECONDS


def convert_once(
    converter: DocumentConverter,
    source: Path,
    destination: Path,
) -> Path:
    """把 source 转成 destination 处的 PDF（有缓存就复用）。

    失败抛 ConversionFailed / ConversionUnavailable，调用方降级为"下载后查看"。
    失败会被记住一小时，避免对同一份坏文档反复触发完整超时的转换。
    Raises ConversionFailed / ConversionUnavailable; callers degrade to download-to-view.
    Failures are remembered for an hour so one broken document cannot make every view pay
    the full conversion timeout.
    """
    if destination.exists() and destination.stat().st_size > 0:
        return destination
    if _recently_failed(destination):
        raise ConversionFailed("conversion failed recently; not retrying yet")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="docpreview-") as tmp:
        produced = converter.convert(source, Path(tmp))
        if produced is None:
            _failure_marker(destination).touch()
            raise ConversionFailed("converter produced no PDF")
        # 原子落盘：并发转换同一文档时最坏是重复做功，不会留下半个文件。
        # Atomic publish: concurrent conversions may duplicate work but never leave a
        # half-written PDF behind.
        staging = destination.with_suffix(f".{os.getpid()}.part")
        shutil.copyfile(produced, staging)
        os.replace(staging, destination)
    # 成功了就把失败标记清掉（Content 变了/环境修好了都能立刻恢复）。
    try:
        _failure_marker(destination).unlink()
    except OSError:
        pass
    return destination
