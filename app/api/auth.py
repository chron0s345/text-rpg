"""Accounts: register, log in, log out (session cookie)."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from app.api.cookies import COOKIE_NAME, read_session
from app.api.deps import Services, clear_session_cookie, set_session_cookie
from app.api.schemas import (
    AccountResponse,
    ActionResponse,
    ConfirmDeathRequest,
    LoginRequest,
    RegisterRequest,
)

router = APIRouter(tags=["auth"])


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest, response: Response, services: Services
) -> AccountResponse:
    account_id = await services.accounts.register(body.username, body.password)
    set_session_cookie(response, services, account_id)
    return AccountResponse(account_id=account_id)


@router.post("/login")
async def login(body: LoginRequest, response: Response, services: Services) -> AccountResponse:
    account_id = await services.accounts.authenticate(body.username, body.password)
    set_session_cookie(response, services, account_id)
    return AccountResponse(account_id=account_id)


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    services: Services,
    body: ConfirmDeathRequest | None = None,
) -> ActionResponse:
    """Ends the account session; the character in the world is logged out first (in combat
    only after confirmation, otherwise the cookie stays and the client asks)."""
    account_id = read_session(
        services.settings.secret_key, request.cookies.get(COOKIE_NAME), services.clock()
    )
    if account_id is not None:
        player_id = await services.accounts.active_character(account_id)
        if player_id is not None:
            confirm = body.confirm_death if body is not None else False
            outcome = await services.presence.logout(player_id, confirm_death=confirm)
            if outcome.status == "confirm_death":
                return ActionResponse(status="confirm_death", events=outcome.events)
            clear_session_cookie(response, services)
            return ActionResponse(events=outcome.events)
    clear_session_cookie(response, services)
    return ActionResponse()
