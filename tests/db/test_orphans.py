"""Rows referring to removed content: found after a content change, world rows pruned, character
property only reported."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import load_content
from app.db.models import (
    BankItem,
    FloorItem,
    InventoryItem,
    ItemSpawnState,
    MonsterInstance,
)
from app.db.orphans import find_orphans, prune
from tests.conftest import DATA_DIR
from tests.db.factories import NOW, add_player

CONTENT = load_content(DATA_DIR)
TOWN = CONTENT.rules.town_room_id


async def test_orphans_are_found_and_the_world_is_pruned(session: AsyncSession) -> None:
    gone_template = MonsterInstance(monster_id="removed_monster", room_id="dark_forest", hp=1)
    gone_room = MonsterInstance(monster_id="goblin", room_id="removed_room", hp=1)
    fine = MonsterInstance(monster_id="goblin", room_id="dark_forest", hp=1)
    session.add_all([gone_template, gone_room, fine])
    await session.flush()
    fighter = await add_player(
        session, state="in_combat", combat_target_id=gone_template.id, name="Kämpfer"
    )
    lost = await add_player(
        session, current_room_id="removed_room", checkpoint_room_id="removed_room", name="Verirrt"
    )
    home = await add_player(session, name="Daheim")
    stale_spawn = ItemSpawnState(room_id="dark_forest", item_id="bread")  # no such room rule
    kept_spawn = ItemSpawnState(room_id="goblin_camp", item_id="bread")
    gone_floor = FloorItem(room_id="dark_forest", item_id="removed_item", source="spawn")
    kept_floor = FloorItem(room_id="dark_forest", item_id="bread", source="spawn")
    unknown_carried = InventoryItem(player_id=home.id, item_id="removed_item", quantity=1)
    unknown_banked = BankItem(player_id=home.id, item_id="removed_item", quantity=2)
    session.add_all(
        [stale_spawn, kept_spawn, gone_floor, kept_floor, unknown_carried, unknown_banked]
    )
    await session.flush()

    orphans = await find_orphans(session, CONTENT)

    assert {gone_template.id, gone_room.id} <= set(orphans.monster_ids)
    assert fine.id not in orphans.monster_ids
    assert ("dark_forest", "bread") in orphans.spawn_states
    assert ("goblin_camp", "bread") not in orphans.spawn_states
    assert gone_floor.id in orphans.floor_item_ids and kept_floor.id not in orphans.floor_item_ids
    assert lost.id in orphans.players_misplaced and home.id not in orphans.players_misplaced
    assert orphans.unknown_property["removed_item"] >= 2
    assert "bread" not in orphans.unknown_property
    assert any("Monster" in line for line in orphans.report())

    await prune(session, CONTENT, orphans)

    assert await session.get(MonsterInstance, gone_template.id) is None
    assert await session.get(MonsterInstance, gone_room.id) is None
    assert await session.get(MonsterInstance, fine.id) is not None
    assert await session.get(ItemSpawnState, ("dark_forest", "bread")) is None
    assert await session.get(ItemSpawnState, ("goblin_camp", "bread")) is not None
    assert await session.get(FloorItem, gone_floor.id) is None
    assert await session.get(FloorItem, kept_floor.id) is not None
    await session.refresh(fighter)
    assert (fighter.state, fighter.combat_target_id) == ("exploring", None)
    await session.refresh(lost)
    assert (lost.current_room_id, lost.checkpoint_room_id) == (TOWN, TOWN)
    # property is never deleted: it needs a real data migration
    assert await session.get(InventoryItem, unknown_carried.id) is not None
    assert await session.get(BankItem, unknown_banked.id) is not None


async def test_nothing_to_do_on_a_consistent_world(session: AsyncSession) -> None:
    player = await add_player(session, last_action_at=NOW)
    session.add(InventoryItem(player_id=player.id, item_id="bread", quantity=1))
    await session.flush()

    orphans = await find_orphans(session, CONTENT)

    assert player.id not in orphans.players_misplaced
    assert "bread" not in orphans.unknown_property
