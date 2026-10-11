"""Runtime configuration from the environment (no balancing values here: those live in data/)."""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from app.db.session import database_url

logger = logging.getLogger(__name__)


def _flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str
    # signs the session cookie; must be the same for all workers, or cookies of one worker are
    # rejected by the others
    secret_key: str
    data_dir: Path = Path("data")
    icons_dir: Path = Path("app/web/static/icons")
    cookie_secure: bool = False  # set in production (HTTPS only)
    session_max_age_seconds: int = 7 * 24 * 3600
    start_world_ticker: bool = True

    @classmethod
    def from_env(cls) -> Settings:
        secret = os.environ.get("SECRET_KEY")
        if not secret:
            # fine for a single dev process; with several workers every worker gets its own key
            logger.warning("SECRET_KEY is not set; using a random key (sessions end on restart)")
            secret = secrets.token_urlsafe(32)
        return cls(
            database_url=database_url(),
            secret_key=secret,
            cookie_secure=_flag("COOKIE_SECURE", False),
            start_world_ticker=_flag("WORLD_TICKER", True),
        )
