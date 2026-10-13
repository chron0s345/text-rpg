"""Runtime configuration from environment variables."""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass, field

from app.db.session import database_url

logger = logging.getLogger(__name__)


def _session_secret() -> str:
    secret = os.environ.get("SESSION_SECRET")
    if secret:
        return secret
    # Fine for a single dev process; with several workers or restarts, sessions break.
    logger.warning("SESSION_SECRET is not set; using a random secret (sessions end on restart)")
    return secrets.token_urlsafe(32)


def _int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value else default


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=database_url)
    session_secret: str = field(default_factory=_session_secret)
    # Secure flag on the session cookie; enable behind HTTPS (production)
    https_only: bool = field(
        default_factory=lambda: os.environ.get("SESSION_HTTPS_ONLY", "") == "1"
    )
    data_dir: str = "data"
    icons_dir: str = "app/web/static/icons"
    heartbeat_seconds: float = 15.0
    # background upkeep (respawns, cleanup, disconnect deadlines, ambushes); WORLD_TICKER=0 disables
    world_ticker: bool = field(default_factory=lambda: os.environ.get("WORLD_TICKER", "1") != "0")
    # login protection (services/login_guard.py): attempts per window, then a lock
    login_attempts_per_user: int = field(default_factory=lambda: _int("LOGIN_ATTEMPTS_PER_USER", 5))
    login_attempts_per_address: int = field(
        default_factory=lambda: _int("LOGIN_ATTEMPTS_PER_ADDRESS", 30)
    )
    registrations_per_address: int = field(
        default_factory=lambda: _int("REGISTRATIONS_PER_ADDRESS", 10)
    )
    login_window_seconds: int = field(default_factory=lambda: _int("LOGIN_WINDOW_SECONDS", 900))
    login_lockout_seconds: int = field(default_factory=lambda: _int("LOGIN_LOCKOUT_SECONDS", 900))
