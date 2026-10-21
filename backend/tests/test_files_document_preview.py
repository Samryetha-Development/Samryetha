"""Office 文档预览：转换适配器 + 端点契约。

这里用**假转换器**而不是真 LibreOffice，原因有两个 / A fake converter instead of real
LibreOffice, for two reasons:

- 测试不依赖宿主是否装了 soffice，CI 也能跑；
  tests must not depend on whether soffice exists on the host;
- 真正要钉住的是"失败如何降级""缓存是否生效""权限是否守住"，与转换引擎无关。
  what actually needs pinning is degradation, caching and authorization, none of which
  depend on the engine.

真机转换由 smoke 脚本覆盖，不在单测里跑（一次转换要秒级，进测试套件不划算）。
"""

from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from samryetha.adapters.docpreview import (
    ConversionFailed,
    convert_once,
    cache_path,
    is_convertible,
)


class FakeConverter:
    """把源文件原样当成"PDF"返回；可配置为失败。"""

    name = "fake"

    def __init__(self, *, fail: bool = False, payload: bytes = b"%PDF-1.4 fakepdf") -> None:
        self.fail = fail
        self.payload = payload
        self.calls = 0

    def available(self) -> bool:
        return True

    def convert(self, source: Path, out_dir: Path) -> Path | None:
        self.calls += 1
        if self.fail:
            return None
        produced = Path(out_dir) / (Path(source).stem + ".pdf")
        produced.write_bytes(self.payload)
        return produced


# ---------------------------------------------------------------- 适配器单元


def test_convertible_set_covers_office_family():
    for ext in (".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp", ".rtf"):
        assert is_convertible(ext), ext
    # 浏览器能原生渲染的、以及二进制归档都不该走转换
    for ext in (".pdf", ".txt", ".png", ".zip", ".mp4"):
        assert not is_convertible(ext), ext


def test_convert_once_writes_atomically_and_caches(tmp_path):
    source = tmp_path / "a.docx"
    source.write_bytes(b"docx")
    dest = cache_path(str(tmp_path / "cache"), "uuid/a.docx")

    first = FakeConverter()
    out = convert_once(first, source, dest)
    assert out == dest and dest.read_bytes().startswith(b"%PDF-")
    assert first.calls == 1

    # 缓存命中：这次给一个"一被调用就报错"的转换器，证明不会再转。
    class Exploding:
        name = "exploding"

        def available(self) -> bool:
            return True

        def convert(self, source, out_dir):  # noqa: ANN001
            raise AssertionError("cache miss: converted again")

    assert convert_once(Exploding(), source, dest) == dest


def test_failed_conversion_is_remembered_and_not_retried(tmp_path):
    """失败后一小时内不再重试：否则每次浏览都白等一个完整超时。"""
    source = tmp_path / "bad.docx"
    source.write_bytes(b"docx")
    dest = cache_path(str(tmp_path / "cache"), "uuid/bad.docx")

    failing = FakeConverter(fail=True)
    with pytest.raises(ConversionFailed):
        convert_once(failing, source, dest)
    assert failing.calls == 1

    with pytest.raises(ConversionFailed):
        convert_once(failing, source, dest)
    # 只调用过一次：第二次直接命中失败缓存。
    assert failing.calls == 1


def test_failure_cache_expires(tmp_path):
    """过期后允许重试，不会把一份曾经失败的文档永久判死。"""
    source = tmp_path / "later.docx"
    source.write_bytes(b"docx")
    dest = cache_path(str(tmp_path / "cache"), "uuid/later.docx")

    failing = FakeConverter(fail=True)
    with pytest.raises(ConversionFailed):
        convert_once(failing, source, dest)
    marker = dest.with_suffix(".failed")
    assert marker.exists()
    # 把标记改成两小时前
    old = time.time() - 7200
    marker.touch()
    import os

    os.utime(marker, (old, old))

    ok = FakeConverter()
    assert convert_once(ok, source, dest) == dest
    assert ok.calls == 1
    assert not marker.exists(), "成功后应清掉失败标记"


