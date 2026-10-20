# Linke App-Leiste: öffnet sich, wenn die Maus an den linken Fensterrand fährt (schließt beim Verlassen),
# oder per Klick auf den „scourge“-Chip oben links (bleibt dann offen bis zum nächsten Klick/Esc/Klick daneben).
# Von dort startet man die anderen eigenen Apps; unten steht der Changelog.
import json
import os
import shutil
import subprocess

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk

# (Befehl, Symbol, Name, Beschreibung)
APPS = (
    ("notist", "󰠮", "notist", "Notizen, Words, LaTeX"),
    ("filist", "󰉋", "filist", "Dateien & Ordner"),
    ("musicalist", "󰝚", "musicalist", "YouTube Music"),
    ("datist", "󰸗", "datist", "Kalender"),
    ("wallist", "󰸉", "wallist", "Hintergründe für Super+W"),
)

HERE = os.path.dirname(os.path.realpath(__file__))


def version():
    try:
        return open(os.path.join(HERE, "VERSION")).read().strip()
    except OSError:
        return ""

EDGE = 6          # Breite der unsichtbaren Fühlkante am linken Rand
WIDTH = 236
TOP = 44          # Höhe der Kopfzeile (Chip, Tabs)

DRAWER_CSS = """
.drawer { background-color: @background; border: 1px solid alpha(@primary, 0.55); border-left: none;
    border-radius: 0 6px 6px 0; padding: 10px 8px; box-shadow: 6px 0 22px rgba(0,0,0,0.28); }
.glass .drawer { background-color: alpha(@background, 0.92); }
.drawer .drawer-title { font-size: 13px; font-weight: bold; color: @primary; margin: 2px 6px 8px 6px; }
.drawer .drawer-sub { font-size: 11px; color: @secondary; font-weight: normal; margin: 10px 6px 0 6px; }
button.applink { background: none; background-image: none; border: 1px solid transparent; box-shadow: none;
    border-radius: 6px; padding: 7px 8px; min-height: 0; color: @foreground; outline: none; }
button.applink:hover, button.applink:focus-visible { border-color: alpha(@primary, 0.55); background-color: alpha(@primary, 0.10); }
button.applink .app-icon { font-size: 18px; color: @primary; min-width: 26px; }
button.applink .app-name { font-size: 13px; font-weight: bold; }
button.applink .app-desc { font-size: 11px; color: @secondary; font-weight: normal; }
button.applink:disabled { opacity: 0.45; }
.drawer-edge { background: none; }
.drawer.pinned { border: 1px solid alpha(@primary, 0.55); border-radius: 6px; box-shadow: none;
    margin: 10px 0 10px 10px; background-color: alpha(@background, 0.35); }
"""


def app_path(cmd):
    local = os.path.expanduser(f"~/.local/bin/{cmd}")
    return local if os.access(local, os.X_OK) else shutil.which(cmd)


PREFS = os.path.expanduser("~/.config/scourge/drawer.json")


