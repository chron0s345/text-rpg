# Spielregeln

Wie das Spiel aus Sicht der Spielenden funktioniert. Technische Umsetzung: [`ARCHITECTURE.md`](../ARCHITECTURE.md). Alle Zahlen kommen aus [`data/rules.yaml`](../data/rules.yaml) bzw. den Content-Dateien in [`data/`](../data/); die Tabelle *Regelwerte* am Ende wird von `tests/content/test_game_rules_doc.py` gegen `rules.yaml` geprüft.

## Die Welt

Alle spielen in **einer gemeinsamen, dauerhaften Welt**. Wer im selben Raum steht, sieht sich gegenseitig, dieselben Monster und dieselben Gegenstände am Boden. Gespeichert wird automatisch nach jeder Aktion.

Ein Account kann mehrere Charaktere haben (höchstens `max_characters_per_account`), aber immer nur **einer ist gleichzeitig im Spiel**. Wählst du einen anderen, wird der bisherige ausgeloggt.

Dein Passwort änderst du auf der Charakterauswahl. Dabei werden alle anderen Geräte abgemeldet und eingeloggte Charaktere ausgeloggt; steckt einer im Kampf, beende den Kampf zuerst.

Neue Charaktere starten auf Stufe 1 auf dem **Marktplatz von Oakhaven**.

### Fundstücke

Überall in der Welt liegen **Briefe, Tagebuchseiten und Chroniken** – am Brunnen von Oakhaven, im Goblin-Lager, in der Gruft, im Zwergenposten und an noch dunkleren Orten; manche tragen auch Monster bei sich. Ein Klick auf den Namen liest sie vor. Zusammen erzählen sie, warum die Toten laufen, woher das grüne Licht kommt und was unter der Mine wartet.

### Räume

| Raum | Besonderheit |
|---|---|
| Marktplatz von Oakhaven | Startpunkt, sicher, Checkpoint |
| Bank von Oakhaven (nördlich) | sicher, Bank |
| Goldschmiede (östlich der Bank) | sicher, Goldschmied Aurel (Ringe und Amulette für Einsteiger) |
| Kaufmannsstand (östlich) | sicher, Alchemistin Elira (Tränke, Proviant, Taschen) |
| Schmiede (westlich) | sicher, Schmied Brandt (Waffen und Rüstung) |
| Südtor zum Wald (südlich) | Übergang in die Wildnis |
| Dunkler Wald | Goblin-Späher und Hungriger Wolf |
| Goblin-Lager (östlich des Waldes) | zwei Goblin-Späher und ein Goblin-Krieger, frisches Brot liegt aus |
| Feldweg (östlich des Kaufmannsstands) | drei Feldratten |
| Weizenfelder (nördlich des Feldwegs) | zwei Riesenkrähen, wilde Äpfel am Baum |
| Dornendickicht (östlich der Felder) | zwei Wildschweine – greifen nur an, wenn man sie stört |
| Mühle am Bach (östlich des Feldwegs) | sicher, Checkpoint, Müllerin Grete (Proviant, Mistgabel) |
| Alter Friedhof (südlich des Feldwegs) | drei Wandelnde Leichname |
| Gruft der Familie Valen (unter dem Friedhof) | zwei Gruftritter |
| Grabkammer des Grabfürsten (nördlich der Gruft) | Grabfürst Valen – nur für Gruppen |
| Alte Grenzsteine (östlich des Dornendickichts) | keine Monster – aber eine Warnung: dahinter beginnt Stufe 8 |
| Silberhain | zwei Irrlichter (greifen nicht von selbst an), Silberblätter |
| Mondlichtung (nördlich des Hains) | zwei Verdorbene Dryaden |
| Verfallene Elfenhalle (östlich des Hains) | sicher, Checkpoint, Waldläuferin Nimriel (Elfenausrüstung, Elfenbrot) |
| Herz der Dornen (östlich der Halle) | zwei Dornenbestien |
| Der Uralte Baum (nördlich der Dornen) | Der Verderbte Hüter – nur für Gruppen |
| Klippenpfad (westlich des Waldes) | zwei Felsenkrabben – greifen nur an, wenn man sie stört; Miesmuscheln am Fels |
| Fischerdorf Möwenstein (nördlich des Klippenpfads) | sicher, Checkpoint, Fischerin Maren (Räucherfisch, Harpune, Ölzeug) |
| Gezeitenhöhlen (unter dem Klippenpfad) | zwei Ertrunkene Seeleute |
| Schmugglerversteck (westlich der Höhlen) | zwei Schmuggler |
| Treppe zum Leuchtturm (westlich des Klippenpfads) | zwei Sturmgeister |
| Laterne des Leuchtturms (oben) | Seehexe Morwenna – nur für Gruppen |
| Gesunkene Treppe (unter den Gezeitenhöhlen) | keine Monster – aber eine Warnung: darunter beginnt Stufe 11 |
| Versunkener Platz | zwei Tiefseeräuber (greifen nicht von selbst an), Seetang |
| Luftkammer des Tauchers (östlich des Platzes) | sicher, Checkpoint, Taucher Brack (Taucherhelm, Perlenkette, Salben) |
| Halle der Ertrunkenen Ritter (westlich des Platzes) | zwei Ertrunkene Ritter |
| Korallenpalast (unter der Halle) | zwei Korallenwächter |
| Schlund des Kraken (unter dem Palast) | Der Kraken Ul'thar – nur für Gruppen |
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
| Basaltstiege (nördlich des Zwergenpostens) | zwei Feuersalamander |
| Rastplatz am Kraterrand (nördlich der Stiege) | sicher, Checkpoint, Kundschafterin Ilva (Obsidian, Phönixtränke) |
| Aschefelder (westlich des Rastplatzes) | zwei Aschegeister, Feuerblüten wachsen in der Asche |
| Lavastrom (östlich des Rastplatzes) | zwei Magmagolems |
| Schmiede der Feuerriesen (nördlich des Lavastroms) | zwei Feuerriesen |
| Herz des Vulkans (über der Schmiede) | Glutfürst Ignaros – nur für Gruppen |
| Schattenriss (unter dem Spinnennest) | zwei Schattenpirscher |
| Brücke über das Nichts (nördlich des Risses) | zwei Leerenritter |
| Letzte Zuflucht (nördlich der Brücke) | sicher, Checkpoint, Bank, der Händler ohne Gesicht (Sternenstahl, Tonika) |
| Verdorrter Garten (westlich der Zuflucht) | zwei Albträume, Mondlilien leuchten zwischen den Bäumen |
| Halle der Stille (nördlich der Zuflucht) | zwei Schattenwächter |
| Thron der Schatten (nördlich der Halle) | Schattenkönig Morvath – der letzte Gegner, nur für Gruppen |

