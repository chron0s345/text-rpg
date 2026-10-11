"""ActionService against PostgreSQL: persistence, commit-before-publish, rate limit, retries,
log entries, lazy respawn, and concurrent actions."""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlmodel import col

from app.api.schemas import (
    AttackAction,
    BuyAction,
    LookAction,
    MoveAction,
    PickUpAction,
    RespawnAction,
    SayAction,
)
from app.db.models import CombatParticipation, FloorItem, InventoryItem, LogEntry, MonsterInstance
from app.db.models import Player as PlayerRow
from app.db.repository import Repository, SnapshotChangedError
from app.services.action_service import (
    BUSY,
    NOT_AVAILABLE,
    TOO_FAST,
    NotPlayingError,
    PreRead,
    RetryLimitExceededError,
)
from tests.db.factories import NOW, add_player
from tests.pg import backend_pid, wait_until_blocked
from tests.services.conftest import ServiceHarness


async def player(h: ServiceHarness, room: str, **fields: Any) -> PlayerRow:
    h.committed.rooms.append(room)
    async with h.committed.session() as db, db.begin():
        row = await add_player(db, current_room_id=room, **fields)
        h.committed.accounts.append(row.account_id)
    return row


async def load(h: ServiceHarness, model: type[Any], row_id: object) -> Any:
    async with h.committed.session() as db:
        return await db.get(model, row_id)


async def logs(h: ServiceHarness, player_id: UUID) -> list[str]:
    async with h.committed.session() as db:
        rows = await db.scalars(
            select(LogEntry).where(col(LogEntry.player_id) == player_id).order_by(col(LogEntry.id))
        )
        return [r.text for r in rows.all()]


def texts(events: list[Any]) -> list[str]:
    return [e.text for e in events]


# --- happy path: persist, then publish ---


async def test_move_persists_publishes_and_logs(harness: ServiceHarness) -> None:
    h = harness
    gate, forest = h.scratch_path()
    actor = await player(h, gate, name=f"Anna{uuid4().hex[:4]}")
    stays = await player(h, gate)
    waits = await player(h, forest)

    outcome = await h.service.execute(actor.id, MoveAction(direction="south"))

    assert texts(outcome.events) == ["Du gehst nach Süden: Dunkler Wald."]
    assert (await load(h, PlayerRow, actor.id)).current_room_id == forest
    # other players learn about it via the publisher, the actor only via the HTTP response
    assert {e.kind for e in h.publisher.published} == {"move.leave", "move.enter"}
    assert all(e.audience.kind != "actor" for e in h.publisher.published)
    # everybody concerned gets a log entry (reconnect catch-up)
    assert await logs(h, actor.id) == ["Du gehst nach Süden: Dunkler Wald."]
    assert await logs(h, stays.id) == [f"{actor.name} geht nach Süden."]
    assert await logs(h, waits.id) == [f"{actor.name} kommt herein."]


async def test_kill_through_the_service(harness: ServiceHarness) -> None:
    h = harness
    room = h.scratch_room()
    actor = await player(h, room, base_attack=50)
    async with h.committed.session() as db, db.begin():
        goblin = MonsterInstance(monster_id="goblin", room_id=room, hp=3)
        db.add(goblin)

    outcome = await h.service.execute(actor.id, AttackAction(target_id=goblin.id))

    assert "Du hast Goblin-Späher besiegt." in texts(outcome.events)
    row = await load(h, PlayerRow, actor.id)
    assert (row.state, row.xp) == ("exploring", 15)
    assert row.gold > 0
    monster = await load(h, MonsterInstance, goblin.id)
    assert (monster.alive, monster.respawn_at) == (False, NOW + timedelta(seconds=60))


# --- rules enforced by the service ---


