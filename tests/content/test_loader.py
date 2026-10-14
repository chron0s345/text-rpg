from pathlib import Path

import pytest

from app.content.loader import ContentValidationError, load_content, validate_content
from app.content.schemas import (
    ItemTemplate,
    MerchantOffer,
    MerchantTemplate,
    MonsterTemplate,
    RoomTemplate,
    Rules,
    SpawnRule,
)


def test_load_real_content_data() -> None:
    data_dir = Path("data")
    registry = load_content(data_dir)
    assert "town_square" in registry.rooms
    assert "town_bank" in registry.rooms
    assert registry.rooms["town_bank"].bank is True
    assert "goblin" in registry.monsters
    assert "healing_potion" in registry.items
    assert registry.items["healing_potion"].icon == "potion"
    assert registry.items["rusty_dagger"].icon == "dagger"
    assert "alchemist" in registry.merchants
    assert registry.rules.start_room_id == "town_square"


def test_missing_bank_room(rules: Rules) -> None:
    rooms = {
        "town_square": RoomTemplate(
            id="town_square",
            name="Town",
            description="",
            safe=True,
            checkpoint=True,
            bank=False,
        )
    }
    with pytest.raises(ContentValidationError, match="No room with bank: true"):
        validate_content(rooms, {}, {}, {}, rules)


def test_safe_room_with_spawns(rules: Rules) -> None:
    rooms = {
        "town_square": RoomTemplate(
            id="town_square",
            name="Town",
            description="",
            safe=True,
            checkpoint=True,
            bank=True,
            spawns=[SpawnRule(monster_id="goblin", count=1)],
        )
    }
    monsters = {
        "goblin": MonsterTemplate(
            id="goblin",
            name="Goblin",
            max_hp=10,
            attack=2,
            defense=1,
            xp_reward=10,
            respawn_seconds=30,
        )
    }
    with pytest.raises(ContentValidationError, match="cannot have monster spawns"):
        validate_content(rooms, monsters, {}, {}, rules)


def test_infinite_money_loop_detection(rules: Rules) -> None:
    rooms = {
        "town_square": RoomTemplate(
            id="town_square",
            name="Town",
            description="",
            safe=True,
            checkpoint=True,
            bank=True,
        )
    }
    items = {
        "gold_bar": ItemTemplate(
            id="gold_bar",
            name="Gold Bar",
            description="",
            kind="misc",
            value=100,
            sellable=True,
        )
    }
    # Merchant sells for 10g, value is 100g with sell_ratio 0.25 -> sell_revenue = 25g (exploit)
    merchants = {
        "cheap_merchant": MerchantTemplate(
            id="cheap_merchant",
            name="Cheap",
            room_id="town_square",
            greeting="",
            sells=[MerchantOffer(item_id="gold_bar", price=10)],
            buys="all",
            sell_ratio=0.25,
        )
    }
    with pytest.raises(ContentValidationError, match="Infinite money loop detected"):
        validate_content(rooms, {}, items, merchants, rules)


def _monster(monster_id: str, **fields: object) -> MonsterTemplate:
    return MonsterTemplate.model_validate(
        {
            "id": monster_id,
            "name": monster_id,
            "max_hp": 10,
            "attack": 2,
            "defense": 1,
            "xp_reward": 10,
            "respawn_seconds": 30,
            **fields,
        }
    )


def test_rare_variants_are_validated() -> None:
    from app.content.loader import validate_rare_variants

    def variant(monster_id: str, chance: float = 0.1) -> dict[str, object]:
        return {"rare_variant": {"monster_id": monster_id, "chance": chance}}

    golden = _monster("golden")
    ok = {"rat": _monster("rat", **variant("golden")), "golden": golden}
    room = RoomTemplate(
        id="field", name="Feld", description="", spawns=[SpawnRule(monster_id="rat")]
    )
    validate_rare_variants({"field": room}, ok)

    with pytest.raises(ContentValidationError, match="unknown rare variant"):
        validate_rare_variants({}, {"rat": _monster("rat", **variant("nope"))})
    with pytest.raises(ContentValidationError, match="must not have a rare variant itself"):
        validate_rare_variants(
            {},
            {
                "rat": _monster("rat", **variant("golden")),
                "golden": _monster("golden", **variant("rat")),
            },
        )
    with pytest.raises(ContentValidationError, match="belongs to both"):
        validate_rare_variants(
            {},
            {
                "rat": _monster("rat", **variant("golden")),
                "mouse": _monster("mouse", **variant("golden")),
                "golden": golden,
            },
        )
    direct = RoomTemplate(
        id="vault", name="Gewölbe", description="", spawns=[SpawnRule(monster_id="golden")]
    )
    with pytest.raises(ContentValidationError, match="spawns the rare variant 'golden' directly"):
        validate_rare_variants({"vault": direct}, ok)


def test_quests_are_validated() -> None:
    from app.content.loader import validate_quests
    from app.content.schemas import QuestTemplate

    real = load_content(Path("data"))

    def quest(quest_id: str = "q", **fields: object) -> QuestTemplate:
        return QuestTemplate.model_validate(
            {
                "id": quest_id,
                "name": "Q",
                "giver_id": "alchemist",
                "description": "",
                "completion_text": "",
                "objective": {"type": "deliver", "item_id": "goblin_ear"},
                **fields,
            }
        )

    def check(*quests: QuestTemplate) -> None:
        validate_quests({q.id: q for q in quests}, real.monsters, real.items, real.merchants)

    check(quest())
    with pytest.raises(ContentValidationError, match="unknown giver"):
        check(quest(giver_id="nobody"))
    with pytest.raises(ContentValidationError, match="unknown item 'nope'"):
        check(quest(objective={"type": "deliver", "item_id": "nope"}))
    with pytest.raises(ContentValidationError, match="unknown monster"):
        check(quest(objective={"type": "kill", "monster_id": "nope"}))
    with pytest.raises(ContentValidationError, match="rewards an unknown item"):
        check(quest(reward={"items": [{"item_id": "nope"}]}))
    with pytest.raises(ContentValidationError, match="requires an unknown quest"):
        check(quest(requires=["missing"]))
    with pytest.raises(ContentValidationError, match="form a cycle"):
        check(quest("a", requires=["b"]), quest("b", requires=["a"]))
    variants = [m.rare_variant.monster_id for m in real.monsters.values() if m.rare_variant]
    if variants:
        with pytest.raises(ContentValidationError, match="rare variant"):
            check(quest(objective={"type": "kill", "monster_id": variants[0]}))
