# Hintergrundbild setzen – je nach Arbeitsumgebung (Hyprland/Sway: awww, swww, swaybg; GNOME, KDE, Cinnamon, MATE, Xfce, X11: feh).
import os, shutil, subprocess

import gi
from gi.repository import Gio


def setter(path):
    """Befehl, der auf dieser Arbeitsumgebung das Hintergrundbild setzt (oder None)."""
    desk = (os.environ.get("XDG_CURRENT_DESKTOP", "") + ":" + os.environ.get("DESKTOP_SESSION", "")).lower()
    uri = Gio.File.new_for_path(path).get_uri()
    if shutil.which("awww"):
        return ["awww", "img", path]
    if shutil.which("swww"):
        return ["swww", "img", path]
    if ("kde" in desk or "plasma" in desk) and shutil.which("plasma-apply-wallpaperimage"):
        return ["plasma-apply-wallpaperimage", path]
    if any(d in desk for d in ("gnome", "unity", "budgie", "pantheon", "ubuntu", "pop")) and shutil.which("gsettings"):
        return ["sh", "-c", 'gsettings set org.gnome.desktop.background picture-uri "$1"; '
                'gsettings set org.gnome.desktop.background picture-uri-dark "$1" 2>/dev/null; '
                'gsettings set org.gnome.desktop.background picture-options zoom', "sh", uri]
    if "cinnamon" in desk and shutil.which("gsettings"):
        return ["gsettings", "set", "org.cinnamon.desktop.background", "picture-uri", uri]
    if "mate" in desk and shutil.which("gsettings"):
        return ["gsettings", "set", "org.mate.background", "picture-filename", path]
    if "xfce" in desk and shutil.which("xfconf-query"):
        return ["sh", "-c", 'for p in $(xfconf-query -c xfce4-desktop -l | grep last-image); do '
                'xfconf-query -c xfce4-desktop -p "$p" -s "$1"; done', "sh", path]
    if shutil.which("swaybg") and os.environ.get("WAYLAND_DISPLAY"):
        return ["sh", "-c", 'pkill -x swaybg; setsid -f swaybg -m fill -i "$1" >/dev/null 2>&1', "sh", path]
    if shutil.which("feh") and os.environ.get("DISPLAY"):
        return ["feh", "--bg-fill", path]
    return None


def available():
    return setter("/tmp/x.png") is not None


def apply(path):
    """(ok, Meldung)."""
    cmd = setter(path)
    if not cmd:
        return False, "Diese Arbeitsumgebung kann Scourge nicht direkt setzen – das Bild liegt im Ordner."
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"Setzen fehlgeschlagen: {e}"
    if r.returncode != 0:
        return False, "Setzen fehlgeschlagen: " + ((r.stderr or r.stdout).strip().splitlines() or [f"Code {r.returncode}"])[-1][:100]
    return True, "Hintergrundbild gesetzt"
