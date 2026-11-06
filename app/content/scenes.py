"""Pixel panoramas of the rooms (``data/scenes.yaml`` → ``static/scenes/<room_id>.svg``).

A *theme* is a stack of layers painted back to front on a 64×24 pixel canvas: sky, moon,
mountains, trees, houses, water, cave ceiling, graves, a campfire … Each room picks a theme and
may add layers of its own (a lighthouse, a throne, a bridge). Layers with randomness (where the
trees stand, how jagged the mountains are) draw from a generator seeded with the room id, so
every forest looks a little different but a room always looks the same.

Like the sprites, the SVG files are generated (``python -m app.content.scenes``) and committed;
a test checks they match the YAML and that every room has a scene.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Final, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from app.content.sprites import HEX, write_all

WIDTH: Final = 64
HEIGHT: Final = 24

Color = Annotated[str, Field(pattern=HEX.pattern)]
Span = tuple[int, int]  # inclusive (low, high)
Grid = list[list[str | None]]


class Sky(BaseModel):
    """Bands of color from the top down to row ``until`` (exclusive), dithered where they
    meet."""

    type: Literal["sky"] = "sky"
    colors: list[Color] = Field(min_length=1)
    until: int = HEIGHT


class Stars(BaseModel):
    type: Literal["stars"] = "stars"
    color: Color = "#f2ecdc"
    count: int = 14
    until: int = 10


class Orb(BaseModel):
    """Sun or moon: a disc with an optional ring of glow."""

    type: Literal["orb"] = "orb"
    color: Color
    glow: Color | None = None
    x: int
    y: int
    r: int = 2


class Clouds(BaseModel):
    type: Literal["clouds"] = "clouds"
    color: Color
    count: int = 3
    rows: Span = (2, 8)


class Ridge(BaseModel):
    """Mountains or hills: a random-walk skyline over row ``base``, filled down to the bottom;
    ``cap`` paints the top pixels of each column (snow, embers)."""

    type: Literal["ridge"] = "ridge"
    color: Color
    base: int
    height: Span = (2, 8)
    rough: int = 1
    cap: Color | None = None


class Trees(BaseModel):
    type: Literal["trees"] = "trees"
    kind: Literal["pine", "round", "dead"] = "pine"
    leaf: Color = "#2e5a3a"
    trunk: Color = "#4a3424"
    count: int = 6
    base: int = 20
    height: Span = (6, 10)


class Houses(BaseModel):
    type: Literal["houses"] = "houses"
    wall: Color = "#8a6a4a"
    roof: Color = "#7a2a2a"
    window: Color = "#ffd23f"
    count: int = 4
    base: int = 20


class Columns(BaseModel):
    type: Literal["columns"] = "columns"
    color: Color
    count: int = 4
    base: int = 21
    height: Span = (8, 14)
    broken: bool = False


class Spikes(BaseModel):
    """Stalactites hanging from row ``base`` (``down``) or stalagmites rising from it."""

    type: Literal["spikes"] = "spikes"
    color: Color
    count: int = 10
    base: int = 0
    length: Span = (2, 6)
    direction: Literal["down", "up"] = "down"


class Ground(BaseModel):
    type: Literal["ground"] = "ground"
    color: Color
    top: int = 20
    speckle: Color | None = None
    density: float = 0.12


class Water(BaseModel):
    type: Literal["water"] = "water"
    color: Color
    light: Color
    top: int = 18
    density: float = 0.1


class Graves(BaseModel):
    type: Literal["graves"] = "graves"
    color: Color = "#8a8f9c"
    count: int = 5
    base: int = 20


class Fire(BaseModel):
    type: Literal["fire"] = "fire"
    x: int = 32
    base: int = 21


class Torches(BaseModel):
    type: Literal["torches"] = "torches"
    count: int = 3
    row: int = 9


class Crystals(BaseModel):
    type: Literal["crystals"] = "crystals"
    color: Color
    light: Color
    count: int = 5
    base: int = 21
    height: Span = (3, 6)


class Fog(BaseModel):
    type: Literal["fog"] = "fog"
    color: Color
    rows: Span = (16, 20)
    density: float = 0.35


class Wall(BaseModel):
    """An inside wall of bricks between two rows."""

    type: Literal["wall"] = "wall"
    color: Color
    mortar: Color
    rows: Span = (0, 19)


class Banners(BaseModel):
    type: Literal["banners"] = "banners"
    colors: list[Color] = Field(min_length=1)
    count: int = 3
    top: int = 2
    length: int = 8


class Plants(BaseModel):
    """Grass, wheat or reeds: thin stalks standing on row ``base``."""

    type: Literal["plants"] = "plants"
    color: Color
    tip: Color | None = None
    base: int = 20
    height: Span = (1, 3)
    density: float = 0.5


class Art(BaseModel):
    """A hand-drawn landmark (lighthouse, throne, mill): rows of letters, ``.`` transparent,
    every other letter mapped to a color by ``palette``."""

    type: Literal["art"] = "art"
    x: int
    y: int
    rows: list[str] = Field(min_length=1)
    palette: dict[str, Color]

    @model_validator(mode="after")
    def _known_letters(self) -> Art:
        unknown = set("".join(self.rows)) - set(self.palette) - {"."}
        if unknown:
            raise ValueError(f"art uses letters without a color: {sorted(unknown)}")
        return self


Layer = Annotated[
    Sky
    | Stars
    | Orb
    | Clouds
    | Ridge
    | Trees
    | Houses
    | Columns
    | Spikes
    | Ground
    | Water
    | Graves
    | Fire
    | Torches
    | Crystals
    | Fog
    | Wall
    | Banners
    | Plants
    | Art,
    Field(discriminator="type"),
]


class RoomScene(BaseModel):
    theme: str
    add: list[Layer] = Field(default_factory=list)


class SceneBook(BaseModel):
    themes: dict[str, list[Layer]]
    rooms: dict[str, RoomScene]

    @field_validator("themes")
    @classmethod
    def _non_empty(cls, themes: dict[str, list[Layer]]) -> dict[str, list[Layer]]:
        for name, layers in themes.items():
            if not layers:
                raise ValueError(f"theme '{name}' has no layers")
        return themes

    @model_validator(mode="after")
    def _known_themes(self) -> SceneBook:
        for room_id, scene in self.rooms.items():
            if scene.theme not in self.themes:
                raise ValueError(f"scene of '{room_id}' uses unknown theme '{scene.theme}'")
        return self


def load_scenes(path: Path) -> SceneBook:
    with path.open(encoding="utf-8") as fh:
        return SceneBook.model_validate(yaml.safe_load(fh))


# --- painting ---------------------------------------------------------------------------------


def _put(grid: Grid, x: int, y: int, color: str | None) -> None:
    if 0 <= x < WIDTH and 0 <= y < HEIGHT and color is not None:
        grid[y][x] = color


def _fill_down(grid: Grid, x: int, top: int, color: str) -> None:
    for y in range(max(0, top), HEIGHT):
        _put(grid, x, y, color)


def _sky(grid: Grid, layer: Sky, rng: random.Random) -> None:
    bands = len(layer.colors)
    for y in range(min(layer.until, HEIGHT)):
        position = y * bands / max(1, layer.until)
        index = min(bands - 1, int(position))
        # dither the last row of a band into the next one
        if index + 1 < bands and position - index > 0.75 and (y % 2 == 0):
            for x in range(WIDTH):
                _put(grid, x, y, layer.colors[index + (x % 2)])
        else:
            for x in range(WIDTH):
                _put(grid, x, y, layer.colors[index])


def _stars(grid: Grid, layer: Stars, rng: random.Random) -> None:
    for _ in range(layer.count):
        _put(grid, rng.randrange(WIDTH), rng.randrange(max(1, layer.until)), layer.color)


def _orb(grid: Grid, layer: Orb, rng: random.Random) -> None:
    for dy in range(-layer.r - 1, layer.r + 2):
        for dx in range(-layer.r - 1, layer.r + 2):
            distance = dx * dx + dy * dy
            if distance <= layer.r * layer.r:
                _put(grid, layer.x + dx, layer.y + dy, layer.color)
            elif layer.glow and distance <= (layer.r + 1) * (layer.r + 1):
                _put(grid, layer.x + dx, layer.y + dy, layer.glow)


def _clouds(grid: Grid, layer: Clouds, rng: random.Random) -> None:
    for _ in range(layer.count):
        x, y = rng.randrange(WIDTH), rng.randint(*layer.rows)
        width = rng.randint(6, 12)
        for dx in range(width):
            _put(grid, x + dx, y, layer.color)
        for dx in range(2, width - 2):
            _put(grid, x + dx, y - 1, layer.color)


def _ridge(grid: Grid, layer: Ridge, rng: random.Random) -> None:
    low, high = layer.height
    height = rng.randint(low, high)
    for x in range(WIDTH):
        height = max(low, min(high, height + rng.randint(-layer.rough, layer.rough)))
        top = layer.base - height
        _fill_down(grid, x, top, layer.color)
        if layer.cap is not None and height >= (low + high) // 2:
            _put(grid, x, top, layer.cap)
            if height >= high - 1:
                _put(grid, x, top + 1, layer.cap)


def _trees(grid: Grid, layer: Trees, rng: random.Random) -> None:
    spacing = WIDTH / max(1, layer.count)
    for i in range(layer.count):
        x = int(i * spacing + rng.uniform(0, spacing - 3)) + 1
        height = rng.randint(*layer.height)
        top = layer.base - height
        for y in range(top + height // 2, layer.base):
            _put(grid, x, y, layer.trunk)
        if layer.kind == "pine":
            for row in range(height - 1):
                half = min(4, row // 2)
                for dx in range(-half, half + 1):
                    _put(grid, x + dx, top + row, layer.leaf)
        elif layer.kind == "round":
            radius = max(2, height // 3)
            cy = top + radius
            for dy in range(-radius, radius + 1):
                for dx in range(-radius - 1, radius + 2):
                    if dx * dx * 0.7 + dy * dy <= radius * radius:
                        _put(grid, x + dx, cy + dy, layer.leaf)
        else:  # dead: bare trunk with a few branches
            for y in range(top, layer.base):
                _put(grid, x, y, layer.trunk)
            for _ in range(3):
                y = rng.randint(top, top + height // 2)
                side = rng.choice((-1, 1))
                for d in range(1, rng.randint(2, 4)):
                    _put(grid, x + side * d, y - d // 2, layer.trunk)


def _houses(grid: Grid, layer: Houses, rng: random.Random) -> None:
    spacing = WIDTH / max(1, layer.count)
    for i in range(layer.count):
        width = rng.randint(7, 10)
        x = int(i * spacing + rng.uniform(0, max(1.0, spacing - width)))
        height = rng.randint(4, 6)
        top = layer.base - height
        for y in range(top, layer.base):
            for dx in range(width):
                _put(grid, x + dx, y, layer.wall)
        for row in range(width // 2 + 1):  # a gable roof with eaves
            for dx in range(row - 1, width - row + 1):
                _put(grid, x + dx, top - 1 - row, layer.roof)
        for wx in range(x + 2, x + width - 2, 3):
            _put(grid, wx, top + 2, layer.window)
        door = x + width // 2
        _put(grid, door, layer.base - 1, "#2a1a10")
        _put(grid, door, layer.base - 2, "#2a1a10")


def _columns(grid: Grid, layer: Columns, rng: random.Random) -> None:
    spacing = WIDTH / max(1, layer.count)
    for i in range(layer.count):
        x = int(i * spacing + spacing / 2) - 1
        height = rng.randint(*layer.height)
        if layer.broken and rng.random() < 0.5:
            height = height // 2
        top = layer.base - height
        for y in range(top, layer.base):
            _put(grid, x, y, layer.color)
            _put(grid, x + 1, y, layer.color)
            _put(grid, x + 2, y, layer.color)
        if not layer.broken:
            for dx in range(-1, 4):
                _put(grid, x + dx, top, layer.color)


def _spikes(grid: Grid, layer: Spikes, rng: random.Random) -> None:
    for _ in range(layer.count):
        x = rng.randrange(WIDTH)
        length = rng.randint(*layer.length)
        step = 1 if layer.direction == "down" else -1
        for d in range(length):
            y = layer.base + step * d
            _put(grid, x, y, layer.color)
            if d < length // 2:
                _put(grid, x + 1, y, layer.color)


def _ground(grid: Grid, layer: Ground, rng: random.Random) -> None:
    for y in range(layer.top, HEIGHT):
        for x in range(WIDTH):
            speckled = layer.speckle is not None and rng.random() < layer.density
            _put(grid, x, y, layer.speckle if speckled else layer.color)


def _water(grid: Grid, layer: Water, rng: random.Random) -> None:
    for y in range(layer.top, HEIGHT):
        for x in range(WIDTH):
            _put(grid, x, y, layer.color)
        for _ in range(int(WIDTH * layer.density)):
            x = rng.randrange(WIDTH - 2)
            _put(grid, x, y, layer.light)
            _put(grid, x + 1, y, layer.light)


def _graves(grid: Grid, layer: Graves, rng: random.Random) -> None:
    spacing = WIDTH / max(1, layer.count)
    for i in range(layer.count):
        x = int(i * spacing + rng.uniform(1, spacing - 4))
        if rng.random() < 0.5:  # a cross
            for y in range(layer.base - 5, layer.base):
                _put(grid, x + 1, y, layer.color)
            for dx in range(3):
                _put(grid, x + dx, layer.base - 4, layer.color)
        else:  # a rounded stone
            for y in range(layer.base - 3, layer.base):
                for dx in range(3):
                    _put(grid, x + dx, y, layer.color)
            _put(grid, x + 1, layer.base - 4, layer.color)


FIRE: Final = [
    "...y...",
    "..yoy..",
    ".yoroy.",
    ".orrro.",
    "bbwbwbb",
]
FIRE_COLORS: Final = {
    "y": "#ffd23f",
    "o": "#ff8a2a",
    "r": "#e0502e",
    "b": "#5a3a22",
    "w": "#3a2414",
}


def _fire(grid: Grid, layer: Fire, rng: random.Random) -> None:
    top = layer.base - len(FIRE)
    for dy, row in enumerate(FIRE):
        for dx, letter in enumerate(row):
            if letter != ".":
                _put(grid, layer.x - 3 + dx, top + dy, FIRE_COLORS[letter])


def _torches(grid: Grid, layer: Torches, rng: random.Random) -> None:
    spacing = WIDTH / max(1, layer.count)
    for i in range(layer.count):
        x = int(i * spacing + spacing / 2)
        _put(grid, x, layer.row - 1, "#ffd23f")
        _put(grid, x, layer.row, "#ff8a2a")
        _put(grid, x, layer.row + 1, "#5a3a22")
        _put(grid, x, layer.row + 2, "#5a3a22")


def _crystals(grid: Grid, layer: Crystals, rng: random.Random) -> None:
    for _ in range(layer.count):
        x = rng.randrange(1, WIDTH - 3)
        height = rng.randint(*layer.height)
        for d in range(height):
            y = layer.base - 1 - d
            width = 1 if d >= height - 1 else 2
            for dx in range(width):
                _put(grid, x + dx, y, layer.light if dx == 0 else layer.color)


def _fog(grid: Grid, layer: Fog, rng: random.Random) -> None:
    """Drifting strands of mist: short horizontal streaks, more of them the denser it is."""
    for y in range(layer.rows[0], layer.rows[1] + 1):
        x = rng.randrange(8)
        while x < WIDTH:
            if rng.random() < layer.density:
                for dx in range(rng.randint(4, 10)):
                    _put(grid, x + dx, y, layer.color)
            x += rng.randint(6, 14)


def _wall(grid: Grid, layer: Wall, rng: random.Random) -> None:
    for y in range(layer.rows[0], layer.rows[1] + 1):
        row = y - layer.rows[0]
        for x in range(WIDTH):
            mortar = row % 3 == 2 or (x + (4 if (row // 3) % 2 else 0)) % 8 == 0
            _put(grid, x, y, layer.mortar if mortar else layer.color)


def _banners(grid: Grid, layer: Banners, rng: random.Random) -> None:
    spacing = WIDTH / max(1, layer.count)
    for i in range(layer.count):
        x = int(i * spacing + spacing / 2) - 2
        color = layer.colors[i % len(layer.colors)]
        for y in range(layer.top, layer.top + layer.length):
            for dx in range(4):
                _put(grid, x + dx, y, color)
        _put(grid, x + 1, layer.top + layer.length, color)
        _put(grid, x + 2, layer.top + layer.length, color)
        for dx in range(-1, 5):
            _put(grid, x + dx, layer.top - 1, "#5a3a22")


def _plants(grid: Grid, layer: Plants, rng: random.Random) -> None:
    for x in range(WIDTH):
        if rng.random() >= layer.density:
            continue
        height = rng.randint(*layer.height)
        for d in range(height):
            _put(grid, x, layer.base - 1 - d, layer.color)
        if layer.tip is not None:
            _put(grid, x, layer.base - height - 1, layer.tip)


def _art(grid: Grid, layer: Art, rng: random.Random) -> None:
    for dy, row in enumerate(layer.rows):
        for dx, letter in enumerate(row):
            if letter != ".":
                _put(grid, layer.x + dx, layer.y + dy, layer.palette[letter])


PAINTERS: Final[dict[str, Callable[..., None]]] = {
    "sky": _sky,
    "stars": _stars,
    "orb": _orb,
    "clouds": _clouds,
    "ridge": _ridge,
    "trees": _trees,
    "houses": _houses,
    "columns": _columns,
    "spikes": _spikes,
    "ground": _ground,
    "water": _water,
    "graves": _graves,
    "fire": _fire,
    "torches": _torches,
    "crystals": _crystals,
    "fog": _fog,
    "wall": _wall,
    "banners": _banners,
    "plants": _plants,
    "art": _art,
}


def paint(layers: list[Layer], seed: str) -> Grid:
    grid: Grid = [[None] * WIDTH for _ in range(HEIGHT)]
    for index, layer in enumerate(layers):
        PAINTERS[layer.type](grid, layer, random.Random(f"{seed}:{index}"))
    return grid


def render_scene(grid: Grid, title: str) -> str:
    rects: list[str] = []
    for y, row in enumerate(grid):
        x = 0
        while x < WIDTH:
            color = row[x]
            run = 1
            while x + run < WIDTH and row[x + run] == color:
                run += 1
            if color is not None:
                rects.append(f'<rect x="{x}" y="{y}" width="{run}" height="1" fill="{color}"/>')
            x += run
    title = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
        f'width="{WIDTH * 6}" height="{HEIGHT * 6}" shape-rendering="crispEdges" '
        f'preserveAspectRatio="xMidYMid slice">'
        f"<title>{title}</title>{''.join(rects)}</svg>\n"
    )


def render_rooms(book: SceneBook, names: dict[str, str]) -> dict[str, str]:
    """File name → SVG for every room in ``names`` (id → display name) that has a scene."""
    files = {}
    for room_id, scene in book.rooms.items():
        if room_id in names:
            grid = paint([*book.themes[scene.theme], *scene.add], room_id)
            files[f"{room_id}.svg"] = render_scene(grid, names[room_id])
    return files


if __name__ == "__main__":  # pragma: no cover - developer tool
    from app.content.loader import load_content

    root = Path(__file__).resolve().parents[2]
    content = load_content(root / "data")
    book = load_scenes(root / "data" / "scenes.yaml")
    names = {r.id: r.name for r in content.rooms.values()}
    for line in write_all(render_rooms(book, names), root / "app/web/static/scenes"):
        print(line)
