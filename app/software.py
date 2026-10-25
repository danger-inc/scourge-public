# Startseite → „software“: Standard-Apps ändern, die eigenen Suite-Apps (mit Einstellungen, Kürzeln und eigenen
# Icons) und Terminal-Visuals (istfx und vorhandene Programme) mit Startknopf.
import os
import re
import shlex
import shutil
import subprocess
import threading
import time

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
    ("mailist", "󰇮", "mailist", "Gmail im Explorer-Stil (Super+G)", ["mailist"],
     "Einstellungen im Menü oben rechts, Konto links unter „konto“", "~/.local/share/mailist/VERSION",
     "~/.config/mailist"),
    ("sharist", "󰒊", "sharist", "Quick Share mit Android-Handys (Super+D)", ["sharist"],
     "Name, Ordner und Sichtbarkeit im Tab „empfangen“", "~/.local/share/sharist/VERSION", "~/.config/sharist"),
    ("linguist", "󰗊", "linguist", "Übersetzer, Hover in der Bildschirmecke (Super+T)", ["linguist"], ["linguist", "--settings"],
     "~/.local/share/linguist/VERSION", "~/.config/linguist"),
    ("inquirist", "󰍉", "inquirist", "Starter: Apps, Fenster, Dateien, Rechner (Super+R)", ["inquirist"],
     "Keine Einstellungen – Modi per Super+R, Tab, Umschalt+V, Punkt", "~/.local/share/inquirist/VERSION", "~/.config/inquirist"),
]


# Verwaltung je App: Prozess (Regex auf die Befehlszeile), Protokoll, Cache (Ordner + was dort bleiben muss),
# Repo und wie neu gestartet wird ("self" = scourge selbst, Liste = Befehl im Hintergrund, None = beenden + öffnen).
RUN = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
ADMIN = {
    "scourge": dict(proc=r"/scourge/scourge\.py", log=f"{RUN}/scourge.log", restart="self",
                    cache=("~/.cache/scourge", ("originale", "versionen")), repo="~/Projects/scourge"),
    "notist": dict(proc=r"/notes/app/notes\.py", restart=["notist", "--daemon"],
                   cache=("~/.cache/notes", ()), repo="~/Projects/notist"),
    "filist": dict(proc=r"/filist/app/filist\.py", log=f"{RUN}/filist.log", unit="filist.service",
                   restart=["filist", "--daemon"], repo="~/Projects/filist"),
    "musicalist": dict(proc=r"/rice-apps/music/musicd\.py", log=f"{RUN}/rice-music.log",
                       restart=["rice-music", "--daemon"], repo="~/Projects/musicalist"),
    "datist": dict(proc=r"/rice-apps/calendar/ui\.py", restart=None, repo="~/Projects/datist"),
    "wallist": dict(proc=r"/wallist/wallist\.py", restart=None,
                    cache=("~/.cache/wallist/thumbs", ()), repo="~/Projects/wallist"),
    # mail.db ist der Zwischenspeicher (Gmail hat alles) – aber nie leeren, solange noch etwas zu senden ist
    "mailist": dict(proc=r"/mailist/mailist\.py", log=f"{RUN}/mailist.log", restart=["mailist", "--daemon"],
                    cache=("~/.cache/mailist", ("mail.db", "mail.db-wal", "mail.db-shm")), repo="~/Projects/mailist"),
    "sharist": dict(proc=r"/sharist/sharist\.py", log=f"{RUN}/sharist.log", restart=["sharist", "--daemon"],
                    repo="~/Projects/sharist"),
    "linguist": dict(proc=r"/linguist/linguist\.py", log=f"{RUN}/linguist.log", restart=["linguist", "--daemon"],
                     repo="~/Projects/linguist"),
    "inquirist": dict(proc=r"/inquirist/inquirist\.py", log=f"{RUN}/inquirist.log", restart=["inquirist", "--daemon"],
                      repo="~/Projects/inquirist"),
}


