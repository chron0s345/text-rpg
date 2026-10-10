"""Read-only view of the world for the game page (ARCHITECTURE.md, "Frontend").

Unlocked reads, no log entries, no rate limit: this only *shows* state, it never decides
anything. Derived values (effective stats, regenerated monster HP, inventory capacity) come from
the same engine functions the actions use, so the page and the rules cannot disagree.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.content.registry import ContentRegistry
from app.content.schemas import Direction, Heal
from app.db.mappers import player_snapshot
from app.db.models import FloorItem, InventoryItem, LogEntry, MonsterInstance, Player
from app.engine.inventory import calculate_inventory_capacity, get_occupied_slots
from app.engine.movement import DIRECTION_NAMES
from app.engine.regeneration import calculate_current_monster_hp
from app.engine.state import InventoryItemState
from app.engine.stats import stats_of
from app.services.action_service import utc_now

LOG_LINES = 50


@dataclass(frozen=True)
class PlayerView:
    id: UUID
    name: str
    level: int
    xp: int
    xp_next: int | None  # XP needed for the next level; None at max level
    hp: int
    max_hp: int
    attack: int
    defense: int
    gold: int
    state: str
    combat_target_id: UUID | None


@dataclass(frozen=True)
class ExitView:
    direction: Direction
    label: str
    room_name: str


@dataclass(frozen=True)
class MonsterView:
    id: UUID
    name: str
    hp: int
    max_hp: int
    fighting_me: bool


@dataclass(frozen=True)
class FloorItemView:
    id: UUID
    name: str
    icon: str | None
    quantity: int


@dataclass(frozen=True)
class RoomView:
    id: str
    name: str
    description: str
    safe: bool
    bank: bool
    exits: list[ExitView]
    monsters: list[MonsterView]
    players: list[tuple[str, str]]  # (name, state)
    merchants: list[str]
    floor_items: list[FloorItemView]


@dataclass(frozen=True)
class ItemView:
    id: UUID
    name: str
    description: str
    icon: str | None
    quantity: int
    equipped_slot: str | None
    equippable: bool
    usable: bool


@dataclass(frozen=True)
class InventoryView:
    used: int
    capacity: int
    bags: int
    bag_slots: int
    carried: list[ItemView]
    equipped: list[ItemView]


@dataclass(frozen=True)
class LogLine:
    id: int
    kind: str
    text: str


@dataclass(frozen=True)
class GameView:
    player: PlayerView
    room: RoomView
    inventory: InventoryView


class ViewService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        content: ContentRegistry,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.sessions = sessions
        self.content = content
        self.clock = clock

    async def game(self, player_id: UUID) -> GameView | None:
        """``None`` if the character is not in the world (offline or deleted)."""
        now = self.clock()
        async with self.sessions() as session:
            row = await session.get(Player, player_id)
            if row is None or not row.online or row.deleted_at is not None:
                return None
            inventory = list(
                (
                    await session.scalars(
                        select(InventoryItem)
                        .where(col(InventoryItem.player_id) == player_id)
                        .order_by(col(InventoryItem.item_id), col(InventoryItem.id))
                    )
                ).all()
            )
            monsters = (
                await session.scalars(
                    select(MonsterInstance)
                    .where(
                        col(MonsterInstance.room_id) == row.current_room_id,
                        col(MonsterInstance.alive),
                    )
                    .order_by(col(MonsterInstance.monster_id), col(MonsterInstance.id))
                )
            ).all()
            others = (
                await session.execute(
                    select(col(Player.name), col(Player.state))
                    .where(
                        col(Player.current_room_id) == row.current_room_id,
                        col(Player.online),
                        col(Player.deleted_at).is_(None),
                        col(Player.id) != player_id,
                    )
                    .order_by(col(Player.name))
                )
            ).all()
            floor = (
                await session.scalars(
                    select(FloorItem)
                    .where(col(FloorItem.room_id) == row.current_room_id)
                    .order_by(col(FloorItem.item_id), col(FloorItem.id))
                )
            ).all()

        snapshot = player_snapshot(row, inventory)
        stats = stats_of(snapshot, self.content.items)
        rules = self.content.rules
        next_rule = next((r for r in rules.levels if r.level == row.level + 1), None)
        player = PlayerView(
            id=row.id,
            name=row.name,
            level=row.level,
            xp=row.xp,
            xp_next=next_rule.xp_required if next_rule else None,
            hp=row.hp,
            max_hp=stats.max_hp,
            attack=stats.attack,
            defense=stats.defense,
            gold=row.gold,
            state=row.state,
            combat_target_id=row.combat_target_id,
        )

        room = self.content.get_room(row.current_room_id)
        monster_views = []
        for monster in monsters:
            template = self.content.get_monster(monster.monster_id)
            hp = calculate_current_monster_hp(template, monster.hp, monster.last_combat_at, now)
            monster_views.append(
                MonsterView(
                    id=monster.id,
                    name=template.name,
                    hp=hp,
                    max_hp=template.max_hp,
                    fighting_me=monster.id == row.combat_target_id,
                )
            )
        floor_views = []
        for item_row in floor:
            if item_row.expires_at is not None and item_row.expires_at <= now:
                continue  # expired: hidden even before the ticker removes it
            item = self.content.get_item(item_row.item_id)
            floor_views.append(FloorItemView(item_row.id, item.name, item.icon, item_row.quantity))
        room_view = RoomView(
            id=room.id,
            name=room.name,
            description=room.description,
            safe=room.safe,
            bank=room.bank,
            exits=[
                ExitView(direction, DIRECTION_NAMES[direction], self.content.get_room(target).name)
                for direction, target in room.exits.items()
            ],
            monsters=monster_views,
            players=[(name, state) for name, state in others],
            merchants=[m.name for m in self.content.merchants_in_room(room.id)],
            floor_items=floor_views,
        )

        items = [self._item(entry) for entry in snapshot.inventory]
        inventory_view = InventoryView(
            used=get_occupied_slots(snapshot.inventory),
            capacity=calculate_inventory_capacity(
                rules.inventory.slots, snapshot.inventory, self.content.items
            ),
            bags=sum(1 for i in snapshot.inventory if i.equipped_slot == "bag"),
            bag_slots=rules.inventory.bag_slots,
            carried=[i for i in items if i.equipped_slot is None],
            equipped=[i for i in items if i.equipped_slot is not None],
        )
        return GameView(player=player, room=room_view, inventory=inventory_view)

    def _item(self, entry: InventoryItemState) -> ItemView:
        item = self.content.get_item(entry.item_id)
        return ItemView(
            id=entry.id,
            name=item.name,
            description=item.description,
            icon=item.icon,
            quantity=entry.quantity,
            equipped_slot=entry.equipped_slot,
            equippable=item.slot is not None,
            usable=any(isinstance(e, Heal) for e in item.effects),
        )

    async def log(self, player_id: UUID, limit: int = LOG_LINES) -> list[LogLine]:
        """The latest log lines, oldest first."""
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    select(col(LogEntry.id), col(LogEntry.kind), col(LogEntry.text))
                    .where(col(LogEntry.player_id) == player_id)
                    .order_by(col(LogEntry.id).desc())
                    .limit(limit)
                )
            ).all()
        return [
            LogLine(entry_id, kind, text)
            for entry_id, kind, text in reversed(rows)
            if entry_id is not None  # always set for stored rows
        ]