async def test_rate_limit(harness: ServiceHarness) -> None:
    h = harness
    gate, forest = h.scratch_path()
    actor = await player(h, gate)
    await h.service.execute(actor.id, LookAction())
    h.publisher.published.clear()

    h.clock.advance(milliseconds=100)
    blocked = await h.service.execute(actor.id, MoveAction(direction="south"))
    assert texts(blocked.events) == [TOO_FAST]
    assert (await load(h, PlayerRow, actor.id)).current_room_id == gate
    assert h.publisher.published == []

    h.clock.advance(seconds=1)
    allowed = await h.service.execute(actor.id, MoveAction(direction="south"))
    assert texts(allowed.events) == ["Du gehst nach Süden: Dunkler Wald."]


async def test_offline_deleted_or_unknown_characters_cannot_act(harness: ServiceHarness) -> None:
    h = harness
    gate, forest = h.scratch_path()
    offline = await player(h, gate, online=False)
    deleted = await player(h, gate, online=False, deleted_at=NOW)
    for player_id in (offline.id, deleted.id, uuid4()):
        with pytest.raises(NotPlayingError):
            await h.service.execute(player_id, LookAction())
    assert h.publisher.published == []


async def test_actions_of_later_steps_are_rejected(harness: ServiceHarness) -> None:
    h = harness
    actor = await player(h, h.scratch_room("town_market"))
    outcome = await h.service.execute(actor.id, BuyAction(merchant_id="alchemist", item_id="bread"))
    assert texts(outcome.events) == [NOT_AVAILABLE]


async def test_say_reaches_room_through_publisher(harness: ServiceHarness) -> None:
    h = harness
    gate, forest = h.scratch_path()
    actor = await player(h, gate)
    listener = await player(h, gate)
    await h.service.execute(actor.id, SayAction(text="<b>Hallo</b>"))
    [said] = h.publisher.published
    assert said.data["message"] == "<b>Hallo</b>"  # escaping happens when rendering
    assert len(await logs(h, listener.id)) == 1


# --- transactions: rollback never publishes, conflicts are retried ---


