"""Crafting (ARCHITECTURE.md, "Handwerk"): a crafter turns ingredients and a fee into an item.

Recipes belong to a merchant and are crafted where that merchant stands. Ingredients come
from the free backpack (not equipped, not offered in a trade); the result must fit, otherwise
nothing is taken.
"""

from __future__ import annotations

import random
from datetime import datetime

from app.content.registry import ContentRegistry
from app.content.schemas import RecipeTemplate, Rules
from app.engine.events import ActionResult, ResultBuilder, rejected
from app.engine.materials import count_available, give_all_or_nothing, quantity_text, take_items
from app.engine.permissions import check_state
from app.engine.state import ActionSnapshot, PlayerSnapshot


def missing_ingredients(recipe: RecipeTemplate, actor: PlayerSnapshot) -> list[tuple[str, int]]:
    """(item_id, how many are missing) for every ingredient the backpack lacks."""
    return [
        (i.item_id, i.quantity - have)
        for i in recipe.ingredients
        if (have := count_available(actor, i.item_id)) < i.quantity
    ]


def resolve_craft(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    recipe_id: str,
) -> ActionResult:
    actor = snapshot.actor
    if denied := check_state("craft", actor):
        return denied
    recipe = content.recipes.get(recipe_id)
    if recipe is None:
        return rejected("Dieses Rezept gibt es nicht.")
    crafter = content.merchants[recipe.crafter_id]
    result = content.get_item(recipe.item_id)
    if crafter.room_id != actor.current_room_id:
        return rejected(f"{crafter.name} ist nicht hier.")
    if actor.level < recipe.min_level:
        return rejected(f"{result.name} kannst du erst ab Stufe {recipe.min_level} herstellen.")
    if missing := missing_ingredients(recipe, actor):
        names = ", ".join(quantity_text(content, item_id, n) for item_id, n in missing)
        return rejected(f"Für {result.name} fehlt dir noch: {names}.")
    if actor.gold < recipe.gold:
        return rejected(f"{crafter.name} verlangt {recipe.gold} Gold, du hast {actor.gold}.")

    builder = ResultBuilder(actor_id=actor.id)
    for ingredient in recipe.ingredients:
        take_items(builder, actor, ingredient.item_id, ingredient.quantity)
    if not give_all_or_nothing(
        builder, actor, [(recipe.item_id, recipe.quantity)], content, rules, rng
    ):
        return rejected(f"Für {result.name} ist kein Platz im Inventar.")
    if recipe.gold:
        actor.gold -= recipe.gold
        builder.update_player(actor.id, gold=actor.gold)
    fee = f" für {recipe.gold} Gold" if recipe.gold else ""
    builder.to_actor(
        "craft.done",
        f"{crafter.name} stellt dir {quantity_text(content, recipe.item_id, recipe.quantity)}"
        f"{fee} her.",
        recipe_id=recipe.id,
    )
    return builder.build()
