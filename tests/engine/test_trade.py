"""Trading between players (engine/trade.py): request, accept, offers with revisions,
confirmation, execution in one step, failures that move nothing, cancelling."""

import random
from datetime import timedelta
from uuid import UUID, uuid4

from app.content.registry import ContentRegistry
from app.content.schemas import Rules
from app.engine.events import ActionResult, PlayersAudience
from app.engine.state import (
    InventoryItemState,
    PlayerSnapshot,
    TradeActionSnapshot,
    TradeOfferSnapshot,
    TradeSnapshot,
)
from app.engine.trade import (
    resolve_trade_accept,
    resolve_trade_cancel,
    resolve_trade_check,
    resolve_trade_confirm,
    resolve_trade_offer_gold,
    resolve_trade_offer_item,
    resolve_trade_remove_item,
    resolve_trade_request,
)
from tests.engine.factories import NOW, actor_texts, fight, make_monster, make_player

RNG = random.Random(2)
ROOM = "town_square"


def stack(item_id: str, quantity: int = 1, slot: str | None = None) -> InventoryItemState:
    return InventoryItemState.model_validate(
        {"id": uuid4(), "item_id": item_id, "quantity": quantity, "equipped_slot": slot}
    )


def traders() -> tuple[PlayerSnapshot, PlayerSnapshot]:
    return make_player("Anna", room=ROOM, gold=50), make_player("Ben", room=ROOM, gold=10)


def open_trade(anna: PlayerSnapshot, ben: PlayerSnapshot, **fields: object) -> TradeSnapshot:
    data: dict[str, object] = {
        "id": uuid4(),
        "initiator_id": anna.id,
        "partner_id": ben.id,
        "room_id": ROOM,
        "status": "open",
        "expires_at": NOW + timedelta(seconds=30),
    }
    data.update(fields)
    return TradeSnapshot.model_validate(data)


def texts_for(result: ActionResult, player_id: UUID) -> list[str]:
    return [
        e.text
        for e in result.events
        if isinstance(e.audience, PlayersAudience) and player_id in e.audience.player_ids
    ]


# --- request & accept ---


def test_request(content: ContentRegistry, rules: Rules) -> None:
    anna, ben = traders()
    result = resolve_trade_request(
        TradeActionSnapshot(actor=anna, partner=ben), content, rules, NOW, RNG, ben.id
    )
    [created] = result.changes.trades_create
    assert (created.initiator_id, created.partner_id, created.room_id) == (anna.id, ben.id, ROOM)
    assert created.expires_at == NOW + timedelta(seconds=rules.trade.request_ttl_seconds)
    assert texts_for(result, ben.id) == ["Anna möchte mit dir handeln."]


def test_request_rejections(content: ContentRegistry, rules: Rules) -> None:
    anna, ben = traders()

    def request(partner: PlayerSnapshot | None, target: UUID, *trades: TradeSnapshot) -> str:
        snap = TradeActionSnapshot(actor=anna, partner=partner, trades=list(trades))
        [text] = actor_texts(resolve_trade_request(snap, content, rules, NOW, RNG, target))
        return text

    assert request(None, anna.id) == "Mit dir selbst kannst du nicht handeln."
    assert request(None, uuid4()) == "Diese Person ist nicht hier."
    elsewhere = make_player("Ben", room="town_bank")
    assert request(elsewhere, elsewhere.id) == "Diese Person ist nicht hier."
    twin = make_player("Zwilling", room=ROOM)
    twin.account_id = anna.account_id
    assert request(twin, twin.id) == "Deine eigenen Charaktere können nicht miteinander handeln."
    fight(ben, make_monster())
    assert request(ben, ben.id) == "Ben ist gerade beschäftigt."
    ben.state, ben.combat_target_id = "exploring", None
    ben.disconnected = True
    assert request(ben, ben.id) == "Ben ist gerade beschäftigt."
    ben.disconnected = False
    cleo = make_player("Cleo", room=ROOM)
    assert request(ben, ben.id, open_trade(anna, cleo)) == "Du handelst bereits."
    assert request(ben, ben.id, open_trade(cleo, ben)) == "Ben handelt gerade mit jemand anderem."