async def test_failure_rolls_back_and_publishes_nothing(
    harness: ServiceHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness
    gate, forest = h.scratch_path()
    actor = await player(h, gate)

    async def broken(self: Repository, changes: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(Repository, "apply_changes", broken)
    with pytest.raises(RuntimeError):
        await h.service.execute(actor.id, MoveAction(direction="south"))

    row = await load(h, PlayerRow, actor.id)
    assert (row.current_room_id, row.last_action_at) == (gate, None)
    assert h.publisher.published == []
    assert await logs(h, actor.id) == []


async def test_snapshot_conflicts_are_retried_with_a_new_repository(
    harness: ServiceHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness
    gate, forest = h.scratch_path()
    actor = await player(h, gate)
    original = Repository.load_action_snapshot
    repositories: list[Repository] = []

    async def flaky(self: Repository, actor_id: UUID, room_id: str | None = None) -> Any:
        repositories.append(self)
        if len(repositories) == 1:
            raise SnapshotChangedError("group changed")
        return await original(self, actor_id, room_id)

    monkeypatch.setattr(Repository, "load_action_snapshot", flaky)
    outcome = await h.service.execute(actor.id, MoveAction(direction="south"))

    assert texts(outcome.events) == ["Du gehst nach Süden: Dunkler Wald."]
    assert len(repositories) == 2 and repositories[0] is not repositories[1]
    assert len(h.publisher.published) == 2  # published once, by the successful attempt


async def test_retry_limit(harness: ServiceHarness, monkeypatch: pytest.MonkeyPatch) -> None:
    h = harness
    gate, forest = h.scratch_path()
    actor = await player(h, gate)

    async def always(self: Repository, actor_id: UUID, room_id: str | None = None) -> Any:
        raise SnapshotChangedError("busy")

    monkeypatch.setattr(Repository, "load_action_snapshot", always)
    with pytest.raises(RetryLimitExceededError):
        await h.service.execute(actor.id, LookAction())
    assert h.publisher.published == []


async def test_actor_moved_between_pre_read_and_lock_is_retried(
    harness: ServiceHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness
    gate, forest = h.scratch_path()
    actor = await player(h, gate)
    service = h.service
    original = service._pre_read
    calls = 0

    async def stale_first(session: Any, player_id: UUID) -> PreRead:
        nonlocal calls
        calls += 1
        pre = await original(session, player_id)
        return dataclasses.replace(pre, room_id="goblin_camp") if calls == 1 else pre

    monkeypatch.setattr(service, "_pre_read", stale_first)
    outcome = await service.execute(actor.id, MoveAction(direction="south"))
    assert calls == 2
    assert texts(outcome.events) == ["Du gehst nach Süden: Dunkler Wald."]


# --- lazy respawn ---


async def test_entering_a_room_respawns_due_monsters(harness: ServiceHarness) -> None:
    h = harness
    gate, forest = h.scratch_path()
    actor = await player(h, gate)
    async with h.committed.session() as db, db.begin():
        dead = MonsterInstance(
            monster_id="wolf",
            room_id=forest,
            hp=0,
            alive=False,
            respawn_at=NOW - timedelta(seconds=1),
        )
        db.add(dead)

    outcome = await h.service.execute(actor.id, MoveAction(direction="south"))

    assert "Hungriger Wolf taucht auf." in texts(outcome.events)
    row = await load(h, MonsterInstance, dead.id)
    assert (row.alive, row.hp, row.respawn_at) == (True, 18, None)


# --- concurrency ---


async def test_two_players_attack_the_same_monster_concurrently(harness: ServiceHarness) -> None:
    h = harness
    room = h.scratch_room()
    anna = await player(h, room)
    ben = await player(h, room)
    async with h.committed.session() as db, db.begin():
        wolf = MonsterInstance(monster_id="wolf", room_id=room, hp=18)
        db.add(wolf)

    await asyncio.gather(
        h.service.execute(anna.id, AttackAction(target_id=wolf.id)),
        h.service.execute(ben.id, AttackAction(target_id=wolf.id)),
    )

    async with h.committed.session() as db:
        parts = (
            await db.scalars(
                select(CombatParticipation).where(
                    col(CombatParticipation.monster_instance_id) == wolf.id
                )
            )
        ).all()
        monster = await db.get(MonsterInstance, wolf.id)
    assert monster is not None
    assert {p.player_id for p in parts} == {anna.id, ben.id}
    # no lost update: every point of damage dealt is reflected in the monster's HP
    assert monster.hp == 18 - sum(p.damage_dealt for p in parts)


async def test_two_players_pick_up_the_same_item_concurrently(harness: ServiceHarness) -> None:
    h = harness
    gate, forest = h.scratch_path()
    anna = await player(h, gate)
    ben = await player(h, gate)
    async with h.committed.session() as db, db.begin():
        pile = FloorItem(
            room_id=gate,
            item_id="goblin_ear",
            quantity=3,
            source="loot",
            expires_at=NOW + timedelta(minutes=5),
        )
        db.add(pile)

    outcomes = await asyncio.gather(
        h.service.execute(anna.id, PickUpAction(floor_item_id=pile.id)),
        h.service.execute(ben.id, PickUpAction(floor_item_id=pile.id)),
    )

    results = sorted(texts(o.events)[0] for o in outcomes)
    assert results == ["Das liegt hier nicht.", "Du hebst 3x Goblinohr auf."]
    async with h.committed.session() as db:
        ears = (
            await db.scalars(
                select(InventoryItem).where(
                    col(InventoryItem.player_id).in_([anna.id, ben.id]),
                    col(InventoryItem.item_id) == "goblin_ear",
                )
            )
        ).all()
        assert await db.get(FloorItem, pile.id) is None
    assert sum(i.quantity for i in ears) == 3  # nothing duplicated, nothing lost


# --- review follow-ups ---


async def test_concurrent_requests_of_one_player_execute_once(harness: ServiceHarness) -> None:
    """Exactly one of several simultaneous requests runs. (That rejected requests do not
    wait for locks is proven by the two fail-fast tests below.)"""
    h = harness
    gate, forest = h.scratch_path()
    actor = await player(h, gate)

    outcomes = await asyncio.gather(
        *(h.service.execute(actor.id, MoveAction(direction="south")) for _ in range(5))
    )

    results = [texts(o.events)[0] for o in outcomes]
    assert results.count("Du gehst nach Süden: Dunkler Wald.") == 1
    assert all(r in (TOO_FAST, BUSY) for r in results if not r.startswith("Du gehst"))
    assert (await load(h, PlayerRow, actor.id)).current_room_id == forest
    # rejected requests leave no trace: one log entry, events of one move only
    assert await logs(h, actor.id) == ["Du gehst nach Süden: Dunkler Wald."]
    assert len(h.publisher.published) == 2


async def test_failing_publisher_does_not_fail_a_saved_action(
    harness: ServiceHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness
    gate, forest = h.scratch_path()
    actor = await player(h, gate)

    async def broken(events: object, relocations: object = ()) -> None:
        raise ConnectionError("bus down")

    monkeypatch.setattr(h.publisher, "publish", broken)
    outcome = await h.service.execute(actor.id, MoveAction(direction="south"))

    assert texts(outcome.events) == ["Du gehst nach Süden: Dunkler Wald."]
    assert (await load(h, PlayerRow, actor.id)).current_room_id == forest


async def test_publisher_sees_committed_state(
    harness: ServiceHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness
    gate, forest = h.scratch_path()
    actor = await player(h, gate)
    seen: list[str] = []

    async def checking(events: object, relocations: object = ()) -> None:
        # a different connection: only committed data is visible here
        seen.append((await load(h, PlayerRow, actor.id)).current_room_id)

    monkeypatch.setattr(h.publisher, "publish", checking)
    await h.service.execute(actor.id, MoveAction(direction="south"))
    assert seen == [forest]


class FakePgError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


@pytest.mark.parametrize("sqlstate", ["40P01", "40001"], ids=["deadlock", "serialization"])
async def test_deadlock_and_serialization_failures_are_retried(
    harness: ServiceHarness, monkeypatch: pytest.MonkeyPatch, sqlstate: str
) -> None:
    from sqlalchemy.exc import DBAPIError

    h = harness
    gate, forest = h.scratch_path()
    actor = await player(h, gate)
    original = Repository.load_action_snapshot
    calls = 0

    async def conflicting_once(self: Repository, actor_id: UUID, room_id: str | None = None) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise DBAPIError("SELECT ...", None, FakePgError(sqlstate))
        return await original(self, actor_id, room_id)

    monkeypatch.setattr(Repository, "load_action_snapshot", conflicting_once)
    outcome = await h.service.execute(actor.id, MoveAction(direction="south"))
    assert calls == 2
    assert texts(outcome.events) == ["Du gehst nach Süden: Dunkler Wald."]


async def test_other_database_errors_are_not_retried(
    harness: ServiceHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.exc import DBAPIError

    h = harness
    gate, _forest = h.scratch_path()
    actor = await player(h, gate)
    calls = 0

    async def failing(self: Repository, actor_id: UUID, room_id: str | None = None) -> Any:
        nonlocal calls
        calls += 1
        raise DBAPIError("SELECT ...", None, FakePgError("23505"))

    monkeypatch.setattr(Repository, "load_action_snapshot", failing)
    with pytest.raises(DBAPIError):
        await h.service.execute(actor.id, LookAction())
    assert calls == 1
    assert h.publisher.published == []


@pytest.mark.parametrize("change", ["offline", "deleted"])
async def test_logout_or_delete_while_waiting_for_the_lock(
    harness: ServiceHarness, change: str
) -> None:
    h = harness
    gate, _forest = h.scratch_path()
    actor = await player(h, gate)

    async with h.committed.session() as writer:
        await writer.begin()
        writer_pid = await backend_pid(writer)
        row = (
            await writer.scalars(
                select(PlayerRow).where(col(PlayerRow.id) == actor.id).with_for_update()
            )
        ).one()
        if change == "offline":
            row.online = False
        else:
            row.online, row.deleted_at = False, NOW
        task = asyncio.create_task(h.service.execute(actor.id, LookAction()))
        try:
            await wait_until_blocked(h.committed.engine, writer_pid)
        finally:
            await writer.commit()
        with pytest.raises(NotPlayingError):
            await asyncio.wait_for(task, timeout=10)
    assert h.publisher.published == []


async def test_clock_going_backwards_never_locks_a_player_out(harness: ServiceHarness) -> None:
    h = harness
    gate, _forest = h.scratch_path()
    actor = await player(h, gate, last_action_at=NOW + timedelta(minutes=5))
    outcome = await h.service.execute(actor.id, LookAction())
    assert texts(outcome.events) != [TOO_FAST]


async def test_second_action_in_flight_is_rejected_without_waiting(
    harness: ServiceHarness,
) -> None:
    """While another action of the same player runs (claim held), a request fails fast
    instead of queueing for row locks and holding a connection."""
    h = harness
    gate, _forest = h.scratch_path()
    actor = await player(h, gate)

    async with h.committed.session() as other, other.begin():
        await other.execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended(f"action:{actor.id}", 0)))
        )
        outcome = await asyncio.wait_for(h.service.execute(actor.id, LookAction()), timeout=10)

    assert texts(outcome.events) == [BUSY]
    assert await logs(h, actor.id) == []


async def test_too_fast_is_rejected_before_any_row_lock(harness: ServiceHarness) -> None:
    """The pre-read rejects spam without touching locks: even while the player's row is
    locked by someone else, the answer comes immediately."""
    h = harness
    gate, _forest = h.scratch_path()
    actor = await player(h, gate, last_action_at=NOW - timedelta(milliseconds=100))

    async with h.committed.session() as other, other.begin():
        await other.execute(
            select(PlayerRow).where(col(PlayerRow.id) == actor.id).with_for_update()
        )
        outcome = await asyncio.wait_for(h.service.execute(actor.id, LookAction()), timeout=10)

    assert texts(outcome.events) == [TOO_FAST]


async def test_offline_is_rejected_before_any_row_lock(harness: ServiceHarness) -> None:
    h = harness
    gate, _forest = h.scratch_path()
    actor = await player(h, gate, online=False)

    async with h.committed.session() as other, other.begin():
        await other.execute(
            select(PlayerRow).where(col(PlayerRow.id) == actor.id).with_for_update()
        )
        with pytest.raises(NotPlayingError):
            await asyncio.wait_for(h.service.execute(actor.id, LookAction()), timeout=10)


async def test_relocations_follow_room_changes_only(harness: ServiceHarness) -> None:
    h = harness
    gate, forest = h.scratch_path()
    actor = await player(h, gate)

    await h.service.execute(actor.id, LookAction())
    assert h.publisher.relocations == []

    h.clock.advance(seconds=1)
    await h.service.execute(actor.id, MoveAction(direction="north"))  # no exit: rejected
    assert h.publisher.relocations == []

    h.clock.advance(seconds=1)
    await h.service.execute(actor.id, MoveAction(direction="south"))
    assert [(r.player_id, r.room_id) for r in h.publisher.relocations] == [(actor.id, forest)]


async def test_respawn_relocates_to_the_checkpoint(harness: ServiceHarness) -> None:
    h = harness
    gate, _forest = h.scratch_path()
    checkpoint = h.scratch_room("town_square")
    actor = await player(h, gate, state="dead", hp=0, checkpoint_room_id=checkpoint)

    await h.service.execute(actor.id, RespawnAction())

    assert [(r.player_id, r.room_id) for r in h.publisher.relocations] == [(actor.id, checkpoint)]
