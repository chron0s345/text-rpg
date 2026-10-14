"""A bot plays one character from level 1 to the top with the real engine and real content.

Fights run through ``combat.resolve_attack`` (damage, XP, level-ups, loot, gold, death), so
the simulation sees exactly what a player sees. Everything around the fights is a simple,
greedy player model:

- **Hunting:** a normal monster (no boss) of the highest level not above the character's,
  in a room where it spawns; on respawn it may come back as its rare form.
- **Healing:** players do not regenerate. Below ``heal_below`` of max HP the bot rests in a
  safe room (``actions.resolve_rest``: full HP, free). Dying wakes it up with full HP (and the
  death losses).
- **Trading:** everything it does not wear is sold at the best price; then it buys, slot by
  slot, the shop item that improves it most per gold.
- Merchants and safe rooms count as reachable at all times (walking is free), quests and
  crafting are left out: the numbers are the plain hunting baseline.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import uuid4

from app.content.prices import buy_price, sell_price
from app.content.registry import ContentRegistry
from app.content.schemas import EquipSlot, ItemTemplate, MonsterTemplate, StatModifier
from app.engine.actions import resolve_rest
from app.engine.combat import resolve_attack
from app.engine.state import (
    ActionSnapshot,
    InventoryItemState,
    MonsterSnapshot,
    PlayerSnapshot,
)
from app.engine.stats import stats_of

START = datetime(2026, 1, 1, 12, 0, 0)
FIGHT_LIMIT = 6000  # a run that needs more fights than this counts as stuck


@dataclass
class LevelReport:
    level: int
    fights: int = 0
    deaths: int = 0
    rare_fights: int = 0
    hp_lost: int = 0  # damage taken in fights at this level
    gold_looted: int = 0  # gold dropped by monsters
    gold_sold: int = 0  # gold from selling loot
    rests: int = 0  # times the bot rested in a safe room
    gold_gear: int = 0  # gold spent on equipment
    gold_lost: int = 0  # gold lost by dying
    attack: int = 0  # effective values when the level was reached
    defense: int = 0
    max_hp: int = 0
    gear: dict[str, str] = field(default_factory=dict)  # slot -> item name when reached

    @property
    def loss_per_fight(self) -> float:
        """Average share of max HP a fight cost."""
        return self.hp_lost / self.fights / self.max_hp if self.fights and self.max_hp else 0.0


@dataclass
class RunReport:
    levels: list[LevelReport]
    reached: int
    fights: int
    stuck: bool  # hit FIGHT_LIMIT before the top level


def _score(item: ItemTemplate) -> float:
    total = 0.0
    for effect in item.effects:
        if isinstance(effect, StatModifier):
            total += effect.amount * (0.2 if effect.stat == "max_hp" else 2.0)
    return total


class Bot:
    def __init__(
        self,
        content: ContentRegistry,
        seed: int = 1,
        heal_below: float = 0.6,
    ) -> None:
        self.content = content
        self.rules = content.rules
        self.rng = random.Random(seed)
        self.heal_below = heal_below
        level1 = self.rules.level_rule(1)
        self.player = PlayerSnapshot(
            id=uuid4(),
            account_id=uuid4(),
            name="Bot",
            hp=level1.max_hp,
            base_max_hp=level1.max_hp,
            base_attack=level1.attack,
            base_defense=level1.defense,
            xp=0,
            level=1,
            gold=0,
            current_room_id=self.rules.start_room_id,
            checkpoint_room_id=self.rules.town_room_id,
            state="exploring",
        )
        self.now = START
        self.spawns = self._spawn_rooms()
        self.safe_room = self.rules.town_room_id
        self.reports: dict[int, LevelReport] = {}
        self._enter_level()

    # --- content lookups -------------------------------------------------------------------

    def _spawn_rooms(self) -> dict[str, str]:
        """Normal (non-boss) monster -> a room it spawns in that allows fights."""
        rooms: dict[str, str] = {}
        for room in self.content.rooms.values():
            if room.safe:
                continue
            for spawn in room.spawns:
                template = self.content.monsters[spawn.monster_id]
                if not template.boss:
                    rooms.setdefault(template.id, room.id)
        return rooms

    def _shop(self) -> dict[EquipSlot, list[tuple[ItemTemplate, int]]]:
        by_slot: dict[EquipSlot, list[tuple[ItemTemplate, int]]] = {}
        for merchant in self.content.merchants.values():
            for offer in merchant.sells:
                item = self.content.items[offer.item_id]
                if item.slot is not None and item.slot != "bag":
                    by_slot.setdefault(item.slot, []).append(
                        (item, buy_price(offer, item, self.rules))
                    )
        return by_slot

    def _sale_value(self, item: ItemTemplate) -> int:
        prices = [sell_price(item, m, self.rules) for m in self.content.merchants.values()]
        return max((p for p in prices if p is not None), default=0)

    # --- bookkeeping -----------------------------------------------------------------------

    @property
    def report(self) -> LevelReport:
        return self.reports[self.player.level]

    def _max_hp(self) -> int:
        return stats_of(self.player, self.content.items).max_hp

    def _enter_level(self) -> None:
        stats = stats_of(self.player, self.content.items)
        self.reports[self.player.level] = LevelReport(
            level=self.player.level,
            attack=stats.attack,
            defense=stats.defense,
            max_hp=stats.max_hp,
            gear={
                i.equipped_slot: self.content.items[i.item_id].name
                for i in self.player.inventory
                if i.equipped_slot is not None
            },
        )

    def _worn(self, slot: EquipSlot) -> InventoryItemState | None:
        return next((i for i in self.player.inventory if i.equipped_slot == slot), None)

    # --- between fights --------------------------------------------------------------------

    def _equip_best(self) -> None:
        """Wear the best piece per slot among what the bot carries."""
        for entry in self.player.inventory:
            item = self.content.items[entry.item_id]
            if item.slot is None or item.slot == "bag" or entry.equipped_slot is not None:
                continue
            worn = self._worn(item.slot)
            if worn is None or _score(item) > _score(self.content.items[worn.item_id]):
                if worn is not None:
                    worn.equipped_slot = None
                if entry.quantity > 1:  # stackable gear: split one off
                    entry.quantity -= 1
                    entry = InventoryItemState(id=uuid4(), item_id=entry.item_id, quantity=1)
                    self.player.inventory.append(entry)
                entry.equipped_slot = item.slot

    def _sell_loot(self) -> None:
        keep = []
        for entry in self.player.inventory:
            item = self.content.items[entry.item_id]
            if entry.equipped_slot is not None:
                keep.append(entry)
                continue
            gold = self._sale_value(item) * entry.quantity
            self.player.gold += gold
            self.report.gold_sold += gold
        self.player.inventory = keep

    def _buy_gear(self) -> None:
        shop = self._shop()
        while True:
            best: tuple[float, ItemTemplate, int] | None = None
            for slot, offers in shop.items():
                worn = self._worn(slot)
                current = _score(self.content.items[worn.item_id]) if worn else 0.0
                for item, price in offers:
                    gain = _score(item) - current
                    if gain > 0 and self.player.gold >= price:
                        if best is None or gain / price > best[0]:
                            best = (gain / price, item, price)
            if best is None:
                return
            _, item, price = best
            self.player.gold -= price
            self.report.gold_gear += price
            self.player.inventory.append(
                InventoryItemState(id=uuid4(), item_id=item.id, quantity=1)
            )
            self._equip_best()
            self._sell_loot()  # the replaced piece

    def _heal(self) -> None:
        if self.player.hp >= self.heal_below * self._max_hp():
            return
        self.player.current_room_id = self.safe_room
        resolve_rest(
            ActionSnapshot(actor=self.player), self.content, self.rules, self.now, self.rng
        )
        self.report.rests += 1

    # --- fighting --------------------------------------------------------------------------

    def _pick_monster(self) -> MonsterTemplate:
        candidates = [
            self.content.monsters[mid]
            for mid in self.spawns
            if self.content.monsters[mid].level <= self.player.level
            and self.content.base_monster_id(mid) == mid
        ]
        top = max(m.level for m in candidates)
        base = self.rng.choice([m for m in candidates if m.level == top])
        rare = base.rare_variant
        if rare is not None and self.rng.random() < rare.chance:
            return self.content.monsters[rare.monster_id]
        return base

    def fight(self) -> None:
        template = self._pick_monster()
        room = self.spawns[self.content.base_monster_id(template.id)]
        actor = self.player
        actor.current_room_id = room
        monster = MonsterSnapshot(
            id=uuid4(), monster_id=template.id, room_id=room, hp=template.max_hp, alive=True
        )
        report = self.report
        report.fights += 1
        report.rare_fights += template.id != self.content.base_monster_id(template.id)
        level_before, gold_before = actor.level, actor.gold
        while monster.hp > 0 and actor.state != "dead":
            hp_before = actor.hp
            self.now += timedelta(seconds=2)
            resolve_attack(
                ActionSnapshot(actor=actor, monsters=[monster]),
                self.content,
                self.rules,
                self.now,
                self.rng,
                monster.id,
            )
            if actor.level == level_before:
                report.hp_lost += max(0, hp_before - actor.hp)
        if actor.state == "dead":
            report.deaths += 1
            report.gold_lost += max(0, gold_before - actor.gold)
            actor.state = "exploring"
            actor.combat_target_id = None
            actor.hp = self._max_hp()
        else:
            report.gold_looted += max(0, actor.gold - gold_before)
        actor.state = "exploring"
        actor.combat_target_id = None
        while actor.level > max(self.reports):
            self._enter_level()

    def run(self) -> RunReport:
        top = max(rule.level for rule in self.rules.levels)
        fights = 0
        while self.player.level < top and fights < FIGHT_LIMIT:
            self._equip_best()
            self._sell_loot()
            self._buy_gear()
            self._heal()
            self.fight()
            fights += 1
        return RunReport(
            levels=[self.reports[lvl] for lvl in sorted(self.reports)],
            reached=self.player.level,
            fights=fights,
            stuck=self.player.level < top,
        )


def simulate(content: ContentRegistry, seed: int = 1) -> RunReport:
    return Bot(content, seed=seed).run()
