# Einstellungen oben rechts (Zahnrad): Design, Hintergrundmodus, Autostart, Tabs schließen, beenden.
import json, os

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk

import glass

CONF = os.path.expanduser("~/.config/scourge/settings.json")
WINDOWS = os.name == "nt"


def load():
    try:
        with open(CONF) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def get(key, default=None):
    return load().get(key, default)


def put(key, value):
    data = load()
    data[key] = value
    os.makedirs(os.path.dirname(CONF), exist_ok=True)
    tmp = CONF + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, CONF)


def background_mode(key="background"):
    """Fenster schließen = im Hintergrund weiterlaufen (nur Linux; Windows hat kein D-Bus zum Wiederfinden)."""
    return not WINDOWS and bool(get(key, True))


HYPR_CONF = os.path.expanduser("~/.config/hypr/hyprland.lua")


def autostart(cmd):
    """True/False: `<cmd> --daemon` im Hyprland-Autostart (ein/auskommentiert); None: keine solche Zeile."""
    import re
    try:
        text = open(HYPR_CONF).read()
    except OSError:
        return None
    for line in text.splitlines():
        if re.search(rf"/{re.escape(cmd)} --daemon", line):
            return not line.lstrip().startswith("--")
    return None


def set_autostart(cmd, on):
    import re
    try:
        text = open(HYPR_CONF).read()
    except OSError:
        return False
    out, done = [], False
    for line in text.splitlines(keepends=True):
        if not done and re.search(rf"/{re.escape(cmd)} --daemon", line):
            body = line.lstrip()
            ind = line[:len(line) - len(body)]
            if on and body.startswith("--"):
                line = ind + re.sub(r"^--\s?", "", body)
            elif not on and not body.startswith("--"):
                line = ind + "-- " + body
            done = True
        out.append(line)
    if done:
        with open(HYPR_CONF, "w") as f:
            f.write("".join(out))
    return done


def _row(title, desc, widget):
    box = Gtk.Box(spacing=12)
    txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True)
    t = Gtk.Label(label=title, xalign=0)
    t.add_css_class("val")
    txt.append(t)
    if desc:
        d = Gtk.Label(label=desc, xalign=0, wrap=True, max_width_chars=44)
        d.add_css_class("note")
        txt.append(d)
    box.append(txt)
    widget.set_valign(Gtk.Align.CENTER)
    box.append(widget)
    return box


def _switch(on, cb):
    sw = Gtk.Switch(active=bool(on))
    sw.connect("state-set", lambda s, v: (cb(v), False)[-1])
    return sw


_windows = {}


