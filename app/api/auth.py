"""Registration, login and logout of an account."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from app.api.deps import ServicesDep, current_account_id, session_player_id
from app.api.schemas import AccountOut, Credentials, LogoutRequest
from app.services.account_service import AccountError, UsernameTakenError
from app.services.login_guard import TooManyAttemptsError

router = APIRouter(tags=["auth"])


def client_address(request: Request) -> str | None:
    """Behind a reverse proxy run uvicorn with ``--proxy-headers`` (and ``--forwarded-allow-ips``),
    otherwise every client has the proxy's address and shares its login limit."""
    return request.client.host if request.client else None


def too_many(exc: TooManyAttemptsError) -> HTTPException:
    seconds = max(1, int(exc.retry_after.total_seconds()))
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS, exc.message, headers={"Retry-After": str(seconds)}
    )


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(body: Credentials, request: Request, services: ServicesDep) -> AccountOut:
    try:
        account = await services.accounts.register(
            body.username, body.password, address=client_address(request)
        )
    except TooManyAttemptsError as exc:
        raise too_many(exc) from exc
    except UsernameTakenError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except AccountError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    request.session.clear()
    request.session["account_id"] = str(account.id)
    return AccountOut(id=account.id, username=account.username)


@router.post("/login")
async def login(body: Credentials, request: Request, services: ServicesDep) -> AccountOut:
    try:
        account = await services.accounts.login(
            body.username, body.password, address=client_address(request)
        )
    except TooManyAttemptsError as exc:
        raise too_many(exc) from exc
    if account is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Benutzername oder Passwort falsch.")
    request.session.clear()
    request.session["account_id"] = str(account.id)
    return AccountOut(id=account.id, username=account.username)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request, services: ServicesDep, body: LogoutRequest | None = None
) -> None:
    """Ends the account session; the active character leaves the world first. In combat this
    needs ``confirm_death`` (the character dies), otherwise 409."""
    current_account_id(request)
    player_id = session_player_id(request)
    if player_id is not None:
        result = await services.presence.logout(
            player_id, confirm_death=body.confirm_death if body else False
        )
        if result.status == "confirm_required":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Dein Charakter ist im Kampf und stirbt, wenn du dich ausloggst. "
                "Bestätige mit confirm_death.",
            )
        if result.status == "rejected":
            raise HTTPException(status.HTTP_409_CONFLICT, "Versuche es gleich noch einmal.")
    request.session.clear()
