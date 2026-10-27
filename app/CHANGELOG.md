# scourge – Änderungen

## 2.15.0 (2026-10-27)
- **Dokumente umwandeln** (neue Kachel auf der Startseite): PDFs zusammenfügen (Reihenfolge per ▲▼, pro PDF ein Seitenbereich wie „1-3, 5, 8-“, Bilder werden eigene Seiten), Seiten behalten/löschen/in Einzelseiten oder n-Seiten-Teile teilen/drehen/umkehren, PDF verkleinern sowie PDF ⇄ Bild (PNG/JPG/WebP, wählbare Auflösung) ⇄ Text ⇄ Word/ODT/HTML/Markdown. Word, Excel und PowerPoint gehen mit LibreOffice, Markdown und EPUB mit pandoc; was fehlt, steht oben auf der Seite. Originale bleiben unverändert.
- **Akku-Warnung**: Fällt der Akku unter 20 % (und noch einmal unter 10 %), kommt eine Meldung – auch wenn Scourge nur im Hintergrund läuft. Abschaltbar im Zahnrad unter „verhalten“.
- **PDF-Zoom mit zwei Fingern**: Die Ansicht scrollte beim Zusammen-/Auseinanderziehen weit weg; jetzt bleibt die Bildmitte an ihrer Stelle, und Rest-Scrollbewegungen während der Geste werden ignoriert.

## 2.14.1 (2026-10-27)
- **Fenster verschieben (GNOME/KDE/Debian)**: Die Funktion fehlte in der veröffentlichten Fassung ganz; jetzt lässt sich das Fenster am Kopf ziehen, auch auf den Reitern (bei mehreren Reitern: senkrecht ziehen, waagerecht ordnet sie um).
- **Scrollen**: Im Glas-Modus ruckelte es alle paar Bilder; behoben.
- **Changelog**: Fenster öffnet auch mit älterem GTK.

## 2.14.0 (2026-10-27)
- **Wechsel-Animation**: Der Wechsel zwischen Hell und Dunkel (und zwischen Designs) läuft als Scanbalken über den ganzen Bildschirm. Alle Fenster, die Leiste und das Terminal wechseln darunter im selben Moment, hinter dem Balken steht das neue Design. An/aus und Dauer (Standard 1,5 s) stehen in der Designauswahl der Einstellungen.

## 2.13.0 (2026-10-27)
- **divist** (neuer Taschenrechner der Suite) ist in der Apps-Leiste, unter software → suite-apps (starten, aktualisieren, Autostart, Icon), im Veröffentlichen-Fenster und bei den Update-Hinweisen eingetragen.

## 2.12.0 (2026-10-26)
- **Design wählbar**: In den Einstellungen gibt es die Designauswahl wie in notist (Automatisch + 9 Designs). Die Wahl gilt für alle -ist-Apps zugleich und wirkt sofort. Bei „Automatisch“ (Desktop-Farben) kannst du zwischen **Dunkel** und **Hell** umschalten.

## 2.11.4 (2026-10-25)
- **Windows**: Schlägt die Installation einer App fehl, steht der Grund in der Meldung und im Log
  (`<app>-install.log` im Benutzerordner).

## 2.11.3 (2026-10-25)
- **Private Apps ohne `gh` installieren**: Bei „kein zugriff aufs repo“ fragt der Knopf jetzt nach einem GitHub-Token
  (Lesezugriff), und das Installieren/Aktualisieren nutzt den gespeicherten Token (vorher ging nur `gh auth login`).

## 2.11.2 (2026-10-25)
- **Apps-Leiste: Rechtsklick auf eine App** – „dateipfad öffnen“ (Programmordner im Dateimanager) und
  „deinstallieren“ (mit Rückfrage; Programm in den Papierkorb, eigene Daten bleiben).

## 2.11.1 (2026-10-25)
- **Windows**: Speichern brach bei Zeichen wie „☑“ mit einem Zeichensatz-Fehler ab; Textdateien sind dort jetzt UTF-8.
- **Windows**: Der Glas-Schalter funktioniert dort wieder (nur die Unterlage, die das Einfrieren auslöste, bleibt aus).
- **Windows**: Die Apps-Leiste zeigt jetzt alle Suite-Apps und lässt sie herunterladen. Private Repos gehen mit
  einem GitHub-Token (Lesezugriff): schlägt der Download mit „kein Zugriff“ fehl, fragt der Knopf danach.
  Apps ohne Windows-Installer landen als Quelltext in Downloads.

