"""``python -m app.sim [--seed N] [--runs N]``: a table of how a bot progresses per level."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.content.loader import load_content
from app.sim.progression import simulate

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--runs", type=int, default=1, help="runs with seeds seed, seed+1, …")
    args = parser.parse_args()
    content = load_content(DATA_DIR)
    for seed in range(args.seed, args.seed + args.runs):
        run = simulate(content, seed=seed)
        state = "festgefahren" if run.stuck else "geschafft"
        print(f"Seed {seed}: Stufe {run.reached} nach {run.fights} Kämpfen ({state})")
        print("Stufe Kämpfe Tode HP/Kampf Rasten Gold(Beute) Gold(Verkauf) Gold(Ausrüstung)")
        for level in run.levels:
            print(
                f"{level.level:5} {level.fights:7} {level.deaths:4} "
                f"{level.loss_per_fight:8.0%} {level.rests:6} {level.gold_looted:11} "
                f"{level.gold_sold:13} {level.gold_gear:16}"
            )
        print()


if __name__ == "__main__":
    main()
