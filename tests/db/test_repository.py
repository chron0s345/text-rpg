from __future__ import annotations

import random
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlmodel import col

from app.content.loader import load_content
from app.content.registry import ContentRegistry
from app.db.models import (
    CombatParticipation,
    FloorItem,
    Group,
    InventoryItem,
    MonsterInstance,
    Player,
    Trade,
    TradeOffer,
)
from app.db.repository import (
    InconsistentChangesError,
    Repository,
    SnapshotChangedError,
    UnknownPlayerError,
)
from app.engine.combat import resolve_attack
from app.engine.events import InventoryUpsert, StateChanges
from app.engine.inventory_actions import resolve_drop
from app.engine.state import InventoryItemState
from tests.conftest import DATA_DIR
from tests.db.factories import NOW, add_player


@pytest.fixture(scope="module")
def content() -> ContentRegistry:
    return load_content(DATA_DIR)


async def add_group(session: AsyncSession, *members: Player) -> Group:
    group = Group(leader_id=members[0].id)
    session.add(group)
    await session.flush()
    for offset, member in enumerate(members):
        member.group_id = group.id
        member.group_joined_at = NOW + timedelta(seconds=offset)
    await session.flush()
    return group


# --- snapshot ---------------------------------------------------------------------------------


async def test_snapshot_contains_exactly_the_room(session: AsyncSession) -> None:
    actor = await add_player(session, name="Anna")
    friend = await add_player(session, name="Ben")
    away = await add_player(session, name="Cleo", current_room_id="goblin_camp")
    await add_player(session, name="Dora", online=False)  # offline: not in the room view
    group = await add_group(session, actor, away)
    goblin = MonsterInstance(monster_id="goblin", room_id="dark_forest", hp=12)
    elsewhere = MonsterInstance(monster_id="goblin", room_id="goblin_camp", hp=12)
    dagger = InventoryItem(player_id=actor.id, item_id="rusty_dagger")
    loot = FloorItem(
        room_id="dark_forest", item_id="bread", source="loot", expires_at=NOW + timedelta(1)
    )
    session.add_all([goblin, elsewhere, dagger, loot])
    await session.flush()
    session.add(
        CombatParticipation(monster_instance_id=goblin.id, player_id=friend.id, party_key="x")
    )
    await session.flush()

    snapshot = await Repository(session).load_action_snapshot(actor.id)

    assert snapshot.actor.name == "Anna"
    assert [i.item_id for i in snapshot.actor.inventory] == ["rusty_dagger"]
    assert [p.name for p in snapshot.room_players] == ["Ben"]
    assert [m.id for m in snapshot.monsters] == [goblin.id]
    assert [(p.player_id, p.party_key) for p in snapshot.participations] == [(friend.id, "x")]
    assert [f.id for f in snapshot.floor_items] == [loot.id]
    # the group is complete (incl. members elsewhere) and ordered by join time
    assert [(g.id, g.member_ids) for g in snapshot.groups] == [(group.id, [actor.id, away.id])]


async def test_snapshot_for_another_room(session: AsyncSession) -> None:
    actor = await add_player(session)
    other = await add_player(session, current_room_id="forest_gate")
    snapshot = await Repository(session).load_action_snapshot(actor.id, "forest_gate")
    assert [p.id for p in snapshot.room_players] == [other.id]


async def test_snapshot_marks_items_offered_in_active_trades(session: AsyncSession) -> None:
    actor = await add_player(session)
    partner = await add_player(session)
    offered = InventoryItem(player_id=actor.id, item_id="bread", quantity=3)
    old_offer = InventoryItem(player_id=actor.id, item_id="goblin_ear")
    session.add_all([offered, old_offer])
    open_trade = Trade(
        initiator_id=actor.id,
        partner_id=partner.id,
        room_id="dark_forest",
        status="open",
        expires_at=NOW,
    )
    done = Trade(
        initiator_id=actor.id,
        partner_id=partner.id,
        room_id="dark_forest",
        status="completed",
        expires_at=NOW,
    )
    session.add_all([open_trade, done])
    await session.flush()
    session.add_all(
        [
            TradeOffer(
                trade_id=open_trade.id, inventory_item_id=offered.id, player_id=actor.id, quantity=1
            ),
            TradeOffer(
                trade_id=done.id, inventory_item_id=old_offer.id, player_id=actor.id, quantity=1
            ),
        ]
    )
    await session.flush()

    snapshot = await Repository(session).load_action_snapshot(actor.id)

    assert snapshot.actor.locked_item_ids == [offered.id]


async def test_unknown_or_deleted_actor(session: AsyncSession) -> None:
    repo = Repository(session)
    with pytest.raises(UnknownPlayerError):
        await repo.load_action_snapshot(uuid4())
    deleted = await add_player(session, online=False, deleted_at=NOW)
    with pytest.raises(UnknownPlayerError):
        await repo.load_action_snapshot(deleted.id)


