"""PR #126 rejection regressions: isolation, concurrency, permissions and capability."""
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
import json
import multiprocessing
import os
import shutil
import socket
import subprocess
import threading
import time

import pytest
from pydantic import ValidationError

from samryetha.adapters import docpreview
from samryetha.adapters.document_sandbox import DocumentSandbox
from samryetha.core.config import Settings
from test_files_document_preview import FakeConverter, _publish


def test_parallel_atomic_publication_uses_distinct_staging_files(tmp_path, monkeypatch):
    source = tmp_path / "notes.docx"
    source.write_bytes(b"docx")
    destination = tmp_path / "cache" / "preview.pdf"
    converted = threading.Barrier(2)
    copied = threading.Barrier(2)
    real_copy = docpreview._copy_converter_output
    stages = []

    class ConcurrentConverter(FakeConverter):
        def convert(self, source, out_dir):
            produced = super().convert(source, out_dir)
            converted.wait(timeout=5)
            return produced

    def synchronized_copy(src, dst):
        result = real_copy(src, dst)
        stages.append(str(dst))
        copied.wait(timeout=5)
        return result

    monkeypatch.setattr(docpreview, "_copy_converter_output", synchronized_copy)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(docpreview.convert_once, ConcurrentConverter(), source, destination) for _ in range(2)]
        assert all(task.result() == destination for task in tasks)
    assert len(set(stages)) == 2
    assert destination.read_bytes() == FakeConverter().payload
    assert not list(destination.parent.glob("*.part"))


def test_parallel_requests_do_not_repeat_conversion_or_hold_db_connections(api):
    entered = threading.Event()
    release = threading.Event()

    class SlowConverter(FakeConverter):
        def convert(self, source, out_dir):
            self.calls += 1
            entered.set()
            assert release.wait(timeout=8)
            result = out_dir / "result.pdf"
            result.write_bytes(self.payload)
            return result

    converter = SlowConverter()
    api.app.state.doc_preview = converter
    api.mkuser("alice", role="admin")
    api.login("alice")
    resource = _publish(api, "slow.docx")
    api.mkuser("bob")
    api.login("bob")
    url = f"/api/files/resources/{resource['id']}/document-preview"
    with ThreadPoolExecutor(max_workers=16) as pool:
        tasks = [pool.submit(api.c.get, url) for _ in range(15)]
        try:
            assert entered.wait(timeout=5)
            done, pending = wait(tasks, timeout=2)
            assert len(done) == 14 and len(pending) == 1
            for task in done:
                assert task.result().status_code == 503
                assert task.result().headers["retry-after"] == "2"
            assert converter.calls == 1
            assert api.app.state.db.engine.pool.checkedout() == 0
            start = time.monotonic()
            assert api.c.get("/api/health").json() == {"status": "ok"}
            assert time.monotonic() - start < 2
        finally:
            release.set()
        assert next(iter(pending)).result().status_code == 200
    assert api.c.get(url).status_code == 200
    assert converter.calls == 1


@pytest.mark.parametrize("extension,mime", [
    (".odt", "application/vnd.oasis.opendocument.text"),
    (".ods", "application/vnd.oasis.opendocument.spreadsheet"),
    (".odp", "application/vnd.oasis.opendocument.presentation"),
    (".rtf", "application/rtf"),
])
def test_every_advertised_format_can_upload_and_preview(api, extension, mime):
    api.app.state.doc_preview = FakeConverter()
    api.mkuser("alice", role="admin")
    api.login("alice")
    resource = _publish(api, f"notes{extension}")
    response = api.c.get(f"/api/files/resources/{resource['id']}/document-preview")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    ticket = api.c.get(f"/api/files/resources/{resource['id']}/download?preview=true").json()
    assert api.c.get(ticket["downloadUrl"]).headers["content-type"] == mime


def test_unavailable_forced_converter_is_not_advertised(api):
    assert docpreview.build_converter("libreoffice", "/nonexistent/pr126-soffice", 1) is None

    class MissingConverter(FakeConverter):
        def available(self):
            return False

    api.app.state.doc_preview = MissingConverter()
    api.mkuser("alice", role="admin")
    api.login("alice")
    resource = _publish(api, "notes.docx")
    assert api.c.get("/api/files/config").json()["documentPreview"] is False
    assert api.c.get(f"/api/files/resources/{resource['id']}/document-preview").status_code == 404


