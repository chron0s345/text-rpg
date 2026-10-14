import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from app.content.loader import ContentValidationError, load_content
from app.content.schemas import DeathRules, Rules
from tests.conftest import DATA_DIR

ICONS_DIR = DATA_DIR.parent / "app" / "web" / "static" / "icons"


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Copy of the real data/ directory that a test may modify."""
    target = tmp_path / "data"
    shutil.copytree(DATA_DIR, target)
    return target


def _read(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _write(path: Path, content: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(content, f, allow_unicode=True)


def _raw_rules() -> dict[str, Any]:
    raw = _read(DATA_DIR / "rules.yaml")
    assert isinstance(raw, dict)
    return raw


# --- Rules ------------------------------------------------------------------


def test_rules_have_no_defaults() -> None:
    raw = _raw_rules()
    del raw["combat"]
    with pytest.raises(ValidationError, match="combat"):
        Rules.model_validate(raw)


@pytest.mark.parametrize(
    "levels",
    [
        [{"level": 2, "xp_required": 0, "max_hp": 20, "attack": 5, "defense": 2}],
        [{"level": 1, "xp_required": 10, "max_hp": 20, "attack": 5, "defense": 2}],
        [
            {"level": 1, "xp_required": 0, "max_hp": 20, "attack": 5, "defense": 2},
            {"level": 3, "xp_required": 100, "max_hp": 30, "attack": 8, "defense": 4},
        ],
        [
            {"level": 1, "xp_required": 0, "max_hp": 20, "attack": 5, "defense": 2},
            {"level": 2, "xp_required": 0, "max_hp": 30, "attack": 8, "defense": 4},
        ],
        [
            {"level": 1, "xp_required": 0, "max_hp": 20, "attack": 5, "defense": 2},
            {"level": 1, "xp_required": 0, "max_hp": 20, "attack": 5, "defense": 2},
        ],
        [],
    ],
    ids=["no-level-1", "level-1-needs-xp", "gap", "xp-not-increasing", "duplicate", "empty"],
)
def test_invalid_level_curve_rejected(levels: list[dict[str, int]]) -> None:
    raw = _raw_rules()
    raw["levels"] = levels
    with pytest.raises(ValidationError):
        Rules.model_validate(raw)


def test_level_curve_may_be_unordered_in_yaml() -> None:
    raw = _raw_rules()
    raw["levels"] = list(reversed(raw["levels"]))
    level_2 = next(level for level in raw["levels"] if level["level"] == 2)
    rules = Rules.model_validate(raw)
    assert rules.level_rule(2).xp_required == level_2["xp_required"]


def test_level_rule_unknown_level(rules: Rules) -> None:
    with pytest.raises(KeyError):
        rules.level_rule(999)


@pytest.mark.parametrize("gold_loss", [(20, 5), (5, 101)], ids=["min>max", "over-100"])
def test_death_gold_loss_range_rejected(gold_loss: tuple[int, int]) -> None:
    with pytest.raises(ValidationError, match="gold_loss_percent"):
        DeathRules(
            gold_loss_percent=gold_loss,
            item_loss_chance=0.1,
            equipped_loss_chance=0.1,
            max_items_lost=1,
        )


def test_death_gold_loss_full_range_allowed() -> None:
    DeathRules(
        gold_loss_percent=(0, 100),
        item_loss_chance=0.0,
        equipped_loss_chance=0.0,
        max_items_lost=0,
    )


# --- Loader errors -----------------------------------------------------------


def test_real_content_with_icons_loads() -> None:
    registry = load_content(DATA_DIR, icons_dir=ICONS_DIR)
    assert [m.id for m in registry.merchants_in_room("town_market")] == ["alchemist"]
    assert registry.merchants_in_room("town_square") == []


def test_invalid_yaml_raises(data_dir: Path) -> None:
    (data_dir / "items.yaml").write_text("- id: [unclosed\n", encoding="utf-8")
    with pytest.raises(ContentValidationError, match="Invalid YAML in"):
        load_content(data_dir)


def test_missing_file_raises(data_dir: Path) -> None:
    (data_dir / "merchants.yaml").unlink()
    with pytest.raises(ContentValidationError, match="not found"):
        load_content(data_dir)


def test_list_file_must_be_list(data_dir: Path) -> None:
    _write(data_dir / "monsters.yaml", {"goblin": {}})
    with pytest.raises(ContentValidationError, match="monsters.yaml must contain a YAML list"):
        load_content(data_dir)


def test_rules_file_must_be_mapping(data_dir: Path) -> None:
    _write(data_dir / "rules.yaml", [1, 2])
    with pytest.raises(ContentValidationError, match="rules.yaml must contain a YAML mapping"):
        load_content(data_dir)


def test_invalid_entry_reports_file_and_index(data_dir: Path) -> None:
    monsters = _read(data_dir / "monsters.yaml")
    monsters[1]["max_hp"] = -5
    _write(data_dir / "monsters.yaml", monsters)
    with pytest.raises(ContentValidationError, match=r"monsters.yaml entry #1"):
        load_content(data_dir)


def test_duplicate_id_rejected(data_dir: Path) -> None:
    items = _read(data_dir / "items.yaml")
    items.append(dict(items[0]))
    _write(data_dir / "items.yaml", items)
    with pytest.raises(ContentValidationError, match="Duplicate ID in items.yaml"):
        load_content(data_dir)


def test_invalid_rules_wrapped(data_dir: Path) -> None:
    raw = _read(data_dir / "rules.yaml")
    del raw["timings"]
    _write(data_dir / "rules.yaml", raw)
    with pytest.raises(ContentValidationError, match="rules.yaml"):
        load_content(data_dir)


def test_merchant_listed_without_kinds_rejected(data_dir: Path) -> None:
    merchants = _read(data_dir / "merchants.yaml")
    merchants[0]["buys"] = "listed"
    merchants[0]["buys_kinds"] = []
    _write(data_dir / "merchants.yaml", merchants)
    with pytest.raises(ContentValidationError, match="buys: listed but no buys_kinds"):
        load_content(data_dir)


def test_missing_icon_rejected(data_dir: Path, tmp_path: Path) -> None:
    icons = tmp_path / "icons"
    shutil.copytree(ICONS_DIR, icons)
    (icons / "potion.svg").unlink()
    with pytest.raises(ContentValidationError, match="missing icon 'potion.svg'"):
        load_content(data_dir, icons_dir=icons)


def test_duplicate_item_spawn_rule_in_room_rejected(data_dir: Path) -> None:
    rooms = _read(data_dir / "rooms.yaml")
    room = next(r for r in rooms if r["items"])
    room["items"].append(dict(room["items"][0]))
    _write(data_dir / "rooms.yaml", rooms)
    with pytest.raises(ContentValidationError, match="several spawn rules"):
        load_content(data_dir)
