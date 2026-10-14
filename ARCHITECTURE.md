# Architecture & Tech Specs

## Grundannahmen

- **Multiplayer:** Alle Spieler teilen sich eine persistente Welt. Spieler im selben Raum sehen sich gegenseitig, sehen dieselben Monster und können gemeinsam kämpfen.
- **Kein PvP** in der ersten Version, für später vorgesehen.
- **Accounts mit mehreren Charakteren.** Pro Account ist immer nur ein Charakter gleichzeitig eingeloggt.
- **Gruppen:** Spieler können Gruppen bilden; Beute wird innerhalb der Gruppe geteilt.
- **Monster:** respawnen nach Zeit, regenerieren außerhalb des Kampfes und können Spieler überfallen.
- **Tod:** Wiederbelebung am zuletzt betretenen Checkpoint (automatische Bindung); dabei können zufällig Items und Gold verloren gehen. Verlorenes verschwindet endgültig.
- **Inventar** ist begrenzt (Anzahl Plätze, stapelbare Items mit Stapelgrenze) und lässt sich durch Taschen erweitern.
- **Bank** in der Stadt: zusätzlicher Stauraum pro Charakter, sicher vor Verlust beim Tod.
- **Händler-NPCs** kaufen und verkaufen Items gegen Gold.
- **Handel** zwischen Spielern ist möglich, nicht aber zwischen Charakteren desselben Accounts.
- **Ausloggen:** Der Charakter wird in die Stadt zurückgesetzt. Wer sich im Kampf ausloggt oder die Verbindung verliert und nicht rechtzeitig zurückkommt, stirbt.
- **Speichern:** Automatisch. Jede Aktion wird sofort in der Datenbank festgeschrieben.
- **Floor-Items** (fallengelassen oder gedroppt) verschwinden nach einer Zeit.

## Tech Stack

| Bereich | Technologie |
|---|---|
| Backend | Python 3.12+, FastAPI (async), SQLModel / SQLAlchemy 2 (async), Pydantic v2 |
| Datenbank | PostgreSQL 16 (Dev via Docker Compose), Treiber `asyncpg`, Migrationen mit Alembic |
| Echtzeit | Server-Sent Events für Welt-Events, verteilt über PostgreSQL `LISTEN/NOTIFY` |
| Frontend | Jinja2 + HTMX (inkl. SSE-Extension), minimales CSS |
| Auth | Benutzername + Passwort (argon2), Session-Cookie |
| Content | YAML-Dateien in `data/`, beim Start validiert |
| Tests | pytest, pytest-asyncio, Testcontainers (PostgreSQL) |
| Tooling | ruff (Lint + Format), mypy (strict) |

### Warum SSE statt WebSockets

Spieleraktionen laufen als normale REST-Requests. Der Server muss zusätzlich Ereignisse anderer Spieler und der Welt an den Client schicken (jemand betritt den Raum, ein Monster überfällt die Gruppe, ein Mitspieler trifft den Goblin). Das ist reiner Server→Client-Push, und dafür ist SSE einfacher: normales HTTP, automatischer Reconnect, native HTMX-Unterstützung.

### Warum PostgreSQL `LISTEN/NOTIFY` statt Redis

Bei mehreren Server-Prozessen muss ein Event aus Prozess A auch die SSE-Verbindungen in Prozess B erreichen. PostgreSQL kann das ohne zusätzliche Infrastruktur. Der Event-Bus ist hinter einem Interface versteckt, sodass Redis später austauschbar ist.

## Projektstruktur

```
app/
├── main.py              # FastAPI-App (create_app), Lifespan: Content laden, Event-Bus & World-Ticker starten
├── settings.py          # Konfiguration aus Umgebungsvariablen
├── api/                 # HTTP-Routen, nur dünne Schicht
│   ├── deps.py          # Services aus dem App-State, Session-Cookie (Account, aktiver Charakter)
│   ├── auth.py          # Registrierung, Login, Logout
│   ├── characters.py    # Charaktere anlegen, auswählen, löschen
│   ├── actions.py       # POST /actions
│   ├── stream.py        # GET /stream (SSE), GET /log
│   └── views.py         # Seiten & HTMX-Partials (/welcome, /select, /, /ui/*)
├── engine/              # Reine Spiellogik, kein HTTP, kein DB-Zugriff
│   ├── state.py         # Snapshots (ActionSnapshot, PlayerSnapshot, ...) als Engine-Eingabe
│   ├── permissions.py   # Erlaubte Aktionen je Charakterzustand
│   ├── actions.py       # say, look, respawn
│   ├── combat.py        # attack, flee
│   ├── encounter.py     # Gemeinsame Kampfbausteine (Schaden, Kampfkontakt, Monsterschlag)
│   ├── participation.py # Kampfteilnahme & Aufheben der Markierung
│   ├── rewards.py       # Kill: XP, Gold, Beute
│   ├── ambush.py        # Überfälle durch Monster (beim Betreten & für den Ticker)
│   ├── regeneration.py  # Monster-HP außerhalb des Kampfes
│   ├── loot.py          # Loot würfeln & verteilen (Gruppen)
│   ├── groups.py        # Einladen, Beitreten, Verlassen, Leader
│   ├── group_status.py  # Gruppenpanel: HP & Ort der Mitglieder
│   ├── movement.py
│   ├── inventory.py     # Plätze, Stapel, Kapazitätsprüfung
│   ├── inventory_actions.py # equip, unequip, use_item, pick_up, drop, inventory
│   ├── trade.py         # Handelsablauf zwischen zwei Spielern
│   ├── bank.py          # Einlagern & Abheben
│   ├── merchant.py      # Kaufen & Verkaufen bei NPCs
│   ├── progression.py   # XP, Level-Up
│   ├── death.py         # Tod, Verlustwürfe, Wiederbelebung
│   ├── presence.py      # Ein-/Ausloggen, Verbindungsabbruch
│   ├── respawn.py       # Fällige Monster-Respawns bestimmen
│   ├── cleanup.py       # Abgelaufene Floor-Items bestimmen
│   ├── stats.py         # Effektive Werte berechnen
│   └── events.py        # Ergebnis-Events
├── content/
│   ├── schemas.py
│   ├── loader.py
│   ├── prices.py        # Kauf-/Verkaufspreise (Engine & Validierung)
│   └── registry.py
├── db/
│   ├── models.py        # SQLModel-Tabellen (nur veränderlicher Zustand)
│   ├── session.py
│   └── repository.py    # inkl. Locking-Queries
├── realtime/
│   ├── bus.py           # PostgresEventBus (LISTEN/NOTIFY), implementiert services.events.EventPublisher
│   └── connections.py   # SSE-Verbindungen pro Prozess, Zustellung nach Raum/Gruppe/Spieler/global
├── services/            # Orchestrierung: sperrt & lädt Zustand, ruft Engine, speichert, publiziert
│   ├── events.py            # EventPublisher-Interface (Umsetzung im Realtime-Layer)
│   ├── action_service.py
│   ├── log.py               # Log-Einträge pro Empfänger eines Events
│   ├── account_service.py   # Registrierung, Passwortprüfung (argon2), Charaktere anlegen/löschen
│   ├── session_service.py   # Login/Logout eines Charakters, Disconnect-Handling
│   ├── view_service.py      # Lesende Spielansicht (Status, Raum, Inventar, Log) für die Seiten
│   └── world_ticker.py      # Respawns, Aufräumen, Disconnect-Fristen
└── web/
    ├── render.py        # Jinja2-Umgebung (Autoescape), gemeinsam für Seiten und SSE
    ├── templates/       # Seiten + partials/ (status, room, actions, inventory, log_line)
    └── static/          # style.css, app.js, icons/, vendor/ (htmx, htmx-ext-sse, unverändert)
data/
├── rooms.yaml
├── monsters.yaml
├── items.yaml
├── merchants.yaml
└── rules.yaml           # XP-Kurve, Startwerte, Todesstrafe, Zeiten & Limits
migrations/
tests/
├── engine/
├── content/
├── services/
└── api/
docker-compose.yml
```

### Abhängigkeitsregeln

- `engine/` importiert **nichts** aus `api/`, `db/`, `realtime/` oder `web/`.
- `api/` enthält keine Spiellogik, sondern ruft `services/` auf.
- `services/` ist die einzige Schicht, die Engine, Datenbank und Event-Bus zusammenbringt.

## Grundprinzip: Content vs. Zustand

| | Content (Templates) | Zustand (Instanzen) |
|---|---|---|
| Quelle | YAML in `data/` | PostgreSQL |
| Veränderlich | nein (zur Laufzeit) | ja |
| Beispiele | "Goblin hat 12 HP Basis, respawnt nach 120 s" | "Dieser Goblin hat noch 3 HP" |
| Lebt in | `ContentRegistry` im Speicher | Datenbank |

Content wird **nicht** in die Datenbank kopiert. Zustand referenziert Content über stabile String-IDs. Es gibt genau **eine Welt**; Monster- und Raumzustand gehören der Welt, nicht einzelnen Spielern.

## Content-Modell (YAML → Pydantic)

