"""FastAPI app. Lifespan: load content, connect the database and the event bus, wire services.

``create_app`` takes overrides for tests (database, clock, RNG); uvicorn uses ``app``.
The world ticker runs as a background task (only one process ticks, see services/world_ticker.py).
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from app.api import actions, auth, characters, stream, views
from app.api.deps import Services
from app.content.loader import load_content
from app.db.session import create_engine, create_session_factory
from app.realtime.bus import PostgresEventBus
from app.realtime.connections import ConnectionManager
from app.services.account_service import AccountService
from app.services.action_service import ActionService, utc_now
from app.services.login_guard import LoginGuard, ThrottlePolicy
from app.services.session_service import SessionService
from app.services.view_service import ViewService
from app.services.world_ticker import WorldTicker
from app.settings import Settings
from app.web.render import STATIC_DIR

logger = logging.getLogger(__name__)

SESSION_COOKIE = "rpg_session"
SESSION_MAX_AGE_SECONDS = 14 * 24 * 3600


def login_guard(
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    clock: Callable[[], datetime],
) -> LoginGuard:
    window = timedelta(seconds=settings.login_window_seconds)
    lockout = timedelta(seconds=settings.login_lockout_seconds)
    return LoginGuard(
        sessions,
        clock=clock,
        per_user=ThrottlePolicy(settings.login_attempts_per_user, window, lockout),
        per_address=ThrottlePolicy(settings.login_attempts_per_address, window, lockout),
        registrations_per_address=ThrottlePolicy(
            settings.registrations_per_address, window, lockout
        ),
    )


def create_app(
    settings: Settings | None = None,
    *,
    clock: Callable[[], datetime] = utc_now,
    rng_factory: Callable[[], random.Random] = random.SystemRandom,
) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        content = load_content(Path(settings.data_dir), icons_dir=Path(settings.icons_dir))
        app.state.content = content
        engine = create_engine(settings.database_url)
        sessions = create_session_factory(engine)
        connections = ConnectionManager()
        bus = PostgresEventBus(settings.database_url, connections)
        await bus.start()
        actions_service = ActionService(
            sessions, content, bus, clock=clock, rng_factory=rng_factory
        )
        presence = SessionService(sessions, actions_service, clock=clock)
        guard = login_guard(settings, sessions, clock)
        app.state.services = Services(
            content=content,
            sessions=sessions,
            accounts=AccountService(sessions, content, clock=clock, guard=guard),
            actions=actions_service,
            presence=presence,
            views=ViewService(sessions, content, clock=clock),
            connections=connections,
            heartbeat_seconds=settings.heartbeat_seconds,
        )
        # nobody is connected to a fresh process: the disconnect grace period applies
        marked = await presence.mark_all_disconnected()
        if marked:
            logger.info("marked %d online characters as disconnected", marked)
        stop = asyncio.Event()
        ticker_task: asyncio.Task[None] | None = None
        if settings.world_ticker:
            ticker = WorldTicker(
                engine,
                sessions,
                content,
                actions_service,
                bus,
                clock=clock,
                rng_factory=rng_factory,
                login_guard=guard,
            )
            ticker_task = asyncio.create_task(ticker.run(stop), name="world-ticker")
        try:
            yield
        finally:
            stop.set()
            if ticker_task is not None:
                await ticker_task
            await bus.stop()
            await engine.dispose()

    app = FastAPI(
        title="Text-RPG",
        description="Multiplayer text-based RPG backend",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie=SESSION_COOKIE,
        max_age=SESSION_MAX_AGE_SECONDS,
        same_site="lax",
        https_only=settings.https_only,
    )
    app.include_router(auth.router)
    app.include_router(characters.router)
    app.include_router(actions.router)
    app.include_router(stream.router)
    app.include_router(views.router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/health")
    async def health_check() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
