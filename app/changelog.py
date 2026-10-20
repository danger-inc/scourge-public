# Changelog-Fenster für alle -ist-Apps (scourge, notist, filist, musicalist, datist, wallist).
# Liest die CHANGELOG.md der App (Format: "## 1.2.0 (2026-10-19)" und darunter "- Punkt", Folgezeilen
# eingerückt) und zeigt sie im Suite-Look: je Version ein Kasten mit Titel im Rahmen, neue Versionen hervorgehoben.
# Eigenständig (nur Gtk), damit jede App eine Kopie mitliefern kann.
#   changelog.open_window(parent, "datist", "/pfad/CHANGELOG.md", key="datist")
#   changelog.check_new("datist", version) → True, wenn seit dem letzten Start eine neue Version da ist
import json
import math
import os
import re

import gi
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo

SEEN_FILE = os.path.expanduser("~/.config/ist-changelog.json")
MATUGEN_CSS = os.path.expanduser("~/.cache/matugen/colors.css")
FALLBACK = """@define-color background #1a120d; @define-color foreground #f0dfd7;
@define-color primary #ffb68e; @define-color on_primary #532200; @define-color secondary #e5bfa9;
@define-color tertiary #cdc98f;"""
FONT = "JetBrainsMono Nerd Font"

CSS = """
window.changelog { background-color: @cl_background; color: @cl_foreground; }
window.changelog * { font-family: "JetBrainsMono Nerd Font", "JetBrains Mono", "Notes Icons", monospace; }
window.changelog label { font-size: 13px; }
window.changelog .cl-top { padding: 10px 14px 6px 14px; }
window.changelog .cl-brand { background-color: @cl_primary; color: @cl_on_primary; padding: 1px 9px; border-radius: 4px;
    font-weight: bold; margin-right: 8px; }
window.changelog .cl-head { font-weight: bold; }
window.changelog .cl-dim { color: @cl_secondary; }
window.changelog .cl-accent { color: @cl_primary; font-weight: bold; }
window.changelog button.cl-close { background: none; border: 1px solid transparent; box-shadow: none; min-height: 0;
    min-width: 0; padding: 1px 6px; border-radius: 4px; color: @cl_foreground; }
window.changelog button.cl-close:hover { background-color: @cl_tertiary; color: @cl_background; }
window.changelog .cl-list { padding: 4px 14px 6px 14px; }
window.changelog .cl-box { color: alpha(@cl_primary, 0.85); padding: 24px 14px 10px 14px; }
window.changelog .cl-box.new { color: @cl_primary; }
window.changelog .cl-box > * { color: @cl_foreground; }
window.changelog .cl-item { font-size: 12.5px; }
window.changelog .cl-bullet { color: @cl_primary; font-weight: bold; }
window.changelog .cl-status { padding: 4px 14px 8px 14px; font-size: 12px; font-weight: bold; }
window.changelog scrollbar slider { background-color: alpha(@cl_primary, 0.45); min-width: 4px; border-radius: 2px; }
"""


# ---------- Datei lesen ----------
def parse(path):
    """→ [{"version": "1.2.0", "date": "2026-10-19", "items": ["text", …]}, …] (neueste zuerst)."""
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return []
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)     # HTML-Kommentare (auch Ausgaben-Marker) überspringen
    out, item = [], None
    for line in text.splitlines():
        m = re.match(r"^##\s+(\S+)\s*(?:\((.*?)\))?", line)
        if m:
            out.append({"version": m.group(1), "date": m.group(2) or "", "items": []})
            item = None
        elif not out:
            continue
        elif re.match(r"^\s*[-*]\s+", line):
            item = re.sub(r"^\s*[-*]\s+", "", line).strip()
            out[-1]["items"].append(item)
        elif line.strip() and item is not None:          # eingerückte Folgezeile
            out[-1]["items"][-1] += " " + line.strip()
        elif not line.strip():
            item = None
    return out


def to_markup(text):
    """**fett** und `code` in Pango-Markup (Rest wird maskiert)."""
    t = GLib.markup_escape_text(text)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"`(.+?)`", r"<tt>\1</tt>", t)
    return t


def nice_date(iso):
    m = re.match(r"(\d{4})-(\d\d)-(\d\d)", iso or "")
    return f"{m.group(3)}.{m.group(2)}.{m.group(1)}" if m else (iso or "")


def _vkey(v):
    return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.\-]", v))


