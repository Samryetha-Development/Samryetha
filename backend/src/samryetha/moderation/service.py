"""Typed reports, bans, moderation actions, and content restoration service."""

from __future__ import annotations

from samryetha.moderation.repository import ModerationRepository

import datetime

from sqlalchemy.engine import Connection

from ..authz import Abilities, Actor, AuthorizationService
from ..core.db import now_ms
from ..core.errors import conflict, internal_error, not_found
from ..core.ids import DiscussionID, ModerationActionID, ReplyID, ReportID, UserID
from .models import (
    DiscussionReportTarget,
    ModerationActionListResponse,
    ModerationActionResponse,
    ModerationAuthorResponse,
    ReplyReportTarget,
    ReportListResponse,
    ReportResponse,
    ReportableType,
    ReportStatus,
    ReportTarget,
    RestoreTargetType,
    UserReportTarget,
)
from ..notifications.models import UserBannedPayload
from ..events.outbox import OutboxWriter
from ..users import make_handle, normalize_username
from .repository import AuthorRecord, ReportRecord


def _author_response(row: AuthorRecord) -> ModerationAuthorResponse:
    return ModerationAuthorResponse(
        id=row.id,
        username=row.username,
        handle=make_handle(row.username, row.discriminator),
        display_name=row.display_name,
    )


def _report_response(row: ReportRecord, reporter: AuthorRecord | None, target: ReportTarget | None) -> ReportResponse:
    return ReportResponse(
        id=row.id,
        reporter=_author_response(reporter) if reporter else None,
        reportable_type=row.reportable_type,
        reportable_id=row.reportable_id,
        reason=row.reason,
        status=row.status,
        created_at=row.created_at,
        target=target,
    )


def _preview(md: str) -> str:
    flat = " ".join(md.split()).strip()
    return flat[:160] + "…" if len(flat) > 160 else flat


def _iso(ms: int) -> str:
    return datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def preview_text(md: str) -> str:
    return _preview(md)


