"""Jinja2 rendering shared by the HTML views and the SSE stream (autoescape is on: chat texts and
character names are always escaped)."""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

STATE_LABELS = {"exploring": "unterwegs", "in_combat": "im Kampf", "dead": "tot"}
templates.env.globals["state_label"] = lambda state: STATE_LABELS.get(state, state)


def log_line(kind: str, text: str) -> str:
    """One ``<li>`` of the game log."""
    template = templates.get_template("partials/log_line.html")
    return template.render(kind=kind, text=text).strip()
