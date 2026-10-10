from pathlib import Path

import pytest
import yaml

from app.content.schemas import Rules

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_real_rules() -> Rules:
    with open(DATA_DIR / "rules.yaml", encoding="utf-8") as f:
        return Rules.model_validate(yaml.safe_load(f))


@pytest.fixture
def rules() -> Rules:
    """The real data/rules.yaml (Rules has no defaults, all balancing lives in data/)."""
    return load_real_rules()
