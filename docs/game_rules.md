# Spielregeln

Wie das Spiel aus Sicht der Spielenden funktioniert. Technische Umsetzung: [`ARCHITECTURE.md`](../ARCHITECTURE.md). Alle Zahlen kommen aus [`data/rules.yaml`](../data/rules.yaml) bzw. den Content-Dateien in [`data/`](../data/); die Tabelle *Regelwerte* am Ende wird von `tests/content/test_game_rules_doc.py` gegen `rules.yaml` geprüft.

## Die Welt

Alle spielen in **einer gemeinsamen, dauerhaften Welt**. Wer im selben Raum steht, sieht sich gegenseitig, dieselben Monster und dieselben Gegenstände am Boden. Gespeichert wird automatisch nach jeder Aktion.

Ein Account kann mehrere Charaktere haben (höchstens `max_characters_per_account`), aber immer nur **einer ist gleichzeitig im Spiel**. Wählst du einen anderen, wird der bisherige ausgeloggt.

Neue Charaktere starten auf Stufe 1 auf dem **Marktplatz von Oakhaven**.

### Räume

| Raum | Besonderheit |
|---|---|
| Marktplatz von Oakhaven | Startpunkt, sicher, Checkpoint |
| Bank von Oakhaven (nördlich) | sicher, Bank |
| Kaufmannsstand (östlich) | sicher, Alchemistin Elira (Tränke, Proviant, Taschen) |
| Schmiede (westlich) | sicher, Schmied Brandt (Waffen und Rüstung) |
| Südtor zum Wald (südlich) | Übergang in die Wildnis |
| Dunkler Wald | Goblin-Späher und Hungriger Wolf |
| Goblin-Lager (östlich des Waldes) | zwei Goblin-Späher und ein Goblin-Krieger, frisches Brot liegt aus |
| Moorpfad (südlich des Waldes) | Moorschleicher und Hungriger Wolf, Moorkraut wächst am Weg |
| Lagerfeuer am Moor (östlich des Moorpfads) | sicher, Checkpoint |
| Eingestürztes Tor (südlich des Moorpfads) | ein Skelettwächter |
| Halle der Ruine | zwei Skelettwächter, eine Treppe führt hinab |
| Alte Mine (unter der Halle) | der Höhlentroll |
| Steiler Anstieg (östlich des Lagerfeuers) | zwei Felsharpyien |
| Zwergenposten Khazdur (nördlich des Anstiegs) | sicher, Checkpoint, Bank, Waffenmeisterin Brunhild (Zwergenstahl, Elixiere) |
| Frostklamm (östlich des Postens) | zwei Frostwölfe, Frostbeeren wachsen im Eis |
| Gletscherhang (oberhalb der Klamm) | zwei Eisgolems |
| Hort des Eisdrachen (nördlich des Hangs) | Eisdrache Hrimfang – nur für Gruppen |
| Verlassene Grubenwache (unter der Mine) | sicher, Checkpoint |
| Tiefe Stollen (östlich der Grubenwache) | zwei Grubenghule |
| Spinnengrotte | drei Höhlenspinnen |
| Nest der Spinnenkönigin (unter der Grotte) | Spinnenkönigin Ysvara – nur für Gruppen |

Die Welt wird nach außen gefährlicher: Rund um Oakhaven und im Wald kommt man mit Stufe 1–3 zurecht, Moor, Ruine und Mine sind für Stufe 3–5. Das **Grauzahn-Gebirge** östlich des Moors und **die Tiefen** unter der Mine sind für Stufe 6–10; die beiden Bosse dort (Spinnenkönigin, Eisdrache) schafft man nur in der Gruppe oder mit vielen Tränken. Im Zwergenposten gibt es eine zweite Bank.

In **sicheren Räumen** wird nicht gekämpft und niemand wird überfallen. Betrittst du einen **Checkpoint**, merkt sich dein Charakter diesen Ort; dort erwachst du nach dem Tod.

## Werte & Stufen

Jeder Charakter hat **Lebenspunkte (HP)**, **Angriff** und **Verteidigung**. Die Grundwerte hängen von der Stufe ab, ausgerüstete Gegenstände kommen dazu.

| Stufe | Erfahrung (XP) ab | Max. HP | Angriff | Verteidigung |
|---|---|---|---|---|
| 1 | 0 | 20 | 5 | 2 |
| 2 | 100 | 26 | 7 | 3 |
| 3 | 250 | 33 | 9 | 4 |
| 4 | 500 | 42 | 12 | 5 |
| 5 | 900 | 52 | 15 | 7 |
| 6 | 1400 | 63 | 18 | 8 |
| 7 | 2000 | 75 | 21 | 9 |
| 8 | 2800 | 88 | 24 | 11 |
| 9 | 3800 | 102 | 27 | 12 |
| 10 | 5000 | 118 | 31 | 14 |

Ein **Stufenaufstieg** heilt vollständig (inklusive der HP aus Ausrüstung). Reicht die Erfahrung für mehrere Stufen auf einmal, steigst du direkt bis zur höchsten erreichten auf.

