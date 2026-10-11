"""Durable polling: each claim and publication shares one SQLite transaction."""

import logging
import threading

from ..adapters.storage import Storage
from ..core.config import Settings
from ..core.db import Database, now_ms
from ..core.errors import APIError
from .repository import ScheduledPostRepository
from .service import ScheduledPostService

logger = logging.getLogger("samryetha.scheduled_posts")


class ScheduledPostWorker:
    def __init__(self, db: Database, settings: Settings, storage: Storage) -> None:
        self._db = db
        self._settings = settings
        self._storage = storage
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def publish_once(self, batch_size: int = 20) -> int:
        with self._db.request_conn() as conn:
            ids = ScheduledPostRepository(conn).due_ids(now_ms(), batch_size)
        published = 0
        for post_id in ids:
            with self._db.request_conn() as conn:
                repo = ScheduledPostRepository(conn)
                row = repo.claim(post_id, now_ms())
                if row is None:
                    continue
                try:
                    with conn.begin_nested():
                        ScheduledPostService(conn, self._settings, self._storage).publish_claimed(row)
                    published += 1
                except Exception as exc:
                    # The savepoint keeps the draft, attachments and outbox unchanged.
                    # Permanent failures are visible to the owner, never silently published later.
                    message = exc.message if isinstance(exc, APIError) else "Publication failed. Please try again."
                    repo.change(row.id, status="failed", last_error=message, updated_at=now_ms())
                    logger.warning("Scheduled post %s failed (%s)", row.id, type(exc).__name__)
        return published

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="scheduled-post-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.publish_once()
            except Exception:
                logger.exception("Scheduled publication poll failed")
            self._stop.wait(1)