class ModerationService:
    """Application use-case implementations in a caller-owned transaction."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self._outbox = OutboxWriter(self._conn)
        self._repository = ModerationRepository(self._conn)

    def create_report(
        self, actor: Actor, reportable_type: ReportableType, reportable_id: int, reason: str | None
    ) -> ReportResponse:
        AuthorizationService(self._conn).assert_can(actor, Abilities.REPORT_CREATE, None)
        report_id = self._repository.insert_report(
            reporter_user_id=UserID(actor.id),
            reportable_type=reportable_type,
            reportable_id=reportable_id,
            reason=reason,
            created_at=now_ms(),
        )
        row = self._repository.report(report_id)
        if row is None:
            raise internal_error()
        reporters = self._repository.authors((row.reporter_user_id,))
        return _report_response(
            row, reporters.get(row.reporter_user_id), self._targets_for((row,)).get((reportable_type, reportable_id))
        )

    def _targets_for(
        self, records: tuple[ReportRecord, ...] | list[ReportRecord]
    ) -> dict[tuple[ReportableType, int], ReportTarget]:
        discussion_ids = [
            DiscussionID(row.reportable_id) for row in records if row.reportable_type is ReportableType.Discussion
        ]
        reply_ids = [ReplyID(row.reportable_id) for row in records if row.reportable_type is ReportableType.Reply]
        user_ids = [UserID(row.reportable_id) for row in records if row.reportable_type is ReportableType.User]
        targets: dict[tuple[ReportableType, int], ReportTarget] = {}
        for target in self._repository.discussion_targets(discussion_ids).values():
            targets[(ReportableType.Discussion, target.id)] = DiscussionReportTarget(
                id=target.id,
                title=target.title,
                board_slug=target.board_slug,
            )
        for target in self._repository.reply_targets(reply_ids).values():
            targets[(ReportableType.Reply, target.id)] = ReplyReportTarget(
                id=target.id,
                discussion_id=target.discussion_id,
            )
        for target in self._repository.authors(user_ids).values():
            targets[(ReportableType.User, target.id)] = UserReportTarget(
                id=target.id,
                username=target.username,
                handle=make_handle(target.username, target.discriminator),
                display_name=target.display_name,
            )
        return targets

    def list_reports(
        self, actor: Actor, status: ReportStatus | None, cursor: ReportID | None, limit: int = 20
    ) -> ReportListResponse:
        AuthorizationService(self._conn).assert_can(actor, Abilities.MODERATION_VIEW, None)
        page_limit = min(limit, 50)
        records = self._repository.report_page(status=status, cursor=cursor, limit=page_limit)
        has_more = len(records) > limit
        page = records[:limit]
        reporters = self._repository.authors((row.reporter_user_id for row in page))
        targets = self._targets_for(page)
        items = [
            _report_response(
                row, reporters.get(row.reporter_user_id), targets.get((row.reportable_type, row.reportable_id))
            )
            for row in page
        ]
        return ReportListResponse(items=items, next_cursor=items[-1].id if has_more and items else None)

    def resolve_report(
        self, actor: Actor, report_id: ReportID, status: ReportStatus, action: str | None, reason: str | None
    ) -> ReportResponse:
        AuthorizationService(self._conn).assert_can(actor, Abilities.MODERATION_RESOLVE, None)
        row = self._repository.report(report_id)
        if row is None:
            raise not_found("Report not found")
        self._repository.update_report_status(report_id, status)
        self._repository.insert_action(
            actor_user_id=UserID(actor.id),
            action=action or f"report.{status.value}",
            target_type="report",
            target_id=report_id,
            reason=reason,
            created_at=now_ms(),
        )
        updated = ReportRecord(
            row.id, row.reporter_user_id, row.reportable_type, row.reportable_id, row.reason, status, row.created_at
        )
        reporters = self._repository.authors((row.reporter_user_id,))
        return _report_response(
            updated,
            reporters.get(row.reporter_user_id),
            self._targets_for((row,)).get((row.reportable_type, row.reportable_id)),
        )

    def ban_user(self, actor: Actor, username: str, reason: str | None, duration_hours: int | None) -> None:
        AuthorizationService(self._conn).assert_can(actor, Abilities.USER_BAN, None)
        target = self._repository.ban_target(normalize_username(username))
        if target is None:
            raise not_found("User not found")
        target_id = target.id
        target_role = target.role
        if int(target_id) == actor.id:
            raise conflict("Cannot ban yourself")
        if target_role == "admin":
            raise conflict("Cannot ban an admin")
        if target_role == "moderator" and actor.role != "admin":
            raise conflict("Cannot ban a moderator")
        timestamp = now_ms()
        banned_until = timestamp + duration_hours * 3_600_000 if duration_hours else None
        self._repository.insert_ban(
            user_id=target_id,
            banned_by_user_id=UserID(actor.id),
            reason=reason,
            banned_until=banned_until,
            created_at=timestamp,
        )
        self._repository.set_user_status(target_id, "banned", updated_at=timestamp)
        self._repository.delete_sessions(target_id)
        self._repository.insert_action(
            actor_user_id=UserID(actor.id),
            action="user.ban",
            target_type="user",
            target_id=target_id,
            reason=reason,
            created_at=timestamp,
        )
        self._outbox.emit(
            "user.banned",
            aggregate_type="user",
            aggregate_id=str(int(target_id)),
            payload=UserBannedPayload(
                user_id=target_id,
                banned_by_user_id=UserID(actor.id),
                reason=reason,
                banned_until=_iso(banned_until) if banned_until is not None else None,
            ),
        )

    def unban_user(self, actor: Actor, username: str, reason: str | None) -> None:
        AuthorizationService(self._conn).assert_can(actor, Abilities.MODERATION_UNBAN, None)
        target = self._repository.ban_target(normalize_username(username))
        if target is None:
            raise not_found("User not found")
        target_id = target.id
        timestamp = now_ms()
        self._repository.deactivate_active_bans(target_id)
        self._repository.set_user_status(target_id, "active", updated_at=timestamp)
        self._repository.insert_action(
            actor_user_id=UserID(actor.id),
            action="user.unban",
            target_type="user",
            target_id=target_id,
            reason=reason,
            created_at=timestamp,
        )

    def lift_ban_if_expired(self, user_id: int) -> bool:
        timestamp = now_ms()
        typed_user_id = UserID(user_id)
        active = self._repository.active_ban_deadlines(typed_user_id)
        if not active or any(deadline is None or deadline > timestamp for deadline in active):
            return False
        self._repository.deactivate_active_bans(typed_user_id)
        self._repository.set_user_status(typed_user_id, "active", updated_at=timestamp)
        return True

    def list_actions(
        self, actor: Actor, cursor: ModerationActionID | None, limit: int = 20
    ) -> ModerationActionListResponse:
        AuthorizationService(self._conn).assert_can(actor, Abilities.MODERATION_VIEW, None)
        page_limit = min(limit, 50)
        records = self._repository.action_page(cursor=cursor, limit=page_limit)
        has_more = len(records) > limit
        page = records[:limit]
        actors = self._repository.authors((row.actor_user_id for row in page))
        items = [
            ModerationActionResponse(
                id=row.id,
                actor=_author_response(actors[row.actor_user_id]) if row.actor_user_id in actors else None,
                action=row.action,
                target_type=row.target_type,
                target_id=row.target_id,
                reason=row.reason,
                created_at=row.created_at,
            )
            for row in page
        ]
        return ModerationActionListResponse(items=items, next_cursor=items[-1].id if has_more and items else None)

    def restore_content(self, actor: Actor, target_type: RestoreTargetType, target_id: int, reason: str | None) -> None:
        AuthorizationService(self._conn).assert_can(actor, Abilities.MODERATION_RESOLVE, None)
        timestamp = now_ms()
        if target_type is RestoreTargetType.Discussion:
            discussion_id = DiscussionID(target_id)
            content = self._repository.discussion_deletion(discussion_id)
            if content is None:
                raise not_found("Discussion not found")
            self._repository.restore_discussion(discussion_id, updated_at=timestamp)
            if content.deleted:
                self._repository.restore_discussion_attachments(discussion_id)
        else:
            reply_id = ReplyID(target_id)
            content = self._repository.reply_deletion(reply_id)
            if content is None:
                raise not_found("Reply not found")
            self._repository.restore_reply(reply_id, updated_at=timestamp)
            if content.deleted and content.discussion_id is not None:
                self._repository.increment_reply_count(content.discussion_id)
        self._repository.insert_action(
            actor_user_id=UserID(actor.id),
            action="content.restore",
            target_type=target_type.value,
            target_id=target_id,
            reason=reason,
            created_at=timestamp,
        )