def test_cached_pdf_rechecks_visibility_and_deletion(api):
    converter = FakeConverter()
    api.app.state.doc_preview = converter
    api.mkuser("alice", role="admin")
    api.login("alice")
    resource = _publish(api, "notes.docx")
    url = f"/api/files/resources/{resource['id']}/document-preview"
    assert api.c.get(url).status_code == 200
    api.mkuser("bob")
    api.login("bob")
    assert api.c.get(url).status_code == 200
    api.login("alice")
    assert api.c.patch(f"/api/files/resources/{resource['id']}", json={"visibility": "private"}).status_code == 200
    api.login("bob")
    assert api.c.get(url).status_code == 404
    api.c.post("/api/auth/logout")
    assert api.c.get(url).status_code == 404
    api.login("alice")
    assert api.c.delete(f"/api/files/resources/{resource['id']}").status_code == 200
    assert api.c.get(url).status_code == 404
    assert converter.calls == 1


def test_cache_budget_version_and_failure_memoisation(tmp_path):
    source = tmp_path / "notes.docx"
    source.write_bytes(b"docx")
    item = docpreview.PreviewSource("uuid/notes.docx", source)
    first = docpreview.PreviewCache(str(tmp_path / "cache"), version="fonts-1")
    converter = FakeConverter()
    old_pdf = first.render(converter, item)
    updated = docpreview.PreviewCache(str(tmp_path / "cache"), version="fonts-2")
    assert updated.render(converter, item) != old_pdf
    assert converter.calls == 2
    assert first.render(converter, item) == old_pdf
    assert converter.calls == 2
    full = docpreview.PreviewCache(str(tmp_path / "full"), max_bytes=1)
    with pytest.raises(docpreview.ConversionFailed, match="full"):
        full.render(converter, item)
    assert not list(full.directory.glob("*.pdf"))
    failed = FakeConverter(fail=True)
    cache = docpreview.PreviewCache(str(tmp_path / "failed"))
    for _ in range(2):
        with pytest.raises(docpreview.ConversionFailed):
            cache.render(failed, item)
    assert failed.calls == 1


@pytest.mark.skipif(os.name != "posix", reason="Linux cross-process file locks")
def test_conversion_slots_and_object_locks_are_shared_across_processes(tmp_path):
    ctx = multiprocessing.get_context("fork")
    entered, release = ctx.Event(), ctx.Event()
    source = tmp_path / "notes.docx"
    source.write_bytes(b"docx")
    directory = str(tmp_path / "cache")
    item = docpreview.PreviewSource("one/notes.docx", source)
    other = docpreview.PreviewSource("two/notes.docx", source)

    def convert_child():
        class Slow(FakeConverter):
            def convert(self, source, out_dir):
                entered.set()
                assert release.wait(timeout=8)
                return super().convert(source, out_dir)
        docpreview.PreviewCache(directory, concurrency=1).render(Slow(), item)

    process = ctx.Process(target=convert_child)
    process.start()
    try:
        assert entered.wait(timeout=5)
        cache = docpreview.PreviewCache(directory, concurrency=1)
        for resource in (item, other):
            with pytest.raises(docpreview.ConversionBusy):
                cache.render(FakeConverter(), resource)
    finally:
        release.set()
        process.join(timeout=10)
        if process.is_alive():
            process.kill()
            process.join()
    assert process.exitcode == 0
    converter = FakeConverter()
    assert cache.render(converter, item).exists()
    assert converter.calls == 0
    assert cache.render(converter, other).exists()
    assert converter.calls == 1


@pytest.mark.parametrize("values", [
    {"preview_converter": "of"}, {"preview_timeout_seconds": 0}, {"preview_timeout_seconds": 301},
    {"preview_max_conversions": 0}, {"preview_max_conversions": 9},
    {"preview_cache_version": ""}, {"preview_cache_max_bytes": 0},
])
def test_preview_configuration_is_validated(values):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_pdf_openapi_declares_binary_response(api):
    response = api.app.openapi()["paths"]["/api/files/resources/{resource_id}/document-preview"]["get"]["responses"]
    assert response["200"]["content"] == {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}
    assert "503" in response and "404" in response
    assert "application/json" in response["404"]["content"]
    assert "Retry-After" in response["503"]["headers"]


