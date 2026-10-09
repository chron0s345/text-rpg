import random
from uuid import uuid4

from app.content.schemas import Ambush, LevelRule, MonsterTemplate, Rules
from app.engine.ambush import check_enter_ambush
from app.engine.combat import resolve_combat_round
from app.engine.loot import distribute_gold, distribute_xp
from app.engine.progression import calculate_level_progression


def test_combat_round_player_defeats_monster() -> None:
    rng = random.Random(42)
    # Player attack 20 vs monster defense 2, monster hp 10 -> Monster dies, no counter-attack
    res = resolve_combat_round(
        player_hp=30,
        player_attack=20,
        player_defense=5,
        monster_hp=10,
        monster_attack=10,
        monster_defense=2,
        rng=rng,
    )
    assert res.monster_died is True
    assert res.new_monster_hp == 0
    assert res.monster_damage == 0
    assert res.new_player_hp == 30


def test_combat_round_mutual_exchange() -> None:
    rng = random.Random(42)
    # Monster has 50 hp, player hits for ~5, monster counters for ~5
    res = resolve_combat_round(
        player_hp=20,
        player_attack=8,
        player_defense=3,
        monster_hp=50,
        monster_attack=8,
        monster_defense=3,
        rng=rng,
    )
    assert res.monster_died is False
    assert res.player_died is False
    assert res.new_monster_hp < 50
    assert res.new_player_hp < 20


def test_level_progression() -> None:
    rules = Rules(
        levels=[
            LevelRule(level=1, xp_required=0, max_hp=20, attack=5, defense=2),
            LevelRule(level=2, xp_required=100, max_hp=30, attack=8, defense=4),
        ]
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
    res = distribute_xp(total_xp=100, damage_by_player={p1: 75, p2: 25})
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
