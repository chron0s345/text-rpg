# Spielregeln

Wie das Spiel aus Sicht der Spielenden funktioniert. Technische Umsetzung: [`ARCHITECTURE.md`](../ARCHITECTURE.md). Alle Zahlen kommen aus [`data/rules.yaml`](../data/rules.yaml) bzw. den Content-Dateien in [`data/`](../data/); die Tabelle *Regelwerte* am Ende wird von `tests/content/test_game_rules_doc.py` gegen `rules.yaml` geprüft.

## Die Welt

Alle spielen in **einer gemeinsamen, dauerhaften Welt**. Wer im selben Raum steht, sieht sich gegenseitig, dieselben Monster und dieselben Gegenstände am Boden. Gespeichert wird automatisch nach jeder Aktion.

Die **Minikarte** im Raumpanel zeigt die Umgebung auf einem Raster von 5 × 5 Feldern um dich herum: wo du stehst, sichere Räume, Händler und – mit Stern – wohin dich deine Aufträge gerade führen (das Ziel eines „Gehe zu“, die Räume mit den gesuchten Monstern oder den Auftraggeber, wenn du abgeben kannst). Kleine Dreiecke zeigen Wege nach oben oder unten.

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
| Gasthaus »Zum Goldenen Krug« (nördlich des Kaufmannsstands) | sicher, Wirtin Hanne (Eintopf, Met), Gerüchte am Tresen |
| Tempel des Lichts (südlich des Kaufmannsstands) | sicher, Checkpoint, Schwester Liora (Geweihtes Wasser, Lichtamulett) |
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
| Hügelgräberfeld (östlich des Anstiegs) | zwei Grabwichte (selten: Uralter Grabwicht) |
| Tor des Großen Hügels (östlich des Gräberfelds) | zwei Knochenhunde, überfallen Eindringlinge |
| Ahnengänge (unter dem Tor) | ein Grabhüter und ein Grabwicht |
| Grab des Wolfskönigs (nördlich der Ahnengänge) | Boss: Wolfskönig Gerulf |
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

### Reisen

Von einigen sicheren Orten fahren Kutschen und Boote gegen Gold zu anderen sicheren Orten. Die Ankunft zählt wie Hingehen (Checkpoint, Besuchsziele), nur ohne Gefahr unterwegs.

| Von | Nach | Mit | Gold | ab Stufe |
|---|---|---|---|---|
| Marktplatz von Oakhaven | Fischerdorf Möwenstein | Postkutsche | 10 | 3 |
| Marktplatz von Oakhaven | Zwergenposten Khazdur | Postkutsche | 25 | 6 |
| Marktplatz von Oakhaven | Verfallene Elfenhalle | Postkutsche | 40 | 8 |
| Marktplatz von Oakhaven | Rastplatz am Kraterrand | Postkutsche | 60 | 11 |
| Fischerdorf Möwenstein | Luftkammer des Tauchers | Boot des Tauchers | 30 | 11 |
| Letzte Zuflucht | Marktplatz von Oakhaven | Schattenpfad | 80 | 1 |

Jede Kutsche und das Boot fahren zum gleichen Preis auch zurück. Zur Letzten Zuflucht führt kein Weg außer dem eigenen.

## Werte & Stufen

Jeder Charakter hat **Lebenspunkte (HP)**, **Angriff** und **Verteidigung**. Die Grundwerte hängen von der Stufe ab, ausgerüstete Gegenstände kommen dazu.

| Stufe | Erfahrung (XP) ab | Max. HP | Angriff | Verteidigung |
|---|---|---|---|---|
| 1 | 0 | 20 | 5 | 2 |
| 2 | 300 | 26 | 7 | 3 |
| 3 | 750 | 33 | 9 | 4 |
| 4 | 1500 | 42 | 12 | 5 |
| 5 | 2700 | 52 | 15 | 7 |
| 6 | 4200 | 63 | 18 | 8 |
| 7 | 6000 | 75 | 21 | 9 |
| 8 | 8400 | 88 | 24 | 11 |
| 9 | 11400 | 102 | 27 | 12 |
| 10 | 15000 | 118 | 31 | 14 |
| 11 | 19500 | 135 | 35 | 16 |
| 12 | 24600 | 153 | 39 | 18 |
| 13 | 30600 | 172 | 43 | 20 |
| 14 | 37500 | 192 | 47 | 22 |
| 15 | 45000 | 215 | 52 | 25 |