Die Welt wird nach außen gefährlicher: Rund um Oakhaven, im Wald und in den **Kornfeldern** östlich der Stadt kommt man mit Stufe 1–3 zurecht; Moor, Ruine, Mine und die Gruft unter dem Alten Friedhof sind für Stufe 3–5. Die **Sturmküste** westlich des Waldes schlägt die Brücke zu Stufe 6–7, mit der Seehexe Morwenna im Leuchtturm. Hinter den Kornfeldern liegt, durch alte Grenzsteine gewarnt, der **Silberhain** für Stufe 8–10: ein Elfenwald, den der Schatten verdorben hat. Das **Grauzahn-Gebirge** östlich des Moors und **die Tiefen** unter der Mine sind für Stufe 6–10; die beiden Bosse dort (Spinnenkönigin, Eisdrache) schafft man nur in der Gruppe oder mit vielen Tränken. Im Zwergenposten gibt es eine zweite Bank. Nördlich des Zwergenpostens liegt der **Feuerschlund** für Stufe 11–13, mit dem Glutfürsten Ignaros im Herzen des Vulkans. Wer lieber taucht als schwitzt, findet unter den Gezeitenhöhlen die **Versunkene Stadt Aelmar**, ebenfalls für Stufe 11–13, mit dem Kraken Ul'thar in ihrer Tiefe. Ganz unten, durch einen Riss unter dem Nest der Spinnenkönigin, liegt das **Schattenreich** für Stufe 14–15. Dort wartet der Schattenkönig Morvath, der stärkste Gegner des Spiels.

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
| 11 | 6500 | 135 | 35 | 16 |
| 12 | 8200 | 153 | 39 | 18 |
| 13 | 10200 | 172 | 43 | 20 |
| 14 | 12500 | 192 | 47 | 22 |
| 15 | 15000 | 215 | 52 | 25 |

Ein **Stufenaufstieg** heilt vollständig (inklusive der HP aus Ausrüstung). Reicht die Erfahrung für mehrere Stufen auf einmal, steigst du direkt bis zur höchsten erreichten auf.

## Kampf

- **Angreifen** startet einen Kampf gegen ein Monster im Raum. Jeder Angriff ist eine **Runde**: Du schlägst zu, danach schlägt das Monster **dich** zurück (sofern es noch lebt).
- **Schaden** = Angriff − Verteidigung des Gegners, zufällig um ±`combat.damage_variance` verschoben, aber mindestens `combat.min_damage`.
- Mehrere Spielende können **dasselbe Monster** angreifen; jeder bekommt den Gegenschlag für die eigene Runde.
- Während eines Kampfes kannst du dich nicht bewegen, nichts ausrüsten, nichts aufheben oder fallen lassen und nicht handeln. Einen Gegenstand benutzen (z. B. Heiltrank) geht, **kostet aber die Runde**: Das Monster schlägt danach zu.
- **Fliehen** gelingt mit Wahrscheinlichkeit `combat.flee_chance`. Gelingt es, endet der Kampf ohne Gegenschlag; misslingt es, schlägt das Monster zu.
- **Regeneration:** Monster, die eine Weile nicht im Kampf waren, heilen sich langsam (z. B. der Goblin-Späher 1 HP pro Sekunde nach 10 Sekunden Ruhe). Wer ein Monster angeschlagen stehen lässt, findet es später wieder gesund vor.
- Ein besiegtes Monster **erscheint nach seiner Respawn-Zeit wieder** (Goblin-Späher 60 s, Hungriger Wolf 90 s).

