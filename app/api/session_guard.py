"""Ends sessions whose account changed its password (ARCHITECTURE.md, "API").

The session cookie is signed but lives only in the browser, so it cannot be revoked by deleting
it on the server. Instead it carries the account's ``session_version`` (``sv``); a password
change raises the version in the database, and this middleware empties every session cookie
with an older one before a route sees it: the request continues as "not logged in".

Pure ASGI middleware between ``SessionMiddleware`` (which fills ``scope["session"]``) and the
routes; costs one primary key lookup per request with a logged-in cookie (none for static files).
"""

from __future__ import annotations

from typing import Any, Final
from uuid import UUID

from starlette.types import ASGIApp, Receive, Scope, Send

SESSION_VERSION: Final = "sv"
SKIPPED_PREFIXES: Final = ("/static/", "/health")


def remember_login(session: dict[str, Any], account_id: UUID, version: int) -> None:
    """A fresh session for the account (after login, registration or a password change)."""
    session.clear()
    session["account_id"] = str(account_id)
    session[SESSION_VERSION] = version


class SessionVersionGuard:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and not scope["path"].startswith(SKIPPED_PREFIXES):
            session: dict[str, Any] = scope.get("session") or {}
            if session.get("account_id") and not await self._valid(scope, session):
                session.clear()
        await self.app(scope, receive, send)

    async def _valid(self, scope: Scope, session: dict[str, Any]) -> bool:
        try:
            account_id = UUID(str(session["account_id"]))
        except ValueError:
            return False
        accounts = scope["app"].state.services.accounts
        version = await accounts.session_version(account_id)
        # cookies from before session versions existed carry none: version 0
        return version is not None and version == session.get(SESSION_VERSION, 0)
