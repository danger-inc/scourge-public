# Linke App-Leiste: öffnet sich, wenn die Maus an den linken Fensterrand fährt (schließt beim Verlassen),
# oder per Klick auf den „scourge“-Chip oben links (bleibt dann offen bis zum nächsten Klick/Esc/Klick daneben).
# Von dort startet man die anderen eigenen Apps; unten steht der Changelog.
import json
import os
import shutil
import subprocess
import threading
import time

import gi
import suite_install as si
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk

# (Befehl, Symbol, Name, Beschreibung)
APPS = (
    ("notist", "󰠮", "notist", "Notizen, Words, LaTeX"),
    ("filist", "󰉋", "filist", "Dateien & Ordner"),
    ("musicalist", "󰝚", "musicalist", "YouTube Music"),
    ("datist", "󰸗", "datist", "Kalender"),
    ("wallist", "󰸉", "wallist", "Hintergründe für Super+W"),
    ("mailist", "󰇮", "mailist", "Gmail, leicht löschen"),
    ("sharist", "󰒊", "sharist", "Quick Share mit dem Handy"),
    ("linguist", "󰗊", "linguist", "Übersetzer – Maus in die Ecke"),
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
button.appinst { background: none; background-image: none; box-shadow: none; outline: none; min-width: 24px;
    min-height: 24px; padding: 0; margin: 0 2px 0 4px; border-radius: 6px; border: 1px solid alpha(@primary, 0.30);
    color: alpha(@primary, 0.85); font-size: 14px; }
button.appinst:hover { background-color: alpha(@primary, 0.16); border-color: @primary; color: @primary; }
button.appinst.upd { border-color: @primary; color: @primary; background-color: alpha(@primary, 0.10); }
button.appinst.err { border-color: #e8736b; color: #e8736b; }
button.appinst.busy { border-color: transparent; }
button.appinst spinner { min-width: 14px; min-height: 14px; color: @primary; }
button.applink.updrow { border-color: alpha(@primary, 0.18); }
button.applink.updrow.has-upd { border-color: alpha(@primary, 0.70); background-color: alpha(@primary, 0.12); }
button.applink.updrow.has-upd .app-name { color: @primary; }
.drawer.pinned { border: 1px solid alpha(@primary, 0.55); border-radius: 6px; box-shadow: none;
    margin: 10px 0 10px 10px; background-color: alpha(@background, 0.35); }
"""


def app_path(cmd):
    local = os.path.expanduser(f"~/.local/bin/{cmd}")
    return local if os.access(local, os.X_OK) else shutil.which(cmd)


ICON_PX = 20       # eigene App-Icons in der Leiste (≈ Höhe der 18-px-Symbolschrift)
PREFS = os.path.expanduser("~/.config/scourge/drawer.json")


def custom_icon(cmd):
    """Eigenes Icon aus ~/.config/ist-icons (software → suite-apps) als kleines Bild, sonst None."""
    try:
        import icons
        f = icons.small_file(cmd, ICON_PX * 2)          # kleine Theme-PNG statt der 8-MB-SVG
        if not f:
            return None
        from gi.repository import GdkPixbuf
        pb = GdkPixbuf.Pixbuf.new_from_file_at_size(f, ICON_PX * 2, ICON_PX * 2)   # ×2: scharf bei Skalierung
        img = Gtk.Image.new_from_paintable(Gdk.Texture.new_for_pixbuf(pb))
        img.set_pixel_size(ICON_PX)                     # feste Größe wie die Symbole der anderen Apps
        img.set_size_request(26, -1)
        return img
    except Exception:  # noqa: BLE001
        return None


class AppDrawer(Gtk.Box):
    """Zwei Zustände: schwebend (Maus am linken Rand, liegt über dem Inhalt, schließt beim Verlassen) und
    angeheftet (Klick auf „scourge“: steht links neben dem Inhalt und schiebt ihn zur Seite – nichts wird verdeckt)."""

    def __init__(self, app, content):
        super().__init__()
        self.app = app
        self._close_id = None
        self.buttons = []
        self.inst_rows = {}       # cmd → [(knopf, beschreibung, standardtext)] – je Leiste (angeheftet/schwebend)
        self.upd_rows = []
        self.mgr = si.manager()
        self.mgr.listen(self._sync)

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
        # Maus fährt über die Kante gleich aus dem Fenster hinaus (ohne die Leiste zu berühren) → wieder schließen
        m.connect("leave", lambda *_: self.is_open() and self._leave())
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
        self._sync(None)
        GLib.timeout_add_seconds(20, lambda: (self.mgr.check(), False)[-1])   # still im Hintergrund, 6-h-Cache

    def _panel(self, pinned):
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, width_request=WIDTH, hexpand=False)
        panel.add_css_class("drawer")
        if pinned:
            panel.add_css_class("pinned")
        title = Gtk.Label(label="󰀻  apps", xalign=0)
        title.add_css_class("drawer-title")
        panel.append(title)
        for cmd, icon, name, desc in APPS:
            b = Gtk.Button(focus_on_click=False, hexpand=True)
            b.add_css_class("applink")
            row = Gtk.Box(spacing=10)
            ic = custom_icon(cmd) or Gtk.Label(label=icon, xalign=0.5)
            ic.add_css_class("app-icon")
            txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            n = Gtk.Label(label=name, xalign=0)
            n.add_css_class("app-name")
            d = Gtk.Label(label=desc, xalign=0, ellipsize=3, max_width_chars=22)
            d.add_css_class("app-desc")
            txt.append(n)
            txt.append(d)
            row.append(ic)
            row.append(txt)
            b.set_child(row)
            b.connect("clicked", lambda *_, c=cmd, nm=name: self.launch(c, nm))
            self.buttons.append((b, cmd, name))
            line = Gtk.Box()
            line.append(b)
            inst = Gtk.Button(focus_on_click=False, valign=Gtk.Align.CENTER, visible=False)
            inst.add_css_class("appinst")
            inst.connect("clicked", lambda *_, c=cmd: self.inst_clicked(c))
            line.append(inst)
            self.inst_rows.setdefault(cmd, []).append((inst, d, desc))
            panel.append(line)
        if si.SUPPORTED:
            b = Gtk.Button(focus_on_click=False)
            b.add_css_class("applink")
            b.add_css_class("updrow")
            row = Gtk.Box(spacing=10)
            ic = Gtk.Label(label="󰚰", xalign=0.5)
            ic.add_css_class("app-icon")
            txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            n = Gtk.Label(label="nach updates suchen", xalign=0)
            n.add_css_class("app-name")
            d = Gtk.Label(label="", xalign=0, ellipsize=3, max_width_chars=22)
            d.add_css_class("app-desc")
            txt.append(n)
            txt.append(d)
            row.append(ic)
            row.append(txt)
            b.set_child(row)
            b.set_margin_top(6)
            b.connect("clicked", lambda *_: self.update_clicked())
            self.upd_rows.append((b, ic, n, d))
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
            b.set_tooltip_text(f"{name} öffnen" if ok else f"{name} ist nicht installiert – rechts daneben installieren")
        self.mgr.check()
        self._sync(None)

    # ---------- installieren / aktualisieren ----------
    def _sync(self, key):
        """Knöpfe und Texte aus dem gemeinsamen Zustand (suite_install.manager) neu setzen."""
        for cmd, rows in self.inst_rows.items():
            if key and key != cmd:
                continue
            look = self._look(cmd)
            for inst, d, default in rows:
                glyph, tip, text = look
                inst.set_visible(glyph is not None)
                if glyph == "spin":
                    sp = inst.get_child()
                    if not isinstance(sp, Gtk.Spinner):
                        inst.set_child(Gtk.Spinner(spinning=True))
                elif glyph:
                    inst.set_child(None)
                    inst.set_label(glyph)
                inst.set_tooltip_text(tip)
                for c in ("busy", "err", "upd"):
                    inst.remove_css_class(c)
                if glyph == "spin":
                    inst.add_css_class("busy")
                elif glyph == "󰀦":
                    inst.add_css_class("err")
                elif glyph in ("󰚰", "󰑓"):
                    inst.add_css_class("upd")
                d.set_label(text or default)
                d.set_tooltip_text(text or None)
        self._sync_central()
        if key:
            self._refresh_buttons()

    def _refresh_buttons(self):
        for b, cmd, name in self.buttons:
            b.set_sensitive(app_path(cmd) is not None)

    def _look(self, cmd):
        """(Symbol | "spin" | None, Tooltip, Beschreibung) für die App-Zeile."""
        if not si.SUPPORTED or cmd not in si.APPS:
            return None, None, None
        info = self.mgr.info[cmd]
        if info.get("busy"):
            return "spin", f"{cmd}: {info.get('msg', '')}", info.get("msg")
        if info.get("status") == "err":
            tip = info.get("msg", "fehlgeschlagen")
            if info.get("hint") == "packages":
                return "󰀦", f"{tip} – klick: im terminal installieren (fragt nach systempaketen/sudo)", tip
            return "󰀦", f"{tip} – klick: nochmal versuchen", tip
        if info.get("restart"):
            return "󰑓", f"{cmd} neu starten, damit die neue version läuft", "neu starten für die neue version"
        st = si.state(cmd)
        if st == "missing":
            return "󰇚", f"{cmd} installieren (von github)", "nicht installiert"
        if st == "old":
            olds = si.old_names(cmd)
            was = f"früher „{olds[0]}“" if olds else "alte installation"
            return "󰇚", f"{cmd} installieren – ersetzt die alte installation ({was}), daten bleiben", was
        if self.mgr.has_update(cmd):
            cur, lat = si.installed_version(cmd), self.mgr.latest(cmd)
            return "󰚰", f"update v{cur} → v{lat} installieren", f"update v{lat} da"
        if info.get("status") == "ok":
            return None, None, info.get("msg")
        return None, None, None

    def _sync_central(self):
        m = self.mgr
        ups = m.updates()
        queued = [k for k in si.ORDER if m.info[k].get("busy")]
        if queued:
            k = queued[0]
            title, sub, glyph = "installiert …", f"{k}: {m.info[k].get('msg', '')}", "󰦖"
        elif m.info["scourge"].get("restart"):
            title, sub, glyph = "scourge neu starten", m.info["scourge"].get("msg", "neue version installiert"), "󰑓"
        elif ups:
            title = "updates installieren"
            sub = f"{len(ups)} neu: " + ", ".join(ups)
            glyph = "󰚰"
        elif m.checking:
            title, sub, glyph = "sucht updates …", "github wird gefragt", "󰑐"
        else:
            title, glyph = "nach updates suchen", "󰑐"
            sub = ("alles aktuell · " + time.strftime("%H:%M", time.localtime(m.checked))) if m.checked else \
                "alle apps auf github prüfen"
        for b, ic, n, d in self.upd_rows:
            n.set_label(title)
            d.set_label(sub)
            ic.set_label(glyph)
            b.set_tooltip_text(sub)
            b.set_sensitive(not queued and (bool(ups) or not m.checking))
            if ups or m.info["scourge"].get("restart"):
                b.add_css_class("has-upd")
            else:
                b.remove_css_class("has-upd")

    def update_clicked(self):
        m = self.mgr
        if m.info["scourge"].get("restart"):
            return self.restart_self()
        ups = m.updates()
        if ups:
            m.install(ups)
            self.app.notify(f"installiere {len(ups)} update{'s' if len(ups) > 1 else ''}: " + ", ".join(ups))
        else:
            m.check(force=True)

    def inst_clicked(self, cmd):
        info = self.mgr.info[cmd]
        if info.get("busy"):
            return
        if info.get("restart"):
            return self.restart_app(cmd)
        if info.get("status") == "err" and info.get("hint") == "packages":
            info["status"] = None
            if si.terminal_install(cmd):
                self.app.notify(f"{cmd}: installation läuft im terminal weiter")
                self._sync(cmd)
                return
        self.mgr.install([cmd])
        self.app.notify(f"{cmd} wird installiert – läuft im hintergrund")

    def restart_app(self, cmd):
        self.mgr.info[cmd]["restart"] = False
        self._sync(cmd)

        def work():
            import software
            a = software.ADMIN.get(cmd, {})
            if software.unit_exists(a.get("unit")):
                subprocess.run(["systemctl", "--user", "restart", a["unit"]], capture_output=True, timeout=15)
                return
            software.stop(si.APPS[cmd]["proc"])
            software.run_detached(a.get("restart") or [cmd])
        threading.Thread(target=work, daemon=True).start()
        self.app.notify(f"{cmd} startet neu")

    def restart_self(self):
        exe = app_path("scourge")
        if not exe:
            return
        subprocess.Popen(["sh", "-c", 'while kill -0 "$1" 2>/dev/null; do sleep 0.2; done; exec "$0"', exe,
                          str(os.getpid())], start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.app.notify("scourge startet neu …")
        GLib.timeout_add(300, lambda: (self.app.activate_action("quit", None), False)[-1])

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