### Seltene Monster

Manche Monster haben eine **seltene Form**, etwa einen besonders starken oder reichen Verwandten. Jedes Mal, wenn ein solches Monster wieder auftaucht, besteht eine kleine Chance, dass stattdessen die seltene Form erscheint; das Log meldet dann „… taucht auf – ein seltener Anblick!“. Seltene Formen sind stärker und lassen bessere Beute fallen. Nach ihrem Tod kommt meist wieder die gewöhnliche Form.

| Gewöhnlich | Selten (ca. 4–5 %) | Besondere Beute |
|---|---|---|
| Feldratte | Rattenkönig | Rattenkrone |
| Goblin-Späher | Goldener Goblin | Goldzahn, viel Gold, Kupferring |
| Hungriger Wolf | Weißer Wolf | Weißes Wolfsfell, Knochenamulett |
| Moorschleicher | Uralter Moorschleicher | Moorkraut, Großer Heiltrank, Lederstiefel |
| Skelettwächter | Skelettchampion | Langschwert |
| Schmuggler | Schmugglerkönig Rask | Schmuggelware, Silberring, Harpune |
| Grubenghul | Ghulfürst | Eiserne Beinschienen |
| Frostwolf | Rudelführer Silberfang | Frostwolfpelze, Eisenstiefel, Silberring |
| Magmagolem | Obsidiangolem | Glutkerne, Obsidianschild |
| Albtraum | Nachtmahr | Sternenlichttonikum, Mondsteinamulett |

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
- **Ausrüstung** trägst du an acht Plätzen gleichzeitig: Waffe, Schild, Kopf, Brust, Beine, Füße, Amulett und Ring, je ein Stück. Ein neues Stück ersetzt das alte am selben Platz, das alte wandert in den Rucksack. Was ein Gegenstand bringt (z. B. „Waffe · +3 Angriff“, „heilt 15“), steht im Inventar, in der Bank und beim Händler unter seinem Namen. Ein Klick auf den Namen (auch am Boden und im Laden) schreibt die ganze Beschreibung ins Log.
- **Schilde, Amulette und Ringe** gibt es bei jedem Ausrüstungshändler entlang des Weges (Brandt, Brunhild, Ilva, der Händler ohne Gesicht) und beim Goldschmied in Oakhaven. Die besten Stücke lassen nur die Bosse fallen.
- **Taschen** werden ausgerüstet (bis zu `inventory.bag_slots` gleichzeitig) und geben zusätzliche Plätze (Kleine Tasche: +2). Eine Tasche ablegen geht nur, wenn danach noch alles passt.
- Passt beim Aufheben nicht alles, nimmst du so viel, wie passt; der Rest bleibt liegen.
- **Fallengelassenes und Beute auf dem Boden** verschwindet nach `timings.floor_item_ttl_seconds` Sekunden. Gegenstände, die ein Charakter **deines eigenen Accounts** fallen gelassen hat, kannst du nicht aufheben.

## Gruppen

- Lade jemanden per Namen ein; die Einladung gilt `timings.group_invite_ttl_seconds` Sekunden. Mit dem Annehmen entsteht die Gruppe, wer eingeladen hat, leitet sie.
- Höchstens `max_group_size` Mitglieder. Nur die Leitung lädt ein und entfernt Mitglieder (nicht im Kampf).
- Verlässt die Leitung die Gruppe, übernimmt das am längsten dabeiseiende Mitglied. Bleibt nur eine Person übrig, löst sich die Gruppe auf.
- Im Gruppenpanel siehst du HP und Aufenthaltsort der anderen. Beim Ausloggen verlässt du die Gruppe.

## Chat

Im Chatfeld unter dem Log:

- **Text** sagst du im Raum, alle Anwesenden hören es.
- **`/g Text`** geht an deine Gruppe, egal wo die anderen gerade sind.
- **`/w Name Text`** flüstert einem Charakter, der gerade im Spiel ist, egal wo. Nur ihr beide seht es.

Gruppenchat und Flüstern gehen auch, wenn du tot bist. Chatten zählt wie jede Aktion gegen den Mindestabstand zwischen Aktionen.

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