def procs(pattern):
    """[(pid, rss_bytes, laufzeit_s)] aller Prozesse, deren Befehlszeile auf pattern passt."""
    rx = re.compile(pattern)
    out = []
    try:
        hz = os.sysconf("SC_CLK_TCK")
        up = float(open("/proc/uptime").read().split()[0])
    except (OSError, ValueError):
        hz, up = 100, 0
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            cmd = open(f"/proc/{d}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace")
            if not rx.search(cmd) or "sh -c" in cmd:
                continue
            stat = open(f"/proc/{d}/stat").read().rsplit(")", 1)[1].split()
            rss = int(stat[21]) * os.sysconf("SC_PAGE_SIZE")
            out.append((int(d), rss, max(0, up - int(stat[19]) / hz)))
        except (OSError, ValueError, IndexError):
            continue
    return out


def window_pids():
    """PIDs mit sichtbarem Fenster (Hyprland); ohne Hyprland leer."""
    if not shutil.which("hyprctl"):
        return set()
    try:
        import json
        r = subprocess.run(["hyprctl", "clients", "-j"], capture_output=True, text=True, timeout=2)
        return {c.get("pid") for c in json.loads(r.stdout or "[]")}
    except (OSError, ValueError, subprocess.SubprocessError):
        return set()


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024


def duration(s):
    s = int(s)
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min"
    if s < 86400:
        return f"{s // 3600} h {s % 3600 // 60:02d} min"
    return f"{s // 86400} d {s % 86400 // 3600} h"


def cache_entries(spec):
    """Was „cache leeren“ löschen darf: alles im Ordner außer den geschützten Einträgen."""
    if not spec:
        return []
    d, keep = spec
    d = os.path.expanduser(d)
    try:
        return [os.path.join(d, n) for n in os.listdir(d) if n not in keep]
    except OSError:
        return []


def du(paths):
    total = 0
    for p in paths:
        if os.path.isfile(p) or os.path.islink(p):
            total += os.lstat(p).st_size
            continue
        for root, _dirs, files in os.walk(p):
            for f in files:
                try:
                    total += os.lstat(os.path.join(root, f)).st_size
                except OSError:
                    pass
    return total


def unit_exists(unit):
    return bool(unit) and shutil.which("systemctl") and subprocess.run(
        ["systemctl", "--user", "cat", unit], capture_output=True, timeout=3).returncode == 0


def repo_info(path):
    """(Web-Adresse, letzter Commit als Text) des lokalen Repos oder None."""
    path = os.path.expanduser(path or "")
    if not path or not os.path.isdir(os.path.join(path, ".git")):
        return None
    try:
        url = subprocess.run(["git", "-C", path, "remote", "get-url", "origin"], capture_output=True, text=True,
                             timeout=3).stdout.strip()
        ts = subprocess.run(["git", "-C", path, "log", "-1", "--format=%ct"], capture_output=True, text=True,
                            timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r"(?:git@|https://)([^:/]+)[:/](.+?)(?:\.git)?$", url)
    web = f"https://{m.group(1)}/{m.group(2)}" if m else None
    when = time.strftime("%d.%m. %H:%M", time.localtime(int(ts))) if ts else ""
    return web, when


def stop(pattern, wait=3.0):
    """Prozesse höflich beenden (TERM), hängen sie, hart (KILL). Läuft im Hintergrund-Thread."""
    pids = [p for p, _r, _t in procs(pattern) if p != os.getpid()]
    for p in pids:
        try:
            os.kill(p, 15)
        except OSError:
            pass
    end = time.time() + wait
    while pids and time.time() < end:
        time.sleep(0.1)
        pids = [p for p in pids if os.path.exists(f"/proc/{p}")]
    for p in pids:
        try:
            os.kill(p, 9)
        except OSError:
            pass


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
    f = icons.small_file(key, size * 2) or icons.preview_file(key)     # kleine PNG statt 8-MB-SVG
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