```python
class RoomTemplate(BaseModel):
    id: str
    name: str
    description: str
    exits: dict[Direction, str]
    checkpoint: bool = False  # Betreten setzt den Checkpoint
    safe: bool = False  # keine Überfälle, keine Kämpfe (z. B. Stadt)
    bank: bool = False  # Bankzugang in diesem Raum
    spawns: list[SpawnRule] = []
    items: list[ItemSpawnRule] = []


class SpawnRule(BaseModel):
    monster_id: str
    count: PositiveInt = 1


class ItemSpawnRule(BaseModel):
    item_id: str
    quantity: PositiveInt = 1
    respawn_seconds: PositiveInt | None = None  # None = einmalig


class MonsterTemplate(BaseModel):
    id: str
    name: str
    max_hp: PositiveInt
    attack: NonNegativeInt
    defense: NonNegativeInt
    xp_reward: NonNegativeInt
    gold_reward: tuple[int, int] = (0, 0)
    respawn_seconds: PositiveInt
    regen: Regeneration | None = None  # None = regeneriert nicht
    ambush: Ambush | None = None  # None = greift nie von selbst an
    loot_table: list[LootEntry] = []


class Regeneration(BaseModel):
    delay_seconds: NonNegativeInt  # Wartezeit nach dem letzten Kampfkontakt
    hp_per_second: PositiveFloat


class Ambush(BaseModel):
    chance_on_enter: float = Field(ge=0, le=1)  # beim Betreten des Raums
    chance_per_tick: float = Field(ge=0, le=1)  # während Spieler im Raum verweilen
    max_level_difference: int | None = None  # greift keine deutlich stärkeren Spieler an


class LootEntry(BaseModel):
    item_id: str
    chance: float = Field(ge=0, le=1)
    min_qty: PositiveInt = 1
    max_qty: PositiveInt = 1


class ItemTemplate(BaseModel):
    id: str
    name: str
    description: str
    kind: Literal["weapon", "armor", "consumable", "bag", "misc"]
    slot: EquipSlot | None = None  # Taschen: "bag"
    bag_slots: PositiveInt | None = None  # nur bei kind = "bag": zusätzliche Inventarplätze
    value: NonNegativeInt = 0  # Basiswert in Gold, Grundlage für Händlerpreise
    sellable: bool = True
    stackable: bool = False
    max_stack: PositiveInt = 1  # > 1 nur bei stackable
    keep_on_death: bool = False  # z. B. Quest-Items, nie verlierbar
    tradable: bool = True  # Quest-Items u. Ä. nicht handelbar
    icon: str | None = None  # Icon Identifier oder Emoji für UI-Darstellung
    effects: list[Effect] = []


class StatModifier(BaseModel):
    type: Literal["stat"] = "stat"
    stat: Literal["attack", "defense", "max_hp"]
    amount: int


class Heal(BaseModel):
    type: Literal["heal"] = "heal"
    amount: PositiveInt


Effect = Annotated[StatModifier | Heal, Field(discriminator="type")]


class MerchantTemplate(BaseModel):
    id: str
    name: str
    room_id: str
    greeting: str
    sells: list[MerchantOffer]  # Sortiment, unbegrenzter Vorrat
    buys: Literal["all", "listed"] = "all"
    buys_kinds: list[ItemKind] = []  # bei "listed": nur diese Item-Arten
    sell_ratio: float | None = None  # überschreibt rules.merchant.sell_ratio


class MerchantOffer(BaseModel):
    item_id: str
    price: PositiveInt | None = None  # None = Item-value × rules.merchant.buy_ratio
```

### `rules.yaml`

```yaml
start_room_id: town_square         # Startraum neuer Charaktere
town_room_id: town_square          # Ziel beim Ausloggen
max_characters_per_account: 5
max_group_size: 4

inventory:
  slots: 20                        # Grundplätze; ausgerüstete Items belegen keinen Platz
  bag_slots: 4                     # Anzahl Taschen, die gleichzeitig ausgerüstet sein können

bank:
  slots: 40                        # pro Charakter

merchant:
  buy_ratio: 1.0                   # Spieler kauft zu value × buy_ratio
  sell_ratio: 0.25                 # Spieler verkauft zu value × sell_ratio (abgerundet)

trade:
  request_ttl_seconds: 60
  max_offer_entries: 8             # pro Seite

death:
  gold_loss_percent: [5, 20]       # zufällig im Bereich
  item_loss_chance: 0.15           # pro Inventar-Stapel (nicht ausgerüstet)
  equipped_loss_chance: 0.05       # pro ausgerüstetem Item
  max_items_lost: 3

timings:
  floor_item_ttl_seconds: 600
  disconnect_grace_seconds: 60
  ticker_interval_seconds: 5
  min_action_interval_ms: 800

levels: [...]                       # XP-Kurve & Stat-Zuwachs
```

### Validierung beim Laden

Der Start bricht ab, wenn:

- ein Exit, eine Spawn-Regel, ein Loot-Eintrag oder ein Raum-Item auf eine unbekannte ID zeigt,
- IDs doppelt vergeben sind,
- ein `weapon`/`armor` keinen `slot` hat oder ein anderes Item einen hat,
- `max_stack > 1` bei einem nicht stapelbaren Item gesetzt ist,
- ein `bag` kein `bag_slots` hat oder ein anderes Item eines hat,
- ein Händler in einem nicht existierenden Raum steht oder ein unbekanntes Item anbietet,
- ein Item beim Händler günstiger zu kaufen ist, als man es bei irgendeinem Händler verkaufen kann (Gelddruck-Schleife),
- es keinen Raum mit `bank: true` gibt,
- `start_room_id` oder `town_room_id` nicht existiert, nicht `safe` oder kein `checkpoint` ist,
- ein `safe`-Raum Monster-Spawns hat,
- Wahrscheinlichkeiten oder Bereiche ungültig sind.

Warnungen: nicht erreichbare Räume; Räume mit Monstern, von denen aus kein Checkpoint erreichbar ist.

## Zustandsmodell (Datenbank)

```python
class Account(SQLModel, table=True):
    id: UUID
    username: str  # unique
    password_hash: str
    created_at: datetime


class Player(SQLModel, table=True):  # = ein Charakter
    id: UUID
    account_id: UUID  # FK → Account, 1:n
    name: str  # unique, im Spiel sichtbar
    hp: int
    base_max_hp: int
    base_attack: int
    base_defense: int
    xp: int
    level: int
    gold: int
    current_room_id: str
    checkpoint_room_id: str
    state: PlayerState  # "exploring" | "in_combat" | "dead"
    combat_target_id: UUID | None  # FK → MonsterInstance
    group_id: UUID | None  # FK → Group
    online: bool
    disconnected_at: datetime | None  # gesetzt bei Verbindungsverlust, Frist läuft
    last_action_at: datetime | None  # Rate Limit
    deleted_at: datetime | None  # Soft Delete


class InventoryItem(SQLModel, table=True):
    id: UUID
    player_id: UUID
    item_id: str
    quantity: int  # ≤ max_stack
    equipped_slot: EquipSlot | None  # gesetzt = ausgerüstet, belegt keinen Platz


class MonsterInstance(SQLModel, table=True):
    id: UUID
    monster_id: str
    room_id: str
    hp: int  # Stand zum Zeitpunkt last_combat_at
    alive: bool
    last_combat_at: datetime | None  # Basis für Regeneration
    respawn_at: datetime | None
    tagged_by_party: str | None  # siehe "Kill-Credit"


class CombatParticipation(SQLModel, table=True):
    monster_instance_id: UUID
    player_id: UUID
    party_key: str  # "group:{id}" oder "solo:{player_id}"
    damage_dealt: int


class Group(SQLModel, table=True):
    id: UUID
    leader_id: UUID  # FK → Player
    loot_cursor: int  # Position für Round-Robin
    created_at: datetime


class GroupInvite(SQLModel, table=True):
    group_id: UUID
    player_id: UUID
    expires_at: datetime


class FloorItem(SQLModel, table=True):
    id: UUID
    room_id: str
    item_id: str
    quantity: int
    source: Literal["spawn", "drop", "loot"]
    dropped_by_account_id: UUID | None  # gesetzt bei source = "drop"
    expires_at: datetime | None  # None nur bei source = "spawn"


class ItemSpawnState(SQLModel, table=True):
    room_id: str
    item_id: str
    next_spawn_at: datetime | None  # gesetzt, wenn aufgehoben und respawnfähig


class BankItem(SQLModel, table=True):
    id: UUID
    player_id: UUID  # pro Charakter, nicht pro Account
    item_id: str
    quantity: int  # ≤ max_stack


class Trade(SQLModel, table=True):
    id: UUID
    initiator_id: UUID  # FK → Player
    partner_id: UUID  # FK → Player
    room_id: str
    status: TradeStatus  # "requested" | "open" | "completed" | "cancelled"
    initiator_gold: int
    partner_gold: int
    initiator_confirmed: bool
    partner_confirmed: bool
    revision: int  # erhöht sich bei jeder Angebotsänderung
    expires_at: datetime  # nur für "requested"
    created_at: datetime


class TradeOffer(SQLModel, table=True):
    trade_id: UUID
    player_id: UUID
    inventory_item_id: UUID
    quantity: int


class TradeLog(SQLModel, table=True):  # Audit, unveränderlich
    id: int
    trade_id: UUID
    completed_at: datetime
    summary: dict  # JSON: wer hat was an wen gegeben


class LogEntry(SQLModel, table=True):
    id: int
    player_id: UUID
    created_at: datetime
    kind: str
    text: str


class LoginThrottle(SQLModel, table=True):  # Login-Limit, siehe "Fehler & Schutz"
    key: str  # "user:<name>", "ip:<adresse>", "register:<adresse>"
    attempts: int
    window_started_at: datetime
    locked_until: datetime | None
    expires_at: datetime  # danach irrelevant, der World-Ticker löscht die Zeile
```

Ein Seed-Befehl erzeugt `MonsterInstance`- und Spawn-`FloorItem`-Einträge aus den Templates und gleicht bei Content-Änderungen fehlende Instanzen ab, ohne bestehende anzufassen.

Umsetzung in `app/db/models.py` (Abweichungen und Ergänzungen zum Modell oben):

