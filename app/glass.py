# Glas-Modus für Pixelblick, wallist und Notes: Fenster werden durchscheinend wie die Konsole
# (kitty: background_opacity 0.6), Hyprland zeichnet den Blur dahinter. Die Einstellung gilt für alle
# Apps gemeinsam und wird live übernommen, auch in Fenstern, die schon offen sind.
import json, os, subprocess

from gi.repository import Gio, GLib, Gtk

SETTINGS = os.path.expanduser("~/.config/rice-apps/settings.json")

# Nur Hintergründe werden durchsichtig; Schrift, Rahmen und Akzentfarben bleiben voll deckend.
# :not(...) sorgt dafür, dass Hover-/Aktiv-Zustände ihre volle Akzentfüllung behalten.
GLASS_CSS = """
/* backdrop-filter: GTK (ab 4.24) meldet Hyprland damit die Blur-Fläche. Ohne diese Zeile schaltet
   Hyprland den Blur hinter GTK-Fenstern ab, weil GTK sonst eine leere Blur-Fläche anmeldet. */
window.pb-window.glass { background-color: alpha(@background, 0.6); backdrop-filter: blur(10px); }
.glass .card, .glass .tile, .glass .editor, .glass .canvas-frame, .glass .dropzone.loaded {
    background-color: alpha(@surface_container, 0.42);
}
.glass .dropzone { background-color: alpha(@surface_container, 0.25); }
.glass .tile.add { background-color: alpha(@surface_container, 0.2); }
.glass list.notes > row:not(:selected) { background-color: alpha(@surface_container, 0.42); }
.glass button.pb:not(:hover):not(:checked):not(:focus-visible):not(:disabled),
.glass entry.pb, .glass .chip { background-color: alpha(@background, 0.3); }
.glass button.pb:disabled { background-color: transparent; }
.glass .preview { background-color: alpha(@background, 0.82); }
.glass .badge:not(.accent) { background-color: alpha(@background, 0.6); }
button.glass-toggle { padding: 4px 11px; }
"""


def tiling_desktop():
    """Kachel-Fenstermanager (Hyprland, Sway, …) zeichnen Rahmen selbst. Überall sonst (GNOME, KDE, Windows)
    braucht das Fenster einen eigenen Rahmen zum Verschieben und Größe-Ändern."""
    env = os.environ
    if os.name == "nt":
        return False
    if env.get("HYPRLAND_INSTANCE_SIGNATURE") or env.get("SWAYSOCK") or env.get("NIRI_SOCKET"):
        return True
    desk = env.get("XDG_CURRENT_DESKTOP", "").lower()
    return any(d in desk for d in ("hyprland", "sway", "river", "niri", "wayfire", "i3", "bspwm", "qtile"))


def fit_size(w, h):
    """Startgröße auf den Bildschirm begrenzen (kleine Laptops, z. B. 1366×768: sonst ragt das Fenster über den
    Rand). Kachel-WMs bestimmen die Größe ohnehin selbst."""
    try:
        from gi.repository import Gdk
        mons = Gdk.Display.get_default().get_monitors()
        if mons.get_n_items():
            g = mons.get_item(0).get_geometry()
            w, h = min(w, int(g.width * 0.92)), min(h, int(g.height * 0.86))
    except Exception:   # noqa: BLE001 – ohne Bildschirm-Info einfach die Wunschgröße
        pass
    return w, h


def frame_window(win):
    """Kachel-WM: rahmenlos (Rahmen und Rundung kommen vom Compositor). Sonst: GTK-Rahmen mit Schatten und
    Größenänderung, aber ohne zusätzliche Titelleiste – verschoben wird über die eigene Kopfzeile."""
    if tiling_desktop():
        win.set_decorated(False)
    else:
        win.set_decorated(True)
        bar = Gtk.Box(visible=False)      # unsichtbare Titelleiste: nur Rahmen, keine Standard-Leiste
        win.set_titlebar(bar)
        win.add_css_class("framed")


def _hypr(*args):
    out = subprocess.run(["hyprctl", *args], capture_output=True, text=True, timeout=2).stdout
    return json.loads(out) if "-j" in args else out


def bring_here(win):
    """Hyprland: ist das Fenster schon auf einem anderen Arbeitsbereich/Bildschirm offen, wird es vor dem
    Zeigen auf den aktuellen geholt (statt dorthin zu springen). Danach win.present() aufrufen."""
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") or not win.get_visible():
        return                           # ein frisch gezeigtes Fenster landet ohnehin auf dem aktuellen
    try:
        ws = _hypr("activeworkspace", "-j")
        mine = [c for c in _hypr("clients", "-j") if c.get("pid") == os.getpid() and c.get("mapped", True)]
        title = win.get_title() or ""
        hit = [c for c in mine if c.get("title") == title] or (mine if len(mine) == 1 else [])
        for c in hit[:1]:
            if c["workspace"]["id"] != ws["id"]:
                _hypr("dispatch", f'hl.dsp.window.move({{ workspace = {ws["id"]}, '
                                  f'window = "address:{c["address"]}", follow = false }})')
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        pass


def load():
    try:
        with open(SETTINGS) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def is_on():
    return bool(load().get("glass", False))


def set_on(on):
    data = load()
    data["glass"] = bool(on)
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    tmp = SETTINGS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, SETTINGS)


class GlassToggle(Gtk.ToggleButton):
    """Schalter für die Kopfzeile. Hält das Fenster und die Datei synchron."""

    def __init__(self, window):
        super().__init__(label="󰃝", tooltip_text="Glas-Effekt an/aus (durchscheinend wie die Konsole)",
                         valign=Gtk.Align.CENTER)
        self.add_css_class("pb")
        self.add_css_class("glass-toggle")
        self.window = window
        self._sync = False
        self.apply(is_on())
        self.connect("toggled", self.on_toggled)
        os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
        self.monitor = Gio.File.new_for_path(SETTINGS).monitor_file(Gio.FileMonitorFlags.NONE, None)
        self.monitor.connect("changed", lambda *_: GLib.timeout_add(80, self.reload))

    def apply(self, on):
        self._sync = True
        self.set_active(on)
        self._sync = False
        (self.window.add_css_class if on else self.window.remove_css_class)("glass")

    def on_toggled(self, *_):
        if not self._sync:
            set_on(self.get_active())
            self.apply(self.get_active())

    def reload(self):
        on = is_on()
        if on != self.window.has_css_class("glass"):
            self.apply(on)
        return False