## Kampf

- **Angreifen** startet einen Kampf gegen ein Monster im Raum. Jeder Angriff ist eine **Runde**: Du schlägst zu, danach schlägt das Monster **dich** zurück (sofern es noch lebt).
- **Schaden** = Angriff − Verteidigung des Gegners, zufällig um ±`combat.damage_variance` verschoben, aber mindestens `combat.min_damage`.
- Mehrere Spielende können **dasselbe Monster** angreifen; jeder bekommt den Gegenschlag für die eigene Runde.
- Während eines Kampfes kannst du dich nicht bewegen, nichts ausrüsten, nichts aufheben oder fallen lassen und nicht handeln. Einen Gegenstand benutzen (z. B. Heiltrank) geht, **kostet aber die Runde**: Das Monster schlägt danach zu.
- **Fliehen** gelingt mit Wahrscheinlichkeit `combat.flee_chance`. Gelingt es, endet der Kampf ohne Gegenschlag; misslingt es, schlägt das Monster zu.
- **Regeneration:** Monster, die eine Weile nicht im Kampf waren, heilen sich langsam (z. B. der Goblin-Späher 1 HP pro Sekunde nach 10 Sekunden Ruhe). Wer ein Monster angeschlagen stehen lässt, findet es später wieder gesund vor.
- Ein besiegtes Monster **erscheint nach seiner Respawn-Zeit wieder** (Goblin-Späher 60 s, Hungriger Wolf 90 s).

### Überfälle

Manche Monster greifen **von selbst** an: beim Betreten ihres Raums oder während du dort verweilst. Das Monster schlägt zuerst zu, und du bist im Kampf. Nie in sicheren Räumen, nie gegen Tote oder Ausgeloggte, und Monster greifen keine Charaktere an, die ihnen um mehr als einige Stufen überlegen sind. Ein Monster, das bereits kämpft, überfällt niemanden sonst.

### Beute & Belohnung

- Die erste **Partei** (Gruppe oder Einzelspieler), die einem Monster Schaden zufügt, **markiert** es. Nur die markierende Partei bekommt Gold und Gegenstände – so kann niemand Fremdes durch den letzten Schlag Beute stehlen. Fliehen oder sterben alle Mitglieder der markierenden Partei, wird die Markierung aufgehoben.
- **Erfahrung** bekommt jeder, der zugeschlagen hat, anteilig nach seinem Schaden. Gruppenmitglieder im selben Raum, die nicht zugeschlagen haben, bekommen einen kleineren Anteil (`xp.group_idle_share` des durchschnittlichen Schadens als Gewicht). Bei der Aufteilung geht kein Punkt verloren.
- **Gold** wird gleichmäßig unter den Mitgliedern der markierenden Partei im Raum aufgeteilt; übrige Münzen gehen reihum.
- **Gegenstände** gehen allein direkt ins Inventar, in der Gruppe reihum an die Mitglieder im Raum. Hat das nächste Mitglied keinen Platz, bekommt es das folgende; hat niemand Platz, landet der Gegenstand auf dem Boden.

## Tod

Fallen deine HP auf 0, stirbt dein Charakter **sofort mit Verlusten**:

- ein zufälliger Anteil deines Goldes (`death.gold_loss_percent`),
- jeder nicht ausgerüstete Stapel im Inventar mit Wahrscheinlichkeit `death.item_loss_chance`,
- jeder ausgerüstete Gegenstand mit Wahrscheinlichkeit `death.equipped_loss_chance` – **Taschen gehen nie verloren**,
- insgesamt höchstens `death.max_items_lost` Gegenstände.

Verlorenes ist **endgültig weg**, es gibt keine Leiche zum Zurückholen. Das Log zeigt genau, was verloren ging. **Die Bank ist sicher**: eingelagerte Gegenstände sind nie betroffen.

Danach kannst du nur noch **am Checkpoint erwachen** – mit vollen HP.

## Inventar

- `inventory.slots` Plätze. Jeder nicht ausgerüstete Stapel belegt einen Platz, egal wie viele Stück er enthält. Ausgerüstetes belegt keinen Platz, Gold auch nicht.
- Stapelbare Gegenstände füllen zuerst vorhandene Stapel auf (bis zur Stapelgrenze des Gegenstands, z. B. 5 Heiltränke).
- **Taschen** werden ausgerüstet (bis zu `inventory.bag_slots` gleichzeitig) und geben zusätzliche Plätze (Kleine Tasche: +2). Eine Tasche ablegen geht nur, wenn danach noch alles passt.
- Passt beim Aufheben nicht alles, nimmst du so viel, wie passt; der Rest bleibt liegen.
- **Fallengelassenes und Beute auf dem Boden** verschwindet nach `timings.floor_item_ttl_seconds` Sekunden. Gegenstände, die ein Charakter **deines eigenen Accounts** fallen gelassen hat, kannst du nicht aufheben.

## Gruppen

