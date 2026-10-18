from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, PositiveInt, field_validator

from app.content.schemas import Direction
from app.engine.events import GameEvent


class MoveAction(BaseModel):
    type: Literal["move"] = "move"
    direction: Direction


class AttackAction(BaseModel):
    type: Literal["attack"] = "attack"
    target_id: UUID


class FleeAction(BaseModel):
    type: Literal["flee"] = "flee"


class TravelAction(BaseModel):
    type: Literal["travel"] = "travel"
    room_id: str = Field(min_length=1, max_length=64)


class UseItemAction(BaseModel):
    type: Literal["use_item"] = "use_item"
    inventory_item_id: UUID


class EquipAction(BaseModel):
    type: Literal["equip"] = "equip"
    inventory_item_id: UUID


class EquipBestAction(BaseModel):
    type: Literal["equip_best"] = "equip_best"


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


class GroupSayAction(BaseModel):
    type: Literal["group_say"] = "group_say"
    text: str = Field(min_length=1, max_length=300)


class WhisperAction(BaseModel):
    type: Literal["whisper"] = "whisper"
    player_name: str = Field(min_length=1, max_length=20)
    text: str = Field(min_length=1, max_length=300)


class GroupInviteAction(BaseModel):
    type: Literal["group_invite"] = "group_invite"
    player_name: str = Field(min_length=1, max_length=20)


class GroupAcceptAction(BaseModel):
    type: Literal["group_accept"] = "group_accept"
    inviter_id: UUID  # the invitation may predate the group (created on accept)


class GroupDeclineAction(BaseModel):
    type: Literal["group_decline"] = "group_decline"
    inviter_id: UUID


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


class BankDepositMaterialsAction(BaseModel):
    type: Literal["bank_deposit_materials"] = "bank_deposit_materials"


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


class SellJunkAction(BaseModel):
    type: Literal["sell_junk"] = "sell_junk"
    merchant_id: str = Field(min_length=1, max_length=64)


class RespawnAction(BaseModel):
    type: Literal["respawn"] = "respawn"


class LookAction(BaseModel):
    type: Literal["look"] = "look"


class RestAction(BaseModel):
    type: Literal["rest"] = "rest"


class InventoryAction(BaseModel):
    type: Literal["inventory"] = "inventory"


class QuestAcceptAction(BaseModel):
    type: Literal["quest_accept"] = "quest_accept"
    quest_id: str = Field(min_length=1, max_length=64)


class QuestTurnInAction(BaseModel):
    type: Literal["quest_turn_in"] = "quest_turn_in"
    quest_id: str = Field(min_length=1, max_length=64)


class QuestAbandonAction(BaseModel):
    type: Literal["quest_abandon"] = "quest_abandon"
    quest_id: str = Field(min_length=1, max_length=64)


class CraftAction(BaseModel):
    type: Literal["craft"] = "craft"
    recipe_id: str = Field(min_length=1, max_length=64)


class InspectAction(BaseModel):
    type: Literal["inspect"] = "inspect"
    item_id: str = Field(min_length=1, max_length=64)


class InspectPlayerAction(BaseModel):
    type: Literal["inspect_player"] = "inspect_player"
    player_id: UUID


class InspectMonsterAction(BaseModel):
    type: Literal["inspect_monster"] = "inspect_monster"
    monster_id: UUID


class SetTitleAction(BaseModel):
    type: Literal["set_title"] = "set_title"
    achievement_id: str | None = Field(default=None, max_length=64)  # None/"": no title

    @field_validator("achievement_id", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        return None if value == "" else value


class LogoutAction(BaseModel):
    type: Literal["logout"] = "logout"
    confirm_death: bool = False


GameAction = Annotated[
    MoveAction
    | TravelAction
    | AttackAction
    | FleeAction
    | UseItemAction
    | EquipAction
    | EquipBestAction
    | UnequipAction
    | PickUpAction
    | DropAction
    | SayAction
    | GroupSayAction
    | WhisperAction
    | GroupInviteAction
    | GroupAcceptAction
    | GroupDeclineAction
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
    | BankDepositMaterialsAction
    | BankWithdrawAction
    | BuyAction
    | SellAction
    | SellJunkAction
    | RespawnAction
    | LookAction
    | RestAction
    | InventoryAction
    | QuestAcceptAction
    | QuestTurnInAction
    | QuestAbandonAction
    | CraftAction
    | InspectAction
    | InspectPlayerAction
    | InspectMonsterAction
    | SetTitleAction
    | LogoutAction,
    Field(discriminator="type"),
]


# --- requests & responses of the HTTP API (app/api/) ------------------------------------------


class Credentials(BaseModel):
    username: str = Field(max_length=32)
    password: str = Field(max_length=128)


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=128)
    new_password: str = Field(max_length=128)
    confirm_death: bool = False  # a character of the account is in combat


class AccountOut(BaseModel):
    id: UUID
    username: str


class CharacterCreate(BaseModel):
    name: str = Field(max_length=20)


class CharacterOut(BaseModel):
    id: UUID
    name: str
    level: int
    state: str
    room_id: str
    online: bool


class PlayRequest(BaseModel):
    confirm_death: bool = False  # another character of the account is in combat


class LogoutRequest(BaseModel):
    confirm_death: bool = False  # the active character is in combat


class EventOut(BaseModel):
    """A game event as the client sees it (audience/routing details stay on the server)."""

    kind: str
    text: str
    data: dict[str, Any] = Field(default_factory=dict)


class ActionResponse(BaseModel):
    events: list[EventOut]

    @classmethod
    def of(cls, events: Sequence[GameEvent]) -> ActionResponse:
        return cls(events=[EventOut(kind=e.kind, text=e.text, data=e.data) for e in events])


class LogEntryOut(BaseModel):
    id: int
    kind: str
    text: str
    created_at: datetime


class LogPage(BaseModel):
    entries: list[LogEntryOut]  # oldest first
