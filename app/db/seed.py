"""Seed command: creates world state from content (ARCHITECTURE.md, "Zustandsmodell").

Creates missing ``MonsterInstance`` rows per spawn rule and the spawn ``FloorItem`` +
``ItemSpawnState`` per room item rule. Existing rows are never touched, so the command can run
after every content change (``python -m app.db.seed``).
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.content.loader import load_content
from app.content.registry import ContentRegistry
from app.db.models import FloorItem, ItemSpawnState, MonsterInstance
from app.db.orphans import find_orphans, prune
from app.db.session import create_engine, create_session_factory


@dataclass
class SeedPlan:
    monsters: list[MonsterInstance] = field(default_factory=list)
    floor_items: list[FloorItem] = field(default_factory=list)
    spawn_states: list[ItemSpawnState] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{len(self.monsters)} Monster, {len(self.floor_items)} Spawn-Items, "
            f"{len(self.spawn_states)} Spawn-Zustände angelegt"
        )


def plan_seed(
    content: ContentRegistry,
    monster_counts: Mapping[tuple[str, str], int],
    seeded_spawns: set[tuple[str, str]],
) -> SeedPlan:
    """Pure: what is missing, given existing monster counts per (room, monster) and the
    (room, item) pairs that already have an ``ItemSpawnState``."""
    plan = SeedPlan()
    # a rare variant stands in for its base: it fills the base's spawn rule
    counts: dict[tuple[str, str], int] = {}
    for (room_id, monster_id), count in monster_counts.items():
        key = (room_id, content.base_monster_id(monster_id))
        counts[key] = counts.get(key, 0) + count
    for room in content.rooms.values():
        wanted: dict[str, int] = {}
        for spawn in room.spawns:
            wanted[spawn.monster_id] = wanted.get(spawn.monster_id, 0) + spawn.count
        for monster_id, count in wanted.items():
            template = content.get_monster(monster_id)
            missing = count - counts.get((room.id, monster_id), 0)
            plan.monsters.extend(
                MonsterInstance(monster_id=monster_id, room_id=room.id, hp=template.max_hp)
                for _ in range(max(0, missing))
            )
        for rule in room.items:
            if (room.id, rule.item_id) in seeded_spawns:
                continue
            plan.floor_items.append(
                FloorItem(
                    room_id=room.id, item_id=rule.item_id, quantity=rule.quantity, source="spawn"
                )
            )
            plan.spawn_states.append(ItemSpawnState(room_id=room.id, item_id=rule.item_id))
    return plan


SEED_LOCK = "text-rpg:seed"


async def seed(session: AsyncSession, content: ContentRegistry) -> SeedPlan:
    # two seeds at once would both see the same gaps: serialize them for this transaction
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(SEED_LOCK))))
    rows = await session.execute(
        select(
            col(MonsterInstance.room_id), col(MonsterInstance.monster_id), func.count()
        ).group_by(col(MonsterInstance.room_id), col(MonsterInstance.monster_id))
    )
    counts = {(room_id, monster_id): count for room_id, monster_id, count in rows.all()}
    spawn_rows = await session.execute(
        select(col(ItemSpawnState.room_id), col(ItemSpawnState.item_id))
    )
    seeded = {(room_id, item_id) for room_id, item_id in spawn_rows.all()}

    plan = plan_seed(content, counts, seeded)
    session.add_all([*plan.monsters, *plan.floor_items, *plan.spawn_states])
    await session.flush()
    return plan


async def _main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.db.seed",
        description="Legt fehlenden Welt-Zustand aus data/ an und meldet verwaiste Zeilen.",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="verwaiste Welt-Zeilen löschen und Charaktere aus entfernten Räumen in die Stadt "
        "setzen (Inventar und Bank bleiben unangetastet)",
    )
    args = parser.parse_args(argv)
    content = load_content(Path("data"))
    engine = create_engine()
    try:
        async with create_session_factory(engine)() as session, session.begin():
            plan = await seed(session, content)
            orphans = await find_orphans(session, content)
            if orphans and args.prune:
                await prune(session, content, orphans)
        print(plan.summary())
        for line in orphans.report():
            print(("Bereinigt: " if args.prune else "Verwaist: ") + line)
        if orphans and not args.prune:
            # only reported: the seed runs on every container start and must not block it
            print("Datenmigration nachholen oder mit --prune bereinigen (siehe app/db/orphans.py).")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
