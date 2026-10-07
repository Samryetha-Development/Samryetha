"""反馈数据备份 — 镜像 backend/src/feedback/backup.ts。

VACUUM INTO 生成自包含库文件到 <db目录>/backups/；restore 写 .restore_pending 标记，
下次启动在打开引擎前换库文件（apply_pending_restore）。cron 用 apscheduler 5 字段。
app_settings["feedback.backup"] = {"backupCron": "5 字段 cron", "backupKeep": n}。
"""

from __future__ import annotations

from samryetha.feedback.repository import DatabaseSnapshotRepository
from samryetha.feedback.repository import FeedbackRepository

import json
import os
import re
import shutil
import threading
from typing import Protocol, TypedDict, cast

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.engine import Connection

from .models import BackupsResponse, BackupFileResponse, BackupSettingsResponse
from ..core.config import Settings
from ..core.db import Database
from ..core.errors import bad_request, not_found

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


def _cron_trigger(expr: str) -> CronTrigger:
    return CronTrigger.from_crontab(expr)


# ---------------------------------------------------------------- backups


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
            expr = BackupSettingsService(conn).get_backup_settings()["backupCron"]
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
        scheduler.add_job(
            BackupService(self.db).create_backup, trigger=trigger, id="auto-backup", replace_existing=True
        )
        scheduler.start()
        self._scheduler = scheduler


class BackupSettingsService:
    """Application use-case implementations in a caller-owned transaction."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self._repository = FeedbackRepository(self._conn)

    def get_backup_settings(self) -> BackupSettings:
        raw_value = self._repository.setting(SETTINGS_KEY)
        value: dict[str, object] = {}
        if raw_value:
            try:
                parsed: object = json.loads(raw_value)
                value = cast(dict[str, object], parsed) if isinstance(parsed, dict) else {}
            except (TypeError, ValueError):
                value = {}
        raw_cron = value.get("backupCron")
        raw_keep = value.get("backupKeep")
        return {
            "backupCron": raw_cron if isinstance(raw_cron, str) else "",
            "backupKeep": raw_keep if isinstance(raw_keep, int) and not isinstance(raw_keep, bool) else 5,
        }

    def set_backup_settings(self, backup_cron: str, backup_keep: int) -> None:
        cron = backup_cron.strip()
        keep = max(1, min(500, backup_keep))
        if cron:
            try:
                _cron_trigger(cron)
            except ValueError as exc:
                raise bad_request("Invalid cron expression") from exc
        payload = json.dumps({"backupCron": cron, "backupKeep": keep}, ensure_ascii=False)
        self._repository.set_setting(SETTINGS_KEY, payload)


class BackupService:
    """Backup lifecycle use cases with explicit database transaction boundaries."""

    def __init__(self, db: Database, *, scheduler: BackupScheduler | None = None) -> None:
        self._db = db
        self._scheduler = scheduler
        self._snapshot_repository = DatabaseSnapshotRepository(self._db)

    def overview(self) -> BackupsResponse:
        with self._db.request_conn() as conn:
            settings = BackupSettingsService(conn).get_backup_settings()
        return BackupsResponse(
            backups=[BackupFileResponse.model_validate(item) for item in self.list_backups()],
            settings=BackupSettingsResponse.model_validate(settings),
        )

    def configure_schedule(self, cron: str, keep: int) -> None:
        with self._db.request_conn() as conn:
            BackupSettingsService(conn).set_backup_settings(cron, keep)
        # The scheduler must observe committed settings, as before the migration.
        if self._scheduler is not None:
            self._scheduler.start()

    def _data_dir(self) -> str | None:
        url = self._db.database_url
        if not url or url == ":memory:":
            return None
        return os.path.dirname(os.path.abspath(url)) or "."

    def _backup_dir(self) -> str | None:
        ddir = self._data_dir()
        if ddir is None:
            return None
        bdir = os.path.join(ddir, "backups")
        os.makedirs(bdir, exist_ok=True)
        return bdir

    def list_backups(self) -> list[BackupFile]:
        bdir = self._backup_dir()
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

    def create_backup(self) -> BackupFile:
        if self._data_dir() is None:
            raise bad_request("Backup not available for in-memory database")
        bdir = self._backup_dir()
        if bdir is None:
            raise bad_request("Backup not available for in-memory database")
        name = f"backup-{_stamp()}.sqlite"
        target = os.path.join(bdir, name)
        self._snapshot_repository.create(target)
        st = os.stat(target)
        with self._db.request_conn() as conn:
            settings = BackupSettingsService(conn).get_backup_settings()
        _prune_files(bdir, settings["backupKeep"])
        return {"name": name, "size": st.st_size, "createdAt": int(st.st_mtime * 1000)}

    def restore_backup(self, name: str) -> None:
        if self._data_dir() is None:
            raise bad_request("Backup not available for in-memory database")
        if not BACKUP_RE.match(name):
            raise bad_request("Invalid backup name")
        bdir = self._backup_dir()
        if bdir is None:
            raise bad_request("Backup not available for in-memory database")
        source = os.path.join(bdir, name)
        if not os.path.exists(source):
            raise not_found("Backup not found")
        ddir = self._data_dir()
        if ddir is None:
            raise bad_request("Backup not available for in-memory database")
        marker = os.path.join(ddir, _PENDING_RESTORE_FILE)
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write(name)