## 2.11.0 (2026-10-25)
- **scourge-pdf – der schlanke PDF-Editor**: PDFs öffnen jetzt in einem eigenen, sofort startenden Programm
  (`scourge-pdf`, eigener Prozess und Menü-Eintrag, wartet im Hintergrund). Aus filist, per `scourge datei.pdf` und
  über die Kachel „Öffnen“ landen PDFs dort. Es speichert automatisch, Schließen (Super+C) sichert und schließt alle
  Tabs. Im vollen Scourge zeigt ein PDF nur noch die Analyse mit Leseansicht und dem Knopf „in scourge-pdf
  bearbeiten“.
- **PDF-Editor im Suite-Look**: durchsichtiger Hintergrund (Glas), Schrift und Tabs wie in Scourge, Popups („zu
  Seite springen“, „eigene Farbe“ mit Reglern und Vorschlägen), kleinere Schrift-/Größenfelder; die erste Kachel der
  Werkzeuge ist jetzt „text schreiben“, danach „text auswählen“.
- **PDF-Editor**: Zoom mit zwei Fingern (Pinch), Zu-Seite-springen (Klick auf die Seitenzahl oder Strg+G), eigene
  Markierfarben (Knopf „+“, Rechtsklick entfernt eine Farbe) und ein eindeutigeres Symbol für „auswählen“ (Pfeil
  statt Textcursor, keine Verwechslung mit „Text“).
- **Öffnen**: Neue erste Kachel unter misc, dieselbe Funktion wie der Knopf „öffnen“ oben und Strg+O (alles andere, z. B. Office,
  geht in die Dateiprüfung; die gibt es auch als Kachel „Datei prüfen“). Bilder gehen im Vollbild auf (← → blättert durch den Ordner), Videos und
  Musik spielen mit Zeitleiste (Zehntelsekunden, Zeit unter dem Mauszeiger), Wiederholen (standardmäßig an),
  Lautstärke und Leertaste; Esc/Doppelklick beendet das Vollbild. Die Musik-Anzeige in der Dateiprüfung hat dieselbe
  Leiste – der Name steht nicht mehr über dem Play-Knopf.
- **Video schneiden mit mehreren Spuren**: Videos und Musik liegen als Stücke auf Spuren (wie in DaVinci Resolve).
  Werkzeug „Verschieben“ (V) zieht Stücke – auch auf andere Spuren, um sie zu überlagern (Musik unter Gameplay) –
  und kürzt an den Kanten; „Schneiden“ (C) teilt per Klick. Magnetisch (ein/aus) rastet an Kanten und am Abspielkopf
  ein, beim Entfernen schließt sich die Lücke (ein/aus). Die Vorschau spielt alle Spuren zugleich, der Export mischt
  den Ton und legt das Bild der obersten Spur über Schwarz. Zeitleistenhöhe am Griff ziehen (jetzt flüssig),
  Leertaste, Wiederholen. Beim Verlassen fragt Scourge, ob der Schnitt zwischengespeichert werden soll (Karte
  „Zwischenstand“ beim nächsten Öffnen).
- **Einstellungen oben rechts (Zahnrad)** öffnen ein eigenes Fenster im Suite-Look: Glas-Design, im Hintergrund
  weiterlaufen, Autostart, alle Tabs schließen, komplett beenden, Version und Update-Suche. **Schließen (Super+C, ✕,
  Strg+Q) schließt jetzt alle Tabs** (ungespeicherte PDFs werden vorher gesichert).
- **Suche auf der Startseite** (einfach lostippen oder Strg+F): findet Werkzeuge, Einstellungen und Autostart. Die
  Startseite rutscht beim Öffnen einer Kachel nicht mehr nach unten, die 2-Finger-Animation passt dadurch exakt –
  und der Hyprland-Kachel-Absturz (Fehler beim Aufräumen der Wisch-Ansicht) ist behoben.
- **2 Finger auch vorwärts**: Nach dem Zurück-Wischen bringt die Gegenrichtung zur verlassenen Seite zurück.
- **System & Terminal-Visuals**: Bildschirm, Ton und Funk sind eine Kachel. Hyprland-Kachel, Eingabe, Cursor und
  Waybar erscheinen nur, wo Hyprland eingerichtet ist. Terminal-Visuals: der erste Eintrag ist nicht mehr markiert,
  fehlende Programme haben einen „installieren“-Knopf (öffnet ein Terminal mit dem passenden Befehl), Visuals
  starten im Vollbild.
