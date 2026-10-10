import random
from uuid import UUID, uuid4

from app.content.schemas import Ambush, MonsterTemplate
from app.engine.ambush import check_enter_ambush
from app.engine.events import ResultBuilder, RoomAudience, rejected
from app.engine.participation import remove_from_combat, upsert_participation
from app.engine.state import (
    ActionSnapshot,
    MonsterSnapshot,
    ParticipationSnapshot,
    PlayerSnapshot,
    new_id,
    party_key_for,
)


def _player(group_id: UUID | None = None, name: str = "Anna") -> PlayerSnapshot:
    return PlayerSnapshot(
        id=uuid4(),
        account_id=uuid4(),
        name=name,
        hp=20,
        base_max_hp=20,
        base_attack=5,
        base_defense=2,
        xp=0,
        level=1,
        gold=0,
        current_room_id="forest",
        checkpoint_room_id="town_square",
        state="in_combat",
        group_id=group_id,
    )


def _monster(tagged_by: str | None = None) -> MonsterSnapshot:
    return MonsterSnapshot(
        id=uuid4(),
        monster_id="goblin",
        room_id="forest",
        hp=12,
        alive=True,
        tagged_by_party=tagged_by,
    )


# --- state helpers -------------------------------------------------------------


def test_new_id_is_deterministic_uuid4() -> None:
    a = [new_id(random.Random(7)) for _ in range(2)]
    assert a[0] == a[1]
    assert a[0].version == 4
    rng = random.Random(7)
    assert new_id(rng) != new_id(rng)


def test_party_key() -> None:
    pid, gid = uuid4(), uuid4()
    assert party_key_for(pid, None) == f"solo:{pid}"
    assert party_key_for(pid, gid) == f"group:{gid}"
    assert _player(group_id=gid).party_key == f"group:{gid}"


# --- ResultBuilder ----------------------------------------------------------------


def test_builder_merges_updates_per_entity() -> None:
    builder = ResultBuilder()
    pid, mid = uuid4(), uuid4()
    builder.update_player(pid, hp=10)
    builder.update_player(pid, gold=5, hp=8)
    builder.update_monster(mid, hp=3)

    result = builder.build()

    assert len(result.changes.players) == 1
    update = result.changes.players[0]
    assert update.model_dump(exclude_unset=True) == {"player_id": pid, "hp": 8, "gold": 5}
    assert result.changes.monsters[0].model_dump(exclude_unset=True) == {
        "monster_instance_id": mid,
        "hp": 3,
    }


def test_builder_explicit_none_is_kept_as_set() -> None:
    builder = ResultBuilder()
    pid = uuid4()
    builder.update_player(pid, combat_target_id=None)
    update = builder.build().changes.players[0]
    assert update.model_dump(exclude_unset=True) == {"player_id": pid, "combat_target_id": None}


def test_builder_events() -> None:
    builder = ResultBuilder()
    actor = uuid4()
    builder.to_actor("combat.hit", "Du triffst.", damage=5)
    builder.event("combat.hit", RoomAudience(room_id="forest"), "Anna trifft.", exclude=[actor])
    events = builder.build().events
    assert events[0].audience.kind == "actor"
    assert events[0].data == {"damage": 5}
    assert events[1].exclude_player_ids == [actor]


def test_rejected() -> None:
    result = rejected("Das geht gerade nicht.")
    assert result.changes.players == []
    assert [(e.kind, e.text) for e in result.events] == [
        ("action.rejected", "Das geht gerade nicht.")
    ]


# --- participation ----------------------------------------------------------------


def test_upsert_participation_creates_and_accumulates() -> None:
    player = _player()
    monster = _monster()
    snapshot = ActionSnapshot(actor=player, monsters=[monster])
    builder = ResultBuilder()

    upsert_participation(builder, snapshot, player, monster.id, 4)
    upsert_participation(builder, snapshot, player, monster.id, 3)

    assert snapshot.participations_for(monster.id)[0].damage_dealt == 7
    upserts = builder.changes.participations_upsert
    assert len(upserts) == 1
    assert upserts[0].damage_dealt == 7
    assert upserts[0].party_key == player.party_key


def test_remove_last_tagging_member_clears_tag() -> None:
    player = _player()
    monster = _monster(tagged_by=player.party_key)
    player.combat_target_id = monster.id
    snapshot = ActionSnapshot(
        actor=player,
        monsters=[monster],
        participations=[
            ParticipationSnapshot(
                monster_instance_id=monster.id, player_id=player.id, party_key=player.party_key
            )
        ],
    )
    builder = ResultBuilder()

    remove_from_combat(builder, snapshot, player, monster.id)

    assert player.combat_target_id is None
    assert snapshot.participations == []
    assert monster.tagged_by_party is None
    changes = builder.build().changes
    assert changes.participations_delete[0].player_id == player.id
    assert changes.monsters[0].model_dump(exclude_unset=True) == {
        "monster_instance_id": monster.id,
        "tagged_by_party": None,
    }


def test_remove_keeps_tag_while_party_member_remains() -> None:
    group_id = uuid4()
    anna, ben = _player(group_id, "Anna"), _player(group_id, "Ben")
    monster = _monster(tagged_by=anna.party_key)
    snapshot = ActionSnapshot(
        actor=anna,
        room_players=[ben],
        monsters=[monster],
        participations=[
            ParticipationSnapshot(
                monster_instance_id=monster.id, player_id=p.id, party_key=p.party_key
            )
            for p in (anna, ben)
        ],
    )
    builder = ResultBuilder()

    remove_from_combat(builder, snapshot, anna, monster.id)

    assert monster.tagged_by_party == anna.party_key
    assert builder.build().changes.monsters == []


def test_remove_keeps_tag_of_other_party() -> None:
    stranger = _player()
    player = _player()
    monster = _monster(tagged_by=stranger.party_key)
    snapshot = ActionSnapshot(
        actor=player,
        room_players=[stranger],
        monsters=[monster],
        participations=[
            ParticipationSnapshot(
                monster_instance_id=monster.id, player_id=p.id, party_key=p.party_key
            )
            for p in (player, stranger)
        ],
    )
    remove_from_combat(ResultBuilder(), snapshot, player, monster.id)
    assert monster.tagged_by_party == stranger.party_key


# --- ambush level difference ------------------------------------------------------


def _ambusher(level: int, max_diff: int | None) -> MonsterTemplate:
    return MonsterTemplate(
        id="wolf",
        name="Wolf",
        level=level,
        max_hp=10,
        attack=3,
        defense=1,
        xp_reward=10,
        respawn_seconds=30,
        ambush=Ambush(chance_on_enter=1.0, chance_per_tick=0.0, max_level_difference=max_diff),
    )


def test_ambush_respects_level_difference_relative_to_monster_level() -> None:
    wolf = _ambusher(level=3, max_diff=2)
    monsters = [(uuid4(), wolf)]
    rng = random.Random(1)
    # 5 - 3 = 2 <= 2 -> attacks
    assert check_enter_ambush(5, False, monsters, rng).ambushed is True
    # 6 - 3 = 3 > 2 -> leaves the player alone
    assert check_enter_ambush(6, False, monsters, rng).ambushed is False
    # weaker players are always fair game
    assert check_enter_ambush(1, False, monsters, rng).ambushed is True


def test_ambush_without_level_limit() -> None:
    monsters = [(uuid4(), _ambusher(level=1, max_diff=None))]
    assert check_enter_ambush(50, False, monsters, random.Random(1)).ambushed is True
