import random
from datetime import timedelta
from uuid import uuid4

from app.content.registry import ContentRegistry
from app.content.schemas import Rules
from app.engine.events import ActionResult
from app.engine.presence import (
    CONFIRM_DEATH,
    CONFIRM_DEATH_KIND,
    disconnect_expired,
    resolve_disconnect,
    resolve_login,
    resolve_logout,
)
from app.engine.state import (
    ActionSnapshot,
    GroupSnapshot,
    InventoryItemState,
    ParticipationSnapshot,
    TradeSnapshot,
)
from tests.engine.factories import (
    NOW,
    ROOM,
    actor_texts,
    fight,
    make_monster,
    make_player,
    player_update,
)


def rng() -> random.Random:
    return random.Random(3)


def room_texts(result: ActionResult) -> list[str]:
    return [e.text for e in result.events if e.audience.kind == "room"]


# --- login ---


def test_login_enters_the_world(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()

    result = resolve_login(ActionSnapshot(actor=actor), content, rules, NOW, rng())

    assert player_update(result, actor.id) == {
        "player_id": actor.id,
        "online": True,
        "disconnected_at": None,
    }
    assert actor_texts(result) == ["Willkommen zurück, Anna. Du bist in Dunkler Wald."]
    [room] = [e for e in result.events if e.audience.kind == "room"]
    assert (room.text, room.exclude_player_ids) == ("Anna betritt die Welt.", [actor.id])


def test_login_while_still_online_does_not_announce_again(
    content: ContentRegistry, rules: Rules
) -> None:
    result = resolve_login(
        ActionSnapshot(actor=make_player()), content, rules, NOW, rng(), already_online=True
    )
    assert room_texts(result) == []


def test_dead_character_logs_in_dead(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(hp=0)
    actor.state = "dead"

    result = resolve_login(ActionSnapshot(actor=actor), content, rules, NOW, rng())

    assert actor_texts(result) == ["Willkommen zurück, Anna. Du bist tot."]
    assert result.events[0].data["dead"] is True


# --- logout ---


def test_logout_goes_to_town_and_offline(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()

    result = resolve_logout(ActionSnapshot(actor=actor), content, rules, NOW, rng())

    assert player_update(result, actor.id) == {
        "player_id": actor.id,
        "current_room_id": rules.town_room_id,
        "online": False,
        "disconnected_at": None,
    }
    assert actor_texts(result) == ["Du hast dich ausgeloggt."]
    [room] = [e for e in result.events if e.audience.kind == "room"]
    # the room the character left, not the town
    assert room.audience.model_dump() == {"kind": "room", "room_id": ROOM}
    assert room.text == "Anna verlässt die Welt."


def test_logout_in_town_stays_in_town(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(room=rules.town_room_id)
    result = resolve_logout(ActionSnapshot(actor=actor), content, rules, NOW, rng())
    assert "current_room_id" not in player_update(result, actor.id)


def test_logout_in_combat_needs_confirmation(content: ContentRegistry, rules: Rules) -> None:
    actor, goblin = make_player(), make_monster()
    fight(actor, goblin)

    result = resolve_logout(
        ActionSnapshot(actor=actor, monsters=[goblin]), content, rules, NOW, rng()
    )

    assert result.changes.players == []
    assert [(e.kind, e.text) for e in result.events] == [(CONFIRM_DEATH_KIND, CONFIRM_DEATH)]


def test_confirmed_logout_in_combat_kills(content: ContentRegistry, rules: Rules) -> None:
    actor, goblin = make_player(gold=100), make_monster()
    fight(actor, goblin)
    participation = ParticipationSnapshot(
        monster_instance_id=goblin.id, player_id=actor.id, party_key=actor.party_key
    )
    snapshot = ActionSnapshot(actor=actor, monsters=[goblin], participations=[participation])

    result = resolve_logout(snapshot, content, rules, NOW, rng(), confirm_death=True)

    update = player_update(result, actor.id)
    assert (update["state"], update["hp"], update["online"]) == ("dead", 0, False)
    assert update["gold"] < 100  # loss rolls
    assert "current_room_id" not in update  # the dead stay where they fell
    assert [d.player_id for d in result.changes.participations_delete] == [actor.id]
    assert actor_texts(result)[0].startswith("Du bist gestorben.")
    assert actor_texts(result)[-1] == "Du hast dich ausgeloggt."


def test_expired_disconnect_in_combat_kills_without_asking(
    content: ContentRegistry, rules: Rules
) -> None:
    actor, goblin = make_player(), make_monster()
    fight(actor, goblin)

    result = resolve_logout(
        ActionSnapshot(actor=actor, monsters=[goblin]),
        content,
        rules,
        NOW,
        rng(),
        reason="disconnect",
    )

    assert player_update(result, actor.id)["state"] == "dead"
    # no actor in a forced logout: the character is told via its own channel (and log)
    assert all(e.audience.kind != "actor" for e in result.events)
    personal = [e.text for e in result.events if e.audience.kind == "players"]
    assert personal[-1] == "Deine Verbindung ist abgerissen. Du wurdest ausgeloggt."


def test_dead_character_logs_out_where_it_is(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(hp=0)
    actor.state = "dead"
    result = resolve_logout(ActionSnapshot(actor=actor), content, rules, NOW, rng())
    assert player_update(result, actor.id) == {
        "player_id": actor.id,
        "online": False,
        "disconnected_at": None,
    }


def test_logout_leaves_the_group(content: ContentRegistry, rules: Rules) -> None:
    group_id = uuid4()
    actor, ben, cleo = (make_player(n, group_id=group_id) for n in ("Anna", "Ben", "Cleo"))
    group = GroupSnapshot(id=group_id, leader_id=ben.id, member_ids=[ben.id, actor.id, cleo.id])
    snapshot = ActionSnapshot(actor=actor, room_players=[ben, cleo], groups=[group])

    result = resolve_logout(snapshot, content, rules, NOW, rng())

    update = player_update(result, actor.id)
    assert (update["group_id"], update["group_joined_at"]) == (None, None)
    assert "Anna hat die Gruppe verlassen." in [e.text for e in result.events]


def test_logout_cancels_trades(content: ContentRegistry, rules: Rules) -> None:
    actor, partner = make_player(), make_player("Ben")
    actor.inventory = [InventoryItemState(id=uuid4(), item_id="bread", quantity=1)]
    actor.locked_item_ids = [actor.inventory[0].id]
    trade = TradeSnapshot(id=uuid4(), initiator_id=partner.id, partner_id=actor.id, status="open")
    snapshot = ActionSnapshot(actor=actor, room_players=[partner], trades=[trade])

    result = resolve_logout(snapshot, content, rules, NOW, rng())

    assert result.changes.trades_cancel == [trade.id]
    [to_partner] = [e for e in result.events if e.audience.kind == "players"]
    assert to_partner.audience.model_dump()["player_ids"] == [partner.id]
    assert to_partner.text == "Der Handel mit Anna wurde abgebrochen."
    assert "Dein Handel wurde abgebrochen." in actor_texts(result)
    assert snapshot.trades == [] and actor.locked_item_ids == []


# --- connection lost ---


def test_disconnect_starts_the_grace_period_and_cancels_trades(
    content: ContentRegistry, rules: Rules
) -> None:
    actor, partner = make_player(), make_player("Ben")
    trade = TradeSnapshot(
        id=uuid4(), initiator_id=actor.id, partner_id=partner.id, status="requested"
    )

    result = resolve_disconnect(
        ActionSnapshot(actor=actor, trades=[trade]), content, rules, NOW, rng()
    )

    assert player_update(result, actor.id) == {"player_id": actor.id, "disconnected_at": NOW}
    assert result.changes.trades_cancel == [trade.id]
    assert all(e.audience.kind != "actor" for e in result.events)


def test_grace_period(rules: Rules) -> None:
    grace = timedelta(seconds=rules.timings.disconnect_grace_seconds)
    assert disconnect_expired(None, NOW, rules) is False
    assert disconnect_expired(NOW - grace + timedelta(seconds=1), NOW, rules) is False
    assert disconnect_expired(NOW - grace, NOW, rules) is True
