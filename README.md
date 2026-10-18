# Scourge

Dateien durchleuchten (Metadaten/EXIF), PDFs bearbeiten, Bildschirm aufnehmen,
Videos schneiden, Memes bauen, Farbverläufe & ASCII-Kunst erzeugen – eine GTK4-App für Linux.

## Installieren

```bash
git clone https://github.com/invincible-nix/scourge-public ~/scourge-src
cd ~/scourge-src
./install.sh
```

Das Skript installiert die Abhängigkeiten (fragt vorher), kopiert Scourge nach
`~/.local/share/scourge` und legt den Starter `scourge` und einen Menüeintrag an.
Die Symbole bringt Scourge selbst mit (`app/fonts/notes-icons.ttf`).

## Aktualisieren

```bash
cd ~/scourge-src
./update.sh
```

Holt die neueste Version und installiert sie. Danach Scourge einmal neu starten.

Starten danach:

```bash
scourge            # oder über das Anwendungsmenü ("Scourge")
scourge bild.jpg   # direkt eine Datei öffnen
```

## Schneller Start & PDF-Bearbeitung

- **Hintergrundmodus:** ✕ bzw. Strg+Q blendet Scourge nur aus; der nächste Aufruf von `scourge` ist sofort da
  (der Starter spricht die laufende Instanz direkt per D-Bus an). Ganz beenden: `scourge --quit`.
  Für sofortigen ersten Start in den Autostart eintragen, z. B. Hyprland: `exec-once = scourge --daemon`.
- **PDFs** öffnen sofort; die Analyse läuft im Hintergrund. In der Werkzeugleiste: Text auswählen &
  kopieren, markieren, unterstreichen, durchstreichen, Text einfügen (Schrift, Größe, Farbe), Stift, Rahmen,
  Notiz, Radierer, Rückgängig (Strg+Z) und Speichern (Strg+S, Strg+Umschalt+S = neue Datei). Die Anmerkungen
  sind echte PDF-Anmerkungen und bleiben in anderen PDF-Programmen bearbeitbar.
- **Apps-Leiste:** Maus an den linken Fensterrand oder Klick auf „scourge“ oben links – Starter für
  notist, filist, musicalist und datist (falls installiert).

## Was läuft wo

Scourge braucht **Linux mit GTK4** (Wayland empfohlen, X11 geht für das meiste auch).
Die App passt sich automatisch an ein vorhandenes **matugen**-Theme an
(`~/.cache/matugen/colors.css`); ist keines da, nutzt sie ein eigenes Farbschema.

Diese Funktionen hängen von zusätzlichen Programmen ab:

| Funktion | braucht | Hinweis |
|---|---|---|
| Datei prüfen, Meme-Editor, Verlauf & ASCII | – | läuft überall |
| PDF ansehen | Poppler | per Paket installiert |
| Video schneiden / Export, Vorschaubilder | `ffmpeg` | per Paket installiert |
| Kopieren in die Zwischenablage | `wl-clipboard` (Wayland) | unter X11 nutzt GTK xclip-artig selbst |
| **Bildschirm aufnehmen** | `wf-recorder`, `slurp` | **nur wlroots/Hyprland/Sway** – unter GNOME/KDE nicht verfügbar |
| **System-Einstellungen** (Maus/Tastatur) | `hyprctl` | **nur Hyprland** – der Rest (Helligkeit/Ton/Funk) geht über `brightnessctl`/`wpctl`/`nmcli`, falls vorhanden |

Kurz: **Datei-Analyse, Editor, Cutter und Studio laufen auf jedem Linux.**
Die Bildschirmaufnahme und die Maus-/Tastatur-Einstellungen sind Hyprland-spezifisch
und bleiben auf anderen Oberflächen einfach inaktiv.

## Abhängigkeiten von Hand (falls der Paketmanager nicht erkannt wird)

**Python/GTK-Kern:** GTK4, PyGObject, pycairo, Pillow, numpy, lxml
**GI-Typelibs:** Gtk-4.0, Gst-1.0 (GStreamer), Poppler-0.18
**GStreamer-Plugins:** base, good, libav (für Videowiedergabe)
**Externe Tools:** ffmpeg, wl-clipboard

- **Debian/Ubuntu:** `python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-gst-plugins-base-1.0 gir1.2-poppler-0.18 gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-libav python3-pil python3-numpy python3-lxml ffmpeg wl-clipboard`
- **Fedora:** `python3-gobject python3-cairo gtk4 gstreamer1-plugins-base gstreamer1-plugins-good poppler-glib python3-pillow python3-numpy python3-lxml wl-clipboard ffmpeg` (ffmpeg via RPM Fusion)
- **openSUSE:** `python3-gobject typelib-1_0-Gtk-4_0 typelib-1_0-Poppler-0_18 typelib-1_0-Gst-1_0 gstreamer-plugins-base gstreamer-plugins-good python3-Pillow python3-numpy python3-lxml ffmpeg wl-clipboard`
- **Arch:** `gtk4 python-gobject python-cairo gst-plugins-base gst-plugins-good poppler-glib python-pillow python-numpy python-lxml ffmpeg wl-clipboard ttf-jetbrains-mono-nerd`

## Deinstallieren

```bash
./uninstall.sh
```

Entfernt die App, den Starter, den Menüeintrag und die Icons. Systempakete und die
Schriftart bleiben.