Ein **Stufenaufstieg** heilt vollständig (inklusive der HP aus Ausrüstung). Reicht die Erfahrung für mehrere Stufen auf einmal, steigst du direkt bis zur höchsten erreichten auf.

**Heilen:** Deine HP kommen nicht von selbst zurück. An einem **sicheren Ort** (Oakhaven, Lager, Zuflucht – auf der Karte grün umrandet) kannst du **rasten** und bist sofort wieder bei vollen Kräften, kostenlos. Unterwegs helfen nur Essen und Tränke. Stufenaufstieg und Erwachen nach dem Tod heilen ebenfalls vollständig.

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
- Das Panel **„Ausrüstung“** zeigt alle acht Plätze und deine Taschen, auch leere Plätze, dazu deine Gesamtwerte und Set-Boni. Liegt im Rucksack etwas Besseres für einen Platz, ist er grün umrandet.
- **Vergleich:** Bei jedem ausrüstbaren Gegenstand – im Rucksack, beim Händler, am Boden und in der Bank – steht, was sich ändern würde, wenn du ihn statt des getragenen Stücks anlegst: **▲ besser**, **▼ schlechter**, **◆ gemischt** (manches besser, manches schlechter) oder **= gleichwertig**, mit den genauen Werten. Set-Boni, die du dabei gewinnst oder verlierst, sind eingerechnet.
- **Bestes anlegen:** Ein Knopf im Ausrüstungsfeld (nur sichtbar, wenn es etwas zu verbessern gibt) legt alles aus dem Rucksack an, was **▲ besser** ist – also mindestens einen Wert erhöht und keinen senkt, Set-Boni eingerechnet. Das größte Plus zuerst, so lange, bis nichts mehr besser wird. Gemischte Stücke, Taschen und im Handel angebotene Gegenstände bleiben, wo sie sind.
- **Schilde, Amulette und Ringe** gibt es bei jedem Ausrüstungshändler entlang des Weges (Brandt, Brunhild, Ilva, der Händler ohne Gesicht) und beim Goldschmied in Oakhaven. Die besten Stücke lassen nur die Bosse fallen.
- **Taschen** werden ausgerüstet (bis zu `inventory.bag_slots` gleichzeitig) und geben zusätzliche Plätze (Kleine Tasche: +2). Eine Tasche ablegen geht nur, wenn danach noch alles passt.
- Passt beim Aufheben nicht alles, nimmst du so viel, wie passt; der Rest bleibt liegen.
- **Fallengelassenes und Beute auf dem Boden** verschwindet nach `timings.floor_item_ttl_seconds` Sekunden. Gegenstände, die ein Charakter **deines eigenen Accounts** fallen gelassen hat, kannst du nicht aufheben.

## Gruppen

- Lade jemanden per Namen ein; die Einladung gilt `timings.group_invite_ttl_seconds` Sekunden. Mit dem Annehmen entsteht die Gruppe, wer eingeladen hat, leitet sie.
- Höchstens `max_group_size` Mitglieder. Nur die Leitung lädt ein und entfernt Mitglieder (nicht im Kampf).
- Verlässt die Leitung die Gruppe, übernimmt das am längsten dabeiseiende Mitglied. Bleibt nur eine Person übrig, löst sich die Gruppe auf.
- Im Gruppenpanel siehst du HP und Aufenthaltsort der anderen. Beim Ausloggen verlässt du die Gruppe.
- **Wer mitten im Kampf geht, geht leer aus:** Verlässt du die Gruppe, während sie gegen ein Monster kämpft, bekommst du von diesem Monster kein Gold, keine Beute und keinen Auftragsfortschritt mehr – nur die Erfahrung für den Schaden, den du selbst angerichtet hast. Löst sich die Gruppe dagegen auf, weil nur noch eine Person übrig ist, zählen alle, die gegen das Monster gekämpft haben, weiter als Gruppe.

## Chat

Im Chatfeld unter dem Log:

- **Text** sagst du im Raum, alle Anwesenden hören es.
- **`/g Text`** geht an deine Gruppe, egal wo die anderen gerade sind.
- **`/w Name Text`** flüstert einem Charakter, der gerade im Spiel ist, egal wo. Nur ihr beide seht es.

Gruppenchat und Flüstern gehen auch, wenn du tot bist. Chatten zählt wie jede Aktion gegen den Mindestabstand zwischen Aktionen.

