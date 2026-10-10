"""Database access for the service layer: locking snapshot loads and applying engine results.

Lock order (ARCHITECTURE.md, "Locking") — every code path takes row locks in this order to avoid
deadlocks: Group -> Trade -> Player (sorted by id) -> InventoryItem -> BankItem ->
MonsterInstance -> FloorItem. Within a table rows are locked sorted by primary key.

Two rules make the locks meaningful:

- Every locking query uses ``populate_existing``: rows already in the session (identity map) are
  refreshed with the values read under the lock. Without it, SQLAlchemy would keep attributes
  loaded earlier and a concurrent commit could be silently overwritten (lost update).
- ``apply_changes`` only writes rows that this repository locked (``Locks``). Anything else is
  rejected with ``InconsistentChangesError`` instead of being read and written without a lock.

Which rows to lock is decided by an unlocked, column-only pre-read. If the locked state no longer
matches that pre-read in a way that matters (the actor moved, a player joined a group that is not
locked), ``SnapshotChangedError`` is raised: the caller rolls back and retries the transaction.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, select
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
    """An engine result touches rows outside the locked snapshot (programming error)."""


class StaleRepositoryError(Exception):
    """A Repository was used in a different transaction than the one its locks belong to.

    Row locks end with commit/rollback. Create a new Repository per transaction (e.g. per retry
    after ``SnapshotChangedError``); a stale lock set must never authorize writes.
    """


@dataclass
class Locks:
    """Rows locked in the current transaction; ``apply_changes`` may only write these."""

    groups: set[UUID] = field(default_factory=set)
    players: set[UUID] = field(default_factory=set)
    inventory: dict[UUID, UUID] = field(default_factory=dict)  # item id -> owner id
    monsters: set[UUID] = field(default_factory=set)
    participations: set[tuple[UUID, UUID]] = field(default_factory=set)
    floor_items: set[UUID] = field(default_factory=set)
    rooms: set[str] = field(default_factory=set)  # rooms whose monsters/floor items are locked


def _locked(stmt: Select[Any]) -> Select[Any]:
    return stmt.with_for_update().execution_options(populate_existing=True)


class Repository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.locks = Locks()
        self._transaction: object | None = None  # the transaction the locks belong to

    def _bind_transaction(self) -> None:
        """Ties the lock set to the current transaction; any other transaction is an error."""
        current = self.session.get_transaction()
        if current is None:
            raise StaleRepositoryError("no active transaction")
        if self._transaction is None:
            self._transaction = current
        elif current is not self._transaction:
            raise StaleRepositoryError("repository reused across transactions")

    async def _lock_rows(self, stmt: Select[Any]) -> list[Any]:
        rows = list((await self.session.scalars(_locked(stmt))).all())
        self._bind_transaction()  # after executing: the statement may have started the transaction
        return rows

    # --- locking primitives (always in lock order, sorted by primary key) ---------------------

    async def lock_groups(self, group_ids: Iterable[UUID]) -> list[Group]:
        ids = sorted(set(group_ids))
        if not ids:
            return []
        stmt = select(Group).where(col(Group.id).in_(ids)).order_by(col(Group.id))
        rows = await self._lock_rows(stmt)
        self.locks.groups.update(r.id for r in rows)
        return rows

    async def lock_players(self, player_ids: Iterable[UUID]) -> list[Player]:
        ids = sorted(set(player_ids))
        if not ids:
            return []
        stmt = select(Player).where(col(Player.id).in_(ids)).order_by(col(Player.id))
        rows = await self._lock_rows(stmt)
        self.locks.players.update(r.id for r in rows)
        return rows

    async def lock_inventories(self, player_ids: Iterable[UUID]) -> list[InventoryItem]:
        ids = sorted(set(player_ids))
        if not ids:
            return []
        stmt = (
            select(InventoryItem)
            .where(col(InventoryItem.player_id).in_(ids))
            .order_by(col(InventoryItem.player_id), col(InventoryItem.id))
        )
        rows = await self._lock_rows(stmt)
        self.locks.inventory.update({r.id: r.player_id for r in rows})
        return rows

    async def lock_monsters_in_room(self, room_id: str) -> list[MonsterInstance]:
        stmt = (
            select(MonsterInstance)
            .where(col(MonsterInstance.room_id) == room_id)
            .order_by(col(MonsterInstance.id))
        )
        rows = await self._lock_rows(stmt)
        self.locks.monsters.update(r.id for r in rows)
        return rows

    async def lock_participations(self, monster_ids: Sequence[UUID]) -> list[CombatParticipation]:
        if not monster_ids:
            return []
        stmt = (
            select(CombatParticipation)
            .where(col(CombatParticipation.monster_instance_id).in_(monster_ids))
            .order_by(
                col(CombatParticipation.monster_instance_id), col(CombatParticipation.player_id)
            )
        )
        rows = await self._lock_rows(stmt)
        self.locks.participations.update((r.monster_instance_id, r.player_id) for r in rows)
        return rows

    async def lock_floor_items_in_room(self, room_id: str) -> list[FloorItem]:
        stmt = (
            select(FloorItem).where(col(FloorItem.room_id) == room_id).order_by(col(FloorItem.id))
        )
        rows = await self._lock_rows(stmt)
        self.locks.floor_items.update(r.id for r in rows)
        return rows

    # --- snapshot ----------------------------------------------------------------------------

    async def load_action_snapshot(
        self, actor_id: UUID, room_id: str | None = None
    ) -> ActionSnapshot:
        """Locks and loads everything an action in ``room_id`` may touch.

        ``room_id`` defaults to the actor's current room; ``move`` passes the destination and
        ``respawn`` the checkpoint (the snapshot describes the room where the action ends).
        All player data in the result was read under the lock.
        """
        # unlocked, column-only pre-read (never puts rows into the identity map)
        pre_actor = (
            await self.session.execute(
                select(col(Player.current_room_id), col(Player.group_id)).where(
                    col(Player.id) == actor_id, col(Player.deleted_at).is_(None)
                )
            )
        ).first()
        if pre_actor is None:
            raise UnknownPlayerError(actor_id)
        room = room_id or pre_actor.current_room_id
        others = (
            await self.session.execute(
                select(col(Player.id), col(Player.group_id)).where(
                    col(Player.online),
                    col(Player.current_room_id) == room,
                    col(Player.deleted_at).is_(None),
                    col(Player.id) != actor_id,
                )
            )
        ).all()
        group_ids = {g for _, g in others if g is not None}
        if pre_actor.group_id is not None:
            group_ids.add(pre_actor.group_id)

        groups = await self.lock_groups(group_ids)
        players = await self.lock_players([actor_id, *(pid for pid, _ in others)])

        actor = next((p for p in players if p.id == actor_id), None)
        if actor is None or actor.deleted_at is not None:
            raise UnknownPlayerError(actor_id)
        if room_id is None and actor.current_room_id != room:
            raise SnapshotChangedError(f"{actor_id} moved during the snapshot load")
        in_room = [
            p
            for p in players
            if p.id != actor_id and p.online and p.deleted_at is None and p.current_room_id == room
        ]
        for player in [actor, *in_room]:
            if player.group_id is not None and player.group_id not in self.locks.groups:
                raise SnapshotChangedError(f"group {player.group_id} of {player.id} is not locked")

        player_ids = [actor.id, *(p.id for p in in_room)]
        inventory = await self.lock_inventories(player_ids)
        monsters = await self.lock_monsters_in_room(room)
        participations = await self.lock_participations([m.id for m in monsters])
        floor_items = await self.lock_floor_items_in_room(room)
        self.locks.rooms.add(room)

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

    def _require(self, condition: bool, what: str) -> None:
        if not condition:
            raise InconsistentChangesError(f"{what} is not locked by this transaction")

    async def apply_changes(self, changes: StateChanges) -> None:
        """Writes an engine result. Only rows locked by this repository in the current
        transaction may be written; locked rows are in the identity map, so lookups do not hit
        the database."""
        if self._transaction is None:
            raise InconsistentChangesError("nothing is locked by this repository")
        self._bind_transaction()
        session, locks = self.session, self.locks

        for player_update in changes.players:
            self._require(player_update.player_id in locks.players, "player")
            player = await session.get(Player, player_update.player_id)
            assert player is not None
            for key, value in player_update.model_dump(
                exclude_unset=True, exclude={"player_id"}
            ).items():
                setattr(player, key, value)

        for monster_update in changes.monsters:
            self._require(monster_update.monster_instance_id in locks.monsters, "monster")
            monster = await session.get(MonsterInstance, monster_update.monster_instance_id)
            assert monster is not None
            for key, value in monster_update.model_dump(
                exclude_unset=True, exclude={"monster_instance_id"}
            ).items():
                setattr(monster, key, value)

        for monster_id in changes.participations_clear_monster_ids:
            self._require(monster_id in locks.monsters, "monster")
            for pk in [k for k in locks.participations if k[0] == monster_id]:
                participation = await session.get(CombatParticipation, pk)
                if participation is not None:
                    await session.delete(participation)
                locks.participations.discard(pk)
        for delete in changes.participations_delete:
            pk = (delete.monster_instance_id, delete.player_id)
            self._require(pk in locks.participations, "participation")
            participation = await session.get(CombatParticipation, pk)
            if participation is not None:
                await session.delete(participation)
        for upsert in changes.participations_upsert:
            pk = (upsert.monster_instance_id, upsert.player_id)
            self._require(
                upsert.monster_instance_id in locks.monsters and upsert.player_id in locks.players,
                "participation",
            )
            participation = await session.get(CombatParticipation, pk)
            if participation is None:
                session.add(CombatParticipation(**upsert.model_dump()))
                locks.participations.add(pk)
            else:
                participation.party_key = upsert.party_key
                participation.damage_dealt = upsert.damage_dealt

        for inventory_upsert in changes.inventory_upsert:
            state, owner = inventory_upsert.item, inventory_upsert.player_id
            self._require(owner in locks.players, "inventory owner")
            if state.id in locks.inventory:
                if locks.inventory[state.id] != owner:
                    raise InconsistentChangesError(f"inventory entry {state.id} changed owner")
                item = await session.get(InventoryItem, state.id)
                assert item is not None
                if item.item_id != state.item_id:
                    raise InconsistentChangesError(f"inventory entry {state.id} changed item")
                item.quantity = state.quantity
                item.equipped_slot = state.equipped_slot
            else:
                # new entry; an id of an existing row of someone else fails on the primary key
                session.add(
                    InventoryItem(
                        id=state.id,
                        player_id=owner,
                        item_id=state.item_id,
                        quantity=state.quantity,
                        equipped_slot=state.equipped_slot,
                    )
                )
                locks.inventory[state.id] = owner
        for item_id in changes.inventory_delete:
            self._require(item_id in locks.inventory, "inventory entry")
            item = await session.get(InventoryItem, item_id)
            if item is not None:
                await session.delete(item)
            del locks.inventory[item_id]

        for create in changes.floor_items_create:
            self._require(create.room_id in locks.rooms, f"room {create.room_id}")
            session.add(FloorItem(**create.model_dump()))
            locks.floor_items.add(create.id)
        for floor_update in changes.floor_items_update:
            self._require(floor_update.id in locks.floor_items, "floor item")
            floor = await session.get(FloorItem, floor_update.id)
            assert floor is not None
            floor.quantity = floor_update.quantity
        for floor_id in changes.floor_items_delete:
            self._require(floor_id in locks.floor_items, "floor item")
            floor = await session.get(FloorItem, floor_id)
            if floor is not None:
                await session.delete(floor)
            locks.floor_items.discard(floor_id)

        for spawn in changes.item_spawns:
            # spawn state follows its floor item, which is locked with the room
            self._require(spawn.room_id in locks.rooms, f"room {spawn.room_id}")
            state_row = await session.get(ItemSpawnState, (spawn.room_id, spawn.item_id))
            if state_row is None:
                session.add(ItemSpawnState(**spawn.model_dump()))
            else:
                state_row.next_spawn_at = spawn.next_spawn_at

        for group_update in changes.groups:
            self._require(group_update.group_id in locks.groups, "group")
            group = await session.get(Group, group_update.group_id)
            assert group is not None
            group.loot_cursor = group_update.loot_cursor

        await session.flush()

    async def add_log_entry(
        self, player_id: UUID, kind: str, text: str, created_at: datetime
    ) -> LogEntry:
        entry = LogEntry(player_id=player_id, kind=kind, text=text, created_at=created_at)
        self.session.add(entry)
        return entry
