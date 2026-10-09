"""Poll contracts shared by discussion publication and private drafts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


def _to_camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.capitalize() for part in tail)


class PollModel(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, extra="forbid")


class PollDraftInput(PollModel):
    # Keep incomplete compositions editable when a draft is restored.
    question: Annotated[str, Field(max_length=200)] = ""
    allow_multiple: bool = Field(default=False, strict=True)
    options: list[Annotated[str, Field(max_length=100)]] = Field(min_length=2, max_length=10)


class PollInput(PollModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    allow_multiple: bool = Field(default=False, strict=True)
    options: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]] = Field(min_length=2, max_length=10)

    @model_validator(mode="after")
    def unique_options(self) -> Self:
        if len({option.casefold() for option in self.options}) != len(self.options):
            raise ValueError("Poll options must be distinct")
        return self


class PollVoteBody(PollModel):
    option_ids: list[Annotated[int, Field(ge=1, strict=True)]] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def unique_choices(self) -> Self:
        if len(set(self.option_ids)) != len(self.option_ids):
            raise ValueError("Choose each option only once")
        return self


class PollOptionResponse(PollModel):
    id: int
    label: str
    vote_count: int


class PollResponse(PollModel):
    question: str
    allow_multiple: bool
    options: list[PollOptionResponse]
    total_voters: int
    total_votes: int
    viewer_option_ids: list[int]
    can_vote: bool


@dataclass(frozen=True, slots=True)
class PollOptionRecord:
    id: int
    label: str
    vote_count: int
    selected: bool


@dataclass(frozen=True, slots=True)
class PollRecord:
    question: str
    allow_multiple: bool
    options: tuple[PollOptionRecord, ...]
    total_voters: int