- Tabellennamen in snake_case; `Group` heißt `player_group` (GROUP ist ein SQL-Schlüsselwort).
- `Player.group_joined_at`: Beitrittszeit, bestimmt Round-Robin-Reihenfolge und Leader-Nachfolge.
- `GroupInvite`: Schlüssel `(inviter_id, player_id)`, `group_id` ist `NULL`, solange der Einladende noch keine Gruppe hat (sie entsteht erst beim Annehmen).
- `CombatParticipation`, `ItemSpawnState`, `TradeOffer` haben zusammengesetzte Primärschlüssel; `TradeLog.summary` ist JSONB.
- Alle Zeitstempel sind `timestamptz` (UTC).
- Integritätsregeln in der Datenbank: keine negativen Werte für Gold/HP/XP/Mengen, gültige Zustände und Slots, ablaufende Floor-Items außer Spawns, Charakternamen eindeutig (ohne Groß-/Kleinschreibung) unter nicht gelöschten Charakteren, Benutzernamen eindeutig (ohne Groß-/Kleinschreibung), **höchstens ein Charakter pro Account online** (partieller Unique-Index).
- Seed: `python -m app.db.seed` (idempotent).

### Abgeleitete Werte

Werte, die sich aus anderen ergeben, werden **berechnet, nicht gespeichert**:

- `effective_attack/defense/max_hp` = Basiswert + Modifikatoren ausgerüsteter Items (`engine/stats.py`).
- **Aktuelle Monster-HP** = `min(max_hp, hp + regen.hp_per_second × max(0, now − last_combat_at − delay))` (`engine/regeneration.py`). Die Regeneration wird erst beim nächsten Kampfkontakt in die DB geschrieben. So braucht es keinen Ticker, der ständig HP hochzählt.

## Engine

### Charakterzustände

```
             greift an / wird überfallen
exploring ─────────────────────────────────▶ in_combat
    ▲                                            │
    │   Monster besiegt / Flucht /               │
    │   Monster von anderen getötet              │
    ├────────────────────────────────────────────┤
    │                                            │ HP ≤ 0
    │                                            │ oder Logout im Kampf
    │                                            │ oder Disconnect-Frist im Kampf abgelaufen
    │                                            ▼
    └──────────── respawn (Checkpoint) ─────── dead
```

Unabhängig davon ist ein Charakter `online` oder offline.

| Aktion | exploring | in_combat | dead |
|---|---|---|---|
| move | ✓ | ✗ | ✗ |
| attack | ✓ (startet Kampf, nicht in `safe`-Räumen) | ✓ | ✗ |
| flee | ✗ | ✓ | ✗ |
| use_item | ✓ | ✓ (kostet Runde) | ✗ |
| equip / unequip | ✓ | ✗ | ✗ |
| pick_up / drop | ✓ | ✗ | ✗ |
| say | ✓ | ✓ | ✗ |
| group_invite / accept / leave / kick | ✓ | ✓ (außer kick) | ✓ (nur leave) |
| trade_* | ✓ | ✗ (laufender Handel wird abgebrochen) | ✗ |
| bank_deposit / bank_withdraw | ✓ (nur in Bank-Räumen) | ✗ | ✗ |
| buy / sell | ✓ (nur beim Händler im Raum) | ✗ | ✗ |
| respawn | ✗ | ✗ | ✓ |
| logout | ✓ | ✓ (mit Bestätigung → Tod) | ✓ |
| look / inventory | ✓ | ✓ | ✓ |

### Engine-Schnittstelle

Engine-Funktionen sind rein. Sie bekommen einen Snapshot des relevanten Weltausschnitts, den Content, die aktuelle Zeit und einen RNG.

```python
def resolve_attack(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
) -> ActionResult: ...


class ActionResult(BaseModel):
    changes: StateChanges
    events: list[GameEvent]


class GameEvent(BaseModel):
    audience: Audience  # Actor | Room(id) | Group(id) | Players([...]) | Global
    ...
```

Ein Event wie "Anna trifft den Goblin für 5 Schaden" geht an den Raum, "Du triffst den Goblin für 5 Schaden" an Anna selbst.

- Alle Aktionen haben die Form `resolve_<aktion>(snapshot, content, rules, now, rng, ...) -> ActionResult`. Abgelehnte Aktionen liefern nur ein `action.rejected`-Event an den Handelnden und keine Änderungen.
- Der Snapshot beschreibt den Raum, in dem die Aktion endet: bei `move` den Zielraum, bei `respawn` den Checkpoint.
- `StateChanges` enthält keine widersprüchlichen Einträge: Was gelöscht wird (Inventar-Einträge, Floor-Items, Kampfteilnahmen), steht nie zugleich als Schreibvorgang darin – Löschungen gewinnen. Daher ist die Reihenfolge beim Anwenden egal.
- Ergebnisse ohne Handelnden (z. B. Überfall durch den Ticker, `resolve_idle_ambush`) enthalten keine `Actor`-Events; der Betroffene wird über `Players([id])` benachrichtigt.

### Kampf

- Kämpfe sind an die **Monster-Instanz** gebunden. Mehrere Spieler können dasselbe Monster angreifen.
- Jede `attack`-Aktion ist eine Runde: Spieler schlägt zu, danach schlägt das Monster **diesen** Spieler zurück.
- Vor jeder Runde wird die Regeneration angewendet und `last_combat_at = now` gesetzt.
- In `safe`-Räumen gibt es keine Kämpfe.

### Kill-Credit & Beute

Die erste **Partei**, die einem Monster Schaden zufügt, "markiert" es (`tagged_by_party`). Eine Partei ist eine Gruppe oder ein einzelner Spieler ohne Gruppe. Das verhindert, dass Fremde einer Gruppe die Beute wegschnappen, indem sie den letzten Schlag setzen.

Stirbt das Monster:

- **XP** bekommt jeder Teilnehmer anteilig nach `damage_dealt`. Gruppenmitglieder im selben Raum, die nicht zugeschlagen haben, bekommen einen Grundanteil (`xp.group_idle_share` × Durchschnittsschaden der Angreifer als Gewicht), auch wenn ein Gruppenfremder den letzten Schlag setzt. Die Aufteilung verliert keine Punkte durch Rundung (Largest-Remainder-Verfahren).
- **Level-Up** setzt die Basiswerte aus `levels` und heilt den Charakter vollständig (effektive Max-HP inkl. Ausrüstung).
- **Gold** geht an die markierende Partei und wird gleichmäßig unter den Mitgliedern im Raum aufgeteilt. Rest-Münzen bekommt der Reihe nach das nächste Mitglied.
- **Items** werden für die markierende Partei gewürfelt:
  - Solo: direkt ins Inventar.
  - Gruppe: Round-Robin über die Mitglieder im Raum (`Group.loot_cursor`), direkt ins Inventar. Hat das nächste Mitglied keinen Platz, geht das Item an das darauffolgende mit Platz.
  - Hat niemand Platz: als `FloorItem` (`source = "loot"`) in den Raum, mit Log-Meldung "Inventar voll".
- Monster: `alive = false`, `respawn_at = now + respawn_seconds`, Markierung und `CombatParticipation` werden gelöscht, alle Teilnehmer wechseln zu `exploring`.

Fliehen oder sterben alle Mitglieder der markierenden Partei, wird die Markierung aufgehoben.

### Inventar

- Jeder nicht ausgerüstete `InventoryItem`-Eintrag belegt **einen Platz**, unabhängig von der Menge. Kapazität: `inventory.slots`.
- Stapelbare Items füllen zuerst bestehende Stapel bis `max_stack` auf, erst dann wird ein neuer Platz belegt.
- Gold belegt keinen Platz.
- Ausrüsten gibt einen Platz frei, Ablegen belegt einen. Ablegen schlägt fehl, wenn kein Platz frei ist.
- **Taschen** werden in einen von `inventory.bag_slots` Taschenplätzen ausgerüstet und erhöhen die Kapazität um ihre `bag_slots`:
  `kapazität = inventory.slots + Σ bag_slots ausgerüsteter Taschen`.
- Eine Tasche kann nur abgelegt werden, wenn danach alle Items noch Platz haben (die abgelegte Tasche selbst belegt dann ebenfalls einen Platz). Sonst: "Leere die Tasche zuerst."
- Die Kapazität ist ein abgeleiteter Wert (`engine/inventory.py`) und wird nicht gespeichert.
- Ausrüsten/Ablegen wird geprüft, indem das resultierende Inventar simuliert wird (Tausch im selben Slot, Teilen eines Stapels, Taschen). Sinkt die Max-HP dadurch, werden die HP auf das neue Maximum gekürzt.
- Items in einem offenen Handel (`PlayerSnapshot.locked_item_ids`) können nicht benutzt, ausgerüstet oder fallengelassen werden.
- Aufheben gesperrt für Items, die ein Charakter desselben Accounts fallengelassen hat (auch der Charakter selbst, da nur `dropped_by_account_id` gespeichert wird).
- Alle Wege, auf denen Items ins Inventar kommen (Aufheben, Loot, Handel), nutzen dieselbe Funktion `inventory.can_add(...)` / `inventory.add(...)` und prüfen vorher die Kapazität. Teilmengen sind erlaubt: Passen von 10 Pfeilen nur 4, werden 4 aufgehoben und 6 bleiben liegen.

### Handel

Ablauf in zwei Phasen, damit niemand im letzten Moment das Angebot austauschen kann:

