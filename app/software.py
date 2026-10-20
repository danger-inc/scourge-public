# Startseite → „software“: Standard-Apps ändern, die eigenen Suite-Apps (mit Einstellungen, Kürzeln und eigenen
# Icons) und Terminal-Visuals (istfx und vorhandene Programme) mit Startknopf.
import os
import re
import shlex
import shutil
import subprocess

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

import chrome as ch
import icons
from tools import ToolPage

HYPR_CONF = os.path.expanduser("~/.config/hypr/hyprland.lua")

# ---------------- Standard-Apps ----------------
# (Name, Symbol, MIME-Typen – der erste bestimmt die Auswahl, alle werden gesetzt)
DEFAULT_GROUPS = [
    ("Browser", "󰖟", ["x-scheme-handler/http", "x-scheme-handler/https", "text/html", "application/xhtml+xml"]),
    ("Dateimanager", "󰉋", ["inode/directory"]),
    ("PDF", "󰈦", ["application/pdf"]),
    ("Bilder", "󰋩", ["image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "image/tiff", "image/avif",
                     "image/heic", "image/svg+xml"]),
    ("Videos", "󰕧", ["video/mp4", "video/x-matroska", "video/webm", "video/quicktime", "video/x-msvideo",
                     "video/mpeg"]),
    ("Musik", "󰝚", ["audio/mpeg", "audio/flac", "audio/ogg", "audio/x-wav", "audio/mp4", "audio/aac", "audio/opus"]),
    ("Textdateien", "󰈙", ["text/plain"]),
    ("Markdown", "󰍔", ["text/markdown"]),
    ("Word-Dokumente", "󰈬", ["application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                             "application/msword"]),
    ("LibreOffice-Text", "󰈬", ["application/vnd.oasis.opendocument.text"]),
    ("LaTeX", "󰿈", ["text/x-tex"]),
    ("Archive", "󰗄", ["application/zip", "application/x-tar", "application/x-7z-compressed",
                      "application/vnd.rar", "application/gzip"]),
    ("E-Mail-Links", "󰇮", ["x-scheme-handler/mailto"]),
]


def apps_for(mimes):
    seen, out = set(), []
    for m in mimes:
        for a in Gio.AppInfo.get_all_for_type(m) + Gio.AppInfo.get_recommended_for_type(m):
            if a.get_id() and a.get_id() not in seen and a.should_show():
                seen.add(a.get_id())
                out.append(a)
    out.sort(key=lambda a: a.get_display_name().lower())
    return out


class DefaultApps(ToolPage):
    title = "Standard-Apps"
    subtitle = ""
    privacy = "Ändert nur, welches Programm bei Doppelklick startet (~/.config/mimeapps.list). Nichts verlässt das Gerät."

    def build(self):
        c = self.card("standard-apps")
        intro = Gtk.Label(label="Welches Programm öffnet was – gilt sofort für filist, den Browser und alle anderen "
                                "Apps. Bei Gruppen (z. B. Bilder) wird jedes Format der Gruppe umgestellt.",
                          xalign=0, wrap=True)
        intro.add_css_class("note")
        c.append(intro)
        for name, icon, mimes in DEFAULT_GROUPS:
            self.group_row(c, name, icon, mimes)

    def group_row(self, parent, name, icon, mimes):
        apps = apps_for(mimes)
        cur = Gio.AppInfo.get_default_for_type(mimes[0], False)
        row = Gtk.Box(spacing=10)
        ic = Gtk.Label(label=icon)
        ic.add_css_class("card-icon")
        l = Gtk.Label(label=name, xalign=0, hexpand=True)
        l.add_css_class("key")
        row.append(ic)
        row.append(l)
        state = Gtk.Label(label="", xalign=1)
        state.add_css_class("note")
        if not apps:
            none = Gtk.Label(label="kein passendes Programm installiert", xalign=1)
            none.add_css_class("note")
            row.append(none)
            parent.append(row)
            return
        labels = [a.get_display_name() for a in apps]
        idx = next((i for i, a in enumerate(apps) if cur and a.get_id() == cur.get_id()), -1)
        if idx < 0:
            labels.insert(0, "– nicht festgelegt –")
            apps.insert(0, None)
            idx = 0
        dd = Gtk.DropDown.new_from_strings(labels)
        dd.add_css_class("pb")
        dd.set_selected(idx)
        dd.set_size_request(240, -1)

        def changed(d, _):
            a = apps[d.get_selected()]
            if a is None:
                return
            bad = []
            for m in mimes:
                try:
                    a.set_as_default_for_type(m)
                except GLib.Error:
                    bad.append(m)
            state.set_label("󰄬" if not bad else "teilweise")
            self.tab.notify(f"{name}: öffnet jetzt mit {a.get_display_name()}")
        dd.connect("notify::selected", changed)
        row.append(state)
        row.append(dd)
        parent.append(row)


# ---------------- Suite-Apps ----------------
# (Schlüssel, Symbol, Name, Beschreibung, Start-Befehl, Einstellungs-Befehl oder Hinweis, Version, Konfig-Ordner)
SUITE = [
    ("scourge", "󰈈", "scourge", "Dateien durchleuchten, PDF-Editor, Aufnahme, Schnitt", None,
     "du bist schon drin – Apps-Leiste, System-Kacheln", "~/.local/share/scourge/VERSION", "~/.config/scourge"),
    ("notist", "󰠮", "notist", "Notizen, Words, LaTeX", ["notist"], ["notist", "--settings"],
     "~/.local/share/notes/app/VERSION", "~/.local/share/notes"),
    ("filist", "󰉋", "filist", "Dateien & Ordner, Datei-Dialog für alle Apps", ["filist"],
     "Einstellungen im Menü oben rechts im Fenster", "~/.local/share/filist/app/VERSION", "~/.config/filist"),
    ("musicalist", "󰝚", "musicalist", "YouTube Music", ["rice-music", "show"], ["rice-music", "show:settings"],
     "~/.local/share/rice-apps/music/VERSION", "~/.local/share/rice-apps/music"),
    ("datist", "󰸗", "datist", "Kalender (Uni-Raster, Google)", ["datist"], ["datist", "--tab", "acct"],
     "~/.local/share/rice-apps/calendar/VERSION", "~/.config/kalender"),
    ("wallist", "󰸉", "wallist", "Hintergründe für Super+W", ["wallist"], "Einstellungen im Fenster selbst",
     "~/.local/share/wallist/VERSION", "~/.config/wallist"),
]


def bin_path(cmd):
    local = os.path.expanduser(f"~/.local/bin/{cmd}")
    return local if os.access(local, os.X_OK) else shutil.which(cmd)


def run_detached(argv):
    exe = bin_path(argv[0])
    if not exe:
        return False
    try:
        subprocess.Popen([exe] + argv[1:], start_new_session=True, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def read_version(path):
    try:
        return open(os.path.expanduser(path)).read().strip()
    except OSError:
        return ""


def hypr_text():
    try:
        return open(HYPR_CONF).read()
    except OSError:
        return ""


def shortcuts(cmd, text):
    """Tastenkürzel aus der hyprland.lua, die diese App starten."""
    if not text:
        return []
    variables = dict(re.findall(r'^\s*local\s+(\w+)\s*=\s*"([^"]*)"', text, re.M))
    out = []
    for line in text.splitlines():
        if line.lstrip().startswith("--") or "hl.bind(" not in line:
            continue
        m = re.search(r'hl\.bind\((.+?),\s*hl\.dsp\.exec_cmd\((.+?)\)\s*\)', line)
        if not m:
            continue
        target = m.group(2)
        lit = re.findall(r'"([^"]*)"', target)
        resolved = " ".join(lit) if lit else variables.get(target.strip(), "")
        if not re.search(rf"(^|/|\s){re.escape(cmd)}(\s|$)", resolved):
            continue
        keys = m.group(1).replace("mainMod", "Super").replace("..", "").replace('"', "")
        keys = re.sub(r"\s*\+\s*", "+", keys).replace(" ", "")
        out.append(keys.replace("SHIFT", "Umschalt").replace("CTRL", "Strg").replace("ALT", "Alt"))
    return out


def autostart(cmd, text):
    """True/False: wird die App beim Anmelden im Hintergrund vorgeladen? None: keine Zeile dafür."""
    for line in text.splitlines():
        if re.search(rf"/{re.escape(cmd)} --daemon", line):
            return not line.lstrip().startswith("--")
    return None


def set_autostart(cmd, on):
    text = hypr_text()
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


def default_for(desktop_id):
    """Welche der Standard-Gruppen öffnet diese App?"""
    out = []
    for name, _icon, mimes in DEFAULT_GROUPS:
        cur = Gio.AppInfo.get_default_for_type(mimes[0], False)
        if cur and cur.get_id() == desktop_id:
            out.append(name)
    return out


def icon_widget(key, glyph, size=48):
    f = icons.preview_file(key)
    if f:
        try:
            tex = Gdk.Texture.new_from_filename(f)
            pic = Gtk.Picture.new_for_paintable(tex)
            pic.set_size_request(size, size)
            pic.set_can_shrink(True)
            pic.set_content_fit(Gtk.ContentFit.CONTAIN)
            return pic
        except GLib.Error:
            pass
    l = Gtk.Label(label=glyph)
    l.add_css_class("card-icon")
    l.set_size_request(size, size)
    return l


class SuiteApps(ToolPage):
    title = "Suite-Apps"
    subtitle = ""
    privacy = "Startet nur die eigenen Apps und ändert lokale Einstellungen (Icons, Autostart). Nichts verlässt das Gerät."

    def build(self):
        c = self.card("eigene icons")
        t = Gtk.Label(label=f"Eigene Icons: lege <app>.svg oder <app>.png (z. B. notist.svg) in "
                            f"{icons.CUSTOM.replace(os.path.expanduser('~'), '~')} – scourge schreibt sie als "
                            "App-Symbol ins System (Starter, Taskleiste, Fenster) und beim Start erneut, falls ein "
                            "Update das Logo überschrieben hat. Oder unten bei der App „icon wählen“.",
                      xalign=0, wrap=True)
        t.add_css_class("note")
        c.append(t)
        row = Gtk.Box(spacing=8)
        row.append(self.button("󰉋 ordner öffnen", self.open_icon_dir, small=True))
        row.append(self.button("󰑐 jetzt anwenden", self.apply_all, "Alle Icons aus dem Ordner übernehmen", small=True))
        c.append(row)
        self.cards = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.box.append(self.cards)
        self.fill()

    def fill(self):
        while (w := self.cards.get_first_child()):
            self.cards.remove(w)
        text = hypr_text()
        for key, glyph, name, desc, start, settings, ver, cfg in SUITE:
            self.app_card(key, glyph, name, desc, start, settings, ver, cfg, text)

    def app_card(self, key, glyph, name, desc, start, settings, ver, cfg, text):
        installed = key == "scourge" or (start and bin_path(start[0]))
        v = read_version(ver)
        c = ch.Card(f"{name}" + (f" · v{v}" if v else ""), spacing=8)
        self.cards.append(c)
        top = Gtk.Box(spacing=14)
        top.append(icon_widget(key, glyph))
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        d = Gtk.Label(label=desc if installed else desc + " – nicht installiert", xalign=0, wrap=True)
        d.add_css_class("key")
        info.append(d)
        cmd = (start or ["scourge"])[0]
        keys = shortcuts(cmd, text)
        desk = icons.SUITE[key][1]
        facts = []
        if keys:
            facts.append("󰌌 " + ", ".join(keys))
        opens = default_for(desk)
        if opens:
            facts.append("öffnet " + ", ".join(opens))
        facts.append("󰉋 " + os.path.expanduser(cfg).replace(os.path.expanduser("~"), "~"))
        if icons.has_custom(key):
            facts.append("󰏘 eigenes icon")
        f = Gtk.Label(label="   ·   ".join(facts), xalign=0, wrap=True)
        f.add_css_class("note")
        info.append(f)
        top.append(info)
        c.append(top)

        row = Gtk.Box(spacing=8)
        if start:
            row.append(self.button("󰐊 öffnen", lambda s=start, n=name: self.launch(s, n), small=True))
        if isinstance(settings, list):
            row.append(self.button("󰒓 einstellungen", lambda s=settings, n=name: self.launch(s, n), small=True))
        elif settings:
            h = Gtk.Label(label=settings, xalign=0)
            h.add_css_class("note")
            row.append(h)
        spacer = Gtk.Box(hexpand=True)
        row.append(spacer)
        auto = autostart(cmd, text)
        if auto is not None:
            l = Gtk.Label(label="vorladen beim anmelden")
            l.add_css_class("note")
            sw = Gtk.Switch(active=auto, valign=Gtk.Align.CENTER,
                            tooltip_text="Startet die App beim Anmelden unsichtbar im Hintergrund (hyprland.lua) – "
                                         "das Fenster ist dann sofort da")
            sw.connect("state-set", lambda s, on, cm=cmd: (set_autostart(cm, on) and
                                                            self.tab.notify(f"{cm}: Autostart {'an' if on else 'aus'} "
                                                                            "(ab der nächsten Anmeldung)"), False)[-1])
            row.append(l)
            row.append(sw)
        row.append(self.button("󰋩 icon wählen", lambda k=key: self.pick_icon(k), small=True))
        if icons.has_custom(key):
            row.append(self.button("󰜉 icon zurücksetzen", lambda k=key: self.reset_icon(k), small=True))
        if os.path.isdir(os.path.expanduser(cfg)):
            row.append(self.button("󰉋", lambda p=cfg: self.open_dir(p), "Daten-/Einstellungsordner öffnen", small=True))
        c.append(row)

    def launch(self, argv, name):
        if not run_detached(argv):
            self.tab.notify(f"{name} ist nicht installiert")

    @staticmethod
    def open_dir(path):
        path = os.path.expanduser(path)
        os.makedirs(path, exist_ok=True)
        Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(path).get_uri(), None)

    def open_icon_dir(self):
        self.open_dir(icons.CUSTOM)

    def apply_all(self):
        changed = icons.apply_all()
        self.tab.notify("Icons übernommen" if changed else "Alle Icons sind schon aktuell")
        self.fill()

    def pick_icon(self, key):
        f = Gtk.FileFilter(name="Icon (SVG, PNG)")
        f.add_pattern("*.svg")
        f.add_pattern("*.png")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(f)
        dlg = Gtk.FileDialog(title=f"Icon für {key}", filters=filters)

        def done(d, res):
            try:
                path = d.open_finish(res).get_path()
            except GLib.Error:
                return
            try:
                icons.set_custom(key, path)
            except (OSError, ValueError) as e:
                self.tab.notify(f"Icon nicht übernommen: {e}")
                return
            self.tab.notify(f"{key}: neues Icon gesetzt (Starter/Taskleiste ggf. nach Neustart der App)")
            self.fill()
        dlg.open(self.tab.win, None, done)

    def reset_icon(self, key):
        try:
            icons.reset(key)
        except OSError as e:
            self.tab.notify(f"Zurücksetzen fehlgeschlagen: {e}")
            return
        self.tab.notify(f"{key}: ursprüngliches Icon wiederhergestellt (dein Entwurf liegt in .alt/)")
        self.fill()


# ---------------- Terminal-Visuals ----------------
# (Befehl, Beschreibung, Paket falls nicht installiert) – istfx ist scourges eigenes Programm
VISUALS_EXTRA = [
    ("cbonsai -l -i", "wachsender Bonsai (live, unendlich)", "cbonsai"),
    ("pipes.sh -t 1 -R", "Rohre, die sich durchs Terminal schlängeln", "pipes.sh"),
    ("cava", "Audio-Visualizer zur laufenden Musik", "cava"),
    ("btop", "Systemmonitor mit Diagrammen", "btop"),
    ("fastfetch", "Systeminfo mit Logo", "fastfetch"),
    ("cmatrix -b", "der Klassiker aus Matrix", "cmatrix"),
    ("asciiquarium", "Aquarium mit Fischen und Hai", "asciiquarium"),
    ("tty-clock -c -C 4", "große Uhr", "tty-clock"),
    ("sl", "Dampflok fährt durchs Terminal (bei Tippfehler „ls“)", "sl"),
]


def terminal():
    m = re.search(r'^\s*local\s+terminal\s*=\s*"([^"]+)"', hypr_text(), re.M)
    for t in ([m.group(1)] if m else []) + ["kitty", "foot", "alacritty", "wezterm", "xterm"]:
        if shutil.which(t.split()[0]):
            return t
    return None


def istfx_effects():
    exe = bin_path("istfx") or os.path.join(os.path.dirname(os.path.realpath(__file__)), "istfx.py")
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("istfx", os.path.join(os.path.dirname(
            os.path.realpath(__file__)), "istfx.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return [(f"istfx {k}", d) for k, (_fn, d) in mod.EFFECTS.items()], exe
    except Exception:
        return [], exe


class TerminalVisuals(ToolPage):
    title = "Terminal-Visuals"
    subtitle = ""
    privacy = "Startet nur Programme im Terminal. Nichts verlässt das Gerät."

    def build(self):
        self.term = terminal()
        own, _exe = istfx_effects()
        c = self.card("istfx · in deinen farben")
        t = Gtk.Label(label="Eigene Effekte von scourge, immer in den Farben des aktuellen Hintergrunds (Super+W). "
                            "Im Terminal einfach den Befehl tippen – „istfx“ allein zeigt die Liste. Beenden mit q.",
                      xalign=0, wrap=True)
        t.add_css_class("note")
        c.append(t)
        for cmd, desc in own:
            self.cmd_row(c, cmd, desc, True)
        c2 = self.card("weitere programme")
        missing = []
        for cmd, desc, pkg in VISUALS_EXTRA:
            ok = bool(shutil.which(cmd.split()[0]))
            if not ok:
                missing.append(pkg)
            self.cmd_row(c2, cmd, desc, ok, pkg)
        if missing:
            hint = Gtk.Label(label="Fehlende installieren: sudo pacman -S " + " ".join(
                p for p in missing if p != "pipes.sh") + "  (asciiquarium/tty-clock ggf. aus dem AUR)",
                             xalign=0, wrap=True, selectable=True)
            hint.add_css_class("note")
            c2.append(hint)
        if not self.term:
            w = Gtk.Label(label="Kein Terminal gefunden – „starten“ geht dann nicht, kopieren schon.", xalign=0)
            w.add_css_class("note")
            self.box.append(w)

    def cmd_row(self, parent, cmd, desc, ok, pkg=None):
        row = Gtk.Box(spacing=10)
        k = Gtk.Label(label=cmd, xalign=0, width_chars=22, selectable=True)
        k.add_css_class("val")
        k.add_css_class("mono")
        d = Gtk.Label(label=desc if ok else f"{desc} – nicht installiert ({pkg})", xalign=0, hexpand=True, wrap=True)
        d.add_css_class("note" if not ok else "key")
        row.append(k)
        row.append(d)
        start = self.button("󰐊 starten", lambda: self.start(cmd), f"In {self.term or 'einem Terminal'} starten",
                            small=True)
        start.set_sensitive(ok and bool(self.term))
        row.append(start)
        row.append(self.button("󰆏", lambda: self.copy(cmd), "Befehl kopieren", small=True))
        parent.append(row)

    def start(self, cmd):
        term = self.term
        if not term:
            return
        name = os.path.basename(term.split()[0])
        inner = cmd + "; exec $SHELL" if cmd.startswith(("fastfetch", "istfx fetch")) else cmd
        if name == "wezterm":
            argv = shlex.split(term) + ["start", "--", "sh", "-c", inner]
        else:
            argv = shlex.split(term) + ["-e", "sh", "-c", inner]
        try:
            subprocess.Popen(argv, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            self.tab.notify(f"Terminal startet nicht: {e}")

    def copy(self, cmd):
        Gdk.Display.get_default().get_clipboard().set(cmd)
        self.tab.notify(f"kopiert: {cmd}")