async def test_unlocked_group_change_requests_retry(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = await add_player(session)
    await add_group(session, actor)
    repo = Repository(session)

    async def no_groups(_: object) -> list[Group]:  # simulates: group joined after the pre-read
        return []

    monkeypatch.setattr(repo, "lock_groups", no_groups)
    with pytest.raises(SnapshotChangedError):
        await repo.load_action_snapshot(actor.id)


# --- applying engine results -----------------------------------------------------------------


async def test_attack_round_trip(session: AsyncSession, content: ContentRegistry) -> None:
    actor = await add_player(session)
    goblin = MonsterInstance(monster_id="goblin", room_id="dark_forest", hp=12)
    session.add(goblin)
    await session.flush()
    repo = Repository(session)

    snapshot = await repo.load_action_snapshot(actor.id)
    result = resolve_attack(snapshot, content, content.rules, NOW, random.Random(3), goblin.id)
    await repo.apply_changes(result.changes)

    again = await repo.load_action_snapshot(actor.id)
    assert again.actor.state == "in_combat"
    assert again.actor.combat_target_id == goblin.id
    assert again.actor.hp == snapshot.actor.hp
    [monster] = again.monsters
    assert monster.hp == snapshot.monsters[0].hp
    assert monster.tagged_by_party == actor_party(actor)
    assert monster.last_combat_at == NOW
    [participation] = again.participations
    assert participation.damage_dealt == 12 - monster.hp


def actor_party(player: Player) -> str:
    return f"solo:{player.id}"


async def test_kill_writes_rewards_and_clears_fight(
    session: AsyncSession, content: ContentRegistry
) -> None:
    actor = await add_player(session, base_attack=50)
    goblin = MonsterInstance(monster_id="goblin", room_id="dark_forest", hp=1)
    session.add(goblin)
    await session.flush()
    repo = Repository(session)

    result = resolve_attack(
        await repo.load_action_snapshot(actor.id),
        content,
        content.rules,
        NOW,
        random.Random(3),
        goblin.id,
    )
    await repo.apply_changes(result.changes)

    await session.refresh(goblin)
    await session.refresh(actor)
    assert (goblin.alive, goblin.hp, goblin.tagged_by_party) == (False, 0, None)
    assert goblin.respawn_at is not None
    assert (actor.state, actor.combat_target_id, actor.xp) == ("exploring", None, 15)
    rows = (await session.scalars(select(CombatParticipation))).all()
    assert rows == []
    loot = (
        await session.scalars(select(InventoryItem).where(col(InventoryItem.player_id) == actor.id))
    ).all()
    assert {i.item_id for i in loot} == {u.item.item_id for u in result.changes.inventory_upsert}


async def test_drop_creates_floor_item(session: AsyncSession, content: ContentRegistry) -> None:
    actor = await add_player(session)
    bread = InventoryItem(player_id=actor.id, item_id="bread", quantity=4)
    session.add(bread)
    await session.flush()
    repo = Repository(session)

    result = resolve_drop(
        await repo.load_action_snapshot(actor.id),
        content,
        content.rules,
        NOW,
        random.Random(1),
        bread.id,
        3,
    )
    await repo.apply_changes(result.changes)

    await session.refresh(bread)
    assert bread.quantity == 1
    [floor] = (await session.scalars(select(FloorItem))).all()
    assert (floor.quantity, floor.source, floor.dropped_by_account_id) == (
        3,
        "drop",
        actor.account_id,
    )


async def test_inventory_upsert_cannot_move_items_between_players(session: AsyncSession) -> None:
    owner = await add_player(session)
    thief = await add_player(session)
    item = InventoryItem(player_id=owner.id, item_id="bread")
    session.add(item)
    await session.flush()

    changes = StateChanges(
        inventory_upsert=[
            InventoryUpsert(
                player_id=thief.id,
                item=InventoryItemState(id=item.id, item_id="bread", quantity=1),
            )
        ]
    )
    with pytest.raises(InconsistentChangesError):
        await Repository(session).apply_changes(changes)


# --- locking ----------------------------------------------------------------------------------


async def test_snapshot_rows_are_locked_until_commit(engine: AsyncEngine) -> None:
    """A second transaction cannot lock the same player/monster rows while the first holds them."""
    async with engine.connect() as setup:
        async with setup.begin():
            account_id = uuid4()
            await setup.execute(
                text(
                    "INSERT INTO account (id, username, password_hash, created_at) "
                    "VALUES (:id, :name, 'x', now())"
                ),
                {"id": account_id, "name": f"lock-{account_id.hex[:8]}"},
            )
    async with AsyncSession(engine, expire_on_commit=False) as first:
        actor = await add_player_with_account(first, account_id)
        await first.commit()
        try:
            async with first.begin():
                await Repository(first).load_action_snapshot(actor.id)

                async with AsyncSession(engine) as second, second.begin():
                    await second.execute(text("SET LOCAL lock_timeout = '200ms'"))
                    with pytest.raises(DBAPIError, match="lock"):
                        await Repository(second).load_action_snapshot(actor.id)
        finally:
            async with AsyncSession(engine) as cleanup, cleanup.begin():
                await cleanup.execute(
                    text("DELETE FROM account WHERE id = :id"), {"id": account_id}
                )


async def add_player_with_account(session: AsyncSession, account_id: object) -> Player:
    player = Player(
        account_id=account_id,  # type: ignore[arg-type]
        name=f"L{uuid4().hex[:8]}",
        hp=20,
        base_max_hp=20,
        base_attack=5,
        base_defense=2,
        current_room_id="lock_room",
        checkpoint_room_id="town_square",
        online=True,
    )
    session.add(player)
    await session.flush()
    return player
