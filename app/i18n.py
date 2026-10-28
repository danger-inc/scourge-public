# Sprache der Oberfläche. Deutsch ist die Grundsprache im Code; für Englisch hakt sich dieses Modul in GTK ein
# (Beschriftungen, Tooltips, Platzhalter, Fenstertitel, Menüs, Auswahllisten) und übersetzt jeden Text beim Setzen
# über die Tabelle lang/en.json. Zahlen, „Zitate“ und Dateinamen werden dabei als {} behandelt, damit z. B. „3 Bilder“
# und „12 Bilder“ denselben Eintrag nutzen. Gleiche Technik wie in notist.
# Sammelmodus (für Übersetzer): SCOURGE_I18N_COLLECT=datei schreibt alle gesehenen deutschen Texte mit.
import json, os, re

HERE = os.path.dirname(os.path.realpath(__file__))
CONF = os.path.expanduser("~/.config/scourge/settings.json")
KEYS = (("Strg+", "Ctrl+"), ("Umschalt+", "Shift+"), ("+Leertaste", "+Space"), ("+Entf", "+Del"),
        ("Strg ", "Ctrl "), ("Umschalt ", "Shift "))
LANG = "de"
TABLE = {}
_collect = None
_seen = set()

ICON = re.compile(r"^([-\U000f0000-\U000fffff←-⇿☀-➿·•✓✗Ω●○▲▼]+\s*)(.*)$", re.S)
PH = re.compile(r"„[^“]*“|\"[^\"]*\"|[\w.-]+\.(?:tex|pdf|docx|doc|odt|txt|md|html|png|jpe?g|webp|avif|gif|mp4|webm|mp3|zip|svg|json|py)\b"
                r"|\d+(?:[.,:]\d+)*")


def system_lang():
    """Sprache des Systems: Deutsch, sonst Englisch."""
    for var in ("LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG"):
        v = (os.environ.get(var) or "").lower()
        if v and v not in ("c", "posix"):
            return "de" if v.startswith("de") else "en"
    return "en"


def preferred():
    """Gewählte Sprache: Einstellung (Zahnrad → sprache); ohne Einstellung folgt Scourge dem System."""
    env = os.environ.get("SCOURGE_LANG")
    if env in ("de", "en"):
        return env
    try:
        with open(CONF) as f:
            v = json.load(f).get("lang")
    except (OSError, ValueError):
        v = None
    return v if v in ("de", "en") else system_lang()


def setup(lang=None):
    global LANG, TABLE, _collect
    LANG = lang if lang in ("de", "en") else preferred()
    _collect = os.environ.get("SCOURGE_I18N_COLLECT")
    if LANG != "de":
        try:
            with open(os.path.join(HERE, "lang", LANG + ".json"), encoding="utf-8") as f:
                TABLE = json.load(f)
        except (OSError, ValueError):
            TABLE = {}
    if LANG != "de" or _collect:
        install()


def _note(key):
    if _collect and key not in _seen and re.search(r"[A-Za-zÄÖÜäöüß]{2}", key):
        _seen.add(key)
        with open(_collect, "a", encoding="utf-8") as f:
            f.write(json.dumps(key, ensure_ascii=False) + "\n")


_LC = {}


def _lc(s):
    """Tabelle ohne Beachtung der Groß-/Kleinschreibung (Kachelnamen u. Ä. werden klein gezeichnet)."""
    if not _LC and TABLE:
        for k, v in TABLE.items():
            _LC.setdefault(k.lower(), v)
    return _LC.get(s.lower())


def _val(v):
    """Eingesetzte Werte: Zitate („Datei prüfen“) sind oft Namen aus der Oberfläche → mit übersetzen."""
    if v.startswith("„") and v.endswith("“") and len(v) > 2:
        inner = _tr(v[1:-1]) if LANG == "en" else v[1:-1]
        return "\u201c" + inner + "\u201d" if LANG == "en" else v
    return v


def _lookup(s):
    if s in TABLE:
        return TABLE[s]
    vals = []
    key = PH.sub(lambda m: (vals.append(m.group()), "{}")[1], s)
    if vals and key in TABLE:
        out = TABLE[key]
        for v in vals:
            out = out.replace("{}", _val(v), 1)
        return out
    return None


