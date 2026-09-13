#!/usr/bin/env python3
"""Migrate forum users to Lako Auth with pre-provisioned identity mapping.

For each active forum user this script:
  1. dry-runs POST /api/admin/users/import (validation only),
  2. creates the Lako account for real (idempotent),
  3. writes the forum oidc_identities row (issuer, Lako subject -> forum id),
  4. optionally sends a set-password invite (real addresses only).

Because the mapping is pre-provisioned, migrated users land on their
EXISTING forum account on first OAuth login — no data loss, no empty dupes.

Email policy: recovery_email when present and well-formed, else
<username>@migrated.invalid (RFC 2606, never delivers). Placeholder
addresses are never marked verified and never receive invites.

Collision policy: if an import resolves to a Lako account this script did
not create (tracked in the state file), the user is reported for manual
review and NO mapping is written. Shared recovery emails automatically
fall back to the placeholder address.

State file (default ./migration-state.json) makes reruns safe:
{username: {"lako_user_id": ..., "invited": bool}}.

Usage:
  python migrate_users_to_lako.py --lako-url https://auth.example.com \\
      --issuer https://auth.example.com --forum-db ../data/app.db --dry-run
  python migrate_users_to_lako.py --lako-url ... --issuer ... --forum-db ... \\
      --send-invites   # real run
Env: LAKO_URL, LAKO_ADMIN_TOKEN, LAKO_ISSUER (args take precedence).
Exit 0 = clean, 1 = items need manual review, 2 = fatal error.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request

PLACEHOLDER_DOMAIN = "migrated.invalid"
IMPORT_BATCH = 200


def now_ms() -> int:
    return int(time.time() * 1000)


def valid_email(value: str | None) -> str | None:
    if not value:
        return None
    email = value.strip().lower()
    if "@" not in email or " " in email or len(email) > 320:
        return None
    local, _, domain = email.partition("@")
    if not local or "." not in domain:
        return None
    return email


class Lako:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _post(self, path: str, payload: dict):
        data = json.dumps(payload).encode()
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read().decode() or "{}")
            except (ValueError, UnicodeDecodeError):
                body = {}
            return exc.code, body
        except (urllib.error.URLError, OSError) as exc:
            raise RuntimeError(f"cannot reach Lako at {self.base_url}{path}: {exc}")

    def import_users(self, users: list, dry_run: bool):
        status, body = self._post("/api/admin/users/import", {"users": users, "dry_run": dry_run})
        if status not in (200, 413):
            raise RuntimeError(f"import failed: HTTP {status} {body}")
        return status, body

    def invite(self, username: str):
        status, body = self._post("/api/admin/users/invite", {"username": username})
        if status != 200:
            raise RuntimeError(f"invite failed for {username}: HTTP {status} {body}")
        return body


def load_forum_users(db_path: str) -> list:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, username, display_name, role, status, recovery_email, deleted_at FROM users"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def existing_mapping(conn: sqlite3.Connection, issuer: str, subject: str) -> bool:
    row = conn.execute(
        "SELECT id FROM oidc_identities WHERE issuer = ? AND subject = ?", (issuer, subject)
    ).fetchone()
    return row is not None


def write_mapping(conn: sqlite3.Connection, issuer: str, subject: str, user_id: int, email: str) -> None:
    if existing_mapping(conn, issuer, subject):
        return
    now = now_ms()
    conn.execute(
        "INSERT INTO oidc_identities (user_id, issuer, subject, email_at_link, created_at, last_login_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, issuer, subject, email, now, now),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lako-url", default=os.environ.get("LAKO_URL", ""), help="Lako API base URL")
    parser.add_argument("--token", default=os.environ.get("LAKO_ADMIN_TOKEN", ""), help="ADMIN_IMPORT_TOKEN")
    parser.add_argument("--issuer", default=os.environ.get("LAKO_ISSUER", ""), help="OIDC issuer URL (mapping key)")
    parser.add_argument("--forum-db", default="backend/data/app.db", help="Forum sqlite path")
    parser.add_argument("--state", default="migration-state.json", help="Local state file for idempotent reruns")
    parser.add_argument("--dry-run", action="store_true", help="Validate only; write nothing anywhere")
    parser.add_argument("--send-invites", action="store_true", help="Send set-password invites (real run only)")
    parser.add_argument("--include-non-active", action="store_true", help="Also migrate pending/banned/deactivated (NOT recommended)")
    args = parser.parse_args()

    if not args.lako_url or not args.token or not args.issuer:
        print("error: --lako-url, --token and --issuer are required (or LAKO_URL/LAKO_ADMIN_TOKEN/LAKO_ISSUER)", file=sys.stderr)
        return 2
    if not os.path.exists(args.forum_db):
        print(f"error: forum db not found: {args.forum_db}", file=sys.stderr)
        return 2

    state: dict = {}
    if os.path.exists(args.state):
        with open(args.state, encoding="utf-8") as fh:
            state = json.load(fh)

    lako = Lako(args.lako_url, args.token)
    forum_users = load_forum_users(args.forum_db)
    report = {"created": [], "exists": [], "invalid": [], "collisions": [], "skipped": [], "invited": [], "invite_failed": []}

    def known_ids() -> set:
        return {v.get("lako_user_id") for v in state.values() if v.get("lako_user_id")}

    def claim(conn: sqlite3.Connection, user: dict, item: dict, entry: dict | None) -> None:
        write_mapping(conn, args.issuer, entry["lako_user_id"], user["id"], item["email"])
        if args.send_invites and not item["email"].endswith("@" + PLACEHOLDER_DOMAIN) and not entry.get("invited"):
            try:
                lako.invite(user["username"])
                entry["invited"] = True
                report["invited"].append(user["username"])
            except RuntimeError as exc:
                report["invite_failed"].append(str(exc))

    conn = sqlite3.connect(args.forum_db)
    try:
        for user in forum_users:
            username = user["username"]
            if user["deleted_at"] is not None:
                report["skipped"].append(f"{username} (deleted)")
                continue
            if user["status"] != "active" and not args.include_non_active:
                report["skipped"].append(f"{username} (status={user['status']})")
                continue
            email = valid_email(user.get("recovery_email"))
            if email is None:
                email = f"{username.lower()}@{PLACEHOLDER_DOMAIN}"
            display = (user.get("display_name") or username)[:120]
            admin = user.get("role") == "admin"
            item = {"username": username, "email": email, "display_name": display, "admin": admin}

            if state.get(username, {}).get("lako_user_id"):
                # Seen before: verify convergence, never remap blindly.
                status, body = lako.import_users([item], dry_run=True)
                if status != 200:
                    report["collisions"].append(f"{username} (recheck failed: {body})")
                    continue
                result = (body.get("results") or [{}])[0]
                if result.get("status") != "exists" or result.get("lako_user_id") != state[username]["lako_user_id"]:
                    report["collisions"].append(f"{username} (identity drift: {result})")
                    continue
                report["exists"].append(username)
            else:
                status, body = lako.import_users([item], dry_run=True)
                if status == 413:
                    print("error: batch rejected, aborting", file=sys.stderr)
                    return 2
                result = (body.get("results") or [{}])[0]
                if result.get("status") == "invalid" and "@" + PLACEHOLDER_DOMAIN not in item["email"]:
                    # Most likely a shared recovery email: retry with placeholder.
                    item["email"] = f"{username.lower()}@{PLACEHOLDER_DOMAIN}"
                    status, body = lako.import_users([item], dry_run=True)
                    result = (body.get("results") or [{}])[0]
                if result.get("status") == "invalid":
                    report["invalid"].append(f"{username} ({result.get('error')})")
                    continue
                if result.get("status") == "exists":
                    if result.get("lako_user_id") in known_ids():
                        # Shared address already claimed by a migrated user: fall back.
                        item["email"] = f"{username.lower()}@{PLACEHOLDER_DOMAIN}"
                        status, body = lako.import_users([item], dry_run=True)
                        result = (body.get("results") or [{}])[0]
                        if result.get("status") != "created":
                            report["collisions"].append(f"{username} (placeholder fallback deviated: {result})")
                            continue
                    else:
                        # Claimed by an account we did not create: manual review, no mapping.
                        report["collisions"].append(f"{username} (held by another Lako id {result.get('lako_user_id')})")
                        continue
                if not args.dry_run:
                    status, body = lako.import_users([item], dry_run=False)
                    result = (body.get("results") or [{}])[0]
                    if result.get("status") != "created":
                        report["collisions"].append(f"{username} (create deviated: {result})")
                        continue
                    state[username] = {"lako_user_id": result["lako_user_id"], "invited": False}
                    report["created"].append(username)
                else:
                    report["created"].append(username + " (would create)")

            entry = state.get(username)
            if not args.dry_run and entry and entry.get("lako_user_id"):
                claim(conn, user, item, entry)
        if not args.dry_run:
            conn.commit()
            with open(args.state, "w", encoding="utf-8") as fh:
                json.dump(state, fh, ensure_ascii=False, indent=2)
    except (RuntimeError, OSError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()

    print(f"users scanned: {len(forum_users)}")
    for key in ("created", "exists", "invited"):
        print(f"{key}: {len(report[key])}")
    manual = report["invalid"] + report["collisions"] + report["invite_failed"]
    print(f"manual review needed: {len(manual)}")
    for line in manual:
        print(f"  - {line}")
    print(f"skipped (non-active/deleted): {len(report['skipped'])}")
    return 1 if manual else 0


if __name__ == "__main__":
    raise SystemExit(main())