def test_private_runtime_wrappers_and_missing_sandbox_fail_closed(tmp_path, monkeypatch):
    wrapper = tmp_path / "soffice"
    wrapper.write_text("#!/bin/sh\nexit 0\n")
    wrapper.chmod(0o700)
    assert DocumentSandbox(str(wrapper), 5).available() is False
    monkeypatch.setattr(shutil, "which", lambda _binary: None)
    assert docpreview.build_converter("auto", "soffice", 5) is None
    assert docpreview.build_converter("libreoffice", "soffice", 5) is None


def test_real_sandbox_hides_host_files_environment_and_network(tmp_path):
    sandbox = DocumentSandbox("/usr/bin/soffice", 10)
    if not sandbox.available():
        pytest.skip("system LibreOffice/bubblewrap/user namespaces unavailable")
    canary = tmp_path / "server-only.txt"
    canary.write_text("PR126_CANARY")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        code = ("import json,os,socket; from pathlib import Path; "
                f"print(json.dumps([Path({str(canary)!r}).exists(), os.getenv('PR126_SECRET'), "
                f"socket.socket().connect_ex(('127.0.0.1',{listener.getsockname()[1]}))]))")
        result = subprocess.check_output([*sandbox._base_command(), "/usr/bin/python3", "-c", code],
                                         env={"PATH": "/usr/bin:/bin", "PR126_SECRET": "secret"}, timeout=10)
    visible, secret, network_status = json.loads(result)
    assert visible is False and secret is None and network_status != 0


def test_real_office_conversion_cannot_embed_host_image(tmp_path):
    converter = docpreview.LibreOfficeConverter("/usr/bin/soffice", 20)
    if not converter.available() or not shutil.which("pdftotext"):
        pytest.skip("system sandboxed LibreOffice and pdftotext unavailable")
    canary = tmp_path / "secret.svg"
    canary.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="400" height="100">'
                      '<text x="10" y="55">PR126_SERVER_ONLY_CANARY</text></svg>')
    source = tmp_path / "notes.doc"
    source.write_text(f'<html><body><p>PR126_VISIBLE_TEXT</p><img src="{canary.as_uri()}"></body></html>')
    pdf = docpreview.convert_once(converter, source, tmp_path / "cache" / "result.pdf")
    text = subprocess.check_output(["pdftotext", str(pdf), "-"]).decode()
    assert "PR126_VISIBLE_TEXT" in text
    assert "PR126_SERVER_ONLY_CANARY" not in text


def test_timeout_kills_the_entire_converter_process_group(tmp_path, monkeypatch):
    sandbox = DocumentSandbox("/usr/bin/soffice", 1)
    sandbox.binary = "/usr/bin/soffice"
    sandbox.bwrap = "/usr/bin/bwrap"
    monkeypatch.setattr(sandbox, "available", lambda: True)
    source = tmp_path / "notes.docx"
    source.write_bytes(b"docx")
    output = tmp_path / "out"
    output.mkdir()
    killed = []

    class TimedOutProcess:
        pid = 12345
        waits = 0
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def wait(self, timeout=None):
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired("sandbox", timeout)
            return -9

    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: TimedOutProcess())
    monkeypatch.setattr(os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    assert sandbox.convert(source, output) is None
    assert killed == [(12345, 9)]


def test_offline_cache_prune_is_dry_run_by_default(tmp_path):
    import sys
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "old.pdf").write_bytes(b"pdf")
    (cache / "failed.failed").touch()
    original = cache / "upload.docx"
    original.write_bytes(b"original")
    env = {**os.environ, "PREVIEW_CACHE_DIR": str(cache), "SAMRYETHA_DATA_DIR": str(tmp_path / "data")}
    command = [sys.executable, "scripts/prune_preview_cache.py", "--max-bytes", "0"]
    assert "Would remove 2 files" in subprocess.check_output(command, env=env, text=True)
    assert (cache / "old.pdf").exists()
    assert "Removed 2 files" in subprocess.check_output([*command, "--apply"], env=env, text=True)
    assert not (cache / "old.pdf").exists()
    assert original.read_bytes() == b"original"
