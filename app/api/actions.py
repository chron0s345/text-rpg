"""``POST /actions``: one game action of the active character (ARCHITECTURE.md, "Aktionen").

Rejections by the game rules (wrong state, too fast, ...) are not HTTP errors but events in the
response. The actor gets its results here; everyone else via SSE.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Request, status

from app.api.deps import PlayerId, ServicesDep, not_playing
from app.api.schemas import ActionResponse, GameAction, LogoutAction
from app.engine.presence import LOGGED_OUT
from app.services.action_service import NotPlayingError, RetryLimitExceededError

router = APIRouter(tags=["game"])


@router.post("/actions")
async def act(
    action: Annotated[GameAction, Body()],
    request: Request,
    player_id: PlayerId,
    services: ServicesDep,
) -> ActionResponse:
    try:
        outcome = await services.actions.execute(player_id, action)
    except NotPlayingError as exc:
        raise not_playing(request) from exc
    except RetryLimitExceededError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Zu viel los gerade, versuche es noch einmal."
        ) from exc
    if isinstance(action, LogoutAction) and any(e.kind == LOGGED_OUT for e in outcome.events):
        request.session.pop("player_id", None)  # back to character selection
    return ActionResponse.of(outcome.events)