1. **Anfrage:** `trade_request` an einen Spieler im selben Raum. Status `requested`, läuft nach `request_ttl_seconds` ab.
2. **Annahme:** `trade_accept` öffnet das Handelsfenster (`open`).
3. **Angebot:** Beide legen Items (`trade_offer_item`) und Gold (`trade_offer_gold`) ins Angebot oder nehmen sie wieder heraus. Jede Änderung erhöht `revision` und **setzt beide Bestätigungen zurück**.
4. **Bestätigung:** `trade_confirm` mit der aktuellen `revision`. Bestätigt jemand eine veraltete Revision, wird das abgelehnt.
5. **Ausführung:** Sobald beide die aktuelle Revision bestätigt haben, wird der Tausch in **einer Transaktion** ausgeführt: Besitz und Mengen prüfen, Kapazität auf beiden Seiten prüfen, Items und Gold umbuchen, `TradeLog` schreiben, Status `completed`. Schlägt eine Prüfung fehl, passiert nichts und beide Bestätigungen werden zurückgesetzt.

Regeln:

- Beide Charaktere müssen online, im selben Raum, im Zustand `exploring` und nicht bereits in einem anderen Handel sein.
- **Kein Handel zwischen Charakteren desselben Accounts.** Wird bei der Anfrage geprüft.
- Nicht handelbar: Items mit `tradable: false` und ausgerüstete Items (müssen erst abgelegt werden).
- Angebotene Items bleiben bis zur Ausführung im Inventar des Besitzers; sie können währenddessen nicht fallengelassen, benutzt oder ausgerüstet werden.
- Ein Handel wird automatisch abgebrochen, wenn einer der beiden den Raum verlässt, in einen Kampf gerät (auch durch Überfall), stirbt, sich ausloggt oder die Disconnect-Frist beginnt.
- **Endet ein Handel (`completed` oder `cancelled`), werden seine `TradeOffer`-Zeilen in derselben Transaktion gelöscht.** Ein angebotenes Item ist in der Datenbank gegen Löschen geschützt (kein Cascade); bleiben Angebote stehen, kann das Item danach nie mehr verbraucht, fallengelassen oder übertragen werden. Bei der Ausführung werden die Angebote vor dem Umbuchen der Items gelöscht.
- Rate Limit auf `trade_request`, damit niemand mit Anfragen zugespammt werden kann.

Umsetzung (`engine/trade.py`, `Repository.load_trade_snapshot`):

- Handelsaktionen arbeiten auf einem eigenen Snapshot (`TradeActionSnapshot`): Handelnder und Gegenüber mit Inventaren, alle aktiven Handel beider und der genannte Handel. Sperrreihenfolge: `Trade` → `Player` → `InventoryItem` → `TradeOffer`. Neue Handel entstehen nur unter der Sperre beider Spieler; die aktiven Handel werden nach dem Sperren der Spieler erneut gelesen, ein dazwischen entstandener führt zur Wiederholung. So hat niemand zwei Handel gleichzeitig (auch nicht bei zwei gleichzeitigen Anfragen an dieselbe Person).
- Pro Charakter höchstens ein aktiver Handel (Anfrage oder offen). Das Rate Limit für Anfragen ist das allgemeine Aktions-Rate-Limit; eine unerwünschte Anfrage lässt sich ablehnen (`trade_cancel`) und läuft sonst ab.
- Items wechseln den Besitzer als neue Inventarzeilen des Empfängers (eine Zeile wechselt nie den Besitzer); beim Geber wird der Stapel verringert bzw. gelöscht. Die Kapazitätsprüfung rechnet auf beiden Seiten mit dem Platz, den die eigenen abgegebenen Items freigeben.
- `apply_changes` schreibt Handel und Angebote vor allen Inventaränderungen und führt dazwischen einen Flush aus, damit gelöschte Angebote die angebotenen Items freigeben.
- Abbruch: Jede Operation, die Raum, Zustand, Online-Status oder Verbindung eines Spielers ändert (auch Überfall durch den Ticker, Tod, Logout, Verbindungsverlust), prüft danach dessen aktive Handel in einer eigenen Transaktion (`ActionService.check_trades`, `resolve_trade_check`) und bricht sie ab, wenn nicht mehr beide anwesend und frei sind. Der World-Ticker wiederholt die Prüfung (Schritt 8). Die Ausführung prüft alle Bedingungen ohnehin noch einmal.

### Bank

- Zugang nur in Räumen mit `bank: true`, im Zustand `exploring`.
- Jeder Charakter hat sein eigenes Lager mit `bank.slots` Plätzen; Stapelregeln wie im Inventar.
- `bank_deposit` verschiebt Items (auch Teilmengen) vom Inventar in die Bank, `bank_withdraw` zurück. Beide prüfen die Kapazität des Ziels.
- Ausgerüstete Items und Items in einem laufenden Handel können nicht eingelagert werden.
- Gold wird nicht eingelagert. Es bleibt beim Charakter und unterliegt der Todesstrafe (siehe *Offene Entscheidungen*).
- Bankinhalte sind vor Verlust beim Tod geschützt.

Umsetzung (`engine/bank.py`): Für Bankaktionen lädt und sperrt `load_action_snapshot(..., with_bank=True)` zusätzlich das Bankfach des Handelnden (`ActionSnapshot.bank`, Sperre nach dem Inventar). Bankfach-Einträge nutzen dieselben Stapel- und Kapazitätsfunktionen wie das Inventar (`bank.slots`). Passt nicht alles, wird so viel wie möglich verschoben und der Rest gemeldet.

### Händler

- Händler sind Content (`merchants.yaml`), keine Datenbankobjekte. Sie stehen in einem festen Raum und werden in der Raumansicht angezeigt.
- **Kaufen:** Preis aus `MerchantOffer.price` oder `value × buy_ratio`. Prüfung: genug Gold und Platz im Inventar. Vorrat ist unbegrenzt.
- **Verkaufen:** Erlös `floor(value × sell_ratio)`. Nicht verkaufbar: Items mit `sellable: false`, ausgerüstete Items, Items in einem laufenden Handel und Items, die der Händler laut `buys` nicht annimmt.
- Verkaufte Items verschwinden; Händler verkaufen sie nicht an andere Spieler weiter.
- Kaufen und Verkaufen sind je eine Transaktion und landen im Log des Spielers.
- Die Validierung beim Laden stellt sicher, dass sich kein Item günstiger kaufen als verkaufen lässt.

Umsetzung (`engine/merchant.py`, Preise in `content/prices.py`, gemeinsam für Engine und Content-Validierung): Kaufen ist „alles oder nichts“ – die ganze Menge muss bezahlbar sein und ins Inventar passen. Ein berechneter Kaufpreis ist mindestens 1 Gold. Verkaufen geht stück- oder stapelweise; stehen mehrere Händler im Raum, zeigt die Oberfläche das beste Angebot.

### Schutz vor Item-Transfer zwischen eigenen Charakteren

Da Charaktere desselben Accounts nicht miteinander handeln dürfen, werden auch die naheliegenden Umwege gesperrt:

- **Fallenlassen und mit dem anderen Charakter aufheben:** `FloorItem` speichert `dropped_by_account_id`. Charaktere desselben Accounts können diese Items nicht aufheben.
- **Gruppen-Loot:** entfällt, weil pro Account nur ein Charakter online ist.
- **Bank:** ist pro Charakter, nicht pro Account.
- **Händler:** verkaufte Items werden nicht weiterverkauft, also auch kein Umweg über den Händler.

Ein Transfer über einen befreundeten Dritten lässt sich technisch nicht verhindern. Das `TradeLog` ermöglicht aber, so etwas im Nachhinein nachzuvollziehen.

### Überfälle

Monster mit `ambush` können Spieler von sich aus angreifen:

- **Beim Betreten** eines Raums: Für jedes lebende, nicht kämpfende Monster mit `ambush` wird `chance_on_enter` gewürfelt.
- **Beim Verweilen:** Der World-Ticker würfelt `chance_per_tick` für Spieler, die im Zustand `exploring` in einem Raum mit solchen Monstern stehen.
- Nie in `safe`-Räumen, nie gegen tote oder offline Charaktere, nie gegen Spieler mit Levelabstand über `max_level_difference`.
- Bei Erfolg: Das Monster schlägt **zuerst** zu, das Opfer wechselt zu `in_combat`, das Monster ist von dessen Partei markiert. Raum und Gruppe bekommen ein Event, damit Mitspieler eingreifen können.
- Pro Monster höchstens ein Überfall gleichzeitig; ein bereits kämpfendes Monster überfällt niemanden.

### Gruppen

- Ein Spieler lädt einen anderen ein (`GroupInvite`, läuft ab). Mit `accept` tritt er bei; existiert noch keine Gruppe, wird sie dabei angelegt und der Einladende ist Leader.
- Maximal `max_group_size` Mitglieder; ein Charakter ist in höchstens einer Gruppe.
- `leave` jederzeit; `kick` nur durch den Leader und nicht im Kampf. Verlässt der Leader die Gruppe, wird das am längsten dabeiseiende Mitglied Leader. Bleibt ein Mitglied übrig, wird die Gruppe aufgelöst.
- Beim Ausloggen verlässt ein Charakter seine Gruppe automatisch.
- Gruppenmitglieder sehen HP und Aufenthaltsort der anderen; dafür gibt es den Kanal `group:{id}`.

Umsetzung (`engine/groups.py`, `Repository.load_group_snapshot`):

