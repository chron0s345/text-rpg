"""Service exceptions -> HTTP status codes (ARCHITECTURE.md, "Fehler & Schutz").

Actions that are not allowed in the current state are no HTTP errors: they come back as
``action.rejected`` events in a normal response.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.api.deps import NO_CHARACTER
from app.services.account_service import (
    CharacterLimitError,
    CharacterNameTakenError,
    CharacterOnlineError,
    InvalidCredentialsError,
    UsernameTakenError,
)
from app.services.action_service import NotPlayingError
from app.services.session_service import UnknownCharacterError
from app.services.transactions import RetryLimitExceededError

ERRORS: dict[type[Exception], tuple[int, str]] = {
    NotPlayingError: (status.HTTP_401_UNAUTHORIZED, NO_CHARACTER),
    InvalidCredentialsError: (status.HTTP_401_UNAUTHORIZED, "Benutzername oder Passwort falsch."),
    UnknownCharacterError: (status.HTTP_404_NOT_FOUND, "Diesen Charakter gibt es nicht."),
    UsernameTakenError: (status.HTTP_409_CONFLICT, "Dieser Benutzername ist vergeben."),
    CharacterNameTakenError: (status.HTTP_409_CONFLICT, "Dieser Name ist vergeben."),
    CharacterLimitError: (
        status.HTTP_409_CONFLICT,
        "Du hast bereits die maximale Anzahl Charaktere.",
    ),
    CharacterOnlineError: (
        status.HTTP_409_CONFLICT,
        "Ein Charakter im Spiel kann nicht gelöscht werden.",
    ),
    RetryLimitExceededError: (
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "Gerade ist viel los. Bitte versuche es noch einmal.",
    ),
}


def install_error_handlers(app: FastAPI) -> None:
    for exc_type, (code, detail) in ERRORS.items():

        async def handler(
            request: Request, exc: Exception, code: int = code, detail: str = detail
        ) -> JSONResponse:
            return JSONResponse({"detail": detail}, status_code=code)

        app.add_exception_handler(exc_type, handler)
