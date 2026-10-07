"""Typed service layer for the moderation review queue."""

from __future__ import annotations

from samryetha.review_queue.repository import ReviewQueueRepository

from pydantic import TypeAdapter, ValidationError
from sqlalchemy.engine import Connection

from ..automod import AutomodService
from ..core.db import now_ms
from ..core.deps import CurrentUser
from ..core.errors import bad_request, forbidden, not_found
from ..core.ids import ModerationQueueID, UserID
from .models import (
    ContentType,
    DecisionResponse,
    DecisionResult,
    RecheckResponse,
    SignalResponse,
    QueueAuthorResponse,
    QueueCountsResponse,
    QueueItemResponse,
    QueueListResponse,
    QueueStatus,
    Resolution,
    ResolutionFilter,
    RetainedItemResponse,
    RetainedListResponse,
    ReviewState,
)
from .repository import AuthorRecord, QueueRecord


_signals_adapter = TypeAdapter(list[SignalResponse])
_recheck_adapter = TypeAdapter(RecheckResponse)
_blocked = (Resolution.Blocked, Resolution.BlockedByMachine)


def _signals(record: QueueRecord) -> list[SignalResponse]:
    try:
        return _signals_adapter.validate_json(record.signals or "[]")
    except ValidationError:
        return []


def _recheck(record: QueueRecord) -> RecheckResponse | None:
    if not record.recheck:
        return None
    try:
        return _recheck_adapter.validate_json(record.recheck)
    except ValidationError:
        return None


def _author(record: AuthorRecord | None) -> QueueAuthorResponse | None:
    if record is None:
        return None
    return QueueAuthorResponse(id=record.id, username=record.username, display_name=record.display_name)


