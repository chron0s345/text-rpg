"""Animations: which log lines carry an effect for the game page (app/web/effects.py)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.api.stream import encode_game_event
from app.engine.events import ActorAudience, GameEvent, PlayersAudience, RoomAudience
from app.web.effects import Effect, effect_of
from app.web.render import log_line


def event(kind: str, audience: Any = None, **data: Any) -> GameEvent:
    return GameEvent(kind=kind, audience=audience or ActorAudience(), text="…", data=data)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (event("combat.hit", damage=7), Effect("strike", 7)),
        (event("combat.hit_taken", damage=3, crushing_blow=False), Effect("hurt", 3)),
        (event("combat.hit_taken", damage=9, crushing_blow=True), Effect("crushed", 9)),
        (event("combat.drain", healed=2), Effect("drain", 2)),
        (event("inventory.used", healed=12), Effect("heal", 12)),
        (event("inventory.used", healed=0), None),  # a buff potion: nothing healed
        (event("skill.heal", healed=8), Effect("heal", 8)),
        (event("loot.gold", PlayersAudience(player_ids=[uuid4()]), amount=5), Effect("gold", 5)),
        (event("player.level_up", level=3), Effect("level")),
        (event("achievement.earned"), Effect("achievement")),
        (event("chat.say"), None),
        (event("combat.hit", RoomAudience(room_id="r"), damage=7), None),  # someone else's hit
    ],
)
def test_effect_of(given: GameEvent, expected: Effect | None) -> None:
    assert effect_of(given) == expected


def test_log_lines_carry_the_effect() -> None:
    assert log_line("combat.hit", "Du triffst.", Effect("strike", 7)) == (
        '<li class="log-combat-hit" data-fx="strike" data-amount="7">Du triffst.</li>'
    )
    assert log_line("monster.killed", "Besiegt.", Effect("kill")) == (
        '<li class="log-monster-killed" data-fx="kill">Besiegt.</li>'
    )
    assert log_line("move.enter", "Hier.") == '<li class="log-move-enter">Hier.</li>'


def test_the_stream_sends_personal_effects_only() -> None:
    mine = encode_game_event(
        event("combat.hit_taken", PlayersAudience(player_ids=[uuid4()]), damage=4)
    )
    assert 'data-fx="hurt" data-amount="4"' in mine
    theirs = encode_game_event(event("combat.hit_taken", RoomAudience(room_id="r"), damage=4))
    assert "data-fx" not in theirs
