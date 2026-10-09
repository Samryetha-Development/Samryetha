"""SQLAlchemy boundary for poll configuration, ballots, and aggregate counts."""

from sqlalchemy import case, distinct, func, select
from sqlalchemy.engine import Connection

from ..core.schema import discussion_polls, poll_options, poll_votes
from ..core.records import require_int, require_str
from .models import PollInput, PollOptionRecord, PollRecord


class PollRepository:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def get(self, discussion_id: int, viewer_id: int | None) -> PollRecord | None:
        total_voters = select(func.count(distinct(poll_votes.c.user_id))).where(
            poll_votes.c.discussion_id == discussion_id
        ).correlate(None).scalar_subquery()
        # Configuration, counts, and the viewer's selections share one read snapshot.
        rows = self._conn.execute(select(
            discussion_polls.c.question, discussion_polls.c.allow_multiple,
            poll_options.c.id, poll_options.c.label, poll_options.c.position,
            func.count(poll_votes.c.user_id).label("vote_count"),
            func.max(case((poll_votes.c.user_id == (viewer_id if viewer_id is not None else 0), 1), else_=0)).label("selected"),
            total_voters.label("total_voters"),
        ).select_from(discussion_polls.join(
            poll_options, poll_options.c.discussion_id == discussion_polls.c.discussion_id
        ).outerjoin(poll_votes, poll_votes.c.option_id == poll_options.c.id)).where(
            discussion_polls.c.discussion_id == discussion_id
        ).group_by(poll_options.c.id).order_by(poll_options.c.position)).mappings().all()
        if not rows:
            return None
        first = rows[0]
        return PollRecord(
            question=require_str(first["question"], "question"),
            allow_multiple=bool(first["allow_multiple"]),
            total_voters=require_int(first["total_voters"], "total_voters"),
            options=tuple(PollOptionRecord(
                id=require_int(row["id"], "option id"), label=require_str(row["label"], "label"),
                vote_count=require_int(row["vote_count"], "vote_count"), selected=bool(row["selected"]),
            ) for row in rows),
        )

    def configure(self, discussion_id: int, poll: PollInput | None) -> None:
        self._conn.execute(discussion_polls.delete().where(discussion_polls.c.discussion_id == discussion_id))
        if poll is not None:
            self._conn.execute(discussion_polls.insert().values(
                discussion_id=discussion_id, question=poll.question, allow_multiple=int(poll.allow_multiple),
            ))
            self._conn.execute(poll_options.insert(), [
                {"discussion_id": discussion_id, "label": label, "position": position}
                for position, label in enumerate(poll.options)
            ])

    def lock(self, discussion_id: int) -> bool:
        # Serialize configuration changes and ballot replacement before checking permissions again.
        result = self._conn.execute(discussion_polls.update().where(
            discussion_polls.c.discussion_id == discussion_id
        ).values(question=discussion_polls.c.question))
        return result.rowcount == 1

    def vote(self, discussion_id: int, user_id: int, option_ids: list[int]) -> None:
        self._conn.execute(poll_votes.delete().where(
            poll_votes.c.discussion_id == discussion_id, poll_votes.c.user_id == user_id,
        ))
        self._conn.execute(poll_votes.insert(), [
            {"discussion_id": discussion_id, "user_id": user_id, "option_id": option_id}
            for option_id in option_ids
        ])