ADMIN_CSS = """
separator.adm-sep { background: alpha(@primary, 0.15); min-height: 1px; margin: 2px 0; }
flowbox.adm-flow > flowboxchild { padding: 0; background: none; }
textview.adm-log, textview.adm-log text { background: none; color: @foreground; font-size: 11px; }
"""
_admin_css = None


class SuiteApps(ToolPage):
    title = "Suite-Apps"
    subtitle = ""
    privacy = "Startet nur die eigenen Apps und ändert lokale Einstellungen (Icons, Autostart). Nichts verlässt das Gerät."

    def build(self):
        global _admin_css
        if _admin_css is None:                  # einmal pro Prozess, nicht pro geöffneter Seite
            _admin_css = Gtk.CssProvider()
            _admin_css.load_from_string(ADMIN_CSS)
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), _admin_css,
                                                      Gtk.STYLE_PROVIDER_PRIORITY_USER)
        c = self.card("eigene icons")
        t = Gtk.Label(label=f"Eigene Icons: lege <app>.svg oder <app>.png (z. B. notist.svg) in "
                            f"{icons.CUSTOM.replace(os.path.expanduser('~'), '~')} – scourge schreibt sie als "
                            "App-Symbol ins System (Starter, Taskleiste, Fenster) und beim Start erneut, falls ein "
                            "Update das Logo überschrieben hat. Oder unten bei der App „icon wählen“. Beim "
                            "Veröffentlichen kommt das eigene Icon mit ins Repo (Logo und Windows-Symbol).",
                      xalign=0, wrap=True)
        t.add_css_class("note")
        c.append(t)
        row = Gtk.Box(spacing=8)
        row.append(self.button("󰑐 jetzt anwenden", self.apply_all, "Alle Icons aus dem Ordner übernehmen", small=True))
        c.append(row)
        self.cards = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.box.append(self.cards)
        self.admin = {}
        self._tick = 0
        self.connect("map", lambda *_: self._watch(True))
        self.connect("unmap", lambda *_: self._watch(False))
        self.fill()

    def _watch(self, on):
        """Prozess-Status alle 3 s auffrischen, solange die Seite sichtbar ist."""
        if self._tick:
            GLib.source_remove(self._tick)
            self._tick = 0
        if on:
            self.update_status()
            self._tick = GLib.timeout_add_seconds(3, lambda: (self.update_status(), True)[-1])

    def fill(self):
        while (w := self.cards.get_first_child()):
            self.cards.remove(w)
        self.admin = {}
        text = hypr_text()
        for key, glyph, name, desc, start, settings, ver, cfg in SUITE:
            self.app_card(key, glyph, name, desc, start, settings, ver, cfg, text)
        self.update_status()

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
        if key in ADMIN and installed:
            self.admin_rows(c, key, name, start)

    # ---------- Verwaltung ----------
    def admin_rows(self, c, key, name, start):
        a = ADMIN[key]
        w = {"name": name, "start": start}
        self.admin[key] = w
        sep = Gtk.Separator()
        sep.add_css_class("adm-sep")
        c.append(sep)
        st = Gtk.Label(xalign=0, wrap=True)
        st.add_css_class("note")
        w["status"] = st
        c.append(st)
        fl = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=8, row_spacing=6,
                         max_children_per_line=12, homogeneous=False)
        fl.add_css_class("adm-flow")
        c.append(fl)

        def add(b):
            fl.append(b)
            b.get_parent().set_focusable(False)
            return b
        tip = ("scourge schließt sich und ist gleich wieder da" if a["restart"] == "self" else
               "Beenden und frisch im Hintergrund starten" if a["restart"] else "Beenden und neu öffnen")
        w["restart"] = add(self.button("󰜉 neu starten", lambda: self.restart(key), tip, small=True))
        if a["restart"] != "self":
            w["stop"] = add(self.button("󰓛 beenden", lambda: self.stop_app(key), "Prozess beenden (auch den "
                                        "unsichtbaren Hintergrund-Prozess)", small=True))
        if a.get("log") or a.get("unit"):
            w["log_btn"] = add(self.button("󰦪 protokoll", lambda: self.toggle_log(key),
                                           "Letzte Ausgaben der App (Fehlersuche)", small=True))
        entries = cache_entries(a.get("cache"))
        size = du(entries)
        if size:
            w["cache"] = add(self.button(f"󰃢 cache leeren · {human(size)}", lambda: self.clear_cache(key),
                                         "Vorschaubilder/Zwischendateien löschen – werden bei Bedarf neu erzeugt",
                                         small=True))
        info = repo_info(a.get("repo"))
        if info:
            web, when = info
            if web:
                add(self.button("󰊤 github", lambda u=web: Gio.AppInfo.launch_default_for_uri(u, None),
                                f"{web} im Browser öffnen" + (f" · zuletzt hochgeladen {when}" if when else ""),
                                small=True))
            if self.publish_page():
                add(self.button("󰅧 veröffentlichen", self.open_publish, "Änderungen (auch das eigene Icon) prüfen "
                                "und auf GitHub hochladen", small=True))
            w["repo"] = when
        rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        tv = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        tv.add_css_class("adm-log")
        sc = Gtk.ScrolledWindow(min_content_height=120, max_content_height=260, propagate_natural_height=True)
        sc.set_child(tv)
        rev.set_child(sc)
        c.append(rev)
        w["log"] = (rev, tv)

    @staticmethod
    def publish_page():
        try:
            import publish
        except ImportError:                     # öffentliche Ausgabe: ohne Veröffentlichen-Seite
            return None
        return publish if publish.available() else None

    def open_publish(self):
        publish = self.publish_page()
        if publish:
            self.tab.open_page("tool-publish", lambda: publish.PublishPage(self.tab))

    def update_status(self, only=None):
        if not self.admin:
            return
        wins = window_pids()
        for key, w in self.admin.items():
            if only and key != only:
                continue
            ps = procs(ADMIN[key]["proc"])
            w["running"] = bool(ps)
            w["windows"] = sum(1 for p, _r, _t in ps if p in wins)
            if ps:
                rss = sum(r for _p, r, _t in ps)
                parts = [f"● läuft seit {duration(max(t for _p, _r, t in ps))}", human(rss) + " RAM"]
                if w["windows"]:
                    parts.append("fenster offen")
                elif key != "scourge":
                    parts.append("im hintergrund")
            else:
                parts = ["○ läuft nicht"]
            if w.get("repo"):
                parts.append(f"zuletzt hochgeladen {w['repo']}")
            w["status"].set_label("   ·   ".join(parts))
            if "stop" in w and not w.get("armed"):
                w["stop"].set_sensitive(bool(ps))

    def _confirm(self, key, btn, label, action):
        """Bei offenem Fenster erst nach zweitem Klick – sonst geht ein offenes Fenster ungefragt verloren."""
        w = self.admin[key]
        if w.get("armed") == btn:
            btn.set_label(w.pop("armed_label"))
            w["armed"] = None
            return action()
        if not w.get("windows"):
            return action()
        w["armed"], w["armed_label"] = btn, btn.get_label()
        btn.set_label(f"{label}? fenster ist offen – nochmal klicken")

        def reset():
            if w.get("armed") == btn:
                w["armed"] = None
                btn.set_label(w.pop("armed_label"))
            return False
        GLib.timeout_add_seconds(4, reset)

    def _bg(self, key, work, msg):
        w = self.admin[key]
        for k in ("restart", "stop"):
            if k in w:
                w[k].set_sensitive(False)

        def run():
            try:
                work()
            finally:
                GLib.idle_add(done)

        def done():
            for k in ("restart", "stop"):
                if k in w:
                    w[k].set_sensitive(True)
            self.tab.notify(msg)
            GLib.timeout_add(1200, lambda: (self.update_status(key), False)[-1])
            return False
        threading.Thread(target=run, daemon=True).start()

    def stop_app(self, key):
        w = self.admin[key]
        self._confirm(key, w["stop"], "beenden",
                      lambda: self._bg(key, lambda: stop(ADMIN[key]["proc"]), f"{w['name']} beendet"))

    def restart(self, key):
        a, w = ADMIN[key], self.admin[key]
        if a["restart"] == "self":
            exe = bin_path("scourge")
            if not exe:
                return
            # neuer Start erst, wenn dieser Prozess weg ist (sonst nimmt die alte Instanz den Aufruf an)
            subprocess.Popen(["sh", "-c", 'while kill -0 "$1" 2>/dev/null; do sleep 0.2; done; exec "$0"', exe,
                              str(os.getpid())], start_new_session=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.tab.notify("scourge startet neu …")
            GLib.timeout_add(300, lambda: (Gio.Application.get_default().activate_action("quit", None), False)[-1])
            return

        def work():
            if unit_exists(a.get("unit")):
                subprocess.run(["systemctl", "--user", "restart", a["unit"]], capture_output=True, timeout=15)
                return
            stop(a["proc"])
            run_detached(a["restart"] or w["start"] or [key])
        self._confirm(key, w["restart"], "neu starten", lambda: self._bg(key, work, f"{w['name']} neu gestartet"))

    def toggle_log(self, key):
        a, w = ADMIN[key], self.admin[key]
        rev, tv = w["log"]
        if rev.get_reveal_child():
            rev.set_reveal_child(False)
            return
        text = ""
        if a.get("log") and os.path.isfile(a["log"]):
            try:
                with open(a["log"], errors="replace") as f:
                    text = "".join(f.readlines()[-80:])
            except OSError:
                pass
        if not text.strip() and unit_exists(a.get("unit")):
            try:
                text = subprocess.run(["journalctl", "--user", "-u", a["unit"], "-n", "80", "--no-pager", "-o", "cat"],
                                      capture_output=True, text=True, timeout=5).stdout
            except (OSError, subprocess.SubprocessError):
                pass
        noise = re.compile(r"GtkGizmo .* reported min (width|height)|^\s*$")      # harmlose GTK-Warnungen
        text = "\n".join(l for l in text.splitlines() if not noise.search(l))
        tv.get_buffer().set_text(text.strip() or "(keine Ausgaben – alles ruhig)")
        rev.set_reveal_child(True)
        buf = tv.get_buffer()
        GLib.idle_add(lambda: (tv.scroll_to_iter(buf.get_end_iter(), 0, False, 0, 0), False)[-1])

    def clear_cache(self, key):
        w = self.admin[key]
        btn = w["cache"]
        if w.get("armed") != btn:
            w["armed"] = btn
            old = btn.get_label()
            btn.set_label("󰃢 wirklich leeren?")

            def reset():
                if w.get("armed") == btn:
                    w["armed"] = None
                    btn.set_label(old)
                return False
            GLib.timeout_add_seconds(4, reset)
            return
        w["armed"] = None
        entries = cache_entries(ADMIN[key].get("cache"))
        freed = du(entries)
        for p in entries:
            try:
                if os.path.isdir(p) and not os.path.islink(p):
                    shutil.rmtree(p)
                else:
                    os.remove(p)
            except OSError:
                pass
        self.tab.notify(f"{w['name']}: {human(freed)} Cache gelöscht")
        btn.set_visible(False)

    def launch(self, argv, name):
        if not run_detached(argv):
            self.tab.notify(f"{name} ist nicht installiert")

    @staticmethod
    def open_dir(path):
        path = os.path.expanduser(path)
        os.makedirs(path, exist_ok=True)
        Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(path).get_uri(), None)

    def apply_all(self):
        def work():
            changed = icons.apply_all()
            GLib.idle_add(lambda: (self.tab.notify("Icons übernommen" if changed else "Alle Icons sind schon aktuell"),
                                   self.fill(), False)[-1])
        threading.Thread(target=work, daemon=True).start()

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
            self.tab.notify(f"{key}: Icon wird übernommen …")

            def work():            # erzeugt alle Theme-Größen (bei großen SVGs ein paar Sekunden)
                try:
                    icons.set_custom(key, path)
                    msg = f"{key}: neues Icon gesetzt (Starter/Taskleiste ggf. nach Neustart der App)"
                except Exception as e:  # noqa: BLE001
                    msg = f"Icon nicht übernommen: {e}"
                GLib.idle_add(lambda: (self.tab.notify(msg), self.fill(), False)[-1])
            threading.Thread(target=work, daemon=True).start()
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


