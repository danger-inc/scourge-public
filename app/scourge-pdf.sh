#!/usr/bin/env bash
# Starter für scourge-pdf (schlanker PDF-Editor). Läuft Scourge schon (auch unsichtbar im Hintergrund), geht der Aufruf direkt per D-Bus
# hin – ohne Python-/GTK-Start, das Fenster ist sofort da. Sonst wird Scourge gestartet.
#   scourge-pdf [datei.pdf …]   scourge-pdf --daemon   scourge-pdf --quit
APP=de.invincible.ScourgePdf
OBJ=/de/invincible/ScourgePdf
MAIN="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/scourge_pdf.py"

running() { gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
    --method org.freedesktop.DBus.NameHasOwner "$APP" 2>/dev/null | grep -q true; }

case "${1:-}" in
    --daemon)
        running && exit 0
        setsid -f python3 "$MAIN" --daemon >"${XDG_RUNTIME_DIR:-/tmp}/scourge-pdf.log" 2>&1 </dev/null
        exit 0 ;;
    --quit)
        running && gdbus call --session --dest "$APP" --object-path "$OBJ" \
            --method org.gtk.Actions.Activate quit '[]' '{}' >/dev/null 2>&1
        exit 0 ;;
esac

running || exec python3 "$MAIN" "$@"

# Absicherung: Die Hintergrund-Instanz kann „blind“ werden (Grafik-Kontext kaputt) – sie nimmt den Aufruf an,
# zeigt aber kein Fenster. Erscheint es nicht binnen ~2 s, wird sie beendet und Scourge frisch gestartet.
shown() {
    command -v hyprctl >/dev/null || return 0            # ohne Hyprland nicht prüfbar
    local i
    for (( i = 0; i < 20; i++ )); do
        hyprctl clients -j 2>/dev/null | grep -q "\"class\": \"$APP\"" && return 0
        sleep 0.1
    done
    return 1
}
restart() {
    local pid
    pid=$(gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
        --method org.freedesktop.DBus.GetConnectionUnixProcessID "$APP" 2>/dev/null | grep -o '[0-9]\+' | tail -1)
    if [ -n "$pid" ]; then
        kill "$pid" 2>/dev/null
        for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$pid" 2>/dev/null || break; sleep 0.2; done
        kill -9 "$pid" 2>/dev/null
    fi
    exec python3 "$MAIN" "$@"
}

if [ $# -eq 0 ]; then
    gdbus call --session --timeout 3 --dest "$APP" --object-path "$OBJ" \
        --method org.freedesktop.Application.Activate "{}" >/dev/null 2>&1 && { shown || restart; exit 0; }
    restart                                              # Instanz antwortet nicht → neu starten
fi

urlencode() {      # Pfad → URI-Teil (UTF-8 byteweise, wie Gio es erwartet) – ohne Python-Start
    local LC_ALL=C s="$1" out="" c i
    for (( i = 0; i < ${#s}; i++ )); do
        c="${s:i:1}"
        case "$c" in [a-zA-Z0-9/._~-]) out+="$c" ;; *) printf -v c '%%%02X' "'$c"; out+="$c" ;; esac
    done
    printf '%s' "$out"
}

uris=()
for a in "$@"; do
    case "$a" in
        file://*) uris+=("'$a'") ;;
        -*) exec python3 "$MAIN" "$@" ;;          # unbekannte Option: normal starten
        *) uris+=("'file://$(urlencode "$(realpath -m -- "$a")")'") ;;
    esac
done
join() { local IFS=,; echo "[$*]"; }
gdbus call --session --timeout 3 --dest "$APP" --object-path "$OBJ" \
    --method org.freedesktop.Application.Open "$(join "${uris[@]}")" "{}" >/dev/null 2>&1 && { shown || restart "$@"; exit 0; }
restart "$@"
