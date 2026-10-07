"""Connection-owning repositories preserve the caller's transaction boundary."""

import pytest

from samryetha.auth import AuthService
from samryetha.core.db import now_ms
from samryetha.core.ids import UserID
from samryetha.events.outbox import OutboxWriter
from samryetha.events.repository import EventRepository
from samryetha.tasks.models import TaskPriority, TaskStatus
from samryetha.tasks.repository import TaskRepository


@pytest.mark.parametrize("rollback", [False, True])
def test_task_and_outbox_share_caller_transaction(db, rollback):
    with db.request_conn() as conn:
        author_id = UserID(AuthService(conn).register("repositorywriter", "password123"))

    def write():
        with db.request_conn() as conn:
            tasks = TaskRepository(conn)
            task_id = tasks.insert_task(
                author_id=author_id, category="General", title="Atomic task", notes="",
                priority=TaskPriority.Normal, status=TaskStatus.Open, done_at=None, now=now_ms(),
            )
            OutboxWriter(conn).emit("repository.test", aggregate_type="task", aggregate_id=str(task_id))
            assert tasks.task(task_id) is not None
            assert len(EventRepository(conn).pending_batch(available_at=now_ms() + 1000, limit=20)) == 1
            if rollback:
                raise RuntimeError("abort caller transaction")

    if rollback:
        with pytest.raises(RuntimeError, match="abort caller transaction"):
            write()
    else:
        write()

    with db.request_conn() as conn:
        assert len(TaskRepository(conn).all_tasks()) == (0 if rollback else 1)
        assert len(EventRepository(conn).pending_batch(available_at=now_ms() + 1000, limit=20)) == (0 if rollback else 1)