def test_accept(content: ContentRegistry, rules: Rules) -> None:
    anna, ben = traders()
    trade = open_trade(anna, ben, status="requested")
    snap = TradeActionSnapshot(actor=ben, partner=anna, trades=[trade])
    result = resolve_trade_accept(snap, content, rules, NOW, RNG, trade.id)
    [update] = result.changes.trades
    assert update.model_dump(exclude_unset=True) == {
        "trade_id": trade.id,
        "status": "open",
        "revision": 0,
    }
    assert texts_for(result, anna.id) == ["Ben nimmt deinen Handel an."]

    own = TradeActionSnapshot(actor=anna, partner=ben, trades=[trade])
    assert actor_texts(resolve_trade_accept(own, content, rules, NOW, RNG, trade.id)) == [
        "Warte, bis Ben annimmt."
    ]
    late = NOW + timedelta(seconds=31)
    assert actor_texts(resolve_trade_accept(snap, content, rules, late, RNG, trade.id)) == [
        "Die Anfrage ist abgelaufen."
    ]


# --- offers ---


def test_offer_bumps_revision_and_resets_confirmations(
    content: ContentRegistry, rules: Rules
) -> None:
    anna, ben = traders()
    potions = stack("healing_potion", 4)
    anna.inventory = [potions]
    trade = open_trade(anna, ben, revision=3, initiator_confirmed=True, partner_confirmed=True)
    snap = TradeActionSnapshot(actor=anna, partner=ben, trades=[trade])

    result = resolve_trade_offer_item(snap, content, rules, NOW, RNG, trade.id, potions.id, 2)

    [offer] = result.changes.trade_offers_upsert
    assert (offer.player_id, offer.inventory_item_id, offer.quantity) == (anna.id, potions.id, 2)
    [update] = result.changes.trades
    assert update.model_dump(exclude_unset=True, exclude={"trade_id"}) == {
        "revision": 4,
        "initiator_confirmed": False,
        "partner_confirmed": False,
    }
    assert texts_for(result, ben.id) == ["Anna ändert das Angebot: 2x Heiltrank angeboten."]


def test_offer_rejections(content: ContentRegistry, rules: Rules) -> None:
    anna, ben = traders()
    worn = stack("rusty_dagger", slot="weapon")
    quest = stack("goblin_ear", 2)
    anna.inventory = [worn, quest]
    trade = open_trade(anna, ben)

    def offer(item: UUID, quantity: int = 1, t: TradeSnapshot = trade) -> str:
        snap = TradeActionSnapshot(actor=anna, partner=ben, trades=[t])
        [text] = actor_texts(
            resolve_trade_offer_item(snap, content, rules, NOW, RNG, t.id, item, quantity)
        )
        return text

    assert offer(uuid4()) == "Diesen Gegenstand hast du nicht."
    assert offer(worn.id) == "Lege den Gegenstand zuerst ab."
    assert offer(quest.id, 3) == "So viele hast du nicht."
    requested = open_trade(anna, ben, status="requested")
    assert offer(quest.id, t=requested) == "Der Handel ist nicht offen."

    content.items["goblin_ear"], original = (
        content.items["goblin_ear"].model_copy(update={"tradable": False}),
        content.items["goblin_ear"],
    )
    try:
        assert offer(quest.id) == "Goblinohr lässt sich nicht handeln."
    finally:
        content.items["goblin_ear"] = original

    full = open_trade(
        anna,
        ben,
        offers=[
            TradeOfferSnapshot(player_id=anna.id, inventory_item_id=uuid4(), quantity=1)
            for _ in range(rules.trade.max_offer_entries)
        ],
    )
    assert offer(quest.id, t=full) == "Mehr passt nicht in dein Angebot."