# Paketnamen je Paketverwalter (None = gibt es dort nicht in den Paketquellen)
VISUAL_PKGS = {
    "cbonsai": {"pacman": "cbonsai", "apt": "cbonsai", "dnf": "cbonsai", "zypper": "cbonsai"},
    "pipes.sh": {"pacman": "pipes.sh", "paru": "pipes.sh"},
    "cava": {"pacman": "cava", "apt": "cava", "dnf": "cava", "zypper": "cava"},
    "btop": {"pacman": "btop", "apt": "btop", "dnf": "btop", "zypper": "btop"},
    "fastfetch": {"pacman": "fastfetch", "apt": "fastfetch", "dnf": "fastfetch", "zypper": "fastfetch"},
    "cmatrix": {"pacman": "cmatrix", "apt": "cmatrix", "dnf": "cmatrix", "zypper": "cmatrix"},
    "asciiquarium": {"apt": "asciiquarium", "dnf": "asciiquarium", "paru": "asciiquarium"},
    "tty-clock": {"apt": "tty-clock", "dnf": "tty-clock", "paru": "tty-clock-git"},
    "sl": {"pacman": "sl", "apt": "sl", "dnf": "sl", "zypper": "sl"},
}
# Terminal-Optionen, die das Fenster gleich im Vollbild öffnen
TERM_FULLSCREEN = {"kitty": ["--start-as=fullscreen"], "foot": ["--fullscreen"], "xterm": ["-fullscreen"],
                   "alacritty": ["-o", 'window.startup_mode="Fullscreen"'], "gnome-terminal": ["--full-screen"],
                   "konsole": ["--fullscreen"]}