# ---------- „Neu seit dem letzten Mal“ ----------
def _load_seen():
    try:
        with open(SEEN_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_seen(data):
    try:
        os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)
        tmp = SEEN_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, SEEN_FILE)
    except OSError:
        pass


def seen_version(key):
    return _load_seen().get(key)


def check_new(key, version):
    """True, wenn diese Version neuer ist als die zuletzt gesehene. Beim allerersten Start nur merken."""
    seen = seen_version(key)
    if seen is None:
        mark_seen(key, version)
        return False
    return _vkey(version) > _vkey(seen)


def mark_seen(key, version):
    data = _load_seen()
    if data.get(key) != version:
        data[key] = version
        _save_seen(data)


# ---------- Darstellung ----------
class Box(Gtk.Box):
    """Kasten mit Titel in der oberen Rahmenlinie (wie btop / chrome.Card). Farbe = CSS-color von .cl-box."""
    PAD = (24, 14, 10, 14)

    def __init__(self, title, tag=""):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("cl-box")
        self.title, self.tag = title, tag
        self.font = Pango.FontDescription.from_string(f"{FONT} Bold 10")

    def do_snapshot(self, snap):
        Gtk.Box.do_snapshot(self, snap)
        w, h = self.get_width(), self.get_height()
        if w < 4 or h < 4:
            return
        gi.require_version("Graphene", "1.0")
        from gi.repository import Graphene
        col = self.get_color()
        top, pr, pb, pl = self.PAD
        W, H = w + pl + pr, h + top + pb
        snap.save()
        pt = Graphene.Point()
        pt.init(-pl, -top)
        snap.translate(pt)
        cr = snap.append_cairo(Graphene.Rect().init(0, 0, W, H))
        r, x0, x1, y1, y0 = 6, 0.5, W - 0.5, H - 0.5, 10.5
        cr.set_line_width(1)

        def text(s, x):
            lay = self.create_pango_layout("")
            lay.set_font_description(self.font)
            lay.set_text(f" {s} ", -1)
            tw, th = lay.get_pixel_size()
            cr.set_source_rgba(col.red, col.green, col.blue, 1)
            cr.move_to(x, y0 - th / 2)
            PangoCairo.show_layout(cr, lay)
            return tw
        tw = text(self.title, 12)
        gap = (12, 12 + tw)
        tag_gap = None
        if self.tag:
            lay = self.create_pango_layout(f" {self.tag} ")
            lay.set_font_description(self.font)
            gw = lay.get_pixel_size()[0]
            tag_gap = (W - 12 - gw, W - 12)
            text(self.tag, tag_gap[0])
        cr.set_source_rgba(col.red, col.green, col.blue, 1.0 if self.has_css_class("new") else 0.45)
        cr.move_to(gap[1], y0)
        if tag_gap:
            cr.line_to(tag_gap[0], y0)
            cr.move_to(tag_gap[1], y0)
        cr.line_to(x1 - r, y0)
        cr.arc(x1 - r, y0 + r, r, 1.5 * math.pi, 2 * math.pi)
        cr.line_to(x1, y1 - r)
        cr.arc(x1 - r, y1 - r, r, 0, 0.5 * math.pi)
        cr.line_to(x0 + r, y1)
        cr.arc(x0 + r, y1 - r, r, 0.5 * math.pi, math.pi)
        cr.line_to(x0, y0 + r)
        cr.arc(x0 + r, y0 + r, r, math.pi, 1.5 * math.pi)
        cr.line_to(gap[0], y0)
        cr.stroke()
        snap.restore()


def view(path, since=None):
    """Alle Versionen als Spalte von Kästen; Versionen neuer als `since` sind hervorgehoben."""
    col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    col.add_css_class("cl-list")
    entries = parse(path)
    if not entries:
        lbl = Gtk.Label(label="Noch keine Einträge.", xalign=0)
        lbl.add_css_class("cl-dim")
        col.append(lbl)
    for i, e in enumerate(entries):
        new = since is not None and _vkey(e["version"]) > _vkey(since)
        tag = "neu" if new else ("aktuell" if i == 0 else "")
        box = Box(f"v{e['version']} · {nice_date(e['date'])}" if e["date"] else f"v{e['version']}", tag)
        if new or (since is None and i == 0):
            box.add_css_class("new")
        for it in e["items"]:
            row = Gtk.Box(spacing=8)
            b = Gtk.Label(label="▸", valign=Gtk.Align.START)
            b.add_css_class("cl-bullet")
            row.append(b)
            lbl = Gtk.Label(xalign=0, wrap=True, hexpand=True, selectable=False)
            lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            lbl.set_markup(to_markup(it))
            lbl.add_css_class("cl-item")
            row.append(lbl)
            box.append(row)
        col.append(box)
    return col


