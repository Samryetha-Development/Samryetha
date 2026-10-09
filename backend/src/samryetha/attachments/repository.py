"""Typed SQLAlchemy persistence boundary for attachments."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import insert, or_, select, update
from sqlalchemy.engine import Connection, RowMapping
from sqlalchemy.sql.elements import ColumnElement

from .models import AttachmentRecord, AttachmentState, CreateAttachment
from ..core.db import now_ms
from ..core.ids import AttachmentID, DiscussionID, UserID
from ..core.schema import attachments, boards, discussions, draft_attachments, file_resources, users
from ..core.records import opt_int, require_int, require_str


@dataclass(frozen=True, slots=True)
class BoardAccessRecord:
    id: int
    visibility: str
    posting_policy: str


@dataclass(frozen=True, slots=True)
class AttachmentPromotionSource:
    """可被转入文件服务的附件快照（含父帖板块可见性与原上传者）。

    A snapshot of an attachment that may be promoted into the file service, including the
    visibility of the board its parent discussion lives on and its original uploader.
    """

    attachment_id: int
    object_key: str
    original_filename: str
    size_bytes: int
    board_visibility: str
    # 原上传者（与执行转入的管理员无关）。附件不按上传者过滤，任何用户的附件都可被转入。
    # The original uploader, independent of the admin performing the promotion. Attachments are
    # not filtered by uploader, so any user's attachment may be promoted.
    uploader_id: int
    # 原上传者是否仍可持有资料（账号 active 且未软删）。为 True 时资料 owner 取原上传者，
    # 使非管理员上传者转入后仍出现在其「我的上传」并持有 file.update/file.delete；
    # 为 False（已封禁/已注销）时服务层改由执行转入的管理员持有，避免产出无人可管的资料。
    # Whether the original uploader may still own a resource (account active and not
    # soft-deleted). When True the resource is owned by the uploader, so a non-admin uploader
    # keeps it in "my uploads" with file.update/file.delete; when False (banned or deleted) the
    # service hands ownership to the acting admin instead of producing an unmanageable resource.
    uploader_eligible: bool


def _record(row: RowMapping) -> AttachmentRecord:
    discussion_id = opt_int(row["discussion_id"], "discussion id")
    return AttachmentRecord(
        id=AttachmentID(require_int(row["id"], "attachment id")),
        uploader_id=UserID(require_int(row["uploader_id"], "uploader id")),
        discussion_id=DiscussionID(discussion_id) if discussion_id is not None else None,
        object_key=require_str(row["object_key"], "object key"),
        original_filename=require_str(row["original_filename"], "original filename"),
        mime_type=require_str(row["mime_type"], "mime type"),
        size_bytes=require_int(row["size_bytes"], "size bytes"),
        state=AttachmentState(require_str(row["state"], "state")),
        created_at=require_int(row["created_at"], "created at"),
    )


class AttachmentRepository:
    """Typed persistence operations; transaction ownership remains with the caller."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def create_pending(
        self, uploader_id: UserID, command: CreateAttachment, object_key: str, safe_mime_type: str
    ) -> AttachmentID:
        value = self._conn.execute(
            insert(attachments)
            .values(
                uploader_id=uploader_id,
                object_key=object_key,
                original_filename=command.filename,
                mime_type=safe_mime_type,
                size_bytes=command.size_bytes,
                created_at=now_ms(),
            )
            .returning(attachments.c.id)
        ).scalar_one()
        return AttachmentID(require_int(value, "inserted attachment id"))

    def get(self, attachment_id: AttachmentID) -> AttachmentRecord | None:
        row = self._conn.execute(select(attachments).where(attachments.c.id == attachment_id)).mappings().first()
        return None if row is None else _record(row)

    def get_by_object_key(self, object_key: str) -> AttachmentRecord | None:
        row = self._conn.execute(select(attachments).where(attachments.c.object_key == object_key)).mappings().first()
        return None if row is None else _record(row)

    def list_attached(self, discussion_id: DiscussionID) -> list[AttachmentRecord]:
        rows = self._conn.execute(
            select(attachments)
            .where(
                (attachments.c.discussion_id == discussion_id) & (attachments.c.state == AttachmentState.Attached.value)
            )
            .order_by(attachments.c.id)
        ).mappings()
        return [_record(row) for row in rows]

    def user_status(self, user_id: UserID) -> str | None:
        value = self._conn.execute(select(users.c.status).where(users.c.id == user_id)).scalar_one_or_none()
        return None if value is None else require_str(value, "user status")

    def mark_uploaded(self, object_key: str) -> bool:
        result = self._conn.execute(
            update(attachments)
            .where((attachments.c.object_key == object_key) & (attachments.c.state == AttachmentState.Pending.value))
            .values(state=AttachmentState.Uploaded.value)
        )
        return (result.rowcount or 0) == 1

    def remove(self, attachment_id: AttachmentID) -> None:
        self._conn.execute(attachments.delete().where(attachments.c.id == attachment_id))

    def _unclaimed_by_resource(self) -> ColumnElement[bool]:
        """该附件的对象尚未被文件服务资料认领。

        附件转入资料是"认领"（复用同一个 object_key），所以对象可能同时被两个子系统引用。
        回收器只应清理不再被任何一方引用的对象，否则会把资料的字节删掉。
        The attachment's object has not been claimed by a file-service resource. Promotion
        claims the object (the same object_key), so an object can belong to two subsystems at
        once; the reaper may only collect objects nothing references any more, or it would
        delete the resource's bytes.
        """
        return (
            ~select(file_resources.c.id).where(file_resources.c.object_key == attachments.c.object_key).exists()
        )

    def orphan_candidates(self, pending_cutoff: int, uploaded_cutoff: int) -> Sequence[tuple[AttachmentID, str]]:
        pending_rows = self._conn.execute(
            select(attachments.c.id, attachments.c.object_key).where(
                (attachments.c.created_at < pending_cutoff)
                & or_(
                    attachments.c.state == AttachmentState.Pending.value,
                    attachments.c.state == AttachmentState.Orphaned.value,
                )
                & self._unclaimed_by_resource()
            )
        ).all()
        uploaded_rows = self._conn.execute(
            select(attachments.c.id, attachments.c.object_key).where(
                (attachments.c.state == AttachmentState.Uploaded.value)
                & attachments.c.discussion_id.is_(None)
                & (attachments.c.created_at < uploaded_cutoff)
                & ~select(draft_attachments.c.attachment_id)
                .where(draft_attachments.c.attachment_id == attachments.c.id)
                .exists()
                & self._unclaimed_by_resource()
            )
        ).all()
        return [
            (AttachmentID(require_int(row.id, "attachment id")), require_str(row.object_key, "object key"))
            for row in (*pending_rows, *uploaded_rows)
        ]

    def promotion_source(self, attachment_id: AttachmentID) -> AttachmentPromotionSource | None:
        """读取「可转入文件服务」的附件快照；不可转换时返回 None。

        只有「已挂到存活讨论帖、且该帖所在板块仍存活」的附件才算可转换：
        未挂帖的待发布草稿与已撤下的内容都不允许通过转换路径对外发布。
        Reads the snapshot of an attachment that may be promoted; returns None when it is not
        eligible. Only attachments already attached to a live discussion on a live board are
        eligible: unpublished drafts and withdrawn content must not be published through the
        promotion path.
        """
        row = (
            self._conn.execute(
                select(
                    attachments.c.object_key,
                    attachments.c.original_filename,
                    attachments.c.size_bytes,
                    attachments.c.uploader_id,
                    users.c.status.label("uploader_status"),
                    users.c.deleted_at.label("uploader_deleted_at"),
                    boards.c.visibility.label("board_visibility"),
                )
                .select_from(
                    attachments.join(discussions, discussions.c.id == attachments.c.discussion_id).join(
                        boards, boards.c.id == discussions.c.board_id
                    ).join(users, users.c.id == attachments.c.uploader_id)
                )
                .where(
                    (attachments.c.id == attachment_id)
                    & (attachments.c.state == AttachmentState.Attached.value)
                    & (attachments.c.discussion_id.is_not(None))
                    & discussions.c.deleted_at.is_(None)
                    & boards.c.deleted_at.is_(None)
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return AttachmentPromotionSource(
            attachment_id=int(attachment_id),
            object_key=require_str(row["object_key"], "object key"),
            original_filename=require_str(row["original_filename"], "original filename"),
            size_bytes=require_int(row["size_bytes"], "size bytes"),
            board_visibility=require_str(row["board_visibility"], "board visibility"),
            uploader_id=require_int(row["uploader_id"], "uploader id"),
            # 只有「账号 active 且未软删」的原上传者才接手成为资料 owner；
            # 封禁或已注销的账号登录不了，指派给他会让资料变成无人可管的孤儿。
            # Only an account that is active and not soft-deleted may take ownership; a banned
            # or deleted account cannot sign in, and assigning the resource to it would strand
            # it with no one able to manage it.
            uploader_eligible=(
                require_str(row["uploader_status"], "uploader status") == "active"
                and opt_int(row["uploader_deleted_at"], "uploader deleted at") is None
            ),
        )

    def claimed_by_resource(self, object_key: str) -> bool:
        """该对象是否已被文件服务的资料认领。

        Whether the file service has already claimed this object.
        """
        return (
            self._conn.execute(select(file_resources.c.id).where(file_resources.c.object_key == object_key)).first()
            is not None
        )

    def active_board(self, board_id: int) -> BoardAccessRecord | None:
        row = (
            self._conn.execute(
                select(boards.c.id, boards.c.visibility, boards.c.posting_policy).where(
                    boards.c.id == board_id, boards.c.deleted_at.is_(None)
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return BoardAccessRecord(
            id=require_int(row["id"], "board id"),
            visibility=require_str(row["visibility"], "board visibility"),
            posting_policy=require_str(row["posting_policy"], "board posting policy"),
        )