**Im Spiel:** Das Panel „Im Spiel“ zeigt, wie viele Charaktere gerade online sind, und aufgeklappt die höchsten 30 mit Stufe, Titel, Ort und Zustand – praktisch, um jemandem zu flüstern oder eine Gruppe zu finden.

## Handel

- **Anfrage** an jemanden im selben Raum; sie läuft nach `trade.request_ttl_seconds` Sekunden ab. Nicht zwischen Charakteren desselben Accounts.
- Nach dem **Annehmen** legen beide Gegenstände und Gold ins Angebot (höchstens `trade.max_offer_entries` Einträge pro Seite). Ausgerüstete und nicht handelbare Gegenstände gehen nicht.
- **Jede Änderung setzt beide Bestätigungen zurück.** Ausgeführt wird erst, wenn beide den **aktuellen** Stand bestätigt haben – niemand kann im letzten Moment das Angebot austauschen.
- Angebotene Gegenstände bleiben bis zum Abschluss bei dir, können aber nicht benutzt, ausgerüstet, verkauft, eingelagert oder fallen gelassen werden.
- Der Handel bricht ab, wenn jemand den Raum verlässt, in einen Kampf gerät, stirbt, sich ausloggt oder die Verbindung verliert.

## Aufträge

Viele Händler haben **Aufträge**. Steht ein Auftraggeber im Raum, zeigt das Panel „Aufträge“ seine Angebote mit Aufgabe und Belohnung; annehmen und abgeben geht nur bei ihm und nicht im Kampf.

- **Bringen:** Immer der letzte Schritt. Beim Abgeben werden die Gegenstände aus dem Rucksack genommen (nicht ausgerüstete und nicht im Handel angebotene).
- **Schritte:** Manche Aufträge haben mehrere Schritte, die der Reihe nach erledigt werden (z. B. „Gehe zu: Alter Friedhof → Besiege 3× Wandelnder Leichnam → Gehe zu: Gruft der Familie Valen“). Das Panel zeigt den aktuellen Schritt („Schritt 2/3“); ist einer erledigt, sagt dir das Log, was als Nächstes kommt.
- **Gehe zu:** Der Schritt ist erledigt, sobald du den Raum betrittst – oder sofort, wenn du schon dort stehst.
- **Besiegen:** Jeder Sieg zählt für alle Mitglieder deiner Gruppe im Raum, die den Auftrag haben – wie Gold und Beute. Seltene Formen zählen für ihre gewöhnliche Form.
- Belohnungen sind Erfahrung, Gold und Gegenstände; ist im Rucksack kein Platz für die Gegenstände, kannst du noch nicht abgeben.
- Manche Aufträge setzen eine Stufe oder einen anderen Auftrag voraus, manche lassen sich wiederholen. Höchstens 10 Aufträge gleichzeitig; abbrechen geht jederzeit, der Fortschritt ist dann verloren.
- **Einstieg:** Hauptmann Rowan auf dem Marktplatz führt Neulinge mit vier kurzen Aufträgen durch das Wichtigste: Wege und Händler, der erste Kampf, Rasten, Beute verkaufen, Ausrüstung. Wer auf Stufe 1 einloggt und den ersten noch nicht angenommen hat, bekommt einen Hinweis im Log. Der Hauptmann handelt nicht.
- **Questketten mit Geschichte:** *Der verschwundene Jäger* (Wirtin Hanne und Fischerin Maren, ab Stufe 4), *Die Halle von Khazdur* (Waffenmeisterin Brunhild, ab Stufe 6) und *Die Laternen der Tiefe* (von Kundschafterin Ilva über Taucher Brack und den Händler ohne Gesicht bis zu Schwester Liora, ab Stufe 11).
- **Kopfgelder:** In jeder Region gibt es einen wiederholbaren Jagdauftrag („Kopfgeld: …“) – Hauptmann Rowan für die Gegend um Oakhaven, die Händler der Regionen für ihre Plagen. Die Belohnung ist etwa die Hälfte der Erfahrung, die die Kills selbst bringen, plus etwas Gold. Kills zählen erst ab dem Annehmen.

