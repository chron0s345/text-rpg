from __future__ import annotations

from dataclasses import dataclass, field

from app.content.schemas import (
    ItemSet,
    ItemTemplate,
    MerchantTemplate,
    MonsterTemplate,
    QuestTemplate,
    RecipeTemplate,
    RoomTemplate,
    Rules,
)


@dataclass(frozen=True)
class ContentRegistry:
    """Immutable in-memory content (templates + rules), loaded once at startup."""

    rules: Rules
    rooms: dict[str, RoomTemplate] = field(default_factory=dict)
    monsters: dict[str, MonsterTemplate] = field(default_factory=dict)
    items: dict[str, ItemTemplate] = field(default_factory=dict)
    merchants: dict[str, MerchantTemplate] = field(default_factory=dict)
    quests: dict[str, QuestTemplate] = field(default_factory=dict)
    recipes: dict[str, RecipeTemplate] = field(default_factory=dict)
    item_sets: dict[str, ItemSet] = field(default_factory=dict)  # pieces carry theirs, too

    def get_room(self, room_id: str) -> RoomTemplate:
        if room_id not in self.rooms:
            raise KeyError(f"Room '{room_id}' not found in registry")
        return self.rooms[room_id]

    def get_monster(self, monster_id: str) -> MonsterTemplate:
        if monster_id not in self.monsters:
            raise KeyError(f"Monster '{monster_id}' not found in registry")
        return self.monsters[monster_id]

    def base_monster_id(self, monster_id: str) -> str:
        """The monster a rare variant belongs to (itself if it is no variant): spawn rules,
        the seed and respawns all count variants as their base."""
        for template in self.monsters.values():
            if template.rare_variant is not None and template.rare_variant.monster_id == monster_id:
                return template.id
        return monster_id

    def get_quest(self, quest_id: str) -> QuestTemplate:
        if quest_id not in self.quests:
            raise KeyError(f"Quest '{quest_id}' not found in registry")
        return self.quests[quest_id]

    def recipes_in_room(self, room_id: str) -> list[RecipeTemplate]:
        """Recipes of the crafters standing in this room, in file order."""
        return [
            recipe
            for recipe in self.recipes.values()
            if self.merchants[recipe.crafter_id].room_id == room_id
        ]

    def get_item(self, item_id: str) -> ItemTemplate:
        if item_id not in self.items:
            raise KeyError(f"Item '{item_id}' not found in registry")
        return self.items[item_id]

    def get_merchant(self, merchant_id: str) -> MerchantTemplate:
        if merchant_id not in self.merchants:
            raise KeyError(f"Merchant '{merchant_id}' not found in registry")
        return self.merchants[merchant_id]

    def merchants_in_room(self, room_id: str) -> list[MerchantTemplate]:
        return [m for m in self.merchants.values() if m.room_id == room_id]
