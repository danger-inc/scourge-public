# Startseite → software → „Hyprland“: alle Tastenkürzel aus der hyprland.lua verständlich beschrieben und
# durchsuchbar, dazu Gesten, Autostart, Status (Version, Bildschirme, Konfig-Fehler), nützliche hyprctl-Befehle
# und die wichtigen Dateien. Liest die Konfiguration nur – geändert wird nichts (außer „neu laden“).
import json
import os
import re
import shutil
import subprocess
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from tools import ToolPage

CONF = os.path.expanduser("~/.config/hypr/hyprland.lua")
HOME = os.path.expanduser("~")

HYPR_CSS = """
label.hk { font-family: "JetBrainsMono Nerd Font", monospace; font-size: 11px; font-weight: bold; color: @primary;
    border: 1px solid alpha(@primary, 0.45); border-radius: 4px; padding: 0 6px; min-height: 18px; }
label.hk-plus { color: alpha(@foreground, 0.4); font-size: 11px; }
box.hk-row { padding: 3px 0; }
box.hk-row:hover { background-color: alpha(@primary, 0.06); border-radius: 4px; }
label.hk-sec { font-size: 11px; color: @secondary; font-weight: bold; margin-top: 6px; }
"""
_css = None

# Tasten → lesbare Namen
KEYNAMES = {
    "SUPER": "Super", "MOD4": "Super", "SHIFT": "Umschalt", "CTRL": "Strg", "CONTROL": "Strg", "ALT": "Alt",
    "MOD1": "Alt", "PRINT": "Druck", "TAB": "Tab", "SPACE": "Leertaste", "RETURN": "Enter", "ESCAPE": "Esc",
    "period": ".", "comma": ",", "left": "←", "right": "→", "up": "↑", "down": "↓",
    "mouse_down": "Mausrad ↓", "mouse_up": "Mausrad ↑", "mouse:272": "linke Maustaste",
    "mouse:273": "rechte Maustaste", "mouse:274": "mittlere Maustaste",
    "XF86AudioRaiseVolume": "Lauter", "XF86AudioLowerVolume": "Leiser", "XF86AudioMute": "Stumm",
    "XF86AudioMicMute": "Mikro stumm", "XF86MonBrightnessUp": "Heller", "XF86MonBrightnessDown": "Dunkler",
    "XF86AudioNext": "Weiter", "XF86AudioPrev": "Zurück", "XF86AudioPlay": "Play", "XF86AudioPause": "Pause",
}