def install_command(pkg):
    """Befehl, der `pkg` (Paket-Schlüssel aus VISUALS_EXTRA) für diesen Rechner installiert – oder None."""
    names = VISUAL_PKGS.get(pkg, {})
    for mgr, tmpl in (("pacman", "sudo pacman -S --needed {}"), ("apt", "sudo apt install -y {}"),
                      ("dnf", "sudo dnf install -y {}"), ("zypper", "sudo zypper install -y {}")):
        if shutil.which(mgr if mgr != "apt" else "apt-get") and mgr in names:
            return tmpl.format(names[mgr])
    if "paru" in names:                       # nur im AUR: ein AUR-Helfer muss da sein
        for helper in ("paru", "yay"):
            if shutil.which(helper):
                return f"{helper} -S --needed {names['paru']}"
    return None


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
        if own:                                   # istfx liegt nicht bei jeder Installation bei
            c = self.card("istfx · in deinen farben")
            t = Gtk.Label(label="Eigene Effekte von scourge, immer in den Farben des aktuellen Hintergrunds (Super+W). "
                                "Im Terminal einfach den Befehl tippen – „istfx“ allein zeigt die Liste. Beenden mit q.",
                          xalign=0, wrap=True)
            t.add_css_class("note")
            c.append(t)
            for cmd, desc in own:
                self.cmd_row(c, cmd, desc, True)
        c2 = self.card("weitere programme")
        for cmd, desc, pkg in VISUALS_EXTRA:
            ok = bool(shutil.which(cmd.split()[0]))
            self.cmd_row(c2, cmd, desc, ok, pkg)
        if not self.term:
            w = Gtk.Label(label="Kein Terminal gefunden – „starten“ geht dann nicht, kopieren schon.", xalign=0)
            w.add_css_class("note")
            self.box.append(w)

    def cmd_row(self, parent, cmd, desc, ok, pkg=None):
        row = Gtk.Box(spacing=10)
        k = Gtk.Label(label=cmd, xalign=0, width_chars=22)       # nicht auswählbar: sonst ist der erste Eintrag immer markiert
        k.add_css_class("val")
        k.add_css_class("mono")
        d = Gtk.Label(label=desc if ok else f"{desc} – nicht installiert ({pkg})", xalign=0, hexpand=True, wrap=True)
        d.add_css_class("note" if not ok else "key")
        row.append(k)
        row.append(d)
        if ok or not pkg:
            start = self.button("󰐊 starten", lambda: self.start(cmd), f"In {self.term or 'einem Terminal'} starten",
                                small=True)
            start.set_sensitive(ok and bool(self.term))
            row.append(start)
        else:                                                    # fehlt: gleich hier installieren
            inst = install_command(pkg)
            b = self.button("󰇚 installieren", lambda: self.install(pkg, inst),
                            inst or "Dafür gibt es hier kein Paket in den Paketquellen", small=True)
            b.set_sensitive(bool(inst) and bool(self.term))
            row.append(b)
        row.append(self.button("󰆏", lambda: self.copy(cmd), "Befehl kopieren", small=True))
        parent.append(row)

    def install(self, pkg, inst):
        """Installation im Terminal (braucht sudo) – danach wird die Liste neu geprüft."""
        if not (self.term and inst):
            return
        term = self.term
        name = os.path.basename(term.split()[0])
        inner = f"{inst}; echo; echo '» fertig – Enter schließt'; read _"
        argv = shlex.split(term) + (["start", "--"] if name == "wezterm" else ["-e"]) + ["sh", "-c", inner]
        try:
            p = subprocess.Popen(argv, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            return self.tab.notify(f"Terminal startet nicht: {e}")
        self.tab.notify(f"{pkg}: Installation läuft im Terminal …")

        def wait():
            p.wait()
            GLib.idle_add(self.refresh)
        threading.Thread(target=wait, daemon=True).start()

    def refresh(self):
        kids = []
        c = self.box.get_first_child()
        while c is not None:
            kids.append(c)
            c = c.get_next_sibling()
        for k in kids[1 if self.privacy else 0:]:
            self.box.remove(k)
        self.build()
        return False

    def start(self, cmd):
        term = self.term
        if not term:
            return
        name = os.path.basename(term.split()[0])
        # Der Hintergrunddienst (Autostart) hat ~/.local/bin oft nicht im PATH → istfx direkt über den eigenen
        # Pfad starten und PATH ergänzen; schlägt der Befehl fehl, bleibt das Fenster mit der Meldung offen.
        env = dict(os.environ)
        local_bin = os.path.expanduser("~/.local/bin")
        if local_bin not in env.get("PATH", "").split(":"):
            env["PATH"] = local_bin + ":" + env.get("PATH", "/usr/bin")
        env.pop("TERM", None)
        run = cmd
        if cmd.split()[0] == "istfx":
            here = os.path.join(os.path.dirname(os.path.realpath(__file__)), "istfx.py")
            run = shlex.join([shutil.which("python3") or "python3", here]) + cmd[len("istfx"):]
        if cmd.startswith(("fastfetch", "istfx fetch")):
            inner = run + "; exec ${SHELL:-sh}"
        else:
            inner = (f"{run} || {{ echo; echo '» {cmd} ist fehlgeschlagen – Enter schließt'; read _; }}")
        if name == "wezterm":
            argv = shlex.split(term) + ["start", "--", "sh", "-c", inner]
        else:
            argv = shlex.split(term) + TERM_FULLSCREEN.get(name, []) + ["-e", "sh", "-c", inner]   # immer im Vollbild
        try:
            subprocess.Popen(argv, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             env=env, cwd=os.path.expanduser("~"))
        except OSError as e:
            self.tab.notify(f"Terminal startet nicht: {e}")

    def copy(self, cmd):
        Gdk.Display.get_default().get_clipboard().set(cmd)
        self.tab.notify(f"kopiert: {cmd}")
