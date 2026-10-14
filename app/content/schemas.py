from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    Field,
    NonNegativeInt,
    PositiveFloat,
    PositiveInt,
    model_validator,
)

Direction = Literal["north", "south", "east", "west", "up", "down"]
ItemKind = Literal["weapon", "armor", "consumable", "bag", "misc"]
EquipSlot = Literal["weapon", "offhand", "head", "chest", "legs", "feet", "neck", "ring", "bag"]
PlayerState = Literal["exploring", "in_combat", "dead"]
Probability = Annotated[float, Field(ge=0.0, le=1.0)]


class SpawnRule(BaseModel):
    monster_id: str
    count: PositiveInt = 1


class ItemSpawnRule(BaseModel):
    item_id: str
    quantity: PositiveInt = 1
    respawn_seconds: PositiveInt | None = None  # None = einmalig


class RoomTemplate(BaseModel):
    id: str
    name: str
    description: str
    exits: dict[Direction, str] = Field(default_factory=dict)
    checkpoint: bool = False  # Betreten setzt den Checkpoint
    safe: bool = False  # keine Überfälle, keine Kämpfe (z. B. Stadt)
    bank: bool = False  # Bankzugang in diesem Raum
    spawns: list[SpawnRule] = Field(default_factory=list)
    items: list[ItemSpawnRule] = Field(default_factory=list)


class Regeneration(BaseModel):
    delay_seconds: NonNegativeInt  # Wartezeit nach dem letzten Kampfkontakt
    hp_per_second: PositiveFloat


class Ambush(BaseModel):
    chance_on_enter: Probability  # beim Betreten des Raums
    chance_per_tick: Probability  # während Spieler im Raum verweilen
    # greift keine Spieler an, deren Level um mehr als diesen Wert über dem Monster-Level liegt
    max_level_difference: NonNegativeInt | None = None


class LootEntry(BaseModel):
    item_id: str
    chance: Probability
    min_qty: PositiveInt = 1
    max_qty: PositiveInt = 1


class RareVariant(BaseModel):
    """On every respawn the monster comes back as ``monster_id`` with this chance."""

    monster_id: str
    chance: float = Field(gt=0, le=1)


class MonsterTemplate(BaseModel):
    id: str
    name: str
    level: PositiveInt = 1  # Grundlage für Ambush.max_level_difference
    max_hp: PositiveInt
    attack: NonNegativeInt
    defense: NonNegativeInt
    xp_reward: NonNegativeInt
    gold_reward: tuple[NonNegativeInt, NonNegativeInt] = (0, 0)
    respawn_seconds: PositiveInt
    regen: Regeneration | None = None  # None = regeneriert nicht
    ambush: Ambush | None = None  # None = greift nie von selbst an
    loot_table: list[LootEntry] = Field(default_factory=list)
    rare_variant: RareVariant | None = None  # seltene Form, gewürfelt bei jedem Respawn


class StatModifier(BaseModel):
    type: Literal["stat"] = "stat"
    stat: Literal["attack", "defense", "max_hp"]
    amount: int


class Heal(BaseModel):
    type: Literal["heal"] = "heal"
    amount: PositiveInt


Effect = Annotated[StatModifier | Heal, Field(discriminator="type")]


class SetBonus(BaseModel):
    pieces: int = Field(ge=2)  # from this many equipped pieces on (bonuses add up)
    effects: list[StatModifier] = Field(min_length=1)


class ItemSet(BaseModel):  # sets.yaml
    id: str
    name: str
    items: list[str] = Field(min_length=2)  # one per equipment slot
    bonuses: list[SetBonus] = Field(min_length=1)


class ItemTemplate(BaseModel):
    id: str
    name: str
    description: str
    kind: ItemKind
    slot: EquipSlot | None = None  # Taschen: "bag"
    bag_slots: PositiveInt | None = None  # nur bei kind = "bag": zusätzliche Inventarplätze
    value: NonNegativeInt = 0  # Basiswert in Gold, Grundlage für Händlerpreise
    sellable: bool = True
    stackable: bool = False
    max_stack: PositiveInt = 1  # > 1 nur bei stackable
    keep_on_death: bool = False  # z. B. Quest-Items, nie verlierbar
    tradable: bool = True  # Quest-Items u. Ä. nicht handelbar
    icon: str | None = None  # Dateiname (ohne .svg) in app/web/static/icons/
    effects: list[Effect] = Field(default_factory=list)
    item_set: ItemSet | None = None  # filled in by the loader from sets.yaml, never in items.yaml


class MerchantOffer(BaseModel):
    item_id: str
    price: PositiveInt | None = None  # None = Item-value * rules.merchant.buy_ratio


class MerchantTemplate(BaseModel):
    id: str
    name: str
    room_id: str
    greeting: str
    sells: list[MerchantOffer] = Field(default_factory=list)  # Sortiment, unbegrenzter Vorrat
    buys: Literal["all", "listed"] = "all"
    buys_kinds: list[ItemKind] = Field(default_factory=list)  # bei "listed": nur diese Item-Arten
    sell_ratio: Probability | None = None  # überschreibt rules.merchant.sell_ratio


