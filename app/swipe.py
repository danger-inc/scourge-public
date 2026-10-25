# 2-Finger-Zurück im -ist-Design („Seitenstapel“): Beim Wischen schiebt sich die vorige Seite als Rahmen
# vom Rand über die aktuelle, die ein Stück zur Seite rückt und dunkler wird. Rahmen gestrichelt, voll =
# durchgezogen + Titel-Chip in @primary. Loslassen klappt die vorige Seite über das ganze Fenster auf.
# Die Vorschau ist ein echtes Bild der vorigen Seite: SwipeBack merkt sich bei jedem Bild, das GTK zeichnet,
# den Render-Knoten der aktuellen Seite (where() → Schlüssel) – beim Seitenwechsel bleibt der letzte stehen.
# Kein Extra-Rendern, immer frisch. Maustaste 8/9 = zurück/vor.
# Gemeinsam für scourge, wallist, musicalist (rice-apps/swipe.py); Kopien: notist app/swipe.py, filist app/swipe.py.
import inspect, math, weakref
from collections import OrderedDict

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Graphene, Gtk, Pango, PangoCairo

CSS = """
label.swipe-acc { color: @primary; }
label.swipe-on { color: @on_primary; }
label.swipe-bg { color: @background; }

/* Rückmeldung für Umschalt-Knöpfe (Ton behalten, Wiederholen …): „an“ muss auch unter dem Mauszeiger sichtbar
   sein. Sonst sieht „an + Hover“ genauso aus wie „aus + Hover“, und man erkennt den Klick erst nach dem
   Wegfahren. Gedrückt (:active) färbt sofort. Gilt für alle Apps, die swipe.py laden (alle der Suite). */
button.toggle:checked:hover, button.pb:checked:hover, button.tbtn:checked:hover {
    background-image: none; background-color: alpha(@primary, 0.42); color: @foreground; border-color: @primary;
    box-shadow: inset 0 0 0 1px @primary; }
button.toggle:active, button.pb:active, button.tbtn:active {
    background-image: none; background-color: alpha(@primary, 0.62); color: @foreground; border-color: @primary; }
"""

DIST = 150            # so weit (px) wischen, bis es auslöst
GHOST = 0.34          # Anteil der Breite, den die vorige Seite beim vollen Wischen zeigt
SHIFT = 30            # so weit rückt die aktuelle Seite beiseite
KEEP = 16             # so viele Seitenbilder merken
DUR_COMMIT = 300_000  # µs
DUR_CANCEL = 180_000


_css_done = False


def _install_css():
    """Farben für den Chip selbst laden – nicht darauf verlassen, dass jede App swipe.CSS einbindet
    (musicalist tat es nicht: Chip hellgrau auf hell, Name unlesbar)."""
    global _css_done
    if _css_done:
        return
    disp = Gdk.Display.get_default()
    if disp is None:
        return
    prov = Gtk.CssProvider()
    prov.load_from_string(CSS)
    Gtk.StyleContext.add_provider_for_display(disp, prov, Gtk.STYLE_PROVIDER_PRIORITY_USER + 5)
    _css_done = True


def _rect(x, y, w, h):
    return Graphene.Rect().init(x, y, max(0.0, w), max(0.0, h))


def _weak(fn):
    """Gebundene Methoden nur schwach halten – sonst Kreis über C (Widget → Controller → Besitzer) und ein
    geschlossener Tab/Pane bliebe für immer im Speicher."""
    if fn is None:
        return None
    if inspect.ismethod(fn):
        wm = weakref.WeakMethod(fn)

        def call(*a):
            m = wm()
            return m(*a) if m is not None else None
        return call
    return fn


def _ease(t):
    return 1 - (1 - t) ** 3


