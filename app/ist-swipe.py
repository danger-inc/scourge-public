#!/usr/bin/env python3
# Hell/Dunkel-Wechsel der ganzen Suite als Scanbalken: Standbild des Bildschirms (grim) als Vollbild-Ebene, darunter
# wechseln alle Fenster/Leiste/Terminal ihre Farben, dann zieht eine Lichtkante einmal von links nach rechts und
# legt hinter sich das neue Aussehen frei. Aufruf (meist über istheme.switch):  ist-swipe.py [--theme KEY] [--mode dark|light]
# Läuft mit LD_PRELOAD=libgtk4-layer-shell.so; bei jedem Fehler wird die Auswahl ohne Animation gespeichert.
import fcntl
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import istheme  # noqa: E402

SETTLE = 0.22        # s zwischen Speichern und Start: Apps, Leiste und Terminal haben unter dem Standbild gewechselt
ROW = 8              # logische Pixel je Zeile der Kante
GLOW = 120           # Breite des Nachglühens im neuen Aussehen


def args():
    a = sys.argv[1:]
    out = {}
    for k in ("--theme", "--mode"):
        if k in a:
            out[k[2:]] = a[a.index(k) + 1]
    return out


def plain(a):
    istheme.save(theme=a.get("theme"), mode=a.get("mode"))


T0 = time.time()


def log(msg):
    if os.environ.get("IST_SWIPE_DEBUG"):
        print("%.3f %s" % (time.time() - T0, msg), file=sys.stderr, flush=True)


def main():
    a = args()
    lock = open(os.path.join(os.environ.get("XDG_RUNTIME_DIR", tempfile.gettempdir()), "ist-swipe.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return plain(a)        # läuft schon ein Wechsel: nur speichern
    shot = os.path.join(tempfile.gettempdir(), "ist-swipe-%d.png" % os.getpid())
    try:
        grim = subprocess.Popen(["grim", "-l", "0", shot])     # läuft, während GTK noch geladen wird
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        gi.require_version("Gsk", "4.0")
        gi.require_version("Graphene", "1.0")
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk
        from gi.repository import Gtk4LayerShell as LS
        import cairo
        if grim.wait(timeout=5) != 0:
            raise RuntimeError("grim")
        tex = Gdk.Texture.new_from_filename(shot)
    except Exception as e:      # noqa: BLE001
        print("ist-swipe:", repr(e), file=sys.stderr)
        try:
            os.unlink(shot)
        except OSError:
            pass
        return plain(a)
    os.unlink(shot)

    sec = istheme.swipe_cfg()["sec"]
    st = {"phase": "hold", "p": 0.0, "t0": 0.0, "drawn": False, "edge": None, "glow": None}

    def R(x, y, w, h):
        return Graphene.Rect().init(x, y, w, h)

    def P(x, y):
        return Graphene.Point().init(x, y)

    def rgba(h, alpha=1.0):
        c = Gdk.RGBA()
        c.parse(h)
        c.alpha = alpha
        return c

    def ease(t):
        return 1 - pow(1 - t, 1.8)       # startet gleich zügig, bremst zum Ende

    def hash_(x, y):
        import math
        v = math.sin(x * 127.1 + y * 311.7) * 43758.5453
        return v - math.floor(v)

    class Sweep(Gtk.Widget):
        __gtype_name__ = "IstSwipeSweep"

        def do_snapshot(self, snap):
            w, h = self.get_width(), self.get_height()
            if w < 2:
                return
            full = R(0, 0, w, h)
            if st["phase"] == "hold":
                snap.append_texture(tex, full)
                st["drawn"] = True
                return
            xf = -90 + ease(st["p"]) * (w + 180)
            tk = int(time.time() * 18)
            n = (h + ROW - 1) // ROW
            stops = []
            for off, al in ((0.0, 0.0), (1.0, 0.42)):
                s = Gsk.ColorStop()
                s.offset = off
                s.color = rgba(st["glow"], al)
                stops.append(s)
            for j in range(n):
                y = j * ROW
                import math
                o = math.sin(j * .35 + time.time() * 7) * 5
                if hash_(j, tk) > .93:
                    o += (hash_(j, tk + 9) - .5) * 70
                x = xf + o
                if x < w:
                    snap.push_clip(R(max(x, 0), y, w - max(x, 0), ROW))
                    snap.append_texture(tex, full)
                    snap.pop()
                if -GLOW < x < w + 4:
                    snap.save()
                    snap.translate(P(x - GLOW, y))
                    snap.append_linear_gradient(R(0, 0, GLOW, ROW), P(0, 0), P(GLOW, 0), stops)
                    snap.restore()
                    snap.append_color(rgba(st["edge"]), R(x - 2, y, 4, ROW))

    win = Gtk.Window()
    win.set_decorated(False)
    sw = Sweep()
    sw.set_hexpand(True)
    sw.set_vexpand(True)
    win.set_child(sw)
    css = Gtk.CssProvider()
    css.load_from_string("window { background: transparent; }")
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_USER)
    LS.init_for_window(win)
    LS.set_namespace(win, "ist-swipe")
    LS.set_layer(win, LS.Layer.OVERLAY)
    for edge in (LS.Edge.TOP, LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT):
        LS.set_anchor(win, edge, True)
    LS.set_exclusive_zone(win, -1)
    LS.set_keyboard_mode(win, LS.KeyboardMode.NONE)

    loop = GLib.MainLoop()

    def done():
        log("done")
        loop.quit()
        return False

    def colors():
        c = istheme.parse(istheme.resolve()[0])
        st["edge"] = c.get("primary", "#ffffff")
        st["glow"] = c.get("primary", "#ffffff")

    def tick(w, clock):
        if st["phase"] == "sweep":
            st["p"] = min(1.0, (time.time() - st["t0"]) / sec)
            if st["p"] >= 1.0:
                GLib.timeout_add(30, done)
                return GLib.SOURCE_REMOVE
        w.queue_draw()
        return GLib.SOURCE_CONTINUE

    def after_save():
        colors()
        GLib.timeout_add(int(SETTLE * 1000), start_sweep)
        return False

    def start_sweep():
        log("sweep")
        st["t0"] = time.time()
        st["phase"] = "sweep"
        return False

    def wait_drawn():
        if not st["drawn"]:
            return True
        # Standbild ist vom Compositor übernommen: jetzt darunter wechseln
        log("drawn")
        GLib.timeout_add(40, lambda: (plain(a), after_save(), False)[2])
        return False

    def mapped(*_):
        surf = win.get_surface()
        if surf is not None:
            surf.set_input_region(cairo.Region())     # Klicks gehen durch
    win.connect("map", mapped)
    sw.add_tick_callback(tick)
    GLib.timeout_add(20, wait_drawn)
    GLib.timeout_add(int((sec + 6) * 1000), done)      # Sicherung: die Ebene bleibt nie hängen
    log("present")
    win.present()
    loop.run()
    win.destroy()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:      # noqa: BLE001
        print("ist-swipe:", repr(e), file=sys.stderr)
        plain(args())
