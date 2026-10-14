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