class SwipeBin(Gtk.Widget):
    """Hält den Inhalt und zeichnet ihn – im Ruhezustand unverändert, beim Wischen verschoben/geclippt."""
    __gtype_name__ = "IstSwipeBin"

    def __init__(self, owner, child):
        super().__init__(hexpand=True, vexpand=True)
        self.set_layout_manager(Gtk.BinLayout())
        self._owner = owner
        self.child = child
        child.set_parent(self)
        self.helpers = {}
        for k in ("acc", "on", "bg"):          # nur für die Farben aus dem Theme (werden nie gezeichnet)
            lb = Gtk.Label(can_target=False, can_focus=False, opacity=0)
            lb.add_css_class("swipe-" + k)
            lb.set_parent(self)
            self.helpers[k] = lb

    def do_dispose(self):
        ch, hp = getattr(self, "child", None), getattr(self, "helpers", None) or {}
        for w in [ch] + list(hp.values()):
            if w is not None and w.get_parent() is self:
                w.unparent()
        self.child, self.helpers = None, {}
        Gtk.Widget.do_dispose(self)

    def color(self, k, alpha=None):
        lb = self.helpers.get(k)
        c = lb.get_color() if lb is not None else Gdk.RGBA(red=1, green=0.7, blue=0.85, alpha=1)
        if alpha is not None:
            c = c.copy()
            c.alpha = alpha
        return c

    def do_snapshot(self, snap):
        o = self._owner
        if self.child is None or o is None:
            return
        inner = Gtk.Snapshot()
        self.snapshot_child(self.child, inner)
        node = inner.to_node()
        o._frame(node)
        st = o.st
        if st is None:
            if node is not None:
                snap.append_node(node)
            return
        o._draw(self, snap, node, st)


