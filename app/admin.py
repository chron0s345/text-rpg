"""Operator commands (ARCHITECTURE.md, "Echtzeit": channel ``global`` for server announcements).

    python -m app.admin announce "Neustart in 5 Minuten."

An announcement reaches every connected player in every server process (PostgreSQL NOTIFY via the
event bus) and is highlighted in their log. Like every ``Global`` event it is not stored per
player: whoever is offline or reconnects later does not see it.
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Final

from app.db.session import database_url
from app.engine.events import GameEvent, GlobalAudience
from app.realtime.bus import PostgresEventBus
from app.realtime.connections import ConnectionManager
from app.services.events import EventPublisher

ANNOUNCEMENT: Final = "server.announcement"
MAX_LENGTH: Final = 300


def announcement(text: str) -> GameEvent:
    text = " ".join(text.split())
    if not text:
        raise ValueError("Die Ankündigung ist leer.")
    if len(text) > MAX_LENGTH:
        raise ValueError(f"Höchstens {MAX_LENGTH} Zeichen.")
    return GameEvent(kind=ANNOUNCEMENT, audience=GlobalAudience(), text=f"Ankündigung: {text}")


async def announce(publisher: EventPublisher, text: str) -> GameEvent:
    event = announcement(text)
    await publisher.publish([event])
    return event


async def _main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.admin", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    announce_cmd = commands.add_parser("announce", help="Nachricht an alle verbundenen Spieler")
    announce_cmd.add_argument("text", help=f"höchstens {MAX_LENGTH} Zeichen")
    args = parser.parse_args(argv)

    if args.command == "announce":
        try:
            event = announcement(args.text)
        except ValueError as exc:
            parser.error(str(exc))
        # only sends: no listener needed, the server processes deliver it to their connections
        bus = PostgresEventBus(database_url(), ConnectionManager())
        try:
            await bus.publish([event])
        finally:
            await bus.stop()
        print(f"Gesendet: {event.text}")


if __name__ == "__main__":
    asyncio.run(_main())
