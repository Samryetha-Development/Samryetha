"""Schedule management and atomic reuse of the ordinary publication path."""

from sqlalchemy.engine import Connection

from ..adapters.storage import Storage
from ..authz import Abilities, Actor, AuthorizationService
from ..boards import BoardService
from ..core.config import Settings
from ..core.db import now_ms
from ..core.errors import conflict, forbidden, not_found, validation_failed
from ..discussions.models import BodyFormat, CreateDiscussionBody
from ..discussions.service import derive_title, preview
from ..drafts.models import DraftBodyFormat, SaveDraftBody
from ..drafts.service import DraftService
from ..polls.models import PollDraftInput, PollInput
from .models import SchedulePostBody, ScheduledPostListResponse, ScheduledPostRecord, ScheduledPostResponse, ScheduleStatus
from .repository import ScheduledPostRepository


def _validate_time(stamp: int) -> None:
    if not now_ms() < stamp <= now_ms() + 366 * 24 * 3600 * 1000:
        raise validation_failed([{
            "field": "scheduledAt", "message": "Choose a future time within the next year", "code": "custom"
        }])


class ScheduledPostService:
    def __init__(self, conn: Connection, settings: Settings, storage: Storage) -> None:
        self._conn = conn
        self._settings = settings
        self._storage = storage
        self._repository = ScheduledPostRepository(conn)

    def owned(self, actor: Actor, post_id: int, *, lock: bool = False) -> ScheduledPostRecord:
        row = self._repository.owned(actor.id, post_id, lock=lock)
        if row is None:
            raise not_found("Scheduled post not found")
        return row

    def list(self, actor: Actor, before_id: int | None, limit: int) -> ScheduledPostListResponse:
        rows = self._repository.list_owned(actor.id, before_id, limit)
        return ScheduledPostListResponse(
            items=[ScheduledPostResponse.model_validate(row) for row in rows[:limit]],
            next_cursor=str(rows[limit - 1].id) if len(rows) > limit else None
        )

    def create(self, actor: Actor, body: SchedulePostBody) -> ScheduledPostRecord:
        _validate_time(body.scheduled_at)
        if not body.body_markdown.strip():
            raise validation_failed([{"field": "bodyMarkdown", "message": "Post body is required", "code": "custom"}])
        board = BoardService(self._conn).get_board_for_authz(body.board_slug)
        if board is None:
            raise not_found("Board not found")
        AuthorizationService(self._conn).assert_can(actor, Abilities.DISCUSSION_CREATE, {"type": "board", **board})
        draft = DraftService(self._conn, self._storage).save_draft(actor, SaveDraftBody(
            board_slug=body.board_slug, title=body.title or "", body_markdown=body.body_markdown,
            body_format=DraftBodyFormat(body.body_format.value), attachment_ids=body.attachment_ids or [],
            poll=PollDraftInput.model_validate(body.poll.model_dump()) if body.poll is not None else None,
        ).to_command(), body.draft_id)
        # Saving the draft takes the writer lock: check permissions again before committing.
        AuthorizationService(self._conn).assert_actor_current(actor.id, expected_role=actor.role)
        current_board = BoardService(self._conn).get_board_for_authz(body.board_slug)
        if current_board is None or current_board["id"] != board["id"]:
            raise conflict("Board changed; reload and try again")
        AuthorizationService(self._conn).assert_can(actor, Abilities.DISCUSSION_CREATE, {"type": "board", **current_board})
        _validate_time(body.scheduled_at)
        stamp = now_ms()
        row = ScheduledPostRecord(
            id=0, author_id=actor.id, draft_id=draft.id, discussion_id=None,
            board_slug=body.board_slug, title=(body.title or "").strip() or derive_title(body.body_markdown),
            preview=preview(body.body_markdown), scheduled_at=body.scheduled_at, can_modify=body.can_modify,
            status=ScheduleStatus.Pending, last_error=None, created_at=stamp, updated_at=stamp,
        )
        return self.owned(actor, self._repository.insert(row))

    def _modifiable(self, actor: Actor, post_id: int) -> ScheduledPostRecord:
        row = self.owned(actor, post_id, lock=True)
        if row.status not in (ScheduleStatus.Pending, ScheduleStatus.Failed) or row.draft_id is None:
            raise conflict("This post is no longer waiting to be published")
        if not row.can_modify:
            raise forbidden("This schedule is locked and cannot be changed or cancelled")
        return row

    def reschedule(self, actor: Actor, post_id: int, stamp: int) -> ScheduledPostRecord:
        self._modifiable(actor, post_id)
        _validate_time(stamp)
        self._repository.change(post_id, scheduled_at=stamp, status="pending", last_error=None, updated_at=now_ms())
        return self.owned(actor, post_id)

    def cancel(self, actor: Actor, post_id: int) -> int:
        row = self._modifiable(actor, post_id)
        if row.draft_id is None:
            raise conflict("This post is no longer waiting to be published")
        self._repository.change(post_id, draft_id=None, status="cancelled", updated_at=now_ms())
        return row.draft_id

    def publish_claimed(self, row: ScheduledPostRecord) -> None:
        from ..discussions.service import DiscussionService

        actor = self._repository.author(row.author_id)
        if actor is None or actor.status != "active":
            raise forbidden("The author's account is not active")
        if row.draft_id is None:
            raise conflict("Scheduled post has no draft to publish")
        # Release the draft only inside this publication transaction. A rollback restores
        # both the schedule and its files; another worker cannot claim the writer-locked row.
        self._repository.change(row.id, draft_id=None)
        draft = DraftService(self._conn, self._storage).get_draft(actor, row.draft_id)
        if draft.board_slug is None:
            raise not_found("Board not found")
        discussion = DiscussionService(self._conn, self._settings, storage=self._storage).create(actor, CreateDiscussionBody(
            board_slug=draft.board_slug, title=draft.title, body_markdown=draft.body_markdown,
            body_format=BodyFormat(draft.body_format.value), attachment_ids=[item.id for item in draft.attachments],
            draft_id=draft.id,
            poll=PollInput.model_validate(draft.poll.model_dump()) if draft.poll is not None else None,
        ))
        self._repository.change(row.id, discussion_id=discussion.id, status="published", last_error=None, updated_at=now_ms())
