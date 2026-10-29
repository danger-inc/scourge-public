# Akku-Warnung: meldet sich einmal, wenn der Ladestand unter 20 % fällt (und noch einmal unter 10 %).
# Läuft im Hintergrund von Scourge (auch im --daemon-Modus), prüft einmal pro Minute /sys/class/power_supply.
import glob, os, shutil, subprocess

from gi.repository import Gio, GLib

import settings_menu

LEVELS = (20, 10)               # Schwellen in Prozent
REARM = 25                      # ab hier (oder am Netzteil) wird wieder gewarnt
_state = {"warned": set()}


def read():
    """(Prozent, entlädt) des ersten Akkus – None ohne Akku."""
    for d in sorted(glob.glob("/sys/class/power_supply/BAT*")):
        try:
            cap = int(open(os.path.join(d, "capacity")).read())
            status = open(os.path.join(d, "status")).read().strip()
        except (OSError, ValueError):
            continue
        return cap, status == "Discharging"
    return None


def available():
    return read() is not None


def enabled():
    return bool(settings_menu.get("battery_warn", True))


def warn(app, pct):
    title = f"Akku bei {pct} %"
    body = "Bitte das Netzteil anschließen." if pct > 10 else "Gleich ist Schluss – jetzt das Netzteil anschließen!"
    if shutil.which("notify-send"):
        subprocess.Popen(["notify-send", "-a", "Scourge", "-u", "critical" if pct <= 10 else "normal", "-t", "4000",
                          "-i", "battery-caution",
                          "-h", "string:x-canonical-private-synchronous:scourge-battery", title, body],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    n = Gio.Notification.new(title)
    n.set_body(body)
    n.set_priority(Gio.NotificationPriority.URGENT)
    app.send_notification("battery", n)


def check(app):
    r = read()
    if r is None or not enabled():
        return True
    pct, discharging = r
    if not discharging or pct >= REARM:
        _state["warned"].clear()
        return True
    for lv in LEVELS:                                   # engste Schwelle zuerst melden
        if pct < lv and lv not in _state["warned"]:
            _state["warned"].update(l for l in LEVELS if l >= lv)
            warn(app, pct)
            break
    return True


def start(app):
    """Einmal beim Start aufrufen; ohne Akku (Desktop-PC) passiert nichts."""
    if not available():
        return
    GLib.timeout_add_seconds(60, check, app)
    GLib.timeout_add_seconds(5, lambda: (check(app), False)[1])     # Start mit schwachem Akku