- **Regler zurücksetzen**: Doppelklick auf den Titel eines Reglers (z. B. bei ASCII, Verlauf, Komprimieren) setzt
  ihn auf den Ausgangswert. **Umschalt-Knöpfe** („Ton behalten“ …) zeigen den Klick sofort, auch unter dem
  Mauszeiger.
- **Update-Knopf**: Neben der Versionsnummer steht „󰑐“ – Klick sucht sofort nach einem Update. Gibt es eine neuere
  Version, wird daraus „󰚰 update“; ein Klick lädt und installiert sie (Linux und Windows), danach heißt er „󰑓 neu starten“.
- **Öffentliche Ausgabe**: Die Kachel „Suite-Apps“ entfällt; Standard-Apps, Terminal-Visuals und Hyprland bleiben.

## 2.10.0 (2026-10-24)
- **linguist in der Suite**: Der neue Übersetzer steht in der Apps-Leiste, unter „software“ (Installieren, Updates,
  Neustart, Einstellungen), bei den Suite-Icons und in der Veröffentlichen-Liste. Maus in die untere rechte
  Bildschirmecke → Übersetzer-Karte (Hover, in den linguist-Einstellungen einschaltbar). Super+T öffnet das Fenster,
  Super+Umschalt+T übersetzt den markierten Text. Der Knopf „übersetzer“ in notist und mailist zeigt dann an, ob
  Hover an ist.

## 2.9.0 (2026-10-22)
- **Apps installieren mit einem Klick**: In der Apps-Leiste links hat jede App, die fehlt, rechts einen kleinen
  Knopf 󰇚. scourge lädt sie dann von GitHub und installiert sie im Hintergrund (Fortschritt steht unter dem
  Namen). Fehlen Systempakete, öffnet ein zweiter Klick die Installation im Terminal (dort fragt sie nach sudo).
- **Alte Namen werden erkannt**: Ist eine App noch unter einem früheren Namen installiert (wallpapaer, filewise,
  kalender, textwise, musicminus), steht „früher …“ darunter. Die Installation räumt den alten Menüeintrag, das
  alte Icon und den alten Hintergrunddienst weg, übernimmt z. B. das Stil-KI-Modell von wallpapaer und lässt den
  alten Befehl auf die neue App zeigen – eigene Tastenkürzel gehen also weiter. Deine Daten bleiben.
- **Updates für alle Apps an einer Stelle**: Unter den Apps steht „nach updates suchen“ bzw. **„updates
  installieren“** mit der Liste der Apps, für die es eine neue Version gibt. Ein Klick installiert alle
  nacheinander; Apps, die nur im Hintergrund laufen, starten danach gleich mit der neuen Version, bei offenen
  Fenstern zeigt der Knopf 󰑓 „neu starten“. Einzelne Updates gehen auch über den Knopf 󰚰 neben der App.

## 2.8.0 (2026-10-22)
- **Neu: Hyprland** (Startseite → software): alle Tastenkürzel aus der hyprland.lua verständlich beschrieben,
  nach Rubriken sortiert und durchsuchbar (z. B. „screenshot“ oder „Super+M“) – neue Kürzel erscheinen von
  selbst. Dazu Touchpad-Gesten, was beim Anmelden startet, Status (Version, Bildschirme, Konfig-Fehler) mit
  Knopf „neu laden“, nützliche hyprctl-Befehle zum Ausführen oder Kopieren und die wichtigen Dateien.
- **Tabs in schmalen Fenstern**: Die Reiter teilen sich jetzt die ganze Kopfzeile, das ✕ steht immer am rechten
  Rand des Reiters. Wird es eng, werden die Namen gekürzt; Pfeile gibt es erst bei sehr vielen Tabs. Vorher
  verschwanden Reiter, und der letzte wurde mit dem ✕ in der Mitte in die Breite gezogen.
- **Eigene Icons laden schneller**: scourge legt sie jetzt zusätzlich als kleine Bilder (16–512 px) ins
  Icon-Theme und verkleinert eingebettete Riesenbilder in der SVG (8 MB → 0,3 MB). Der Starter (Super+R) und die
  Taskleiste zeigen sie dadurch sofort.
- **Apps-Leiste links**: Eigene Icons erscheinen in derselben Größe wie die anderen Symbole statt riesig.