# ---------------------------------------------------------------- 端点契约


def _category_id(api, slug: str = "study-syllabus") -> int:
    r = api.c.get("/api/files/config")
    assert r.status_code == 200, r.text
    for item in r.json()["categories"]:
        if item["slug"] == slug:
            return item["id"]
    raise AssertionError(f"category {slug} not found")


def _publish(api, filename: str, body: bytes = b"docx-bytes", visibility: str = "members") -> dict:
    c = api.c
    presign = c.post(
        "/api/files/resources/presign",
        json={"filename": filename, "mimeType": "application/octet-stream", "sizeBytes": len(body)},
    ).json()
    assert c.put(presign["uploadUrl"], content=body).status_code == 204
    query = parse_qs(urlparse(presign["uploadUrl"]).query)
    created = c.post(
        "/api/files/resources",
        json={
            "objectKey": presign["objectKey"],
            "expires": query["expires"][0],
            "sig": query["sig"][0],
            "sizeBytes": len(body),
            "categoryId": _category_id(api),
            "title": f"文档 {filename}",
            "visibility": visibility,
            "tags": [],
            "originalFilename": filename,
            "mimeType": "application/octet-stream",
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def test_config_reports_capability_consistently(api):
    """config 的 documentPreview 必须与真实状态一致——前端靠它决定要不要挂 iframe。"""
    r = api.c.get("/api/files/config")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["documentPreview"] is (api.app.state.doc_preview is not None and api.app.state.doc_preview.available())
    assert ".docx" in body["convertibleExtensions"]
    assert ".pdf" not in body["convertibleExtensions"]


def test_document_preview_converts_and_serves_pdf(api):
    api.app.state.doc_preview = FakeConverter()
    api.mkuser("alice", role="admin")
    api.login("alice")
    created = _publish(api, "notes.docx")

    resp = api.c.get(f"/api/files/resources/{created['id']}/document-preview")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.headers["content-disposition"] == "inline"
    assert resp.content.startswith(b"%PDF-")


def test_document_preview_404_when_no_converter(api):
    """没有转换器：明确 404，让前端降级成"下载后查看"。"""
    api.app.state.doc_preview = None
    api.mkuser("alice", role="admin")
    api.login("alice")
    created = _publish(api, "notes.docx")

    resp = api.c.get(f"/api/files/resources/{created['id']}/document-preview")
    assert resp.status_code == 404, resp.text


def test_document_preview_rejects_non_convertible_types(api):
    api.app.state.doc_preview = FakeConverter()
    api.mkuser("alice", role="admin")
    api.login("alice")
    created = _publish(api, "pack.zip", b"PK\x03\x04")

    resp = api.c.get(f"/api/files/resources/{created['id']}/document-preview")
    assert resp.status_code == 400, resp.text


def test_document_preview_is_invisible_to_outsiders(api):
    """权限跟资料可见性一致：别人的私有资料连转换都不能触发。

    注意用 private 而不是 members：members 表示"全体已登录成员可读"，那是公开的，
    用 members 写这个用例反而会掩盖越权。
    """
    api.app.state.doc_preview = FakeConverter()
    api.mkuser("alice", role="admin")
    api.login("alice")
    created = _publish(api, "secret.docx", visibility="private")

    api.c.post("/api/auth/logout")
    api.mkuser("bob")
    api.login("bob")
    resp = api.c.get(f"/api/files/resources/{created['id']}/document-preview")
    assert resp.status_code == 404, resp.text


def test_document_preview_failure_degrades_to_404(api):
    """转换失败不抛 500：预览是增强功能，坏文档不该把页面弄崩。"""
    api.app.state.doc_preview = FakeConverter(fail=True)
    api.mkuser("alice", role="admin")
    api.login("alice")
    created = _publish(api, "broken.docx")

    resp = api.c.get(f"/api/files/resources/{created['id']}/document-preview")
    assert resp.status_code == 404, resp.text