- Gruppenaktionen arbeiten auf einem eigenen Snapshot (`GroupActionSnapshot`): Handelnder, der andere Beteiligte (Eingeladene, Einladende, Gekickte), alle Mitglieder ihrer Gruppen – egal in welchem Raum – und die offenen Einladungen von und an diese Spieler. Sperrreihenfolge: `Group` → `Player` (sortiert) → `GroupInvite`. Hat jemand inzwischen die Gruppe gewechselt, wird wie üblich wiederholt.
- Einladen per Name (ohne Groß-/Kleinschreibung), nur Online-Spieler ohne Gruppe; mit Gruppe lädt nur die Gruppenleitung ein. Annehmen per `inviter_id` (die Einladung kann vor der Gruppe entstanden sein). Eine Einladung verfällt, wenn sich die Gruppe der Einladenden seitdem geändert hat. Nach dem Beitritt werden alle Einladungen von und an das neue Mitglied gelöscht. Einladungen können abgelehnt werden (`group_decline`).
- Gruppengröße und Leitung werden am gesperrten Zustand geprüft: Zwei gleichzeitige Beitritte zum letzten freien Platz schaffen nicht beide.
- Beitritt und Austritt ändern `group_id` und damit die Abos der Live-Verbindungen (`Relocation`, wie bei Raumwechseln).
- Logout (auch nach abgelaufener Frist) verlässt die Gruppe direkt danach in einer eigenen Transaktion; was dabei schiefgeht, räumt der World-Ticker auf (Offline-Charaktere mit Gruppe).
- Kill-Credit bleibt bei der markierenden Partei: Wird eine markierende Gruppe mitten im Kampf aufgelöst, zählen ihre ehemaligen Mitglieder, die gegen dieses Monster gekämpft haben (Kampfteilnahme mit diesem `party_key`), weiter als Partei. Wer eine bestehende Gruppe mitten im Kampf verlässt, bekommt von deren Beute nichts mehr (XP nach Schaden aber schon).
- DB-Regel: Mitglieder haben ein `group_joined_at` (Migration `0002`: `group_id IS NULL OR group_joined_at IS NOT NULL`). Die frühere Regel „beide oder keins“ ließ das `ON DELETE SET NULL` beim Löschen einer Gruppe auf Datenbankebene scheitern.

### Tod & Checkpoint

- Betritt ein Charakter einen Raum mit `checkpoint: true`, wird `checkpoint_room_id` automatisch gesetzt, mit einer Log-Meldung ("Du fühlst dich mit diesem Ort verbunden.").
- Bei HP ≤ 0: `state = dead`, Kampfteilnahme endet, **Verlustwürfe** werden sofort ausgeführt:
  - Gold: zufälliger Prozentsatz aus `death.gold_loss_percent`.
  - Jeder nicht ausgerüstete Inventar-Stapel: Verlust mit `item_loss_chance`.
  - Jedes ausgerüstete Item außer Taschen: Verlust mit `equipped_loss_chance`. Taschen gehen nie verloren, sonst wäre plötzlich das Inventar übervoll.
  - Items in der Bank sind nicht betroffen.
  - Höchstens `max_items_lost` Items; Items mit `keep_on_death` sind ausgenommen.
  - Verlorenes Gold und verlorene Items verschwinden endgültig aus dem Spiel. Es gibt keine Leiche und kein Zurückholen.
- Der Spieler sieht im Log genau, was verloren ging.
- Aktion `respawn`: Charakter erscheint im `checkpoint_room_id` mit vollen HP.

### Einloggen, Ausloggen & Verbindungsabbruch

| Situation | Ergebnis |
|---|---|
| Charakter wählen (Login) | `online = true`. Ist der Charakter `dead`, sieht er nur "Am Checkpoint erwachen". |
| Logout, nicht im Kampf | Laufender Handel wird abgebrochen, Charakter wird nach `town_room_id` gesetzt, verlässt die Gruppe, `online = false`. |
| Logout im Kampf | Bestätigungsdialog ("Dein Charakter wird sterben"). Danach Tod mit Verlustwürfen, dann offline. Beim nächsten Login: `dead`. |
| Verbindung bricht ab | `disconnected_at = now`. Der Charakter bleibt im Spiel und kann weiter angegriffen werden. |
| Reconnect innerhalb der Frist | `disconnected_at = null`, alles läuft weiter. |
| Frist abgelaufen, nicht im Kampf | Wie Logout: Stadt, Gruppe verlassen, offline. |
| Frist abgelaufen, im Kampf | Tod mit Verlustwürfen, offline. |
| Anderer Charakter desselben Accounts wird gewählt | Wie Logout des bisherigen Charakters (inkl. Tod, wenn im Kampf; mit Bestätigung). |

Die Frist (`disconnect_grace_seconds`) schützt vor dem Verlust eines Charakters durch kurze Netzwerkaussetzer, verhindert aber, dass man sich durch Schließen des Tabs aus einem verlorenen Kampf rettet.

Server-Neustart: Beim Start werden alle Charaktere mit `online = true` und ohne aktive Verbindung als disconnected markiert, sodass dieselbe Frist greift.

Umsetzung: Engine `engine/presence.py` (`resolve_login`, `resolve_logout`), Kontoregeln in `services/session_service.py`.

- Logout außerhalb des Kampfes setzt nach `town_room_id`; tote Charaktere bleiben liegen und erwachen nach dem nächsten Login am Checkpoint.
- Charakterwechsel loggt den bisherigen Charakter zuerst in einer eigenen Transaktion aus (im Kampf nur mit `confirm_death`, sonst `409`), danach wird der neue eingeloggt. Ein gleichzeitiger zweiter Login scheitert am partiellen Unique-Index (ein Online-Charakter pro Account) und wird als `409` gemeldet.
- Ein Login eines bereits eingeloggten Charakters (zweiter Tab, Reconnect) setzt nur `disconnected_at = null`, ohne den Raum erneut zu benachrichtigen.
- Abgelaufene Fristen arbeitet der World-Ticker ab (`connection_expired`, `resolve_connection_lost` = Logout mit `confirm_death`). Ob die Frist abgelaufen ist, wird am gesperrten Datensatz geprüft; ein Reconnect kurz davor gewinnt.
- Beim Logout wird die Gruppe verlassen (siehe *Gruppen*) und ein laufender Handel abgebrochen (siehe *Handel*).

### Respawn & Aufräumen

- **Monster:** Fällige `MonsterInstance` (`alive = false`, `respawn_at <= now`) werden mit vollen HP zurückgesetzt. Zusätzlich gibt es einen Lazy Check beim Betreten eines Raums.
- **Spawn-Items:** Wird ein Spawn-Item aufgehoben und hat es `respawn_seconds`, wird `next_spawn_at` gesetzt und das Item erscheint danach wieder.
- **Floor-Items** mit `source = "drop"` oder `"loot"` bekommen `expires_at = now + floor_item_ttl_seconds` und werden danach gelöscht. Abgelaufene Items werden beim Anzeigen sofort herausgefiltert, auch wenn der Ticker sie noch nicht gelöscht hat.

### World-Ticker

Ein Hintergrund-Task läuft alle `ticker_interval_seconds` und erledigt in getrennten, kurzen Transaktionen:

1. Monster-Respawns
2. Item-Respawns
3. Floor-Items aufräumen
4. Abgelaufene Disconnect-Fristen verarbeiten
5. Überfall-Würfe für verweilende Spieler
6. Abgelaufene Gruppeneinladungen, Handelsanfragen und nicht mehr relevante Login-Zähler löschen
7. Log-Einträge löschen, die älter als `LOG_RETENTION_DAYS` sind (Standard 30, `0` = nie; höchstens stündlich)

Log-Aufbewahrung (`services/log_retention.py`): Log-IDs wachsen mit der Schreibzeit, daher findet eine Binärsuche über den Primärschlüssel die Grenze (gemessen: 0,08 s bei einer Million Einträgen), gelöscht wird nach ID-Bereichen in kurzen Transaktionen zu je 5000 (667.000 Einträge in 2,8 s). Ein Index auf `created_at` ist dafür nicht nötig.

Bei mehreren Server-Prozessen holt sich der Ticker vorher ein PostgreSQL Advisory Lock (`pg_try_advisory_lock`), sodass nur ein Prozess tickt. Die Entscheidungen ("ist fällig", "wird überfallen") liegen in der Engine; der Ticker orchestriert nur.

Umsetzung (`app/services/world_ticker.py`, Engine: `engine/respawn.py`, `engine/cleanup.py`, `engine/presence.py`, `engine/ambush.py`):

- Kandidaten werden ungesperrt gesucht (Räume mit fälligen Monstern/Items/abgelaufenen Floor-Items, Spieler mit abgelaufener Frist, verweilende Spieler); entschieden wird erst am gesperrten Zustand. Jeder Raum bzw. Spieler ist eine eigene kurze Transaktion; Events werden nach dem Commit publiziert, ein fehlschlagender Schritt hält die anderen nicht auf.
- Schritte 4 (Fristen) und 5 (Überfälle) laufen über `ActionService` im Modus „System“: gleiche Sperren, Log-Einträge und Wiederholungen wie Spieleraktionen, aber kein Rate Limit, kein `last_action_at` und kein Lazy Respawn. Läuft gerade eine Aktion desselben Spielers, wird er bis zum nächsten Tick übersprungen.
- Überfall-Kandidaten: online, `exploring`, in einem nicht sicheren Raum mit einem lebenden Monster mit `ambush`. Auch Charaktere mit laufender Frist (Verbindung weg) können überfallen werden.
- Spawn-Items: Sperre auf die Floor-Items des Raums, dann auf die `ItemSpawnState`-Zeilen (dieselbe Reihenfolge wie beim Aufheben). Liegt das Spawn-Item noch, wird es nicht verdoppelt; Regeln, die aus dem Content entfernt wurden, werden nur zurückgesetzt. Ein neues Spawn-Item erzeugt ein Raum-Event.
- Abgelaufene Floor-Items verschwinden ohne Event (sie werden ohnehin schon ausgeblendet).
- Gelöscht werden abgelaufene Gruppeneinladungen und nur Handelsanfragen im Status `requested`.
- Schritt 7: Offline-Charaktere, die noch in einer Gruppe sind, verlassen sie (Sicherheitsnetz für den Logout).
- Schritt 8: Aktive Handel, deren Beteiligte nicht mehr beide anwesend und frei sind, werden abgebrochen (Sicherheitsnetz).
- Leader-Wahl: Der Lock hängt an einer eigenen, dauerhaft gehaltenen Verbindung; jeder Tick prüft, ob sie noch lebt. Beim Freigeben wird der Lock explizit zurückgegeben (eine nur in den Pool zurückgegebene Verbindung hielte ihn sonst weiter). Bricht die Verbindung ab, übernimmt ein anderer Prozess beim nächsten Tick.
- Abschaltbar mit `WORLD_TICKER=0` (z. B. für API-Tests); getestet wird mit fester Zeit und einem `tick()` pro Test.

