"""SessionVersionGuard on its own: which session cookies survive (no database needed)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

from starlette.types import Receive, Scope, Send

from app.api.session_guard import SessionVersionGuard

ACCOUNT = uuid4()


class Accounts:
    def __init__(self, versions: dict[UUID, int]) -> None:
        self.versions = versions
        self.lookups = 0

    async def session_version(self, account_id: UUID) -> int | None:
        self.lookups += 1
        return self.versions.get(account_id)


async def seen_by_route(
    session: dict[str, Any], accounts: Accounts, path: str = "/select"
) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    async def route(scope: Scope, receive: Receive, send: Send) -> None:
        seen.update(scope["session"])

    app = SimpleNamespace(state=SimpleNamespace(services=SimpleNamespace(accounts=accounts)))
    scope = {"type": "http", "path": path, "session": session, "app": app}

    async def receive() -> Any:
        raise AssertionError

    async def send(message: Any) -> None:
        raise AssertionError

    await SessionVersionGuard(route)(scope, receive, send)
    return seen


async def test_current_version_passes() -> None:
    session = {"account_id": str(ACCOUNT), "sv": 2, "player_id": "x"}
    assert await seen_by_route(dict(session), Accounts({ACCOUNT: 2})) == session


async def test_cookie_without_version_counts_as_version_zero() -> None:
    old = {"account_id": str(ACCOUNT)}
    assert await seen_by_route(dict(old), Accounts({ACCOUNT: 0})) == old
    assert await seen_by_route(dict(old), Accounts({ACCOUNT: 1})) == {}


async def test_outdated_unknown_or_broken_sessions_are_emptied() -> None:
    accounts = Accounts({ACCOUNT: 3})
    assert await seen_by_route({"account_id": str(ACCOUNT), "sv": 2}, accounts) == {}
    assert await seen_by_route({"account_id": str(uuid4()), "sv": 0}, accounts) == {}
    assert await seen_by_route({"account_id": "kein-uuid"}, accounts) == {}


async def test_static_files_and_anonymous_requests_cost_no_lookup() -> None:
    accounts = Accounts({})
    session = {"account_id": str(ACCOUNT)}
    assert await seen_by_route(dict(session), accounts, "/static/style.css") == session
    assert await seen_by_route({}, accounts) == {}
    assert accounts.lookups == 0