- Lade jemanden per Namen ein; die Einladung gilt `timings.group_invite_ttl_seconds` Sekunden. Mit dem Annehmen entsteht die Gruppe, wer eingeladen hat, leitet sie.
- Höchstens `max_group_size` Mitglieder. Nur die Leitung lädt ein und entfernt Mitglieder (nicht im Kampf).
- Verlässt die Leitung die Gruppe, übernimmt das am längsten dabeiseiende Mitglied. Bleibt nur eine Person übrig, löst sich die Gruppe auf.
- Im Gruppenpanel siehst du HP und Aufenthaltsort der anderen. Beim Ausloggen verlässt du die Gruppe.

## Handel

- **Anfrage** an jemanden im selben Raum; sie läuft nach `trade.request_ttl_seconds` Sekunden ab. Nicht zwischen Charakteren desselben Accounts.
- Nach dem **Annehmen** legen beide Gegenstände und Gold ins Angebot (höchstens `trade.max_offer_entries` Einträge pro Seite). Ausgerüstete und nicht handelbare Gegenstände gehen nicht.
- **Jede Änderung setzt beide Bestätigungen zurück.** Ausgeführt wird erst, wenn beide den **aktuellen** Stand bestätigt haben – niemand kann im letzten Moment das Angebot austauschen.
- Angebotene Gegenstände bleiben bis zum Abschluss bei dir, können aber nicht benutzt, ausgerüstet, verkauft, eingelagert oder fallen gelassen werden.
- Der Handel bricht ab, wenn jemand den Raum verlässt, in einen Kampf gerät, stirbt, sich ausloggt oder die Verbindung verliert.

## Händler & Bank

- **Elira** (Kaufmannsstand) verkauft Heiltränke, Brot und Kleine Taschen und kauft alles Verkaufbare an.
- Kaufen kostet den Listenpreis (sonst Wert × `merchant.buy_ratio`), Verkaufen bringt **abgerundet** Wert × `merchant.sell_ratio`. Ausgerüstete und angebotene Gegenstände kannst du nicht verkaufen; verkaufte Gegenstände verschwinden.
- Die **Bank** (Bank von Oakhaven) hat `bank.slots` Plätze pro Charakter – nicht pro Account. Einlagern und Abheben nur dort und nicht im Kampf. **Gold** kann nicht eingelagert werden.

## Ausloggen & Verbindung

- **Ausloggen außerhalb des Kampfes:** Dein Charakter kehrt auf den Marktplatz zurück und verlässt seine Gruppe; ein laufender Handel wird abgebrochen.
- **Ausloggen im Kampf** musst du bestätigen – **dein Charakter stirbt** (mit den üblichen Verlusten).
- **Verbindung verloren** (Tab geschlossen, Netz weg): Dein Charakter bleibt `timings.disconnect_grace_seconds` Sekunden in der Welt und **kann weiter angegriffen werden**. Kommst du rechtzeitig zurück, geht alles weiter. Sonst wirst du ausgeloggt – **im Kampf mit Tod**. So rettet niemand einen verlorenen Kampf durch Schließen des Tabs.
- Zwischen zwei Aktionen müssen mindestens `timings.min_action_interval_ms` Millisekunden liegen („Nicht so schnell.“).

## Regelwerte

Stand von `data/rules.yaml` (automatisch geprüft):

| Regel | Schlüssel | Wert |
|---|---|---|
| Charaktere pro Account | `max_characters_per_account` | 5 |
| Gruppengröße | `max_group_size` | 4 |
| Inventarplätze | `inventory.slots` | 20 |
| Taschenplätze | `inventory.bag_slots` | 4 |
| Bankplätze | `bank.slots` | 40 |
| Kaufpreis-Faktor | `merchant.buy_ratio` | 1.0 |
| Verkaufserlös-Faktor | `merchant.sell_ratio` | 0.25 |
| Handelsanfrage gültig (s) | `trade.request_ttl_seconds` | 60 |
| Angebotseinträge pro Seite | `trade.max_offer_entries` | 8 |
| Goldverlust beim Tod (%) | `death.gold_loss_percent` | [5, 20] |
| Verlustchance pro Inventarstapel | `death.item_loss_chance` | 0.15 |
| Verlustchance pro ausgerüstetem Item | `death.equipped_loss_chance` | 0.05 |
| Max. verlorene Items | `death.max_items_lost` | 3 |
| Mindestschaden | `combat.min_damage` | 1 |
| Schadensschwankung (±) | `combat.damage_variance` | 1 |
| Fluchtchance | `combat.flee_chance` | 0.5 |
| XP-Anteil untätiger Gruppenmitglieder | `xp.group_idle_share` | 0.5 |
| Bodenobjekte verschwinden nach (s) | `timings.floor_item_ttl_seconds` | 600 |
| Frist bei Verbindungsverlust (s) | `timings.disconnect_grace_seconds` | 60 |
| Mindestabstand zwischen Aktionen (ms) | `timings.min_action_interval_ms` | 800 |
| Gruppeneinladung gültig (s) | `timings.group_invite_ttl_seconds` | 60 |
