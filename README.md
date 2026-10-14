# Text-RPG

Ein textbasiertes Multiplayer-Rollenspiel im Browser: eine gemeinsame, persistente Welt mit Kämpfen, Gruppen, Handel, Händlern und Bank. Backend mit FastAPI und PostgreSQL, Oberfläche mit Jinja2 und HTMX, Live-Ereignisse per Server-Sent Events.

Wie alles zusammenspielt (Datenmodell, Engine, Sperren, Echtzeit, API) steht in [`ARCHITECTURE.md`](ARCHITECTURE.md). Die Spielinhalte (Räume, Monster, Items, Händler, Regeln) liegen als YAML in [`data/`](data/) und werden beim Start validiert.

## Schnellstart (Entwicklung)

Voraussetzungen: Python 3.12+, Docker.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

docker compose up -d db          # PostgreSQL 16 auf 127.0.0.1:5432
alembic upgrade head             # Schema anlegen
python -m app.db.seed            # Monster und Spawn-Items aus data/ anlegen (idempotent)

uvicorn app.main:app --reload --port 8000
```

Dann <http://localhost:8000> öffnen, einen Account registrieren, einen Charakter anlegen und losspielen.

Nach Änderungen an `data/`: Server neu starten und `python -m app.db.seed` erneut ausführen (legt nur Fehlendes an).

## Tests & Prüfungen

```bash
pytest tests/ -v                 # startet PostgreSQL per Testcontainers (Docker nötig)
ruff check . && ruff format --check .
mypy .                           # strict
```

Ohne Docker werden die Datenbanktests übersprungen; in CI ist ein übersprungener Test ein Fehler.

## Betrieb im Container

```bash
SESSION_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(32))") \
    docker compose --profile app up --build
```

Der App-Container führt beim Start `alembic upgrade head` und den Seed aus und startet dann uvicorn auf Port 8000. Beides ist idempotent; ein Neustart legt nichts doppelt an.

Hinweise für den Produktivbetrieb (siehe auch *Deployment* in `ARCHITECTURE.md`):

- `SESSION_SECRET` fest setzen. Ohne Secret würfelt jeder Prozess ein eigenes, und alle Sessions enden beim Neustart. Mit mehr als einem Worker verweigert der Container ohne Secret den Start.
- Hinter einem Reverse Proxy (Caddy, nginx) `FORWARDED_ALLOW_IPS` auf die Adresse des Proxys setzen, damit die echten Client-Adressen ankommen. Für `/stream` muss der Proxy Buffering abschalten und lange Verbindungen erlauben.
- Über HTTPS `SESSION_HTTPS_ONLY=1` setzen (Cookie nur über HTTPS).

## Konfiguration

| Variable | Standard | Bedeutung |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://rpg:rpg@localhost:5432/rpg` | Datenbank (asyncpg) |
| `SESSION_SECRET` | zufällig pro Prozess | Signiert das Session-Cookie; für alle Worker gleich setzen |
| `SESSION_HTTPS_ONLY` | `0` | `1`: Session-Cookie nur über HTTPS |
| `WORLD_TICKER` | `1` | `0` schaltet den Hintergrund-Ticker ab (Respawns, Aufräumen, Fristen, Überfälle) |
| `WEB_CONCURRENCY` | `1` | Anzahl uvicorn-Worker (nur Container) |
| `PORT` | `8000` | Port im Container |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Wessen `X-Forwarded-*`-Header vertraut wird (Adresse des Reverse Proxys) |

Spielwerte (XP-Kurve, Inventargröße, Todesstrafe, Zeiten) sind keine Umgebungsvariablen, sondern stehen in [`data/rules.yaml`](data/rules.yaml).

## Projektstruktur (kurz)

```
app/engine/     reine Spiellogik (kein I/O, Zufall und Zeit werden übergeben)
app/services/   Transaktionen: sperren, Engine aufrufen, speichern, Events veröffentlichen
app/api/        dünne HTTP-Schicht (JSON-API und HTML-Seiten)
app/realtime/   Event-Bus über PostgreSQL LISTEN/NOTIFY, SSE-Verbindungen
app/web/        Templates und statische Dateien
data/           Spielinhalte (YAML)
migrations/     Alembic
```
