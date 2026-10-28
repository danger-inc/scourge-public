#!/usr/bin/env python3
# Maus-Hervorhebung für die Bildschirmaufnahme (eigener Prozess, siehe recextra.Halo).
import json
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gdk, GLib, Gtk, Gtk4LayerShell as LS

from recextra import hypr_query


class HaloWin:
    def __init__(self, app):
        self.pos = (-200.0, -200.0)
        mon = None
        try:
            for m in json.loads(hypr_query("j/monitors") or "[]"):
                if m.get("focused"):
                    mon = m
        except ValueError:
            pass
        self.origin = (mon["x"], mon["y"]) if mon else (0, 0)
        self.scale = (mon.get("scale") or 1) if mon else 1
        self.win = Gtk.Window(application=app, decorated=False)
        self.win.set_focusable(False)
        LS.init_for_window(self.win)
        LS.set_layer(self.win, LS.Layer.OVERLAY)
        LS.set_namespace(self.win, "scourge-halo")
        LS.set_exclusive_zone(self.win, -1)
        LS.set_keyboard_mode(self.win, LS.KeyboardMode.NONE)
        for edge in (LS.Edge.TOP, LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT):
            LS.set_anchor(self.win, edge, True)
        if mon:
            for dm in Gdk.Display.get_default().get_monitors():
                if dm.get_connector() == mon.get("name"):
                    LS.set_monitor(self.win, dm)
        css = Gtk.CssProvider()
        css.load_from_string("window.halo-win, window.halo-win > * { background: transparent; }")
        self.win.add_css_class("halo-win")
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_USER)
        self.area = Gtk.DrawingArea()
        self.area.set_draw_func(self._draw)
        self.win.set_child(self.area)
        self.win.connect("realize", self._realize)
        self.win.present()
        GLib.timeout_add(16, self._poll)
        self.ppid = os.getppid()

    def _realize(self, w):
        import cairo
        w.get_surface().set_input_region(cairo.Region())      # leere Eingabefläche: Klicks gehen durch

    def _poll(self):
        if os.getppid() != self.ppid:                         # Scourge beendet → mit aufhören
            self.win.get_application().quit()
            return False
        r = hypr_query("j/cursorpos")
        if r:
            try:
                p = json.loads(r)
                self.pos = ((p["x"] - self.origin[0]), (p["y"] - self.origin[1]))
                self.area.queue_draw()
            except (ValueError, KeyError):
                pass
        return True

    def _draw(self, area, cr, w, h):
        x, y = self.pos
        cr.set_source_rgba(1.0, 0.85, 0.1, 0.28)
        cr.arc(x, y, 34, 0, 6.2832)
        cr.fill()
        cr.set_source_rgba(1.0, 0.85, 0.1, 0.9)
        cr.set_line_width(3)
        cr.arc(x, y, 34, 0, 6.2832)
        cr.stroke()


app = Gtk.Application(application_id="de.invincible.ScourgeHalo")
app.connect("activate", lambda a: setattr(a, "halo", HaloWin(a)))
signal.signal(signal.SIGTERM, lambda *_: app.quit())
sys.exit(app.run([]))
