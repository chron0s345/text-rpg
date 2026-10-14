"""Rows that refer to content which no longer exists (ARCHITECTURE.md, "Migrationen").

Content ids are stable; renaming or removing one needs a data migration. When that was forgotten,
world rows point at nothing: a monster whose template is gone breaks every action and view in its
room. ``find_orphans`` lists them after a content change (``python -m app.db.seed`` reports
them), ``prune`` cleans up what belongs to the world:

- monster instances, item spawn states and floor items with an unknown room or template are
  deleted (they are world state, the seed recreates whatever the content still wants),
- characters in an unknown room or with an unknown checkpoint are moved to ``town_room_id``;
  characters fighting a deleted monster stop fighting.

Character property (inventory, bank) with unknown items is only reported, never deleted: that
needs a decision (replace the item? compensate?), i.e. a real data migration.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.content.registry import ContentRegistry
from app.db.models import (
    BankItem,
    FloorItem,
    InventoryItem,
    ItemSpawnState,
    MonsterInstance,
    Player,
)


@dataclass
class Orphans:
    monster_ids: list[UUID] = field(default_factory=list)
    spawn_states: list[tuple[str, str]] = field(default_factory=list)  # (room, item)
    floor_item_ids: list[UUID] = field(default_factory=list)
    players_misplaced: list[UUID] = field(default_factory=list)  # unknown room or checkpoint
    unknown_property: Counter[str] = field(default_factory=Counter)  # item id -> rows

    def __bool__(self) -> bool:
        return bool(
            self.monster_ids
            or self.spawn_states
            or self.floor_item_ids
            or self.players_misplaced
            or self.unknown_property
        )

    def report(self) -> list[str]:
        lines = []
        if self.monster_ids:
            lines.append(f"{len(self.monster_ids)} Monster mit unbekanntem Raum oder Template")
        if self.spawn_states:
            lines.append(f"{len(self.spawn_states)} Spawn-Zustände ohne passende Raumregel")
        if self.floor_item_ids:
            lines.append(f"{len(self.floor_item_ids)} Boden-Items mit unbekanntem Raum oder Item")
        if self.players_misplaced:
            lines.append(
                f"{len(self.players_misplaced)} Charaktere in unbekanntem Raum oder Checkpoint"
            )
        for item_id, rows in sorted(self.unknown_property.items()):
            lines.append(f"{rows} Inventar-/Bankeinträge mit unbekanntem Item '{item_id}'")
        return lines


async def find_orphans(session: AsyncSession, content: ContentRegistry) -> Orphans:
    rooms, monsters, items = content.rooms, content.monsters, content.items
    orphans = Orphans()

    for monster_id, room_id, template in (
        await session.execute(
            select(
                col(MonsterInstance.id),
                col(MonsterInstance.room_id),
                col(MonsterInstance.monster_id),
            )
        )
    ).all():
        if room_id not in rooms or template not in monsters:
            orphans.monster_ids.append(monster_id)

    for room_id, item_id in (
        await session.execute(select(col(ItemSpawnState.room_id), col(ItemSpawnState.item_id)))
    ).all():
        room = rooms.get(room_id)
        if room is None or all(rule.item_id != item_id for rule in room.items):
            orphans.spawn_states.append((room_id, item_id))

    for floor_id, room_id, item_id in (
        await session.execute(
            select(col(FloorItem.id), col(FloorItem.room_id), col(FloorItem.item_id))
        )
    ).all():
        if room_id not in rooms or item_id not in items:
            orphans.floor_item_ids.append(floor_id)

    for player_id, room_id, checkpoint in (
        await session.execute(
            select(
                col(Player.id), col(Player.current_room_id), col(Player.checkpoint_room_id)
            ).where(col(Player.deleted_at).is_(None))
        )
    ).all():
        if room_id not in rooms or checkpoint not in rooms:
            orphans.players_misplaced.append(player_id)

    for model in (InventoryItem, BankItem):
        for (item_id,) in (await session.execute(select(col(model.item_id)))).all():
            if item_id not in items:
                orphans.unknown_property[item_id] += 1
    return orphans


async def prune(session: AsyncSession, content: ContentRegistry, orphans: Orphans) -> None:
    """Cleans up the world rows of ``orphans`` (see module docstring). Character property is
    left alone."""
    town = content.rules.town_room_id
    if orphans.monster_ids:
        # fighting a monster that is about to vanish: the fight ends (the FK would only null the
        # target and leave the character "in combat" with nobody)
        await session.execute(
            update(Player)
            .where(col(Player.combat_target_id).in_(orphans.monster_ids))
            .values(state="exploring", combat_target_id=None)
        )
        await session.execute(
            delete(MonsterInstance).where(col(MonsterInstance.id).in_(orphans.monster_ids))
        )
    for room_id, item_id in orphans.spawn_states:
        await session.execute(
            delete(ItemSpawnState).where(
                col(ItemSpawnState.room_id) == room_id, col(ItemSpawnState.item_id) == item_id
            )
        )
    if orphans.floor_item_ids:
        await session.execute(
            delete(FloorItem).where(col(FloorItem.id).in_(orphans.floor_item_ids))
        )
    rooms = set(content.rooms)
    for player_id in orphans.players_misplaced:
        player = await session.get(Player, player_id, with_for_update=True)
        if player is None:
            continue
        if player.current_room_id not in rooms:
            player.current_room_id = town
            if player.state == "in_combat":
                player.state, player.combat_target_id = "exploring", None
        if player.checkpoint_room_id not in rooms:
            player.checkpoint_room_id = town
    await session.flush()
