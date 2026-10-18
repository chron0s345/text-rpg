"""Consumable boosts (ARCHITECTURE.md, "Stärkungstränke"): a ``buff`` effect adds to a stat for
the next few kills the character gets credit for. Drinking the same potion again starts it
over; different potions add up. Death ends them all.

Counting fights instead of minutes keeps the engine free of clocks: a boost lasts as long as
it is useful, however long the player pauses.
"""

from __future__ import annotations

from app.content.describe import STAT_NAMES
from app.content.registry import ContentRegistry
from app.content.schemas import Buff, ItemTemplate
from app.engine.events import ResultBuilder
from app.engine.group_status import report_hp
from app.engine.state import ActiveBuff, PlayerSnapshot
from app.engine.stats import stats_of


def fights_text(fights: int) -> str:
    return "1 Sieg" if fights == 1 else f"{fights} Siege"


def buff_text(buffs: list[ActiveBuff] | list[Buff]) -> str:
    """E.g. "+3 Angriff, +2 Verteidigung"."""
    return ", ".join(f"+{b.amount} {STAT_NAMES[b.stat]}" for b in buffs)


def apply_buffs(builder: ResultBuilder, player: PlayerSnapshot, item: ItemTemplate) -> str:
    """Starts (or restarts) the boosts of ``item``; returns the log text, "" if it has none."""
    effects = [e for e in item.effects if isinstance(e, Buff)]
    if not effects:
        return ""
    fresh = [
        ActiveBuff(item_id=item.id, stat=e.stat, amount=e.amount, fights_left=e.fights)
        for e in effects
    ]
    player.buffs = [b for b in player.buffs if b.item_id != item.id] + fresh
    builder.update_player(player.id, buffs=player.buffs)
    longest = max(e.fights for e in effects)
    return f"{buff_text(effects)} für {fights_text(longest)}."


def tick_buffs(builder: ResultBuilder, player: PlayerSnapshot, content: ContentRegistry) -> None:
    """One kill credit: every boost loses a fight; spent ones end with a log line (and HP that
    no longer fit the lower maximum are cut)."""
    if not player.buffs:
        return
    ticked = [b.model_copy(update={"fights_left": b.fights_left - 1}) for b in player.buffs]
    spent = {b.item_id for b in ticked if b.fights_left <= 0}
    player.buffs = [b for b in ticked if b.fights_left > 0]
    builder.update_player(player.id, buffs=player.buffs)
    for item_id in sorted(spent):
        item = content.items.get(item_id)
        name = item.name if item is not None else item_id
        builder.to_player(player.id, "buff.ended", f"Die Wirkung von {name} lässt nach.")
    _cap_hp(builder, player, content)


def clear_buffs(builder: ResultBuilder, player: PlayerSnapshot) -> None:
    """Death ends every boost."""
    if player.buffs:
        player.buffs = []
        builder.update_player(player.id, buffs=[])


def _cap_hp(builder: ResultBuilder, player: PlayerSnapshot, content: ContentRegistry) -> None:
    max_hp = stats_of(player, content.items).max_hp
    if player.hp > max_hp:
        player.hp = max_hp
        builder.update_player(player.id, hp=max_hp)
        report_hp(builder, player)