class SwipeBack:
    """content in SwipeBin packen (→ .widget einsetzen).
    can_back()/back(): ob/wie es zurück geht. where() → (schlüssel, titel) der aktuellen Seite,
    target() → (schlüssel, titel) der Seite, auf der zurück landet (oder None).
    Optional vor: can_fwd()/fwd()/fwd_target(). mouse=False: Maustasten 8/9 macht die App selbst (→ trigger). blocked(widget) → True, wenn das Widget unter dem Zeiger
    waagerechtes Wischen selbst braucht."""

    def __init__(self, content, can_back, back, blocked=None, where=None, target=None,
                 can_fwd=None, fwd=None, fwd_target=None, mouse=True, goto=None):
        _install_css()
        self.can_back, self.back, self.blocked = _weak(can_back), _weak(back), _weak(blocked)
        self.where, self.target = _weak(where), _weak(target)
        self.can_fwd, self.fwd, self.fwd_target = _weak(can_fwd), _weak(fwd), _weak(fwd_target)
        # Automatischer Vorwärts-Verlauf: Wer nur goto(schlüssel) -> bool liefert (Seite per where()-Schlüssel
        # öffnen), bekommt „vor“ geschenkt: jede Zurück-Geste merkt die verlassene Seite.
        self.goto = _weak(goto)
        self.auto_fwd = goto is not None and fwd is None
        self._fstack = []            # [(schlüssel, titel)] verlassene Seiten, jüngste zuletzt
        self._quiet = 0.0            # bis dahin zählen Seitenwechsel als Folge unserer Geste
        self.bin = SwipeBin(self, content)
        self.bin.set_overflow(Gtk.Overflow.HIDDEN)
        # .widget bleibt ein Overlay (Apps hängen eigene Leisten daran, z. B. notist „zuletzt“) –
        # die liegen dann über dem Wisch-Bild und rutschen nicht mit.
        self.widget = Gtk.Overlay(child=self.bin, hexpand=True, vexpand=True)
        self.snaps = OrderedDict()   # schlüssel → render-knoten (zuletzt gezeichnetes Bild der Seite)
        self._last = (None, None)    # (schlüssel, knoten) des zuletzt gezeichneten Bildes
        self.st = None               # Zeichenzustand während Geste/Animation
        self._g = None               # Gestenzustand
        self._anim = None
        self._tid = None
        sc = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        sc.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        me = weakref.ref(self)
        sc.connect("scroll-begin", lambda *_: me() and me()._begin())
        sc.connect("scroll", lambda c, dx, dy: bool(me() and me()._scroll(c, dx, dy)))
        sc.connect("scroll-end", lambda c: me() and me()._end(c))
        self.bin.add_controller(sc)
        for btn, d in (((8, -1), (9, 1)) if mouse else ()):                  # Seitentasten „zurück“/„vor“ der Maus
            mouse = Gtk.GestureClick(button=btn)
            mouse.connect("pressed", lambda *_, d=d: me() and me().trigger(d=d))
            self.bin.add_controller(mouse)

    # ---------- Seitenbilder ----------
    def _key(self):
        if self.where is None:
            return None
        try:
            return self.where()[0]
        except Exception:
            return None

    def _frame(self, node):
        """Wird bei jedem gezeichneten Bild gerufen: Seitenwechsel → letztes Bild der alten Seite merken."""
        key = self._key()
        lk, ln = self._last
        if lk is not None and lk != key and self._fstack and GLib.get_monotonic_time() > self._quiet:
            self._fstack.clear()                    # Nutzer ist woanders hin navigiert → kein „vor“ mehr
        if lk is not None and lk != key and ln is not None:
            self.snaps[lk] = ln
            self.snaps.move_to_end(lk)
            while len(self.snaps) > KEEP:
                self.snaps.popitem(last=False)
        if self.st is not None and self.st.get("mode") == "commit":
            return                                  # beim Aufklappen nicht die alte Seite überschreiben
        if node is not None:
            self._last = (key, node)
        elif lk != key:
            self._last = (key, None)

    def forget(self, key=None):
        """Bild(er) verwerfen, z. B. wenn eine Seite gelöscht wurde."""
        if key is None:
            self.snaps.clear()
        else:
            self.snaps.pop(key, None)

    def _info(self, d):
        fn = self.target if d < 0 else self.fwd_target
        key, title = None, None
        if d > 0 and self.auto_fwd and self._fstack:
            key, title = self._fstack[-1]
            fn = None
        if fn is not None:
            try:
                r = fn()
                if r:
                    key, title = r
            except Exception:
                pass
        node = self.snaps.get(key) if key is not None else None
        return node, (title or ("zurück" if d < 0 else "vor"))

    # ---------- Auslösen ----------
    def _able(self, d):
        try:
            if d < 0:
                return bool(self.can_back())
            if self.auto_fwd:
                return bool(self._fstack)
            return bool(self.can_fwd and self.fwd and self.can_fwd())
        except Exception:
            return False

    def trigger(self, animate=True, d=-1):
        if self._anim or not self._able(d):
            return False
        if self.st is None:
            node, title = self._info(d)
            self.st = {"d": d, "p": 0.0, "node": node, "title": title}
        self._commit(animate)
        return True

    # ---------- Geste ----------
    def _begin(self):
        self._g = {"dx": 0.0, "dy": 0.0, "lock": None}

    def _picked(self, ctrl):
        ev = ctrl.get_current_event()
        native = self.bin.get_native()
        if ev is None or native is None:
            return None
        pos = ev.get_position()
        ok, x, y = pos if len(pos) == 3 else (True, *pos)
        if not ok:
            return None
        tx, ty = native.get_surface_transform()
        root = native if isinstance(native, Gtk.Widget) else self.bin.get_root()
        return root.pick(x - tx, y - ty, Gtk.PickFlags.DEFAULT)

    def _scrollers(self, ctrl):
        """Scrollflächen unter dem Zeiger samt Stand VOR der Geste. Die ersten Millimeter, bevor die Geste als
        waagerecht erkannt ist, scrollen sie noch mit – und liefen nach dem Loslassen mit Schwung weiter: die
        Zielseite (gleiche Scrollfläche, z. B. filist-Ordner) sprang dann nach oben."""
        out = []
        w = self._picked(ctrl)
        while w is not None and w is not self.bin:
            if isinstance(w, Gtk.ScrolledWindow):
                out.append((w, w.get_hadjustment().get_value(), w.get_vadjustment().get_value()))
            w = w.get_parent()
        return out

    def _freeze(self, g):
        for sw, h, v in g.get("sws", ()):
            sw.set_kinetic_scrolling(False)       # Nachlauf aus (und laufenden abbrechen)
            sw.get_hadjustment().set_value(h)
            sw.get_vadjustment().set_value(v)

    @staticmethod
    def _thaw(g):
        sws = [sw for sw, _h, _v in g.get("sws", ())] if g else []
        if sws:
            GLib.timeout_add(200, lambda: [sw.set_kinetic_scrolling(True) for sw in sws] and False)

    def _target_scrolls(self, ctrl, d):
        """Liegt unter dem Zeiger etwas, das selbst in diese Richtung scrollen kann? Dann gehört die Geste ihm."""
        w = self._picked(ctrl)
        while w is not None and w is not self.bin:
            if self.blocked and self.blocked(w):
                return True
            if getattr(w, "swipe_owns_h", False):        # z. B. Fortschrittsbalken: waagrecht = spulen
                return True
            if isinstance(w, Gtk.ScrolledWindow):
                a = w.get_hadjustment()
                if a.get_upper() - a.get_lower() > a.get_page_size() + 1:
                    if d < 0 and a.get_value() > a.get_lower() + 0.5:
                        return True
                    if d > 0 and a.get_value() < a.get_upper() - a.get_page_size() - 0.5:
                        return True
            w = w.get_parent()
        return False

    def _scroll(self, ctrl, dx, dy):
        if ctrl.get_unit() != Gdk.ScrollUnit.SURFACE:      # Mausrad: nichts zu tun
            return False
        g = self._g
        if g is None:                                      # manche Geräte melden kein scroll-begin
            g = self._g = {"dx": 0.0, "dy": 0.0, "lock": None}
        if self._anim:
            return g["lock"] == "h"
        if "sws" not in g:
            g["sws"] = self._scrollers(ctrl)
        g["dx"] += dx
        g["dy"] += dy
        if g["lock"] is None and abs(g["dx"]) + abs(g["dy"]) > 14:
            # zurück = Finger nach rechts = dx < 0 (wie im Browser), vor = dx > 0
            d = -1 if g["dx"] < 0 else 1
            if abs(g["dx"]) > 1.6 * abs(g["dy"]) and self._able(d) and not self._target_scrolls(ctrl, d):
                g["lock"], g["d"] = "h", d
                self._freeze(g)
                node, title = self._info(d)
                self.st = {"d": d, "p": 0.0, "node": node, "title": title}
            else:
                g["lock"] = "v"
        if g["lock"] != "h":
            return False
        p = max(0.0, g["dx"] * g["d"]) / DIST
        self.st["p"] = min(p, 1.0)
        self.st["raw"] = p
        self.bin.queue_draw()
        if self._tid:
            GLib.source_remove(self._tid)
        self._tid = GLib.timeout_add(600, self._timeout)   # falls kein Ende gemeldet wird
        return True

    def _timeout(self):
        self._tid = None
        self._end(None)
        return False

    def _end(self, ctrl):
        if self._tid and ctrl is not None:
            GLib.source_remove(self._tid)
            self._tid = None
        g, self._g = self._g, None
        if g and g.get("lock") == "h":
            self._thaw(g)                       # Nachlauf in jedem Fall wieder einschalten
        if not g or g.get("lock") != "h" or self._anim or self.st is None:
            return False
        if g["dx"] * g["d"] >= DIST and self._able(g["d"]):
            self._commit(True)
        else:
            self._cancel()
        return False

    # ---------- Animationen ----------
    def _run(self, dur, step, done):
        t0 = [None]

        def tick(widget, clock):
            now = clock.get_frame_time()
            if t0[0] is None:
                t0[0] = now
            t = min(1.0, (now - t0[0]) / dur)
            step(t)
            widget.queue_draw()
            if t >= 1:
                self._anim = None
                done()
                widget.queue_draw()
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE
        self._anim = self.bin.add_tick_callback(tick)

    def _cancel(self):
        st = self.st
        p0 = st["p"]

        def step(t):
            st["p"] = p0 * (1 - _ease(t))
            st["ready"] = False

        def done():
            self.st = None
        self._run(DUR_CANCEL, step, done)

    def _commit(self, animate):
        st = self.st
        old = self._last[1]
        d = st["d"]
        if self.auto_fwd:
            self._quiet = GLib.get_monotonic_time() + 2_000_000
            if d < 0:
                try:
                    cur = tuple(self.where())
                except Exception:
                    cur = None
                self.back()
                if cur is not None and (not self._fstack or self._fstack[-1][0] != cur[0]):
                    self._fstack.append(cur)
                    del self._fstack[:-KEEP]
            else:
                key, _t = self._fstack.pop()
                try:
                    if self.goto(key) is False:
                        self._fstack.clear()
                except Exception:
                    self._fstack.clear()
        else:
            (self.back if d < 0 else self.fwd)()
        if not animate or self.bin.get_width() <= 0:
            self.st = None
            self.bin.queue_draw()
            return
        st.update({"mode": "commit", "old": old, "p0": st["p"], "e": 0.0, "t": 0.0})

        def step(t):
            st["t"], st["e"] = t, _ease(t)

        def done():
            self.st = None
        self._run(DUR_COMMIT, step, done)

    # ---------- Zeichnen ----------
    def _draw(self, w, snap, live, st):
        W, H = w.get_width(), w.get_height()
        d = st["d"]
        commit = st.get("mode") == "commit"
        if commit:
            e = st["e"]
            p0 = st["p0"]
            gw = (GHOST * p0 + (1 - GHOST * p0) * e) * W
            shift = SHIFT * p0 + (SHIFT * 2 - SHIFT * p0) * e
            front_alpha = (1 - 0.3 * p0) * (1 - 0.75 * e)
            ready = True
            frame_alpha = 1 - e
        else:
            p = st["p"]
            gw = GHOST * p * W
            shift = SHIFT * p
            front_alpha = 1 - 0.3 * p
            ready = st.get("raw", p) >= 1.0
            frame_alpha = min(1.0, p * 3)
        # Bereich der vorigen Seite (Geist): zurück = links, vor = rechts
        gx = 0 if d < 0 else W - gw
        fx = gw if d < 0 else 0                     # Rest: aktuelle (bzw. beim Aufklappen: alte) Seite
        sx = shift if d < 0 else -shift
        front = st.get("old") if commit else live
        # 1) aktuelle/alte Seite beiseite gerückt
        if front is not None and front_alpha > 0.01:
            snap.push_clip(_rect(fx, 0, W - gw, H))
            snap.push_opacity(front_alpha)
            snap.save()
            snap.translate(Graphene.Point().init(sx, 0))
            snap.append_node(front)
            snap.restore()
            snap.pop()
            snap.pop()
        # 2) vorige Seite im Rahmen
        node = st.get("node")
        snap.push_clip(_rect(gx, 0, gw, H))
        if commit:
            fade = max(0.0, min(1.0, (st["t"] - 0.55) / 0.45))     # zum Ende hin aufs Live-Bild überblenden
            if live is not None and (node is None or fade > 0):
                snap.push_opacity(1.0 if node is None else fade)
                snap.append_node(live)
                snap.pop()
            if node is not None and fade < 1:
                snap.push_opacity(1 - fade)
                snap.append_node(node)
                snap.pop()
        else:
            if node is not None:
                snap.push_opacity(0.72 if ready else 0.5 + 0.15 * st["p"])
                snap.append_node(node)
                snap.pop()
            else:
                snap.append_color(w.color("bg", 0.82), _rect(gx, 0, gw, H))
        snap.pop()
        # 3) Rahmen + Titel-Chip
        if gw > 2 and frame_alpha > 0.01:
            self._chrome(w, snap, gx, gw, H, st, ready, frame_alpha, d, node is None and not commit)

    def _chrome(self, w, snap, gx, gw, H, st, ready, alpha, d, empty):
        cr = snap.append_cairo(_rect(gx, 0, gw, H))
        acc, on, bg = w.color("acc"), w.color("on"), w.color("bg")
        cr.set_line_width(1.0)
        r = 6.0
        x0, y0, x1, y1 = gx + 0.5, 0.5, gx + gw - 0.5, H - 0.5
        cr.new_sub_path()
        cr.arc(x1 - r, y0 + r, r, -math.pi / 2, 0)
        cr.arc(x1 - r, y1 - r, r, 0, math.pi / 2)
        cr.arc(x0 + r, y1 - r, r, math.pi / 2, math.pi)
        cr.arc(x0 + r, y0 + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()
        if not ready:
            cr.set_dash([5.0, 4.0])
        cr.set_source_rgba(acc.red, acc.green, acc.blue, alpha)
        cr.stroke()
        cr.set_dash([])
        if ready:                                   # leichter Schein nach innen
            for i, a in ((1, 0.35), (2, 0.18), (3, 0.08)):
                cr.rectangle(x0 + i, y0 + i, x1 - x0 - 2 * i, y1 - y0 - 2 * i)
                cr.set_source_rgba(acc.red, acc.green, acc.blue, a * alpha)
                cr.stroke()
        # Titel-Chip oben am Rahmen (zurück: rechts oben, vor: links oben)
        text = ("‹ " + st["title"]) if d < 0 else (st["title"] + " ›")
        lay = w.create_pango_layout(text)
        fd = lay.get_context().get_font_description() or Pango.FontDescription()
        fd = fd.copy()
        fd.set_weight(Pango.Weight.BOLD)
        lay.set_font_description(fd)
        _ink, log = lay.get_pixel_extents()
        tw, th = log.width, log.height
        cw, ch = tw + 16, th + 4
        cx = (gx + gw - cw - 10) if d < 0 else (gx + 10)
        cx = max(gx + 4, min(cx, gx + gw - cw - 4)) if cw + 8 < gw else gx + 4
        cy = 8
        cr.save()
        cr.rectangle(gx, 0, gw, H)
        cr.clip()
        cr.new_sub_path()
        rr = 4.0
        cr.arc(cx + cw - rr, cy + rr, rr, -math.pi / 2, 0)
        cr.arc(cx + cw - rr, cy + ch - rr, rr, 0, math.pi / 2)
        cr.arc(cx + rr, cy + ch - rr, rr, math.pi / 2, math.pi)
        cr.arc(cx + rr, cy + rr, rr, math.pi, 3 * math.pi / 2)
        cr.close_path()
        if ready:
            cr.set_source_rgba(acc.red, acc.green, acc.blue, alpha)
            cr.fill()
            fg = on
        else:
            cr.set_source_rgba(bg.red, bg.green, bg.blue, 0.92 * alpha)
            cr.fill_preserve()
            cr.set_source_rgba(acc.red, acc.green, acc.blue, alpha)
            cr.stroke()
            fg = acc
        cr.move_to(cx + 8, cy + 2)
        cr.set_source_rgba(fg.red, fg.green, fg.blue, alpha)
        PangoCairo.show_layout(cr, lay)
        # Ohne Bild der vorigen Seite: Titel groß in die Mitte
        if empty and gw > tw + 24:
            lay2 = w.create_pango_layout(text)
            fd2 = fd.copy()
            fd2.set_size(int(fd.get_size() * 1.6) if fd.get_size() > 0 else 20 * Pango.SCALE)
            lay2.set_font_description(fd2)
            _i, l2 = lay2.get_pixel_extents()
            if l2.width + 16 < gw:
                cr.move_to(gx + (gw - l2.width) / 2, H / 2 - l2.height / 2)
                cr.set_source_rgba(acc.red, acc.green, acc.blue, 0.55 * alpha)
                PangoCairo.show_layout(cr, lay2)
        cr.restore()