# Befehle → (Rubrik, Beschreibung); erster Treffer gewinnt
COMMANDS = [
    (r"hyprlock", "sitzung", "Bildschirm sperren"),
    (r"hyprshutdown|exit\(\)", "sitzung", "Abmelden (Hyprland beenden)"),
    (r"launcher\.sh fenster", "apps", "Starter: offene Fenster"),
    (r"launcher\.sh ablage", "apps", "Starter: Zwischenablage-Verlauf"),
    (r"launcher\.sh zeichen", "apps", "Starter: Emoji & Sonderzeichen"),
    (r"launcher\.sh|rofi|wofi", "apps", "Starter (Apps, Fenster, Dateien, Rechner …)"),
    (r"rice-music (next)", "medien", "musicalist: nächster Titel"),
    (r"rice-music (prev)", "medien", "musicalist: vorheriger Titel"),
    (r"rice-music play-pause", "medien", "musicalist: Play/Pause"),
    (r"rice-music|musicalist", "apps", "musicalist (Musik) ein/aus"),
    (r"datist", "apps", "datist (Kalender) ein/aus"),
    (r"mailist", "apps", "mailist (Mail) ein/aus"),
    (r"sharist", "apps", "sharist (Quick Share) ein/aus"),
    (r"linguist --selection", "apps", "linguist: markierten Text übersetzen"),
    (r"linguist", "apps", "linguist (Übersetzer) ein/aus"),
    (r"notist", "apps", "notist (Notizen) öffnen"),
    (r"filist|thunar|nautilus|dolphin", "apps", "Dateimanager"),
    (r"scourge", "apps", "scourge öffnen"),
    (r"random_wall|wallist|wallpaper", "apps", "Neuer Hintergrund + Farben (wallist)"),
    (r"zen-browser|firefox|chromium|brave", "apps", "Browser"),
    (r"kitty|foot|alacritty|wezterm|terminal", "apps", "Terminal"),
    (r"hyprshot -m region|grim.*slurp", "bild", "Screenshot: Bereich"),
    (r"hyprshot -m window", "bild", "Screenshot: aktives Fenster"),
    (r"hyprshot -m output|grim", "bild", "Screenshot: ganzer Bildschirm"),
    (r"wf-recorder", "bild", "Bildschirmaufnahme (scourge) stoppen"),
    (r"wpctl set-volume .*\+", "medien", "Lauter"),
    (r"wpctl set-volume", "medien", "Leiser"),
    (r"wpctl set-mute @DEFAULT_AUDIO_SOURCE@", "medien", "Mikrofon stumm/an"),
    (r"wpctl set-mute", "medien", "Ton stumm/an"),
    (r"brightnessctl.*\+", "medien", "Heller"),
    (r"brightnessctl", "medien", "Dunkler"),
    (r"playerctl next", "medien", "Medien: nächster Titel"),
    (r"playerctl previous", "medien", "Medien: vorheriger Titel"),
    (r"playerctl play-pause", "medien", "Medien: Play/Pause"),
]

SECTIONS = [
    ("apps", "󰀻  apps & starter"),
    ("fenster", "󰖯  fenster"),
    ("flaechen", "󰕰  arbeitsflächen"),
    ("bild", "󰄀  screenshots & aufnahme"),
    ("medien", "󰕾  medien, ton & helligkeit"),
    ("sitzung", "󰌾  sitzung"),
    ("sonst", "󰘳  sonstiges"),
]

# Autostart-Einträge ohne eigenen Kommentar
AUTO_KNOWN = [
    (r"awww img|swww img", "letztes Hintergrundbild wieder setzen"),
    (r"awww-daemon|swww-daemon|hyprpaper", "Hintergrundbild-Dienst"),
    (r"^waybar", "Statusleiste oben"),
    (r"cliphist", "Zwischenablage-Verlauf"),
    (r"hypridle", "Bildschirm aus / sperren bei Inaktivität"),
]

# (Befehl, Beschreibung, darf direkt ausgeführt werden)
USEFUL = [
    ("hyprctl reload", "Konfiguration neu laden (passiert beim Speichern meist von selbst)", True),
    ("hyprctl configerrors", "Fehler in der Konfiguration anzeigen", False),
    ("hyprctl kill", "Fenster per Klick beenden, wenn eins hängt (Esc bricht ab)", True),
    ("hyprctl clients", "Alle Fenster mit Klasse und Titel – für Fensterregeln", False),
    ("hyprctl binds", "Alle aktiven Tastenkürzel (roh)", False),
    ("hyprctl monitors", "Bildschirme: Auflösung, Hz, Skalierung", False),
    ("hyprctl devices", "Tastaturen, Mäuse, Touchpads (Namen für Geräte-Einstellungen)", False),
    ("hyprctl activewindow", "Infos zum gerade aktiven Fenster", False),
    ("hyprpicker -a", "Farbe vom Bildschirm aufnehmen und kopieren", True),
]


def _strip_comment(line):
    """Lua-Kommentar (--) außerhalb von Strings abtrennen → (code, kommentar)."""
    q = None
    i = 0
    while i < len(line):
        c = line[i]
        if q:
            if c == "\\":
                i += 2
                continue
            if c == q:
                q = None
        elif c in "\"'":
            q = c
        elif line.startswith("--", i):
            return line[:i], line[i + 2:].strip()
        i += 1
    return line, ""


