"""Kill credit & rewards when a monster dies (ARCHITECTURE.md, "Kill-Credit & Beute")."""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from uuid import UUID

from app.content.registry import ContentRegistry
from app.content.schemas import MonsterTemplate, Rules
from app.engine.events import (
    FloorItemCreate,
    GroupAudience,
    GroupUpdate,
    ResultBuilder,
    RoomAudience,
)
from app.engine.inventory import add_item_to_inventory, calculate_inventory_capacity
from app.engine.loot import distribute_gold, distribute_xp, roll_monster_gold, roll_monster_loot
from app.engine.progression import apply_xp
from app.engine.state import (
    ActionSnapshot,
    GroupSnapshot,
    MonsterSnapshot,
    PlayerSnapshot,
    new_id,
)


def party_members_in_room(
    snapshot: ActionSnapshot, party_key: str
) -> tuple[list[PlayerSnapshot], GroupSnapshot | None]:
    """Living members of a party in the room, in round-robin order (group join order)."""
    alive = [p for p in snapshot.players_in_room if p.state != "dead"]
    if party_key.startswith("solo:"):
        player_id = UUID(party_key.removeprefix("solo:"))
        return [p for p in alive if p.id == player_id], None

    group_id = UUID(party_key.removeprefix("group:"))
    members = [p for p in alive if p.group_id == group_id]
    group = snapshot.group(group_id)
    if group is None:
        return sorted(members, key=lambda p: p.id), None
    order = {pid: index for index, pid in enumerate(group.member_ids)}
    return sorted(members, key=lambda p: (order.get(p.id, len(order)), p.id)), group


def resolve_kill(
    builder: ResultBuilder,
    snapshot: ActionSnapshot,
    monster: MonsterSnapshot,
    template: MonsterTemplate,
    killer: PlayerSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
) -> None:
    """Distributes XP, gold and items, ends the fight for all participants, schedules respawn."""
    builder.event(
        "monster.killed",
        RoomAudience(room_id=monster.room_id),
        f"{killer.name} hat {template.name} besiegt.",
        exclude=[killer.id],
        monster_instance_id=str(monster.id),
    )
    builder.to_actor("monster.killed", f"Du hast {template.name} besiegt.")

    _grant_xp(builder, snapshot, monster, template, content, rules)

    tag = monster.tagged_by_party or killer.party_key
    recipients, group = party_members_in_room(snapshot, tag)
    cursor = group.loot_cursor if group is not None else 0
    cursor = _grant_gold(builder, recipients, template, cursor, rng)
    cursor = _grant_items(builder, recipients, monster, template, content, rules, cursor, now, rng)
    if group is not None and cursor != group.loot_cursor:
        group.loot_cursor = cursor
        builder.changes.groups.append(GroupUpdate(group_id=group.id, loot_cursor=cursor))

    for participation in snapshot.participations_for(monster.id):
        participant = snapshot.player(participation.player_id)
        if participant is not None and participant.combat_target_id == monster.id:
            participant.state = "exploring"
            participant.combat_target_id = None
            builder.update_player(participant.id, state="exploring", combat_target_id=None)
    snapshot.participations = [
        p for p in snapshot.participations if p.monster_instance_id != monster.id
    ]
    builder.changes.participations_clear_monster_ids.append(monster.id)

    monster.hp = 0
    monster.alive = False
    monster.tagged_by_party = None
    monster.last_combat_at = now
    monster.respawn_at = now + timedelta(seconds=template.respawn_seconds)
    builder.update_monster(
        monster.id,
        hp=0,
        alive=False,
        tagged_by_party=None,
        last_combat_at=now,
        respawn_at=monster.respawn_at,
    )


