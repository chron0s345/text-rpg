"""The minimap lays rooms out from their exits: grid steps for the four directions."""

from pathlib import Path

from app.content.loader import load_content
from app.services.minimap import CELL, minimap

CONTENT = load_content(Path("data"))


def test_rooms_sit_where_their_exits_point() -> None:
    m = minimap(CONTENT, "town_square", quest_rooms=frozenset({"dark_forest"}))
    at = {r.room_id: (r.x, r.y) for r in m.rooms}
    sx, sy = at["town_square"]
    assert at["town_bank"] == (sx, sy - CELL)
    assert at["town_market"] == (sx + CELL, sy)
    assert at["forest_gate"] == (sx, sy + CELL)
    assert at["town_smithy"] == (sx - CELL, sy)
    assert at["dark_forest"] == (sx, sy + 2 * CELL)  # two steps away still fits

    rooms = {r.room_id: r for r in m.rooms}
    assert rooms["town_square"].current and rooms["town_square"].safe
    assert rooms["town_market"].merchant and not rooms["town_bank"].merchant
    assert rooms["dark_forest"].quest and not rooms["forest_gate"].quest
    # every placed room fits into the picture
    assert all(0 <= r.x < m.width and 0 <= r.y < m.height for r in m.rooms)


def test_the_window_and_its_links() -> None:
    m = minimap(CONTENT, "town_square", radius=1)  # a 3×3 window around the square
    rooms = {r.room_id: r for r in m.rooms}
    assert {"town_bank", "town_market", "forest_gate", "town_smithy"} <= set(rooms)
    assert "temple" in rooms  # south of the market: the corner of the window
    centre = rooms["town_square"]
    assert all(abs(r.x - centre.x) <= CELL and abs(r.y - centre.y) <= CELL for r in m.rooms)
    for link in m.links:  # links only ever join grid neighbours
        assert abs(link.x2 - link.x1) + abs(link.y2 - link.y1) == CELL


def test_every_room_can_be_drawn() -> None:
    for room_id in CONTENT.rooms:
        m = minimap(CONTENT, room_id)
        assert [r.room_id for r in m.rooms if r.current] == [room_id]