### Zufall & Zeit

Die Engine ruft nie `random.*` oder `datetime.now()` direkt auf. RNG und `now` werden übergeben. In Tests: fester Seed und feste Zeit.

## Persistenz & Nebenläufigkeit

### Automatisches Speichern

Jede Aktion und jeder Ticker-Schritt ist **eine Datenbanktransaktion**. Nach dem Commit ist der Zustand gespeichert. Es gibt keinen Zustand, der nur im Speicher des Servers lebt (abgesehen vom Content und offenen SSE-Verbindungen).

### Ablauf einer Aktion

1. Route validiert die Aktion und ermittelt den aktiven Charakter aus der Session.
2. Rate Limit über `last_action_at` prüfen.
3. `ActionService` öffnet eine Transaktion und sperrt die beteiligten Zeilen (`Repository.load_action_snapshot`). Pro Transaktion – also auch pro Wiederholung nach `SnapshotChangedError` – wird ein **neues** `Repository` erzeugt; ein wiederverwendetes wirft `StaleRepositoryError`.
4. Der Service prüft am **gesperrten** Datensatz, dass der Charakter online und nicht gelöscht ist (Zustandsregeln wie „tot“ prüft die Engine). Das Rate Limit aus Schritt 2 wird ebenfalls erst hier am gesperrten Datensatz verbindlich gesetzt.
5. Engine berechnet das Ergebnis.
6. Service schreibt Änderungen und Log-Einträge.
7. **Commit.**
8. **Erst nach dem Commit** werden Events publiziert. Ein Rollback darf nie Events erzeugen.
9. Route rendert die Antwort-Partials für den Handelnden.

Umsetzung: `ActionService.execute` (`app/services/action_service.py`).

- Vor der Aktion werden fällige Monster im Raum lazy respawnt (`engine/respawn.py`).
- Rate Limit: Aktionen innerhalb von `min_action_interval_ms` nach der letzten werden mit „Nicht so schnell.“ abgelehnt – ohne Zustandsänderung und ohne Log-Eintrag. Schon **vor** jeder Zeilensperre wird abgewiesen, wenn die ungesperrte Vorab-Lesung zu kurz nach der letzten Aktion liegt oder bereits eine andere Aktion desselben Spielers läuft (nicht blockierendes `pg_try_advisory_xact_lock` pro Spieler). So kann ein Client mit vielen parallelen Anfragen weder Verbindungen binden noch andere Spieler im Raum ausbremsen. Verbindlich bleibt die Prüfung am gesperrten Datensatz. Eine rückwärts gestellte Uhr sperrt niemanden aus.
- Veröffentlichen ist „best effort“: Schlägt es nach dem Commit fehl, wird das geloggt und die Aktion trotzdem als erfolgreich beantwortet (sonst würde der Client sie erneut senden). Empfänger holen verpasste Events über ihre Log-Einträge nach.
- Log-Einträge bekommt jeder Empfänger eines Events (Handelnder, `Players`, Online-Spieler im Raum, Gruppenmitglieder; ausgeschlossene Spieler nicht; `Global` wird nicht pro Spieler geloggt).
- Wiederholt wird bei `SnapshotChangedError`, Deadlock (`40P01`) und Serialisierungsfehler (`40001`), höchstens dreimal; danach `RetryLimitExceededError`.
- Ist der Charakter offline, gelöscht oder unbekannt (auch wenn das erst während des Wartens auf die Sperre passiert), gibt es `NotPlayingError` (HTTP 401).
- Zufall: `random.SystemRandom` (Würfe sind nicht aus früheren Ergebnissen vorhersagbar); Tests injizieren einen festen Seed und eine feste Uhr.
- Login (`ActionService.login`) läuft über denselben Ablauf (Sperren, Engine `resolve_login`, Log, Publizieren nach Commit); es ist die einzige Operation für einen Offline-Charakter und unterliegt keinem Rate Limit. `logout` ist ebenfalls vom Rate Limit ausgenommen (nur eine gleichzeitig laufende Aktion wird abgewiesen), damit niemand in der Welt festhängt. `online` und `disconnected_at` werden wie jede Änderung über `PlayerUpdate` geschrieben.
- Alle Aktionen aus `GameAction` sind umgesetzt.

### Locking

- `SELECT ... FOR UPDATE` auf betroffene Zeilen.
- **Feste Sperrreihenfolge**, um Deadlocks zu vermeiden: (`Account`, nur beim Anlegen von Charakteren für das Charakterlimit) → `Group` → `Trade` → `Player` (sortiert nach ID) → `InventoryItem` → `BankItem` → `MonsterInstance` → `FloorItem` → `ItemSpawnState`.
- Die Handelsausführung sperrt den `Trade`, beide Spieler und alle beteiligten Inventar-Einträge. Damit kann ein Item nicht gleichzeitig gehandelt und fallengelassen werden.
- Beutevergabe in Gruppen sperrt die Gruppe und alle Empfänger, damit Round-Robin und Inventar konsistent bleiben.
- Überfälle aus dem Ticker sperren Monster und Opfer in derselben Reihenfolge wie Spieleraktionen.
- Umsetzung: `Repository.load_action_snapshot` liest zuerst ungesperrt, wer im Raum ist und welche Gruppen betroffen sind, und sperrt dann in der festen Reihenfolge. Ist danach ein Spieler in einer nicht gesperrten Gruppe (Gruppenwechsel dazwischen), wird `SnapshotChangedError` geworfen; der Service rollt zurück und wiederholt die Transaktion.
- Im Handel angebotene Items werden nach dem Sperren der Spieler gelesen (`locked_item_ids`); Angebote entstehen unter derselben Spielersperre.
- Alle sperrenden Abfragen laufen mit `populate_existing`: Bereits in der Session geladene Zeilen werden mit den unter der Sperre gelesenen Werten aktualisiert. Sonst könnte eine Transaktion veraltete Werte (z. B. Gold) zurückschreiben, obwohl sie korrekt auf die Sperre gewartet hat. Die Vorab-Lesung ist rein spaltenbasiert. Hat sich der Handelnde inzwischen bewegt, gibt es ebenfalls `SnapshotChangedError`.
- `Repository.apply_changes` schreibt **nur Zeilen, die dieselbe Repository-Instanz gesperrt hat** (Spieler, Inventar, Monster, Kampfteilnahmen, Floor-Items und neue Floor-Items/Spawn-Zustände nur im gesperrten Raum, Gruppen). Alles andere – etwa das Löschen eines Items eines Spielers in einem anderen Raum oder ein Inventar-Eintrag, der den Besitzer wechselt – wird mit `InconsistentChangesError` abgelehnt.
- Ein im Handel angebotenes Item kann in der Datenbank nicht gelöscht werden (`trade_offer` → `inventory_item` ohne Cascade); der Handel muss vorher abgebrochen oder ausgeführt werden.
- Der Seed läuft unter einem Advisory-Lock, damit parallele Seeds keine Monster doppelt anlegen.
- `ItemSpawnState`: Aufheben setzt `next_spawn_at` nur, während es das zugehörige Spawn-Floor-Item gesperrt hält. Der Ticker darf ein Spawn-Item nur neu anlegen, wenn er die `ItemSpawnState`-Zeile mit `FOR UPDATE` sperrt und `next_spawn_at` danach noch fällig ist (eine Sperre auf die Floor-Items des Raums reicht nicht, weil nach dem Aufheben dort oft keine Zeile mehr existiert). Zusätzlich tickt immer nur ein Prozess (Advisory Lock, siehe World-Ticker).
- Zwischen dem Sperren (`load_action_snapshot`) und `apply_changes` sind keine Savepoints erlaubt (`begin_nested`): Ein Rollback auf einen Savepoint gibt in PostgreSQL die danach gesetzten Zeilensperren frei. Das `Repository` lehnt Sperren innerhalb eines Savepoints ab.

### Migrationen

- Schema-Änderungen ausschließlich über Alembic.
- Content-IDs sind stabil. Umbenennen oder Entfernen erfordert eine Datenmigration für Charaktere, Inventare, Monster-Instanzen und Floor-Items.

## Echtzeit

### Kanäle

| Kanal | Empfänger | Beispiele |
|---|---|---|
| `room:{room_id}` | Alle Online-Spieler im Raum | Spieler kommt/geht, Kampfmeldungen, Überfall, Respawn, `say` |
| `group:{group_id}` | Gruppenmitglieder | Mitglied wird angegriffen, HP-Änderungen, Beute, Beitritt/Austritt |
| `player:{player_id}` | Ein Charakter | Einladungen, Handelsanfragen & -änderungen, Beute, Überfall auf einen selbst |
| `global` | Alle | Server-Ankündigungen |

### Ablauf

