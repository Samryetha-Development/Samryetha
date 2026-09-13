"""Migration script: dry-run safety, idempotency, collisions, invites, mappings."""

from __future__ import annotations

import importlib.util
import json
import re
import sqlite3
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "migrate_users_to_lako.py")


def _load_script():
    spec = importlib.util.spec_from_file_location("migrate_users_to_lako", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StubLako:
    """Minimal in-memory stand-in honoring the import/invite contract."""

    PATTERN = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")

    def __init__(self):
        self.by_username: dict[str, dict] = {}
        self.by_email: dict[str, dict] = {}
        self.invites: list[str] = []

    def _norm(self, value: str) -> str:
        return value.strip().lower()

    def import_users(self, users: list, dry_run: bool) -> tuple[int, dict]:
        if len(users) > 500:
            return 413, {}
        results = []
        for item in users:
            username = item["username"].strip()
            email = item["email"].strip()
            if not self.PATTERN.fullmatch(username):
                results.append({"username": item["username"], "status": "invalid", "error": "INVALID_USERNAME"})
                continue
            if "@" not in self._norm(email):
                results.append({"username": username, "status": "invalid", "error": "INVALID_EMAIL"})
                continue
            if item.get("password") is not None and len(item["password"]) < 12:
                results.append({"username": username, "status": "invalid", "error": "WEAK_PASSWORD"})
                continue
            uname = self._norm(username)
            ename = self._norm(email)
            u_owner = self.by_username.get(uname, {}).get("id")
            e_owner = self.by_email.get(ename, {}).get("id")
            if u_owner and e_owner and u_owner != e_owner:
                results.append({"username": username, "status": "invalid", "error": "AMBIGUOUS_IDENTITIES"})
                continue
            owner = u_owner or e_owner
            if owner:
                results.append({"username": username, "status": "exists", "lako_user_id": owner})
                continue
            if dry_run:
                results.append({"username": username, "status": "created", "lako_user_id": None})
                continue
            new_id = str(uuid.uuid4())
            self.by_username[uname] = {"id": new_id, "email": ename}
            self.by_email[ename] = {"id": new_id, "username": uname}
            results.append({"username": username, "status": "created", "lako_user_id": new_id})
        return 200, {"dry_run": dry_run, "results": results}

    def invite(self, username: str):
        uname = self._norm(username)
        if uname not in self.by_username:
            return 404, {}
        self.invites.append(uname)
        return 200, {"ok": True, "email": self.by_username[uname]["email"]}


def _serve(stub: StubLako, token: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _reply(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.headers.get("authorization") != f"Bearer {token}":
                self._reply(403, {})
                return
            length = int(self.headers.get("content-length", 0))
            payload = json.loads(self.rfile.read(length).decode() or "{}")
            if self.path == "/api/admin/users/import":
                status, body = stub.import_users(payload.get("users", []), payload.get("dry_run", False))
                self._reply(status, body)
            elif self.path == "/api/admin/users/invite":
                status, body = stub.invite(payload.get("username", ""))
                self._reply(status, body)
            else:
                self._reply(404, {})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _forum_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, display_name TEXT,"
        " role TEXT, status TEXT, recovery_email TEXT, deleted_at INTEGER, password_hash TEXT)"
    )
    conn.execute(
        "CREATE TABLE oidc_identities (id INTEGER PRIMARY KEY, user_id INTEGER,"
        " issuer TEXT, subject TEXT, email_at_link TEXT, created_at INTEGER, last_login_at INTEGER)"
    )
    users = [
        (1, "alice", "Alice", "student", "active", "alice@example.com", None, "hash-alice"),
        (2, "bob", "Bob", "student", "active", None, None, "hash-bob"),
        (3, "carol", "Carol", "student", "active", "shared@example.com", None, "hash-carol"),
        (4, "dave", "Dave", "student", "active", "shared@example.com", None, "hash-dave"),
        (5, "erin", "Erin", "student", "banned", "erin@example.com", None, "hash-erin"),
        (6, "frank", "Frank", "student", "active", None, 123, "hash-frank"),
        (7, "bad name", "Bad", "student", "active", None, None, "hash-bad"),
        (8, "root", "Root", "admin", "active", "root@example.com", None, "hash-root"),
    ]
    conn.executemany("INSERT INTO users VALUES (?,?,?,?,?,?,?,?)", users)
    conn.commit()
    conn.close()


def _run(monkeypatch, db: str, state: str, url: str, *extra: str) -> int:
    module = _load_script()
    argv = [
        "migrate",
        "--lako-url", url,
        "--token", "test-token",
        "--issuer", "https://auth.example.com",
        "--forum-db", db,
        "--state", state,
        *extra,
    ]
    monkeypatch.setattr("sys.argv", argv)
    return module.main()


def _mappings(db: str) -> list:
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT user_id, subject, email_at_link FROM oidc_identities").fetchall()
    finally:
        conn.close()


def test_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    stub = StubLako()
    server = _serve(stub, "test-token")
    try:
        db = str(tmp_path / "forum.db")
        state = str(tmp_path / "state.json")
        _forum_db(db)
        # exit 1: "bad name" is invalid and needs manual review
        assert _run(monkeypatch, db, state, f"http://127.0.0.1:{server.server_port}", "--dry-run") == 1
        assert "manual review needed: 1" in capsys.readouterr().out
        assert stub.by_username == {}
        assert _mappings(db) == []
        assert not Path(state).exists()
    finally:
        server.shutdown()


def test_real_run_maps_invites_and_rerun_is_idempotent(tmp_path, monkeypatch):
    stub = StubLako()
    server = _serve(stub, "test-token")
    try:
        db = str(tmp_path / "forum.db")
        state = str(tmp_path / "state.json")
        _forum_db(db)
        url = f"http://127.0.0.1:{server.server_port}"
        assert _run(monkeypatch, db, state, url, "--send-invites") == 1
        mappings = {row[0]: row[1] for row in _mappings(db)}
        # alice/bob/carol/dave/root mapped; dave fell back to placeholder
        assert set(mappings) == {1, 2, 3, 4, 8}
        assert sorted(stub.invites) == ["alice", "carol", "root"]
        with open(state, encoding="utf-8") as fh:
            saved = json.load(fh)
        assert set(saved) == {"alice", "bob", "carol", "dave", "root"}
        assert saved["alice"]["invited"] is True
        assert saved["bob"]["invited"] is False
        assert saved["root"]["invited"] is True
        before = _mappings(db)
        invites_before = list(stub.invites)
        assert _run(monkeypatch, db, state, url, "--send-invites") == 1
        assert _mappings(db) == before
        assert stub.invites == invites_before
    finally:
        server.shutdown()
    # 映射落定的账号旧密码已被作废；未映射的不动
    conn = sqlite3.connect(db)
    try:
        hashes = dict(conn.execute("SELECT id, password_hash FROM users").fetchall())
    finally:
        conn.close()
    assert hashes[1] != "hash-alice"
    assert hashes[2] != "hash-bob"
    assert hashes[5] == "hash-erin"
    assert hashes[7] == "hash-bad"


def test_foreign_collision_needs_manual_review(tmp_path, monkeypatch):
    stub = StubLako()
    stub.by_username["zed"] = {"id": "foreign-id", "email": "zed@example.com"}
    stub.by_email["zed@example.com"] = {"id": "foreign-id", "username": "zed"}
    server = _serve(stub, "test-token")
    try:
        db = str(tmp_path / "forum.db")
        state = str(tmp_path / "state.json")
        _forum_db(db)
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO users VALUES (9,'zed','Zed','student','active','zed@example.com',NULL,'hash-zed')")
        conn.commit()
        conn.close()
        url = f"http://127.0.0.1:{server.server_port}"
        assert _run(monkeypatch, db, state, url) == 1
        assert all(row[0] != 9 for row in _mappings(db))
    finally:
        server.shutdown()
