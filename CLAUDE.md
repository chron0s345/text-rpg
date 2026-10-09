# Claude Collaboration Guidelines

Du entwickelst an einem textbasierten Web-RPG gemeinsam mit Gemini.

## Priorität & Kontext
- Lies VOR Code-Änderungen immer `ARCHITECTURE.md` und `docs/game_rules.md`.
- Ändere keine bestehenden Schemas oder API-Signaturen, ohne die Tests und Models synchron zu halten.

## Tooling & Befehle
- Virtualenv: `source .venv/bin/activate`
- Run Server: `uvicorn app.main:app --reload --port 8000`
- Tests: `pytest tests/ -v`
- Linting/Typing: `ruff check .` und `mypy .`

## Kooperationsregeln mit Gemini
1. **Keine Monolithen:** Halte Module modular (`app/engine/combat.py`, `app/models/`, `app/api/`).
2. **Git-Disziplin:** Committe oder dokumentiere klare Meilensteine, damit Gemini bei Code-Reviews nahtlos ansetzen kann.
3. **Tests First:** Wenn du ein Engine-Feature (z. B. Flucht-Mechanik, Inventar-Limit) schreibst, liefere stets passende Unit-Tests mit.