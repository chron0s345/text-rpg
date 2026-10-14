"""Pages and HTMX partials (ARCHITECTURE.md, "Frontend"). Thin like the JSON routes: they call the
same services and only render.

Pages use plain forms with POST-redirect-GET (``/welcome``, ``/select``); the game page talks to
``POST /ui/actions`` via HTMX: the response holds the actor's log lines (appended to the log) plus
out-of-band swaps of all panels, so the view always matches the stored state.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import TypeAdapter, ValidationError

from app.api.auth import change_password, client_address
from app.api.deps import Services, ServicesDep, session_player_id
from app.api.schemas import GameAction, LogoutAction
from app.api.session_guard import remember_login
from app.engine.events import GameEvent
from app.engine.presence import LOGGED_OUT
from app.services.account_service import (
    AccountError,
    CharacterNotFoundError,
    CharacterOnlineError,
)
from app.services.action_service import NotPlayingError, RetryLimitExceededError
from app.services.login_guard import TooManyAttemptsError
from app.services.session_service import ConfirmDeathRequiredError, SessionConflictError
from app.web.chat import chat_action
from app.web.render import log_line, templates

router = APIRouter(include_in_schema=False)

ACTION = TypeAdapter(GameAction)


def _account_id(request: Request) -> UUID | None:
    try:
        value = request.session.get("account_id")
        return UUID(str(value)) if value else None
    except ValueError:
        return None


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


def _hx_redirect(url: str) -> Response:
    """Full page navigation from an HTMX request."""
    return Response(status_code=status.HTTP_200_OK, headers={"HX-Redirect": url})


# --- welcome: login & registration -----------------------------------------------------------


@router.get("/welcome", response_class=HTMLResponse)
async def welcome(request: Request) -> Response:
    if _account_id(request) is not None:
        return _redirect("/select")
    return templates.TemplateResponse(request, "welcome.html", {})


async def _form(request: Request) -> dict[str, str]:
    form = await request.form()
    return {key: value for key, value in form.items() if isinstance(value, str)}


def _too_many(request: Request, exc: TooManyAttemptsError, username: str) -> Response:
    response = templates.TemplateResponse(
        request,
        "welcome.html",
        {"error": exc.message, "username": username},
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
    )
    response.headers["Retry-After"] = str(max(1, int(exc.retry_after.total_seconds())))
    return response


@router.post("/welcome/login")
async def welcome_login(request: Request, services: ServicesDep) -> Response:
    form = await _form(request)
    username = form.get("username", "")[:32]
    try:
        account = await services.accounts.login(
            username, form.get("password", "")[:128], address=client_address(request)
        )
    except TooManyAttemptsError as exc:
        return _too_many(request, exc, username)
    if account is None:
        return templates.TemplateResponse(
            request,
            "welcome.html",
            {"error": "Benutzername oder Passwort falsch.", "username": username},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    remember_login(request.session, account.id, account.session_version)
    return _redirect("/select")


@router.post("/welcome/register")
async def welcome_register(request: Request, services: ServicesDep) -> Response:
    form = await _form(request)
    try:
        account = await services.accounts.register(
            form.get("username", "")[:32],
            form.get("password", "")[:128],
            address=client_address(request),
        )
    except TooManyAttemptsError as exc:
        return _too_many(request, exc, "")
    except AccountError as exc:
        return templates.TemplateResponse(
            request, "welcome.html", {"error": str(exc)}, status_code=status.HTTP_400_BAD_REQUEST
        )
    remember_login(request.session, account.id, account.session_version)
    return _redirect("/select")


# --- character selection ---------------------------------------------------------------------


async def _select_page(
    request: Request,
    services: Services,
    account_id: UUID,
    *,
    error: str | None = None,
    notice: str | None = None,
    confirm: dict[str, Any] | None = None,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    characters = await services.accounts.list_characters(account_id)
    return templates.TemplateResponse(
        request,
        "select.html",
        {
            "characters": characters,
            "can_create": len(characters) < services.content.rules.max_characters_per_account,
            "error": error,
            "notice": notice,
            "confirm": confirm,
        },
        status_code=status_code,
    )


@router.get("/select", response_class=HTMLResponse)
async def select(request: Request, services: ServicesDep) -> Response:
    account_id = _account_id(request)
    if account_id is None:
        return _redirect("/welcome")
    notice = "Passwort geändert." if request.query_params.get("passwort") == "neu" else None
    return await _select_page(request, services, account_id, notice=notice)


@router.post("/select/password")
async def select_password(request: Request, services: ServicesDep) -> Response:
    account_id = _account_id(request)
    if account_id is None:
        return _redirect("/welcome")
    form = await _form(request)
    try:
        await change_password(
            request,
            services,
            account_id,
            form.get("current_password", "")[:128],
            form.get("new_password", "")[:128],
        )
    except TooManyAttemptsError as exc:
        error, code = exc.message, status.HTTP_429_TOO_MANY_REQUESTS
    except AccountError as exc:
        error, code = str(exc), status.HTTP_400_BAD_REQUEST
    except ConfirmDeathRequiredError as exc:
        error = f"{exc.name} ist im Kampf. Beende den Kampf oder logge ihn im Spiel aus."
        code = status.HTTP_409_CONFLICT
    except SessionConflictError as exc:
        error, code = str(exc), status.HTTP_409_CONFLICT
    else:
        return _redirect("/select?passwort=neu")
    return await _select_page(request, services, account_id, error=error, status_code=code)


@router.post("/select/new")
async def select_new(request: Request, services: ServicesDep) -> Response:
    account_id = _account_id(request)
    if account_id is None:
        return _redirect("/welcome")
    form = await _form(request)
    try:
        await services.accounts.create_character(account_id, form.get("name", "")[:20])
    except AccountError as exc:
        return await _select_page(
            request, services, account_id, error=str(exc), status_code=status.HTTP_400_BAD_REQUEST
        )
    return _redirect("/select")


@router.post("/select/{character_id}/play")
async def select_play(character_id: UUID, request: Request, services: ServicesDep) -> Response:
    account_id = _account_id(request)
    if account_id is None:
        return _redirect("/welcome")
    form = await _form(request)
    try:
        await services.presence.play(
            account_id, character_id, confirm_death=form.get("confirm_death") == "true"
        )
    except ConfirmDeathRequiredError as exc:
        return await _select_page(
            request,
            services,
            account_id,
            confirm={"id": character_id, "message": str(exc)},
        )
    except (CharacterNotFoundError, SessionConflictError) as exc:
        return await _select_page(
            request, services, account_id, error=str(exc), status_code=status.HTTP_409_CONFLICT
        )
    request.session["player_id"] = str(character_id)
    return _redirect("/")


@router.post("/select/{character_id}/delete")
async def select_delete(character_id: UUID, request: Request, services: ServicesDep) -> Response:
    account_id = _account_id(request)
    if account_id is None:
        return _redirect("/welcome")
    try:
        await services.accounts.delete_character(account_id, character_id)
    except (CharacterNotFoundError, CharacterOnlineError) as exc:
        return await _select_page(
            request, services, account_id, error=str(exc), status_code=status.HTTP_409_CONFLICT
        )
    if session_player_id(request) == character_id:
        request.session.pop("player_id", None)
    return _redirect("/select")


@router.post("/select/logout")
async def select_logout(request: Request, services: ServicesDep) -> Response:
    """Ends the account session. A character still in the world logs out first; in combat the
    game page's logout (with its confirmation) is the way out."""
    account_id = _account_id(request)
    player_id = session_player_id(request)
    if account_id is not None and player_id is not None:
        result = await services.presence.logout(player_id)
        if result.status != "logged_out":
            return await _select_page(
                request,
                services,
                account_id,
                error="Dein Charakter ist im Kampf. Logge ihn im Spiel aus (er stirbt dabei).",
                status_code=status.HTTP_409_CONFLICT,
            )
    request.session.clear()
    return _redirect("/welcome")