# --- quests.yaml ------------------------------------------------------------


class DeliverObjective(BaseModel):
    """Bring items to the quest giver (they are taken on turn-in)."""

    type: Literal["deliver"] = "deliver"
    item_id: str
    quantity: PositiveInt = 1


class KillObjective(BaseModel):
    """Kill monsters; a kill counts for every member of the credited party in the room.
    Rare variants count for their base monster."""

    type: Literal["kill"] = "kill"
    monster_id: str
    count: PositiveInt = 1


Objective = Annotated[DeliverObjective | KillObjective, Field(discriminator="type")]


class RewardItem(BaseModel):
    item_id: str
    quantity: PositiveInt = 1


class QuestReward(BaseModel):
    xp: NonNegativeInt = 0
    gold: NonNegativeInt = 0
    items: list[RewardItem] = Field(default_factory=list)


class QuestTemplate(BaseModel):
    id: str
    name: str
    giver_id: str  # MerchantTemplate.id: accepted and turned in where that merchant stands
    description: str  # the task, as the giver tells it
    completion_text: str  # what the giver says on turn-in
    objective: Objective
    reward: QuestReward = Field(default_factory=QuestReward)
    min_level: PositiveInt = 1
    requires: list[str] = Field(default_factory=list)  # quest ids that must be completed first
    repeatable: bool = False


# --- recipes.yaml -----------------------------------------------------------


class Ingredient(BaseModel):
    item_id: str
    quantity: PositiveInt = 1


class RecipeTemplate(BaseModel):
    """A crafter (a merchant) turns ingredients plus a fee into an item, where they stand."""

    id: str
    crafter_id: str  # MerchantTemplate.id
    item_id: str  # the result
    quantity: PositiveInt = 1
    ingredients: list[Ingredient] = Field(min_length=1)
    gold: NonNegativeInt = 0  # the crafter's fee
    min_level: PositiveInt = 1


# --- rules.yaml -------------------------------------------------------------
# Bewusst ohne Defaults: Alle Balancing-Werte stehen ausschließlich in data/rules.yaml.


class InventoryRules(BaseModel):
    slots: PositiveInt
    bag_slots: PositiveInt


class BankRules(BaseModel):
    slots: PositiveInt


class MerchantRules(BaseModel):
    buy_ratio: PositiveFloat
    sell_ratio: Probability


class TradeRules(BaseModel):
    request_ttl_seconds: PositiveInt
    max_offer_entries: PositiveInt


class DeathRules(BaseModel):
    gold_loss_percent: tuple[NonNegativeInt, NonNegativeInt]
    item_loss_chance: Probability
    equipped_loss_chance: Probability
    max_items_lost: NonNegativeInt

    @model_validator(mode="after")
    def _check_range(self) -> DeathRules:
        lo, hi = self.gold_loss_percent
        if lo > hi or hi > 100:
            raise ValueError(f"gold_loss_percent must satisfy 0 <= min <= max <= 100, got {lo, hi}")
        return self


class CombatRules(BaseModel):
    min_damage: PositiveInt  # Mindestschaden pro Treffer
    damage_variance: NonNegativeInt  # Schaden schwankt um ±damage_variance
    flee_chance: Probability  # Erfolgschance einer Flucht


class XpRules(BaseModel):
    # Gewicht untätiger Gruppenmitglieder im Raum, relativ zum Durchschnittsschaden der Angreifer
    group_idle_share: Probability


class TimingsRules(BaseModel):
    floor_item_ttl_seconds: PositiveInt
    disconnect_grace_seconds: PositiveInt
    ticker_interval_seconds: PositiveInt
    min_action_interval_ms: PositiveInt
    group_invite_ttl_seconds: PositiveInt


class LevelRule(BaseModel):
    level: PositiveInt
    xp_required: NonNegativeInt
    max_hp: PositiveInt
    attack: NonNegativeInt
    defense: NonNegativeInt


class Rules(BaseModel):
    start_room_id: str
    town_room_id: str
    max_characters_per_account: PositiveInt
    max_group_size: PositiveInt
    inventory: InventoryRules
    bank: BankRules
    merchant: MerchantRules
    trade: TradeRules
    death: DeathRules
    combat: CombatRules
    xp: XpRules
    timings: TimingsRules
    levels: list[LevelRule] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_levels(self) -> Rules:
        ordered = sorted(self.levels, key=lambda rule: rule.level)
        if ordered[0].level != 1 or ordered[0].xp_required != 0:
            raise ValueError("levels must start with level 1 at xp_required 0")
        for prev, cur in zip(ordered, ordered[1:], strict=False):
            if cur.level != prev.level + 1:
                raise ValueError(f"levels must be consecutive, gap after level {prev.level}")
            if cur.xp_required <= prev.xp_required:
                raise ValueError(f"xp_required must increase (level {cur.level})")
        return self

    def level_rule(self, level: int) -> LevelRule:
        for rule in self.levels:
            if rule.level == level:
                return rule
        raise KeyError(f"No level rule for level {level}")
