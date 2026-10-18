"""Travel: paid rides between safe rooms (ARCHITECTURE.md, "Reisen")."""

import random

import pytest

from app.content.loader import ContentValidationError, validate_travel
from app.content.registry import ContentRegistry
from app.content.schemas import Rules, TravelRoute
from app.engine.movement import resolve_travel
from app.engine.state import ActionSnapshot
from tests.engine.factories import NOW, actor_texts, make_player, player_update

RNG = random.Random(1)


def travel(actor_room: str, target: str, content: ContentRegistry, rules: Rules, **fields: int):  # type: ignore[no-untyped-def]
    actor = make_player(room=actor_room, gold=fields.get("gold", 100))
    actor.level = fields.get("level", 15)
    return actor, resolve_travel(ActionSnapshot(actor=actor), content, rules, NOW, RNG, target)


def test_a_ride_costs_gold_and_arrives_like_walking(content: ContentRegistry, rules: Rules) -> None:
    actor, result = travel("town_square", "fishing_village", content, rules, gold=15)
    assert actor_texts(result) == [
        "Du nimmst die Postkutsche nach Fischerdorf Möwenstein für 10 Gold.",
        "Du fühlst dich mit diesem Ort verbunden.",
    ]
    update = player_update(result, actor.id)
    assert (update["gold"], update["current_room_id"], update["checkpoint_room_id"]) == (
        5,
        "fishing_village",
        "fishing_village",
    )
    texts = {e.audience.room_id: e.text for e in result.events if e.audience.kind == "room"}
    assert texts == {"town_square": "Anna reist ab.", "fishing_village": "Anna kommt an."}


def test_routes_level_and_gold(content: ContentRegistry, rules: Rules) -> None:
    def refused(room: str, target: str, **fields: int) -> list[str]:
        return actor_texts(travel(room, target, content, rules, **fields)[1])

    assert refused("town_square", "last_refuge") == ["Von hier aus fährt nichts dorthin."]
    assert refused("dark_forest", "town_square") == ["Von hier aus fährt nichts dorthin."]
    assert refused("town_square", "crater_camp", level=10) == [
        "Die Fahrt nach Rastplatz am Kraterrand gibt es erst ab Stufe 11."
    ]
    assert refused("town_square", "crater_camp", gold=59) == [
        "Die Fahrt kostet 60 Gold, du hast nur 59."
    ]
    actor = make_player(room="town_square", gold=100)
    actor.level = 15
    actor.state = "in_combat"
    result = resolve_travel(ActionSnapshot(actor=actor), content, rules, NOW, RNG, "elf_hall")
    assert result.changes.players == []


def test_the_shadow_path_only_leads_home(content: ContentRegistry, rules: Rules) -> None:
    _, result = travel("last_refuge", "town_square", content, rules, level=1)
    assert (
        actor_texts(result)[0]
        == "Du nimmst den Schattenpfad nach Marktplatz von Oakhaven für 80 Gold."
    )


def test_routes_only_between_safe_rooms(content: ContentRegistry) -> None:
    rooms = dict(content.rooms)
    square = rooms["town_square"]
    for target, message in [
        ("dark_forest", "unsafe 'dark_forest'"),
        ("nowhere", "unknown 'nowhere'"),
        ("town_square", "route to itself"),
    ]:
        rooms["town_square"] = square.model_copy(
            update={"travel": [TravelRoute(room_id=target, gold=1)]}
        )
        with pytest.raises(ContentValidationError, match=message):
            validate_travel(rooms)
    rooms["town_square"] = square
    forest = rooms["dark_forest"]
    rooms["dark_forest"] = forest.model_copy(
        update={"travel": [TravelRoute(room_id="town_square", gold=1)]}
    )
    with pytest.raises(ContentValidationError, match="not safe"):
        validate_travel(rooms)