## 2.7.0 (2026-10-20)
- **Suite-Apps verwalten** (Startseite → software → Suite-Apps): Jede App zeigt jetzt, ob sie läuft, seit wann,
  wie viel Arbeitsspeicher sie braucht und ob ein Fenster offen ist. Dazu Knöpfe zum **Neustarten** und
  **Beenden** (auch den unsichtbaren Hintergrund-Prozess), das **Protokoll** der letzten Ausgaben zur Fehlersuche
  und **Cache leeren** (Vorschaubilder und Zwischendateien, gesicherte PDF-Originale bleiben). Ist ein Fenster
  der App offen, fragt scourge vor dem Beenden einmal nach.
- Der Knopf „ordner öffnen“ bei den eigenen Icons ist weg – „icon wählen“ bei der App macht dasselbe direkter.

## 2.6.1 (2026-10-20)
- **Windows: kein Glas-Effekt mehr.** Der Knopf „durchsichtig“ ließ scourge unter Windows einfrieren, und weil
  die Einstellung gespeichert blieb, half auch Neuinstallieren nicht. Unter Windows ist der Knopf jetzt weg,
  Fenster sind immer undurchsichtig, und eine alte Einstellung wird ignoriert.

## 2.6.0 (2026-10-20)
- **Neu: Bilder komprimieren** (Startseite → misc): viele Bilder auf einmal kleiner machen – Bilder wählen,
  Ordner wählen oder hineinziehen. Wahlweise nach Qualität oder mit Zielgröße je Bild (z. B. „höchstens 500 KB“),
  dazu Format wechseln (JPEG, WebP, AVIF, PNG), auf eine längste Seite verkleinern und Metadaten (GPS, Kamera,
  Datum) entfernen. PNGs werden auf Wunsch mit weniger Farben gespeichert, oft 60–80 % kleiner. Eine Vorschau
  zeigt das Ergebnis und die neue Größe, bevor du etwas speicherst. Originale bleiben unverändert, die neuen
  Dateien heißen `…-klein`.
- **Farben nach Wallpaper-Wechsel**: scourge blieb manchmal in den alten Farben hängen (Knöpfe, Regler, der
  Chip oben links), sobald einmal das Changelog-Fenster offen gewesen war. Behoben.

## 2.5.1 (2026-10-20)
- **Neues Zurück-Wischen im -ist-Design**: Beim Wischen mit zwei Fingern schiebt sich die vorige Seite als Rahmen vom Rand herein – mit einem echten Bild davon und ihrem Namen im Chip. Ist der Rahmen durchgezogen, führt Loslassen zurück, und die Seite klappt über das ganze Fenster auf.
- Nach dem Zurück-Wischen springt die Seite nicht mehr ein Stück nach oben (der erste Millimeter der Geste scrollte noch mit und lief nach).

## 2.5.0 (2026-10-20)
- **Zwei Finger nach rechts wischen** (oder Maustaste „zurück“) geht eine Ebene zurück, wie in filist.
- **Wallpaper-Wechsel (Super+W)**: scourge übernimmt die neuen Farben jetzt zuverlässig überall, auch in
  selbst gezeichneten Teilen wie Kartentiteln, und auch wenn scourge gerade im Hintergrund wartet.
- **Terminal-Visuals**: `istfx`-Effekte wechseln beim Wallpaper-Wechsel live die Farben, statt bei den alten
  zu bleiben. „starten“ funktioniert jetzt immer – vorher schloss sich das Terminal sofort, wenn scourge beim
  Anmelden im Hintergrund gestartet war. Schlägt ein Befehl fehl, bleibt das Fenster mit der Meldung offen.

## 2.4.0 (2026-10-20)
- **Neue Rubrik „software“** auf der Startseite:
  - **Standard-Apps**: Browser, Dateimanager, PDF, Bilder, Videos, Musik, Text, Word, LaTeX, Archive, E-Mail-Links –
    je eine Auswahlliste, gilt sofort systemweit.
  - **Suite-Apps**: notist, filist, musicalist, datist, wallist und scourge mit Version, Tastenkürzel, Ordner, wofür
    sie Standard sind, „öffnen“ und „einstellungen“ (springt direkt in die Einstellungen der App) und Schalter fürs
    Vorladen beim Anmelden.
  - **Eigene Icons**: <app>.svg oder .png in ~/.config/ist-icons legen oder „icon wählen“ – scourge schreibt es als
    App-Symbol ins System (Starter, Taskleiste, Apps-Leiste) und setzt es nach Updates erneut. „icon zurücksetzen“
    holt das Original zurück, dein Entwurf bleibt in .alt/.
  - **Terminal-Visuals**: neuer Befehl `istfx` mit matrix, fire, stars, donut, plasma, life, clock, rain und fetch –
    immer in den Farben des aktuellen Hintergrunds. Dazu cbonsai, pipes.sh, cava, btop & Co. mit Start- und
    Kopierknopf.
