# Glas-Modus für Pixelblick, wallist und Notes: Fenster werden durchscheinend wie die Konsole
# (kitty: background_opacity 0.6), Hyprland zeichnet den Blur dahinter. Die Einstellung gilt für alle
# Apps gemeinsam und wird live übernommen, auch in Fenstern, die schon offen sind.
import json, os, subprocess, sys

from gi.repository import Gio, GLib, Graphene, Gtk

# Windows: Python öffnet Textdateien dort mit cp1252 – JSON mit „☑“, Umlauten in Namen usw. brach beim Speichern ab
# (UnicodeEncodeError). Alle Apps importieren glass früh, also hier einmal UTF-8 als Standard setzen.
if os.name == "nt":
    import builtins
    import io
    _open = builtins.open

    def _utf8_open(file, mode="r", buffering=-1, encoding=None, errors=None, *a, **k):
        if encoding is None and "b" not in mode:
            encoding = "utf-8"
            if errors is None and not any(c in mode for c in "wax+"):
                errors = "replace"
        return _open(file, mode, buffering, encoding, errors, *a, **k)
    builtins.open = io.open = _utf8_open

SETTINGS = os.path.expanduser("~/.config/rice-apps/settings.json")

# Windows: Glas an (alpha-Hintergrund, wie vor der Unterlage). Eingefroren ist dort nur die Unterlage (BlurMarker:
# push_copy/append_paste, GTK >= 4.22) – die bleibt unter Windows aus, siehe SUPPORTED.
AVAILABLE = True

