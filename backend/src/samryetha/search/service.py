"""Typed SQLite substring search service."""

from __future__ import annotations

from samryetha.search.repository import SearchRepository

import re

from sqlalchemy.engine import Connection

from ..authz import Actor
from ..discussions import preview, DiscussionService
from .models import SearchAuthor, SearchBoard, SearchItem, SearchOptions, SearchResult
from ..users import make_handle

_LIKE_ESCAPE = re.compile(r"[\\%_]")


def escape_like(value: str) -> str:
    return _LIKE_ESCAPE.sub(lambda match: "\\" + match.group(0), value)


class SearchService:
    """Application use cases within the caller-owned transaction."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self._repository = SearchRepository(self._conn)

    def search_discussions(
        self,
        viewer: Actor | None,
        options: SearchOptions,
    ) -> SearchResult:
        query = escape_like(options.query.strip()[:100])
        limit = min(options.limit, 50)
        visible = DiscussionService(self._conn).visible_board_ids(viewer)
        records, total = self._repository.search_records(
            viewer=viewer, visible_board_ids=visible, query=query, board_slug=options.board_slug, limit=limit
        )
        board_ids = {record.board_id for record in records}
        author_ids = {record.author_id for record in records}

        board_records = self._repository.board_map(board_ids)
        author_records = self._repository.author_map(author_ids)

        items: list[SearchItem] = []
        for record in records:
            board_record = board_records.get(record.board_id)
            board = (
                SearchBoard(record.board_id, "", "")
                if board_record is None
                else SearchBoard(
                    id=board_record.id,
                    slug=board_record.slug,
                    name=board_record.name,
                )
            )
            author_record = author_records.get(record.author_id)
            author = (
                SearchAuthor(record.author_id, "", "", "")
                if author_record is None
                else SearchAuthor(
                    id=author_record.id,
                    username=author_record.username,
                    handle=make_handle(author_record.username, author_record.discriminator),
                    display_name=author_record.display_name,
                )
            )
            items.append(
                SearchItem(
                    id=record.id,
                    title=record.title,
                    preview=preview(record.body_md),
                    board=board,
                    author=author,
                    reply_count=record.reply_count,
                    is_pinned=record.is_pinned,
                    is_locked=record.is_locked,
                    moderation_status=record.moderation_status,
                    created_at=record.created_at,
                    last_activity_at=record.last_reply_at or record.created_at,
                )
            )
        return SearchResult(items=tuple(items), total=total)
