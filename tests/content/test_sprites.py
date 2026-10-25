"""Monster portraits: every monster has one, the committed SVGs match data/sprites.yaml."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.content.loader import load_content
from app.content.sprites import (
    SIZE,
    SpriteColors,
    SpriteSheet,
    load_sprites,
    palette,
    render_all,
    render_svg,
)
from app.web.render import STATIC_DIR
from tests.conftest import DATA_DIR

CONTENT = load_content(DATA_DIR)
SHEET = load_sprites(DATA_DIR / "sprites.yaml")
SPRITES_DIR = STATIC_DIR / "sprites"


def test_every_monster_has_a_portrait() -> None:
    assert set(SHEET.monsters) == set(CONTENT.monsters)


def test_committed_svgs_match_the_yaml() -> None:
    """After changing data/sprites.yaml or a monster's name: python -m app.content.sprites"""
    names = {m.id: m.name for m in CONTENT.monsters.values()}
    expected = render_all(SHEET, names)
    on_disk = {p.name: p.read_text(encoding="utf-8") for p in Path(SPRITES_DIR).glob("*.svg")}
    assert on_disk.keys() == expected.keys()
    stale = [name for name, svg in expected.items() if on_disk[name] != svg]
    assert stale == []


def test_an_svg_is_one_rect_per_run_of_a_color() -> None:
    rows = ["." * SIZE] * (SIZE - 1) + ["oobb.e" + "." * (SIZE - 6)]
    colors = SpriteColors(shape="x", body="#336699", eye="#ffffff")
    svg = render_svg(rows, colors, "Maus <klein>")
    assert svg.startswith('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"')
    assert "<title>Maus &lt;klein&gt;</title>" in svg
    assert svg.count("<rect") == 3
    assert '<rect x="0" y="15" width="2" height="1" fill="#120d1e"/>' in svg
    assert '<rect x="2" y="15" width="2" height="1" fill="#336699"/>' in svg
    assert '<rect x="5" y="15" width="1" height="1" fill="#ffffff"/>' in svg


def test_shades_are_derived_unless_given() -> None:
    derived = palette(SpriteColors(shape="x", body="#808080"))
    assert (derived["d"], derived["l"]) == ("#535353", "#acacac")
    own = palette(SpriteColors(shape="x", body="#808080", shade="#010203", light="#fefdfc"))
    assert (own["d"], own["l"]) == ("#010203", "#fefdfc")


@pytest.mark.parametrize(
    ("sheet", "error"),
    [
        ({"shapes": {"x": ["." * SIZE] * (SIZE - 1)}}, "must be 16×16"),
        ({"shapes": {"x": ["." * (SIZE - 1) + "?"] + ["." * SIZE] * (SIZE - 1)}}, "unknown pixels"),
        ({"shapes": {}, "monsters": {"m": {"shape": "nope", "body": "#000000"}}}, "unknown shape"),
        (
            {"shapes": {}, "monsters": {}, "x": 1}
            | {"monsters": {"m": {"shape": "x", "body": "red"}}},
            "#a1b2c3",
        ),
    ],
)
def test_broken_sheets_are_rejected(sheet: dict[str, object], error: str) -> None:
    with pytest.raises(ValidationError, match=error):
        SpriteSheet.model_validate(sheet)