| Auftrag | Auftraggeber | ab Stufe | Aufgabe |
|---|---|---|---|
| Willkommen in Oakhaven | Hauptmann Rowan | 1 | Gehe zu: Kaufmannsstand → Gehe zu: Schmiede |
| Ratten am Feldweg | Hauptmann Rowan | 1 | Besiege 2× Feldratte (nach „Willkommen in Oakhaven“) |
| Beute zu Gold | Hauptmann Rowan | 1 | Bringe 3× Rattenschwanz (nach „Ratten am Feldweg“) |
| Hinaus in die Welt | Hauptmann Rowan | 1 | Gehe zu: Südtor zum Wald → Besiege 2× Goblin-Späher (nach „Beute zu Gold“) |
| Rattenplage | Müllerin Grete | 1 | 5× Rattenschwanz bringen |
| Wildbret für den Krug | Wirtin Hanne | 1 | 3× Wildschweinkeule bringen |
| Ohren für die Wissenschaft | Alchemistin Elira | 1 | 6× Goblinohr bringen |
| Wölfe vor den Toren | Schmied Brandt | 2 | 4× Hungriger Wolf besiegen |
| Ruhe für die Toten | Schwester Liora | 3 | 5× Wandelnder Leichnam besiegen |
| Der Ring der Valen | Schwester Liora | 4 | 1× Siegelring der Valen bringen (nach „Ruhe für die Toten“) |
| Ein Zahn aus Gold | Goldschmied Aurel | 1 | 1× Goldzahn bringen |
| Scheren und Schalen | Fischerin Maren | 4 | 5× Krabbenpanzer bringen |
| Das Lied verstummen lassen | Fischerin Maren | 6 | 1× Seehexe Morwenna besiegen |
| Pelze für den Winter | Waffenmeisterin Brunhild | 7 | 4× Frostwolfpelz bringen |
| Eis für die Esse | Waffenmeisterin Brunhild | 8 | 3× Ewiges Eis bringen (wiederholbar) |
| Seide aus der Tiefe | Alchemistin Elira | 7 | 6× Spinnenseide bringen |
| Den Hain heilen | Waldläuferin Nimriel | 9 | 4× Verdorbene Dryade besiegen |
| Das Herz der Erde | Waldläuferin Nimriel | 10 | 1× Herzholz des Hüters bringen (nach „Den Hain heilen“) |
| Glutkerne | Kundschafterin Ilva | 12 | 3× Glutkern bringen |
| Die Wappen von Aelmar | Taucher Brack | 12 | 4× Wappen von Aelmar bringen |
| Splitter der Leere | Der Händler ohne Gesicht | 14 | 5× Leerensplitter bringen (wiederholbar) |
| Der letzte König | Schwester Liora | 15 | 1× Schattenkönig Morvath besiegen (nach „Der Ring der Valen“) |
| Vogelscheuche ohne Wirkung | Müllerin Grete | 1 | 4× Krähenfeder bringen |
| Kräuter aus dem Moor | Alchemistin Elira | 3 | 6× Moorkraut bringen |
| Asche zu Asche | Schwester Liora | 3 | 5× Knochenstaub bringen (nach „Ruhe für die Toten“) |
| Brückenzoll | Schmied Brandt | 5 | 1× Höhlentroll besiegen (nach „Wölfe vor den Toren“) |
| Fremde Boote | Fischerin Maren | 6 | 4× Schmuggler besiegen |
| Federn für Pfeile | Waffenmeisterin Brunhild | 6 | 4× Harpyienfeder bringen |
| Die Mine zurückerobern | Waffenmeisterin Brunhild | 6 | 5× Grubenghul besiegen |
| Der Sturm in der Flasche | Fischerin Maren | 7 | 3× Sturmessenz bringen |
| Lichter im Nebel | Waldläuferin Nimriel | 8 | 4× Gefangenes Irrlicht bringen |
| Die Königin der Netze | Alchemistin Elira | 9 | 1× Spinnenkönigin Ysvara besiegen (nach „Seide aus der Tiefe“) |
| Dornen im Hain | Waldläuferin Nimriel | 10 | 3× Dornenbestie besiegen (nach „Den Hain heilen“) |
| Hrimfangs Ende | Waffenmeisterin Brunhild | 10 | 1× Eisdrache Hrimfang besiegen (nach „Pelze für den Winter“) |
| Schuppen aus der Glut | Kundschafterin Ilva | 11 | 4× Salamanderschuppe bringen |
| Asche im Wind | Kundschafterin Ilva | 11 | 5× Aschegeist besiegen |
| Räuber der Tiefe | Taucher Brack | 11 | 5× Tiefseeräuber besiegen |
| Zähne eines Riesen | Goldschmied Aurel | 13 | 3× Riesenzahn bringen |
| Der Glutfürst | Kundschafterin Ilva | 13 | 1× Glutfürst Ignaros besiegen (nach „Glutkerne“) |
| Das Auge der Tiefe | Taucher Brack | 13 | 1× Der Kraken Ul'thar besiegen (nach „Die Wappen von Aelmar“) |
| Pelze aus Schatten | Der Händler ohne Gesicht | 14 | 4× Schattenfell bringen |
| Schlaflose Nächte | Wirtin Hanne | 15 | 3× Albtraum besiegen |
| Der verschwundene Jäger | Wirtin Hanne | 4 | 1× Rostiger Kompass bringen (nach „Wildbret für den Krug“) |
| Unter den Ertrunkenen | Fischerin Maren | 5 | 4× Ertrunkener Seemann besiegen (nach „Der verschwundene Jäger“) |
| Ware ohne Zoll | Fischerin Maren | 6 | 4× Schmuggelware bringen (nach „Unter den Ertrunkenen“) |
| Heimkehr | Wirtin Hanne | 7 | 6× Schmuggler besiegen (nach „Ware ohne Zoll“) |
| Wie tief sie graben | Waffenmeisterin Brunhild | 6 | 6× Ghulkralle bringen (nach „Die Mine zurückerobern“) |
| Gefallene Wächter | Waffenmeisterin Brunhild | 8 | 3× Eisgolem besiegen (nach „Wie tief sie graben“) |
| Netze vor dem Tor | Waffenmeisterin Brunhild | 8 | 6× Höhlenspinne besiegen (nach „Gefallene Wächter“) |
| Die Halle von Khazdur | Waffenmeisterin Brunhild | 9 | 1× Giftzahn der Königin bringen (nach „Netze vor dem Tor“) |
| Die Asche der Kundschafter | Kundschafterin Ilva | 11 | 5× Geisterasche bringen (nach „Asche im Wind“) |
| Laternen unter Wasser | Taucher Brack | 12 | 4× Ertrunkener Ritter besiegen (nach „Die Asche der Kundschafter“) |
| Was die Laternen fürchten | Der Händler ohne Gesicht | 14 | 5× Schattenpirscher besiegen (nach „Laternen unter Wasser“) |
| Das letzte Siegel | Schwester Liora | 15 | 3× Siegel der Wächter bringen (nach „Was die Laternen fürchten“) |
| Ein Brief für Grete | Wirtin Hanne | 1 | Gehe zu: Mühle am Bach → Besiege 3× Feldratte |
| Wache am Südtor | Schmied Brandt | 2 | Gehe zu: Südtor zum Wald → Besiege 3× Goblin-Späher → Bringe 3× Goblinohr |
| Nachtwache | Schwester Liora | 3 | Gehe zu: Alter Friedhof → Besiege 3× Wandelnder Leichnam → Gehe zu: Gruft der Familie Valen |
| Das Feuer im Moor | Alchemistin Elira | 3 | Gehe zu: Lagerfeuer am Moor → Besiege 3× Moorschleicher → Bringe 4× Moorkraut |
| Hinauf zum Licht | Fischerin Maren | 7 | Gehe zu: Treppe zum Leuchtturm → Besiege 3× Sturmgeist → Gehe zu: Laterne des Leuchtturms (nach „Das Lied verstummen lassen“) |
| Grenzgang | Waldläuferin Nimriel | 8 | Gehe zu: Alte Grenzsteine → Gehe zu: Silberhain → Besiege 3× Irrlicht → Bringe 5× Silberblatt |
| Vermessung des Kraters | Kundschafterin Ilva | 12 | Gehe zu: Basaltstiege → Gehe zu: Lavastrom → Gehe zu: Schmiede der Feuerriesen → Besiege 2× Feuerriese |
| Karte von Aelmar | Taucher Brack | 12 | Gehe zu: Versunkener Platz → Gehe zu: Halle der Ertrunkenen Ritter → Gehe zu: Korallenpalast → Besiege 2× Korallenwächter |
| Durch die Dunkelheit | Der Händler ohne Gesicht | 14 | Gehe zu: Schattenriss → Gehe zu: Brücke über das Nichts → Gehe zu: Halle der Stille → Besiege 2× Schattenwächter |
| Unruhe in den Hügeln | Waffenmeisterin Brunhild | 8 | 6× Grabwicht besiegen |
| Staub der Toten | Schwester Liora | 8 | 5× Grabstaub bringen (nach „Unruhe in den Hügeln“) |
| Der Wolfskönig | Waffenmeisterin Brunhild | 9 | Grab des Wolfskönigs aufsuchen, dann Wolfskönig Gerulf besiegen (nach „Unruhe in den Hügeln“) |
| Kopfgeld: Feldratten | Hauptmann Rowan | 1 | 10× Feldratte besiegen (wiederholbar) |
| Kopfgeld: Hungrige Wölfe | Hauptmann Rowan | 2 | 8× Hungriger Wolf besiegen (wiederholbar) |
| Kopfgeld: Skelettwächter | Hauptmann Rowan | 4 | 8× Skelettwächter besiegen (wiederholbar) |
| Kopfgeld: Ertrunkene Seeleute | Fischerin Maren | 5 | 8× Ertrunkener Seemann besiegen (wiederholbar) |
| Kopfgeld: Eisgolems | Waffenmeisterin Brunhild | 8 | 6× Eisgolem besiegen (wiederholbar) |
| Kopfgeld: Verdorbene Dryaden | Waldläuferin Nimriel | 9 | 6× Verdorbene Dryade besiegen (wiederholbar) |
| Kopfgeld: Feuersalamander | Kundschafterin Ilva | 11 | 8× Feuersalamander besiegen (wiederholbar) |
| Kopfgeld: Ertrunkene Ritter | Taucher Brack | 12 | 6× Ertrunkener Ritter besiegen (wiederholbar) |
| Kopfgeld: Schattenpirscher | Der Händler ohne Gesicht | 14 | 8× Schattenpirscher besiegen (wiederholbar) |