def _calls(text, name):
    """Alle Aufrufe name(...) mit vollständigem Argument-Text (auch über mehrere Zeilen) und dem Kommentar
    hinter der schließenden Klammer. Liefert (argumente, kommentar, zeilennummer)."""
    lines = text.splitlines()
    code = []
    comments = []
    for l in lines:
        c, k = _strip_comment(l)
        code.append(c)
        comments.append(k)
    flat = "\n".join(code)
    starts = [0]
    for l in code:
        starts.append(starts[-1] + len(l) + 1)
    out = []
    for m in re.finditer(re.escape(name) + r"\s*\(", flat):
        i = m.end()
        depth, q = 1, None
        while i < len(flat) and depth:
            c = flat[i]
            if q:
                if c == "\\":
                    i += 1
                elif c == q:
                    q = None
            elif c in "\"'":
                q = c
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            i += 1
        args = flat[m.end():i - 1]
        line_no = next(n for n in range(len(starts) - 1) if starts[n + 1] > i - 1)
        start_no = next(n for n in range(len(starts) - 1) if starts[n + 1] > m.start())
        out.append((args, comments[line_no], start_no))
    return out


def _split_top(args):
    """Argumente auf oberster Ebene an Kommas trennen."""
    parts, depth, q, cur = [], 0, None, ""
    for c in args:
        if q:
            cur += c
            if c == q:
                q = None
            continue
        if c in "\"'":
            q = c
        elif c in "({[":
            depth += 1
        elif c in ")}]":
            depth -= 1
        elif c == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
            continue
        cur += c
    if cur.strip():
        parts.append(cur.strip())
    return parts


def _concat(expr, variables, loop_var=None):
    """Lua-Ausdruck aus Strings, Variablen und .. zusammensetzen."""
    out = ""
    for tok in re.split(r"\s*\.\.\s*", expr.strip()):
        tok = tok.strip()
        m = re.fullmatch(r'"([^"]*)"|\'([^\']*)\'', tok)
        if m:
            out += m.group(1) if m.group(1) is not None else m.group(2)
        elif loop_var and tok == loop_var[0]:
            out += loop_var[1]
        elif tok in variables:
            out += variables[tok]
        else:
            out += tok
    return out


def nice_keys(raw):
    parts = [p.strip() for p in raw.split("+") if p.strip()]
    return [KEYNAMES.get(p, KEYNAMES.get(p.upper(), p if len(p) > 1 else p.upper())) for p in parts]


def _ws(v):
    v = v.strip().strip('"\'')
    if v == "e+1":
        return "nächste"
    if v == "e-1":
        return "vorherige"
    if v.startswith("special"):
        return "Ablage (Scratchpad)"
    return v


def describe(action, variables, loop):
    """(rubrik, beschreibung) für den Aktions-Teil eines hl.bind."""
    a = action.strip()
    if a.startswith("function"):
        cmds = re.findall(r'(?:exec_cmd|os\.execute)\(\s*("[^"]*"|\'[^\']*\'|[^)]+)\)', a)
        cmd = " ".join(_concat(c, variables) for c in cmds)
        return _command(cmd)
    m = re.match(r"hl\.dsp\.exec_cmd\((.*)\)\s*$", a, re.S)
    if m:
        return _command(_concat(m.group(1), variables))
    if "window.close" in a:
        return "fenster", "Fenster schließen"
    if "window.float" in a:
        return "fenster", "Fenster schweben lassen / wieder einreihen"
    if "window.pseudo" in a:
        return "fenster", "Pseudo-Kachel (Fenster behält seine Größe im Raster)"
    if "togglesplit" in a:
        return "fenster", "Teilung drehen (nebeneinander ↔ untereinander)"
    if "window.fullscreen" in a:
        return "fenster", "Vollbild ein/aus"
    if "window.drag" in a:
        return "fenster", "Fenster ziehen (gedrückt halten)"
    if "window.resize" in a:
        return "fenster", "Fenstergröße ändern (gedrückt halten)"
    m = re.search(r"direction\s*=\s*\"(\w+)\"", a)
    if m and "focus" in a:
        d = {"left": "links", "right": "rechts", "up": "oben", "down": "unten"}.get(m.group(1), m.group(1))
        return "fenster", f"Fokus nach {d}"
    m = re.search(r"workspace\s*=\s*([^}\s,]+)", a)
    if m:
        val = m.group(1)
        if loop and val.strip() == loop[0]:
            val = loop[2]
        w = _ws(val)
        if "window.move" in a:
            return "flaechen", (f"Fenster in die {w}" if "Ablage" in w else f"Fenster auf Arbeitsfläche {w}")
        if w in ("nächste", "vorherige"):
            return "flaechen", f"{w.capitalize()} Arbeitsfläche"
        return "flaechen", f"Zur Arbeitsfläche {w}"
    if "toggle_special" in a:
        return "flaechen", "Ablage-Arbeitsfläche (Scratchpad) ein/aus"
    short = re.sub(r"\s+", " ", a.replace("hl.dsp.", ""))
    return "sonst", short[:70]