class AppDrawer(Gtk.Box):
    """Zwei Zustände: schwebend (Maus am linken Rand, liegt über dem Inhalt, schließt beim Verlassen) und
    angeheftet (Klick auf „scourge“: steht links neben dem Inhalt und schiebt ihn zur Seite – nichts wird verdeckt)."""

    def __init__(self, app, content):
        super().__init__()
        self.app = app
        self._close_id = None
        self.buttons = []

        # angeheftet: eigene Spalte links
        self.pin_rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_RIGHT, transition_duration=160,
                                    reveal_child=False)
        self.pin_rev.set_child(self._panel(pinned=True))
        self.append(self.pin_rev)

        self.ov = Gtk.Overlay(hexpand=True)
        self.ov.set_child(content)
        self.append(self.ov)
        self.edge = Gtk.Box(halign=Gtk.Align.START, valign=Gtk.Align.FILL, width_request=EDGE)
        self.edge.add_css_class("drawer-edge")
        m = Gtk.EventControllerMotion()
        m.connect("enter", lambda *_: self.open())
        self.edge.add_controller(m)
        self.ov.add_overlay(self.edge)

        # schwebend: unter der Kopfzeile, damit der „scourge“-Chip frei bleibt
        self.rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_RIGHT, transition_duration=160,
                                halign=Gtk.Align.START, valign=Gtk.Align.FILL, reveal_child=False,
                                margin_top=TOP, margin_bottom=TOP // 2)
        self.rev.set_can_target(False)
        self.panel = self._panel(pinned=False)
        pm = Gtk.EventControllerMotion()
        pm.connect("leave", lambda *_: self._leave())
        pm.connect("enter", lambda *_: self._cancel_close())
        self.panel.add_controller(pm)
        self.rev.set_child(self.panel)
        self.ov.add_overlay(self.rev)

        # Klick neben die schwebende Leiste schließt sie (der Klick selbst geht normal weiter)
        click = Gtk.GestureClick(button=0)
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self._outside_click)
        self.ov.add_controller(click)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)

        if self._load_pinned():
            self.pin(True, animate=False)

    def _panel(self, pinned):
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, width_request=WIDTH, hexpand=False)
        panel.add_css_class("drawer")
        if pinned:
            panel.add_css_class("pinned")
        title = Gtk.Label(label="󰀻  apps", xalign=0)
        title.add_css_class("drawer-title")
        panel.append(title)
        for cmd, icon, name, desc in APPS:
            b = Gtk.Button(focus_on_click=False)
            b.add_css_class("applink")
            row = Gtk.Box(spacing=10)
            ic = Gtk.Label(label=icon, xalign=0.5)
            ic.add_css_class("app-icon")
            txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            n = Gtk.Label(label=name, xalign=0)
            n.add_css_class("app-name")
            d = Gtk.Label(label=desc, xalign=0)
            d.add_css_class("app-desc")
            txt.append(n)
            txt.append(d)
            row.append(ic)
            row.append(txt)
            b.set_child(row)
            b.connect("clicked", lambda *_, c=cmd, nm=name: self.launch(c, nm))
            self.buttons.append((b, cmd, name))
            panel.append(b)
        try:
            import publish
            can_publish = bool(publish.available())
        except Exception:
            can_publish = False
        if can_publish:
            sub = Gtk.Label(label="󰊤  github", xalign=0)
            sub.add_css_class("drawer-title")
            sub.set_margin_top(14)
            panel.append(sub)
            b = Gtk.Button(focus_on_click=False, tooltip_text="Alle eigenen Apps prüfen und einzeln oder gemeinsam hochladen")
            b.add_css_class("applink")
            row = Gtk.Box(spacing=10)
            ic = Gtk.Label(label="󰅧", xalign=0.5)
            ic.add_css_class("app-icon")
            txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            n = Gtk.Label(label="veröffentlichen", xalign=0)
            n.add_css_class("app-name")
            d = Gtk.Label(label="einzeln oder alle zusammen", xalign=0)
            d.add_css_class("app-desc")
            txt.append(n)
            txt.append(d)
            row.append(ic)
            row.append(txt)
            b.set_child(row)
            b.connect("clicked", lambda *_: self.open_publish())
            panel.append(b)
        panel.append(Gtk.Box(vexpand=True))
        b = Gtk.Button(focus_on_click=False, tooltip_text="Was ist neu in scourge?")
        b.add_css_class("applink")
        row = Gtk.Box(spacing=10)
        ic = Gtk.Label(label="󰋚", xalign=0.5)
        ic.add_css_class("app-icon")
        txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        n = Gtk.Label(label="changelog", xalign=0)
        n.add_css_class("app-name")
        d = Gtk.Label(label=f"scourge v{version()}", xalign=0)
        d.add_css_class("app-desc")
        txt.append(n)
        txt.append(d)
        row.append(ic)
        row.append(txt)
        b.set_child(row)
        b.connect("clicked", lambda *_: self.open_changelog())
        panel.append(b)
        text = ("klick auf „scourge“ blendet die leiste aus" if pinned
                else "klick auf „scourge“ heftet die leiste an · esc schließt")
        hint = Gtk.Label(label=text, xalign=0, wrap=True, max_width_chars=24, width_chars=24)
        hint.add_css_class("drawer-sub")
        panel.append(hint)
        return panel

    def _refresh(self):
        for b, cmd, name in self.buttons:
            ok = app_path(cmd) is not None
            b.set_sensitive(ok)
            b.set_tooltip_text(f"{name} öffnen" if ok else f"{name} ist nicht installiert")

    # ---------- angeheftet ----------
    @property
    def pinned(self):
        return self.pin_rev.get_reveal_child()

    def pin(self, on, animate=True):
        self.close()
        self._refresh()
        self.pin_rev.set_transition_duration(160 if animate else 0)
        self.pin_rev.set_reveal_child(on)
        self.edge.set_visible(not on)
        self._save_pinned(on)

    def toggle(self):
        self.pin(not self.pinned)

    def _load_pinned(self):
        try:
            return bool(json.load(open(PREFS)).get("pinned"))
        except (OSError, ValueError):
            return False

    def _save_pinned(self, on):
        try:
            os.makedirs(os.path.dirname(PREFS), exist_ok=True)
            json.dump({"pinned": on}, open(PREFS, "w"))
        except OSError:
            pass

    # ---------- schwebend ----------
    def is_open(self):
        return self.rev.get_reveal_child()

    def open(self, pinned=False):
        if pinned:
            return self.pin(True)
        if self.pinned:
            return
        self._cancel_close()
        if not self.is_open():
            self._refresh()
            self.rev.set_can_target(True)
            self.rev.set_reveal_child(True)

    def close(self):
        """Schwebende Leiste schließen (die angeheftete bleibt)."""
        self._cancel_close()
        if self.is_open():
            self.rev.set_reveal_child(False)
            self.rev.set_can_target(False)

    def _leave(self):
        self._cancel_close()
        self._close_id = GLib.timeout_add(220, self._delayed_close)

    def _delayed_close(self):
        self._close_id = None
        self.close()
        return False

    def _cancel_close(self):
        if self._close_id:
            GLib.source_remove(self._close_id)
            self._close_id = None

    def _outside_click(self, g, n, x, y):
        if self.is_open() and x > self.panel.get_width():
            self.close()

    def _key(self, ctrl, keyval, code, mods):
        if keyval == Gdk.KEY_Escape and self.is_open():
            self.close()
            return True
        return False

    def open_changelog(self):
        self.close()
        import changelog
        changelog.open_window(self.app.win, "scourge", os.path.join(HERE, "CHANGELOG.md"), key="scourge",
                              version=version())

    def open_publish(self):
        import publish
        self.close()
        tab = self.app.current()
        tab.open_page("tool-publish", lambda: publish.PublishPage(tab))

    # ---------- starten ----------
    def launch(self, cmd, name):
        path = app_path(cmd)
        self.close()
        if not path:
            return self.app.notify(f"{name} ist nicht installiert")
        env = dict(os.environ)
        for k in ("LD_PRELOAD", "GSK_RENDERER"):     # nichts von Scourges Umgebung vererben
            env.pop(k, None)
        try:
            proc = subprocess.Popen([path], env=env, start_new_session=True, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            GLib.child_watch_add(GLib.PRIORITY_DEFAULT, proc.pid, lambda *_: None)   # Starter abräumen (kein Zombie)
            self.app.notify(f"{name} wird geöffnet")
        except OSError as e:
            self.app.notify(f"{name} ließ sich nicht starten: {e.strerror}")
