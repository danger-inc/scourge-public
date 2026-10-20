# scourge – Änderungen

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
