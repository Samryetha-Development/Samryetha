"""音视频在线播放的传输层契约。

在线播放能不能成立取决于三件事，这个文件逐条钉住 / Online playback rests on three
things, and each one is pinned here:

1. 上传白名单接受媒体扩展名，但仍拒绝浏览器无法原生解码的容器（故意的：宁可不让上传，
   也不给一个"能内联却放不出来"的入口）。
   The upload allow-list accepts media extensions while still rejecting containers browsers
   cannot decode natively — deliberately: better to refuse the upload than to hand out an
   inline URL that silently fails to play.
2. 回源按扩展名给正确的 Content-Type，并且 Content-Disposition 是 inline；非媒体仍然是
   attachment，不会因为这次改动被顺带内联。
   Serving derives the Content-Type from the extension and marks media inline; non-media
   stays attachment so this change cannot widen inline exposure by accident.
3. Range 请求返回 206 + Content-Range + Accept-Ranges —— 拖动进度条的前提。
   Range requests answer 206 with Content-Range and Accept-Ranges, which is what seeking
   depends on.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

# (文件名, 字节, 期望的 Content-Type)
MEDIA_CASES = [
    ("clip.mp4", b"\x00\x00\x00\x18ftypmp42" + b"A" * 64, "video/mp4"),
    ("clip.m4v", b"\x00\x00\x00\x18ftypM4V " + b"B" * 64, "video/x-m4v"),
    ("clip.webm", b"\x1a\x45\xdf\xa3" + b"C" * 64, "video/webm"),
    ("song.mp3", b"ID3" + b"D" * 64, "audio/mpeg"),
    ("song.m4a", b"\x00\x00\x00\x18ftypM4A " + b"E" * 64, "audio/mp4"),
    ("song.flac", b"fLaC" + b"F" * 64, "audio/flac"),
]


def _category_id(api, slug: str = "study-syllabus") -> int:
    r = api.c.get("/api/files/config")
    assert r.status_code == 200, r.text
    for item in r.json()["categories"]:
        if item["slug"] == slug:
            return item["id"]
    raise AssertionError(f"category {slug} not found")


def _publish(api, filename: str, body: bytes) -> dict:
    """按真实文件名走完整上传链路，返回创建后的详情。

    Walks the real upload chain with the real filename and returns the created detail. The
    declared mimeType is intentionally wrong (application/pdf) to prove the server ignores it
    and derives everything from the extension.
    """
    c = api.c
    presign_resp = c.post(
        "/api/files/resources/presign",
        json={"filename": filename, "mimeType": "application/pdf", "sizeBytes": len(body)},
    )
    assert presign_resp.status_code == 200, presign_resp.text
    presign = presign_resp.json()

    upload = c.put(presign["uploadUrl"], content=body)
    assert upload.status_code == 204, upload.text

    query = parse_qs(urlparse(presign["uploadUrl"]).query)
    created = c.post(
        "/api/files/resources",
        json={
            "objectKey": presign["objectKey"],
            "expires": query["expires"][0],
            "sig": query["sig"][0],
            "sizeBytes": len(body),
            "categoryId": _category_id(api),
            "title": f"媒体 {filename}",
            "visibility": "members",
            "tags": [],
            "originalFilename": filename,
            "mimeType": "application/pdf",
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


@pytest.mark.parametrize(("filename", "body", "expected_type"), MEDIA_CASES)
def test_media_is_served_inline_with_derived_content_type(api, filename, body, expected_type):
    """媒体按扩展名给对的 Content-Type，且可内联（否则 <video>/<audio> 拿不到字节）。"""
    api.mkuser("alice", role="admin")
    api.login("alice")
    created = _publish(api, filename, body)

    ticket = api.c.get(f"/api/files/resources/{created['id']}/download?preview=true")
    assert ticket.status_code == 200, ticket.text

    served = api.c.get(ticket.json()["downloadUrl"])
    assert served.status_code == 200, served.text
    # 声明的 mimeType 是 application/pdf，服务端必须无视它、按扩展名推导。
    assert served.headers["content-type"] == expected_type
    assert served.headers["content-disposition"].startswith("inline")
    assert served.content == body


@pytest.mark.parametrize(("filename", "body", "expected_type"), MEDIA_CASES)
def test_media_supports_range_requests(api, filename, body, expected_type):
    """Range → 206 + Content-Range + 4 字节正文；这是拖动进度条的前提。"""
    api.mkuser("alice", role="admin")
    api.login("alice")
    created = _publish(api, filename, body)

    ticket = api.c.get(f"/api/files/resources/{created['id']}/download?preview=true")
    url = ticket.json()["downloadUrl"]

    partial = api.c.get(url, headers={"Range": "bytes=0-3"})
    assert partial.status_code == 206, partial.text
    assert partial.headers["accept-ranges"] == "bytes"
    assert partial.headers["content-range"] == f"bytes 0-3/{len(body)}"
    assert partial.content == body[:4]


def test_media_range_beyond_eof_is_416(api):
    """越界 Range 返回 416，而不是悄悄回整段（播放器会据此判断时长）。"""
    api.mkuser("alice", role="admin")
    api.login("alice")
    created = _publish(api, "clip.mp4", b"\x00\x00\x00\x18ftypmp42" + b"A" * 64)
    ticket = api.c.get(f"/api/files/resources/{created['id']}/download?preview=true")

    resp = api.c.get(ticket.json()["downloadUrl"], headers={"Range": f"bytes=99999-100000"})
    assert resp.status_code == 416, resp.text


def test_archive_stays_attachment(api):
    """非媒体仍然 attachment：这次改动不能顺带把压缩包也内联出去。"""
    api.mkuser("alice", role="admin")
    api.login("alice")
    created = _publish(api, "pack.zip", b"PK\x03\x04" + b"Z" * 32)

    ticket = api.c.get(f"/api/files/resources/{created['id']}/download?preview=true")
    served = api.c.get(ticket.json()["downloadUrl"])
    assert served.status_code == 200, served.text
    assert served.headers["content-disposition"].startswith("attachment")


def test_container_without_native_browser_support_is_not_uploadable(api):
    """.mkv 这类浏览器解不了的容器仍然拒绝上传——不给"能内联但放不出来"的入口。

    要支持它们必须先做转封装/转码（ffmpeg），那是另一件事，不在这一版里悄悄放开。
    """
    api.mkuser("alice", role="admin")
    api.login("alice")
    resp = api.c.post(
        "/api/files/resources/presign",
        json={"filename": "movie.mkv", "mimeType": "video/x-matroska", "sizeBytes": 1024},
    )
    assert resp.status_code == 400, resp.text
    assert "extension" in resp.json()["error"]["message"].lower()