def _command(cmd):
    for pat, sec, text in COMMANDS:
        if re.search(pat, cmd):
            return sec, text
    exe = os.path.basename(cmd.split()[0]) if cmd.split() else cmd
    return "apps", f"{exe} starten"


def parse_binds(text):
    """Alle Tastenkürzel aus der Lua-Konfiguration → Liste von dict(keys, sec, desc, note)."""
    variables = dict(re.findall(r'^\s*local\s+(\w+)\s*=\s*"([^"]*)"', text, re.M))
    # for-Schleifen über Zahlen (Arbeitsflächen 1–10): Zeilenbereiche merken
    loops = []
    lines = text.splitlines()
    for n, l in enumerate(lines):
        m = re.match(r"\s*for\s+(\w+)\s*=\s*(\d+)\s*,\s*(\d+)\s+do", l)
        if m:
            depth, end = 1, n
            for k in range(n + 1, len(lines)):
                code = _strip_comment(lines[k])[0]
                depth += len(re.findall(r"\b(do|function|then)\b", code)) - len(re.findall(r"\bend\b", code))
                if depth <= 0:
                    end = k
                    break
            body = "\n".join(lines[n:end + 1])
            km = re.search(r"local\s+(\w+)\s*=\s*" + m.group(1) + r"\s*%\s*10", body)
            loops.append((n, end, m.group(1), km.group(1) if km else None, m.group(2), m.group(3)))
    out = []
    for args, comment, line_no in _calls(text, "hl.bind"):
        parts = _split_top(args)
        if len(parts) < 2:
            continue
        opts = parts[2] if len(parts) > 2 else ""
        if "non_consuming" in opts and "+" not in parts[0]:
            continue                    # Hilfs-Bindungen (z. B. Waybar-Menüs bei Klick schließen)
        loop = next((lp for lp in loops if lp[0] <= line_no <= lp[1]), None)
        key_loop = loop_info = None
        if loop:
            lo, hi = loop[4], loop[5]
            key_range = f"{int(lo) % 10} … {int(hi) % 10}" if loop[3] else f"{lo} … {hi}"
            key_loop = (loop[3] or loop[2], key_range)
            loop_info = (loop[2], key_range, f"{lo}–{hi}")
        keys = _concat(parts[0], {**variables, "mainMod": variables.get("mainMod", "SUPER")}, key_loop)
        sec, desc = describe(parts[1], variables, loop_info)
        note = comment if comment and comment.lower() not in desc.lower() else ""
        if note in ("dwindle only",):
            note = "nur im dwindle-Layout"
        if "locked" in opts and "true" in opts:
            note = (note + " · " if note else "") + "geht auch bei gesperrtem Bildschirm"
        out.append(dict(keys=nice_keys(keys), sec=sec, desc=desc, note=note))
    return _merge(out)


