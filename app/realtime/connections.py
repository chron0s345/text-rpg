"""SSE connections of this process and which events reach them (ARCHITECTURE.md, "Echtzeit").

Every connected client has one ``Subscription``: its player plus the room and group it currently
listens to. Routing rules:

- ``Room(id)``   -> subscriptions in that room
- ``Group(id)``  -> subscriptions of members of that group
- ``Players``    -> the listed players
- ``Global``     -> everybody
- ``Actor``      -> nobody (the actor gets its results via the HTTP response)
- players in ``exclude_player_ids`` never receive the event.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import UUID

from app.engine.events import (
    GameEvent,
    GlobalAudience,
    GroupAudience,
    PlayersAudience,
    RoomAudience,
)

QUEUE_SIZE = 256


@dataclass(eq=False)
class Subscription:
    player_id: UUID
    room_id: str
    group_id: UUID | None = None
    queue: asyncio.Queue[GameEvent] = field(
        default_factory=lambda: asyncio.Queue(maxsize=QUEUE_SIZE)
    )
    # set when events may be missing (client too slow, bus reconnected, publishing failed):
    # the client must reload its view, the SSE handler re-subscribes from the database
    needs_reload: bool = False

    def wants(self, event: GameEvent) -> bool:
        if self.player_id in event.exclude_player_ids:
            return False
        audience = event.audience
        if isinstance(audience, RoomAudience):
            return audience.room_id == self.room_id
        if isinstance(audience, GroupAudience):
            return self.group_id is not None and audience.group_id == self.group_id
        if isinstance(audience, PlayersAudience):
            return self.player_id in audience.player_ids
        return isinstance(audience, GlobalAudience)

    def offer(self, event: GameEvent) -> None:
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.needs_reload = True


class ConnectionManager:
    """Subscriptions held by this process. Not thread-safe; used from one event loop."""

    def __init__(self) -> None:
        self._subscriptions: set[Subscription] = set()

    def subscribe(
        self, player_id: UUID, room_id: str, group_id: UUID | None = None
    ) -> Subscription:
        subscription = Subscription(player_id=player_id, room_id=room_id, group_id=group_id)
        self._subscriptions.add(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        self._subscriptions.discard(subscription)

    def connected(self, player_id: UUID) -> bool:
        return any(s.player_id == player_id for s in self._subscriptions)

    def deliver(self, event: GameEvent) -> None:
        for subscription in list(self._subscriptions):
            if subscription.wants(event):
                subscription.offer(event)

    def mark_all_for_reload(self) -> None:
        for subscription in self._subscriptions:
            subscription.needs_reload = True

    def relocate(self, player_id: UUID, room_id: str, group_id: UUID | None) -> None:
        """Room or group of a player changed: switch what its connections listen to."""
        for subscription in self._subscriptions:
            if subscription.player_id == player_id:
                subscription.room_id = room_id
                subscription.group_id = group_id