- Jeder eingeloggte Client hält eine SSE-Verbindung auf `GET /stream`.
- Der Prozess, der die Verbindung hält, abonniert `player:{id}`, `global`, den aktuellen Raum und ggf. die Gruppe. Bei Raum- oder Gruppenwechsel werden die Abos umgeschaltet.
- `NOTIFY` transportiert nur kleine JSON-Payloads (Event-Typ + IDs, Limit 8000 Byte).

Umsetzung (`app/realtime/`):

- Es gibt **einen** PostgreSQL-Kanal (`game_events`). Jeder Prozess lauscht einmal und verteilt lokal an seine Verbindungen (`ConnectionManager`); die „Kanäle“ Raum/Gruppe/Spieler/global sind Filter auf den Subscriptions, keine eigenen `LISTEN`s.
- Nachrichten: `event` (ein `GameEvent`) und `relocate` (Raum/Gruppe eines Spielers hat sich geändert). Relocations werden vor den Events derselben Aktion gesendet, damit ein Spieler Events im neuen Raum schon bekommt.
- Passt ein Event nicht in 8000 Byte, entfallen seine `data`, und vom Text bleibt der längste passende Anfang (UTF-8-genau). Passt es selbst ohne Text nicht (z. B. riesige `exclude_player_ids`), wird es übersprungen und geloggt. Der Client kann den Zustand jederzeit über die API neu laden.
- Langsame Clients blockieren nichts: Ist die Warteschlange einer Verbindung voll, wird verworfen und die Verbindung als `needs_reload` markiert (der Client muss neu laden).
- Bricht die `LISTEN`-Verbindung ab, werden sofort alle lokalen Verbindungen als `needs_reload` markiert, und der Bus verbindet sich mit exponentiellem Backoff (bis 30 s) neu; verpasste Events holen Clients über `GET /log?after=` nach.
- Senden: eine Verbindung mit Timeouts (5 s), bei einem Verbindungsfehler genau ein Retry auf frischer Verbindung. Jede Nachricht trägt eine zufällige `id`; Empfänger verwerfen bereits gesehene ids, damit ein Retry nichts doppelt zustellt. Scheitert das Senden endgültig, werden die lokalen Verbindungen als `needs_reload` markiert; andere Prozesse mit intakter `LISTEN`-Verbindung erfahren von einem halb gesendeten Batch nichts und holen über das Log nach.
- Der SSE-Handler rendert Events zu HTMX-Partials und sendet sie als benannte SSE-Events (`log`, `room`, `status`, `group`).
- Der Handelnde bekommt seine Ergebnisse über die HTTP-Antwort, nicht zusätzlich über SSE.

### Verbindungsstatus

- Öffnen der SSE-Verbindung setzt `disconnected_at = null`. Schließen setzt `disconnected_at = now` (siehe *Einloggen, Ausloggen & Verbindungsabbruch*).
- Ein Heartbeat alle 15 s erkennt tote Verbindungen.
- Beim Reconnect lädt der Client Raum, Status und Gruppe neu; verpasste Log-Einträge holt er über `GET /log?after={last_id}`.

Umsetzung (`app/api/stream.py`, `SessionService`):

- Der Heartbeat prüft zugleich, ob der Charakter noch online ist (sonst `event: ended`, Stream endet – etwa nach Logout oder Charakterwechsel in einem anderen Tab), und löscht ein `disconnected_at`, das während einer offenen Verbindung gesetzt wurde (z. B. Neustart eines anderen Workers). Geschrieben wird nur dann.
- Erst wenn die **letzte** Verbindung eines Charakters in diesem Prozess endet, wird `disconnected_at = now` gesetzt (als Task, da der Stream gerade abgebrochen wird).
- Benannte SSE-Events: `log` (fertig gerendertes, escaptes `<li class="log-<kind>">`, wird ans Log angehängt), `update` (die Welt um den Spieler hat sich geändert; die Seite lädt ihre Panels über `GET /ui/panels` nach, entprellt; nicht bei Chat), `reload` (Warteschlange übergelaufen) und `ended`. Statt je Event eigene Partials pro Verbindung zu rendern (DB-Lesen pro Event und Zuschauer), holt sich jeder Client den Zustand einmal selbst.

## API

| Methode | Pfad | Zweck |
|---|---|---|
| `POST` | `/register` | Account anlegen |
| `POST` | `/login` / `/logout` | Account-Session verwalten |
| `GET` | `/characters` | Charakterauswahl |
| `POST` | `/characters` | Charakter anlegen |
| `POST` | `/characters/{id}/play` | Charakter einloggen |
| `DELETE` | `/characters/{id}` | Charakter löschen (Soft Delete, nur offline) |
| `GET` | `/` | Spielansicht |
| `POST` | `/actions` | Aktion ausführen |
| `GET` | `/stream` | SSE-Verbindung |
| `GET` | `/log?before={id}` / `?after={id}` | Log nachladen |

Umsetzung (`app/api/`; die JSON-Routen oben bleiben neben den HTML-Seiten bestehen, z. B. für Tests und andere Clients):

- Session: signiertes Cookie (`SessionMiddleware`, Secret aus `SESSION_SECRET`, `SameSite=Lax`, mit `SESSION_HTTPS_ONLY=1` nur über HTTPS) mit `account_id` und dem gewählten `player_id`. Ob der Charakter wirklich in der Welt ist, entscheidet immer `Player.online` in der Datenbank.
- `POST /register` legt den Account an und meldet ihn an. Benutzernamen: 3–32 Zeichen `[A-Za-z0-9_.-]`, eindeutig ohne Groß-/Kleinschreibung; Passwort 8–128 Zeichen, argon2-Hash (im Worker-Thread; bei unbekanntem Benutzernamen wird gegen einen Dummy-Hash geprüft, damit die Antwortzeit nichts verrät).
- Charakternamen: 3–20 Buchstaben (inkl. Umlaute). Neue Charaktere starten mit den Werten von Level 1 in `start_room_id` (zugleich Checkpoint). Das Limit `max_characters_per_account` hält auch bei gleichzeitigen Anfragen (Sperre auf die Account-Zeile).
- `POST /characters/{id}/play` und `POST /logout` nehmen optional `{"confirm_death": true}`; ohne Bestätigung antworten sie im Kampf mit `409`. `POST /actions` mit `logout` liefert stattdessen das Event `session.confirm_death`.
- `POST /actions`: Antwort `{"events": [{kind, text, data}]}` mit den Events für den Handelnden. `401`, wenn der Charakter nicht (mehr) in der Welt ist; `503`, wenn Sperrkonflikte alle Wiederholungen überdauern.
- `GET /log`: höchstens 50 Einträge, älteste zuerst.

### Aktionen

```python
class MoveAction(BaseModel):
    type: Literal["move"]
    direction: Direction


class AttackAction(BaseModel):
    type: Literal["attack"]
    target_id: UUID


class SayAction(BaseModel):
    type: Literal["say"]
    text: str = Field(min_length=1, max_length=300)


class GroupInviteAction(BaseModel):
    type: Literal["group_invite"]
    player_name: str


class GroupAcceptAction(BaseModel):  # ebenso GroupDeclineAction ("group_decline")
    type: Literal["group_accept"]
    inviter_id: UUID


class LogoutAction(BaseModel):
    type: Literal["logout"]
    confirm_death: bool = False  # im Kampf erforderlich


class TradeOfferItemAction(BaseModel):
    type: Literal["trade_offer_item"]
    trade_id: UUID
    inventory_item_id: UUID
    quantity: PositiveInt


class TradeConfirmAction(BaseModel):
    type: Literal["trade_confirm"]
    trade_id: UUID
    revision: int  # muss der aktuellen Revision entsprechen


class PickUpAction(BaseModel):
    type: Literal["pick_up"]
    floor_item_id: UUID
    quantity: PositiveInt | None = None  # None = so viel wie passt


class DropAction(BaseModel):
    type: Literal["drop"]
    inventory_item_id: UUID
    quantity: PositiveInt | None = None  # None = ganzer Stapel


# ... flee, use_item, equip, unequip, inventory,
#     group_leave, group_kick, respawn, look,
#     trade_request, trade_accept, trade_offer_gold, trade_remove_item, trade_cancel,
#     bank_deposit, bank_withdraw, buy, sell

Action = Annotated[
    MoveAction | AttackAction | SayAction | GroupInviteAction | LogoutAction | ...,
    Field(discriminator="type"),
]
```

### Fehler & Schutz

- Ungültige Eingabe → `422`.
- Nicht eingeloggt oder kein aktiver Charakter → `401` bzw. Redirect.
- Aktion im aktuellen Zustand nicht erlaubt → kein HTTP-Fehler, sondern Log-Meldung.
- `logout` im Kampf ohne `confirm_death` → Antwort mit Bestätigungsdialog.
- Rate Limit über `min_action_interval_ms`.
- **Login- und Registrierungs-Limit** (`services/login_guard.py`, Tabelle `login_throttle`): Jeder Login-Versuch zählt gegen den Benutzernamen (ohne Groß-/Kleinschreibung) und gegen die Client-Adresse, jede Registrierung gegen die Adresse. Wer sein Kontingent pro Zeitfenster aufgebraucht hat, wird für die Sperrzeit abgewiesen: `429` mit `Retry-After` (Formular: Hinweis auf der Seite). Abgewiesen wird **vor** dem argon2-Hash, sodass Versuche weder Passwörter testen noch CPU binden. Die Zähler liegen in der Datenbank (für alle Worker gemeinsam); jeder Versuch reserviert unter Zeilensperre, daher kommen auch viele parallele Anfragen nicht über das Limit. Ein erfolgreicher Login setzt den Namen zurück und gibt der Adresse den Versuch zurück. Bekannter Kompromiss: Wer absichtlich falsch rät, sperrt einen fremden Namen für die (kurze) Sperrzeit. Einstellungen (Umgebung): `LOGIN_ATTEMPTS_PER_USER` (5), `LOGIN_ATTEMPTS_PER_ADDRESS` (30), `REGISTRATIONS_PER_ADDRESS` (10), `LOGIN_WINDOW_SECONDS` (900), `LOGIN_LOCKOUT_SECONDS` (900).
- CSRF: Das Session-Cookie ist `SameSite=Lax`, Browser senden es also nicht bei Formular-POSTs von fremden Seiten; die JSON-Routen erzwingen bei fremden Origins zusätzlich einen CORS-Preflight.
- `say`-Texte und Charakternamen werden beim Rendern escaped (Jinja-Autoescape). Charakternamen werden zusätzlich auf erlaubte Zeichen beschränkt.

