"""FastAPI app: wiring of content, database, event bus, services, routes and the world ticker.

Lifespan (ARCHITECTURE.md, "Projektstruktur"):
1. load and validate content (startup fails on broken YAML),
2. database engine + session factory,
3. event bus (LISTEN/NOTIFY) delivering to this process's SSE connections,
4. services; online characters get the disconnect grace period (server start),
5. world ticker as background task (only the advisory-lock leader actually ticks).
Shutdown runs in reverse order.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import actions, auth, characters, stream
from app.api.deps import AppServices
from app.api.errors import install_error_handlers
from app.content.loader import load_content
from app.db.session import create_engine, create_session_factory
from app.realtime.bus import PostgresEventBus
from app.realtime.connections import ConnectionManager
from app.services.account_service import AccountService
from app.services.action_service import ActionService
from app.services.session_service import SessionService
from app.services.world_ticker import WorldTicker
from app.settings import Settings

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        config = settings or Settings.from_env()
        content = load_content(config.data_dir, icons_dir=config.icons_dir)
        app.state.content = content
        engine = create_engine(config.database_url)
        sessions = create_session_factory(engine)
        connections = ConnectionManager()
        bus = PostgresEventBus(config.database_url, connections)
        ticker_task: asyncio.Task[None] | None = None
        try:
            await bus.start()
            presence = SessionService(sessions, content, bus)
            app.state.services = AppServices(
                settings=config,
                content=content,
                sessions=sessions,
                accounts=AccountService(sessions, content),
                actions=ActionService(sessions, content, bus),
                presence=presence,
                connections=connections,
            )
            marked = await presence.mark_all_disconnected()
            if marked:
                logger.info("%d online characters wait for their connection", marked)
            if config.start_world_ticker:
                ticker = WorldTicker(engine, sessions, content, bus, session_service=presence)
                ticker_task = asyncio.create_task(ticker.run(), name="world-ticker")
            yield
        finally:
            if ticker_task is not None:
                ticker_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await ticker_task
            await bus.stop()
            await engine.dispose()

    app = FastAPI(
        title="Text-RPG",
        description="Multiplayer text-based RPG backend",
        version="0.1.0",
        lifespan=lifespan,
    )
    install_error_handlers(app)
    for module in (auth, characters, actions, stream):
        app.include_router(module.router)

    @app.get("/health")
    async def health_check() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
