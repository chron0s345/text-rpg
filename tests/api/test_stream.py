"""SSE end to end: a real uvicorn server, several clients, events over PostgreSQL NOTIFY.

ASGITransport buffers whole responses, so streams need a real HTTP server here.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import uvicorn
from sqlalchemy import update
from sqlmodel import col

from app.db.models import Player
from tests.api.conftest import Api

TIMEOUT = 5.0


@dataclass
class Server:
    url: str


@pytest.fixture
async def server(api: Api) -> AsyncIterator[Server]:
    # the api fixture already runs the lifespan; uvicorn must not start it a second time
    config = uvicorn.Config(api.app, host="127.0.0.1", port=0, lifespan="off", log_level="warning")
    instance = uvicorn.Server(config)
    task = asyncio.create_task(instance.serve())
    while not instance.started:
        await asyncio.sleep(0.01)
    port = instance.servers[0].sockets[0].getsockname()[1]
    try:
        yield Server(f"http://127.0.0.1:{port}")
    finally:
        instance.should_exit = True
        await task


class Stream:
    """Reads SSE messages of one ``GET /stream`` response."""

    def __init__(self, response: httpx.Response) -> None:
        self.lines = response.aiter_lines()

    async def next(self, timeout: float = TIMEOUT) -> tuple[str, Any]:
        """Next named event (comments such as heartbeats are skipped)."""
        return await asyncio.wait_for(self._next(), timeout)

    async def _next(self) -> tuple[str, Any]:
        name, data = None, []
        async for line in self.lines:
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data.append(line.removeprefix("data: "))
            elif line == "" and name is not None:
                return name, json.loads("\n".join(data))
        raise EOFError

    async def nothing_for(self, seconds: float) -> None:
        with pytest.raises(TimeoutError):
            await self.next(timeout=seconds)


async def joined(api: Api, server: Server, room: str | None = None) -> httpx.AsyncClient:
    client = httpx.AsyncClient(base_url=server.url, timeout=TIMEOUT)
    character_id = await api.player(client)
    if room is not None:
        async with api.committed.session() as db, db.begin():
            await db.execute(
                update(Player).where(col(Player.id) == character_id).values(current_room_id=room)
            )
    return client


async def test_events_reach_the_room_but_not_bystanders(api: Api, server: Server) -> None:
    speaker = await joined(api, server)
    listener = await joined(api, server)
    elsewhere = await joined(api, server, room="town_market")
    try:
        async with (
            listener.stream("GET", "/stream") as listening,
            elsewhere.stream("GET", "/stream") as away,
            speaker.stream("GET", "/stream") as own,
        ):
            assert listening.headers["content-type"].startswith("text/event-stream")
            heard, far, mine = Stream(listening), Stream(away), Stream(own)
            api.clock.advance(seconds=1)
            response = await speaker.post("/actions", json={"type": "say", "text": "<b>Hallo</b>"})
            assert response.json()["events"][0]["text"] == "Du sagst: <b>Hallo</b>"

            name, data = await heard.next()
            assert name == "log"
            assert data["kind"] == "chat.say"
            assert data["text"].endswith("sagt: <b>Hallo</b>")  # escaping is the renderer's job
            await far.nothing_for(0.5)
            await mine.nothing_for(0.5)  # the actor got it with the HTTP response
    finally:
        for client in (speaker, listener, elsewhere):
            await client.aclose()


async def test_stream_ends_when_the_character_leaves(api: Api, server: Server) -> None:
    client = await joined(api, server)
    try:
        async with client.stream("GET", "/stream") as response:
            stream = Stream(response)
            await client.post("/actions", json={"type": "logout"})
            assert await stream.next() == ("ended", {})  # noticed on the next heartbeat
        assert (await client.get("/stream")).status_code == 401
    finally:
        await client.aclose()


async def test_closing_the_last_stream_starts_the_grace_period(api: Api, server: Server) -> None:
    client = await joined(api, server)
    try:
        async with client.stream("GET", "/stream") as response:
            await Stream(response).nothing_for(0.3)
        characters = (await client.get("/characters")).json()
        character_id = characters[0]["id"]
        row: Player | None = None
        for _ in range(100):
            async with api.committed.session() as db:
                row = await db.get(Player, character_id)
            assert row is not None
            if row.disconnected_at is not None:
                break
            await asyncio.sleep(0.05)
        assert row is not None
        assert row.disconnected_at == api.clock.now
        assert row.online  # still in the world until the grace period ends

        async with client.stream("GET", "/stream") as response:  # reconnect
            await Stream(response).nothing_for(0.3)
            async with api.committed.session() as db:
                row = await db.get(Player, character_id)
                assert row is not None and row.disconnected_at is None
    finally:
        await client.aclose()