def tr(s, *args):
    """Text übersetzen (bei Deutsch unverändert). Mit args: Vorlage mit {} füllen."""
    if not isinstance(s, str) or not s:
        return s.format(*args) if args and isinstance(s, str) else s
    if LANG == "de" and not _collect:
        return s.format(*args) if args else s
    out = _tr(s)
    if LANG == "en":
        for a, b in KEYS:
            out = out.replace(a, b)
    return out.format(*args) if args else out


def _tr(s):
    if len(s) > 6000:
        return s
    hit = _lookup(s)
    if hit is not None:
        return hit
    m = ICON.match(s)
    if m and m.group(1) and m.group(2):
        return m.group(1) + _tr(m.group(2))
    for sep in ("\n", "  ·  ", " · ", " – ", "  –  "):
        if sep in s:
            parts = s.split(sep)
            if len(parts) > 1:
                return sep.join(_tr(p) for p in parts)
    if ": " in s:
        head, rest = s.split(": ", 1)
        h = _lookup(head)
        if h is not None:
            return h + ": " + _tr(rest)
    if s.startswith("z. B. "):
        return "e.g. " + s[6:]
    m = re.match(r"^(.*\S) (\([^()]*\))$", s, re.S)
    if m:
        h = _lookup(m.group(1))
        if h is not None:
            inner = _lookup(m.group(2)[1:-1])
            return h + " " + ("(" + inner + ")" if inner else m.group(2))
    if s == s.lower():                                  # Kachelnamen u. Ä. sind klein geschrieben: „datei prüfen“ ← „Datei prüfen“
        hit = _lc(s)
        if hit is not None:
            return hit.lower()
    stripped = s.strip()
    if stripped != s and stripped:
        lead = s[:len(s) - len(s.lstrip())]
        trail = s[len(s.rstrip()):]
        return lead + _tr(stripped) + trail
    if _collect:
        _note(PH.sub("{}", s))
    return s


def install():
    import gi
    from gi.repository import Gio, GObject, Gtk
    KW = ("label", "tooltip_text", "placeholder_text", "title")
    orig_init = GObject.Object.__init__

    def init(self, *a, **kw):
        for k in KW:
            v = kw.get(k)
            if isinstance(v, str) and v:
                if k == "title" and not isinstance(self, (Gtk.Window, Gtk.FileDialog, Gtk.FontDialog, Gtk.ColorDialog,
                                                          Gtk.AlertDialog)):
                    continue
                kw[k] = tr(v)
        orig_init(self, *a, **kw)
    GObject.Object.__init__ = init

    def wrap(cls, name, idx=0):
        orig = getattr(cls, name)

        def f(self, *a):
            a = list(a)
            if len(a) > idx and isinstance(a[idx], str):
                a[idx] = tr(a[idx])
            return orig(self, *a)
        setattr(cls, name, f)
    for cls, name in ((Gtk.Label, "set_label"), (Gtk.Label, "set_text"), (Gtk.Label, "set_markup"),
                      (Gtk.Button, "set_label"), (Gtk.Widget, "set_tooltip_text"), (Gtk.Window, "set_title"),
                      (Gtk.Entry, "set_placeholder_text"), (Gtk.Expander, "set_label"), (Gtk.FileFilter, "set_name"),
                      (Gtk.CheckButton, "set_label"), (Gtk.MenuButton, "set_label"), (Gio.Notification, "set_body"),
                      (Gtk.SearchEntry, "set_placeholder_text"), (Gtk.AlertDialog, "set_message"),
                      (Gtk.AlertDialog, "set_detail")):
        wrap(cls, name)
    for name in ("append", "prepend"):
        wrap(Gio.Menu, name)
    orig_sec = Gio.Menu.append_section

    def append_section(self, label, section):
        return orig_sec(self, tr(label) if label else label, section)
    Gio.Menu.append_section = append_section
    orig_item = Gio.MenuItem.new
    Gio.MenuItem.new = staticmethod(lambda label, action: orig_item(tr(label) if label else label, action))
    orig_dd = Gtk.DropDown.new_from_strings
    Gtk.DropDown.new_from_strings = staticmethod(lambda strings: orig_dd([tr(s) for s in strings]))
    orig_sl = Gtk.StringList.new
    Gtk.StringList.new = staticmethod(lambda strings: orig_sl([tr(s) for s in strings]))
    orig_nn = Gio.Notification.new
    Gio.Notification.new = staticmethod(lambda title: orig_nn(tr(title)))
    orig_btn = Gio.Notification.add_button
    Gio.Notification.add_button = lambda self, label, action: orig_btn(self, tr(label), action)
