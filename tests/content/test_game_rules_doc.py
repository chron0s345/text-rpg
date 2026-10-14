"""docs/game_rules.md must not drift from data/rules.yaml: every value in its tables is checked."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from tests.conftest import load_real_rules

DOC = Path(__file__).resolve().parents[2] / "docs" / "game_rules.md"
VALUE_ROW = re.compile(r"^\|[^|]+\|\s*`([a-z_.]+)`\s*\|\s*(.+?)\s*\|$")
LEVEL_ROW = re.compile(r"^\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|$")


def lookup(values: dict[str, Any], dotted: str) -> Any:
    current: Any = values
    for part in dotted.split("."):
        current = current[part]
    return current


def test_documented_rule_values_match_rules_yaml() -> None:
    rules = load_real_rules().model_dump(mode="json")
    rows = [m.groups() for line in DOC.read_text().splitlines() if (m := VALUE_ROW.match(line))]

    assert len(rows) >= 20, "the Regelwerte table is missing"
    for key, documented in rows:
        assert yaml.safe_load(documented) == lookup(rules, key), key


def test_documented_levels_match_rules_yaml() -> None:
    levels = [
        (r.level, r.xp_required, r.max_hp, r.attack, r.defense) for r in load_real_rules().levels
    ]
    rows = [
        tuple(int(v) for v in m.groups())
        for line in DOC.read_text().splitlines()
        if (m := LEVEL_ROW.match(line))
    ]
    assert rows == levels


def test_room_table_lists_every_room() -> None:
    lines = DOC.read_text().splitlines()
    start = lines.index("| Raum | Besonderheit |") + 2
    documented = []
    for line in lines[start:]:
        if not line.startswith("|"):
            break
        documented.append(line.split("|")[1].split(" (")[0].strip())
    rooms = yaml.safe_load((DOC.parents[1] / "data" / "rooms.yaml").read_text())

    assert sorted(documented) == sorted(room["name"] for room in rooms)


def test_quest_table_lists_every_quest() -> None:
    lines = DOC.read_text().splitlines()
    start = lines.index("| Auftrag | Auftraggeber | ab Stufe | Aufgabe |") + 2
    documented = []
    for line in lines[start:]:
        if not line.startswith("|"):
            break
        documented.append(line.split("|")[1].strip())
    quests = yaml.safe_load((DOC.parents[1] / "data" / "quests.yaml").read_text())

    assert sorted(documented) == sorted(quest["name"] for quest in quests)


def test_recipe_table_lists_every_recipe() -> None:
    lines = DOC.read_text().splitlines()
    start = lines.index("| Ergebnis | Handwerker | ab Stufe | Zutaten | Gold |") + 2
    documented = []
    for line in lines[start:]:
        if not line.startswith("|"):
            break
        documented.append(line.split("|")[3].strip())  # ab Stufe
    recipes = yaml.safe_load((DOC.parents[1] / "data" / "recipes.yaml").read_text())

    assert len(documented) == len(recipes)
    assert documented == [str(recipe.get("min_level", 1)) for recipe in recipes]
