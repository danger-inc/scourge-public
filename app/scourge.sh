#!/usr/bin/env bash
# Starter für Scourge. Läuft Scourge schon (auch unsichtbar im Hintergrund), geht der Aufruf direkt per D-Bus
# hin – ohne Python-/GTK-Start, das Fenster ist sofort da. Sonst wird Scourge gestartet.
#   scourge [datei …]   scourge --daemon   scourge --quit
APP=de.invincible.Scourge
OBJ=/de/invincible/Scourge
MAIN="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/scourge.py"

running() { gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
    --method org.freedesktop.DBus.NameHasOwner "$APP" 2>/dev/null | grep -q true; }

case "${1:-}" in
    --daemon)
        running && exit 0
        setsid -f python3 "$MAIN" --daemon >"${XDG_RUNTIME_DIR:-/tmp}/scourge.log" 2>&1 </dev/null
        exit 0 ;;
    --quit)
        running && gdbus call --session --dest "$APP" --object-path "$OBJ" \
            --method org.gtk.Actions.Activate quit '[]' '{}' >/dev/null 2>&1
        exit 0 ;;
esac

running || exec python3 "$MAIN" "$@"

if [ $# -eq 0 ]; then
    gdbus call --session --dest "$APP" --object-path "$OBJ" \
        --method org.freedesktop.Application.Activate "{}" >/dev/null 2>&1 && exit 0
    exec python3 "$MAIN"
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
gdbus call --session --dest "$APP" --object-path "$OBJ" \
    --method org.freedesktop.Application.Open "$(join "${uris[@]}")" "{}" >/dev/null 2>&1 && exit 0
exec python3 "$MAIN" "$@"
