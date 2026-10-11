from uuid import uuid4

from app.engine.events import (
    ActorAudience,
    GameEvent,
    GlobalAudience,
    GroupAudience,
    PlayersAudience,
    RoomAudience,
)
from app.realtime.connections import QUEUE_SIZE, ConnectionManager


def event(audience: object, **fields: object) -> GameEvent:
    return GameEvent.model_validate({"kind": "test", "audience": audience, "text": "x", **fields})


def received(manager: ConnectionManager, *subs: object) -> list[int]:
    return [s.queue.qsize() for s in subs]  # type: ignore[attr-defined]


def test_routing_by_audience() -> None:
    manager = ConnectionManager()
    group = uuid4()
    anna = manager.subscribe(uuid4(), "forest", group)
    ben = manager.subscribe(uuid4(), "forest")
    cleo = manager.subscribe(uuid4(), "town", group)

    manager.deliver(event(RoomAudience(room_id="forest")))
    assert received(manager, anna, ben, cleo) == [1, 1, 0]
    manager.deliver(event(GroupAudience(group_id=group)))
    assert received(manager, anna, ben, cleo) == [2, 1, 1]
    manager.deliver(event(PlayersAudience(player_ids=[ben.player_id])))
    assert received(manager, anna, ben, cleo) == [2, 2, 1]
    manager.deliver(event(GlobalAudience()))
    assert received(manager, anna, ben, cleo) == [3, 3, 2]
    manager.deliver(event(ActorAudience()))  # never via SSE
    assert received(manager, anna, ben, cleo) == [3, 3, 2]


def test_excluded_players_never_receive() -> None:
    manager = ConnectionManager()
    anna = manager.subscribe(uuid4(), "forest")
    ben = manager.subscribe(uuid4(), "forest")
    manager.deliver(event(RoomAudience(room_id="forest"), exclude_player_ids=[anna.player_id]))
    assert received(manager, anna, ben) == [0, 1]


def test_relocate_switches_room_and_group_for_all_connections_of_a_player() -> None:
    manager = ConnectionManager()
    player = uuid4()
    tab1 = manager.subscribe(player, "forest")
    tab2 = manager.subscribe(player, "forest")
    group = uuid4()

    manager.relocate(player, "town", group)
    manager.deliver(event(RoomAudience(room_id="forest")))
    manager.deliver(event(RoomAudience(room_id="town")))
    manager.deliver(event(GroupAudience(group_id=group)))

    assert received(manager, tab1, tab2) == [2, 2]


def test_unsubscribe_and_connected() -> None:
    manager = ConnectionManager()
    sub = manager.subscribe(uuid4(), "forest")
    assert manager.connected(sub.player_id)
    manager.unsubscribe(sub)
    assert not manager.connected(sub.player_id)
    manager.deliver(event(RoomAudience(room_id="forest")))
    assert sub.queue.qsize() == 0


def test_slow_client_overflow_is_flagged_not_blocking() -> None:
    manager = ConnectionManager()
    sub = manager.subscribe(uuid4(), "forest")
    for _ in range(QUEUE_SIZE + 5):
        manager.deliver(event(RoomAudience(room_id="forest")))
    assert sub.queue.qsize() == QUEUE_SIZE
    assert sub.needs_reload