# Nur Hintergründe werden durchsichtig; Schrift, Rahmen und Akzentfarben bleiben voll deckend.
# :not(...) sorgt dafür, dass Hover-/Aktiv-Zustände ihre volle Akzentfüllung behalten.
GLASS_CSS = """
/* backdrop-filter: GTK (ab 4.24) meldet Hyprland damit die Blur-Fläche. Ohne diese Zeile schaltet
   Hyprland den Blur hinter GTK-Fenstern ab, weil GTK sonst eine leere Blur-Fläche anmeldet.
   Aber: GTK rechnet den Blur dann zusätzlich selbst, jedes Bild über das ganze Fenster (unsichtbar, den
   sichtbaren Blur zeichnet Hyprland) – Scrollen ruckelt, große Fenster nur 30 Bilder/s. Deshalb meldet eine
   Unterlage hinter dem Inhalt (BlurMarker, siehe unten) die Fläche ohne diese Rechnung und zeichnet die Tönung
   (Farbe über die CSS-Eigenschaft color); das Fenster selbst bleibt durchsichtig.
   Fenster ohne Unterlage (Klasse glass-ul fehlt, z. B. GTK < 4.22) behalten den alten Weg. */
window.pb-window.glass:not(.glass-ul) { background-color: alpha(@background, 0.6); backdrop-filter: blur(10px); }
window.glass.glass-ul { background-color: transparent; backdrop-filter: none; }
window.glass .glass-under { color: alpha(@background, 0.6); }
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
    elif os.name == "nt":
        # Windows: echte Titelleiste (verschieben, Größe ändern, minimieren/maximieren/schließen). Mit der
        # unsichtbaren Leiste unten ließ sich das Fenster dort nicht anfassen.
        win.set_decorated(True)
        win.set_titlebar(None)
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
    return AVAILABLE and bool(load().get("glass", False))


def set_on(on):
    data = load()
    data["glass"] = bool(on)
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    tmp = SETTINGS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, SETTINGS)


# GTK meldet eine Blur-Fläche, wenn im Zeichenbaum ein Blur (Radius >= 20) über einem „Einfügen“ des Hintergrunds
# liegt (gsk_render_node_compute_background_blur). Die Fläche wird über die Verschiebung/Skalierung ermittelt, der
# Schwellwert aber am Radius vor der Skalierung geprüft. Also: alles auf 1/1000 verkleinert zeichnen – gemeldet wird
# die volle Fläche, gerechnet wird ein Blur mit Radius 0,024 px (praktisch nichts). Gemessen mit Scourge, halbes
# Fenster, Glas an: GPU beim Scrollen 47 % → 28 % (Glas aus: 22 %); großes Fenster 30 → 60 Bilder/s.
_TINY = 0.001
SUPPORTED = hasattr(Gtk.Snapshot, "push_copy") and hasattr(Gtk.Snapshot, "append_paste")   # GTK >= 4.22
# Der Software-Renderer (GSK_RENDERER=cairo, z. B. ricetui/musicd) kann die riesige Fläche nicht anlegen
# („drawing failure … too big“) – dann bleibt das ganze Fenster leer. Dort gilt weiter backdrop-filter aus GLASS_CSS.
if os.environ.get("GSK_RENDERER", "").strip().lower() == "cairo" or os.name == "nt" or sys.platform.startswith(("win", "cygwin", "msys")):
    SUPPORTED = False


class BlurMarker(Gtk.Widget):
    """Leere Unterlage hinter dem Fensterinhalt: meldet im Glas-Modus die Blur-Fläche und zeichnet die Tönung."""

    def do_snapshot(self, snap):
        root = self.get_root()
        if root is None or not root.has_css_class("glass"):
            return
        w, h = self.get_width(), self.get_height()
        snap.save()
        snap.scale(_TINY, _TINY)
        snap.push_copy()
        snap.push_blur(24)
        snap.append_paste(Graphene.Rect().init(0, 0, w / _TINY, h / _TINY), 0)
        snap.pop()
        snap.pop()
        snap.restore()
        snap.append_color(self.get_color(), Graphene.Rect().init(0, 0, w, h))   # Tönung erst danach (sonst doppelt)


def underlay(win):
    """Legt die Unterlage (BlurMarker) hinter den Fensterinhalt, siehe GLASS_CSS. Wird der Inhalt später
    ausgetauscht (set_child), kommt er automatisch wieder auf eine Unterlage. Mehrfach aufrufbar."""
    if not AVAILABLE or not SUPPORTED or getattr(win, "_glass_ul", False):
        return
    win._glass_ul = True
    win._glass_ov = None

    def wrap(*_):
        child = win.get_child()
        if child is None or child is win._glass_ov:
            return
        old = win._glass_ov
        if old is not None:                    # alte Unterlage steckt evtl. noch tief im neuen Inhalt: abschalten
            old.get_child().set_visible(False)
        ov = Gtk.Overlay()
        leaf = BlurMarker(can_target=False)
        leaf.add_css_class("glass-under")
        ov.set_child(leaf)
        win._glass_ov = ov
        win.set_child(ov)                      # löst child vom Fenster, danach in die Ebene darüber
        ov.add_overlay(child)
        ov.set_measure_overlay(child, True)
        win.add_css_class("glass-ul")

    win.connect("notify::child", wrap)
    wrap()


class GlassToggle(Gtk.ToggleButton):
    """Schalter für die Kopfzeile. Hält das Fenster und die Datei synchron."""

    def __init__(self, window):
        super().__init__(label="󰃝", tooltip_text="Glas-Effekt an/aus (durchscheinend wie die Konsole)",
                         valign=Gtk.Align.CENTER)
        self.add_css_class("pb")
        self.add_css_class("glass-toggle")
        self.window = window
        if not AVAILABLE:                      # Windows: unsichtbar und ohne Wirkung
            self.set_visible(False)
            window.remove_css_class("glass")
            return
        underlay(window)
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


# ---------------------------------------------------------------------------------------------------------
# Animierter Textcursor wie in kitty (cursor_trail): Ein Balken, dessen vier Ecken dem Ziel verschieden schnell
# folgen – wer viel tippt oder löscht, sieht einen dicken Schmierstreifen, der Stillstand läuft zum Strich zusammen.
# Gilt für JEDES Textfeld jedes Fensters, das glass importiert (Gtk.Text/Entry/TextView). Der GTK-eigene Cursor
# wird nur im gerade bearbeiteten Feld unsichtbar gemacht (Klasse ist-caret), alles andere bleibt unberührt.
# Abschalten: Umgebungsvariable IST_NO_CARET=1.
import math, time, weakref

from gi.repository import Gdk

CARET_WIDTH = 2.0          # Breite des Balkens in Pixeln
CARET_FAST = 0.03         # Zeitkonstante (s) der vorauseilenden Ecken
CARET_SLOW = 0.10          # … der nachziehenden Ecken (je größer der Abstand, desto dicker der Streifen)
CARET_IDLE = 0.6           # Ruhe (s) bis zum Blinken
CARET_BLINK = 0.53         # Halbperiode des Blinkens (s)
CARET_CSS = """
.ist-caret, .ist-caret text { caret-color: transparent; -gtk-secondary-caret-color: transparent; }
"""


def _caret_widget(w):
    """Ist w ein bearbeitbares Textfeld, in dem ein Cursor sichtbar ist?"""
    if isinstance(w, Gtk.Text):
        return w.get_editable()
    if isinstance(w, Gtk.TextView):
        return w.get_editable() and w.get_cursor_visible()
    return callable(getattr(w, "ist_caret", None))      # eigene Zeichenfläche mit Cursor: siehe caret_active()


def caret_active(w):
    """Für selbst gezeichnete Textfelder: True, wenn die Cursor-Ebene den Cursor übernimmt (dann nicht selbst zeichnen)."""
    root = w.get_root()
    return root is not None and getattr(root, "_ist_caret", None) is not None and not os.environ.get("IST_NO_CARET")


def _caret_rect(w):
    """(Widget, x, y, h, Farbe|None) des Cursors in Koordinaten des Widgets oder None (z. B. weggescrollt).
    Eigene Zeichenflächen liefern das über w.ist_caret() -> (Widget, x, y, h, (r, g, b)) | None."""
    try:
        hook = getattr(w, "ist_caret", None)
        if callable(hook):
            return hook()
        if isinstance(w, Gtk.Text):
            r = w.compute_cursor_extents(w.get_position())[0]
            x, y, h = r.get_x(), r.get_y(), r.get_height()
        else:
            buf = w.get_buffer()
            r = w.get_iter_location(buf.get_iter_at_mark(buf.get_insert()))
            x, y = w.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, r.x, r.y)
            h = r.height
    except (TypeError, ValueError, AttributeError):
        return None
    if h <= 0 or y + h < 0 or y > w.get_height() or x < -2 or x > w.get_width() + 2:
        return None
    return w, x, y, h, None


class CaretTrail(Gtk.DrawingArea):
    """Liegt als durchklickbare Ebene über dem Fensterinhalt und zeichnet den Cursor des fokussierten Textfelds."""

    def __init__(self, win):
        super().__init__(can_target=False, can_focus=False, hexpand=True, vexpand=True)
        self._win = weakref.ref(win)
        self._tgt = None                       # (x, y, h) in eigenen Koordinaten
        self._cur = None                       # vier Ecken [[x, y] …] (oben links, oben rechts, unten rechts, unten links)
        self._dir = (0.0, 0.0)
        self._widget = None                    # weakref auf das Feld, dessen Cursor wir zeichnen
        self._moved = time.monotonic()
        self._on = True
        self._ticking = False
        self._last = 0
        self._poll_id = 0
        self._color = (0.9, 0.9, 0.9, 1.0)
        self.set_draw_func(self._draw)
        self.connect("map", self._start)
        self.connect("unmap", self._stop)

    # --- Abfrage des fokussierten Felds ------------------------------------------------------------------
    def _start(self, *_):
        if not self._poll_id:
            self._poll_id = GLib.timeout_add(40, self._poll)

    def _stop(self, *_):
        if self._poll_id:
            GLib.source_remove(self._poll_id)
            self._poll_id = 0
        self._release()

    def _watch(self, w):
        """Cursorbewegung des Felds sofort melden (statt bis zu 40 ms auf den nächsten Takt zu warten)."""
        self._unwatch()
        src = w if isinstance(w, Gtk.Text) else (w.get_buffer() if isinstance(w, Gtk.TextView) else None)
        if src is None:
            return
        kick = lambda *_: GLib.idle_add(self._kick)
        self._watched = (src, [src.connect("notify::cursor-position", kick), src.connect("changed", kick)])

    def _unwatch(self):
        cur = getattr(self, "_watched", None)
        self._watched = None
        if cur:
            for i in cur[1]:
                try:
                    cur[0].disconnect(i)
                except TypeError:
                    pass

    def _kick(self):
        if self._poll_id:
            self._poll()
        return False

    def _release(self):
        self._unwatch()
        w = self._widget() if self._widget else None
        if w is not None:
            w.remove_css_class("ist-caret")
        self._widget = None
        if self._tgt is not None:
            self._tgt = self._cur = None
            self.queue_draw()

    def _poll(self):
        win = self._win()
        if win is None:
            self._poll_id = 0
            return False
        w = win.get_focus() if win.is_active() else None
        if w is None or not _caret_widget(w) or not w.get_mapped() or w.get_native() is not win.get_native():
            if self._widget is not None:
                self._release()
            return True
        old = self._widget() if self._widget else None
        if old is not w:
            if old is not None:
                old.remove_css_class("ist-caret")
            w.add_css_class("ist-caret")
            self._widget = weakref.ref(w)
            self._watch(w)
            self._cur = self._tgt = None        # anderes Feld: kein Streifen quer über das Fenster, sondern neu erscheinen
        r = _caret_rect(w)
        host = r[0] if r else w
        p = host.compute_point(self, Graphene.Point().init(r[1], r[2])) if r else None
        sw = host.get_ancestor(Gtk.ScrolledWindow) if p and p[0] else None
        if sw is not None:                      # im Scrollbereich: Cursor außerhalb des Sichtfensters bleibt unsichtbar
            b = sw.compute_bounds(self)
            if b[0] and not (b[1].get_y() - 1 <= p[1].y and p[1].y + r[3] <= b[1].get_y() + b[1].get_height() + 1):
                p = None
        if p is None or not p[0]:
            if self._tgt is not None:
                self._tgt = self._cur = None
                self.queue_draw()
            return True
        if r[4]:
            self._color = (*r[4], 1.0)
        else:
            rgba = w.get_color()
            self._color = (rgba.red, rgba.green, rgba.blue, rgba.alpha)
        tgt = (p[1].x, p[1].y, r[3])
        if tgt != self._tgt:
            self._retarget(tgt)
        else:
            self._blink()
        return True

    def _retarget(self, tgt):
        now = time.monotonic()
        old, self._tgt = self._tgt, tgt
        self._moved, self._on = now, True
        x, y, h = tgt
        corners = [[x, y], [x + CARET_WIDTH, y], [x + CARET_WIDTH, y + h], [x, y + h]]
        if self._cur is None or old is None:
            self._cur = corners                 # erstes Erscheinen: kein Weg
            self.queue_draw()
            return
        dx = (x + CARET_WIDTH / 2) - sum(c[0] for c in self._cur) / 4
        dy = (y + h / 2) - sum(c[1] for c in self._cur) / 4
        n = math.hypot(dx, dy)
        if n < 1.5:
            self._cur = corners
            self.queue_draw()
            return
        self._dir = (dx / n, dy / n)
        self._ensure_tick()

    def _blink(self):
        if self._tgt is None or self._ticking:
            return
        try:
            blink = Gtk.Settings.get_default().props.gtk_cursor_blink
        except AttributeError:
            blink = True
        idle = time.monotonic() - self._moved - CARET_IDLE
        on = True if (not blink or idle < 0) else int(idle / CARET_BLINK) % 2 == 0
        if on != self._on:
            self._on = on
            self.queue_draw()

    # --- Animation ---------------------------------------------------------------------------------------
    def _ensure_tick(self):
        if not self._ticking:
            self._ticking = True
            self._last = 0
            self.add_tick_callback(self._tick)

    def _tick(self, _w, clock):
        t = clock.get_frame_time()
        dt = min((t - self._last) / 1e6, 0.1) if self._last else 1 / 60
        self._last = t
        if self._tgt is None or self._cur is None:
            self._ticking = False
            return GLib.SOURCE_REMOVE
        x, y, h = self._tgt
        goal = [(x, y), (x + CARET_WIDTH, y), (x + CARET_WIDTH, y + h), (x, y + h)]
        cx, cy = x + CARET_WIDTH / 2, y + h / 2
        far = 0.0
        for c, g in zip(self._cur, goal):
            # Ecken, die in Bewegungsrichtung vorn liegen, laufen schnell; die hinteren ziehen nach
            lead = (g[0] - cx) * self._dir[0] + (g[1] - cy) * self._dir[1] >= 0
            k = 1 - math.exp(-dt / (CARET_FAST if lead else CARET_SLOW))
            c[0] += (g[0] - c[0]) * k
            c[1] += (g[1] - c[1]) * k
            far = max(far, abs(g[0] - c[0]), abs(g[1] - c[1]))
        self._on = True
        self._moved = time.monotonic()
        self.queue_draw()
        if far < 0.25:
            self._cur = [list(g) for g in goal]
            self._ticking = False
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _draw(self, _a, cr, _w, _h):
        if self._cur is None or not self._on:
            return
        r, g, b, a = self._color
        cr.set_source_rgba(r, g, b, a)
        cr.move_to(*self._cur[0])
        for c in self._cur[1:]:
            cr.line_to(*c)
        cr.close_path()
        cr.fill()


_caret_css_done = False


def _caret_attach(win):
    """Hängt die Cursor-Ebene über den Inhalt von win (einmal je Fenster)."""
    global _caret_css_done
    if getattr(win, "_ist_caret", None) is not None:
        return
    if not _caret_css_done:
        _caret_css_done = True
        prov = Gtk.CssProvider()
        prov.load_from_data(CARET_CSS.encode())
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), prov,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER + 6)
    trail = CaretTrail(win)
    win._ist_caret = trail
    wref = weakref.ref(win)

    def host(*_):
        win = wref()
        if win is None:
            return False
        child = win.get_child()
        if child is None or trail.get_root() is win:
            return False                       # noch kein Inhalt, oder die Ebene steckt schon im Fenster
        if trail.get_parent() is not None:
            trail.get_parent().remove_overlay(trail)
        if isinstance(child, Gtk.Overlay):
            child.add_overlay(trail)
        else:
            win.set_child(None)
            ov = Gtk.Overlay()
            ov.set_child(child)
            ov.add_overlay(trail)
            win.set_child(ov)
        return False

    win.connect("notify::child", lambda *_: GLib.idle_add(host))
    host()


def _caret_scan():
    if Gdk.Display.get_default() is None:
        return True
    for win in Gtk.Window.list_toplevels():
        try:
            _caret_attach(win)
        except Exception:      # noqa: BLE001 – der Cursor ist Zierde und darf nie ein Fenster verhindern
            pass
    return True


def enable_caret():
    """Einmal je Prozess: alle (auch später geöffnete) Fenster bekommen den animierten Cursor."""
    if os.environ.get("IST_NO_CARET") or getattr(enable_caret, "done", False) or Gtk.get_major_version() < 4:
        return
    enable_caret.done = True
    GLib.timeout_add(400, _caret_scan)


enable_caret()
