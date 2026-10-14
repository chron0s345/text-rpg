"""Which action is allowed in which character state (ARCHITECTURE.md, "Charakterzustände").

Location-dependent conditions (safe room, bank room, merchant present, ...) are checked by the
action itself; this table only covers the character state.
"""

from __future__ import annotations

from typing import Final

from app.content.schemas import PlayerState
from app.engine.events import ActionResult, rejected
from app.engine.state import PlayerSnapshot

_ALIVE: Final = frozenset({"exploring", "in_combat"})
_ALL: Final = frozenset({"exploring", "in_combat", "dead"})
_EXPLORING: Final = frozenset({"exploring"})

ALLOWED_STATES: Final[dict[str, frozenset[str]]] = {
    "move": _EXPLORING,
    "attack": _ALIVE,
    "flee": frozenset({"in_combat"}),
    "use_item": _ALIVE,
    "equip": _EXPLORING,
    "unequip": _EXPLORING,
    "pick_up": _EXPLORING,
    "drop": _EXPLORING,
    "say": _ALIVE,
    "group_say": _ALL,  # the dead may still talk to their group and whisper
    "whisper": _ALL,
    "group_invite": _ALIVE,
    "group_accept": _ALIVE,
    "group_decline": _ALL,
    "group_leave": _ALL,
    "group_kick": _EXPLORING,
    "trade_request": _EXPLORING,
    "trade_accept": _EXPLORING,
    "trade_offer_item": _EXPLORING,
    "trade_offer_gold": _EXPLORING,
    "trade_remove_item": _EXPLORING,
    "trade_confirm": _EXPLORING,
    "trade_cancel": _EXPLORING,
    "bank_deposit": _EXPLORING,
    "bank_withdraw": _EXPLORING,
    "buy": _EXPLORING,
    "sell": _EXPLORING,
    "respawn": frozenset({"dead"}),
    "logout": _ALL,
    "look": _ALL,
    "inventory": _ALL,
    "inspect": _ALL,
}

_DENIED_TEXT: Final[dict[PlayerState, str]] = {
    "dead": "Du bist tot.",
    "in_combat": "Das geht nicht im Kampf.",
    "exploring": "Du bist in keinem Kampf.",
}


def is_allowed(action_type: str, state: PlayerState) -> bool:
    return state in ALLOWED_STATES[action_type]


def check_state(action_type: str, player: PlayerSnapshot) -> ActionResult | None:
    """``None`` if allowed, otherwise the rejection (log message, no HTTP error)."""
    if is_allowed(action_type, player.state):
        return None
    if action_type == "respawn":
        return rejected("Du lebst noch.")
    return rejected(_DENIED_TEXT[player.state])
