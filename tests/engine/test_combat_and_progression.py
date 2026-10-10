import random
from uuid import uuid4

from app.content.schemas import Ambush, LevelRule, MonsterTemplate, Rules
from app.engine.ambush import check_enter_ambush
from app.engine.loot import distribute_gold, distribute_xp
from app.engine.progression import calculate_level_progression


def test_level_progression(rules: Rules) -> None:
    rules = rules.model_copy(
        update={
            "levels": [
                LevelRule(level=1, xp_required=0, max_hp=20, attack=5, defense=2),
                LevelRule(level=2, xp_required=100, max_hp=30, attack=8, defense=4),
            ]
        }
    )
    # Player has 120 xp at level 1 -> levels up to 2
    res = calculate_level_progression(
        current_xp=120,
        current_level=1,
        base_max_hp=20,
        base_attack=5,
        base_defense=2,
        rules=rules,
    )
    assert res.leveled_up is True
    assert res.new_level == 2
    assert res.new_base_max_hp == 30
    assert res.new_base_attack == 8


def test_gold_distribution_with_remainder() -> None:
    p1 = uuid4()
    p2 = uuid4()
    p3 = uuid4()
    # 10 gold among 3 players: 3 each, 1 remainder to p1 (cursor 0)
    res = distribute_gold(total_gold=10, recipient_ids=[p1, p2, p3], cursor=0)
    assert res[p1] == 4
    assert res[p2] == 3
    assert res[p3] == 3


def test_xp_distribution_proportional() -> None:
    p1 = uuid4()
    p2 = uuid4()
    # p1 dealt 75 damage, p2 dealt 25 damage -> 75% vs 25% of 100 XP
    res = distribute_xp(total_xp=100, weights={p1: 75, p2: 25})
    assert res[p1] == 75
    assert res[p2] == 25


def test_ambush_on_entry() -> None:
    rng = random.Random(42)
    inst_id = uuid4()
    goblin = MonsterTemplate(
        id="goblin",
        name="Goblin",
        max_hp=10,
        attack=3,
        defense=1,
        xp_reward=10,
        respawn_seconds=30,
        ambush=Ambush(chance_on_enter=1.0, chance_per_tick=0.5, max_level_difference=5),
    )
    # Non-safe room -> Ambush guaranteed
    res = check_enter_ambush(
        player_level=1,
        is_safe_room=False,
        available_monsters=[(inst_id, goblin)],
        rng=rng,
    )
    assert res.ambushed is True
    assert res.ambushing_monster_instance_id == inst_id

    # Safe room -> No ambush
    res_safe = check_enter_ambush(
        player_level=1,
        is_safe_room=True,
        available_monsters=[(inst_id, goblin)],
        rng=rng,
    )
    assert res_safe.ambushed is False
