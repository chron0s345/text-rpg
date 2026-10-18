"""Looking at another character in the room: level, title, stats, gear, achievements."""

import random
from uuid import uuid4

from app.content.registry import ContentRegistry
from app.content.schemas import Rules
from app.engine.actions import resolve_inspect_player
from app.engine.state import ActionSnapshot, InventoryItemState
from tests.engine.factories import NOW, actor_texts, make_player

RNG = random.Random(1)


def test_inspecting_someone_in_the_room(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player("Anna")
    other = make_player("Bert")
    other.level = 7
    other.achievements = ["kill_ignaros", "gone"]
    other.title = "kill_ignaros"
    other.kills = {"goblin": 3, "golden_goblin": 1, "wolf": 2}
    other.inventory = [
        InventoryItemState(id=uuid4(), item_id="short_sword", quantity=1, equipped_slot="weapon"),
        InventoryItemState(id=uuid4(), item_id="amulet", quantity=1, equipped_slot="neck"),
        InventoryItemState(id=uuid4(), item_id="bread", quantity=2),
    ]
    snapshot = ActionSnapshot(actor=actor, room_players=[other])
    result = resolve_inspect_player(snapshot, content, rules, NOW, RNG, other.id)

    [text] = actor_texts(result)
    assert text.splitlines() == [
        "Bert Feuerbändiger – Stufe 7.",
        "Angriff 9 · Verteidigung 2 · max. HP 25.",
        "Trägt: Waffe: Kurzschwert; Amulett: Amulett.",
        f"Erfolge: 1/{len(content.achievements)} · Bestiarium: 2 Arten.",
    ]
    [noticed] = [e for e in result.events if e.audience.kind == "players"]
    assert noticed.text == "Anna mustert dich."


def test_only_people_in_the_room(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    result = resolve_inspect_player(ActionSnapshot(actor=actor), content, rules, NOW, RNG, uuid4())
    assert actor_texts(result) == ["Diese Person ist nicht hier."]
    me = resolve_inspect_player(ActionSnapshot(actor=actor), content, rules, NOW, RNG, actor.id)
    assert actor_texts(me)[0].startswith("Anna – Stufe 1.")
    assert not [e for e in me.events if e.audience.kind == "players"]
