"""Sizing up a monster: stats, a fight estimate and the loot it may drop."""

import random
from uuid import uuid4

from app.content.registry import ContentRegistry
from app.content.schemas import Rules
from app.engine.actions import resolve_inspect_monster
from app.engine.state import ActionSnapshot
from tests.engine.factories import NOW, actor_texts, make_monster, make_player

RNG = random.Random(1)


def test_a_monster_in_the_room(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()  # attack 5, defense 2, 20 HP
    actor.kills = {"dummy": 4}
    dummy = make_monster(hp=10)  # 10 HP, attack 5, defense 0, drops goblin ears
    result = resolve_inspect_monster(
        ActionSnapshot(actor=actor, monsters=[dummy]), content, rules, NOW, RNG, dummy.id
    )
    [text] = actor_texts(result)
    assert text.splitlines() == [
        "Übungspuppe – Stufe 1, 10/10 HP, Angriff 5, Verteidigung 0.",
        "Du triffst für etwa 5, Übungspuppe für etwa 3. Nach etwa 2 Treffern liegt der Gegner; "
        "das kostet dich rund 3 HP (20 hast du): leicht.",
        "Beute: Goblinohr (häufig).",
        "Von dir besiegt: 4×.",
    ]
    assert result.changes.monsters == []  # looking changes nothing


def test_a_troll_is_deadly_and_bosses_say_so(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    troll = make_monster("troll", hp=10)
    [text] = actor_texts(
        resolve_inspect_monster(
            ActionSnapshot(actor=actor, monsters=[troll]), content, rules, NOW, RNG, troll.id
        )
    )
    assert text.splitlines()[1].endswith("tödlich – allein kaum zu schaffen.")
    boss = make_monster("ignaros", hp=520)
    actor.level = 13
    [text] = actor_texts(
        resolve_inspect_monster(
            ActionSnapshot(actor=actor, monsters=[boss]), content, rules, NOW, RNG, boss.id
        )
    )
    assert text.startswith("Glutfürst Ignaros – Stufe 13 (Boss), 520/520 HP")
    assert "Krone des Glutfürsten (häufig)" in text


def test_only_living_monsters_here(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    dead = make_monster(hp=0, alive=False)
    for snapshot, target in [
        (ActionSnapshot(actor=actor, monsters=[dead]), dead.id),
        (ActionSnapshot(actor=actor), uuid4()),
    ]:
        result = resolve_inspect_monster(snapshot, content, rules, NOW, RNG, target)
        assert actor_texts(result) == ["Diesen Gegner gibt es hier nicht."]