def open_window(app, glass_toggle, cmd="scourge", name="Scourge", bgkey="background", version="", changelog_cb=None,
                update_cb=None):
    """Einstellungen als eigenes Fenster im -ist-Look (Kopf mit Marken-Chip und ✕, Kästen mit Titel im Rahmen,
    Esc schließt) – ein zweiter Aufruf holt das offene nach vorn."""
    import chrome as ch
    win = _windows.get(cmd)
    if win is not None:
        win.present()
        return win
    parent = app.win
    win = Gtk.Window(title=f"{name} · einstellungen", transient_for=parent, modal=False)
    win.add_css_class("pb-window")
    win.set_default_size(560, 640)
    try:
        getattr(glass, "frame_window", lambda w: w.set_decorated(False))(win)
        glass.underlay(win)
    except Exception:
        win.set_decorated(False)
    if glass.AVAILABLE and glass.is_on():
        win.add_css_class("glass")
    if glass.AVAILABLE:
        glass_toggle.connect("toggled", lambda g: (win.add_css_class if g.get_active() else win.remove_css_class)("glass"))

    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    top = Gtk.Box(spacing=8, margin_top=8, margin_start=14, margin_end=12, margin_bottom=2)
    brand = Gtk.Label(label=name.lower(), valign=Gtk.Align.CENTER)
    brand.add_css_class("brand")
    head = Gtk.Label(label="einstellungen", xalign=0, hexpand=True)
    head.add_css_class("tab-heading")
    top.append(brand)
    top.append(head)
    if version:
        v = Gtk.Label(label=f"v{version}")
        v.add_css_class("key")
        top.append(v)
    close = Gtk.Button(label="✕", tooltip_text="Schließen (Esc)", focus_on_click=False, valign=Gtk.Align.CENTER)
    close.add_css_class("tclose")
    close.connect("clicked", lambda *_: win.close())
    top.append(close)
    root.append(Gtk.WindowHandle(child=top))

    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_start=14, margin_end=14, margin_top=6,
                   margin_bottom=10)
    scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, child=body)
    root.append(scroll)

    c = ch.Card("design", spacing=10)
    try:
        import istheme
        c.append(istheme.picker())
        n = Gtk.Label(label="„Automatisch“ übernimmt die Farben deines Desktops (matugen) – dunkel oder hell nach Wahl. "
                            "Das Design gilt für alle -ist-Apps.", xalign=0, wrap=True)
        n.add_css_class("note")
        c.append(n)
    except ImportError:
        pass
    body.append(c)

    if glass.AVAILABLE:
        c = ch.Card("glas", spacing=10)
        sw = _switch(glass_toggle.get_active(), lambda v: glass_toggle.set_active(v))
        glass_toggle.connect("toggled", lambda g: sw.get_active() != g.get_active() and sw.set_active(g.get_active()))
        c.append(_row("glas-effekt", "Fenster scheinen durch wie die Konsole (gilt für alle Apps der Suite)", sw))
        body.append(c)

    if not WINDOWS:
        c = ch.Card("verhalten", spacing=10)
        c.append(_row("im hintergrund weiterlaufen",
                      f"Schließen (✕, Strg+Q, Super+C) schließt alle Tabs und blendet {name} nur aus – beim nächsten "
                      "Aufruf ist es sofort da", _switch(background_mode(bgkey), lambda v: put(bgkey, bool(v)))))
        on = autostart(cmd)
        if on is not None:
            c.append(_row("beim anmelden vorladen", "Autostart im Hintergrund (Zeile in der Hyprland-Konfiguration)",
                          _switch(on, lambda v: set_autostart(cmd, v))))
        if cmd == "scourge":
            try:
                import battery
                if battery.available():
                    c.append(_row("akku-warnung", "Meldung, wenn der Akku unter 20 % (und unter 10 %) fällt",
                                  _switch(battery.enabled(), lambda v: put("battery_warn", bool(v)))))
            except ImportError:
                pass
        body.append(c)
    else:
        c = ch.Card("verhalten", spacing=10)
        n = Gtk.Label(label=f"Unter Windows beendet ✕ {name} ganz.", xalign=0, wrap=True)
        n.add_css_class("note")
        c.append(n)
        body.append(c)

    c = ch.Card("tabs & fenster", spacing=10)
    b1 = ch.button("󰅖 alle tabs schließen", "Alle offenen Tabs zu (ungespeicherte PDFs werden vorher gesichert)",
                   callback=lambda: (app.close_all_tabs(),))
    b2 = ch.button(f"󰐥 {name.lower()} komplett beenden", "Beendet auch den Hintergrundprozess",
                   callback=lambda: (win.close(), app.really_quit()))
    row = Gtk.Box(spacing=8)
    row.append(b1)
    row.append(b2)
    c.append(row)
    body.append(c)

    c = ch.Card("version & updates", spacing=10)
    row = Gtk.Box(spacing=8)
    if update_cb:
        row.append(ch.button("󰑐 nach updates suchen", "Fragt GitHub nach einer neueren Version", callback=update_cb))
    if changelog_cb:
        row.append(ch.button("󰋚 changelog", "Was ist neu?", callback=changelog_cb))
    if update_cb or changelog_cb:
        c.append(row)
    n = Gtk.Label(label="Neben der Versionsnummer oben rechts steht ebenfalls ein Update-Knopf.", xalign=0, wrap=True)
    n.add_css_class("note")
    c.append(n)
    body.append(c)

    status = Gtk.Box(spacing=5, margin_start=14, margin_bottom=8)
    status.add_css_class("tstatus")
    status.append(ch.hint("esc", "zu"))
    root.append(status)
    win.set_child(root)
    keys = Gtk.EventControllerKey()
    keys.connect("key-pressed", lambda _c, kv, *_: (win.close(), True)[1] if kv == Gdk.KEY_Escape else False)
    win.add_controller(keys)
    win.connect("close-request", lambda *_: (_windows.pop(cmd, None), False)[1])
    _windows[cmd] = win
    win.present()
    return win


def build(app, glass_toggle, cmd="scourge", name="Scourge", bgkey="background", version="", changelog_cb=None,
          update_cb=None):
    """Zahnrad-Knopf oben rechts: öffnet das Einstellungsfenster."""
    btn = Gtk.Button(label="󰒓", tooltip_text="Einstellungen", valign=Gtk.Align.CENTER, focus_on_click=False)
    btn.add_css_class("tbtn")
    btn.connect("clicked", lambda *_: open_window(app, glass_toggle, cmd, name, bgkey, version, changelog_cb,
                                                  update_cb))
    btn.popup = lambda: btn.emit("clicked")     # die Suche auf der Startseite öffnet es auch
    return btn