## Frontend

- Jinja2 + HTMX, kein Build-Schritt.
- Seiten: Login/Registrierung, Charakterauswahl, Spielansicht.
- Spielansicht: Log (`aria-live="polite"`), Raum mit Monstern und anwesenden Spielern, Statusleiste, Aktionsleiste, Inventar, Gruppenpanel (Mitglieder mit HP), Chat-Eingabe.
- Handelsfenster: beide Angebote nebeneinander, Bestätigungsstatus beider Seiten. Ändert sich ein Angebot, wird die eigene Bestätigung sichtbar zurückgesetzt und die Änderung hervorgehoben.
- Inventaranzeige mit belegten/freien Plätzen (z. B. "14/32") und Taschenplätzen.
- Bankansicht in Bank-Räumen: Inventar und Bank nebeneinander, Items per Klick verschieben.
- Händleransicht: Sortiment mit Preisen, daneben eigenes Inventar mit Verkaufserlös pro Item. Nicht leistbare oder nicht verkaufbare Items sind ausgegraut.
- Überfälle werden im Log hervorgehoben und schalten die Aktionsleiste sofort auf Kampf um.
- SSE über `hx-ext="sse"`; benannte Events tauschen gezielt die passenden Bereiche aus.
- Buttons werden während laufender Requests deaktiviert (`hx-disabled-elt`).
- `beforeunload`-Warnung, wenn der Tab im Kampf geschlossen wird.

Umsetzung (`app/api/views.py`, `app/web/`, `app/services/view_service.py`):

- Seiten: `/welcome` (Login & Registrierung), `/select` (Charakterauswahl, Anlegen, Löschen, Abmelden), `/` (Spielansicht). Normale Formulare mit Post-Redirect-Get; Fehler werden in der Seite angezeigt. Beim Wechsel aus einem Kampf zeigt `/select` eine Bestätigung („stirbt und verliert Gold und Gegenstände“).
- Spielansicht: Status (inkl. `data-state` für die `beforeunload`-Warnung), Raum (Gegner mit HP, Spieler, Händler, Bodenitems, Ausgänge), Aktionsleiste (je nach Zustand: Angreifen/Fliehen, Erwachen, Umsehen, Ausloggen mit Bestätigung im Kampf), Inventar (Plätze, Taschen, Anlegen/Ablegen/Benutzen/Fallenlassen), Log (`aria-live`) und Chat.
- Aktionen: `POST /ui/actions` (formularkodiert, dieselbe `GameAction`-Validierung). Antwort: die Log-Zeilen des Handelnden plus alle Panels als `hx-swap-oob`, damit die Ansicht immer dem gespeicherten Zustand entspricht. Logout bzw. nicht mehr im Spiel → `HX-Redirect` auf `/select`.
- Die Panels liest `ViewService` ungesperrt und ohne Log-Einträge; abgeleitete Werte (effektive Werte, regenerierte Monster-HP, Kapazität) kommen aus denselben Engine-Funktionen wie bei den Aktionen. Buttons zeigen nur, was im aktuellen Zustand sinnvoll ist; entschieden wird trotzdem ausschließlich in der Engine.
- Nach einem SSE-Reconnect lädt die Seite Log und Panels neu. Buttons sind während ihres Requests deaktiviert.
- htmx 2.0.4 und htmx-ext-sse 2.2.2 liegen unverändert unter `static/vendor/` (0BSD); kein CDN, kein Build-Schritt.
- Gruppenpanel: Mitglieder mit HP-Balken, Aufenthaltsort und Leitung, offene Einladungen (Annehmen/Ablehnen), Einladen per Name, Verlassen, Entfernen (nur Leitung, nicht im Kampf).
- Händleransicht: Sortiment mit Preisen (nicht leistbar = ausgegraut), am Inventar „Verkaufen“ mit Erlös pro Stück und für den ganzen Stapel bzw. „Unverkäuflich“.
- Bankansicht in Bank-Räumen: Bankfach mit Plätzen und „Abheben“, am Inventar „Einlagern“ (ganzer Stapel).
- Handelsfenster: „Handeln“ an anwesenden Spielern, Anfrage annehmen/ablehnen, beide Angebote nebeneinander mit Bestätigungsstatus (bestätigte Seite grün) und Stand (`revision`), Gold setzen, Items per „Anbieten“ am Inventar hinzufügen und per ✕ zurücknehmen. Angebotene Items zeigen „im Angebot“ und keine anderen Aktionen. Jede Änderung setzt die Bestätigungen zurück und erscheint im Log beider Seiten.

## Coding Standards

- Alle API-Ein- und Ausgaben sind Pydantic-Modelle.
- `mypy --strict` und `ruff` müssen fehlerfrei durchlaufen.
- Keine Spiellogik in Routen, Templates, dem SSE-Handler oder dem Ticker.
- Kein hartcodierter Content oder Balancing-Wert im Code; alles steht in `data/`.
- Engine-Funktionen haben keine Seiteneffekte (kein I/O, keine globale Zufallsquelle, keine Systemzeit).
- Events werden ausschließlich nach erfolgreichem Commit publiziert.

## Tests

| Ebene | Was | Wie |
|---|---|---|
| Engine | Kampf, Kill-Credit, Beuteverteilung (Solo/Gruppe/Round-Robin, volles Inventar), Inventar-Kapazität, Stapel & Taschen (inkl. Ablegen bei vollem Inventar), Bank, Kaufen & Verkaufen, Handelsregeln & Revisionen, Verlustwürfe beim Tod, Regeneration, Überfall-Würfe, Respawn, Disconnect-Regeln, Zustandsregeln | Unit-Tests mit festem Seed und fester Zeit |
| Content | Alle YAML-Dateien laden & Referenzen validieren, keine Gelddruck-Schleife bei Händlern | Test lädt das echte `data/`-Verzeichnis |
| Service | Aktion → gespeicherter Zustand + publizierte Events | PostgreSQL via Testcontainers, Fake-Event-Bus |
| Nebenläufigkeit | Zwei Spieler greifen gleichzeitig an; Gruppenbeute bei gleichzeitigem Austritt; Überfall während Spieler den Raum verlässt; zwei Spieler heben dasselbe Item auf; Item wird während der Handelsausführung fallengelassen; Angebot ändert sich zwischen den beiden Bestätigungen; Item wird gleichzeitig verkauft und eingelagert | Parallele Tasks gegen echte PostgreSQL |
| Ticker | Respawn, Aufräumen, abgelaufene Fristen (mit/ohne Kampf) | Feste Zeit, ein Tick-Aufruf pro Test |
| Realtime | Events erreichen Raum/Gruppe, aber nicht Unbeteiligte | Zwei SSE-Clients im Test |
| API | Auth, Charakterauswahl, Logout-Bestätigung, Rate Limit | `httpx.AsyncClient` |

Der Content-Test läuft in CI, damit kaputte YAML-Dateien nie gemerged werden.

Nebenläufigkeitsfälle ohne eigenen Platz in den Feature-Tests stehen in `tests/services/test_concurrency_cases.py`. Jeder Fall läuft in beiden Reihenfolgen: Eine dritte Transaktion hält die umkämpfte Zeile, beide Operationen reihen sich in die Sperrwarteschlange ein, dann wird freigegeben. PostgreSQL vergibt Zeilensperren in Warteschlangenreihenfolge, daher wird pro Reihenfolge das exakte serielle Ergebnis geprüft. Betreffen beide Operationen denselben Charakter (z. B. Überfall durch den Ticker und `move`), wartet die zweite nicht, sondern wird über den Claim pro Spieler abgewiesen (`BUSY`); der Ticker würfelt beim nächsten Tick erneut.

## Deployment

- Ein Container für die App (uvicorn, mehrere Worker möglich), ein Container für PostgreSQL.
- Reverse Proxy (z. B. Caddy oder nginx) muss für `/stream` Buffering deaktivieren und lange Verbindungen erlauben.
- Hinter dem Proxy uvicorn mit `--proxy-headers --forwarded-allow-ips <Proxy-IP>` starten, sonst haben alle Clients die Adresse des Proxys und teilen sich ein Login-Limit.
- Content-Änderungen erfordern einen Neustart; danach Seed-Befehl ausführen.

## Offene Entscheidungen

- **PvP:** für eine spätere Version vorgesehen. Die `safe`-Räume sind als Schutzzonen bereits vorbereitet; offen sind Regeln gegen das Töten von Neulingen und was ein PvP-Tod kostet.
- **Gold in der Bank:** Soll man Gold einlagern können, um es vor der Todesstrafe zu schützen? Aktuell nicht, Gold ist immer beim Charakter.
- **Bank erweitern:** feste Größe (aktuell) oder zusätzliche Plätze gegen Gold?
- **Händlervorrat:** unbegrenzt (aktuell) oder begrenzt mit Nachschub über Zeit?
- **Taschen in der Bank:** Erweitern Taschen auch das Banklager, wenn man sie dort einlagert, oder sind sie dort nur normale Items (aktuell)?