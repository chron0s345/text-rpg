"""Resting: full HP in safe rooms, for free; nowhere else."""

import random
from uuid import uuid4

from app.content.registry import ContentRegistry
from app.content.schemas import Rules
from app.engine.actions import resolve_rest
from app.engine.state import ActionSnapshot, InventoryItemState
from tests.engine.factories import NOW, actor_texts, make_player, player_update

RNG = random.Random(1)


def test_resting_in_a_safe_room_heals_fully(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(hp=3, room="town_square", gold=7)
    actor.inventory = [  # max HP from equipment counts
        InventoryItemState(id=uuid4(), item_id="amulet", quantity=1, equipped_slot="neck")
    ]
    result = resolve_rest(ActionSnapshot(actor=actor), content, rules, NOW, RNG)
    assert actor_texts(result) == [
        "Du rastest in Marktplatz von Oakhaven und kommst wieder zu Kräften."
    ]
    update = player_update(result, actor.id)
    assert update["hp"] == 25 and "gold" not in update  # 20 + amulet 5, nothing to pay


def test_no_rest_in_the_wild_in_combat_or_at_full_hp(
    content: ContentRegistry, rules: Rules
) -> None:
    def rest(**fields: object) -> list[str]:
        actor = make_player(**fields)  # type: ignore[arg-type]
        return actor_texts(resolve_rest(ActionSnapshot(actor=actor), content, rules, NOW, RNG))

    assert rest(hp=3) == ["Rasten kannst du nur an einem sicheren Ort."]  # dark forest
    assert rest(hp=20, room="town_square") == ["Du bist schon bei vollen Kräften."]
    fighting = make_player(hp=3, room="town_square")
    fighting.state = "in_combat"
    result = resolve_rest(ActionSnapshot(actor=fighting), content, rules, NOW, RNG)
    assert result.changes.players == []
