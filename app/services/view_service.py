"""Read-only view of the world for the game page (ARCHITECTURE.md, "Frontend").

Unlocked reads, no log entries, no rate limit: this only *shows* state, it never decides
anything. Derived values (effective stats, regenerated monster HP, inventory capacity) come from
the same engine functions the actions use, so the page and the rules cannot disagree.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.content.describe import item_summary
from app.content.prices import buy_price, sell_price
from app.content.registry import ContentRegistry
from app.content.schemas import Direction, Heal, MerchantOffer, MerchantTemplate
from app.db.mappers import bank_item_state, player_snapshot
from app.db.models import (
    BankItem,
    FloorItem,
    Group,
    GroupInvite,
    InventoryItem,
    LogEntry,
    MonsterInstance,
    Player,
    Trade,
    TradeOffer,
)
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
    item_id: str = ""  # template id, for "Ansehen"


@dataclass(frozen=True)
class RoomView:
    id: str
    name: str
    description: str
    safe: bool
    bank: bool
    exits: list[ExitView]
    monsters: list[MonsterView]
    players: list[tuple[UUID, str, str]]  # (id, name, state)
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
    # best offer of a merchant in this room: (merchant id, gold per unit); None = cannot sell here
    sell: tuple[str, int] | None = None
    tradable: bool = True
    offered: bool = False  # part of my offer in an active trade
    summary: str = ""  # what it does, e.g. "Waffe · +3 Angriff"
    item_id: str = ""  # template id, for "Ansehen"


@dataclass(frozen=True)
class TradeItemView:
    inventory_item_id: UUID
    name: str
    icon: str | None
    quantity: int


@dataclass(frozen=True)
class TradeView:
    id: UUID
    status: str  # requested | open
    i_initiated: bool
    other_name: str
    revision: int
    my_items: list[TradeItemView]
    my_gold: int
    my_confirmed: bool
    their_items: list[TradeItemView]
    their_gold: int
    their_confirmed: bool


@dataclass(frozen=True)
class OfferView:
    item_id: str
    name: str
    description: str
    icon: str | None
    price: int
    affordable: bool
    summary: str = ""


@dataclass(frozen=True)
class ShopView:
    merchant_id: str
    name: str
    greeting: str
    offers: list[OfferView]


@dataclass(frozen=True)
class BankView:
    used: int
    capacity: int
    items: list[ItemView]


@dataclass(frozen=True)
class InventoryView:
    used: int
    capacity: int
    bags: int
    bag_slots: int
    carried: list[ItemView]
    equipped: list[ItemView]


@dataclass(frozen=True)
class MemberView:
    id: UUID
    name: str
    hp: int
    max_hp: int
    state: str
    room_name: str
    online: bool
    leader: bool
    me: bool


@dataclass(frozen=True)
class InviteView:
    inviter_id: UUID
    inviter_name: str


@dataclass(frozen=True)
class GroupView:
    members: list[MemberView]  # join order; empty without a group
    i_lead: bool
    can_invite: bool
    invites: list[InviteView]  # open invitations to me


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
    group: GroupView
    shops: list[ShopView]  # merchants in the room
    bank: BankView | None  # only in bank rooms
    trade: TradeView | None  # my active trade (request or open)


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
                    select(col(Player.id), col(Player.name), col(Player.state))
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
            floor_views.append(
                FloorItemView(item_row.id, item.name, item.icon, item_row.quantity, item.id)
            )
        room_view = RoomView(
            id=room.id,
            name=room.name,
            description=room.description,
            safe=room.safe,
            bank=room.bank,
            exits=[
                ExitView(
                    direction,
                    DIRECTION_NAMES[direction].capitalize(),  # "oben" -> "Oben" on buttons
                    self.content.get_room(target).name,
                )
                for direction, target in room.exits.items()
            ],
            monsters=monster_views,
            players=[(pid, name, state) for pid, name, state in others],
            merchants=[m.name for m in self.content.merchants_in_room(room.id)],
            floor_items=floor_views,
        )

        merchants = self.content.merchants_in_room(room.id)
        trade_view = await self._trade(row.id)
        offered = {i.inventory_item_id for i in trade_view.my_items} if trade_view else set()
        items = [self._item(entry, merchants, offered) for entry in snapshot.inventory]
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
        group_view = await self._group(row, now)
        shops = [
            ShopView(
                merchant_id=merchant.id,
                name=merchant.name,
                greeting=merchant.greeting,
                offers=[self._offer(offer, row.gold) for offer in merchant.sells],
            )
            for merchant in merchants
        ]
        bank_view = await self._bank(row.id) if room.bank else None
        return GameView(
            player=player,
            room=room_view,
            inventory=inventory_view,
            group=group_view,
            shops=shops,
            bank=bank_view,
            trade=trade_view,
        )

    async def _trade(self, player_id: UUID) -> TradeView | None:
        async with self.sessions() as session:
            trade = (
                await session.scalars(
                    select(Trade).where(
                        col(Trade.status).in_(("requested", "open")),
                        (col(Trade.initiator_id) == player_id)
                        | (col(Trade.partner_id) == player_id),
                    )
                )
            ).first()
            if trade is None:
                return None
            other_id = trade.partner_id if trade.initiator_id == player_id else trade.initiator_id
            other = await session.get(Player, other_id)
            rows = (
                await session.execute(
                    select(TradeOffer, InventoryItem)
                    .join(InventoryItem, col(InventoryItem.id) == col(TradeOffer.inventory_item_id))
                    .where(col(TradeOffer.trade_id) == trade.id)
                    .order_by(col(InventoryItem.item_id))
                )
            ).all()

        def items(owner: UUID) -> list[TradeItemView]:
            views = []
            for offer, entry in rows:
                if offer.player_id != owner:
                    continue
                item = self.content.get_item(entry.item_id)
                views.append(TradeItemView(entry.id, item.name, item.icon, offer.quantity))
            return views

        mine_first = trade.initiator_id == player_id
        return TradeView(
            id=trade.id,
            status=trade.status,
            i_initiated=mine_first,
            other_name=other.name if other is not None else "?",
            revision=trade.revision,
            my_items=items(player_id),
            my_gold=trade.initiator_gold if mine_first else trade.partner_gold,
            my_confirmed=trade.initiator_confirmed if mine_first else trade.partner_confirmed,
            their_items=items(other_id),
            their_gold=trade.partner_gold if mine_first else trade.initiator_gold,
            their_confirmed=trade.partner_confirmed if mine_first else trade.initiator_confirmed,
        )

    def _offer(self, offer: MerchantOffer, gold: int) -> OfferView:
        item = self.content.get_item(offer.item_id)
        price = buy_price(offer, item, self.content.rules)
        return OfferView(
            item.id,
            item.name,
            item.description,
            item.icon,
            price,
            price <= gold,
            item_summary(item),
        )

    async def _bank(self, player_id: UUID) -> BankView:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(BankItem)
                    .where(col(BankItem.player_id) == player_id)
                    .order_by(col(BankItem.item_id), col(BankItem.id))
                )
            ).all()
        stored = [bank_item_state(r) for r in rows]
        return BankView(
            used=get_occupied_slots(stored),
            capacity=self.content.rules.bank.slots,
            items=[self._item(entry) for entry in stored],
        )

    async def _group(self, row: Player, now: datetime) -> GroupView:
        rules = self.content.rules
        async with self.sessions() as session:
            members: list[Player] = []
            leader_id = None
            if row.group_id is not None:
                group = await session.get(Group, row.group_id)
                leader_id = group.leader_id if group is not None else None
                members = list(
                    (
                        await session.scalars(
                            select(Player)
                            .where(
                                col(Player.group_id) == row.group_id,
                                col(Player.deleted_at).is_(None),
                            )
                            .order_by(col(Player.group_joined_at), col(Player.id))
                        )
                    ).all()
                )
            items = (
                list(
                    (
                        await session.scalars(
                            select(InventoryItem).where(
                                col(InventoryItem.player_id).in_([m.id for m in members]),
                                col(InventoryItem.equipped_slot).is_not(None),
                            )
                        )
                    ).all()
                )
                if members
                else []
            )
            invites = (
                await session.execute(
                    select(col(GroupInvite.inviter_id), col(Player.name))
                    .join(Player, col(Player.id) == col(GroupInvite.inviter_id))
                    .where(
                        col(GroupInvite.player_id) == row.id,
                        col(GroupInvite.expires_at) > now,
                        col(Player.online),
                    )
                    .order_by(col(GroupInvite.expires_at))
                )
            ).all()

        member_views = []
        for member in members:
            equipped = [i for i in items if i.player_id == member.id]
            stats = stats_of(player_snapshot(member, equipped), self.content.items)
            member_views.append(
                MemberView(
                    id=member.id,
                    name=member.name,
                    hp=member.hp,
                    max_hp=stats.max_hp,
                    state=member.state,
                    room_name=self.content.get_room(member.current_room_id).name,
                    online=member.online,
                    leader=member.id == leader_id,
                    me=member.id == row.id,
                )
            )
        i_lead = row.group_id is not None and leader_id == row.id
        return GroupView(
            members=member_views,
            i_lead=i_lead,
            can_invite=(row.group_id is None or i_lead)
            and len(member_views) < rules.max_group_size,
            invites=[InviteView(inviter_id, name) for inviter_id, name in invites],
        )

    def _item(
        self,
        entry: InventoryItemState,
        merchants: Sequence[MerchantTemplate] = (),
        offered: set[UUID] | None = None,
    ) -> ItemView:
        item = self.content.get_item(entry.item_id)
        offers = [
            (merchant.id, price)
            for merchant in merchants
            if (price := sell_price(item, merchant, self.content.rules)) is not None
        ]
        return ItemView(
            id=entry.id,
            name=item.name,
            description=item.description,
            icon=item.icon,
            quantity=entry.quantity,
            equipped_slot=entry.equipped_slot,
            equippable=item.slot is not None,
            usable=any(isinstance(e, Heal) for e in item.effects),
            sell=max(offers, key=lambda o: o[1]) if offers else None,
            tradable=item.tradable,
            offered=entry.id in (offered or set()),
            summary=item_summary(item),
            item_id=item.id,
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
