from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI

from app.content.loader import load_content
from app.content.registry import ContentRegistry

content_registry: ContentRegistry | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global content_registry
    data_dir = Path("data")
    content_registry = load_content(data_dir)
    app.state.content = content_registry
    yield
    # Shutdown logic (event bus, ticker, etc.)


app = FastAPI(
    title="Text-RPG",
    description="Multiplayer text-based RPG backend",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}
