"""Which log lines play an animation on the game page (``static/app.js`` plays them).

Only lines about the player themselves (actor and personal audiences) get one: someone else's
hit in the same room must not shake your screen. The effect travels as ``data-fx`` on the log
line, a number to float (damage, healing, gold) as ``data-amount``. Lines reloaded from the
stored log carry none, so a page reload replays nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.engine.events import GameEvent

PERSONAL: Final = frozenset({"actor", "players"})

# event kind -> (effect, the data key holding the number to float, if any)
EFFECTS: Final[dict[str, tuple[str, str | None]]] = {
    "combat.hit": ("strike", "damage"),
    "combat.hit_taken": ("hurt", "damage"),
    "combat.drain": ("drain", "healed"),
    "ambush": ("ambush", None),
    "monster.killed": ("kill", None),
    "player.level_up": ("level", None),
    "achievement.earned": ("achievement", None),
    "quest.completed": ("quest", None),
    "craft.done": ("craft", None),
    "player.died": ("death", None),
    "inventory.used": ("heal", "healed"),
    "skill.heal": ("heal", "healed"),
    "loot.gold": ("gold", "amount"),
}


@dataclass(frozen=True)
class Effect:
    name: str
    amount: int | None = None


def effect_of(event: GameEvent) -> Effect | None:
    if event.audience.kind not in PERSONAL or event.kind not in EFFECTS:
        return None
    name, key = EFFECTS[event.kind]
    amount = event.data.get(key) if key is not None else None
    if event.kind == "combat.hit_taken" and event.data.get("crushing_blow"):
        name = "crushed"
    if event.kind == "inventory.used" and not amount:
        return None  # a buff potion or food at full health: nothing to show
    return Effect(name, amount if isinstance(amount, int) and amount > 0 else None)
