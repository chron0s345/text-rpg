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


def wanted_items(content: ContentRegistry) -> list[tuple[str, str, int]]:
    return [
        (room.id, rule.item_id, rule.quantity)
        for room in content.rooms.values()
        for rule in room.items
    ]


# --- pure planning (no database) ---------------------------------------------------------


def test_plan_on_empty_world(content: ContentRegistry) -> None:
    plan = plan_seed(content, {}, set())
    created = Counter((m.room_id, m.monster_id) for m in plan.monsters)
    assert created == wanted_monsters(content)
    assert all(m.hp == content.get_monster(m.monster_id).max_hp and m.alive for m in plan.monsters)
    items = wanted_items(content)
    assert len(items) >= 2
    assert {(f.room_id, f.item_id, f.quantity, f.source) for f in plan.floor_items} == {
        (*item, "spawn") for item in items
    }
    assert [(s.room_id, s.item_id) for s in plan.spawn_states] == [
        (room, item) for room, item, _ in items
    ]


def test_plan_only_fills_gaps(content: ContentRegistry) -> None:
    existing = dict(wanted_monsters(content))
    existing[("goblin_camp", "goblin")] = 1  # one of two goblins missing
    existing[("dark_forest", "wolf")] = 5  # more than wanted: never removed
    plan = plan_seed(content, existing, {(room, item) for room, item, _ in wanted_items(content)})
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


def test_a_rare_variant_fills_its_base_spawn_rule(content: ContentRegistry) -> None:
    from dataclasses import replace

    from app.content.schemas import RareVariant

    goblin = content.get_monster("goblin")
    golden = goblin.model_copy(update={"id": "golden_goblin", "name": "Goldener Goblin"})
    with_variant = replace(
        content,
        monsters={
            **content.monsters,
            "goblin": goblin.model_copy(
                update={"rare_variant": RareVariant(monster_id="golden_goblin", chance=0.1)}
            ),
            "golden_goblin": golden,
        },
    )
    existing = dict(wanted_monsters(with_variant))
    existing[("dark_forest", "goblin")] = 0
    existing[("dark_forest", "golden_goblin")] = 1  # the forest's goblin is golden right now

    seeded = {
        (room.id, rule.item_id) for room in with_variant.rooms.values() for rule in room.items
    }
    plan = plan_seed(with_variant, existing, seeded)

    assert plan.monsters == []
