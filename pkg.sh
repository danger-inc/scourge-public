# Gemeinsamer Teil der Installer (wird von install.sh per „source“ geladen).
# Installiert Systempakete so, dass EIN fehlendes Paket nicht die ganze Installation abbricht:
# was es in den Paketquellen der Distribution nicht gibt, wird übersprungen und am Ende genannt.
#   pkg_install "<arch>" "<debian/ubuntu/mint>" "<fedora>" "<opensuse>"
# Kanonische Quelle: rice-apps-packaging/pkg.sh – Kopien liegen in jedem Repo.

say()  { printf '\033[1;38;5;216m%s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$*"; }

pkg_manager() {
    local m
    for m in pacman apt-get dnf zypper; do
        command -v "$m" >/dev/null 2>&1 && { echo "$m"; return 0; }
    done
    return 1
}

as_root() {
    if [ "$(id -u)" = 0 ]; then "$@"
    elif command -v sudo >/dev/null 2>&1; then sudo "$@"
    elif command -v doas >/dev/null 2>&1; then doas "$@"
    else warn "Weder sudo noch doas gefunden – bitte als root ausführen: $*"; return 1
    fi
}

pkg_install() {
    local m want p skipped=() avail=()
    m=$(pkg_manager) || {
        warn "Kein bekannter Paketmanager (pacman/apt/dnf/zypper) – bitte die Pakete aus der README von Hand installieren."
        return 1
    }
    case $m in
        pacman)  want=$1 ;;
        apt-get) want=$2 ;;
        dnf)     want=$3 ;;
        zypper)  want=$4 ;;
    esac
    [ -n "$want" ] || return 0
    case $m in
        pacman)
            for p in $want; do
                if pacman -Si "$p" >/dev/null 2>&1 || pacman -Sg "$p" >/dev/null 2>&1; then avail+=("$p"); else skipped+=("$p"); fi
            done
            [ ${#avail[@]} -gt 0 ] && as_root pacman -S --needed --noconfirm "${avail[@]}" ;;
        apt-get)
            as_root apt-get update -qq || true
            for p in $want; do
                if [ -n "$(apt-cache policy "$p" 2>/dev/null | grep -m1 'Candidate:' | grep -v '(none)')" ]; then
                    avail+=("$p")
                else
                    skipped+=("$p")
                fi
            done
            [ ${#avail[@]} -gt 0 ] && as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y "${avail[@]}" ;;
        dnf)
            # dnf5 (Fedora 41+) kennt --skip-unavailable, dnf4 dafür strict=False
            as_root dnf install -y --skip-unavailable $want 2>/dev/null || \
                as_root dnf install -y --setopt=strict=False $want ;;
        zypper)
            for p in $want; do
                if zypper --non-interactive --quiet search --match-exact "$p" >/dev/null 2>&1; then avail+=("$p"); else skipped+=("$p"); fi
            done
            [ ${#avail[@]} -gt 0 ] && as_root zypper --non-interactive install --no-recommends "${avail[@]}" ;;
    esac
    local rc=$?
    [ ${#skipped[@]} -gt 0 ] && warn "Gibt es hier nicht (übersprungen): ${skipped[*]}"
    return $rc
}

# Fragt [J/n] (Standard ja) bzw. [j/N] mit default=n; im Update-Modus (UPDATE=1) immer „nein“.
ask() {
    local q=$1 def=${2:-j} a
    (( ${UPDATE:-0} )) && return 1
    if [ "$def" = j ]; then read -rp "$q [J/n] " a || a=n; [[ ! "${a,,}" =~ ^n ]]
    else read -rp "$q [j/N] " a || a=n; [[ "${a,,}" =~ ^[jy] ]]
    fi
}

# Symbole/Menüeintrag dem System bekannt machen (Fehler egal – nicht jedes System hat die Werkzeuge)
refresh_desktop() {
    local icons="$HOME/.local/share/icons/hicolor"
    [ -f "$icons/index.theme" ] || { [ -f /usr/share/icons/hicolor/index.theme ] && cp /usr/share/icons/hicolor/index.theme "$icons/" 2>/dev/null; }
    command -v gtk4-update-icon-cache >/dev/null 2>&1 && gtk4-update-icon-cache -q -t -f "$icons" 2>/dev/null
    command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t -f "$icons" 2>/dev/null
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$HOME/.local/share/applications" 2>/dev/null
    command -v kbuildsycoca6 >/dev/null 2>&1 && kbuildsycoca6 >/dev/null 2>&1
    command -v kbuildsycoca5 >/dev/null 2>&1 && kbuildsycoca5 >/dev/null 2>&1
    return 0
}

# Prüft die GTK-Version (die Apps brauchen GTK ≥ 4.12, getestet mit 4.14)
check_gtk() {
    python3 - <<'PY'
import sys
try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
except Exception as e:
    print("  GTK 4 / PyGObject: FEHLT (" + str(e).splitlines()[0] + ")"); sys.exit(1)
v = (Gtk.get_major_version(), Gtk.get_minor_version(), Gtk.get_micro_version())
ok = v >= (4, 12, 0)
print("  GTK %d.%d.%d: %s" % (v + ("ok" if ok else "ZU ALT – mindestens 4.12 nötig (z. B. Ubuntu 24.04, Debian 13, Fedora 39)",)))
sys.exit(0 if ok else 1)
PY
}

path_hint() {
    case ":$PATH:" in
        *":$HOME/.local/bin:"*) ;;
        *) warn "Hinweis: ~/.local/bin ist nicht in deinem PATH."
           echo "  In ~/.bashrc bzw. ~/.zshrc eintragen:  export PATH=\"\$HOME/.local/bin:\$PATH\"  (danach neues Terminal)"
           echo "  Aus dem Anwendungsmenü starten geht auch ohne." ;;
    esac
}
