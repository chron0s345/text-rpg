"""Server announcements (python -m app.admin announce): every connection in every process gets
them, they are rendered as log lines and do not make clients reload their panels."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from app.admin import ANNOUNCEMENT, MAX_LENGTH, announce, announcement
from app.api.stream import encode_game_event
from app.realtime.bus import PostgresEventBus
from app.realtime.connections import ConnectionManager


async def test_announcement_reaches_everybody_everywhere(database_url: str) -> None:
    server_a = PostgresEventBus(database_url, ConnectionManager())
    server_b = PostgresEventBus(database_url, ConnectionManager())
    operator = PostgresEventBus(database_url, ConnectionManager())  # sends only, like the CLI
    await server_a.start()
    await server_b.start()
    try:
        here = server_a.manager.subscribe(uuid4(), "town_square")
        there = server_b.manager.subscribe(uuid4(), "old_mine", group_id=uuid4())

        await announce(operator, "  Neustart in   5 Minuten. ")

        for subscription in (here, there):
            event = await asyncio.wait_for(subscription.queue.get(), timeout=5)
            assert (event.kind, event.text) == (ANNOUNCEMENT, "Ankündigung: Neustart in 5 Minuten.")
    finally:
        await operator.stop()
        await server_a.stop()
        await server_b.stop()


def test_announcement_text_is_checked() -> None:
    with pytest.raises(ValueError):
        announcement("   ")
    with pytest.raises(ValueError):
        announcement("x" * (MAX_LENGTH + 1))


def test_announcement_is_a_highlighted_log_line_without_panel_refresh() -> None:
    message = encode_game_event(announcement("<b>Wartung</b>"))

    assert message.startswith("event: log\n")
    assert 'class="log-server-announcement"' in message
    assert "&lt;b&gt;Wartung&lt;/b&gt;" in message  # escaped like every log line
    assert "event: update" not in message
