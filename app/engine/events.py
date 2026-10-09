from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ActorAudience(BaseModel):
    kind: Literal["actor"] = "actor"
    player_id: UUID


class RoomAudience(BaseModel):
    kind: Literal["room"] = "room"
    room_id: str


class GroupAudience(BaseModel):
    kind: Literal["group"] = "group"
    group_id: UUID


class PlayerAudience(BaseModel):
    kind: Literal["player"] = "player"
    player_id: UUID


class GlobalAudience(BaseModel):
    kind: Literal["global"] = "global"


Audience = Annotated[
    ActorAudience | RoomAudience | GroupAudience | PlayerAudience | GlobalAudience,
    Field(discriminator="kind"),
]


class GameEvent(BaseModel):
    event_type: str
    audience: Audience
    payload: dict[str, Any] = Field(default_factory=dict)
