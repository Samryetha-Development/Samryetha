"""Small shared HTTP response contracts for system endpoints."""

from pydantic import BaseModel, ConfigDict


def _to_camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.capitalize() for part in tail)


class SystemHttpModel(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)


class FollowResponse(SystemHttpModel):
    following: bool


class PresenceResponse(SystemHttpModel):
    online_count: int


class HealthResponse(SystemHttpModel):
    status: str