## Handwerk

Manche Händler sind auch **Handwerker**. Steht einer im Raum, zeigt das Panel „Handwerk“ seine Rezepte: was entsteht, welche Zutaten nötig sind (in Klammern, wie viele du hast) und was es kostet. Herstellen geht nur bei ihm und nicht im Kampf.

- Zutaten werden aus dem Rucksack genommen (nicht ausgerüstete und nicht im Handel angebotene), dazu die Gebühr in Gold.
- Das Ergebnis muss ins Inventar passen, sonst wird nichts genommen. Ein Stapel, den die Zutaten aufbrauchen, macht dafür Platz.
- Manche Stücke gibt es nur beim Handwerker, etwa den Frostpelzmantel oder die Leerenschneide.
- **Trophäen:** Was Bosse und ihre Gefolgsleute als Andenken hinterlassen – Rattenkrone, Enterhaken, Krakenauge, die Kronen des Glutfürsten und des Schattenkönigs – schmieden Handwerker zu Ausrüstung, die es sonst nirgends gibt.

| Ergebnis | Handwerker | ab Stufe | Zutaten | Gold |
|---|---|---|---|---|
| 2× Heiltrank | Alchemistin Elira | 1 | 3× Moorkraut | 2 |
| Großer Heiltrank | Alchemistin Elira | 3 | Heiltrank, 3× Moorkraut | 5 |
| Elixier des Lebens | Alchemistin Elira | 7 | 2× Großer Heiltrank, Spinnenseide | 15 |
| Wildlederweste | Schmied Brandt | 2 | 4× Wildschweinfell | 20 |
| Knochenschild | Schmied Brandt | 4 | Holzschild, 4× Knochenstaub | 25 |
| Hauerkeule | Schmied Brandt | 5 | 2× Trollhauer | 30 |
| Silberring | Goldschmied Aurel | 1 | 2× Goldzahn | 20 |
| Krallenamulett | Goldschmied Aurel | 6 | Knochenamulett, 4× Ghulkralle | 30 |
| Perlenring | Goldschmied Aurel | 7 | Silberring, Schwarze Perle | 60 |
| 2× Eintopf vom Goldenen Krug | Wirtin Hanne | 1 | 2× Wildschweinkeule, Frisches Brot | 0 |
| 2× Geweihtes Wasser | Schwester Liora | 3 | 4× Knochenstaub | 5 |
| 2× Räucherfisch | Fischerin Maren | 1 | 3× Krabbenfleisch | 0 |
| Sturmamulett | Fischerin Maren | 7 | Korallenamulett, 3× Sturmessenz | 30 |
| Eisenbeschlagene Stiefel | Waffenmeisterin Brunhild | 6 | Lederstiefel, 2× Ewiges Eis | 15 |
| Frostpelzmantel | Waffenmeisterin Brunhild | 7 | 3× Frostwolfpelz, Weißes Wolfsfell | 40 |
| Drachenschuppenhelm | Waffenmeisterin Brunhild | 10 | Eisenhelm, 3× Drachenschuppe | 80 |
| Elfenbrot | Waldläuferin Nimriel | 8 | 4× Silberblatt | 5 |
| Rindenschild | Waldläuferin Nimriel | 9 | Holzschild, 3× Dryadenrinde | 30 |
| Dornenring | Waldläuferin Nimriel | 10 | Blattring, 3× Dornenstachel | 50 |
| Phönixtrank | Kundschafterin Ilva | 11 | Elixier des Lebens, 2× Glutkern | 20 |
| Salamanderstiefel | Kundschafterin Ilva | 11 | Obsidianstiefel, 4× Salamanderschuppe | 40 |
| 2× Seetangsalbe | Taucher Brack | 11 | 4× Seetang | 10 |
| Wappenschild von Aelmar | Taucher Brack | 12 | Zwergischer Turmschild, 3× Wappen von Aelmar, Lebende Koralle | 80 |
| Sternenlichttonikum | Der Händler ohne Gesicht | 14 | 2× Mondlilie, Albtraumessenz | 60 |
| Leerenschneide | Der Händler ohne Gesicht | 15 | Leerenbrecher, 6× Leerensplitter, 2× Albtraumessenz | 200 |
| Wolfszahnklinge | Waffenmeisterin Brunhild | 10 | Gerulfs Fang, 4× Grabfang, Kriegsaxt | 80 |
| Amulett der Totenruhe | Schwester Liora | 8 | Knochenamulett, 5× Grabstaub | 70 |
| Ring des Rattenkönigs | Goldschmied Aurel | 2 | Rattenkrone, 5× Rattenschwanz | 20 |
| Enterbeil | Taucher Brack | 11 | Kriegsaxt, 3× Enterhaken | 90 |
| Amulett des Krakenauges | Fischerin Maren | 13 | Auge des Kraken, Sturmamulett, 2× Lebende Koralle | 80 |
| Glutkrone | Kundschafterin Ilva | 13 | Krone des Glutfürsten, 3× Glutkern | 100 |
| Krone des Siegelwächters | Der Händler ohne Gesicht | 15 | Krone der Schatten, 4× Leerensplitter, 2× Albtraumessenz | 150 |

