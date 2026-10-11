"""Character selection: list, create, play (log in), delete."""

from __future__ import annotations

import dataclasses
from uuid import UUID

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentAccount, Services
from app.api.schemas import (
    ActionResponse,
    CharacterCreateRequest,
    CharacterResponse,
    ConfirmDeathRequest,
)
from app.services.account_service import CharacterSummary

router = APIRouter(prefix="/characters", tags=["characters"])


def _response(character: CharacterSummary) -> CharacterResponse:
    return CharacterResponse.model_validate(dataclasses.asdict(character))


@router.get("")
async def list_characters(
    account_id: CurrentAccount, services: Services
) -> list[CharacterResponse]:
    return [_response(c) for c in await services.accounts.characters(account_id)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_character(
    body: CharacterCreateRequest, account_id: CurrentAccount, services: Services
) -> CharacterResponse:
    player_id = await services.accounts.create_character(account_id, body.name)
    created = next(c for c in await services.accounts.characters(account_id) if c.id == player_id)
    return _response(created)


@router.post("/{player_id}/play")
async def play(
    player_id: UUID,
    account_id: CurrentAccount,
    services: Services,
    body: ConfirmDeathRequest | None = None,
) -> ActionResponse:
    """Logs the character in. Another character of the account in combat would die: then the
    answer is ``confirm_death`` and nothing changed."""
    confirm = body.confirm_death if body is not None else False
    outcome = await services.presence.play(account_id, player_id, confirm_death=confirm)
    return ActionResponse(status=outcome.status, events=outcome.events)


@router.delete("/{player_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_character(
    player_id: UUID, account_id: CurrentAccount, services: Services
) -> Response:
    await services.accounts.delete_character(account_id, player_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