class ReviewQueueService:
    """Application use-case implementations in a caller-owned transaction."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self._repository = ReviewQueueRepository(self._conn)

    def _item(
        self, row: QueueRecord, author: AuthorRecord | None, reviewer: AuthorRecord | None, *, viewer: CurrentUser
    ) -> QueueItemResponse:
        resolution = row.resolution
        resolved_by_ai = resolution in (Resolution.PublishedByAI, *_blocked) and row.reviewer_id is None
        restricted = resolution in _blocked and viewer.role != "admin"
        return QueueItemResponse(
            id=row.id,
            content_type=row.content_type,
            content_id=row.content_id,
            author=_author(author),
            excerpt="" if restricted else row.excerpt,
            excerpt_restricted=restricted,
            decision=row.decision,
            score=row.score,
            signals=_signals(row),
            created_at=row.created_at,
            review_state=row.review_state,
            reviewer=_author(reviewer),
            review_note=row.review_note,
            reviewed_at=row.reviewed_at,
            href=self._repository.content_href(row),
            hold_until=row.hold_until,
            resolution=resolution,
            resolved_at=row.resolved_at,
            resolved_by_ai=resolved_by_ai,
            overturned=row.overturned,
            recheck=_recheck(row),
            awaiting_human=row.review_state is ReviewState.Pending and resolution is None,
            needs_uphold=resolved_by_ai and resolution is Resolution.PublishedByAI,
            needs_release=resolved_by_ai and resolution in _blocked,
        )

    def _cursor(self, raw: str | None) -> tuple[int, ModerationQueueID] | None:
        if raw is None:
            return None
        try:
            parts = raw.split(":")
            if len(parts) == 2:
                score, raw_id = (int(part) for part in parts)
                item_id = ModerationQueueID(raw_id)
            elif len(parts) == 1:
                item_id = ModerationQueueID(int(parts[0]))
                stored = self._repository.score_for_id(item_id)
                if stored is None:
                    raise ValueError
                score = stored
            else:
                raise ValueError
            if not 0 <= score <= 100 or item_id < 1:
                raise ValueError
            return score, item_id
        except (ValueError, TypeError, LookupError):
            raise bad_request("Invalid queue cursor") from None

    def _counts(self) -> QueueCountsResponse:
        values = AutomodService(self._conn).queue_counts()
        return QueueCountsResponse(
            pending=values.get("pending", 0),
            approved=values.get("approved", 0),
            rejected=values.get("rejected", 0),
            awaiting=values.get("awaiting", 0),
            ai_published=values.get("aiPublished", 0),
            ai_blocked=values.get("aiBlocked", 0),
            blocked=values.get("blocked", 0),
        )

    def list_queue(
        self,
        *,
        viewer: CurrentUser,
        status: QueueStatus = QueueStatus.Pending,
        content_type: ContentType | None = None,
        resolution: ResolutionFilter | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> QueueListResponse:
        page_limit = max(1, min(limit, 50))
        awaiting = resolution is ResolutionFilter.Awaiting
        blocked = resolution is ResolutionFilter.Blocked
        exact = None if resolution is None or awaiting or blocked else Resolution(resolution.value)
        records = self._repository.queue_page(
            state=None if status is QueueStatus.All else ReviewState(status.value),
            content_type=content_type,
            resolution=exact,
            awaiting=awaiting,
            blocked=blocked,
            cursor=self._cursor(cursor),
            limit=page_limit,
        )
        page = records[:page_limit]
        people = self._repository.authors(
            [row.author_id for row in page] + [row.reviewer_id for row in page if row.reviewer_id is not None]
        )
        items = [
            self._item(
                row,
                people.get(row.author_id),
                people.get(row.reviewer_id) if row.reviewer_id is not None else None,
                viewer=viewer,
            )
            for row in page
        ]
        next_cursor = f"{page[-1].score}:{page[-1].id}" if len(records) > page_limit and page else None
        return QueueListResponse(items=items, next_cursor=next_cursor, counts=self._counts())

    def decide(
        self, actor: CurrentUser, queue_id: ModerationQueueID, *, approve: bool, note: str | None = None
    ) -> DecisionResult:
        row = self._repository.get(queue_id)
        if row is None:
            raise not_found("Queue item not found")
        if row.superseded_at is not None:
            raise bad_request("This version has been replaced by a newer submission")
        if row.review_state is not ReviewState.Pending:
            raise bad_request("This item has already been reviewed")
        if row.resolution in _blocked and actor.role != "admin":
            raise forbidden("Only administrators can decide on blocked content")
        prior = row.resolution
        flipped = row.reviewer_id is None and (
            (approve and prior in _blocked) or (not approve and prior is Resolution.PublishedByAI)
        )
        overturned = flipped or row.overturned
        state = ReviewState.Approved if approve else ReviewState.Rejected
        resolution = Resolution.PublishedByHuman if approve else Resolution.Blocked
        decided_at = now_ms()
        decided = self._repository.decide(
            row,
            reviewer_id=UserID(actor.id),
            state=state,
            resolution=resolution,
            note=note,
            reviewed_at=decided_at,
            overturned=overturned,
        )
        if decided is None:
            raise bad_request("This version has already been decided or replaced")
        AutomodService(self._conn).apply_review_state(
            content_type=row.content_type.value, content_id=row.content_id, status=state.value
        )
        if flipped:
            action = "automod.overturn"
            reason = note or ("推翻 AI 封禁，重新放行" if approve else "推翻 AI 先行放行，改为封禁")
        else:
            action = f"automod.{state.value}"
            reason = note or ("审核通过" if approve else "审核驳回")
        self._repository.add_action(
            actor_id=UserID(actor.id), action=action, row=row, reason=reason, created_at=decided_at
        )
        should_notify = overturned or prior is None or (not approve and prior is Resolution.BlockedByMachine)
        return DecisionResult(
            DecisionResponse(review_state=state, resolution=resolution, overturned=overturned), decided, should_notify
        )

    def decide_and_notify(
        self,
        actor: CurrentUser,
        queue_id: ModerationQueueID,
        *,
        approve: bool,
        note: str | None = None,
        notify_author_enabled: bool,
    ) -> DecisionResponse:
        """Decide an item and coordinate its author notification in one use case."""
        result = self.decide(actor, queue_id, approve=approve, note=note)
        if result.should_notify and notify_author_enabled:
            self.notify_author(result.row, approved=approve, note=note)
        return result.response

    def _retained_item(self, row: QueueRecord, author: AuthorRecord | None) -> RetainedItemResponse:
        content = AutomodService(self._conn).load_content(
            content_type=row.content_type.value, content_id=row.content_id
        )
        return RetainedItemResponse(
            id=row.id,
            content_type=row.content_type,
            content_id=row.content_id,
            author_id=row.author_id,
            author=_author(author),
            excerpt=row.excerpt,
            title=content.title,
            body=row.submitted_text or content.text,
            from_snapshot=bool(row.submitted_text),
            content_exists=content.exists,
            decision=row.decision,
            score=row.score,
            signals=_signals(row),
            resolution=row.resolution,
            resolved_at=row.resolved_at,
            reviewer_id=row.reviewer_id,
            review_note=row.review_note,
            overturned=row.overturned,
            recheck=_recheck(row),
            created_at=row.created_at,
            href=self._repository.content_href(row),
        )

    def list_retained(
        self, *, content_type: ContentType | None = None, cursor: ModerationQueueID | None = None, limit: int = 20
    ) -> RetainedListResponse:
        page_limit = max(1, min(limit, 50))
        records = self._repository.retained_page(content_type=content_type, cursor=cursor, limit=page_limit)
        page = records[:page_limit]
        people = self._repository.authors((row.author_id for row in page))
        return RetainedListResponse(
            items=[self._retained_item(row, people.get(row.author_id)) for row in page],
            next_cursor=page[-1].id if len(records) > page_limit and page else None,
            total=self._repository.retained_total(),
        )

    def notify_author(self, row: QueueRecord, *, approved: bool, note: str | None, by_ai: bool = False) -> None:
        from ..notifications import NotificationService

        if by_ai and approved:
            body = f"您的内容经 AI 复审后已先行公开。{('说明：' + note) if note else ''}"
        elif by_ai:
            body = f"您的内容经 AI 复审后未通过审核，现已不予公开。{('说明：' + note) if note else ''}"
        elif approved:
            body = "您的内容已通过审核，现已公开。"
        else:
            body = f"您的内容未通过审核。{('原因：' + note) if note else ''}"
        discussion_id = row.content_id if row.content_type is ContentType.Discussion else None
        reply_id = row.content_id if row.content_type is ContentType.Reply else None
        if row.content_type is ContentType.Reply:
            discussion_id = self._repository.reply_discussion_id(row.content_id)
        NotificationService(self._conn).create(
            user_id=row.author_id, type_="moderation", discussion_id=discussion_id, reply_id=reply_id, body=body
        )

    def notify_author_by_id(
        self, queue_id: ModerationQueueID, *, approved: bool, note: str | None, by_ai: bool = False
    ) -> None:
        """Notify through the review-queue boundary without exposing its repository."""
        row = self._repository.get(queue_id)
        if row is not None:
            self.notify_author(row, approved=approved, note=note, by_ai=by_ai)