## Sets

Manche Ausrüstungsstücke gehören zu einem **Set**; das steht unter dem Namen (z. B. „Set „Obsidian““). Je mehr Teile eines Sets du gleichzeitig **trägst**, desto mehr Boni bekommst du – sie addieren sich. Das Inventar zeigt unter „Ausgerüstet“, wie viele Teile du trägst und was der nächste Bonus bringt; „Ansehen“ listet alle Teile und Boni.

| Set | Teile | Boni |
|---|---|---|
| Jägerleder | Lederkappe, Lederrüstung, Lederbeinlinge, Lederstiefel | 2 Teile: +4 max. HP; 4 Teile: +1 Verteidigung, +4 max. HP |
| Zwergeneisen | Eisenhelm, Eiserne Beinschienen, Eisenbeschlagene Stiefel, Eisenschild, Zwergischer Plattenpanzer | 2 Teile: +5 max. HP; 3 Teile: +1 Verteidigung; 5 Teile: +1 Verteidigung, +10 max. HP |
| Erbe der Valen | Valens Klinge, Ring des Grabfürsten | 2 Teile: +1 Angriff, +5 max. HP |
| Ruf der Gezeiten | Dreizack der Seehexe, Korallenamulett, Ölzeug, Perlenring | 2 Teile: +5 max. HP; 3 Teile: +1 Angriff, +1 Verteidigung |
| Winterwacht | Frostpelzmantel, Drachenschuppenhelm, Herz des Winters, Frostklinge, Drachenschuppenschild | 2 Teile: +1 Verteidigung; 3 Teile: +15 max. HP; 5 Teile: +1 Angriff, +2 Verteidigung |
| Silberhain | Elfenbogen, Elfenmantel, Blattring, Rindenschild | 2 Teile: +1 Angriff; 4 Teile: +1 Angriff, +15 max. HP |
| Obsidian | Obsidianhelm, Obsidianbeinschienen, Obsidianstiefel, Obsidianschild, Obsidianklinge | 2 Teile: +1 Verteidigung; 4 Teile: +20 max. HP; 5 Teile: +1 Angriff, +1 Verteidigung |
| Glut des Fürsten | Flammenschwert, Glutpanzer, Glutsteinamulett, Siegel des Glutfürsten | 2 Teile: +1 Angriff; 4 Teile: +1 Angriff, +1 Verteidigung, +20 max. HP |
| Wacht von Aelmar | Dreizack von Aelmar, Perlmuttschild, Perlenkette, Taucherhelm | 2 Teile: +10 max. HP; 4 Teile: +1 Angriff, +2 Verteidigung |
| Sternenstahl | Sternenstahlhelm, Sternenstahlharnisch, Sternenstahlbeinschienen, Sternenstahlstiefel, Sternenstahlschild | 2 Teile: +1 Verteidigung; 3 Teile: +15 max. HP; 5 Teile: +2 Angriff, +2 Verteidigung |
| Krone der Schatten | Schattenklinge, Schattenring, Seelenanhänger | 2 Teile: +1 Angriff; 3 Teile: +1 Angriff, +20 max. HP |
| Erbe des Wolfskönigs | Krone des Wolfskönigs, Pelzmantel des Wolfskönigs, Wolfszahnklinge | 2 Teile: +1 Angriff; 3 Teile: +1 Verteidigung, +10 max. HP |