def test_gold_and_removing_items(content: ContentRegistry, rules: Rules) -> None:
    anna, ben = traders()
    bread = stack("bread", 2)
    anna.inventory = [bread]
    trade = open_trade(
        anna,
        ben,
        offers=[TradeOfferSnapshot(player_id=anna.id, inventory_item_id=bread.id, quantity=2)],
    )
    snap = TradeActionSnapshot(actor=anna, partner=ben, trades=[trade])
    too_much = resolve_trade_offer_gold(snap, content, rules, NOW, RNG, trade.id, 51)
    assert actor_texts(too_much) == ["Du hast nur 50 Gold."]
    gold = resolve_trade_offer_gold(snap, content, rules, NOW, RNG, trade.id, 20)
    assert gold.changes.trades[0].initiator_gold == 20

    removed = resolve_trade_remove_item(snap, content, rules, NOW, RNG, trade.id, bread.id)
    assert [(d.trade_id, d.inventory_item_id) for d in removed.changes.trade_offers_delete] == [
        (trade.id, bread.id)
    ]
    assert removed.changes.trades[0].revision == trade.revision


# --- confirm & execute ---


def test_confirm_needs_the_current_revision(content: ContentRegistry, rules: Rules) -> None:
    anna, ben = traders()
    trade = open_trade(anna, ben, revision=2)
    snap = TradeActionSnapshot(actor=anna, partner=ben, trades=[trade])
    stale = resolve_trade_confirm(snap, content, rules, NOW, RNG, trade.id, 1)
    assert actor_texts(stale) == ["Das Angebot hat sich geändert. Prüfe es und bestätige erneut."]
    first = resolve_trade_confirm(snap, content, rules, NOW, RNG, trade.id, 2)
    [update] = first.changes.trades
    assert update.model_dump(exclude_unset=True, exclude={"trade_id"}) == {
        "initiator_confirmed": True
    }
    assert texts_for(first, ben.id) == ["Anna bestätigt den Handel."]


def test_second_confirmation_executes_the_trade(content: ContentRegistry, rules: Rules) -> None:
    anna, ben = traders()
    potions, dagger = stack("healing_potion", 4), stack("rusty_dagger")
    anna.inventory = [potions]
    own_potion = stack("healing_potion", 1)
    ben.inventory = [dagger, own_potion]
    trade = open_trade(
        anna,
        ben,
        initiator_gold=5,
        partner_gold=0,
        initiator_confirmed=True,
        offers=[
            TradeOfferSnapshot(player_id=anna.id, inventory_item_id=potions.id, quantity=3),
            TradeOfferSnapshot(player_id=ben.id, inventory_item_id=dagger.id, quantity=1),
        ],
    )
    snap = TradeActionSnapshot(actor=ben, partner=anna, trades=[trade])

    result = resolve_trade_confirm(snap, content, rules, NOW, RNG, trade.id, trade.revision)

    changes = result.changes
    assert {(d.trade_id, d.inventory_item_id) for d in changes.trade_offers_delete} == {
        (trade.id, potions.id),
        (trade.id, dagger.id),
    }
    assert changes.trades[-1].status == "completed"
    # Anna keeps 1 potion and gets a new dagger row; Ben's potion stack grows to 4
    by_owner: dict[UUID, list[tuple[str, int]]] = {}
    for upsert in changes.inventory_upsert:
        by_owner.setdefault(upsert.player_id, []).append(
            (upsert.item.item_id, upsert.item.quantity)
        )
    assert sorted(by_owner[anna.id]) == [("healing_potion", 1), ("rusty_dagger", 1)]
    assert by_owner[ben.id] == [("healing_potion", 4)]
    assert changes.inventory_delete == [dagger.id]
    assert all(u.item.id != dagger.id for u in changes.inventory_upsert)  # rows never change owner
    golds = {u.player_id: u.gold for u in changes.players}
    assert golds == {anna.id: 45, ben.id: 15}
    [log] = changes.trade_logs
    assert log.summary["initiator"]["items"] == [{"item_id": "healing_potion", "quantity": 3}]
    assert log.summary["partner"]["gold"] == 0
    assert texts_for(result, anna.id) == [
        "Handel mit Ben abgeschlossen. Du erhältst Rostiger Dolch und gibst 3x Heiltrank, 5 Gold."
    ]


