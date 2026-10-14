"""The chat field of the game page: plain text is said in the room, a prefix picks another
channel (``/g Text`` group, ``/w Name Text`` whisper). Turns the input into action fields."""

from __future__ import annotations

from typing import Final

HELP: Final = "Befehle: /g Text (Gruppe), /w Name Text (flüstern)."
GROUP_PREFIXES: Final = ("/g", "/gruppe")
WHISPER_PREFIXES: Final = ("/w", "/flüstern")


def chat_action(text: str) -> dict[str, str] | str:
    """Action fields for ``text``, or a message for the player if the command is incomplete."""
    stripped = text.strip()
    if not stripped.startswith("/"):
        return {"type": "say", "text": text}
    command, _, rest = stripped.partition(" ")
    command = command.lower()
    if command in GROUP_PREFIXES:
        return {"type": "group_say", "text": rest} if rest.strip() else "Was möchtest du sagen?"
    if command in WHISPER_PREFIXES:
        name, _, message = rest.strip().partition(" ")
        if not name or not message.strip():
            return "Flüstern: /w Name Text"
        return {"type": "whisper", "player_name": name, "text": message}
    return f"Unbekannter Befehl. {HELP}"