## Erfolge & Titel

- Für besondere Taten gibt es **Erfolge**: Stufen erreichen, Aufträge erledigen (einzelne und viele), Bosse besiegen, eine seltene Variante erwischen, ein Set vollständig tragen. Jeder Erfolg zählt einmal und geht nie verloren; im Log steht, wenn du einen errungen hast.
- Manche Erfolge bringen einen **Titel**. Im Erfolge-Panel kannst du einen davon führen – er steht dann hinter deinem Namen, für alle im Raum sichtbar – oder ihn wieder ablegen.
- Bei Kills zählt die Kill-Gutschrift: Die ganze Gruppe im Raum bekommt sie, also auch den Erfolg für einen Boss.
- **Bestiarium:** Jeder Charakter zählt, wie oft er welchen Gegner besiegt hat (seltene Varianten eigens). Das Panel zeigt alle besiegten Arten mit Stufe, Anzahl und wo sie leben. Manche Erfolge verlangen Mengen („100 Feldratten“) oder viele verschiedene Arten.
- Neue Erfolge, die du schon erfüllst (z. B. eine Stufe), bekommst du beim nächsten Einloggen.

## Händler & Bank

- **Elira** (Kaufmannsstand) verkauft Heiltränke, Brot und Kleine Taschen und kauft alles Verkaufbare an.
- Kaufen kostet den Listenpreis (sonst Wert × `merchant.buy_ratio`), Verkaufen bringt **abgerundet** Wert × `merchant.sell_ratio`. Ausgerüstete und angebotene Gegenstände kannst du nicht verkaufen; verkaufte Gegenstände verschwinden.
- **Ramsch verkaufen:** Bei jedem Händler, der etwas davon annimmt, verkauft ein Knopf alles auf einmal, was du nicht brauchst – mit Anzahl und Erlös vorab auf dem Knopf. Ramsch sind: Waffen und Rüstungen, die **▼ schlechter** oder **= gleichwertig** zu deinem getragenen Stück sind (außer Set-Teilen), und Beute, die kein Rezept und kein Auftrag verlangt. Nie Ramsch: Verbrauchsgegenstände, Taschen, Getragenes, Angebotenes, Set-Teile und alles, was ein Rezept oder eine Lieferung braucht.
- Die **Bank** (Bank von Oakhaven) hat `bank.slots` Plätze pro Charakter – nicht pro Account. Einlagern und Abheben nur dort und nicht im Kampf. **Gold** kann nicht eingelagert werden. **Material einlagern** legt mit einem Knopf alle Beute, Handwerksmaterialien, Trophäen und Fundstücke aus dem Rucksack in die Bank, so weit Platz ist; Ausrüstung, Tränke und Essen bleiben bei dir.

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
