#!/usr/bin/env bash
# Entfernt Scourge wieder (installierte Systempakete bleiben).
set -euo pipefail
rm -rf "$HOME/.local/share/scourge"
rm -f  "$HOME/.local/bin/scourge" "$HOME/.local/bin/scourge-pdf"
rm -f  "$HOME/.local/share/applications/scourge.desktop" "$HOME/.local/share/applications/scourge-pdf.desktop"
rm -f  "$HOME/.local/share/icons/hicolor/scalable/apps/scourge.svg" \
       "$HOME/.local/share/icons/hicolor/scalable/apps/scourge-symbolic.svg"
command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
echo "Scourge entfernt. (Die mitgelieferte Schriftart und die Systempakete wurden gelassen.)"
