# Werkzeug-Seiten für Scourge: Grundgerüst (ToolPage), auf dem auch die Systemeinstellungen aufbauen.
import concurrent.futures, hashlib, ipaddress, json, math, os, re, socket, ssl, subprocess, threading, time, \
    urllib.error, urllib.parse, urllib.request

import chrome as ch

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"


def http(url, timeout=15, headers=None, data=None, method=None, follow=True):
    """-> (Status, Text, End-URL, Header). Nie eine Ausnahme nach außen."""
    req = urllib.request.Request(url, data=data, method=method, headers={
        "User-Agent": UA, "Accept-Language": "de,en;q=0.8",
        "Cookie": "SOCS=CAESHAgBEhJnd3NfMjAyMzA4MTAtMF9SQzIaAmRlIAEaBgiA_LyaBg", **(headers or {})})
    try:
        opener = urllib.request.build_opener() if follow else urllib.request.build_opener(_NoRedirect)
        r = opener.open(req, timeout=timeout)
        return r.status, r.read(600_000).decode("utf-8", "ignore"), r.geturl(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, "", url, dict(e.headers or {})
    except Exception as e:
        return None, str(e), url, {}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a):
        return None


# ======================================================================
# Grundgerüst einer Werkzeug-Seite
# ======================================================================
class ToolPage(Gtk.ScrolledWindow):
    title = "Werkzeug"
    subtitle = ""
    privacy = ""

    def __init__(self, tab):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.tab = tab
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.box.add_css_class("home")
        self.set_child(self.box)
        if self.privacy:
            p = Gtk.Label(label="󰒃  " + self.privacy, xalign=0, wrap=True)
            p.add_css_class("note")
            self.box.append(p)
        self.build()

    # Helfer
    def card(self, title, parent=None):
        c = ch.Card(title, spacing=8)
        (parent or self.box).append(c)
        return c

    def entry(self, placeholder, cb, width=28, password=False):
        e = Gtk.PasswordEntry(show_peek_icon=True) if password else Gtk.Entry(placeholder_text=placeholder, width_chars=width)
        if password:
            e.set_property("placeholder-text", placeholder)
        e.add_css_class("pb")
        e.set_hexpand(True)
        e.connect("activate", lambda *_: cb())
        return e

    def button(self, label, cb, tip=None, small=False):
        b = Gtk.Button(label=label, tooltip_text=tip, valign=Gtk.Align.CENTER)
        b.add_css_class("pb")
        if small:
            b.add_css_class("small")
        b.connect("clicked", lambda *_: cb())
        return b

    def input_row(self, *widgets):
        row = Gtk.Box(spacing=8)
        for w in widgets:
            row.append(w)
        self.box.append(row)
        return row

    def line(self, parent, key, value, level=None, link=None):
        row = Gtk.Box(spacing=8)
        if level:
            dot = Gtk.Label(label="●", valign=Gtk.Align.START)
            dot.add_css_class("sev"); dot.add_css_class(f"sev-{level}")
            row.append(dot)
        k = Gtk.Label(label=key, xalign=0, width_chars=18, yalign=0, wrap=True, max_width_chars=22)
        k.add_css_class("key")
        row.append(k)
        if link:
            v = Gtk.LinkButton(uri=link, label=value[:80], halign=Gtk.Align.START, hexpand=True)
        else:
            v = Gtk.Label(label=str(value), xalign=0, wrap=True, wrap_mode=2, selectable=True, hexpand=True)
            v.add_css_class("val")
        row.append(v)
        parent.append(row)
        return row

    def clear(self, box):
        while (c := box.get_first_child()):
            box.remove(c)

    def busy(self, box, text="Läuft …"):
        self.clear(box)
        row = Gtk.Box(spacing=8)
        sp = Gtk.Spinner()
        sp.start()
        row.append(sp)
        l = Gtk.Label(label=text, xalign=0)
        l.add_css_class("note")
        row.append(l)
        box.append(row)

    def run(self, work, done):
        """work() im Hintergrund, done(Ergebnis) im GTK-Thread."""
        def t():
            try:
                res = work()
            except Exception as e:
                res = e
            GLib.idle_add(lambda: (done(res), False)[-1])
        threading.Thread(target=t, daemon=True).start()

    def open_url(self, url):
        Gtk.UriLauncher(uri=url).launch(self.tab.win, None, None)

    def pause(self):
        pass


CLASSES = {}




def make(key, tab):
    page = CLASSES[key](tab)
    return page