def _merge(binds):
    """Doppelte entfernen und Zahlenreihen (Super+Umschalt+1 … 0) zu einer Zeile zusammenfassen."""
    groups = {}
    order = []
    for b in binds:
        last = b["keys"][-1] if b["keys"] else ""
        num = re.search(r"\d+", b["desc"])
        if re.fullmatch(r"\d", last) and num:
            g = (tuple(b["keys"][:-1]), b["desc"][:num.start()] + "#" + b["desc"][num.end():], b["sec"])
            if g not in groups:
                groups[g] = []
                order.append(("g", g))
            groups[g].append((last, num.group(0), b))
        else:
            order.append(("b", b))
    out, seen = [], set()
    for kind, item in order:
        if kind == "g":
            members = groups[item]
            if len(members) >= 3:
                nums = sorted(int(n) for _k, n, _b in members)
                b = dict(members[0][2])
                b["keys"] = list(item[0]) + ["1 … 0" if nums[-1] == 10 else f"{nums[0]} … {nums[-1]}"]
                b["desc"] = item[1].replace("#", f"{nums[0]}–{nums[-1]}")
                b["note"] = next((m[2]["note"] for m in members if m[2]["note"]), "")
                cand = [b]
            else:
                cand = [m[2] for m in members]
        else:
            cand = [item]
        for b in cand:
            sig = (tuple(b["keys"]), b["desc"])
            if sig in seen:
                continue
            seen.add(sig)
            out.append(b)
    return out


def parse_gestures(text):
    out = []
    for args, comment, _n in _calls(text, "hl.gesture"):
        f = re.search(r"fingers\s*=\s*(\d+)", args)
        d = re.search(r'direction\s*=\s*"(\w+)"', args)
        a = re.search(r'action\s*=\s*"(\w+)"', args)
        if not f:
            continue
        dirs = {"horizontal": "waagrecht", "vertical": "senkrecht", "left": "nach links", "right": "nach rechts",
                "up": "nach oben", "down": "nach unten", "swipe": "in jede Richtung"}
        acts = {"workspace": "Arbeitsfläche wechseln", "close": "Fenster schließen", "fullscreen": "Vollbild",
                "float": "Fenster schweben lassen", "move": "Fenster verschieben", "resize": "Größe ändern",
                "special": "Ablage-Arbeitsfläche ein/aus", "dispatcher": "eigene Aktion"}
        out.append((f"{f.group(1)} Finger {dirs.get(d.group(1), d.group(1)) if d else ''} wischen".replace("  ", " "),
                    acts.get(a.group(1), a.group(1)) if a else "", comment))
    return out


def parse_autostart(text):
    """exec_cmd-Zeilen im hl.on("hyprland.start")-Block → (programm, befehl, kommentar)."""
    m = re.search(r'hl\.on\(\s*"hyprland\.start"', text)
    if not m:
        return []
    block = text[m.end():]
    end = re.search(r"^end\)", block, re.M)
    block = block[:end.start()] if end else block
    out = []
    for line in block.splitlines():
        code, comment = _strip_comment(line)
        c = re.search(r'exec_cmd\(\s*"((?:[^"\\]|\\.)*)"', code)
        if not c:
            continue
        cmd = c.group(1).replace('\\"', '"')
        skip = ("sh", "-c", "killall", "sleep", "python3", "until", "do", "done", "then", "exec")
        words = [w for w in (x.strip("'\"") for x in re.split(r"[\s;&|]+", cmd))
                 if w and w not in skip and not re.fullmatch(r"[\d.]+", w)]
        prog = os.path.basename(words[0]).replace(".py", "").replace(".sh", "") if words else cmd
        if prog == "wl-paste":
            prog = "cliphist (" + ("Bilder" if "image" in cmd else "Text") + ")"
        if not comment:
            comment = next((t for pat, t in AUTO_KNOWN if re.search(pat, cmd)), "")
        out.append((prog, cmd.replace(HOME, "~"), comment))
    return out


def _sh(argv, timeout=3):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


