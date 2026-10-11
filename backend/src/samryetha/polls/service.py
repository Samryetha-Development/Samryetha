"""Discussion poll operations in the caller's transaction."""

from sqlalchemy.engine import Connection

from ..authz import Actor, AuthorizationService
from ..core.errors import conflict, forbidden, not_found, validation_failed
from .models import PollInput, PollOptionResponse, PollResponse, PollVoteBody
from .repository import PollRepository


class PollService:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self._repository = PollRepository(conn)

    def get(self, discussion_id: int, viewer: Actor | None, *, locked: bool) -> PollResponse | None:
        record = self._repository.get(discussion_id, viewer.id if viewer else None)
        if record is None:
            return None
        return PollResponse(
            question=record.question, allow_multiple=record.allow_multiple,
            options=[PollOptionResponse(id=option.id, label=option.label, vote_count=option.vote_count)
                     for option in record.options],
            total_voters=record.total_voters, total_votes=sum(option.vote_count for option in record.options),
            viewer_option_ids=[option.id for option in record.options if option.selected],
            can_vote=viewer is not None and viewer.status == "active" and not locked,
        )

    def configure(self, discussion_id: int, poll: PollInput | None) -> None:
        # DiscussionService already holds its write lock and has checked edit permissions.
        current = self._repository.get(discussion_id, None)
        if current is not None:
            if poll is not None and (current.question, current.allow_multiple, [o.label for o in current.options]) == (
                poll.question, poll.allow_multiple, poll.options,
            ):
                return  # Preserve option IDs and all votes on ordinary post edits.
            if current.total_voters:
                raise conflict("A poll with votes cannot be changed or removed")
        self._repository.configure(discussion_id, poll)

    def vote(self, actor: Actor, discussion_id: int, ballot: PollVoteBody) -> PollResponse:
        from ..discussions.service import DiscussionService

        discussions = DiscussionService(self._conn)
        discussions.get(actor, discussion_id)  # Same board, deletion, and report visibility as the post.
        if not self._repository.lock(discussion_id):
            raise not_found("Poll not found")
        AuthorizationService(self._conn).assert_actor_current(actor.id, expected_role=actor.role)
        discussion = discussions.get(actor, discussion_id)  # Recheck after acquiring the write lock.
        poll = discussion.poll
        if poll is None:
            raise not_found("Poll not found")
        if not poll.can_vote:
            raise forbidden("This poll is closed")
        if (not poll.allow_multiple and len(ballot.option_ids) != 1) or not set(ballot.option_ids) <= {
            option.id for option in poll.options
        }:
            raise validation_failed([{"field": "optionIds", "message": "Choose valid poll options", "code": "custom"}])
        self._repository.vote(discussion_id, actor.id, ballot.option_ids)
        result = self.get(discussion_id, actor, locked=False)
        if result is None:
            raise not_found("Poll not found")
        return result