# --- the game --------------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
async def game(request: Request, services: ServicesDep) -> Response:
    if _account_id(request) is None:
        return _redirect("/welcome")
    player_id = session_player_id(request)
    view = await services.views.game(player_id) if player_id is not None else None
    if player_id is None or view is None:
        request.session.pop("player_id", None)
        return _redirect("/select")
    log = await services.views.log(player_id)
    return templates.TemplateResponse(request, "game.html", {"view": view, "log": log})


async def _panels(request: Request, services: Services, player_id: UUID) -> str | None:
    view = await services.views.game(player_id)
    if view is None:
        return None
    template = templates.get_template("partials/panels.html")
    return template.render(view=view, oob=True, request=request)


def _lines(events: list[GameEvent]) -> str:
    return "\n".join(log_line(e.kind, e.text) for e in events)


@router.post("/ui/actions", response_class=HTMLResponse)
async def ui_action(request: Request, services: ServicesDep) -> Response:
    """Form-encoded action from the game page -> log lines + all panels (out of band)."""
    player_id = session_player_id(request)
    if _account_id(request) is None or player_id is None:
        return _hx_redirect("/welcome")
    form = await _form(request)
    if form.get("type") == "chat":  # the chat field: room, group or whisper
        chosen = chat_action(form.get("text", ""))
        if isinstance(chosen, str):
            return HTMLResponse(log_line("action.rejected", chosen))
        form = chosen
    try:
        action = ACTION.validate_python({k: v for k, v in form.items() if v != ""})
    except ValidationError:
        return HTMLResponse(log_line("action.rejected", "Das verstehe ich nicht."))
    try:
        outcome = await services.actions.execute(player_id, action)
    except NotPlayingError:
        request.session.pop("player_id", None)
        return _hx_redirect("/select")
    except RetryLimitExceededError:
        return HTMLResponse(
            log_line("action.rejected", "Zu viel los gerade, versuche es noch einmal.")
        )
    if isinstance(action, LogoutAction) and any(e.kind == LOGGED_OUT for e in outcome.events):
        request.session.pop("player_id", None)
        return _hx_redirect("/select")
    panels = await _panels(request, services, player_id)
    return HTMLResponse(_lines(outcome.events) + "\n" + (panels or ""))


@router.get("/ui/panels", response_class=HTMLResponse)
async def ui_panels(request: Request, services: ServicesDep) -> Response:
    """All panels (out of band); requested after SSE ``update`` messages and on reconnect."""
    player_id = session_player_id(request)
    panels = await _panels(request, services, player_id) if player_id is not None else None
    if panels is None:
        return _hx_redirect("/select")
    return HTMLResponse(panels)


@router.get("/ui/log", response_class=HTMLResponse)
async def ui_log(request: Request, services: ServicesDep) -> Response:
    """The latest log lines (replaces the log after a reconnect)."""
    player_id = session_player_id(request)
    if player_id is None:
        return _hx_redirect("/select")
    lines = await services.views.log(player_id)
    return HTMLResponse("\n".join(log_line(line.kind, line.text) for line in lines))
