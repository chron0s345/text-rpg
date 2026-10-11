"""POST /actions: one game action of the character in the world."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentPlayer, Services
from app.api.schemas import ActionResponse, GameAction, LogoutAction

router = APIRouter(tags=["actions"])


@router.post("/actions")
async def act(action: GameAction, player_id: CurrentPlayer, services: Services) -> ActionResponse:
    """The acting player's results come back here; everybody else gets them via SSE. Actions
    not allowed right now are answered with ``action.rejected`` events, not HTTP errors."""
    if isinstance(action, LogoutAction):
        # the account session (cookie) stays: the client returns to the character selection
        outcome = await services.presence.logout(player_id, confirm_death=action.confirm_death)
        return ActionResponse(status=outcome.status, events=outcome.events)
    result = await services.actions.execute(player_id, action)
    return ActionResponse(events=result.events)
