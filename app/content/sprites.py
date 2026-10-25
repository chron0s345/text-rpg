"""Pixel portraits of the monsters (``data/sprites.yaml`` → ``static/sprites/<monster_id>.svg``).

A *shape* is a 16×16 grid of characters, one per pixel; a monster picks a shape and the colors
that fill it, so one goblin shape serves the scout, the warrior and the golden goblin:

- ``.`` transparent, ``o`` outline
- ``b`` body, ``d`` body shade, ``l`` body highlight
- ``e`` eyes, ``w`` white (teeth, bones, eye whites)
- ``a`` accent (weapons, horns, claws), ``A`` its shade
- ``c`` cloth (armor, robes, crowns), ``C`` its shade

Shades and highlights are derived from the base colors unless a monster sets them. The SVG
files are generated (``python -m app.content.sprites``) and committed, so the server only serves
static files; a test checks they match the YAML.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Final

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

SIZE: Final = 16
OUTLINE: Final = "#120d1e"
WHITE: Final = "#f2ecdc"
PIXELS: Final = frozenset(".obdlewaAcC")
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _check_color(value: str | None) -> str | None:
    if value is not None and not HEX.match(value):
        raise ValueError(f"color must look like #a1b2c3, got {value!r}")
    return value


class SpriteColors(BaseModel):
    shape: str
    body: str
    eye: str = "#ffd23f"
    accent: str = "#c9c2b0"
    cloth: str = "#6b4a8a"
    shade: str | None = None  # default: body darkened
    light: str | None = None  # default: body lightened
    outline: str = OUTLINE

    _hex = field_validator("body", "eye", "accent", "cloth", "shade", "light", "outline")(
        _check_color
    )


class SpriteSheet(BaseModel):
    shapes: dict[str, list[str]]
    monsters: dict[str, SpriteColors] = Field(default_factory=dict)

    @field_validator("shapes")
    @classmethod
    def _grids(cls, shapes: dict[str, list[str]]) -> dict[str, list[str]]:
        for name, rows in shapes.items():
            if len(rows) != SIZE or any(len(row) != SIZE for row in rows):
                raise ValueError(f"shape '{name}' must be {SIZE}×{SIZE} pixels")
            unknown = set("".join(rows)) - PIXELS
            if unknown:
                raise ValueError(f"shape '{name}' uses unknown pixels {sorted(unknown)}")
        return shapes

    @model_validator(mode="after")
    def _known_shapes(self) -> SpriteSheet:
        for monster_id, colors in self.monsters.items():
            if colors.shape not in self.shapes:
                raise ValueError(f"sprite '{monster_id}' uses unknown shape '{colors.shape}'")
        return self


def load_sprites(path: Path) -> SpriteSheet:
    with path.open(encoding="utf-8") as fh:
        return SpriteSheet.model_validate(yaml.safe_load(fh))


def _mix(color: str, other: str, share: float) -> str:
    """``color`` moved ``share`` of the way towards ``other``."""
    a = [int(color[i : i + 2], 16) for i in (1, 3, 5)]
    b = [int(other[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * share):02x}" for x, y in zip(a, b, strict=True))


def palette(colors: SpriteColors) -> dict[str, str]:
    return {
        "o": colors.outline,
        "b": colors.body,
        "d": colors.shade or _mix(colors.body, "#000000", 0.35),
        "l": colors.light or _mix(colors.body, "#ffffff", 0.35),
        "e": colors.eye,
        "w": WHITE,
        "a": colors.accent,
        "A": _mix(colors.accent, "#000000", 0.35),
        "c": colors.cloth,
        "C": _mix(colors.cloth, "#000000", 0.35),
    }


def render_svg(rows: list[str], colors: SpriteColors, title: str) -> str:
    """One ``<rect>`` per horizontal run of a color; crisp edges so it scales as pixels."""
    fills = palette(colors)
    rects: list[str] = []
    for y, row in enumerate(rows):
        x = 0
        while x < SIZE:
            pixel = row[x]
            run = 1
            while x + run < SIZE and row[x + run] == pixel:
                run += 1
            if pixel != ".":
                rects.append(
                    f'<rect x="{x}" y="{y}" width="{run}" height="1" fill="{fills[pixel]}"/>'
                )
            x += run
    title = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}" '
        f'width="{SIZE * 4}" height="{SIZE * 4}" shape-rendering="crispEdges">'
        f"<title>{title}</title>{''.join(rects)}</svg>\n"
    )


def render_all(sheet: SpriteSheet, names: dict[str, str]) -> dict[str, str]:
    """File name → SVG for every monster in ``names`` (id → display name) that has a sprite."""
    return {
        f"{monster_id}.svg": render_svg(sheet.shapes[colors.shape], colors, names[monster_id])
        for monster_id, colors in sheet.monsters.items()
        if monster_id in names
    }


def write_all(files: dict[str, str], out_dir: Path) -> Iterable[str]:
    """Writes the files and removes stale ones; yields what changed."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in sorted(set(p.name for p in out_dir.glob("*.svg")) - files.keys()):
        (out_dir / stale).unlink()
        yield f"removed {stale}"
    for name, svg in sorted(files.items()):
        path = out_dir / name
        if not path.is_file() or path.read_text(encoding="utf-8") != svg:
            path.write_text(svg, encoding="utf-8")
            yield f"wrote {name}"


if __name__ == "__main__":  # pragma: no cover - developer tool
    from app.content.loader import load_content

    root = Path(__file__).resolve().parents[2]
    content = load_content(root / "data")
    sheet = load_sprites(root / "data" / "sprites.yaml")
    names = {m.id: m.name for m in content.monsters.values()}
    for line in write_all(render_all(sheet, names), root / "app/web/static/sprites"):
        print(line)
