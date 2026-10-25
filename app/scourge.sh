#!/usr/bin/env bash
[ "${1:-}" = --quit ] || { [ -x "$HOME/.local/bin/linguist" ] && "$HOME/.local/bin/linguist" --daemon >/dev/null 2>&1 & }   # linguist (Übersetzer) läuft immer mit
# Starter für Scourge. Läuft Scourge schon (auch unsichtbar im Hintergrund), geht der Aufruf direkt per D-Bus
# hin – ohne Python-/GTK-Start, das Fenster ist sofort da. Sonst wird Scourge gestartet.
#   scourge [datei …]   scourge --daemon   scourge --quit
APP=de.invincible.Scourge
OBJ=/de/invincible/Scourge
MAIN="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/scourge.py"

running() { gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
    --method org.freedesktop.DBus.NameHasOwner "$APP" 2>/dev/null | grep -q true; }

case "${1:-}" in
    --osint-file)         # Datei im vollen Scourge mit den OSINT-Werkzeugen prüfen (Knopf in scourge-pdf)
        f="$(realpath -m -- "${2:?datei}")"
        if running; then
            gdbus call --session --timeout 3 --dest "$APP" --object-path "$OBJ" --method org.gtk.Actions.Activate \
                osint "[<('file', '$f')>]" '{}' >/dev/null 2>&1 && exit 0
        fi
        exec python3 "$MAIN" --osint-file "$f" ;;
    --daemon)
        running && exit 0
        setsid -f python3 "$MAIN" --daemon >"${XDG_RUNTIME_DIR:-/tmp}/scourge.log" 2>&1 </dev/null
        exit 0 ;;
    --quit)
        running && gdbus call --session --dest "$APP" --object-path "$OBJ" \
            --method org.gtk.Actions.Activate quit '[]' '{}' >/dev/null 2>&1
        exit 0 ;;
esac

# PDFs gehören in den schlanken Editor (scourge-pdf): sofort dorthin, nur der Rest läuft hier weiter
case "${1:-}" in -*) ;; *)
    pdfs=(); rest=()
    for a in "$@"; do case "${a,,}" in *.pdf) pdfs+=("$a") ;; *) rest+=("$a") ;; esac; done
    if [ ${#pdfs[@]} -gt 0 ]; then
        "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/scourge-pdf.sh" "${pdfs[@]}"
        [ ${#rest[@]} -eq 0 ] && exit 0
        set -- "${rest[@]}"
    fi ;;
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
