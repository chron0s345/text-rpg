"""Quests in the engine: accepting, turning in, abandoning, kill progress from resolve_kill.

Test quests (tests/engine/conftest.py): "tails" (deliver 3 goblin ears to Elira), "hunt"
(kill 2 "plain", requires "tails", repeatable), "veteran" (Brandt, from level 5).
"""

import random

from app.content.registry import ContentRegistry
from app.content.schemas import Rules
from app.engine.combat import resolve_attack
from app.engine.quests import (
    quests_offered_here,
    resolve_quest_abandon,
    resolve_quest_accept,
    resolve_quest_turn_in,
)
from app.engine.state import ActionSnapshot, PlayerSnapshot, QuestState
from tests.engine.factories import NOW, actor_texts, make_monster, make_player, player_update
from tests.engine.test_inventory_actions import fill, give

RNG = random.Random(3)
MARKET = "town_market"  # where Elira (giver of "tails" and "hunt") stands


def at_market(**fields: object) -> PlayerSnapshot:
    return make_player(room=MARKET, **fields)  # type: ignore[arg-type]


def done(quest_id: str, times: int = 1) -> QuestState:
    return QuestState(quest_id=quest_id, status="completed", times_completed=times)


def test_accept_only_at_the_giver_and_only_once(content: ContentRegistry, rules: Rules) -> None:
    elsewhere = make_player()
    assert actor_texts(
        resolve_quest_accept(ActionSnapshot(actor=elsewhere), content, rules, NOW, RNG, "tails")
    ) == ["Alchemistin Elira ist nicht hier."]

    actor = at_market()
    result = resolve_quest_accept(ActionSnapshot(actor=actor), content, rules, NOW, RNG, "tails")

    [line] = actor_texts(result)
    assert line.startswith("Auftrag angenommen: Rattenplage. Alchemistin Elira: „Bring mir")
    [upsert] = result.changes.quests_upsert
    assert (upsert.quest_id, upsert.status, upsert.progress) == ("tails", "active", 0)
    again = resolve_quest_accept(ActionSnapshot(actor=actor), content, rules, NOW, RNG, "tails")
    assert actor_texts(again) == ["Diesen Auftrag hast du schon."]


def test_prerequisites_levels_and_offers(content: ContentRegistry, rules: Rules) -> None:
    actor = at_market()
    offered = {q.id for q in quests_offered_here(actor, content)}
    assert "tails" in offered and "hunt" not in offered  # hunt needs tails first
    assert actor_texts(
        resolve_quest_accept(ActionSnapshot(actor=actor), content, rules, NOW, RNG, "hunt")
    ) == ["Erledige zuerst „Rattenplage“."]

    actor.quests = [done("tails")]
    assert "hunt" in {q.id for q in quests_offered_here(actor, content)}
    assert "tails" not in {q.id for q in quests_offered_here(actor, content)}  # not repeatable

    smith = make_player(room="town_smithy")
    assert actor_texts(
        resolve_quest_accept(ActionSnapshot(actor=smith), content, rules, NOW, RNG, "veteran")
    ) == ["Dafür bist du noch nicht erfahren genug (ab Stufe 5)."]
    assert actor_texts(
        resolve_quest_accept(ActionSnapshot(actor=smith), content, rules, NOW, RNG, "nope")
    ) == ["Diesen Auftrag gibt es nicht."]


def test_delivery_takes_the_items_and_pays(content: ContentRegistry, rules: Rules) -> None:
    actor = at_market(gold=5)
    actor.quests = [QuestState(quest_id="tails", status="active")]
    give(actor, "goblin_ear", 2)
    snapshot = ActionSnapshot(actor=actor)

    not_yet = resolve_quest_turn_in(snapshot, content, rules, NOW, RNG, "tails")
    assert actor_texts(not_yet) == ["Rattenplage: erst 2/3."]

    give(actor, "goblin_ear", 2)  # two stacks: 2 + 2
    result = resolve_quest_turn_in(snapshot, content, rules, NOW, RNG, "tails")

    texts = actor_texts(result)
    assert texts[0] == "Auftrag erledigt: Rattenplage. Alchemistin Elira: „Danke!“"
    assert texts[1] == "Belohnung: 12 Gold, Heiltrank."
    assert any("40 Erfahrung" in t for t in texts)
    ears = sum(i.quantity for i in actor.inventory if i.item_id == "goblin_ear")
    assert ears == 1  # exactly three taken
    assert player_update(result, actor.id)["gold"] == 17
    assert any(i.item_id == "healing_potion" for i in actor.inventory)
    [upsert] = result.changes.quests_upsert
    assert (upsert.status, upsert.times_completed) == ("completed", 1)


