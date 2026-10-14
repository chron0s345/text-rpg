"""Registration, login, logout and password change of an account."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status

from app.api.deps import AccountId, Services, ServicesDep, current_account_id, session_player_id
from app.api.schemas import AccountOut, Credentials, LogoutRequest, PasswordChange
from app.api.session_guard import remember_login
from app.services.account_service import AccountError, UsernameTakenError, WrongPasswordError
from app.services.login_guard import TooManyAttemptsError
from app.services.session_service import ConfirmDeathRequiredError, SessionConflictError

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
    remember_login(request.session, account.id, account.session_version)
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
    remember_login(request.session, account.id, account.session_version)
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


async def change_password(
    request: Request,
    services: Services,
    account_id: UUID,
    current_password: str,
    new_password: str,
    *,
    confirm_death: bool = False,
) -> None:
    """Checks the current password, logs out the account's characters (they may be played from
    a stolen session), stores the new password and keeps only this session logged in.

    Raises ``AccountError``, ``TooManyAttemptsError``, ``ConfirmDeathRequiredError`` and
    ``SessionConflictError``; nothing is changed then."""
    services.accounts.validate_password(new_password)
    await services.accounts.check_password(
        account_id, current_password, address=client_address(request)
    )
    await services.presence.logout_account(account_id, confirm_death=confirm_death)
    version = await services.accounts.set_password(account_id, new_password)
    remember_login(request.session, account_id, version)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def password(
    body: PasswordChange, request: Request, account_id: AccountId, services: ServicesDep
) -> None:
    """All other sessions of the account end; its characters are logged out (in combat only
    with ``confirm_death``, otherwise 409)."""
    try:
        await change_password(
            request,
            services,
            account_id,
            body.current_password,
            body.new_password,
            confirm_death=body.confirm_death,
        )
    except TooManyAttemptsError as exc:
        raise too_many(exc) from exc
    except WrongPasswordError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except AccountError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except (ConfirmDeathRequiredError, SessionConflictError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
