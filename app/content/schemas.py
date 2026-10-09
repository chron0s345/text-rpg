from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, NonNegativeInt, PositiveFloat, PositiveInt

Direction = Literal["north", "south", "east", "west", "up", "down"]
ItemKind = Literal["weapon", "armor", "consumable", "bag", "misc"]
EquipSlot = Literal["weapon", "armor", "head", "chest", "legs", "feet", "bag"]
PlayerState = Literal["exploring", "in_combat", "dead"]


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
    chance_on_enter: float = Field(ge=0.0, le=1.0)  # beim Betreten des Raums
    chance_per_tick: float = Field(ge=0.0, le=1.0)  # während Spieler im Raum verweilen
    max_level_difference: int | None = None  # greift keine deutlich stärkeren Spieler an


class LootEntry(BaseModel):
    item_id: str
    chance: float = Field(ge=0.0, le=1.0)
    min_qty: PositiveInt = 1
    max_qty: PositiveInt = 1


class MonsterTemplate(BaseModel):
    id: str
    name: str
    max_hp: PositiveInt
    attack: NonNegativeInt
    defense: NonNegativeInt
    xp_reward: NonNegativeInt
    gold_reward: tuple[NonNegativeInt, NonNegativeInt] = (0, 0)
    respawn_seconds: PositiveInt
    regen: Regeneration | None = None  # None = regeneriert nicht
    ambush: Ambush | None = None  # None = greift nie von selbst an
    loot_table: list[LootEntry] = Field(default_factory=list)


class StatModifier(BaseModel):
    type: Literal["stat"] = "stat"
    stat: Literal["attack", "defense", "max_hp"]
    amount: int


class Heal(BaseModel):
    type: Literal["heal"] = "heal"
    amount: PositiveInt


Effect = Annotated[StatModifier | Heal, Field(discriminator="type")]


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
    icon: str | None = None  # Icon Identifier oder Emoji für UI-Darstellung
    effects: list[Effect] = Field(default_factory=list)


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
    sell_ratio: float | None = None  # überschreibt rules.merchant.sell_ratio


class InventoryRules(BaseModel):
    slots: PositiveInt = 20
    bag_slots: PositiveInt = 4


class BankRules(BaseModel):
    slots: PositiveInt = 40


class MerchantRules(BaseModel):
    buy_ratio: float = 1.0
    sell_ratio: float = 0.25


class TradeRules(BaseModel):
    request_ttl_seconds: PositiveInt = 60
    max_offer_entries: PositiveInt = 8


class DeathRules(BaseModel):
    gold_loss_percent: tuple[NonNegativeInt, NonNegativeInt] = (5, 20)
    item_loss_chance: float = Field(default=0.15, ge=0.0, le=1.0)
    equipped_loss_chance: float = Field(default=0.05, ge=0.0, le=1.0)
    max_items_lost: PositiveInt = 3


class TimingsRules(BaseModel):
    floor_item_ttl_seconds: PositiveInt = 600
    disconnect_grace_seconds: PositiveInt = 60
    ticker_interval_seconds: PositiveInt = 5
    min_action_interval_ms: PositiveInt = 800


class LevelRule(BaseModel):
    level: PositiveInt
    xp_required: NonNegativeInt
    max_hp: PositiveInt
    attack: NonNegativeInt
    defense: NonNegativeInt


class Rules(BaseModel):
    start_room_id: str = "town_square"
    town_room_id: str = "town_square"
    max_characters_per_account: PositiveInt = 5
    max_group_size: PositiveInt = 4
    inventory: InventoryRules = Field(default_factory=InventoryRules)
    bank: BankRules = Field(default_factory=BankRules)
    merchant: MerchantRules = Field(default_factory=MerchantRules)
    trade: TradeRules = Field(default_factory=TradeRules)
    death: DeathRules = Field(default_factory=DeathRules)
    timings: TimingsRules = Field(default_factory=TimingsRules)
    levels: list[LevelRule] = Field(default_factory=list)
