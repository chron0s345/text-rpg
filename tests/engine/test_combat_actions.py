import dataclasses
import random
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.content.loader import load_content
from app.content.registry import ContentRegistry
from app.content.schemas import (
    CombatRules,
    LootEntry,
    MonsterTemplate,
    Regeneration,
    Rules,
)
from app.engine.combat import calculate_damage, resolve_attack, resolve_flee
from app.engine.events import ActionResult, PlayersAudience
from app.engine.loot import distribute_xp
from app.engine.state import (
    ActionSnapshot,
    GroupSnapshot,
    InventoryItemState,
    MonsterSnapshot,
    ParticipationSnapshot,
    PlayerSnapshot,
    party_key_for,
)
from tests.conftest import DATA_DIR

NOW = datetime(2026, 1, 1, 12, 0, 0)
ROOM = "dark_forest"


@pytest.fixture(scope="module")
def content() -> ContentRegistry:
    real = load_content(DATA_DIR)
    dummy = MonsterTemplate(
        id="dummy",
        name="Übungspuppe",
        max_hp=10,
        attack=5,
        defense=0,
        xp_reward=30,
        gold_reward=(7, 7),
        respawn_seconds=60,
        loot_table=[LootEntry(item_id="goblin_ear", chance=1.0, min_qty=2, max_qty=2)],
    )
    troll = dummy.model_copy(
        update={
            "id": "troll",
            "name": "Troll",
            "attack": 100,
            "regen": Regeneration(delay_seconds=10, hp_per_second=1.0),
        }
    )
    return dataclasses.replace(real, monsters={**real.monsters, "dummy": dummy, "troll": troll})


@pytest.fixture
def rules(content: ContentRegistry) -> Rules:
    """Real rules, but deterministic damage (no variance)."""
    return content.rules.model_copy(
        update={"combat": CombatRules(min_damage=1, damage_variance=0, flee_chance=0.5)}
    )


def make_player(
    name: str = "Anna",
    *,
    group_id: UUID | None = None,
    hp: int = 20,
    gold: int = 0,
    xp: int = 0,
    room: str = ROOM,
) -> PlayerSnapshot:
    return PlayerSnapshot(
        id=uuid4(),
        account_id=uuid4(),
        name=name,
        hp=hp,
        base_max_hp=20,
        base_attack=5,
        base_defense=2,
        xp=xp,
        level=1,
        gold=gold,
        current_room_id=room,
        checkpoint_room_id="town_square",
        state="exploring",
        group_id=group_id,
    )


def make_monster(monster_id: str = "dummy", hp: int = 10, **fields: object) -> MonsterSnapshot:
    return MonsterSnapshot.model_validate(
        {
            "id": uuid4(),
            "monster_id": monster_id,
            "room_id": ROOM,
            "hp": hp,
            "alive": True,
            **fields,
        }
    )


def fight(player: PlayerSnapshot, monster: MonsterSnapshot) -> None:
    player.state = "in_combat"
    player.combat_target_id = monster.id


def player_update(result: ActionResult, player_id: UUID) -> dict[str, Any]:
    return next(
        u.model_dump(exclude_unset=True) for u in result.changes.players if u.player_id == player_id
    )


def monster_update(result: ActionResult, monster_id: UUID) -> dict[str, Any]:
    return next(
        u.model_dump(exclude_unset=True)
        for u in result.changes.monsters
        if u.monster_instance_id == monster_id
    )


def actor_texts(result: ActionResult) -> list[str]:
    return [e.text for e in result.events if e.audience.kind == "actor"]


def attack(
    snapshot: ActionSnapshot, content: ContentRegistry, rules: Rules, target: MonsterSnapshot
) -> ActionResult:
    return resolve_attack(snapshot, content, rules, NOW, random.Random(1), target.id)


# --- damage & xp helpers ----------------------------------------------------------


def test_damage_respects_min_damage_and_variance() -> None:
    combat = CombatRules(min_damage=2, damage_variance=1, flee_chance=0.5)
    rng = random.Random(0)
    assert {calculate_damage(10, 4, combat, rng) for _ in range(200)} == {5, 6, 7}
    assert calculate_damage(1, 50, combat, rng) == 2


