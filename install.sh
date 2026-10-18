#!/usr/bin/env bash
# Scourge-Installer. Installiert die App ins eigene Benutzerverzeichnis (~/.local).
# Kein root nötig außer für die Systempakete (fragt vorher nach).
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/.local/share/scourge"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
FONTS="$HOME/.local/share/fonts"
ICONS="$HOME/.local/share/icons/hicolor/scalable/apps"

say() { printf '\033[1;38;5;216m%s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$*"; }

say "== Scourge installieren =="
echo

# ---------------------------------------------------------------- Systempakete
install_deps() {
    if command -v apt >/dev/null 2>&1; then
        sudo apt update
        sudo apt install -y python3 python3-gi python3-gi-cairo gir1.2-gtk-4.0 \
            gir1.2-gst-plugins-base-1.0 gir1.2-poppler-0.18 \
            gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-libav \
            python3-pil python3-numpy python3-lxml ffmpeg wl-clipboard
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y python3 python3-gobject python3-cairo gtk4 \
            gstreamer1-plugins-base gstreamer1-plugins-good \
            poppler-glib python3-pillow python3-numpy python3-lxml wl-clipboard ffmpeg || \
            warn "Konnte nicht alles installieren. ffmpeg braucht evtl. RPM Fusion (siehe README)."
    elif command -v zypper >/dev/null 2>&1; then
        sudo zypper install -y python3 python3-gobject python3-gobject-cairo \
            typelib-1_0-Gtk-4_0 typelib-1_0-Poppler-0_18 typelib-1_0-Gst-1_0 \
            gstreamer-plugins-base gstreamer-plugins-good gstreamer-plugins-libav \
            python3-Pillow python3-numpy python3-lxml ffmpeg wl-clipboard
    elif command -v pacman >/dev/null 2>&1; then
        sudo pacman -S --needed --noconfirm gtk4 python-gobject python-cairo \
            gst-plugins-base gst-plugins-good poppler-glib \
            python-pillow python-numpy python-lxml ffmpeg wl-clipboard
    else
        warn "Kein bekannter Paketmanager gefunden (apt/dnf/zypper/pacman)."
        warn "Bitte die Abhängigkeiten aus der README.md von Hand installieren."
        return 1
    fi
}

# ./install.sh --update (von update.sh): keine Rückfrage, Pakete nicht anfassen
UPDATE=0; [[ "${1:-}" == "--update" ]] && UPDATE=1
if (( UPDATE )); then
    ans=n
else
    read -rp "Systemabhängigkeiten jetzt per Paketmanager installieren? [J/n] " ans
fi
if (( UPDATE )); then
    :
elif [[ ! "${ans,,}" =~ ^n ]]; then
    install_deps || warn "Weiter ohne Paketinstallation."
else
    echo "Übersprungen. (Bei fehlenden Paketen: siehe README.md)"
fi
echo

# ---------------------------------------------------------------- App kopieren
say "Kopiere Scourge nach $APP_DIR ..."
mkdir -p "$APP_DIR" "$BIN" "$APPS" "$FONTS" "$ICONS"
cp -rf "$SRC/app/." "$APP_DIR/"
chmod +x "$APP_DIR/scourge.py" "$APP_DIR/scourge.sh"

# Launcher (Bash: spricht eine laufende Scourge-Instanz direkt per D-Bus an → Fenster sofort da)
ln -sf "$APP_DIR/scourge.sh" "$BIN/scourge"
# läuft eine alte Version im Hintergrund, beenden – der nächste Aufruf startet die neue
"$BIN/scourge" --quit 2>/dev/null || true

# Desktop-Eintrag + Icons
sed "s|@EXEC@|$BIN/scourge|g" "$SRC/scourge.desktop.in" > "$APPS/scourge.desktop"
cp -f "$APP_DIR/logo/scourge.svg" "$ICONS/scourge.svg"
cp -f "$APP_DIR/logo/scourge-symbolic.svg" "$ICONS/scourge-symbolic.svg"
command -v gtk-update-icon-cache >/dev/null 2>&1 && \
    gtk-update-icon-cache -q -t -f "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database "$APPS" 2>/dev/null || true

# ---------------------------------------------------------------- Schriftart
if ! fc-list 2>/dev/null | grep -qi "nerd font"; then
    say "Installiere JetBrainsMono Nerd Font (für die Symbole) ..."
    cp -f "$SRC/fonts/"*.ttf "$FONTS/"
    fc-cache -f "$FONTS" >/dev/null 2>&1 || true
else
    echo "Nerd Font schon vorhanden – überspringe."
fi
echo

# ---------------------------------------------------------------- Selbsttest
say "Prüfe Abhängigkeiten ..."
missing=""
python3 - <<'PY' || missing="python-pakete"
import importlib.util, sys
mods = ["gi", "PIL", "numpy", "lxml", "cairo"]
bad = [m for m in mods if importlib.util.find_spec(m) is None]
try:
    import gi
    for lib, ver in [("Gtk","4.0"),("Gst","1.0"),("Poppler","0.18")]:
        try: gi.require_version(lib, ver)
        except Exception: bad.append(f"{lib}-{ver} (typelib)")
except Exception:
    bad.append("gi")
if bad:
    print("  FEHLT:", ", ".join(bad)); sys.exit(1)
print("  Python/GTK/GStreamer/Poppler: ok")
PY
for t in ffmpeg ffprobe wl-copy; do
    command -v "$t" >/dev/null 2>&1 && echo "  $t: ok" || { echo "  $t: FEHLT"; missing="$missing $t"; }
done
echo

if [[ -n "$missing" ]]; then
    warn "Es fehlt noch: $missing  → siehe README.md (Abschnitt Abhängigkeiten)."
    echo
fi

# PATH-Hinweis
case ":$PATH:" in
    *":$BIN:"*) ;;
    *) warn "Hinweis: $BIN ist nicht in deinem PATH."
       echo "  Füge in ~/.bashrc (oder ~/.zshrc) hinzu:  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

say "Fertig!  Starten mit:  scourge   (oder über das Anwendungsmenü)"
echo "Aktualisieren:   ./update.sh     Deinstallieren:  ./uninstall.sh"
