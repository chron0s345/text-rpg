"""Database access for the service layer: locking snapshot loads and applying engine results.

Lock order (ARCHITECTURE.md, "Locking") — every code path takes row locks in this order to avoid
deadlocks: Group -> Trade -> Player (sorted by id) -> InventoryItem -> BankItem ->
MonsterInstance -> FloorItem. Within a table rows are locked sorted by primary key.

Which rows to lock is decided by an unlocked pre-read. If the locked state no longer matches that
pre-read in a way that matters for the lock order (a player joined a group that is not locked),
``SnapshotChangedError`` is raised: the caller rolls back and retries the whole transaction.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.db.mappers import (
    floor_item_snapshot,
    group_snapshot,
    monster_snapshot,
    participation_snapshot,
    player_snapshot,
)
from app.db.models import (
    CombatParticipation,
    FloorItem,
    Group,
    InventoryItem,
    ItemSpawnState,
    LogEntry,
    MonsterInstance,
    Player,
    Trade,
    TradeOffer,
)
from app.engine.events import StateChanges
from app.engine.state import ActionSnapshot, PlayerSnapshot

ACTIVE_TRADE_STATUSES = ("requested", "open")


class SnapshotChangedError(Exception):
    """Locked rows differ from the unlocked pre-read; roll back and retry the transaction."""


class UnknownPlayerError(Exception):
    pass


class InconsistentChangesError(Exception):
    """An engine result does not fit the locked state (programming error, never user input)."""


class Repository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- locking primitives (always in lock order, sorted by primary key) ---------------------

    async def lock_groups(self, group_ids: Iterable[UUID]) -> list[Group]:
        ids = sorted(set(group_ids))
        if not ids:
            return []
        stmt = select(Group).where(col(Group.id).in_(ids)).order_by(col(Group.id)).with_for_update()
        return list((await self.session.scalars(stmt)).all())

    async def lock_players(self, player_ids: Iterable[UUID]) -> list[Player]:
        ids = sorted(set(player_ids))
        if not ids:
            return []
        stmt = (
            select(Player).where(col(Player.id).in_(ids)).order_by(col(Player.id)).with_for_update()
        )
        return list((await self.session.scalars(stmt)).all())

    async def lock_inventories(self, player_ids: Iterable[UUID]) -> list[InventoryItem]:
        ids = sorted(set(player_ids))
        if not ids:
            return []
        stmt = (
            select(InventoryItem)
            .where(col(InventoryItem.player_id).in_(ids))
            .order_by(col(InventoryItem.player_id), col(InventoryItem.id))
            .with_for_update()
        )
        return list((await self.session.scalars(stmt)).all())

    async def lock_monsters_in_room(self, room_id: str) -> list[MonsterInstance]:
        stmt = (
            select(MonsterInstance)
            .where(col(MonsterInstance.room_id) == room_id)
            .order_by(col(MonsterInstance.id))
            .with_for_update()
        )
        return list((await self.session.scalars(stmt)).all())

    async def lock_participations(self, monster_ids: Sequence[UUID]) -> list[CombatParticipation]:
        if not monster_ids:
            return []
        stmt = (
            select(CombatParticipation)
            .where(col(CombatParticipation.monster_instance_id).in_(monster_ids))
            .order_by(
                col(CombatParticipation.monster_instance_id), col(CombatParticipation.player_id)
            )
            .with_for_update()
        )
        return list((await self.session.scalars(stmt)).all())

    async def lock_floor_items_in_room(self, room_id: str) -> list[FloorItem]:
        stmt = (
            select(FloorItem)
            .where(col(FloorItem.room_id) == room_id)
            .order_by(col(FloorItem.id))
            .with_for_update()
        )
        return list((await self.session.scalars(stmt)).all())

    # --- snapshot ----------------------------------------------------------------------------

    async def load_action_snapshot(
        self, actor_id: UUID, room_id: str | None = None
    ) -> ActionSnapshot:
        """Locks and loads everything an action in ``room_id`` may touch.

        ``room_id`` defaults to the actor's current room; ``move`` passes the destination and
        ``respawn`` the checkpoint (the snapshot describes the room where the action ends).
        """
        actor_row = await self.session.get(Player, actor_id)
        if actor_row is None or actor_row.deleted_at is not None:
            raise UnknownPlayerError(actor_id)
        room = room_id or actor_row.current_room_id

        # unlocked pre-read: who is in the room, which groups are involved
        pre = (
            await self.session.execute(
                select(col(Player.id), col(Player.group_id)).where(
                    col(Player.online),
                    col(Player.current_room_id) == room,
                    col(Player.deleted_at).is_(None),
                    col(Player.id) != actor_id,
                )
            )
        ).all()
        group_ids = {g for _, g in pre if g is not None}
        if actor_row.group_id is not None:
            group_ids.add(actor_row.group_id)

        groups = await self.lock_groups(group_ids)
        players = await self.lock_players([actor_id, *(pid for pid, _ in pre)])

        actor = next((p for p in players if p.id == actor_id), None)
        if actor is None or actor.deleted_at is not None:
            raise UnknownPlayerError(actor_id)
        in_room = [
            p
            for p in players
            if p.id != actor_id and p.online and p.deleted_at is None and p.current_room_id == room
        ]
        locked_group_ids = {g.id for g in groups}
        for player in [actor, *in_room]:
            if player.group_id is not None and player.group_id not in locked_group_ids:
                raise SnapshotChangedError(f"group {player.group_id} of {player.id} is not locked")

        player_ids = [actor.id, *(p.id for p in in_room)]
        inventory = await self.lock_inventories(player_ids)
        monsters = await self.lock_monsters_in_room(room)
        participations = await self.lock_participations([m.id for m in monsters])
        floor_items = await self.lock_floor_items_in_room(room)

        locked_items = await self._items_in_active_trades(player_ids)
        by_player: dict[UUID, list[InventoryItem]] = {pid: [] for pid in player_ids}
        for item in inventory:
            by_player[item.player_id].append(item)

        def snap(row: Player) -> PlayerSnapshot:
            return player_snapshot(
                row, by_player[row.id], [i.id for i in by_player[row.id] if i.id in locked_items]
            )

        return ActionSnapshot(
            actor=snap(actor),
            room_players=[snap(p) for p in in_room],
            monsters=[monster_snapshot(m) for m in monsters],
            participations=[participation_snapshot(p) for p in participations],
            groups=[group_snapshot(g, await self._member_ids(g.id)) for g in groups],
            floor_items=[floor_item_snapshot(f) for f in floor_items],
        )

    async def _member_ids(self, group_id: UUID) -> list[UUID]:
        """Members in join order (oldest first); members elsewhere are listed but not locked."""
        stmt = (
            select(col(Player.id))
            .where(col(Player.group_id) == group_id, col(Player.deleted_at).is_(None))
            .order_by(col(Player.group_joined_at), col(Player.id))
        )
        return list((await self.session.scalars(stmt)).all())

    async def _items_in_active_trades(self, player_ids: Sequence[UUID]) -> set[UUID]:
        """Read after the players are locked: offers are added under the same player lock."""
        stmt = (
            select(col(TradeOffer.inventory_item_id))
            .join(Trade, col(Trade.id) == col(TradeOffer.trade_id))
            .where(
                col(TradeOffer.player_id).in_(player_ids),
                col(Trade.status).in_(ACTIVE_TRADE_STATUSES),
            )
        )
        return set((await self.session.scalars(stmt)).all())

    # --- applying engine results -------------------------------------------------------------

    async def apply_changes(self, changes: StateChanges) -> None:
        """Writes an engine result. All touched rows must have been locked by the snapshot load;
        rows are looked up in the session (identity map), so no extra queries hit the database
        for locked rows."""
        session = self.session

        for player_update in changes.players:
            player = await session.get(Player, player_update.player_id)
            if player is None:
                raise InconsistentChangesError(f"unknown player {player_update.player_id}")
            for key, value in player_update.model_dump(
                exclude_unset=True, exclude={"player_id"}
            ).items():
                setattr(player, key, value)

        for monster_update in changes.monsters:
            monster = await session.get(MonsterInstance, monster_update.monster_instance_id)
            if monster is None:
                raise InconsistentChangesError(
                    f"unknown monster {monster_update.monster_instance_id}"
                )
            for key, value in monster_update.model_dump(
                exclude_unset=True, exclude={"monster_instance_id"}
            ).items():
                setattr(monster, key, value)

        for monster_id in changes.participations_clear_monster_ids:
            rows = await session.scalars(
                select(CombatParticipation).where(
                    col(CombatParticipation.monster_instance_id) == monster_id
                )
            )
            for row in rows.all():
                await session.delete(row)
        for delete in changes.participations_delete:
            participation = await session.get(
                CombatParticipation, (delete.monster_instance_id, delete.player_id)
            )
            if participation is not None:
                await session.delete(participation)
        for upsert in changes.participations_upsert:
            participation = await session.get(
                CombatParticipation, (upsert.monster_instance_id, upsert.player_id)
            )
            if participation is None:
                session.add(CombatParticipation(**upsert.model_dump()))
            else:
                participation.party_key = upsert.party_key
                participation.damage_dealt = upsert.damage_dealt

        for inventory_upsert in changes.inventory_upsert:
            state = inventory_upsert.item
            item = await session.get(InventoryItem, state.id)
            if item is None:
                session.add(
                    InventoryItem(
                        id=state.id,
                        player_id=inventory_upsert.player_id,
                        item_id=state.item_id,
                        quantity=state.quantity,
                        equipped_slot=state.equipped_slot,
                    )
                )
            elif item.player_id != inventory_upsert.player_id or item.item_id != state.item_id:
                raise InconsistentChangesError(f"inventory entry {state.id} changed owner or item")
            else:
                item.quantity = state.quantity
                item.equipped_slot = state.equipped_slot
        for item_id in changes.inventory_delete:
            item = await session.get(InventoryItem, item_id)
            if item is not None:
                await session.delete(item)

        for create in changes.floor_items_create:
            session.add(FloorItem(**create.model_dump()))
        for floor_update in changes.floor_items_update:
            floor = await session.get(FloorItem, floor_update.id)
            if floor is None:
                raise InconsistentChangesError(f"unknown floor item {floor_update.id}")
            floor.quantity = floor_update.quantity
        for floor_id in changes.floor_items_delete:
            floor = await session.get(FloorItem, floor_id)
            if floor is not None:
                await session.delete(floor)

        for spawn in changes.item_spawns:
            state_row = await session.get(ItemSpawnState, (spawn.room_id, spawn.item_id))
            if state_row is None:
                session.add(ItemSpawnState(**spawn.model_dump()))
            else:
                state_row.next_spawn_at = spawn.next_spawn_at

        for group_update in changes.groups:
            group = await session.get(Group, group_update.group_id)
            if group is None:
                raise InconsistentChangesError(f"unknown group {group_update.group_id}")
            group.loot_cursor = group_update.loot_cursor

        await session.flush()

    async def add_log_entry(
        self, player_id: UUID, kind: str, text: str, created_at: datetime
    ) -> LogEntry:
        entry = LogEntry(player_id=player_id, kind=kind, text=text, created_at=created_at)
        self.session.add(entry)
        return entry