def test_execution_without_space_moves_nothing(content: ContentRegistry, rules: Rules) -> None:
    anna, ben = traders()
    potions = stack("healing_potion", 1)
    anna.inventory = [potions]
    ben.inventory = [stack("rusty_dagger") for _ in range(rules.inventory.slots)]
    trade = open_trade(
        anna,
        ben,
        partner_confirmed=True,
        offers=[TradeOfferSnapshot(player_id=anna.id, inventory_item_id=potions.id, quantity=1)],
    )
    snap = TradeActionSnapshot(actor=anna, partner=ben, trades=[trade])
    result = resolve_trade_confirm(snap, content, rules, NOW, RNG, trade.id, trade.revision)

    assert result.changes.inventory_upsert == [] and result.changes.inventory_delete == []
    assert result.changes.trade_offers_delete == []
    [update] = result.changes.trades
    assert update.model_dump(exclude_unset=True, exclude={"trade_id"}) == {
        "initiator_confirmed": False,
        "partner_confirmed": False,
    }
    assert actor_texts(result) == [
        "Der Handel kann nicht ausgeführt werden: Ben hat nicht genug Platz."
    ]


def test_execution_checks_gold_again(content: ContentRegistry, rules: Rules) -> None:
    anna, ben = traders()
    trade = open_trade(anna, ben, initiator_gold=60, partner_confirmed=True)  # Anna has 50
    snap = TradeActionSnapshot(actor=anna, partner=ben, trades=[trade])
    result = resolve_trade_confirm(snap, content, rules, NOW, RNG, trade.id, trade.revision)
    assert actor_texts(result) == [
        "Der Handel kann nicht ausgeführt werden: Anna hat nicht mehr so viel Gold."
    ]
    assert result.changes.players == []


# --- cancel & check ---


def test_cancel_and_decline(content: ContentRegistry, rules: Rules) -> None:
    anna, ben = traders()
    bread = stack("bread")
    anna.inventory = [bread]
    trade = open_trade(
        anna,
        ben,
        offers=[TradeOfferSnapshot(player_id=anna.id, inventory_item_id=bread.id, quantity=1)],
    )
    result = resolve_trade_cancel(
        TradeActionSnapshot(actor=anna, partner=ben, trades=[trade]),
        content,
        rules,
        NOW,
        RNG,
        trade.id,
    )
    assert result.changes.trades[0].status == "cancelled"
    assert [d.inventory_item_id for d in result.changes.trade_offers_delete] == [bread.id]
    assert texts_for(result, ben.id) == ["Anna bricht den Handel ab."]

    request = open_trade(anna, ben, status="requested")
    declined = resolve_trade_cancel(
        TradeActionSnapshot(actor=ben, partner=anna, trades=[request]),
        content,
        rules,
        NOW,
        RNG,
        request.id,
    )
    assert texts_for(declined, anna.id) == ["Ben lehnt den Handel ab."]


def test_check_ends_broken_trades_only(content: ContentRegistry, rules: Rules) -> None:
    anna, ben = traders()
    trade = open_trade(anna, ben)
    fine = resolve_trade_check(
        TradeActionSnapshot(actor=anna, partner=ben, trades=[trade]), content, rules, NOW, RNG
    )
    assert fine.changes.trades == []

    ben.current_room_id = "town_bank"  # walked away
    moved = resolve_trade_check(
        TradeActionSnapshot(actor=ben, partner=anna, trades=[trade]), content, rules, NOW, RNG
    )
    assert moved.changes.trades[0].status == "cancelled"
    assert texts_for(moved, anna.id) == ["Der Handel wurde abgebrochen."]

    ben.current_room_id = ROOM
    request = open_trade(anna, ben, status="requested", expires_at=NOW)
    expired = resolve_trade_check(
        TradeActionSnapshot(actor=anna, partner=ben, trades=[request]), content, rules, NOW, RNG
    )
    assert texts_for(expired, anna.id) == ["Die Handelsanfrage ist abgelaufen."]
