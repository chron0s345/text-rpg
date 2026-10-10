"""Shared dependencies of the routes: services from the app state, session cookie contents.

The session cookie (signed, ``SessionMiddleware``) holds ``account_id`` and, once a character
was chosen, ``player_id``. Whether that character is really in the world is decided by the
database (``Player.online``), never by the cookie.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.content.registry import ContentRegistry
from app.realtime.connections import ConnectionManager
from app.services.account_service import AccountService
from app.services.action_service import ActionService
from app.services.session_service import SessionService


@dataclass
class Services:
    content: ContentRegistry
    sessions: async_sessionmaker[AsyncSession]
    accounts: AccountService
    actions: ActionService
    presence: SessionService
    connections: ConnectionManager
    heartbeat_seconds: float
    # SSE cleanup tasks scheduled from finished streams (kept so they are not garbage collected)
    background: set[object] = field(default_factory=set)


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


def _uuid(value: object) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except ValueError:
        return None


def current_account_id(request: Request) -> UUID:
    account_id = _uuid(request.session.get("account_id"))
    if account_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Nicht eingeloggt.")
    return account_id


def session_player_id(request: Request) -> UUID | None:
    return _uuid(request.session.get("player_id"))


def current_player_id(request: Request, _account: AccountId) -> UUID:
    player_id = session_player_id(request)
    if player_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Kein Charakter ausgewählt.")
    return player_id


ServicesDep = Annotated[Services, Depends(get_services)]
AccountId = Annotated[UUID, Depends(current_account_id)]
PlayerId = Annotated[UUID, Depends(current_player_id)]


def not_playing(request: Request) -> HTTPException:
    """The cookie names a character that is not in the world (anymore)."""
    request.session.pop("player_id", None)
    return HTTPException(status.HTTP_401_UNAUTHORIZED, "Dein Charakter ist nicht im Spiel.")
