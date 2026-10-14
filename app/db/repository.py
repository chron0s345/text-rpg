"""Database access for the service layer: locking snapshot loads and applying engine results.

Lock order (ARCHITECTURE.md, "Locking") — every code path takes row locks in this order to avoid
deadlocks: Group -> Trade -> Player (sorted by id) -> InventoryItem -> BankItem ->
QuestProgress -> MonsterInstance -> FloorItem -> ItemSpawnState;
group actions: Group -> Player -> GroupInvite;
trade actions: Trade -> Player -> InventoryItem -> TradeOffer.
Within a table rows are locked sorted by primary key.

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

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.db.mappers import (
    bank_item_state,
    floor_item_snapshot,
    group_snapshot,
    invite_snapshot,
    monster_snapshot,
    participation_snapshot,
    player_snapshot,
    trade_snapshot,
)
from app.db.models import (
    BankItem,
    CombatParticipation,
    FloorItem,
    Group,
    GroupInvite,
    InventoryItem,
    ItemSpawnState,
    LogEntry,
    MonsterInstance,
    Player,
    QuestProgress,
    Trade,
    TradeLog,
    TradeOffer,
)
from app.engine.events import StateChanges
from app.engine.state import (
    ActionSnapshot,
    GroupActionSnapshot,
    PlayerSnapshot,
    TradeActionSnapshot,
)

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
    bank: dict[UUID, UUID] = field(default_factory=dict)  # bank item id -> owner id
    bank_owners: set[UUID] = field(default_factory=set)  # banks locked (also when empty)
    quest_owners: set[UUID] = field(default_factory=set)  # players whose quest rows are locked
    monsters: set[UUID] = field(default_factory=set)
    participations: set[tuple[UUID, UUID]] = field(default_factory=set)
    floor_items: set[UUID] = field(default_factory=set)
    rooms: set[str] = field(default_factory=set)  # rooms whose monsters/floor items are locked
    invites: set[tuple[UUID, UUID]] = field(default_factory=set)  # (inviter_id, player_id)
    trades: set[UUID] = field(default_factory=set)
    trade_offers: set[tuple[UUID, UUID]] = field(default_factory=set)  # (trade_id, item_id)


def _locked(stmt: Select[Any]) -> Select[Any]:
    return stmt.with_for_update().execution_options(populate_existing=True)


class Repository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.locks = Locks()
        self._transaction: object | None = None  # the transaction the locks belong to

    def _bind_transaction(self) -> None:
        """Ties the lock set to the current transaction; any other transaction is an error.

        Savepoints are refused: rolling back to a savepoint releases row locks taken after it
        in PostgreSQL, while the lock set here would still claim them.
        """
        if self.session.in_nested_transaction():
            raise StaleRepositoryError("row locks inside a savepoint are not supported")
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

    async def lock_quests(self, player_ids: Iterable[UUID]) -> list[QuestProgress]:
        """Quest rows of these players. New rows are only ever inserted for a locked player,
        so the player lock already serializes them; locking existing rows keeps updates safe."""
        ids = sorted(set(player_ids))
        if not ids:
            return []
        stmt = (
            select(QuestProgress)
            .where(col(QuestProgress.player_id).in_(ids))
            .order_by(col(QuestProgress.player_id), col(QuestProgress.quest_id))
        )
        rows = await self._lock_rows(stmt)
        self.locks.quest_owners.update(ids)
        return rows

    async def lock_bank(self, player_ids: Iterable[UUID]) -> list[BankItem]:
        ids = sorted(set(player_ids))
        if not ids:
            return []
        stmt = (
            select(BankItem)
            .where(col(BankItem.player_id).in_(ids))
            .order_by(col(BankItem.player_id), col(BankItem.id))
        )
        rows = await self._lock_rows(stmt)
        self.locks.bank.update({r.id: r.player_id for r in rows})
        self.locks.bank_owners.update(ids)
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

    async def lock_room_items(self, room_id: str) -> tuple[list[FloorItem], list[ItemSpawnState]]:
        """Floor items and spawn states of a room (world ticker: item respawns). Same order as
        picking up (floor items, then the spawn state), so the two serialize on the room."""
        floor_items = await self.lock_floor_items_in_room(room_id)
        stmt = (
            select(ItemSpawnState)
            .where(col(ItemSpawnState.room_id) == room_id)
            .order_by(col(ItemSpawnState.item_id))
        )
        spawn_states = await self._lock_rows(stmt)
        self.locks.rooms.add(room_id)
        return floor_items, spawn_states

    async def _apply_trades(self, changes: StateChanges) -> None:
        """Trades first: ending offers must be gone before offered items change or vanish."""
        session, locks = self.session, self.locks
        for trade_create in changes.trades_create:
            self._require(
                trade_create.initiator_id in locks.players
                and trade_create.partner_id in locks.players,
                "trade players",
            )
            session.add(Trade(**trade_create.model_dump()))
            locks.trades.add(trade_create.id)
        for trade_update in changes.trades:
            self._require(trade_update.trade_id in locks.trades, "trade")
            trade = await session.get(Trade, trade_update.trade_id)
            assert trade is not None
            for key, value in trade_update.model_dump(
                exclude_unset=True, exclude={"trade_id"}
            ).items():
                setattr(trade, key, value)
        for offer_delete in changes.trade_offers_delete:
            offer_key = (offer_delete.trade_id, offer_delete.inventory_item_id)
            self._require(offer_key in locks.trade_offers, "trade offer")
            offer = await session.get(TradeOffer, offer_key)
            if offer is not None:
                await session.delete(offer)
            locks.trade_offers.discard(offer_key)
        for offer_upsert in changes.trade_offers_upsert:
            offer_key = (offer_upsert.trade_id, offer_upsert.inventory_item_id)
            self._require(
                offer_upsert.trade_id in locks.trades
                and locks.inventory.get(offer_upsert.inventory_item_id) == offer_upsert.player_id,
                "trade offer item",
            )
            offer = await session.get(TradeOffer, offer_key)
            if offer is None:
                session.add(TradeOffer(**offer_upsert.model_dump()))
                locks.trade_offers.add(offer_key)
            else:
                offer.quantity = offer_upsert.quantity
        for log in changes.trade_logs:
            self._require(log.trade_id in locks.trades, "trade")
            session.add(TradeLog(**log.model_dump()))
        if changes.trades_create or changes.trades or changes.trade_offers_delete:
            await session.flush()

    async def lock_trades(self, trade_ids: Iterable[UUID]) -> list[Trade]:
        ids = sorted(set(trade_ids))
        if not ids:
            return []
        stmt = select(Trade).where(col(Trade.id).in_(ids)).order_by(col(Trade.id))
        rows = await self._lock_rows(stmt)
        self.locks.trades.update(r.id for r in rows)
        return rows

    async def lock_trade_offers(self, trade_ids: Iterable[UUID]) -> list[TradeOffer]:
        ids = sorted(set(trade_ids))
        if not ids:
            return []
        stmt = (
            select(TradeOffer)
            .where(col(TradeOffer.trade_id).in_(ids))
            .order_by(col(TradeOffer.trade_id), col(TradeOffer.inventory_item_id))
        )
        rows = await self._lock_rows(stmt)
        self.locks.trade_offers.update((r.trade_id, r.inventory_item_id) for r in rows)
        return rows

    async def _active_trade_ids(self, player_ids: Iterable[UUID]) -> set[UUID]:
        ids = list(set(player_ids))
        stmt = select(col(Trade.id)).where(
            col(Trade.status).in_(ACTIVE_TRADE_STATUSES),
            col(Trade.initiator_id).in_(ids) | col(Trade.partner_id).in_(ids),
        )
        return set((await self.session.scalars(stmt)).all())

    async def load_trade_snapshot(
        self,
        actor_id: UUID,
        *,
        trade_id: UUID | None = None,
        partner_id: UUID | None = None,
    ) -> TradeActionSnapshot:
        """Locks a trade action: the trade it names (if the actor is part of it), every active
        trade of the actor and of the other trader, both players and their inventories, and the
        offers. Order: Trade -> Player -> InventoryItem -> TradeOffer.

        The other trader is ``partner_id`` (request), the other side of ``trade_id``, or else
        the other side of the actor's active trade (housekeeping checks). New trades are only
        created under both players' locks, so the active trades read again after locking them
        are final; a trade that appeared in between means retry."""
        named = None
        if trade_id is not None:
            named = (
                await self.session.execute(
                    select(col(Trade.initiator_id), col(Trade.partner_id)).where(
                        col(Trade.id) == trade_id
                    )
                )
            ).first()
            if named is not None and actor_id not in named:
                named = None  # not the actor's trade: it sees nothing of it
        if partner_id is None and named is not None:
            partner_id = named[1] if named[0] == actor_id else named[0]
        if partner_id is None and trade_id is None:
            for other_trade in await self._active_trade_ids([actor_id]):
                row = (
                    await self.session.execute(
                        select(col(Trade.initiator_id), col(Trade.partner_id)).where(
                            col(Trade.id) == other_trade
                        )
                    )
                ).one()
                partner_id = row[1] if row[0] == actor_id else row[0]
                break
        player_ids = [actor_id, *([partner_id] if partner_id is not None else [])]

        trade_ids = await self._active_trade_ids(player_ids)
        if named is not None and trade_id is not None:
            trade_ids.add(trade_id)
        trades = await self.lock_trades(trade_ids)
        players = await self.lock_players(player_ids)
        actor = next((p for p in players if p.id == actor_id), None)
        if actor is None or actor.deleted_at is not None:
            raise UnknownPlayerError(actor_id)
        if not (await self._active_trade_ids(player_ids)) <= self.locks.trades:
            raise SnapshotChangedError("a trade was started while locking")
        partner = next((p for p in players if p.id != actor_id and p.deleted_at is None), None)

        inventory = await self.lock_inventories(p.id for p in players)
        offers = await self.lock_trade_offers(t.id for t in trades)
        active = {t.id for t in trades if t.status in ACTIVE_TRADE_STATUSES}
        locked_items = {o.inventory_item_id for o in offers if o.trade_id in active}

        def snap(row: Player) -> PlayerSnapshot:
            items = [i for i in inventory if i.player_id == row.id]
            return player_snapshot(row, items, [i.id for i in items if i.id in locked_items])

        return TradeActionSnapshot(
            actor=snap(actor),
            partner=snap(partner) if partner is not None else None,
            trades=[trade_snapshot(t, [o for o in offers if o.trade_id == t.id]) for t in trades],
        )

    async def lock_invites(self, player_ids: Iterable[UUID]) -> list[GroupInvite]:
        """Open invitations from or to these players (taken after the players)."""
        ids = sorted(set(player_ids))
        if not ids:
            return []
        stmt = (
            select(GroupInvite)
            .where((col(GroupInvite.inviter_id).in_(ids)) | (col(GroupInvite.player_id).in_(ids)))
            .order_by(col(GroupInvite.inviter_id), col(GroupInvite.player_id))
        )
        rows = await self._lock_rows(stmt)
        self.locks.invites.update((r.inviter_id, r.player_id) for r in rows)
        return rows

    async def load_group_snapshot(
        self,
        actor_id: UUID,
        *,
        other_id: UUID | None = None,
        other_name: str | None = None,
    ) -> GroupActionSnapshot:
        """Locks what a group action may touch, wherever the players are: the groups of the
        actor and of the other player (by id or case-insensitive name), all their members, the
        two players, and their open invitations. Order: groups -> players -> invites."""
        pre_actor = (
            await self.session.execute(
                select(col(Player.group_id)).where(
                    col(Player.id) == actor_id, col(Player.deleted_at).is_(None)
                )
            )
        ).first()
        if pre_actor is None:
            raise UnknownPlayerError(actor_id)
        other_filter = None
        if other_id is not None:
            other_filter = col(Player.id) == other_id
        elif other_name is not None:
            other_filter = func.lower(col(Player.name)) == other_name.strip().lower()
        pre_other = None
        if other_filter is not None:
            pre_other = (
                await self.session.execute(
                    select(col(Player.id), col(Player.group_id)).where(
                        other_filter, col(Player.deleted_at).is_(None)
                    )
                )
            ).first()
        group_ids = {g for g in (pre_actor.group_id, pre_other[1] if pre_other else None) if g}

        groups = await self.lock_groups(group_ids)
        members = (
            (
                await self.session.scalars(
                    select(col(Player.id)).where(
                        col(Player.group_id).in_([g.id for g in groups]),
                        col(Player.deleted_at).is_(None),
                    )
                )
            ).all()
            if groups
            else []
        )
        wanted = {actor_id, *members, *([pre_other[0]] if pre_other else [])}
        players = await self.lock_players(wanted)
        actor = next((p for p in players if p.id == actor_id), None)
        if actor is None or actor.deleted_at is not None:
            raise UnknownPlayerError(actor_id)
        for player in players:
            if player.group_id is not None and player.group_id not in self.locks.groups:
                raise SnapshotChangedError(f"group {player.group_id} of {player.id} is not locked")
        invites = await self.lock_invites(p.id for p in players)

        def by_join(row: Player) -> tuple[datetime, UUID]:
            assert row.group_joined_at is not None
            return row.group_joined_at, row.id

        snapshots = []
        for group in groups:
            in_group = sorted((p for p in players if p.group_id == group.id), key=by_join)
            snapshots.append(group_snapshot(group, [p.id for p in in_group]))
        return GroupActionSnapshot(
            actor=player_snapshot(actor, []),
            players=[player_snapshot(p, []) for p in players if p.id != actor_id],
            groups=snapshots,
            invites=[invite_snapshot(i) for i in invites],
        )

    # --- snapshot ----------------------------------------------------------------------------

    async def load_action_snapshot(
        self, actor_id: UUID, room_id: str | None = None, *, with_bank: bool = False
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
        bank = await self.lock_bank([actor.id]) if with_bank else []
        quests = await self.lock_quests(player_ids)
        monsters = await self.lock_monsters_in_room(room)
        participations = await self.lock_participations([m.id for m in monsters])
        floor_items = await self.lock_floor_items_in_room(room)
        self.locks.rooms.add(room)

        locked_items = await self._items_in_active_trades(player_ids)
        by_player: dict[UUID, list[InventoryItem]] = {pid: [] for pid in player_ids}
        for item in inventory:
            by_player[item.player_id].append(item)

        quests_by_player: dict[UUID, list[QuestProgress]] = {pid: [] for pid in player_ids}
        for quest in quests:
            quests_by_player[quest.player_id].append(quest)

        def snap(row: Player) -> PlayerSnapshot:
            return player_snapshot(
                row,
                by_player[row.id],
                [i.id for i in by_player[row.id] if i.id in locked_items],
                quests_by_player[row.id],
            )

        return ActionSnapshot(
            actor=snap(actor),
            room_players=[snap(p) for p in in_room],
            monsters=[monster_snapshot(m) for m in monsters],
            participations=[participation_snapshot(p) for p in participations],
            groups=[group_snapshot(g, await self._member_ids(g.id)) for g in groups],
            floor_items=[floor_item_snapshot(f) for f in floor_items],
            bank=[bank_item_state(b) for b in bank],
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

        await self._apply_trades(changes)

        for group_create in changes.groups_create:
            self._require(group_create.leader_id in locks.players, "group leader")
            session.add(Group(**group_create.model_dump()))
            locks.groups.add(group_create.id)
        if changes.groups_create:
            await session.flush()  # members reference the new group

        for player_update in changes.players:
            self._require(player_update.player_id in locks.players, "player")
            player = await session.get(Player, player_update.player_id)
            assert player is not None
            for key, value in player_update.model_dump(
                exclude_unset=True, exclude={"player_id"}
            ).items():
                setattr(player, key, value)

        for deleted in changes.quests_delete:
            self._require(deleted.player_id in locks.quest_owners, "quest")
            row = await session.get(QuestProgress, (deleted.player_id, deleted.quest_id))
            if row is not None:
                await session.delete(row)

        for quest in changes.quests_upsert:
            self._require(quest.player_id in locks.quest_owners, "quest")
            row = await session.get(QuestProgress, (quest.player_id, quest.quest_id))
            if row is None:
                row = QuestProgress(player_id=quest.player_id, quest_id=quest.quest_id)
                session.add(row)
            row.status = quest.status
            row.step = quest.step
            row.progress = quest.progress
            row.times_completed = quest.times_completed

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

        for bank_upsert in changes.bank_upsert:
            state, owner = bank_upsert.item, bank_upsert.player_id
            self._require(owner in locks.bank_owners, "bank")
            if state.id in locks.bank:
                if locks.bank[state.id] != owner:
                    raise InconsistentChangesError(f"bank entry {state.id} changed owner")
                stored = await session.get(BankItem, state.id)
                assert stored is not None
                if stored.item_id != state.item_id:
                    raise InconsistentChangesError(f"bank entry {state.id} changed item")
                stored.quantity = state.quantity
            else:
                session.add(
                    BankItem(
                        id=state.id, player_id=owner, item_id=state.item_id, quantity=state.quantity
                    )
                )
                locks.bank[state.id] = owner
        for bank_id in changes.bank_delete:
            self._require(bank_id in locks.bank, "bank entry")
            stored = await session.get(BankItem, bank_id)
            if stored is not None:
                await session.delete(stored)
            del locks.bank[bank_id]

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
            for key, value in group_update.model_dump(
                exclude_unset=True, exclude={"group_id"}
            ).items():
                setattr(group, key, value)

        for invite_delete in changes.invites_delete:
            invite_key = (invite_delete.inviter_id, invite_delete.player_id)
            self._require(invite_key in locks.invites, "group invite")
            invite = await session.get(GroupInvite, invite_key)
            if invite is not None:
                await session.delete(invite)
            locks.invites.discard(invite_key)
        for invite_upsert in changes.invites_upsert:
            invite_key = (invite_upsert.inviter_id, invite_upsert.player_id)
            self._require(
                invite_upsert.inviter_id in locks.players
                and invite_upsert.player_id in locks.players,
                "group invite players",
            )
            invite = await session.get(GroupInvite, invite_key)
            if invite is None:
                session.add(GroupInvite(**invite_upsert.model_dump()))
                locks.invites.add(invite_key)
            else:
                invite.group_id = invite_upsert.group_id
                invite.expires_at = invite_upsert.expires_at

        await session.flush()  # members leave before their group is deleted
        for group_id in changes.groups_delete:
            self._require(group_id in locks.groups, "group")
            group = await session.get(Group, group_id)
            if group is not None:
                await session.delete(group)  # its open invitations cascade
            locks.groups.discard(group_id)

        await session.flush()

    async def add_log_entry(
        self, player_id: UUID, kind: str, text: str, created_at: datetime
    ) -> LogEntry:
        entry = LogEntry(player_id=player_id, kind=kind, text=text, created_at=created_at)
        self.session.add(entry)
        return entry