def test_distribute_xp_loses_no_points() -> None:
    a, b, c = uuid4(), uuid4(), uuid4()
    shares = distribute_xp(100, {a: 1, b: 1, c: 1})
    assert sum(shares.values()) == 100
    assert shares[a] == 34
    assert distribute_xp(10, {a: 0, b: 0}) == {a: 0, b: 0}


# --- attack: rejections -------------------------------------------------------------


def test_attack_rejected_in_safe_room(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(room="town_square")
    monster = make_monster()
    result = attack(ActionSnapshot(actor=actor, monsters=[monster]), content, rules, monster)
    assert result.changes.players == []
    assert [e.kind for e in result.events] == ["action.rejected"]


def test_attack_rejected_when_dead_or_target_invalid(
    content: ContentRegistry, rules: Rules
) -> None:
    monster = make_monster()
    dead = make_player()
    dead.state = "dead"
    assert actor_texts(
        attack(ActionSnapshot(actor=dead, monsters=[monster]), content, rules, monster)
    ) == ["Du bist tot."]

    actor = make_player()
    gone = make_monster(alive=False)
    result = attack(ActionSnapshot(actor=actor, monsters=[gone]), content, rules, gone)
    assert actor_texts(result) == ["Dieses Ziel gibt es hier nicht."]

    other = make_monster()
    fight(actor, other)
    result = attack(ActionSnapshot(actor=actor, monsters=[monster, other]), content, rules, monster)
    assert actor_texts(result) == ["Du kämpfst bereits gegen einen anderen Gegner."]


# --- attack: a regular round ----------------------------------------------------------


def test_attack_round_hits_tags_and_takes_counter(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    monster = make_monster()
    snapshot = ActionSnapshot(actor=actor, monsters=[monster])

    result = attack(snapshot, content, rules, monster)

    # player: attack 5 - defense 0 = 5; monster: attack 5 - defense 2 = 3
    assert monster_update(result, monster.id) == {
        "monster_instance_id": monster.id,
        "hp": 5,
        "last_combat_at": NOW,
        "tagged_by_party": actor.party_key,
    }
    assert player_update(result, actor.id) == {
        "player_id": actor.id,
        "state": "in_combat",
        "combat_target_id": monster.id,
        "hp": 17,
    }
    assert result.changes.participations_upsert[0].damage_dealt == 5
    room_events = [e for e in result.events if e.audience.kind == "room"]
    assert all(actor.id in e.exclude_player_ids for e in room_events)
    assert "Du triffst Übungspuppe für 5 Schaden." in actor_texts(result)


def test_attack_keeps_existing_tag_of_other_party(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    monster = make_monster(tagged_by_party="solo:someone")
    result = attack(ActionSnapshot(actor=actor, monsters=[monster]), content, rules, monster)
    assert "tagged_by_party" not in monster_update(result, monster.id)


def test_regeneration_applied_before_round(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(hp=500)
    # 2 HP stored, 100 s ago, delay 10 s at 1 HP/s -> back to max 10, then hit for 5
    troll = make_monster("troll", hp=2, last_combat_at=NOW - timedelta(seconds=100))
    result = attack(ActionSnapshot(actor=actor, monsters=[troll]), content, rules, troll)
    assert monster_update(result, troll.id)["hp"] == 5


# --- attack: kill & rewards -------------------------------------------------------------


def test_solo_kill_rewards_and_respawn(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(gold=3)
    monster = make_monster(hp=5)
    result = attack(ActionSnapshot(actor=actor, monsters=[monster]), content, rules, monster)

    update = player_update(result, actor.id)
    assert update["state"] == "exploring"
    assert update["combat_target_id"] is None
    assert update["xp"] == 30
    assert update["gold"] == 10
    assert "hp" not in update  # dead monsters don't strike back
    assert monster_update(result, monster.id) == {
        "monster_instance_id": monster.id,
        "hp": 0,
        "alive": False,
        "last_combat_at": NOW,
        "tagged_by_party": None,
        "respawn_at": NOW + timedelta(seconds=60),
    }
    assert result.changes.participations_clear_monster_ids == [monster.id]
    [upsert] = result.changes.inventory_upsert
    assert (upsert.player_id, upsert.item.item_id, upsert.item.quantity) == (
        actor.id,
        "goblin_ear",
        2,
    )
    assert result.changes.floor_items_create == []
    # personal rewards for the actor go via the HTTP response, never via SSE
    assert not any(e.audience.kind == "players" for e in result.events)


def test_kill_triggers_level_up(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(xp=90)
    monster = make_monster(hp=5)
    result = attack(ActionSnapshot(actor=actor, monsters=[monster]), content, rules, monster)
    level_2 = rules.level_rule(2)
    update = player_update(result, actor.id)
    assert (update["level"], update["base_max_hp"], update["base_attack"]) == (
        2,
        level_2.max_hp,
        level_2.attack,
    )
    assert "Du hast Stufe 2 erreicht!" in actor_texts(result)


def _group_snapshot(
    *, actor_index: int = 0, cursor: int = 0
) -> tuple[ActionSnapshot, list[PlayerSnapshot], MonsterSnapshot, GroupSnapshot]:
    group_id = uuid4()
    members = [make_player(n, group_id=group_id) for n in ("Anna", "Ben", "Cleo")]
    group = GroupSnapshot(
        id=group_id, leader_id=members[0].id, loot_cursor=cursor, member_ids=[m.id for m in members]
    )
    key = party_key_for(members[0].id, group_id)
    monster = make_monster(hp=5, tagged_by_party=key)
    # Ben already dealt 5 damage, Cleo is idle in the room
    participations = [
        ParticipationSnapshot(
            monster_instance_id=monster.id, player_id=members[1].id, party_key=key, damage_dealt=5
        )
    ]
    fight(members[1], monster)
    actor = members[actor_index]
    snapshot = ActionSnapshot(
        actor=actor,
        room_players=[m for m in members if m is not actor],
        monsters=[monster],
        participations=participations,
        groups=[group],
    )
    return snapshot, members, monster, group


def test_group_kill_splits_xp_gold_and_round_robin_loot(
    content: ContentRegistry, rules: Rules
) -> None:
    snapshot, (anna, ben, cleo), monster, group = _group_snapshot()

    result = attack(snapshot, content, rules, monster)

    # weights: Anna 5, Ben 5, Cleo 0.5 * avg(5) = 2.5 -> 30 XP = 12 / 12 / 6
    assert [player_update(result, p.id)["xp"] for p in (anna, ben, cleo)] == [12, 12, 6]
    # 7 gold / 3 = 2 each, remainder coin to Anna (cursor 0)
    assert [player_update(result, p.id)["gold"] for p in (anna, ben, cleo)] == [3, 2, 2]
    # cursor moved to Ben by the remainder coin, so Ben gets the ears; then cursor -> Cleo
    [upsert] = result.changes.inventory_upsert
    assert upsert.player_id == ben.id
    assert [(g.group_id, g.loot_cursor) for g in result.changes.groups] == [(group.id, 2)]
    # every participant leaves combat
    assert player_update(result, ben.id)["state"] == "exploring"
    # loot info for the others goes to the group / to them personally, not to the actor via SSE
    sse_recipients = {
        pid
        for e in result.events
        if isinstance(e.audience, PlayersAudience)
        for pid in e.audience.player_ids
    }
    assert anna.id not in sse_recipients
    assert {ben.id, cleo.id} <= sse_recipients


def test_round_robin_skips_member_without_space(content: ContentRegistry, rules: Rules) -> None:
    snapshot, (_anna, ben, cleo), monster, _group = _group_snapshot(cursor=0)
    ben.inventory = [
        InventoryItemState(id=uuid4(), item_id="rusty_dagger", quantity=1)
        for _ in range(rules.inventory.slots)
    ]
    result = attack(snapshot, content, rules, monster)
    # remainder coin moves cursor to Ben, Ben is full -> Cleo gets the ears
    [upsert] = result.changes.inventory_upsert
    assert upsert.player_id == cleo.id


def test_stranger_cannot_steal_tagged_loot(content: ContentRegistry, rules: Rules) -> None:
    snapshot, (anna, ben, cleo), monster, group = _group_snapshot()
    stranger = make_player("Dieb")
    snapshot = ActionSnapshot(
        actor=stranger,
        room_players=[anna, ben, cleo],
        monsters=[monster],
        participations=snapshot.participations,
        groups=[group],
    )

    result = attack(snapshot, content, rules, monster)

    stranger_update = player_update(result, stranger.id)
    assert "gold" not in stranger_update
    # XP still follows damage (stranger 5, Ben 5) plus idle shares for Anna and Cleo (2.5 each)
    assert stranger_update["xp"] == 10
    assert {u.player_id for u in result.changes.inventory_upsert} <= {anna.id, ben.id, cleo.id}
    assert sum(player_update(result, p.id).get("gold", 0) for p in (anna, ben, cleo)) == 7


def test_loot_falls_to_floor_when_inventory_full(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    actor.inventory = [
        InventoryItemState(id=uuid4(), item_id="rusty_dagger", quantity=1)
        for _ in range(rules.inventory.slots)
    ]
    monster = make_monster(hp=5)
    result = attack(ActionSnapshot(actor=actor, monsters=[monster]), content, rules, monster)

    assert result.changes.inventory_upsert == []
    [floor] = result.changes.floor_items_create
    assert (floor.room_id, floor.item_id, floor.quantity, floor.source) == (
        ROOM,
        "goblin_ear",
        2,
        "loot",
    )
    assert floor.expires_at == NOW + timedelta(seconds=rules.timings.floor_item_ttl_seconds)
    assert any("Inventar voll" in e.text for e in result.events)


# --- attack: player death ---------------------------------------------------------------


def test_counter_attack_kills_player_and_clears_tag(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(hp=3, gold=100)
    troll = make_monster("troll", hp=10)
    result = attack(ActionSnapshot(actor=actor, monsters=[troll]), content, rules, troll)

    update = player_update(result, actor.id)
    assert (update["hp"], update["state"], update["combat_target_id"]) == (0, "dead", None)
    lo, hi = rules.death.gold_loss_percent
    assert 100 - hi <= update["gold"] <= 100 - lo
    assert [d.player_id for d in result.changes.participations_delete] == [actor.id]
    # the only member of the tagging party died -> tag is lifted
    assert monster_update(result, troll.id)["tagged_by_party"] is None
    assert any(e.kind == "player.died" and e.audience.kind == "room" for e in result.events)


# --- flee ----------------------------------------------------------------------------------


def test_flee_rejected_outside_combat(content: ContentRegistry, rules: Rules) -> None:
    result = resolve_flee(
        ActionSnapshot(actor=make_player()), content, rules, NOW, random.Random(1)
    )
    assert actor_texts(result) == ["Du bist in keinem Kampf."]


def test_flee_success_ends_combat_without_damage(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    monster = make_monster(tagged_by_party=actor.party_key)
    fight(actor, monster)
    snapshot = ActionSnapshot(
        actor=actor,
        monsters=[monster],
        participations=[
            ParticipationSnapshot(
                monster_instance_id=monster.id, player_id=actor.id, party_key=actor.party_key
            )
        ],
    )
    always = rules.model_copy(
        update={"combat": rules.combat.model_copy(update={"flee_chance": 1.0})}
    )

    result = resolve_flee(snapshot, content, always, NOW, random.Random(1))

    assert player_update(result, actor.id) == {
        "player_id": actor.id,
        "state": "exploring",
        "combat_target_id": None,
    }
    assert monster_update(result, monster.id) == {
        "monster_instance_id": monster.id,
        "tagged_by_party": None,
    }


def test_flee_failure_takes_a_hit(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    monster = make_monster()
    fight(actor, monster)
    never = rules.model_copy(
        update={"combat": rules.combat.model_copy(update={"flee_chance": 0.0})}
    )

    result = resolve_flee(
        ActionSnapshot(actor=actor, monsters=[monster]), content, never, NOW, random.Random(1)
    )

    assert player_update(result, actor.id) == {"player_id": actor.id, "hp": 17}
    assert monster_update(result, monster.id)["last_combat_at"] == NOW
    assert actor_texts(result)[0] == "Die Flucht misslingt!"


def test_flee_with_vanished_monster_just_ends_combat(
    content: ContentRegistry, rules: Rules
) -> None:
    actor = make_player()
    fight(actor, make_monster())
    result = resolve_flee(ActionSnapshot(actor=actor), content, rules, NOW, random.Random(1))
    assert player_update(result, actor.id)["state"] == "exploring"
