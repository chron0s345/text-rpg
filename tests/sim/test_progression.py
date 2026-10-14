"""Guard rails from the progression simulation (app/sim): with real content, a bot that only
hunts at its own level, rests in safe rooms and buys gear reaches the top level in a
reasonable number of fights without dying all the time."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.content.loader import load_content
from app.sim.progression import RunReport, simulate

CONTENT = load_content(Path(__file__).resolve().parents[2] / "data")
SEEDS = (1, 2, 3)


@pytest.fixture(scope="module", params=SEEDS, ids=lambda s: f"seed{s}")
def run(request: pytest.FixtureRequest) -> RunReport:
    return simulate(CONTENT, seed=request.param)


def test_the_top_level_is_reachable(run: RunReport) -> None:
    top = max(rule.level for rule in CONTENT.rules.levels)
    assert not run.stuck and run.reached == top
    # roughly 15 fights per level: neither a sprint nor a grind
    assert 150 <= run.fights <= 450, run.fights


def test_deaths_stay_rare(run: RunReport) -> None:
    assert sum(level.deaths for level in run.levels) <= 5


def test_no_level_is_a_wall(run: RunReport) -> None:
    for level in run.levels:
        assert level.loss_per_fight <= 0.7, f"level {level.level}: {level.loss_per_fight:.0%}"
        assert level.fights <= 40, f"level {level.level}: {level.fights} fights"


def test_gold_buys_gear_along_the_way(run: RunReport) -> None:
    """Gear grows with the levels: what the balance test assumes is actually bought."""
    by_level = {level.level: level for level in run.levels}
    assert by_level[6].defense > by_level[1].defense + 5
    assert by_level[11].attack > by_level[6].attack + 10
    assert sum(level.gold_gear for level in run.levels) > 2000
