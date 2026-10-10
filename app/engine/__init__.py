"""Pure game logic. Actions take ``(snapshot, content, rules, now, rng, ...)`` and return an
``ActionResult``; the service layer loads/locks state, applies the changes and publishes events."""

from app.engine.actions import resolve_look, resolve_respawn, resolve_say
from app.engine.ambush import resolve_idle_ambush
from app.engine.combat import calculate_damage, resolve_attack, resolve_flee
from app.engine.death import DeathLossResult, resolve_death_losses
from app.engine.events import (
    ActionResult,
    ActorAudience,
    Audience,
    GameEvent,
    GlobalAudience,
    GroupAudience,
    PlayersAudience,
    RoomAudience,
    StateChanges,
)
from app.engine.inventory import (
    add_item_to_inventory,
    calculate_inventory_capacity,
    can_add_quantity,
    can_unequip_bag,
    can_unequip_item,
    get_occupied_slots,
)
from app.engine.loot import (
    RolledLootItem,
    distribute_gold,
    distribute_xp,
    roll_monster_gold,
    roll_monster_loot,
)
from app.engine.movement import resolve_move
from app.engine.permissions import ALLOWED_STATES, check_state, is_allowed
from app.engine.progression import LevelUpResult, calculate_level_progression
from app.engine.regeneration import calculate_current_monster_hp
from app.engine.state import (
    ActionSnapshot,
    GroupSnapshot,
    InventoryItemState,
    MonsterSnapshot,
    ParticipationSnapshot,
    PlayerSnapshot,
)
from app.engine.stats import EffectiveStats, calculate_effective_stats

__all__ = [
    "ALLOWED_STATES",
    "ActionResult",
    "ActionSnapshot",
    "ActorAudience",
    "Audience",
    "DeathLossResult",
    "EffectiveStats",
    "GameEvent",
    "GlobalAudience",
    "GroupAudience",
    "GroupSnapshot",
    "InventoryItemState",
    "LevelUpResult",
    "MonsterSnapshot",
    "ParticipationSnapshot",
    "PlayerSnapshot",
    "PlayersAudience",
    "RolledLootItem",
    "RoomAudience",
    "StateChanges",
    "add_item_to_inventory",
    "calculate_current_monster_hp",
    "calculate_damage",
    "calculate_effective_stats",
    "calculate_inventory_capacity",
    "calculate_level_progression",
    "can_add_quantity",
    "can_unequip_bag",
    "can_unequip_item",
    "check_state",
    "distribute_gold",
    "distribute_xp",
    "get_occupied_slots",
    "is_allowed",
    "resolve_attack",
    "resolve_death_losses",
    "resolve_flee",
    "resolve_idle_ambush",
    "resolve_look",
    "resolve_move",
    "resolve_respawn",
    "resolve_say",
    "roll_monster_gold",
    "roll_monster_loot",
]