def _grant_xp(
    builder: ResultBuilder,
    snapshot: ActionSnapshot,
    monster: MonsterSnapshot,
    template: MonsterTemplate,
    content: ContentRegistry,
    rules: Rules,
) -> None:
    """Share by damage dealt; idle group members in the room get ``group_idle_share`` of the
    average attacker damage as weight, also when a non-member landed the killing blow."""
    weights: dict[UUID, float] = {}
    for participation in snapshot.participations_for(monster.id):
        player = snapshot.player(participation.player_id)
        if player is not None and player.state != "dead":
            weights[player.id] = weights.get(player.id, 0.0) + participation.damage_dealt
    if not weights:
        return

    attacking_groups = {
        p.group_id
        for pid in weights
        if (p := snapshot.player(pid)) is not None and p.group_id is not None
    }
    idle_weight = sum(weights.values()) / len(weights) * rules.xp.group_idle_share
    for player in snapshot.players_in_room:
        if (
            player.id not in weights
            and player.state != "dead"
            and player.group_id in attacking_groups
            and idle_weight > 0
        ):
            weights[player.id] = idle_weight

    for player_id, amount in distribute_xp(template.xp_reward, weights).items():
        player = snapshot.player(player_id)
        if player is not None:
            apply_xp(builder, player, amount, rules, content.items)


def _grant_gold(
    builder: ResultBuilder,
    recipients: list[PlayerSnapshot],
    template: MonsterTemplate,
    cursor: int,
    rng: random.Random,
) -> int:
    """Even split among the tagging party in the room; remainder coins go round-robin."""
    total = roll_monster_gold(template, rng)
    if total <= 0 or not recipients:
        return cursor

    count = len(recipients)
    cursor %= count
    shares = distribute_gold(total, [p.id for p in recipients], cursor)
    for player in recipients:
        share = shares[player.id]
        if share <= 0:
            continue
        player.gold += share
        builder.update_player(player.id, gold=player.gold)
        builder.to_player(player.id, "loot.gold", f"Du erhältst {share} Gold.", amount=share)
    return (cursor + total % count) % count


def _grant_items(
    builder: ResultBuilder,
    recipients: list[PlayerSnapshot],
    monster: MonsterSnapshot,
    template: MonsterTemplate,
    content: ContentRegistry,
    rules: Rules,
    cursor: int,
    now: datetime,
    rng: random.Random,
) -> int:
    """Solo: straight into the inventory. Group: round-robin, skipping members without space.
    Whatever fits nowhere lands on the floor (``source = "loot"``)."""
    count = len(recipients)
    for rolled in roll_monster_loot(template, rng):
        item = content.get_item(rolled.item_id)
        remaining = rolled.quantity
        if count:
            cursor %= count
            order = recipients[cursor:] + recipients[:cursor]
            cursor = (cursor + 1) % count
            for player in order:
                capacity = calculate_inventory_capacity(
                    rules.inventory.slots, player.inventory, content.items
                )
                added, touched = add_item_to_inventory(
                    player.inventory, item, remaining, capacity, lambda: new_id(rng)
                )
                if added <= 0:
                    continue
                remaining -= added
                builder.upsert_inventory(player.id, touched)
                label = f"{added}x {item.name}" if added > 1 else item.name
                builder.to_player(
                    player.id, "loot.item", f"Du erhältst {label}.", item_id=item.id, quantity=added
                )
                if player.group_id is not None:
                    builder.event(
                        "loot.item",
                        GroupAudience(group_id=player.group_id),
                        f"{player.name} erhält {label}.",
                        exclude=[player.id],
                    )
                if remaining == 0:
                    break

        if remaining > 0:
            builder.changes.floor_items_create.append(
                FloorItemCreate(
                    id=new_id(rng),
                    room_id=monster.room_id,
                    item_id=item.id,
                    quantity=remaining,
                    source="loot",
                    expires_at=now + timedelta(seconds=rules.timings.floor_item_ttl_seconds),
                )
            )
            builder.event(
                "loot.floor",
                RoomAudience(room_id=monster.room_id),
                f"Inventar voll: {remaining}x {item.name} fällt zu Boden.",
                item_id=item.id,
                quantity=remaining,
            )
    return cursor