def test_equipped_or_offered_items_do_not_count(content: ContentRegistry, rules: Rules) -> None:
    actor = at_market()
    actor.quests = [QuestState(quest_id="tails", status="active")]
    offered = give(actor, "goblin_ear", 3)
    actor.locked_item_ids = [offered.id]  # in an open trade
    result = resolve_quest_turn_in(ActionSnapshot(actor=actor), content, rules, NOW, RNG, "tails")
    assert actor_texts(result) == ["Rattenplage: erst 0/3."]


def test_reward_needs_room_in_the_backpack(content: ContentRegistry, rules: Rules) -> None:
    actor = at_market()
    actor.quests = [QuestState(quest_id="tails", status="active")]
    give(actor, "goblin_ear", 3)
    fill(actor, rules)  # full, and the ears stay (stack of 3 is taken completely -> one slot free)
    give(actor, "bread")  # fills the freed slot again
    result = resolve_quest_turn_in(ActionSnapshot(actor=actor), content, rules, NOW, RNG, "tails")
    assert actor_texts(result) == ["Für die Belohnung ist kein Platz im Inventar."]
    assert result.changes.quests_upsert == []


def test_kills_count_for_the_party_and_rare_variants(
    content: ContentRegistry, rules: Rules
) -> None:
    actor = make_player()
    actor.quests = [QuestState(quest_id="hunt", status="active", progress=1)]
    golden = make_monster("golden", hp=1)  # the rare form of "plain" counts too
    snapshot = ActionSnapshot(actor=actor, monsters=[golden])

    result = resolve_attack(snapshot, content, rules, NOW, RNG, golden.id)

    assert "Puppenjagd: 2/2 – zurück zu Alchemistin Elira!" in actor_texts(result)
    [upsert] = result.changes.quests_upsert
    assert (upsert.quest_id, upsert.progress) == ("hunt", 2)


def test_kills_beyond_the_count_and_other_monsters_change_nothing(
    content: ContentRegistry, rules: Rules
) -> None:
    actor = make_player()
    actor.quests = [QuestState(quest_id="hunt", status="active", progress=2)]
    for monster_id in ("plain", "dummy"):
        monster = make_monster(monster_id, hp=1)
        result = resolve_attack(
            ActionSnapshot(actor=actor, monsters=[monster]), content, rules, NOW, RNG, monster.id
        )
        assert result.changes.quests_upsert == []


def test_repeatable_quest_and_abandoning(content: ContentRegistry, rules: Rules) -> None:
    actor = at_market()
    actor.quests = [done("tails"), done("hunt", times=2)]
    snapshot = ActionSnapshot(actor=actor)

    accepted = resolve_quest_accept(snapshot, content, rules, NOW, RNG, "hunt")
    [upsert] = accepted.changes.quests_upsert
    assert (upsert.status, upsert.times_completed) == ("active", 2)

    abandoned = resolve_quest_abandon(snapshot, content, rules, NOW, RNG, "hunt")
    assert actor_texts(abandoned) == ["Auftrag abgebrochen: Puppenjagd."]
    [restored] = abandoned.changes.quests_upsert
    assert (restored.status, restored.times_completed) == ("completed", 2)

    first_time = at_market()
    first_time.quests = [QuestState(quest_id="tails", status="active")]
    dropped = resolve_quest_abandon(
        ActionSnapshot(actor=first_time), content, rules, NOW, RNG, "tails"
    )
    [deleted] = dropped.changes.quests_delete
    assert deleted.quest_id == "tails" and first_time.quests == []
