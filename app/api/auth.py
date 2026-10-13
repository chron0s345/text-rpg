"""Registration, login and logout of an account."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from app.api.deps import ServicesDep, current_account_id, session_player_id
from app.api.schemas import AccountOut, Credentials, LogoutRequest
from app.services.account_service import AccountError, UsernameTakenError

router = APIRouter(tags=["auth"])


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(body: Credentials, request: Request, services: ServicesDep) -> AccountOut:
    try:
        account = await services.accounts.register(body.username, body.password)
    except UsernameTakenError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except AccountError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    request.session.clear()
    request.session["account_id"] = str(account.id)
    return AccountOut(id=account.id, username=account.username)


@router.post("/login")
async def login(body: Credentials, request: Request, services: ServicesDep) -> AccountOut:
    account = await services.accounts.authenticate(body.username, body.password)
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
