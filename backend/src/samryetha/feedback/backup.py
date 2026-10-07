"""反馈数据备份 — 镜像 backend/src/feedback/backup.ts。

VACUUM INTO 生成自包含库文件到 <db目录>/backups/；restore 写 .restore_pending 标记，
下次启动在打开引擎前换库文件（apply_pending_restore）。cron 用 apscheduler 5 字段。
app_settings["feedback.backup"] = {"backupCron": "5 字段 cron", "backupKeep": n}。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
from typing import Protocol, TypedDict, cast

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.engine import Connection

from ..core.config import Settings
from ..core.db import Database
from ..core.errors import bad_request, not_found
from ..core.schema import app_settings

BACKUP_RE = re.compile(r"^backup-\d{8}-\d{6}\.sqlite$")
SETTINGS_KEY = "feedback.backup"
_PENDING_RESTORE_FILE = ".restore_pending"


class BackupSettings(TypedDict):
    backupCron: str
    backupKeep: int


class BackupFile(TypedDict):
    name: str
    size: int
    createdAt: int


class _Scheduler(Protocol):
    def shutdown(self, wait: bool = True) -> None: ...
    def start(self) -> None: ...
    def add_job(self, func: object, **kwargs: object) -> object: ...


def _sqlite_escape(p: str) -> str:
    return p.replace("'", "''")


def _data_dir(db: Database) -> str | None:
    url = db.database_url
    if not url or url == ":memory:":
        return None
    return os.path.dirname(os.path.abspath(url)) or "."


def _backup_dir(db: Database) -> str | None:
    ddir = _data_dir(db)
    if ddir is None:
        return None
    bdir = os.path.join(ddir, "backups")
    os.makedirs(bdir, exist_ok=True)
    return bdir


def _stamp() -> str:
    import datetime

    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def _prune_files(bdir: str, keep: int) -> None:
    try:
        files = [f for f in os.listdir(bdir) if BACKUP_RE.match(f)]
    except FileNotFoundError:
        return
    files.sort(reverse=True)
    for f in files[keep:]:
        try:
            os.remove(os.path.join(bdir, f))
        except OSError:
            pass


# ---------------------------------------------------------------- settings

def get_backup_settings(conn: Connection) -> BackupSettings:
    row = conn.execute(select(app_settings).where(app_settings.c.key == SETTINGS_KEY)).first()
    value: dict[str, object] = {}
    if row is not None and row.value:
        try:
            parsed: object = json.loads(row.value)
            value = cast(dict[str, object], parsed) if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            value = {}
    raw_cron = value.get("backupCron")
    raw_keep = value.get("backupKeep")
    return {
        "backupCron": raw_cron if isinstance(raw_cron, str) else "",
        "backupKeep": raw_keep if isinstance(raw_keep, int) and not isinstance(raw_keep, bool) else 5,
    }


def _cron_trigger(expr: str) -> CronTrigger:
    return CronTrigger.from_crontab(expr)


def set_backup_settings(conn: Connection, backup_cron: str, backup_keep: int) -> None:
    cron = backup_cron.strip()
    keep = max(1, min(500, backup_keep))
    if cron:
        try:
            _cron_trigger(cron)
        except ValueError as exc:
            raise bad_request("Invalid cron expression") from exc
    payload = json.dumps({"backupCron": cron, "backupKeep": keep}, ensure_ascii=False)
    existing = conn.execute(select(app_settings).where(app_settings.c.key == SETTINGS_KEY)).first()
    if existing is None:
        conn.execute(app_settings.insert().values(key=SETTINGS_KEY, value=payload))
    else:
        conn.execute(app_settings.update().where(app_settings.c.key == SETTINGS_KEY).values(value=payload))


# ---------------------------------------------------------------- backups

def list_backups(db: Database) -> list[BackupFile]:
    bdir = _backup_dir(db)
    if bdir is None:
        return []
    try:
        names = [f for f in os.listdir(bdir) if BACKUP_RE.match(f)]
    except FileNotFoundError:
        return []
    out: list[BackupFile] = []
    for name in names:
        st = os.stat(os.path.join(bdir, name))
        out.append({"name": name, "size": st.st_size, "createdAt": int(st.st_mtime * 1000)})
    out.sort(key=lambda x: x["createdAt"], reverse=True)
    return out


def create_backup(db: Database) -> BackupFile:
    if _data_dir(db) is None:
        raise bad_request("Backup not available for in-memory database")
    bdir = _backup_dir(db)
    if bdir is None:
        raise bad_request("Backup not available for in-memory database")
    name = f"backup-{_stamp()}.sqlite"
    target = os.path.join(bdir, name)
    escaped = _sqlite_escape(target)
    raw = db.engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        raw.exec_driver_sql(f"VACUUM INTO '{escaped}'")
    finally:
        raw.close()
    st = os.stat(target)
    with db.request_conn() as conn:
        settings = get_backup_settings(conn)
    _prune_files(bdir, settings["backupKeep"])
    return {"name": name, "size": st.st_size, "createdAt": int(st.st_mtime * 1000)}


def restore_backup(db: Database, name: str) -> None:
    if _data_dir(db) is None:
        raise bad_request("Backup not available for in-memory database")
    if not BACKUP_RE.match(name):
        raise bad_request("Invalid backup name")
    bdir = _backup_dir(db)
    if bdir is None:
        raise bad_request("Backup not available for in-memory database")
    source = os.path.join(bdir, name)
    if not os.path.exists(source):
        raise not_found("Backup not found")
    ddir = _data_dir(db)
    if ddir is None:
        raise bad_request("Backup not available for in-memory database")
    marker = os.path.join(ddir, _PENDING_RESTORE_FILE)
    with open(marker, "w", encoding="utf-8") as fh:
        fh.write(name)


def apply_pending_restore(settings: Settings) -> None:
    """启动时在打开引擎前调用：若存在待恢复标记则换库。"""
    if settings.database_url == ":memory:":
        return
    ddir = os.path.dirname(os.path.abspath(settings.database_url)) or "."
    marker = os.path.join(ddir, _PENDING_RESTORE_FILE)
    if not os.path.exists(marker):
        return
    with open(marker, encoding="utf-8") as fh:
        pending = fh.read().strip()
    if not BACKUP_RE.match(pending):
        try:
            os.remove(marker)
        except OSError:
            pass
        return
    source = os.path.join(ddir, "backups", pending)
    db_path = os.path.abspath(settings.database_url)
    try:
        shutil.copyfile(source, db_path)
        for suffix in ("-wal", "-shm"):
            try:
                os.remove(db_path + suffix)
            except FileNotFoundError:
                pass
        os.remove(marker)
    except OSError:
        pass


# ---------------------------------------------------------------- cron job


class BackupScheduler:
    """apscheduler cron 备份。仅生产 main() 启动。"""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._scheduler: _Scheduler | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            self._reschedule()

    def stop(self) -> None:
        with self._lock:
            if self._scheduler is not None:
                self._scheduler.shutdown(wait=False)
                self._scheduler = None

    def _reschedule(self) -> None:
        expr = ""
        with self.db.request_conn() as conn:
            expr = get_backup_settings(conn)["backupCron"]
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
        if not expr:
            return
        try:
            trigger = _cron_trigger(expr)
        except ValueError:
            return
        scheduler = cast(_Scheduler, BackgroundScheduler(daemon=True))
        scheduler.add_job(create_backup, trigger=trigger, args=[self.db], id="auto-backup", replace_existing=True)
        scheduler.start()
        self._scheduler = scheduler
