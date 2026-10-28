#!/usr/bin/env bash
# Scourge-Installer. Installiert die App ins eigene Benutzerverzeichnis (~/.local).
# Kein root nötig außer für die Systempakete (fragt vorher nach).
#   ./install.sh            normal
#   ./install.sh --update   ohne Rückfragen, Systempakete bleiben unberührt (von update.sh)
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SRC/pkg.sh"
APP_DIR="$HOME/.local/share/scourge"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
FONTS="$HOME/.local/share/fonts"
ICONS="$HOME/.local/share/icons/hicolor/scalable/apps"
UPDATE=0; [[ "${1:-}" == "--update" ]] && UPDATE=1

say "== Scourge installieren =="
echo

# ---------------------------------------------------------------- Systempakete
if ask "Systemabhängigkeiten jetzt per Paketmanager installieren?"; then
    pkg_install \
        "gtk4 python python-gobject python-cairo glib2 gst-plugins-base gst-plugins-good gst-libav poppler-glib python-pillow python-numpy python-lxml ffmpeg wl-clipboard fontconfig adwaita-icon-theme wf-recorder slurp poppler qpdf libnotify libpulse" \
        "python3 python3-gi python3-gi-cairo gir1.2-gtk-4.0 libglib2.0-bin gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 gir1.2-poppler-0.18 gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-libav libgtk-4-media-gstreamer python3-pil python3-numpy python3-lxml ffmpeg wl-clipboard fontconfig adwaita-icon-theme wf-recorder slurp poppler-utils qpdf libnotify-bin pulseaudio-utils" \
        "python3 python3-gobject python3-cairo gtk4 glib2 gstreamer1-plugins-base gstreamer1-plugins-good gstreamer1-plugin-libav poppler-glib python3-pillow python3-numpy python3-lxml ffmpeg-free wl-clipboard fontconfig adwaita-icon-theme wf-recorder slurp poppler-utils qpdf libnotify pulseaudio-utils" \
        "python3 python3-gobject python3-gobject-cairo python3-gobject-Gdk typelib-1_0-Gtk-4_0 typelib-1_0-Poppler-0_18 typelib-1_0-Gst-1_0 glib2-tools gstreamer-plugins-base gstreamer-plugins-good gstreamer-plugins-libav python3-Pillow python3-numpy python3-lxml ffmpeg wl-clipboard fontconfig adwaita-icon-theme wf-recorder slurp poppler-tools qpdf libnotify-tools pulseaudio-utils" \
        || warn "Paketinstallation nicht vollständig – weiter."
elif (( ! UPDATE )); then
    echo "Übersprungen. (Bei fehlenden Paketen: siehe README.md)"
fi
echo
# Optional: Texterkennung (OCR) für gescannte PDFs und der Ring um die Maus bei Bildschirmaufnahmen
if ask "Optional: Texterkennung (OCR, Deutsch+Englisch) und Maus-Ring für Aufnahmen installieren?" n; then
    pkg_install \
        "tesseract tesseract-data-deu tesseract-data-eng gtk4-layer-shell" \
        "tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng gir1.2-gtk4layershell-1.0" \
        "tesseract tesseract-langpack-deu tesseract-langpack-eng gtk4-layer-shell" \
        "tesseract-ocr tesseract-ocr-traineddata-german tesseract-ocr-traineddata-english" \
        || warn "Optionale Pakete nicht vollständig – Scourge läuft auch ohne."
    echo
fi

# ---------------------------------------------------------------- App kopieren
say "Kopiere Scourge nach $APP_DIR ..."
mkdir -p "$APP_DIR" "$BIN" "$APPS" "$FONTS" "$ICONS"
cp -rf "$SRC/app/." "$APP_DIR/"
chmod +x "$APP_DIR/scourge.py" "$APP_DIR/scourge.sh" "$APP_DIR/scourge_pdf.py" "$APP_DIR/scourge-pdf.sh"

