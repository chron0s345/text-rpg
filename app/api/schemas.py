from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, PositiveInt

from app.content.schemas import Direction, PlayerState
from app.engine.events import GameEvent
from app.services.account_service import (
    CHARACTER_NAME_PATTERN,
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    USERNAME_PATTERN,
)


class MoveAction(BaseModel):
    type: Literal["move"] = "move"
    direction: Direction


class AttackAction(BaseModel):
    type: Literal["attack"] = "attack"
    target_id: UUID


class FleeAction(BaseModel):
    type: Literal["flee"] = "flee"


class UseItemAction(BaseModel):
    type: Literal["use_item"] = "use_item"
    inventory_item_id: UUID


class EquipAction(BaseModel):
    type: Literal["equip"] = "equip"
    inventory_item_id: UUID


class UnequipAction(BaseModel):
    type: Literal["unequip"] = "unequip"
    inventory_item_id: UUID


class PickUpAction(BaseModel):
    type: Literal["pick_up"] = "pick_up"
    floor_item_id: UUID
    quantity: PositiveInt | None = None  # None = as much as fits


class DropAction(BaseModel):
    type: Literal["drop"] = "drop"
    inventory_item_id: UUID
    quantity: PositiveInt | None = None  # None = the whole stack


class SayAction(BaseModel):
    type: Literal["say"] = "say"
    text: str = Field(min_length=1, max_length=300)


class GroupInviteAction(BaseModel):
    type: Literal["group_invite"] = "group_invite"
    player_name: str


class GroupAcceptAction(BaseModel):
    type: Literal["group_accept"] = "group_accept"
    group_id: UUID


class GroupLeaveAction(BaseModel):
    type: Literal["group_leave"] = "group_leave"


class GroupKickAction(BaseModel):
    type: Literal["group_kick"] = "group_kick"
    player_id: UUID


class TradeRequestAction(BaseModel):
    type: Literal["trade_request"] = "trade_request"
    target_player_id: UUID


class TradeAcceptAction(BaseModel):
    type: Literal["trade_accept"] = "trade_accept"
    trade_id: UUID


class TradeOfferItemAction(BaseModel):
    type: Literal["trade_offer_item"] = "trade_offer_item"
    trade_id: UUID
    inventory_item_id: UUID
    quantity: PositiveInt


class TradeRemoveItemAction(BaseModel):
    type: Literal["trade_remove_item"] = "trade_remove_item"
    trade_id: UUID
    inventory_item_id: UUID


class TradeOfferGoldAction(BaseModel):
    type: Literal["trade_offer_gold"] = "trade_offer_gold"
    trade_id: UUID
    amount: int = Field(ge=0)


class TradeConfirmAction(BaseModel):
    type: Literal["trade_confirm"] = "trade_confirm"
    trade_id: UUID
    revision: int


class TradeCancelAction(BaseModel):
    type: Literal["trade_cancel"] = "trade_cancel"
    trade_id: UUID


class BankDepositAction(BaseModel):
    type: Literal["bank_deposit"] = "bank_deposit"
    inventory_item_id: UUID
    quantity: PositiveInt = 1


class BankWithdrawAction(BaseModel):
    type: Literal["bank_withdraw"] = "bank_withdraw"
    bank_item_id: UUID
    quantity: PositiveInt = 1


class BuyAction(BaseModel):
    type: Literal["buy"] = "buy"
    merchant_id: str
    item_id: str
    quantity: PositiveInt = 1


class SellAction(BaseModel):
    type: Literal["sell"] = "sell"
    merchant_id: str
    inventory_item_id: UUID
    quantity: PositiveInt = 1


class RespawnAction(BaseModel):
    type: Literal["respawn"] = "respawn"


class LookAction(BaseModel):
    type: Literal["look"] = "look"


class InventoryAction(BaseModel):
    type: Literal["inventory"] = "inventory"


class LogoutAction(BaseModel):
    type: Literal["logout"] = "logout"
    confirm_death: bool = False


GameAction = Annotated[
    MoveAction
    | AttackAction
    | FleeAction
    | UseItemAction
    | EquipAction
    | UnequipAction
    | PickUpAction
    | DropAction
    | SayAction
    | GroupInviteAction
    | GroupAcceptAction
    | GroupLeaveAction
    | GroupKickAction
    | TradeRequestAction
    | TradeAcceptAction
    | TradeOfferItemAction
    | TradeRemoveItemAction
    | TradeOfferGoldAction
    | TradeConfirmAction
    | TradeCancelAction
    | BankDepositAction
    | BankWithdrawAction
    | BuyAction
    | SellAction
    | RespawnAction
    | LookAction
    | InventoryAction
    | LogoutAction,
    Field(discriminator="type"),
]


# --- accounts, characters, responses ------------------------------------------------------------


class RegisterRequest(BaseModel):
    username: str = Field(pattern=USERNAME_PATTERN.pattern)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class AccountResponse(BaseModel):
    account_id: UUID


class CharacterCreateRequest(BaseModel):
    name: str = Field(pattern=CHARACTER_NAME_PATTERN.pattern)


class CharacterResponse(BaseModel):
    id: UUID
    name: str
    level: int
    state: PlayerState
    online: bool
    room_id: str


class ConfirmDeathRequest(BaseModel):
    """Logging out or switching characters in combat kills the character: confirm it."""

    confirm_death: bool = False


class ActionResponse(BaseModel):
    """``confirm_death``: nothing happened, the client shows the confirmation dialog and sends
    the request again with ``confirm_death: true``."""

    status: Literal["ok", "confirm_death"] = "ok"
    events: list[GameEvent] = Field(default_factory=list)


class LogEntryResponse(BaseModel):
    id: int
    kind: str
    text: str
    created_at: datetime


class LogPage(BaseModel):
    entries: list[LogEntryResponse]  # oldest first