_provider = None


def _install_css(colors_css=None):
    """Eigener Stil, damit das Fenster in jeder App gleich aussieht (Farben aus matugen bzw. vom Aufrufer)."""
    global _provider
    css = FALLBACK
    if colors_css is None:
        try:
            colors_css = open(MATUGEN_CSS).read()
        except OSError:
            colors_css = ""
    # Alle Farbnamen bekommen das Präfix cl_: @define-color gilt in GTK für das ganze Programm, und dieser
    # Provider liegt über dem der App – mit den echten Namen fror er deren Farben beim ersten Öffnen ein
    # (Super+W wirkte danach nicht mehr).
    css = re.sub(r"@(define-color\s+)?([A-Za-z_][\w-]*)", lambda m: "@" + (m.group(1) or "") + "cl_" + m.group(2),
                 css + "\n" + colors_css) + "\n" + CSS
    if _provider is None:
        _provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), _provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER + 5)
    _provider.load_from_string(css)


_open = {}


def open_window(parent, brand, path, key=None, version=None, colors_css=None):
    """Zeigt das Changelog-Fenster (ein zweiter Aufruf holt das offene nach vorn)."""
    key = key or brand
    win = _open.get(key)
    if win is not None:
        win.present()
        return win
    _install_css(colors_css)
    entries = parse(path)
    version = version or (entries[0]["version"] if entries else "")
    since = seen_version(key)
    if since is not None and _vkey(since) >= _vkey(version):
        since = None                       # nichts Neues → nur die aktuelle Version betonen
    win = Gtk.Window(title=f"{brand} · changelog", transient_for=parent, modal=False)
    win.add_css_class("changelog")
    try:
        mon = parent.get_display().get_monitor_at_surface(parent.get_surface()) if parent else None
        g = mon.get_geometry() if mon else None
        win.set_default_size(min(720, int(g.width * 0.9)) if g else 720, min(760, int(g.height * 0.85)) if g else 760)
    except Exception:
        win.set_default_size(720, 760)
    try:
        import glass
        getattr(glass, "frame_window", lambda w: w.set_decorated(False))(win)
    except Exception:
        win.set_decorated(False)

    top = Gtk.Box(spacing=4)
    top.add_css_class("cl-top")
    b = Gtk.Label(label=brand)
    b.add_css_class("cl-brand")
    top.append(b)
    h = Gtk.Label(label="changelog", hexpand=True, xalign=0)
    h.add_css_class("cl-head")
    top.append(h)
    v = Gtk.Label(label=f"v{version}" if version else "")
    v.add_css_class("cl-dim")
    top.append(v)
    close = Gtk.Button(label="✕", tooltip_text="Schließen (Esc)", focus_on_click=False, margin_start=8)
    close.add_css_class("cl-close")
    close.connect("clicked", lambda *_: win.close())
    top.append(close)

    scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
    scroll.set_child(view(path, since))
    status = Gtk.Box(spacing=5)
    status.add_css_class("cl-status")
    n_new = sum(1 for e in entries if since is not None and _vkey(e["version"]) > _vkey(since))
    for txt, cls in (("esc", "cl-accent"), ("zu", "cl-dim")) + (
            (("   ·   ", "cl-dim"), (f"{n_new} neue Version{'en' if n_new != 1 else ''} seit v{since}", "cl-accent"))
            if n_new else ()):
        lbl = Gtk.Label(label=txt)
        lbl.add_css_class(cls)
        status.append(lbl)
    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    root.append(Gtk.WindowHandle(child=top))
    root.append(scroll)
    root.append(status)
    win.set_child(root)

    keys = Gtk.EventControllerKey()
    keys.connect("key-pressed", lambda _c, kv, *_: (win.close(), True)[1] if kv in (Gdk.KEY_Escape, Gdk.KEY_q) else False)
    win.add_controller(keys)
    win.connect("close-request", lambda *_: (_open.pop(key, None), False)[1])
    _open[key] = win
    if version:
        mark_seen(key, version)
    win.present()
    return win

