"""Room panoramas: every room has one, the committed SVGs match data/scenes.yaml."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.content.loader import load_content
from app.content.scenes import (
    HEIGHT,
    WIDTH,
    Fire,
    Ground,
    Layer,
    SceneBook,
    Sky,
    Trees,
    load_scenes,
    paint,
    render_rooms,
    render_scene,
)
from app.web.render import STATIC_DIR
from tests.conftest import DATA_DIR

CONTENT = load_content(DATA_DIR)
BOOK = load_scenes(DATA_DIR / "scenes.yaml")


def test_every_room_has_a_scene() -> None:
    assert set(BOOK.rooms) == set(CONTENT.rooms)


def test_committed_svgs_match_the_yaml() -> None:
    """After changing data/scenes.yaml or a room's name: python -m app.content.scenes"""
    names = {r.id: r.name for r in CONTENT.rooms.values()}
    expected = render_rooms(BOOK, names)
    on_disk = {
        p.name: p.read_text(encoding="utf-8") for p in Path(STATIC_DIR / "scenes").glob("*.svg")
    }
    assert on_disk.keys() == expected.keys()
    assert [name for name, svg in expected.items() if on_disk[name] != svg] == []


def test_layers_paint_back_to_front_and_the_seed_fixes_the_randomness() -> None:
    layers: list[Layer] = [
        Sky(colors=["#000000"]),
        Trees(count=4, base=20),
        Ground(color="#111111", top=20),
        Fire(x=32, base=20),
    ]
    grid = paint(layers, "dark_forest")
    assert len(grid) == HEIGHT and all(len(row) == WIDTH for row in grid)
    assert grid[0][0] == "#000000"  # sky everywhere above
    assert grid[23][0] == "#111111"  # ground below
    assert grid[19][29] == "#5a3a22"  # the campfire's logs, painted over the ground line
    assert paint(layers, "dark_forest") == grid  # same room, same picture
    assert paint(layers, "bog_path") != grid  # another room, other trees


def test_an_svg_merges_runs_and_escapes_the_title() -> None:
    grid = paint([Sky(colors=["#123456"])], "x")
    svg = render_scene(grid, "Halle <groß>")
    assert svg.count("<rect") == HEIGHT  # one run per row
    assert "<title>Halle &lt;groß&gt;</title>" in svg


@pytest.mark.parametrize(
    ("book", "error"),
    [
        ({"themes": {"t": []}, "rooms": {}}, "has no layers"),
        ({"themes": {}, "rooms": {"r": {"theme": "nope"}}}, "unknown theme"),
        (
            {
                "themes": {
                    "t": [
                        {"type": "art", "x": 0, "y": 0, "rows": ["ab"], "palette": {"a": "#000000"}}
                    ]
                },
                "rooms": {},
            },
            "without a color",
        ),
        ({"themes": {"t": [{"type": "sky", "colors": ["red"]}]}, "rooms": {}}, "pattern"),
    ],
)
def test_broken_books_are_rejected(book: dict[str, object], error: str) -> None:
    with pytest.raises(ValidationError, match=error):
        SceneBook.model_validate(book)
