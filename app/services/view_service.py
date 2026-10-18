"""Read-only view of the world for the game page (ARCHITECTURE.md, "Frontend").

Unlocked reads, no log entries, no rate limit: this only *shows* state, it never decides
anything. Derived values (effective stats, regenerated monster HP, inventory capacity) come from
the same engine functions the actions use, so the page and the rules cannot disagree.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.content.describe import SLOT_NAMES, item_summary, modifiers_text
from app.content.prices import buy_price, sell_price
from app.content.registry import ContentRegistry
from app.content.schemas import (
    Direction,
    Heal,
    ItemSet,
    KillObjective,
    MerchantOffer,
    MerchantTemplate,
    QuestTemplate,
    VisitObjective,
)
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
    QuestProgress,
    Trade,
    TradeOffer,
)
from app.engine.achievements import species_killed
from app.engine.gear import best_upgrade, junk_for
from app.engine.inventory import calculate_inventory_capacity, get_occupied_slots
from app.engine.materials import count_available, quantity_text
from app.engine.movement import DIRECTION_NAMES
from app.engine.quests import (
    current_step,
    objective_progress,
    quests_offered_here,
    ready_to_turn_in,
    step_text,
)
from app.engine.regeneration import calculate_current_monster_hp
from app.engine.state import InventoryItemState, PlayerSnapshot
from app.engine.stats import active_set_effects, stats_of, worn_sets
from app.services.action_service import utc_now
from app.services.gear import Comparison, compare
from app.services.minimap import Minimap, minimap

LOG_LINES = 50


@dataclass(frozen=True)
class PlayerView:
    id: UUID
    name: str
    level: int
    xp: int
    xp_next: int | None  # XP needed for the next level; None at max level
    xp_floor: int  # XP needed for the current level (start of the XP bar)
    hp: int
    max_hp: int
    attack: int
    defense: int
    gold: int
    state: str
    combat_target_id: UUID | None
    title: str = ""  # chosen achievement title, shown after the name


@dataclass(frozen=True)
class ExitView:
    direction: Direction
    label: str
    room_name: str


@dataclass(frozen=True)
class TravelView:
    room_id: str
    room_name: str
    by: str
    gold: int
    min_level: int
    possible: bool  # level reached and gold enough


@dataclass(frozen=True)
class MonsterView:
    id: UUID
    name: str
    hp: int
    max_hp: int
    fighting_me: bool
    level: int
    boss: bool
    rare: bool  # the rare form of a monster


@dataclass(frozen=True)
class FloorItemView:
    id: UUID
    name: str
    icon: str | None
    quantity: int
    item_id: str = ""  # template id, for "Ansehen"
    compare: Comparison | None = None  # against what is worn in its slot


@dataclass(frozen=True)
class RoomView:
    id: str
    name: str
    description: str
    safe: bool
    bank: bool
    exits: list[ExitView]
    monsters: list[MonsterView]
    players: list[tuple[UUID, str, str, str]]  # (id, name, state, title or "")
    merchants: list[str]
    floor_items: list[FloorItemView]
    map: Minimap
    travel: list[TravelView] = field(default_factory=list)


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
    compare: Comparison | None = None  # carried and banked gear: against what is worn


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
    compare: Comparison | None = None


@dataclass(frozen=True)
class ShopView:
    merchant_id: str
    name: str
    greeting: str
    offers: list[OfferView]
    junk: list[str] = field(default_factory=list)  # "2x Rattenschwanz", ... (sell_junk)
    junk_gold: int = 0


@dataclass(frozen=True)
class BankView:
    used: int
    capacity: int
    items: list[ItemView]
    materials: int = 0  # free material stacks in the backpack (bank_deposit_materials)


@dataclass(frozen=True)
class SetView:
    name: str
    worn: int
    total: int
    active: str  # the bonuses reached so far, e.g. "+1 Verteidigung"; "" if none yet
    next: str  # the next bonus, e.g. "bei 4: +20 max. HP"; "" when complete


@dataclass(frozen=True)
class InventoryView:
    used: int
    capacity: int
    bags: int
    bag_slots: int
    carried: list[ItemView]
    equipped: list[ItemView]
    sets: list[SetView]  # sets with at least one piece equipped


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
class QuestOfferView:
    id: str
    name: str
    giver: str
    description: str
    objective: str
    reward: str


@dataclass(frozen=True)
class ActiveQuestView:
    id: str
    name: str
    giver: str
    objective: str  # the current step (or "Erledigt – abgeben" once all are done)
    done: int  # of the current step
    needed: int
    step: int  # 1-based number of the current step
    steps: int
    show_count: bool  # done/needed means something (not for visits or when all is done)
    can_turn_in: bool  # complete and the giver stands here


@dataclass(frozen=True)
class QuestsView:
    offers: list[QuestOfferView]  # from givers in this room
    active: list[ActiveQuestView]
    completed: int  # number of quests finished at least once


@dataclass(frozen=True)
class IngredientView:
    name: str
    have: int
    needed: int


@dataclass(frozen=True)
class RecipeView:
    id: str
    crafter: str
    result: str  # "2× Heiltrank"
    summary: str  # what the result does
    ingredients: list[IngredientView]
    gold: int
    min_level: int
    can_craft: bool  # level, ingredients and gold suffice


@dataclass(frozen=True)
class SlotView:
    slot: str
    label: str  # "Waffe", "Kopf", …
    item: ItemView | None  # None: empty
    upgrade: str  # name of a better piece in the backpack, "" if none


@dataclass(frozen=True)
class EquipmentView:
    slots: list[SlotView]  # every equipment slot, in a fixed order, empty ones included
    bags: list[ItemView]  # worn bags
    bag_slots: int
    sets: list[SetView]
    can_improve: bool = False  # equip_best would put something on


@dataclass(frozen=True)
class AchievementView:
    id: str
    name: str
    description: str
    title: str  # "" when the achievement brings none
    earned: bool
    shown: bool  # its title is the one the character shows


@dataclass(frozen=True)
class AchievementsView:
    earned: int
    entries: list[AchievementView]  # earned first (in the order earned), then the rest


@dataclass(frozen=True)
class BeastView:
    name: str
    level: int
    kills: int
    boss: bool
    rare: bool
    where: str  # rooms it lives in, "" if none (e.g. only summoned by quests)


@dataclass(frozen=True)
class BestiaryView:
    species: int  # different monsters slain (a rare variant counts as its base)
    total_species: int
    kills: int
    beasts: list[BeastView]  # slain ones, by level


@dataclass(frozen=True)
class GameView:
    player: PlayerView
    room: RoomView
    inventory: InventoryView
    group: GroupView
    shops: list[ShopView]  # merchants in the room
    bank: BankView | None  # only in bank rooms
    trade: TradeView | None  # my active trade (request or open)
    quests: QuestsView
    recipes: list[RecipeView]  # of the crafters in this room
    equipment: EquipmentView
    achievements: AchievementsView
    bestiary: BestiaryView


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
                    select(col(Player.id), col(Player.name), col(Player.state), col(Player.title))
                    .where(
                        col(Player.current_room_id) == row.current_room_id,
                        col(Player.online),
                        col(Player.deleted_at).is_(None),
                        col(Player.id) != player_id,
                    )
                    .order_by(col(Player.name))
                )
            ).all()
            quest_rows = (
                await session.scalars(
                    select(QuestProgress)
                    .where(col(QuestProgress.player_id) == player_id)
                    .order_by(col(QuestProgress.quest_id))
                )
            ).all()
            floor = (
                await session.scalars(
                    select(FloorItem)
                    .where(col(FloorItem.room_id) == row.current_room_id)
                    .order_by(col(FloorItem.item_id), col(FloorItem.id))
                )
            ).all()

        # items offered in a trade count as locked, exactly as in the engine (deliveries, crafting)
        trade_view = await self._trade(row.id)
        offered = {i.inventory_item_id for i in trade_view.my_items} if trade_view else set()
        snapshot = player_snapshot(row, inventory, locked_item_ids=offered, quests=quest_rows)
        stats = stats_of(snapshot, self.content.items)
        rules = self.content.rules
        next_rule = next((r for r in rules.levels if r.level == row.level + 1), None)
        player = PlayerView(
            id=row.id,
            name=row.name,
            level=row.level,
            xp=row.xp,
            xp_next=next_rule.xp_required if next_rule else None,
            xp_floor=rules.level_rule(row.level).xp_required,
            hp=row.hp,
            max_hp=stats.max_hp,
            attack=stats.attack,
            defense=stats.defense,
            gold=row.gold,
            state=row.state,
            combat_target_id=row.combat_target_id,
            title=self._title(row.title),
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
                    level=template.level,
                    boss=template.boss,
                    rare=self.content.base_monster_id(template.id) != template.id,
                )
            )
        floor_views = []
        for item_row in floor:
            if item_row.expires_at is not None and item_row.expires_at <= now:
                continue  # expired: hidden even before the ticker removes it
            item = self.content.get_item(item_row.item_id)
            floor_views.append(
                FloorItemView(
                    item_row.id,
                    item.name,
                    item.icon,
                    item_row.quantity,
                    item.id,
                    compare(snapshot, item.id, self.content),
                )
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
            players=[(pid, name, state, self._title(title)) for pid, name, state, title in others],
            travel=[
                TravelView(
                    route.room_id,
                    self.content.get_room(route.room_id).name,
                    route.by,
                    route.gold,
                    route.min_level,
                    row.level >= route.min_level and row.gold >= route.gold,
                )
                for route in room.travel
            ],
            merchants=[m.name for m in self.content.merchants_in_room(room.id)],
            floor_items=floor_views,
            map=minimap(self.content, room.id, self._quest_rooms(snapshot)),
        )

        merchants = self.content.merchants_in_room(room.id)
        items = [self._item(entry, merchants, offered, snapshot) for entry in snapshot.inventory]
        inventory_view = InventoryView(
            used=get_occupied_slots(snapshot.inventory),
            capacity=calculate_inventory_capacity(
                rules.inventory.slots, snapshot.inventory, self.content.items
            ),
            bags=sum(1 for i in snapshot.inventory if i.equipped_slot == "bag"),
            bag_slots=rules.inventory.bag_slots,
            carried=[i for i in items if i.equipped_slot is None],
            equipped=[i for i in items if i.equipped_slot is not None],
            sets=[
                self._set(item_set, worn)
                for item_set, worn in worn_sets(snapshot.inventory, self.content.items)
            ],
        )
        group_view = await self._group(row, now)
        shops = [
            ShopView(
                merchant_id=merchant.id,
                name=merchant.name,
                greeting=merchant.greeting,
                offers=[self._offer(offer, row.gold, snapshot) for offer in merchant.sells],
                **self._junk(snapshot, merchant),
            )
            for merchant in merchants
        ]
        bank_view = await self._bank(row.id, snapshot) if room.bank else None
        equipment = self._equipment(items, inventory_view, snapshot)
        return GameView(
            player=player,
            room=room_view,
            inventory=inventory_view,
            group=group_view,
            shops=shops,
            bank=bank_view,
            trade=trade_view,
            quests=self._quests(snapshot),
            recipes=self._recipes(snapshot),
            equipment=equipment,
            achievements=self._achievements(snapshot),
            bestiary=self._bestiary(snapshot),
        )

    def _quest_rooms(self, player: PlayerSnapshot) -> frozenset[str]:
        """Where the current steps of active quests lead: the room to visit, the rooms where the
        monsters to kill appear, or the giver once only the turn-in is left."""
        rooms: set[str] = set()
        for state in player.quests:
            quest = self.content.quests.get(state.quest_id)
            if state.status != "active" or quest is None:
                continue
            step = current_step(quest, state)
            if isinstance(step, VisitObjective):
                rooms.add(step.room_id)
            elif isinstance(step, KillObjective):
                rooms |= {
                    room.id
                    for room in self.content.rooms.values()
                    if any(spawn.monster_id == step.monster_id for spawn in room.spawns)
                }
            if ready_to_turn_in(quest, state, player):
                rooms.add(self.content.merchants[quest.giver_id].room_id)
        return frozenset(rooms)

    def _set(self, item_set: ItemSet, worn: int) -> SetView:
        upcoming = sorted((b for b in item_set.bonuses if b.pieces > worn), key=lambda b: b.pieces)
        return SetView(
            name=item_set.name,
            worn=worn,
            total=len(item_set.items),
            active=modifiers_text(active_set_effects(item_set, worn), combine=True),
            next=f"bei {upcoming[0].pieces}: {modifiers_text(upcoming[0].effects)}"
            if upcoming
            else "",
        )

    def _recipes(self, player: PlayerSnapshot) -> list[RecipeView]:
        views = []
        for recipe in self.content.recipes_in_room(player.current_room_id):
            ingredients = [
                IngredientView(
                    name=self.content.get_item(i.item_id).name,
                    have=count_available(player, i.item_id),
                    needed=i.quantity,
                )
                for i in recipe.ingredients
            ]
            views.append(
                RecipeView(
                    id=recipe.id,
                    crafter=self.content.merchants[recipe.crafter_id].name,
                    result=quantity_text(self.content, recipe.item_id, recipe.quantity),
                    summary=item_summary(self.content.get_item(recipe.item_id)),
                    ingredients=ingredients,
                    gold=recipe.gold,
                    min_level=recipe.min_level,
                    can_craft=player.level >= recipe.min_level
                    and player.gold >= recipe.gold
                    and all(i.have >= i.needed for i in ingredients),
                )
            )
        return views

    def _objective_text(self, quest: QuestTemplate) -> str:
        """All steps, e.g. "Gehe zu: Leuchtturm → Besiege 1× Seehexe Morwenna"."""
        return " → ".join(step_text(step, self.content) for step in quest.steps)

    def _reward_text(self, quest: QuestTemplate) -> str:
        parts = [f"{quest.reward.xp} XP"] if quest.reward.xp else []
        parts += [f"{quest.reward.gold} Gold"] if quest.reward.gold else []
        parts += [quantity_text(self.content, r.item_id, r.quantity) for r in quest.reward.items]
        return ", ".join(parts)

    def _quests(self, player: PlayerSnapshot) -> QuestsView:
        offers = [
            QuestOfferView(
                id=quest.id,
                name=quest.name,
                giver=self.content.merchants[quest.giver_id].name,
                description=quest.description,
                objective=self._objective_text(quest),
                reward=self._reward_text(quest),
            )
            for quest in quests_offered_here(player, self.content)
        ]
        active = []
        for state in player.quests:
            quest = self.content.quests.get(state.quest_id)
            if state.status != "active" or quest is None:
                continue
            giver = self.content.merchants[quest.giver_id]
            done, needed = objective_progress(quest, state, player)
            step = current_step(quest, state)
            active.append(
                ActiveQuestView(
                    id=quest.id,
                    name=quest.name,
                    giver=giver.name,
                    objective=step_text(step, self.content)
                    if step is not None
                    else "Erledigt – abgeben",
                    done=done,
                    needed=needed,
                    step=min(state.step, len(quest.steps) - 1) + 1,
                    steps=len(quest.steps),
                    show_count=not isinstance(step, VisitObjective) and step is not None,
                    can_turn_in=ready_to_turn_in(quest, state, player)
                    and giver.room_id == player.current_room_id,
                )
            )
        completed = sum(1 for q in player.quests if q.times_completed > 0)
        return QuestsView(offers=offers, active=active, completed=completed)

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

    def _offer(self, offer: MerchantOffer, gold: int, player: PlayerSnapshot) -> OfferView:
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
            compare(player, item.id, self.content),
        )

    def _bestiary(self, player: PlayerSnapshot) -> BestiaryView:
        content = self.content
        homes: dict[str, list[str]] = {}
        for room in content.rooms.values():
            for spawn in room.spawns:
                names = homes.setdefault(spawn.monster_id, [])
                if room.name not in names:
                    names.append(room.name)
        beasts = []
        for monster_id, kills in player.kills.items():
            template = content.monsters.get(monster_id)
            if template is None or kills <= 0:
                continue  # removed from the content
            base = content.base_monster_id(monster_id)
            beasts.append(
                BeastView(
                    name=template.name,
                    level=template.level,
                    kills=kills,
                    boss=template.boss,
                    rare=base != monster_id,
                    where=", ".join(homes.get(base, [])),
                )
            )
        beasts.sort(key=lambda b: (b.level, b.name))
        return BestiaryView(
            species=len(species_killed(player, content) & content.monsters.keys()),
            total_species=len({content.base_monster_id(m) for m in content.monsters}),
            kills=sum(b.kills for b in beasts),
            beasts=beasts,
        )

    def _title(self, achievement_id: str | None) -> str:
        achievement = self.content.achievements.get(achievement_id or "")
        return achievement.title or "" if achievement is not None else ""

    def _achievements(self, player: PlayerSnapshot) -> AchievementsView:
        templates = self.content.achievements
        earned = [templates[a] for a in player.achievements if a in templates]
        rest = [a for a in templates.values() if a.id not in player.achievements]
        return AchievementsView(
            earned=len(earned),
            entries=[
                AchievementView(
                    a.id,
                    a.name,
                    a.description,
                    a.title or "",
                    a in earned,
                    player.title == a.id,
                )
                for a in [*earned, *rest]
            ],
        )

    def _junk(self, player: PlayerSnapshot, merchant: MerchantTemplate) -> dict[str, Any]:
        junk = junk_for(player, merchant, self.content, self.content.rules)
        return {
            "junk": [
                f"{j.entry.quantity}x {self.content.items[j.entry.item_id].name}"
                if j.entry.quantity > 1
                else self.content.items[j.entry.item_id].name
                for j in junk
            ],
            "junk_gold": sum(j.price * j.entry.quantity for j in junk),
        }

    def _equipment(
        self, items: list[ItemView], inventory: InventoryView, player: PlayerSnapshot
    ) -> EquipmentView:
        worn = {i.equipped_slot: i for i in items if i.equipped_slot not in (None, "bag")}
        better: dict[str, str] = {}
        for carried in inventory.carried:
            slot = self.content.items[carried.item_id].slot
            if slot and carried.compare is not None and carried.compare.verdict == "better":
                better.setdefault(slot, carried.name)
        return EquipmentView(
            slots=[
                SlotView(slot, label, worn.get(slot), better.get(slot, ""))
                for slot, label in SLOT_NAMES.items()
                if slot != "bag"
            ],
            bags=[i for i in items if i.equipped_slot == "bag"],
            bag_slots=inventory.bag_slots,
            sets=inventory.sets,
            can_improve=best_upgrade(player, player.inventory, self.content) is not None,
        )

    async def _bank(self, player_id: UUID, player: PlayerSnapshot) -> BankView:
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
            items=[self._item(entry, player=player) for entry in stored],
            materials=sum(
                1
                for entry in player.inventory
                if entry.equipped_slot is None
                and entry.id not in player.locked_item_ids
                and self.content.items[entry.item_id].kind == "misc"
            ),
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
        player: PlayerSnapshot | None = None,
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
            compare=compare(player, item.id, self.content)
            if player is not None and entry.equipped_slot is None
            else None,
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
