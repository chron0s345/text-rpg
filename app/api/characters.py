"""Character selection: list, create, play (log in), delete."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.api.deps import AccountId, ServicesDep, session_player_id
from app.api.schemas import (
    ActionResponse,
    CharacterCreate,
    CharacterOut,
    ClassChoice,
    PlayRequest,
)
from app.services.account_service import (
    AccountError,
    CharacterInfo,
    CharacterLimitError,
    CharacterNameTakenError,
    CharacterNotFoundError,
    CharacterOnlineError,
    ClassLockedError,
)
from app.services.session_service import ConfirmDeathRequiredError, SessionConflictError

router = APIRouter(prefix="/characters", tags=["characters"])


def _out(info: CharacterInfo) -> CharacterOut:
    return CharacterOut(
        id=info.id,
        name=info.name,
        level=info.level,
        state=info.state,
        room_id=info.room_id,
        online=info.online,
        avatar=info.avatar,
        class_id=info.class_id,
        can_choose_class=info.can_choose_class,
    )


@router.get("")
async def list_characters(account_id: AccountId, services: ServicesDep) -> list[CharacterOut]:
    return [_out(c) for c in await services.accounts.list_characters(account_id)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_character(
    body: CharacterCreate, account_id: AccountId, services: ServicesDep
) -> CharacterOut:
    try:
        return _out(
            await services.accounts.create_character(
                account_id, body.name, body.avatar, body.class_id
            )
        )
    except (CharacterNameTakenError, CharacterLimitError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except AccountError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.post("/{character_id}/play")
async def play(
    character_id: UUID,
    request: Request,
    account_id: AccountId,
    services: ServicesDep,
    body: PlayRequest | None = None,
) -> ActionResponse:
    """Logs the character in (and another character of the account out). 409 if that other
    character is in combat and ``confirm_death`` is not set."""
    try:
        outcome = await services.presence.play(
            account_id, character_id, confirm_death=body.confirm_death if body else False
        )
    except CharacterNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (ConfirmDeathRequiredError, SessionConflictError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    request.session["player_id"] = str(character_id)
    return ActionResponse.of(outcome.events)


@router.post("/{character_id}/class")
async def choose_class(
    character_id: UUID, body: ClassChoice, account_id: AccountId, services: ServicesDep
) -> CharacterOut:
    """An adventurer (the start class) picks its class, once; not in a fight."""
    try:
        return _out(await services.accounts.choose_class(account_id, character_id, body.class_id))
    except CharacterNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ClassLockedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except AccountError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.delete("/{character_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_character(
    character_id: UUID, request: Request, account_id: AccountId, services: ServicesDep
) -> Response:
    try:
        await services.accounts.delete_character(account_id, character_id)
    except CharacterNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except CharacterOnlineError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if session_player_id(request) == character_id:
        request.session.pop("player_id", None)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
