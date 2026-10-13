from __future__ import annotations

from collections import Counter

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import load_content
from app.content.registry import ContentRegistry
from app.db.models import FloorItem, ItemSpawnState, MonsterInstance
from app.db.seed import plan_seed, seed
from tests.conftest import DATA_DIR


@pytest.fixture(scope="module")
def content() -> ContentRegistry:
    return load_content(DATA_DIR)


def wanted_monsters(content: ContentRegistry) -> Counter[tuple[str, str]]:
    wanted: Counter[tuple[str, str]] = Counter()
    for room in content.rooms.values():
        for spawn in room.spawns:
            wanted[(room.id, spawn.monster_id)] += spawn.count
    return wanted


# --- pure planning (no database) ---------------------------------------------------------


def test_plan_on_empty_world(content: ContentRegistry) -> None:
    plan = plan_seed(content, {}, set())
    created = Counter((m.room_id, m.monster_id) for m in plan.monsters)
    assert created == wanted_monsters(content)
    assert all(m.hp == content.get_monster(m.monster_id).max_hp and m.alive for m in plan.monsters)
    assert {(f.room_id, f.item_id, f.quantity, f.source) for f in plan.floor_items} == {
        ("goblin_camp", "bread", 2, "spawn"),
        ("bog_path", "bog_herb", 2, "spawn"),
    }
    assert [(s.room_id, s.item_id) for s in plan.spawn_states] == [
        ("goblin_camp", "bread"),
        ("bog_path", "bog_herb"),
    ]


def test_plan_only_fills_gaps(content: ContentRegistry) -> None:
    existing = dict(wanted_monsters(content))
    existing[("goblin_camp", "goblin")] = 1  # one of two goblins missing
    existing[("dark_forest", "wolf")] = 5  # more than wanted: never removed
    plan = plan_seed(content, existing, {("goblin_camp", "bread"), ("bog_path", "bog_herb")})
    assert [(m.room_id, m.monster_id) for m in plan.monsters] == [("goblin_camp", "goblin")]
    assert plan.floor_items == [] and plan.spawn_states == []


# --- database ---------------------------------------------------------------------------------


async def test_seed_is_idempotent(session: AsyncSession, content: ContentRegistry) -> None:
    first = await seed(session, content)
    second = await seed(session, content)

    assert len(first.monsters) == sum(wanted_monsters(content).values())
    assert second.summary() == "0 Monster, 0 Spawn-Items, 0 Spawn-Zustände angelegt"
    monsters = (await session.scalars(select(MonsterInstance))).all()
    assert Counter((m.room_id, m.monster_id) for m in monsters) == wanted_monsters(content)
    item_rules = sum(len(room.items) for room in content.rooms.values())
    assert len((await session.scalars(select(FloorItem))).all()) == item_rules
    assert len((await session.scalars(select(ItemSpawnState))).all()) == item_rules
