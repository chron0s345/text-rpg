"""A small map of the rooms around the player, laid out from the exits alone (rooms have no
coordinates): breadth-first from the current room, north/south/east/west are grid steps.

Rooms reached only by up/down are not placed; their rooms carry a marker instead. Where the
world does not fit on a grid (two paths leading to the same cell), the first room wins.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Final

from app.content.registry import ContentRegistry
from app.content.schemas import Direction

STEPS: Final[dict[Direction, tuple[int, int]]] = {
    "north": (0, -1),
    "south": (0, 1),
    "east": (1, 0),
    "west": (-1, 0),
}
CELL: Final = 28  # px between room centres
SIZE: Final = 18  # px of a room square
MARGIN: Final = 6


@dataclass(frozen=True)
class MapRoom:
    room_id: str
    name: str
    x: int  # px, top left of the square
    y: int
    current: bool
    safe: bool
    merchant: bool
    quest: bool  # the current step of an active quest points here
    up: bool  # an exit up / down leaves from here
    down: bool


@dataclass(frozen=True)
class MapLink:
    x1: int
    y1: int
    x2: int
    y2: int


@dataclass(frozen=True)
class Minimap:
    width: int
    height: int
    rooms: list[MapRoom]
    links: list[MapLink]


def minimap(
    content: ContentRegistry,
    room_id: str,
    quest_rooms: frozenset[str] = frozenset(),
    radius: int = 2,
) -> Minimap:
    """The rooms up to ``radius`` grid steps from ``room_id``."""
    cells: dict[str, tuple[int, int]] = {room_id: (0, 0)}
    taken: set[tuple[int, int]] = {(0, 0)}
    queue = deque([room_id])
    while queue:
        current = queue.popleft()
        cx, cy = cells[current]
        for direction, target in content.get_room(current).exits.items():
            step = STEPS.get(direction)
            if step is None or target in cells:
                continue
            cell = (cx + step[0], cy + step[1])
            if max(abs(cell[0]), abs(cell[1])) > radius or cell in taken:
                continue
            cells[target] = cell
            taken.add(cell)
            queue.append(target)

    min_x = min(x for x, _ in cells.values())
    min_y = min(y for _, y in cells.values())

    def corner(cell: tuple[int, int]) -> tuple[int, int]:
        return MARGIN + (cell[0] - min_x) * CELL, MARGIN + (cell[1] - min_y) * CELL

    rooms = []
    for rid, cell in cells.items():
        room = content.get_room(rid)
        x, y = corner(cell)
        rooms.append(
            MapRoom(
                room_id=rid,
                name=room.name,
                x=x,
                y=y,
                current=rid == room_id,
                safe=room.safe,
                merchant=bool(content.merchants_in_room(rid)),
                quest=rid in quest_rooms,
                up="up" in room.exits,
                down="down" in room.exits,
            )
        )
    links = []
    half = SIZE // 2
    for rid, cell in cells.items():
        exits = content.get_room(rid).exits
        for direction in ("east", "south"):  # each link once
            linked = exits.get(direction)
            dx, dy = STEPS[direction]
            if linked is not None and cells.get(linked) == (cell[0] + dx, cell[1] + dy):
                x1, y1 = corner(cell)
                x2, y2 = corner(cells[linked])
                links.append(MapLink(x1 + half, y1 + half, x2 + half, y2 + half))
    width = MARGIN * 2 + (max(x for x, _ in cells.values()) - min_x) * CELL + SIZE
    height = MARGIN * 2 + (max(y for _, y in cells.values()) - min_y) * CELL + SIZE
    return Minimap(width=width, height=height, rooms=rooms, links=links)