- **PDF per Doppelklick** (z. B. aus filist) öffnet direkt im Vollbild als Arbeitsfläche; Esc/F11 beendet beides.
- Die Apps-Leiste schließt jetzt auch, wenn die Maus über den linken Rand aus dem Fenster fährt.
- **Version oben rechts** wie bei den anderen Apps – Klick zeigt den Changelog, nach einem Update hervorgehoben.

## 2.3.0 (2026-10-20)
- **PDF-Editor speichert automatisch** direkt in die geöffnete Datei – kein Strg+S und kein „Speichern unter“ mehr
  nötig, es entstehen keine Kopien. Vor dem ersten Überschreiben wird das Original einmal nach
  ~/.cache/scourge/originale gesichert (30 Tage). Beim Schließen eines Tabs wird ohne Rückfrage gespeichert.

## 2.2.0 (2026-10-19)
- **Changelog**: Apps-Leiste → „changelog“ zeigt, was sich in jeder Version geändert hat. Nach einem Update
  meldet die Statuszeile einmal, dass es Neues gibt.
- Apps-Leiste: **wallpapaer** (Hintergründe für Super+W) ist jetzt mit verlinkt.
- **Speicherleck behoben**: Geschlossene Tabs blieben samt Bildern, PDFs und Videos im Speicher (rund 40 MB pro
  Tab, im Hintergrundmodus wuchs scourge so über Stunden). Jetzt wird alles freigegeben und an das System zurückgegeben.

## 2.1.0 (2026-10-18)
- **PDF-Editor**: Textmarker, Unterstreichen, Durchstreichen, Freitext direkt auf der Seite (Schrift, Größe und
  Farbe live), Freihand, Rechtecke, Notizen, Radierer, Rückgängig/Wiederholen, Speichern mit Strg+S.
- PDF-Arbeitsfläche (F11 oder 󰊓): blendet Tabs, Infospalte und Statuszeile aus.
- **Hintergrundmodus**: ✕ / Strg+Q blendet nur aus, Tabs bleiben offen; `scourge --daemon` lädt beim Anmelden
  vor, der nächste Aufruf ist sofort da. Ein hängender Hintergrundprozess wird erkannt und neu gestartet.
- **Apps-Leiste** links (Maus an den Fensterrand oder Klick auf „scourge“, anheftbar): startet notist, filist,
  musicalist und datist.
- Neue Seiten unter System & Hardware: **Cursor** (Farbe, Form, Warte-Animation, Ausblenden) und **Waybar**
  (Status, Neustart, letzte Abstürze). Maus/Tastatur-Einstellungen wirken jetzt sofort.
- Dateien auf **Netzlaufwerken** (SMB/NFS) öffnen richtig, statt als „PDF (geschützt)“ zu erscheinen.
- Schnelleres Öffnen von Bildern, PDFs und 4K-Videos.
- Installer: fehlende Pakete werden übersprungen statt abzubrechen; poppler-utils, qpdf, notify-send und pactl
  werden mitinstalliert; Windows-Installer; Startgröße passt auf kleine Bildschirme.

## 2.0.0 (2026-10-18)
- **Suite-Look** wie datist/musicalist: Marken-Chip, Tabs mit Nummern (Alt+1–9), Statuszeile mit Tastenhinweisen,
  Kästen mit Titel im Rahmen.
- Eigene Symbolschrift mitgeliefert – Symbole erscheinen auch ohne Nerd Font.
- Fenster lässt sich sauber bis auf halbe Bildschirmbreite verkleinern.

## 1.x (bis 2026-10-17)
- Datei-Analyse mit Metadaten, Bildschirmaufnahme, Video-Schnitt, Meme-Editor.
- Verlauf & ASCII-Studio, Server-Check (Ping, Ports, Route), System-Einstellungen (Bildschirm, Ton, Eingabe, Funk).
