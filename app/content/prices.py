"""Merchant prices (ARCHITECTURE.md, "Händler"). Used by the content validation (no money loop)
and by the engine, so both always agree."""

from __future__ import annotations

import math

from app.content.schemas import ItemTemplate, MerchantOffer, MerchantTemplate, Rules


def buy_price(offer: MerchantOffer, item: ItemTemplate, rules: Rules) -> int:
    """What the player pays per unit: the offer's price, else ``value × buy_ratio`` (never 0)."""
    if offer.price is not None:
        return offer.price
    return max(1, int(item.value * rules.merchant.buy_ratio))


def merchant_buys(merchant: MerchantTemplate, item: ItemTemplate) -> bool:
    """Whether the merchant takes this kind of item at all (``sellable`` aside)."""
    return merchant.buys == "all" or item.kind in merchant.buys_kinds


def sell_price(item: ItemTemplate, merchant: MerchantTemplate, rules: Rules) -> int | None:
    """What the player gets per unit: ``floor(value × sell_ratio)``; ``None`` if this merchant
    does not take the item."""
    if not item.sellable or not merchant_buys(merchant, item):
        return None
    ratio = merchant.sell_ratio if merchant.sell_ratio is not None else rules.merchant.sell_ratio
    return math.floor(item.value * ratio)
