"""The chat field of the game page: prefixes pick room, group or whisper (parser + route)."""

from __future__ import annotations

import pytest

from app.web.chat import HELP, chat_action
from tests.api.conftest import Api
from tests.api.test_views import playing


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hallo zusammen", {"type": "say", "text": "Hallo zusammen"}),
        ("  /g  Sammeln am Tor ", {"type": "group_say", "text": " Sammeln am Tor"}),
        ("/Gruppe los", {"type": "group_say", "text": "los"}),
        (
            "/w Ben Treffen um acht",
            {"type": "whisper", "player_name": "Ben", "text": "Treffen um acht"},
        ),
        ("/flüstern Ben hi", {"type": "whisper", "player_name": "Ben", "text": "hi"}),
        ("/me winkt fröhlich", {"type": "emote", "text": "winkt fröhlich"}),
        ("/ICH gähnt", {"type": "emote", "text": "gähnt"}),
        ("/me  ", "Was möchtest du tun?"),
        ("/g", "Was möchtest du sagen?"),
        ("/w Ben", "Flüstern: /w Name Text"),
        ("/w", "Flüstern: /w Name Text"),
        ("/tanz", f"Unbekannter Befehl. {HELP}"),
    ],
)
def test_chat_action(text: str, expected: dict[str, str] | str) -> None:
    assert chat_action(text) == expected


async def test_chat_field_whispers_and_explains(api: Api) -> None:
    async with api.client() as anna, api.client() as ben:
        await playing(api, anna)
        ben_name = await playing(api, ben)
        api.clock.advance(seconds=1)

        whispered = await anna.post(
            "/ui/actions", data={"type": "chat", "text": f"/w {ben_name} psst"}
        )
        assert (
            f'<li class="log-chat-whisper">Du flüsterst {ben_name} zu: psst</li>' in whispered.text
        )

        api.clock.advance(seconds=1)
        lonely = await anna.post("/ui/actions", data={"type": "chat", "text": "/g hallo"})
        assert "Du bist in keiner Gruppe." in lonely.text

        unknown = await anna.post("/ui/actions", data={"type": "chat", "text": "/tanz"})
        assert "Unbekannter Befehl." in unknown.text

        api.clock.advance(seconds=1)
        said = await anna.post("/ui/actions", data={"type": "chat", "text": "Hallo"})
        assert "Du sagst: Hallo" in said.text

        api.clock.advance(seconds=1)
        waved = await anna.post("/ui/actions", data={"type": "chat", "text": "/me winkt"})
        assert '<li class="log-chat-emote">' in waved.text

        page = await ben.get("/")
        assert "flüstert dir zu: psst" in page.text  # in Ben's log
        assert " winkt</li>" in page.text
