#!/usr/bin/env bash
# Holt die neueste Scourge-Version aus dem Git-Repo und installiert sie. Deine Einstellungen bleiben unberührt.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
old="$(cat app/VERSION 2>/dev/null || echo '?')"
git pull --ff-only
new="$(cat app/VERSION 2>/dev/null || echo '?')"
./install.sh --update
if [[ "$old" != "$new" ]]; then
    echo "Scourge $old → $new."
else
    echo "Scourge $new ist aktuell."
fi
echo "Falls Scourge gerade offen ist: einmal schließen und neu starten."
