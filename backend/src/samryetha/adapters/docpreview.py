"""Sandboxed Office conversion, bounded cross-process scheduling and versioned PDF cache."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .document_sandbox import DocumentSandbox, MAX_PDF_BYTES

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


class ConversionBusy(RuntimeError):
    """Another conversion owns this document or all conversion slots are occupied."""


@dataclass(frozen=True)
class PreviewSource:
    object_key: str
    path: Path


class LibreOfficeConverter:
    name = "libreoffice"

    def __init__(self, binary: str = "soffice", timeout_seconds: int = 60) -> None:
        self._sandbox = DocumentSandbox(binary, timeout_seconds)

    def available(self) -> bool:
        return self._sandbox.available()

    def convert(self, source: Path, out_dir: Path) -> Path | None:
        if not self.available():
            raise ConversionUnavailable("LibreOffice sandbox is unavailable")
        try:
            return self._sandbox.convert(source, out_dir)
        except OSError as error:
            raise ConversionUnavailable("LibreOffice sandbox could not start") from error


def build_converter(mode: str, binary: str, timeout_seconds: int) -> DocumentConverter | None:
    if mode not in {"auto", "libreoffice", "off"}:
        raise ValueError("Unknown preview converter")
    if mode == "off":
        return None
    converter = LibreOfficeConverter(binary, timeout_seconds)
    if converter.available():
        return converter
    if mode == "libreoffice":
        logger.warning("Document preview disabled: LibreOffice or its Linux sandbox is unavailable")
    return None


def cache_path(cache_dir: str, object_key: str, version: str = "sandbox-v1") -> Path:
    """Include a deployment revision so pre-sandbox or old-font output is never reused."""
    digest = hashlib.sha256(f"{version}\0{object_key}".encode("utf-8")).hexdigest()
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
        size = produced.stat().st_size
        if not 0 < size <= MAX_PDF_BYTES:
            raise ConversionFailed("invalid or oversized PDF")
        # Each thread/process owns its staging inode, on the destination filesystem.
        descriptor, name = tempfile.mkstemp(dir=destination.parent, suffix=".part")
        os.close(descriptor)
        staging = Path(name)
        try:
            shutil.copyfile(produced, staging)
            os.replace(staging, destination)
        finally:
            staging.unlink(missing_ok=True)
    # Clear the failure marker after a successful retry.
    try:
        _failure_marker(destination).unlink()
    except OSError:
        pass
    return destination


@contextmanager
def _try_lock(path: Path) -> Generator[None]:
    # Separate opens make flock effective across both threads and server processes.
    try:
        import fcntl
    except ImportError as error:
        raise ConversionUnavailable("Preview scheduling requires Linux file locks") from error
    with path.open("a+b") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ConversionBusy("Document preview is busy") from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class PreviewCache:
    def __init__(self, directory: str, *, concurrency: int = 2, version: str = "sandbox-v1",
                 max_bytes: int = 512 * 1024 * 1024) -> None:
        self.directory = Path(directory)
        self.concurrency = concurrency
        self.version = version
        self.max_bytes = max_bytes

    def render(self, converter: DocumentConverter, source: PreviewSource) -> Path:
        destination = cache_path(str(self.directory), source.object_key, f"{self.version}:{converter.name}")
        locks = self.directory / ".locks"
        locks.mkdir(parents=True, exist_ok=True)
        # Fixed stripes bound lock-file/inode growth. A stripe collision gets a retryable 503.
        stripe = int(destination.stem[:8], 16) % 64
        with _try_lock(locks / f"object-{stripe}.lock"):
            if destination.is_file() and destination.stat().st_size > 0:
                return destination
            if _recently_failed(destination):
                raise ConversionFailed("conversion failed recently")
            for slot in range(self.concurrency):
                with ExitStack() as stack:
                    try:
                        stack.enter_context(_try_lock(locks / f"slot-{slot}.lock"))
                    except ConversionBusy:
                        continue
                    return self._render_in_slot(converter, source, destination, locks)
            raise ConversionBusy("All document conversion slots are occupied")

    def _render_in_slot(self, converter: DocumentConverter, source: PreviewSource,
                        destination: Path, locks: Path) -> Path:
        # Conversion runs outside the publication lock; only admission/publication is serial.
        with tempfile.TemporaryDirectory(prefix="docpreview-result-") as temporary:
            try:
                result = convert_once(converter, source.path, Path(temporary) / "result.pdf")
            except ConversionFailed:
                _failure_marker(destination).touch()
                raise
            size = result.stat().st_size
            with _try_lock(locks / "publish.lock"):
                used = sum(path.stat().st_size for path in self.directory.glob("*.pdf"))
                if used + size > self.max_bytes:
                    _failure_marker(destination).touch()
                    raise ConversionFailed("Preview cache is full; download the original instead")
                descriptor, name = tempfile.mkstemp(dir=self.directory, suffix=".part")
                os.close(descriptor)
                staging = Path(name)
                try:
                    shutil.copyfile(result, staging)
                    os.replace(staging, destination)
                    _failure_marker(destination).unlink(missing_ok=True)
                finally:
                    staging.unlink(missing_ok=True)
        return destination
