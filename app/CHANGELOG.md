# scourge – Änderungen

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