# Launcher (Bash: spricht eine laufende Scourge-Instanz direkt per D-Bus an → Fenster sofort da)
ln -sf "$APP_DIR/scourge.sh" "$BIN/scourge"
ln -sf "$APP_DIR/scourge-pdf.sh" "$BIN/scourge-pdf"      # schlanker PDF-Editor
# läuft eine alte Version im Hintergrund, beenden – der nächste Aufruf startet die neue
timeout 5 "$BIN/scourge" --quit >/dev/null 2>&1 || true
timeout 5 "$BIN/scourge-pdf" --quit >/dev/null 2>&1 || true

# Desktop-Eintrag + Icons
sed "s|@EXEC@|$BIN/scourge|g" "$SRC/scourge.desktop.in" | grep -v '^# \[' > "$APPS/scourge.desktop"
sed "s|@EXEC@|$BIN/scourge-pdf|g" "$SRC/scourge-pdf.desktop.in" > "$APPS/scourge-pdf.desktop"
cp -f "$APP_DIR/logo/scourge.svg" "$ICONS/scourge.svg"
cp -f "$APP_DIR/logo/scourge-symbolic.svg" "$ICONS/scourge-symbolic.svg"
refresh_desktop

# ---------------------------------------------------------------- Schriftart
# Die Symbole bringt Scourge selbst mit (app/fonts/notes-icons.ttf, wird beim Start geladen).
# Für die Schrift der Oberfläche JetBrains Mono (Nerd Font) mitinstallieren, falls nicht vorhanden.
if ! fc-list : family 2>/dev/null | grep -qi "JetBrainsMono Nerd Font"; then
    say "Installiere JetBrainsMono Nerd Font (Schrift der Oberfläche) ..."
    cp -f "$SRC/fonts/"*.ttf "$FONTS/"
    fc-cache -f "$FONTS" >/dev/null 2>&1 || true
else
    echo "JetBrainsMono Nerd Font schon vorhanden – überspringe."
fi
echo

# ---------------------------------------------------------------- Selbsttest
say "Prüfe Abhängigkeiten ..."
missing=""
check_gtk || missing="gtk"
python3 - <<'PY' || missing="$missing python-pakete"
import importlib.util, sys
bad = [m for m in ["gi", "PIL", "numpy", "lxml", "cairo"] if importlib.util.find_spec(m) is None]
try:
    import gi
    for lib, ver in [("Gst", "1.0"), ("Poppler", "0.18")]:
        try:
            gi.require_version(lib, ver)
        except Exception:
            bad.append(f"{lib}-{ver} (typelib)")
except Exception:
    bad.append("gi")
if bad:
    print("  FEHLT:", ", ".join(bad)); sys.exit(1)
print("  Python/GStreamer/Poppler: ok")
PY
for t in ffmpeg ffprobe wl-copy; do
    command -v "$t" >/dev/null 2>&1 && echo "  $t: ok" || { echo "  $t: FEHLT"; missing="$missing $t"; }
done
for t in pdftotext pdfimages qpdf; do
    command -v "$t" >/dev/null 2>&1 && echo "  $t: ok" || echo "  $t: fehlt – PDF-Text/-Bilder bzw. PDF bereinigen gehen dann nicht (Paket poppler-utils / qpdf)"
done
command -v tesseract >/dev/null 2>&1 && echo "  tesseract: ok" || echo "  tesseract: fehlt – nur für „Text erkennen (OCR)“ im PDF-Editor (Pakete tesseract + Sprachdaten)"
for t in wf-recorder slurp; do
    command -v "$t" >/dev/null 2>&1 && echo "  $t: ok" || echo "  $t: fehlt – nur für die Bildschirmaufnahme (wlroots/Hyprland/Sway)"
done
echo
[[ -n "$missing" ]] && { warn "Es fehlt noch: $missing  → siehe README.md (Abschnitt Abhängigkeiten)."; echo; }

path_hint
say "Fertig!  Starten mit:  scourge   (oder über das Anwendungsmenü)"
echo "Aktualisieren:   ./update.sh     Deinstallieren:  ./uninstall.sh"