class HyprlandInfo(ToolPage):
    title = "Hyprland"
    subtitle = ""
    privacy = "Liest nur die Hyprland-Konfiguration und den Zustand über hyprctl. Nichts verlässt das Gerät."

    def build(self):
        global _css
        if _css is None:
            _css = Gtk.CssProvider()
            _css.load_from_string(HYPR_CSS)
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), _css,
                                                      Gtk.STYLE_PROVIDER_PRIORITY_USER)
        try:
            text = open(CONF).read()
        except OSError:
            text = ""
        if not text:
            c = self.card("hyprland")
            n = Gtk.Label(label=f"Keine Konfiguration gefunden ({CONF.replace(HOME, '~')}). Diese Seite liest die "
                                "Lua-Konfiguration von Hyprland ab Version 0.55.", xalign=0, wrap=True)
            n.add_css_class("note")
            c.append(n)
            return

        # --- Status (async: hyprctl) ---
        self.status = self.card("status")
        self.busy(self.status, "frage Hyprland …")
        threading.Thread(target=self._load_status, daemon=True).start()

        # --- Tastenkürzel ---
        c = self.card("tastenkürzel")
        top = Gtk.Box(spacing=8)
        self.search = Gtk.SearchEntry(placeholder_text="suchen: taste oder aktion (z. B. „screenshot“, „Super+M“)",
                                      hexpand=True)
        self.search.add_css_class("pb")
        self.search.connect("search-changed", lambda *_: self._filter())
        top.append(self.search)
        c.append(top)
        tip = Gtk.Label(label="Super = Windows-Taste. Aus deiner hyprland.lua gelesen – neue Kürzel tauchen hier "
                              "automatisch auf. Graue Zusätze sind deine Kommentare aus der Konfiguration.",
                        xalign=0, wrap=True)
        tip.add_css_class("note")
        c.append(tip)
        self.rows = []          # (widget, suchtext, abschnitt)
        self.sec_labels = {}
        binds = parse_binds(text)
        for sec, title in SECTIONS:
            items = [b for b in binds if b["sec"] == sec]
            if not items:
                continue
            h = Gtk.Label(label=title, xalign=0)
            h.add_css_class("hk-sec")
            c.append(h)
            self.sec_labels[sec] = h
            for b in items:
                row = self._bind_row(b)
                c.append(row)
                self.rows.append((row, (" ".join(b["keys"]) + " " + "+".join(b["keys"]) + " " + b["desc"] + " "
                                        + b["note"]).lower(), sec))
        self.empty = Gtk.Label(label="nichts gefunden", xalign=0)
        self.empty.add_css_class("note")
        self.empty.set_visible(False)
        c.append(self.empty)

        # --- Gesten ---
        gest = parse_gestures(text)
        if gest:
            g = self.card("gesten (touchpad)")
            for what, act, comment in gest:
                self._pair(g, what, act, comment)

        # --- Starter-Tipps (rofi), nur wenn der eigene Starter benutzt wird ---
        if "launcher.sh" in text:
            s = self.card("im starter (Super+R)")
            for k, v in (("Tab / Umschalt+Tab", "nächster / vorheriger Reiter (apps, fenster, dateien, rechner, "
                                                 "ablage, zeichen, web, system)"),
                         ("Enter", "starten / übernehmen"), ("Esc oder Super+R", "schließen")):
                self._pair(s, k, v)

        # --- Autostart ---
        auto = parse_autostart(text)
        if auto:
            a = self.card("autostart (beim anmelden)")
            n = Gtk.Label(label="Startet Hyprland beim Anmelden (Block hl.on(\"hyprland.start\") in der "
                                "hyprland.lua). Die eigenen Apps lassen sich unter software → suite-apps ein- und "
                                "ausschalten.", xalign=0, wrap=True)
            n.add_css_class("note")
            a.append(n)
            for prog, cmd, comment in auto:
                row = self._pair(a, prog, comment or cmd)
                row.set_tooltip_text(cmd)

        # --- nützliche Befehle ---
        u = self.card("nützliche befehle")
        for cmd, desc, runnable in USEFUL:
            if not shutil.which(cmd.split()[0]):
                continue
            row = Gtk.Box(spacing=10)
            row.add_css_class("hk-row")
            k = Gtk.Label(label=cmd, xalign=0, width_chars=22, selectable=True)
            k.add_css_class("val")
            d = Gtk.Label(label=desc, xalign=0, hexpand=True, wrap=True)
            d.add_css_class("key")
            row.append(k)
            row.append(d)
            if runnable:
                row.append(self.button("󰐊", lambda c=cmd: self._run(c), "jetzt ausführen", small=True))
            row.append(self.button("󰆏", lambda c=cmd: self._copy(c), "Befehl kopieren", small=True))
            u.append(row)

        # --- Dateien ---
        f = self.card("dateien")
        sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
        run = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
        files = [
            (CONF, "Hauptkonfiguration (Kürzel, Autostart, Aussehen, Eingabe)"),
            (os.path.expanduser("~/.config/hypr/hyprlock.conf"), "Sperrbildschirm (Super+L)"),
            (os.path.expanduser("~/.config/hypr/hypridle.conf"), "Bildschirm aus / sperren bei Inaktivität"),
            (os.path.expanduser("~/.config/hypr/scripts"), "eigene Skripte (Starter …)"),
            (os.path.join(run, "hypr", sig, "hyprland.log") if sig else "", "Protokoll der laufenden Sitzung"),
        ]
        for path, desc in files:
            if not path or not os.path.exists(path):
                continue
            row = Gtk.Box(spacing=10)
            row.add_css_class("hk-row")
            k = Gtk.Label(label=path.replace(HOME, "~"), xalign=0, selectable=True, ellipsize=1, width_chars=22,
                          max_width_chars=40)
            k.add_css_class("val")
            d = Gtk.Label(label=desc, xalign=0, hexpand=True, wrap=True)
            d.add_css_class("key")
            row.append(k)
            row.append(d)
            row.append(self.button("󰏌", lambda p=path: self._open(p), "öffnen", small=True))
            f.append(row)
        wiki = Gtk.LinkButton(uri="https://wiki.hypr.land", label="wiki.hypr.land – Dokumentation",
                              halign=Gtk.Align.START)
        f.append(wiki)

    # ---------- Zeilen ----------
    def _bind_row(self, b):
        row = Gtk.Box(spacing=12)
        row.add_css_class("hk-row")
        keys = Gtk.Box(spacing=3, valign=Gtk.Align.CENTER)
        keys.set_size_request(230, -1)
        for i, k in enumerate(b["keys"]):
            if i:
                p = Gtk.Label(label="+")
                p.add_css_class("hk-plus")
                keys.append(p)
            l = Gtk.Label(label=k)
            l.add_css_class("hk")
            keys.append(l)
        row.append(keys)
        txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        d = Gtk.Label(label=b["desc"], xalign=0, wrap=True)
        d.add_css_class("val")
        txt.append(d)
        if b["note"]:
            n = Gtk.Label(label=b["note"], xalign=0, wrap=True)
            n.add_css_class("note")
            txt.append(n)
        row.append(txt)
        return row

    def _pair(self, parent, key, value, note=""):
        row = Gtk.Box(spacing=12)
        row.add_css_class("hk-row")
        k = Gtk.Label(label=key, xalign=0, width_chars=22, wrap=True, max_width_chars=30)
        k.add_css_class("val")
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        l = Gtk.Label(label=value, xalign=0, wrap=True)
        l.add_css_class("key")
        v.append(l)
        if note:
            n = Gtk.Label(label=note, xalign=0, wrap=True)
            n.add_css_class("note")
            v.append(n)
        row.append(k)
        row.append(v)
        parent.append(row)
        return row

    def _filter(self):
        q = self.search.get_text().strip().lower().replace(" ", "")
        q2 = self.search.get_text().strip().lower()
        shown = set()
        for row, hay, sec in self.rows:
            ok = not q or q in hay.replace(" ", "") or q2 in hay
            row.set_visible(ok)
            if ok:
                shown.add(sec)
        for sec, h in self.sec_labels.items():
            h.set_visible(sec in shown)
        self.empty.set_visible(not shown)

    # ---------- Status ----------
    def _load_status(self):
        info = {}
        ver = _sh(["hyprctl", "version", "-j"])
        try:
            info["version"] = json.loads(ver).get("tag", "")
        except ValueError:
            info["version"] = ""
        try:
            info["monitors"] = json.loads(_sh(["hyprctl", "monitors", "-j"]) or "[]")
        except ValueError:
            info["monitors"] = []
        errs = _sh(["hyprctl", "configerrors"]).strip()
        info["errors"] = [] if not errs or errs.lower().startswith("no errors") else \
            [e for e in errs.splitlines() if e.strip()]
        try:
            info["binds"] = len(json.loads(_sh(["hyprctl", "binds", "-j"]) or "[]"))
        except ValueError:
            info["binds"] = None
        try:
            text = open(CONF).read()
        except OSError:
            text = ""
        lay = re.search(r'^\s*layout\s*=\s*"(\w+)"', text, re.M)
        kb = re.search(r'^\s*kb_layout\s*=\s*"([^"]*)"', text, re.M)
        info["layout"] = lay.group(1) if lay else ""
        info["kb"] = kb.group(1) if kb else ""
        GLib.idle_add(lambda: (self._show_status(info), False)[-1])

    def _show_status(self, info):
        c = self.status
        self.clear(c)
        if not info["version"] and not info["monitors"]:
            n = Gtk.Label(label="Hyprland antwortet nicht (läuft hier ein anderer Desktop?).", xalign=0, wrap=True)
            n.add_css_class("note")
            c.append(n)
            return
        self.line(c, "version", info["version"] or "?")
        for m in info["monitors"]:
            self.line(c, f"bildschirm {m.get('name', '')}",
                      f"{m.get('width')}×{m.get('height')} @ {round(m.get('refreshRate', 0))} Hz · "
                      f"Skalierung {m.get('scale')}")
        if info["layout"]:
            self.line(c, "layout", info["layout"] + (" (Fenster teilen sich den Platz automatisch)"
                                                     if info["layout"] == "dwindle" else ""))
        if info["kb"]:
            self.line(c, "tastatur", info["kb"])
        if info["binds"] is not None:
            self.line(c, "aktive kürzel", str(info["binds"]))
        if info["errors"]:
            self.line(c, "konfig-fehler", "\n".join(info["errors"][:8]), level="high")
        else:
            self.line(c, "konfig-fehler", "keine 󰄬")
        row = Gtk.Box(spacing=8)
        row.append(self.button("󰑐 konfiguration neu laden", lambda: self._run("hyprctl reload"),
                               "hyprctl reload", small=True))
        c.append(row)

    # ---------- Aktionen ----------
    def _copy(self, cmd):
        Gdk.Display.get_default().get_clipboard().set(cmd)
        self.tab.notify(f"kopiert: {cmd}")

    def _run(self, cmd):
        def work():
            try:
                p = subprocess.run(cmd.split(), capture_output=True, text=True, timeout=60)
                out = (p.stdout or p.stderr).strip().splitlines()
                msg = f"{cmd}: {out[-1] if out else 'ok'}"
            except (OSError, subprocess.SubprocessError) as e:
                msg = f"{cmd} ging nicht: {e}"
            GLib.idle_add(lambda: (self.tab.notify(msg), False)[-1])
            if cmd == "hyprctl reload":
                self._load_status()
        if cmd == "hyprctl kill":
            self.tab.notify("Klick auf das Fenster, das beendet werden soll (Esc bricht ab)")
        threading.Thread(target=work, daemon=True).start()

    def _open(self, path):
        try:
            Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(path).get_uri(), None)
        except GLib.Error as e:
            self.tab.notify(f"öffnen ging nicht: {e.message}")
