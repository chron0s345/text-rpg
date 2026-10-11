"""Shared route dependencies: the app's services and who is asking.

Routes stay thin (ARCHITECTURE.md, "Abhängigkeitsregeln"): they validate input, resolve the
account/character and call a service. No game logic here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.cookies import COOKIE_NAME, read_session, sign_session
from app.content.registry import ContentRegistry
from app.realtime.connections import ConnectionManager
from app.services.account_service import AccountService
from app.services.action_service import ActionService
from app.services.session_service import SessionService
from app.settings import Settings

NOT_LOGGED_IN = "Nicht angemeldet."
NO_CHARACTER = "Kein Charakter im Spiel."


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class AppServices:
    """Everything the routes need; built once in the app lifespan (``app.state.services``)."""

    settings: Settings
    content: ContentRegistry
    sessions: async_sessionmaker[AsyncSession]
    accounts: AccountService
    actions: ActionService
    presence: SessionService
    connections: ConnectionManager
    clock: Callable[[], datetime] = utc_now


def get_services(request: Request) -> AppServices:
    services: AppServices = request.app.state.services
    return services


Services = Annotated[AppServices, Depends(get_services)]


async def current_account(request: Request, services: Services) -> UUID:
    """401 without a valid session cookie (or for an account that no longer exists)."""
    account_id = read_session(
        services.settings.secret_key, request.cookies.get(COOKIE_NAME), services.clock()
    )
    if account_id is None or not await services.accounts.account_exists(account_id):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, NOT_LOGGED_IN)
    return account_id


CurrentAccount = Annotated[UUID, Depends(current_account)]


async def current_player(account_id: CurrentAccount, services: Services) -> UUID:
    """The account's character in the world; 401 if none is (or it was logged out meanwhile)."""
    player_id = await services.accounts.active_character(account_id)
    if player_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, NO_CHARACTER)
    return player_id


CurrentPlayer = Annotated[UUID, Depends(current_player)]


def set_session_cookie(response: Response, services: AppServices, account_id: UUID) -> None:
    settings = services.settings
    expires_at = services.clock() + timedelta(seconds=settings.session_max_age_seconds)
    response.set_cookie(
        COOKIE_NAME,
        sign_session(settings.secret_key, account_id, expires_at),
        max_age=settings.session_max_age_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )


def clear_session_cookie(response: Response, services: AppServices) -> None:
    response.delete_cookie(
        COOKIE_NAME, httponly=True, secure=services.settings.cookie_secure, samesite="lax"
    )
