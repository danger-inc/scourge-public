# scourge – Änderungen

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
