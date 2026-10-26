#!/usr/bin/env python3
# Scourge: Dateien durchleuchten (Metadaten), Bildschirm aufnehmen, Videos schneiden, Memes bauen. Mit Tabs.
# Farben kommen live aus ~/.cache/matugen/colors.css (wechseln mit Super+W mit).
# Start über Super+R ("Scourge") oder: scourge [datei]
import colorsys, datetime, io, json, math, os, re, subprocess, sys
if os.name == "nt":   # Windows/MSYS2: ffmpeg & Co. liegen neben python.exe – die Startmenü-Verknüpfung setzt kein PATH
    os.environ["PATH"] = os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", "")

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk
from PIL import ExifTags, Image, ImageOps, ImageStat

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))


def load_icon_font():
    """Mitgelieferte Symbolschrift (fonts/notes-icons.ttf) nur für diesen Prozess laden – so fehlen die Icons
    auch auf Rechnern ohne (passende) Nerd Font nicht. Pango ≥ 1.56 kann das selbst, sonst fontconfig."""
    path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "fonts", "notes-icons.ttf")
    if not os.path.exists(path):
        return False
    try:
        gi.require_version("PangoCairo", "1.0")
        from gi.repository import PangoCairo
        fm = PangoCairo.FontMap.get_default()
        if hasattr(fm, "add_font_file"):
            return bool(fm.add_font_file(path))
    except Exception:
        pass
    try:
        import ctypes, ctypes.util
        fc = ctypes.CDLL(ctypes.util.find_library("fontconfig") or "libfontconfig.so.1")
        fc.FcConfigGetCurrent.restype = ctypes.c_void_p
        fc.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        return bool(fc.FcConfigAppFontAddFile(fc.FcConfigGetCurrent(), path.encode()))
    except Exception:
        return False


load_icon_font()
sys.path.insert(1, os.path.expanduser("~/.local/share/rice-apps"))
from editor import EDITOR_CSS, MemeEditor  # noqa: E402
import glass  # noqa: E402
import chrome as ch  # noqa: E402
import meta  # noqa: E402
import formats  # noqa: E402
from viewer import VIEWER_CSS, PdfView  # noqa: E402

APP_ID = "de.invincible.Scourge"
PAGE_TITLES = {"editor": "Meme-Editor", "player": "Öffnen"}
VIEW_PAGES = ("main",)                 # Seiten, die die geöffnete Datei zeigen (außer dem Editor)
FILE_PAGES = VIEW_PAGES + ("editor",)
CONFIG = os.path.expanduser("~/.config/scourge")
MATUGEN_CSS = os.path.expanduser("~/.cache/matugen/colors.css")
LOGO_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "logo")
ICON_DIR = os.path.expanduser("~/.local/share/icons/hicolor")


def open_in_pdf_editor(path):
    """PDF im schlanken Editor scourge-pdf öffnen (eigener Prozess, startet sofort)."""
    import subprocess
    here = os.path.dirname(os.path.realpath(__file__))
    if os.name == "nt":
        cmd = [sys.executable.replace("python.exe", "pythonw.exe"), os.path.join(here, "scourge_pdf.py"), path]
        subprocess.Popen(cmd)
    else:
        subprocess.Popen(["bash", os.path.join(here, "scourge-pdf.sh"), path], start_new_session=True)


def install_logo():
    """Das Logo aus logo/ ins System übernehmen (Starter, Taskleiste, Benachrichtigungen).
    scourge.svg (oder .png) = farbiges App-Icon, scourge-symbolic.svg = einfarbig für die Oberfläche."""
    import shutil
    if any(os.path.isfile(os.path.expanduser(f"~/.config/ist-icons/scourge{e}")) for e in (".svg", ".png")):
        return              # eigenes Icon (scourge → software → suite-apps) nicht überschreiben
    changed = False
    for name, sub in (("scourge.svg", "scalable"), ("scourge-symbolic.svg", "scalable"), ("scourge.png", "256x256")):
        src = os.path.join(LOGO_DIR, name)
        dst = os.path.join(ICON_DIR, sub, "apps", name)
        if not os.path.isfile(src):
            continue
        try:
            if os.path.isfile(dst) and open(src, "rb").read() == open(dst, "rb").read():
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
            changed = True
        except OSError:
            pass
    if os.path.isfile(os.path.join(LOGO_DIR, "scourge.png")) and not os.path.isfile(os.path.join(LOGO_DIR, "scourge.svg")):
        try:  # nur PNG geliefert: altes SVG darf nicht mehr gewinnen
            os.remove(os.path.join(ICON_DIR, "scalable", "apps", "scourge.svg"))
            changed = True
        except OSError:
            pass
    if changed:
        subprocess.Popen(["gtk-update-icon-cache", "-f", "-t", "-q", ICON_DIR],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
    theme.add_search_path(LOGO_DIR)  # falls der Icon-Cache noch nicht neu gebaut ist
    Gtk.Window.set_default_icon_name("scourge")

# Fallback, falls matugen noch nichts erzeugt hat; matugen überschreibt diese Werte
FALLBACK_COLORS = """
@define-color background #1a120d;
@define-color foreground #f0dfd7;
@define-color color9 #ffb68e;
@define-color primary #ffb68e;
@define-color on_primary #532200;
@define-color secondary #e5bfa9;
@define-color tertiary #cdc98f;
@define-color surface_container #271e19;
@define-color outline #9f8d84;
"""

# Suite-Look für alle Seiten (Knöpfe, Eingaben, Kacheln, Flächen) – bewusst NICHT in APP_CSS, weil notist
# APP_CSS als Kopie übernimmt. Wird nach allen Modul-Stilen geladen und gewinnt daher.
SUITE_CSS = """
.pb-window label { font-weight: bold; }
/* Knöpfe: schmal, 1 px, 4 px Rundung */
button.pb, menubutton.pb > button, togglebutton.pb {
    background: none; background-image: none; box-shadow: none; text-shadow: none;
    border: 1px solid alpha(@primary, 0.55); border-radius: 4px; padding: 2px 10px; min-height: 0;
    font-size: 12px; font-weight: bold; color: @foreground; transition: background-color 120ms, color 120ms; }
menubutton.pb { padding: 0; border: none; background: none; }
button.pb:hover, button.pb:focus-visible, menubutton.pb > button:hover, menubutton.pb > button:checked {
    background-color: @primary; color: @on_primary; border-color: @primary; }
button.pb:checked { background-color: alpha(@primary, 0.22); color: @foreground; border-color: @primary; }
button.pb:checked:hover { background-color: @primary; color: @on_primary; }
button.pb:disabled { background: none; border-color: alpha(@primary, 0.2); color: alpha(@foreground, 0.35); }
button.pb.small, button.pb.seg { padding: 1px 8px; min-height: 0; font-size: 12px; border-radius: 4px; }
button.pb.tool { padding: 2px 10px; }
.glass menubutton.pb > button:not(:hover):not(:checked) { background: none; }
button.link > label { color: @primary; font-size: 12px; text-decoration: underline; }
/* Eingabefelder, Auswahllisten, Zahlenfelder, Schalter */
entry.pb, .pb-window entry, .pb-window passwordentry, spinbutton.pb, .pb-window spinbutton, dropdown > button {
    background-color: alpha(@background, 0.45); background-image: none; color: @foreground; box-shadow: none;
    border: 1px solid alpha(@primary, 0.45); border-radius: 4px; min-height: 24px; outline: none; }
entry.pb, .pb-window entry { padding: 0 8px; }
entry.pb:focus-within, .pb-window entry:focus-within, .pb-window passwordentry:focus-within,
.pb-window spinbutton:focus-within, dropdown > button:hover, dropdown > button:checked { border-color: @primary; }
.pb-window entry > text > placeholder, .pb-window passwordentry > text > placeholder { color: alpha(@secondary, 0.8); }
.pb-window spinbutton > button { background: none; border: none; box-shadow: none; color: @primary; border-radius: 4px;
    min-width: 22px; padding: 0 4px; }
.pb-window spinbutton > button:hover { background-color: alpha(@primary, 0.16); }
dropdown > button { padding: 0 8px; }
dropdown > button arrow { color: @primary; }
dropdown popover > contents, popover.formats > contents, .pb-window popover.menu > contents {
    background-color: alpha(@background, 0.94); background-image: none; border: 1px solid @primary; border-radius: 6px;
    padding: 4px; box-shadow: 0 8px 22px rgba(0,0,0,0.3); color: @foreground; }
dropdown popover listview > row { border-radius: 4px; padding: 3px 10px; color: @foreground; }
dropdown popover listview > row:hover { background-color: alpha(@primary, 0.14); }
dropdown popover listview > row:selected { background-color: alpha(@primary, 0.24); color: @foreground; }
switch { background-color: alpha(@foreground, 0.1); border: 1px solid alpha(@primary, 0.45); border-radius: 4px;
    min-height: 18px; }
switch:checked, switch:checked:hover { background-color: @primary; border-color: @primary; }
switch > slider { border-radius: 2px; min-width: 14px; min-height: 14px; margin: 2px; }
checkbutton check { border-radius: 3px; border: 1px solid alpha(@primary, 0.55); background: none; }
checkbutton check:checked { background-color: @primary; color: @on_primary; }
progressbar.pb trough { min-height: 4px; border-radius: 2px; }
progressbar.pb progress { min-height: 4px; border-radius: 2px; background-color: @primary; }
/* Startseite */
.home { margin: 4px 14px 10px 14px; }
.home-sub { font-size: 12px; color: @secondary; }
button.home-card {
    background: none; background-image: none; box-shadow: none; border: 1px solid alpha(@primary, 0.25);
    border-radius: 6px; padding: 10px 12px; color: @foreground;
    transition: border-color 120ms, background-color 120ms; }
button.home-card:hover, button.home-card:focus-visible { border-color: @primary; background-color: alpha(@primary, 0.08); }
button.home-card.primary { border-color: alpha(@primary, 0.55); }
.glass button.home-card:not(:hover) { background: none; }
.card-icon { font-size: 18px; color: @primary; }
.card-name { font-size: 13px; font-weight: bold; }
.card-desc { font-size: 11px; color: @secondary; font-weight: normal; }
button.recent { border-radius: 4px; padding: 3px 8px; }
button.recent:hover { background-color: alpha(@primary, 0.12); }
.recent-path { font-size: 10px; color: @secondary; font-weight: normal; }
button.recent-x { border-radius: 4px; }
/* Cursor-Vorschau (System → Cursor) */
.curtile { border: 1px solid alpha(@primary, 0.25); border-radius: 4px; padding: 8px 4px 4px; transition: border-color 120ms; }
.curtile:hover { border-color: @primary; background-color: alpha(@primary, 0.08); }
.curtile.light { background-color: #e9ecef; }
.curtile.light .key { color: #3a4246; }
.home.drag-over { background-color: alpha(@primary, 0.06); border-radius: 6px; }
/* Überschriften und Texte */
.tab-heading { font-size: 14px; font-weight: bold; }
.editor-title { font-size: 15px; font-weight: bold; color: @primary; }
.card-title { font-size: 12px; font-weight: bold; letter-spacing: 0; color: @primary; }
.key { font-size: 12px; color: @secondary; font-weight: normal; }
.val { font-size: 12px; font-weight: bold; }
.note, .key-hint, .hits, .studio-empty, .empty-hint, .finding-detail { color: @secondary; font-weight: normal; }
.app-sub { font-size: 11px; color: @secondary; font-weight: normal; }
.hex { font-size: 20px; }
.empty-icon { font-size: 40px; color: @primary; }
.empty-title { font-size: 14px; font-weight: bold; }
.studio-val { color: @primary; }
/* Flächen: 1 px Rahmen, 6 px Rundung, kein Vollton-Hintergrund */
.dropzone { background: none; border: 1px dashed alpha(@primary, 0.45); border-radius: 6px; margin: 4px 0 10px 14px; }
.dropzone.loaded { border-style: solid; border-color: alpha(@primary, 0.45); background: none; }
.dropzone.drag-over { border-style: solid; border-color: @primary; background-color: alpha(@primary, 0.08); }
.canvas-frame { background: none; border: 1px solid alpha(@primary, 0.45); border-radius: 6px; margin: 0 0 10px 14px; }
.studio-preview { background: none; border: 1px solid alpha(@primary, 0.45); border-radius: 6px; margin: 0 14px 10px 6px; }
.studio-preview.drag-over { border-color: @primary; background-color: alpha(@primary, 0.08); }
.studio-pic { border-radius: 4px; }
.timeline-frame, .pdf-bg { background-color: alpha(black, 0.18); border-radius: 6px; }
.seg-row { border-radius: 4px; }
.summary { border-radius: 4px; }
.vbadge { border-radius: 4px; }
button.dot, button.swatch { border-radius: 4px; border-width: 1px; }
button.swatch.active { border: 2px solid @primary; }
button.dot.active { border-color: @primary; }
button.stop, button.palette { border-radius: 4px; border-width: 1px; }
.ed-bar { margin: 6px 14px 0 14px; }
.studio-top { margin: 6px 14px 0 14px; }
.studio-side { margin: 0 0 10px 14px; }
.countdown { color: @primary; }
"""


# Kalender-/musicalist-Look für Tabs, Kopf- und Statuszeile (nach allen anderen Stilen geladen)
CHROME_CSS = """
notebook.tabs > header { padding: 10px 14px 4px 14px; }
notebook.tabs > header > tabs > tab {
    background: none; background-image: none; border: none; box-shadow: none; border-radius: 4px;
    padding: 2px 6px 2px 9px; min-height: 0; color: alpha(@foreground, 0.8); font-weight: bold; font-size: 13px; }
notebook.tabs > header > tabs > tab .key { color: @primary; }
notebook.tabs > header > tabs > tab:hover { background-color: alpha(@primary, 0.12); color: @foreground; }
notebook.tabs > header > tabs > tab:checked { background-color: alpha(@primary, 0.22); color: @foreground; }
notebook.tabs > header > tabs > tab:checked .key { color: @foreground; }
.glass notebook.tabs > header > tabs > tab:not(:checked) { background: none; }
notebook.tabs > header > tabs > tab button.tab-close { padding: 0 4px; border-radius: 4px; color: alpha(@foreground, 0.55);
    font-size: 11px; }
notebook.tabs > header > tabs > tab button.tab-close:hover { background-color: @tertiary; color: @background; }
notebook.tabs > header button.brand label { font-size: 13px; font-weight: bold; }
notebook.tabs > header button.tbtn, notebook.tabs > header menubutton.tbtn > button { font-size: 13px; font-weight: bold; }
notebook.tabs > header button.pb.glass-toggle { background: none; background-image: none; box-shadow: none; min-height: 0;
    min-width: 0; border: 1px solid alpha(@primary, 0.55); border-radius: 4px; padding: 1px 8px; color: @foreground; }
notebook.tabs > header button.pb.glass-toggle:checked { background-color: alpha(@primary, 0.22); }
notebook.tabs > header button.pb.glass-toggle:hover { background-color: @primary; color: @on_primary; border-color: @primary; }
notebook.tabs > header > arrow { color: @primary; min-width: 18px; }
.tabbar { padding: 6px 14px 2px 14px; }
.tabbar button.tbtn { font-weight: bold; font-size: 13px; }
.tstatus .msg { color: @foreground; }
"""


APP_CSS = """
/* GTKs Standard-Akzent (sonst Adwaita-Blau) auf das Theme ziehen – sonst zeichnet GTK
   Fokusringe und Tab-Schatten immer blau, egal welches Matugen-Theme aktiv ist. */
@define-color accent_color @color9;
@define-color accent_bg_color @color9;
@define-color accent_fg_color @background;
@define-color theme_selected_bg_color @color9;
@define-color theme_selected_fg_color @background;

.pb-window { background-color: @background; color: @foreground; }
.pb-window * { font-family: "JetBrainsMono Nerd Font", "JetBrains Mono", monospace; }

/* Schalter aufs Theme ziehen (sonst Adwaita-Blau im "an"-Zustand). */
switch { background-color: alpha(@foreground, 0.18); border: none; box-shadow: none; }
switch:checked { background-color: @color9; }
switch:hover { background-color: alpha(@foreground, 0.28); }
switch:checked:hover { background-color: @color9; }
switch > slider { background-color: @foreground; box-shadow: none; border: none; }
switch:checked > slider { background-color: @background; }
switch:focus-visible { outline: none; box-shadow: none; }

.topbar { padding: 14px 16px 6px 16px; }
.app-title { font-size: 17px; font-weight: 800; color: @color9; }
.app-logo { color: @color9; }
.app-sub { font-size: 12px; color: alpha(@foreground, 0.55); }

button.pb {
    background-color: @background; background-image: none; box-shadow: none;
    color: @foreground; border: 2px solid @color9; border-radius: 15px;
    padding: 4px 14px; min-height: 26px; font-weight: 800;
    transition: all 200ms cubic-bezier(0.4, 0, 0.2, 1);
}
button.pb:hover, button.pb:focus-visible { background-color: @color9; color: @background; }
button.pb:disabled { border-color: alpha(@color9, 0.3); color: alpha(@foreground, 0.35); background-color: @background; }
button.pb.small { padding: 0 10px; min-height: 22px; font-size: 12px; border-radius: 11px; }

.dropzone {
    background-color: alpha(@surface_container, 0.6);
    border: 2px dashed alpha(@color9, 0.45); border-radius: 15px;
    margin: 6px 0 16px 16px;
}
.dropzone.loaded { border-style: solid; border-color: @color9; background-color: @surface_container; }
.dropzone.drag-over { border-style: solid; border-color: @color9; background-color: alpha(@color9, 0.12); }
.empty-icon { font-size: 54px; color: @color9; }
.empty-title { font-size: 16px; font-weight: 800; }
.empty-hint { font-size: 12px; color: alpha(@foreground, 0.6); }
.error { color: #ffb4ab; font-size: 12px; }

.card {
    background-color: @surface_container; border: 2px solid alpha(@color9, 0.35);
    border-radius: 15px; padding: 12px 14px;
}
.card-title { font-size: 11px; font-weight: 800; letter-spacing: 2px; color: @color9; }
.key { font-size: 12px; color: alpha(@foreground, 0.55); }
.val { font-size: 12px; font-weight: 600; }
.hex { font-size: 22px; font-weight: 800; }
.note { font-size: 11px; color: alpha(@foreground, 0.5); }
button.dot {
    min-width: 24px; min-height: 24px; padding: 0; border-radius: 999px;
    border: 2px solid alpha(@foreground, 0.25); background: none; box-shadow: none;
}
button.dot:hover { border-color: @foreground; }
button.dot.active { border-color: @color9; }
popover.formats > contents { background-color: @background; border: 2px solid @color9; border-radius: 15px; padding: 4px; }
menubutton.pb > button { background-color: @background; background-image: none; box-shadow: none; color: @foreground;
    border: 2px solid @color9; border-radius: 15px; padding: 4px 12px; min-height: 26px; font-weight: 800; }
menubutton.pb > button:hover, menubutton.pb > button:checked { background-color: @color9; color: @background; }
.glass menubutton.pb > button:not(:hover):not(:checked) { background-color: alpha(@background, 0.3); }
.doc-text, .doc-text text { background: none; color: @foreground; font-size: 12px; }
.vbadge {
    background-color: alpha(@background, 0.85); color: @foreground; border-radius: 10px;
    padding: 3px 10px; margin: 26px; font-size: 12px; font-weight: 800;
}
.toast {
    background-color: @color9; color: @background; border-radius: 11px;
    padding: 2px 10px; font-size: 12px; font-weight: 800;
}
.editor-title { font-size: 20px; font-weight: 800; color: @color9; }
"""

MODES = {
    "1": "Schwarz-Weiß (1 Bit)", "L": "Graustufen (8 Bit)", "LA": "Graustufen + Transparenz",
    "P": "Palette (indiziert)", "PA": "Palette + Transparenz", "RGB": "RGB, 8 Bit pro Kanal",
    "RGBA": "RGB + Transparenz", "CMYK": "CMYK (Druckfarben)", "YCbCr": "YCbCr",
    "I;16": "Graustufen (16 Bit)", "I": "Graustufen (32 Bit)", "F": "Gleitkomma (32 Bit)",
}

ORIENTATION = {1: "normal", 2: "gespiegelt", 3: "180° gedreht", 4: "vertikal gespiegelt",
               5: "gespiegelt + 90°", 6: "90° im Uhrzeigersinn", 7: "gespiegelt + 270°", 8: "90° gegen Uhrzeigersinn"}
EXPOSURE_PROGRAM = {1: "manuell", 2: "Programmautomatik", 3: "Zeitautomatik (Blende vorgewählt)",
                    4: "Blendenautomatik (Zeit vorgewählt)", 5: "Kreativprogramm", 6: "Actionprogramm",
                    7: "Porträt", 8: "Landschaft"}

def human_size(n):
    for unit in ("Byte", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n} {unit}" if unit == "Byte" else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024


def fmt_num(x, digits=2):
    s = f"{x:.{digits}f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


def hex_of(rgb):
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _shrink(img, limit):
    """Schnell verkleinern (ganzzahlig per reduce statt Lanczos über das volle Bild)."""
    f = max(1, -(-max(img.size) // limit))
    if f == 1:
        return img
    try:
        return img.reduce(f)
    except (ValueError, NotImplementedError):
        return img.convert("RGBA").reduce(f)


def accent_fallback(img):
    """Kräftige Hauptfarben selbst berechnet, falls matugen fehlt: häufig UND farbig gewinnt."""
    im = _shrink(img, 320).convert("RGBA")
    im.thumbnail((160, 160))
    if ImageStat.Stat(im.getchannel("A")).mean[0] < 0.5:
        return []
    rgb = im.convert("RGB")
    q = rgb.quantize(16, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()
    scored = []
    for count, idx in q.getcolors():
        r, g, b = pal[idx * 3:idx * 3 + 3]
        h, sat, val = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        score = count * (0.15 + sat) * (0.3 if val < 0.18 or val > 0.97 else 1)
        scored.append((score, (r, g, b)))
    scored.sort(reverse=True)
    return [hex_of(c) for _, c in scored[:4]]


def exif_text(v):
    if isinstance(v, bytes):
        v = v.rstrip(b"\0").decode("utf-8", "ignore")
    v = str(v).strip().rstrip("\0").strip()
    return v if v and v.isprintable() else None


def exif_date(v, offset=None):
    try:
        d = datetime.datetime.strptime(str(v).strip(), "%Y:%m:%d %H:%M:%S").strftime("%d.%m.%Y %H:%M:%S")
        return d + (f"  (UTC{offset})" if offset else "")
    except ValueError:
        return exif_text(v)


def gps_to_text(gps):
    try:
        def deg(vals, ref):
            d = float(vals[0]) + float(vals[1]) / 60 + float(vals[2]) / 3600
            return -d if ref in ("S", "W") else d
        lat, lon = deg(gps[2], gps[1]), deg(gps[4], gps[3])
        alt = f", {fmt_num(float(gps[6]), 0)} m Höhe" if 6 in gps else ""
        return f"{lat:.5f}, {lon:.5f}{alt}"
    except Exception:
        return None


def read_metadata(img):
    """Lesbare Auswahl der Metadaten, sortiert wie in einer Fotoübersicht."""
    rows = []
    try:
        exif = img.getexif()
        e = dict(exif)
        e.update(exif.get_ifd(ExifTags.IFD.Exif))
    except Exception:
        exif, e = None, {}
    t = {ExifTags.TAGS.get(k, k): v for k, v in e.items()}

    def add(label, value):
        if value not in (None, ""):
            rows.append((label, value))

    make, model = exif_text(t.get("Make", "")), exif_text(t.get("Model", ""))
    if model:
        add("Kamera", model if make and model.lower().startswith(make.lower()) else f"{make or ''} {model}".strip())
    elif make:
        add("Kamera", make)
    add("Objektiv", exif_text(t.get("LensModel", "")))
    if "DateTimeOriginal" in t:
        add("Aufgenommen", exif_date(t["DateTimeOriginal"], exif_text(t.get("OffsetTimeOriginal", ""))))
    elif "DateTime" in t:
        add("Datum (EXIF)", exif_date(t["DateTime"]))

    if "ExposureTime" in t:
        f = float(t["ExposureTime"])
        add("Belichtungszeit", f"1/{round(1 / f)} s" if 0 < f < 1 else f"{fmt_num(f)} s")
    if "FNumber" in t:
        add("Blende", f"f/{fmt_num(float(t['FNumber']), 1)}")
    iso = t.get("ISOSpeedRatings") or t.get("PhotographicSensitivity")
    if iso:
        add("ISO", str(iso[0] if isinstance(iso, tuple) else iso))
    if "FocalLength" in t:
        kb = t.get("FocalLengthIn35mmFilm")
        add("Brennweite", f"{fmt_num(float(t['FocalLength']), 1)} mm" + (f"  (entspricht {kb} mm)" if kb else ""))
    if t.get("ExposureBiasValue") not in (None, 0):
        add("Belichtungskorrektur", f"{float(t['ExposureBiasValue']):+.1f} EV".replace(".", ","))
    add("Belichtungsprogramm", EXPOSURE_PROGRAM.get(t.get("ExposureProgram")))
    if "Flash" in t:
        add("Blitz", "ausgelöst" if int(t["Flash"]) & 1 else "nicht ausgelöst")
    if "WhiteBalance" in t:
        add("Weißabgleich", "manuell" if t["WhiteBalance"] == 1 else "automatisch")
    zoom = t.get("DigitalZoomRatio")
    if zoom and float(zoom) > 1:
        add("Digitalzoom", f"{fmt_num(float(zoom), 1)}×")
    if t.get("Orientation", 1) != 1:
        add("Ausrichtung", ORIENTATION.get(t["Orientation"]))

    if exif is not None:
        try:
            gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
            add("GPS-Position", gps_to_text(gps) if gps else None)
        except Exception:
            pass

    add("Software", exif_text(t.get("Software", "")))
    add("Urheber", exif_text(t.get("Artist", "")))
    add("Copyright", exif_text(t.get("Copyright", "")))
    add("Beschreibung", exif_text(t.get("ImageDescription", "")))

    # Text-Felder z. B. aus PNGs (Software, Kommentar, KI-Prompt ...); Zahlen sind meist interne Werte
    for key, v in img.info.items():
        if isinstance(v, str) and key not in ("xmp", "XML:com.adobe.xmp") and v.strip():
            s = v.strip()
            add(str(key), s[:300] + "…" if len(s) > 300 else s)

    icc = img.info.get("icc_profile")
    if icc:
        name = "vorhanden"
        try:
            from PIL import ImageCms
            name = ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc))).strip() or name
        except Exception:
            pass
        add("Farbprofil", name)
    if "xmp" in img.info or "XML:com.adobe.xmp" in img.info:
        add("XMP-Daten", "vorhanden")
    return rows


FORMAT_LIST = [
    ("Bilder", "JPG, PNG, WebP, GIF, BMP, TIFF, ICO u. a.",
     "Akzentfarbe, EXIF (GPS, Kamera, Seriennummern, Namen), Motion-Photo-Video, Vorschaubild, Meme-Editor"),
    ("Videos", "MP4, MOV, M4V, MKV, WebM, AVI, 3GP, MTS, WMV, FLV",
     "Codecs, GPS, Gerät, Aufnahmezeit, Tonspur, Meme-Editor mit Export als MP4/GIF"),
    ("PDF", "PDF", "Viewer mit Suche, Anmerkungen (markieren, Text, Stift, Notizen), Autor/Programm/Zeiten"),
    ("Microsoft Office", "DOCX, DOCM, DOTX, XLSX, XLSM, PPTX, PPTM", "Autor/Firma/Zeiten und weitere Metadaten"),
    ("LibreOffice / OpenDocument", "ODT, ODS, ODP, ODG", "Autor, Zeiten und weitere Metadaten"),
    ("E-Books", "EPUB", "Buch-Metadaten, Bilder"),
    ("Audio", "MP3, M4A, AAC, FLAC, OGG, OPUS, WAV, WMA, AIFF, AMR", "Wellenform und Abspielen, Tags, Cover"),
    ("Archive", "ZIP, JAR, APK (+ 7Z, RAR, TAR, GZ nur lesen)", "Dateiliste"),
    ("Text & Daten", "TXT, MD, CSV, JSON, YAML, XML, HTML, SVG, LOG, INI, Code, GPX, KML, VCF, ICS, EML",
     "Vorschau und Metadaten"),
    ("Alle anderen Dateien", "auch alte DOC/XLS/PPT, Programme, Unbekanntes", "Lesbare Texte, Fingerabdruck"),
]

VIDEO_EXT = (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".3gp", ".mts", ".wmv", ".flv")


def fmt_duration(sec):
    sec = int(round(sec))
    return f"{sec // 3600}:{sec // 60 % 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


def probe_video(path):
    """Alle Video-Infos über ffprobe. Maße so, wie das Video angezeigt wird (Handy-Drehung berücksichtigt)."""
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                         capture_output=True, text=True, timeout=30)
    d = json.loads(out.stdout or "{}")
    streams = d.get("streams", [])
    v = next((x for x in streams if x.get("codec_type") == "video"
              and not x.get("disposition", {}).get("attached_pic")), None)
    if not v:
        raise ValueError("keine Videospur")
    a = next((x for x in streams if x.get("codec_type") == "audio"), None)
    rot = 0
    for sd in v.get("side_data_list", []):
        if "rotation" in sd:
            rot = int(sd["rotation"])
    rot = rot or int(v.get("tags", {}).get("rotate", 0) or 0)
    w, h = v["width"], v["height"]
    if abs(rot) % 180 == 90:
        w, h = h, w
    num, _, den = v.get("avg_frame_rate", "0/1").partition("/")
    fps = float(num) / float(den) if float(den or 0) else 0
    fmt = d.get("format", {})
    tags = {k.lower(): val for k, val in {**fmt.get("tags", {}), **v.get("tags", {})}.items()}
    return {"w": w, "h": h, "rotation": rot, "fps": fps, "duration": float(fmt.get("duration") or v.get("duration") or 0),
            "bitrate": int(fmt.get("bit_rate") or 0), "format": fmt.get("format_long_name", "?"),
            "vcodec": v.get("codec_long_name", v.get("codec_name", "?")), "profile": v.get("profile"),
            "audio": a, "tags": tags}


def video_frame(path, t, keyframe=False):
    """Ein Standbild (ffmpeg dreht Handyvideos dabei automatisch richtig). BMP statt PNG: bei 4K ~3× schneller.
    keyframe=True nimmt das nächste Schlüsselbild (noch schneller, für Farbproben genau genug)."""
    pre = ["-skip_frame", "nokey"] if keyframe else []
    raw = subprocess.run(["ffmpeg", "-v", "error", *pre, "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1", "-an", "-sn",
                          "-f", "image2pipe", "-vcodec", "bmp", "-"], capture_output=True, timeout=30).stdout
    im = Image.open(io.BytesIO(raw))
    im.load()
    return im


def video_metadata(info):
    t = info["tags"]
    rows = []
    make = t.get("com.apple.quicktime.make") or t.get("com.android.manufacturer") or t.get("make")
    model = t.get("com.apple.quicktime.model") or t.get("com.android.model") or t.get("model")
    if make or model:
        rows.append(("Kamera", model if make and model and model.lower().startswith(make.lower())
                     else " ".join(x for x in (make, model) if x)))
    when = t.get("com.apple.quicktime.creationdate") or t.get("creation_time")
    if when:
        try:
            dt = datetime.datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone()
            rows.append(("Aufgenommen", dt.strftime("%d.%m.%Y %H:%M:%S")))
        except ValueError:
            rows.append(("Aufgenommen", when))
    loc = t.get("com.apple.quicktime.location.iso6709") or t.get("location") or t.get("location-eng")
    m = re.match(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)?", loc or "")
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        alt = f", {fmt_num(float(m.group(3)), 0)} m Höhe" if m.group(3) else ""
        rows.append(("GPS-Position", f"{lat:.5f}, {lon:.5f}{alt}"))
    if info["rotation"]:
        rows.append(("Ausrichtung", f"gedreht um {abs(info['rotation'])}° (Handy hochkant)"))
    sw = t.get("com.apple.quicktime.software") or t.get("encoder")
    if sw:
        rows.append(("Software", sw))
    for key, label in (("title", "Titel"), ("artist", "Urheber"), ("comment", "Kommentar")):
        if t.get(key):
            rows.append((label, t[key][:200]))
    return rows


_TRANSPOSE = {2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180, 4: Image.Transpose.FLIP_TOP_BOTTOM,
              5: Image.Transpose.TRANSPOSE, 6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE,
              8: Image.Transpose.ROTATE_90}


def pil_to_texture(img, limit=2400):
    """PIL-Bild -> Gdk.Texture: erst verkleinern, dann drehen (EXIF), Pixel direkt übergeben (kein PNG-Umweg)."""
    try:
        orient = img.getexif().get(0x0112)
    except Exception:
        orient = None
    prev = _shrink(img, limit)
    if orient in _TRANSPOSE:
        prev = prev.transpose(_TRANSPOSE[orient])
    if prev.mode not in ("RGB", "RGBA"):
        prev = prev.convert("RGBA")
    w, h = prev.size
    fmt, bpp = (Gdk.MemoryFormat.R8G8B8, 3) if prev.mode == "RGB" else (Gdk.MemoryFormat.R8G8B8A8, 4)
    return Gdk.MemoryTexture.new(w, h, fmt, GLib.Bytes.new(prev.tobytes()), w * bpp)


def formats_popover():
    pop = Gtk.Popover()
    pop.add_css_class("formats")
    sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True, max_content_height=620)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=10, margin_bottom=10, margin_start=12, margin_end=12)
    box.set_size_request(400, -1)
    t = Gtk.Label(label="Was Scourge prüfen kann", xalign=0)
    t.add_css_class("card-title")
    box.append(t)
    for title, exts, what in FORMAT_LIST:
        h = Gtk.Label(label=title, xalign=0)
        h.add_css_class("finding-title")
        e = Gtk.Label(label=exts, xalign=0, wrap=True, max_width_chars=48)
        e.add_css_class("key")
        w = Gtk.Label(label=what, xalign=0, wrap=True, max_width_chars=48)
        w.add_css_class("finding-detail")
        for x in (h, e, w):
            box.append(x)
    sc.set_child(box)
    pop.set_child(sc)
    return pop


class Tab(Gtk.Box):
    """Ein Tab: Startseite, Datei-Ansicht, Meme-Editor, Prüf-Seiten, Aufnahme, Schnitt und Werkzeuge.
    Jeder Tab hat seinen eigenen Zustand; das Fenster (Scourge) leitet Kurzbefehle an den aktiven Tab weiter."""

    PAGE_TITLES = {"home": "Start", "record": "Aufnahme"}

    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.app_main = app
        self.win = app.win
        self.image = None       # aktuelles PIL-Bild (auch Standbild eines Videos)
        self.source = None      # Pfad oder None (Zwischenablage)
        self.title = "Start"
        self.pages = {}         # nachgeladene Seiten: Aufnahme, Schnitt, Werkzeuge
        self.build()

    def notify(self, msg):
        self.app_main.notify(msg)

    # ---------- Aufbau ----------
    def build(self):
        # Leiste des Tabs: Start, Titel, dann die Werkzeuge für die geöffnete Datei
        bar = Gtk.Box(spacing=8)
        bar.add_css_class("tabbar")
        self.home_btn = Gtk.Button(label="󰋜", tooltip_text="Zur Startseite dieses Tabs (Alt+Pos1)", valign=Gtk.Align.CENTER)
        self.home_btn.add_css_class("tbtn")
        self.home_btn.connect("clicked", lambda *_: self.go_home())
        bar.append(self.home_btn)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        self.heading = Gtk.Label(label="Start", xalign=0, ellipsize=3)
        self.heading.add_css_class("tab-heading")
        self.subtitle = Gtk.Label(label="", xalign=0, ellipsize=3)
        self.subtitle.add_css_class("app-sub")
        titles.append(self.heading)
        titles.append(self.subtitle)
        bar.append(titles)
        self.file_btns = []
        btns = [("󰉋  Öffnen", "win.open", "Datei öffnen (Strg+O)"), ("󰏫  Bearbeiten", "win.edit", "Meme-Editor (Strg+E)")]
        for label, action, tip in btns:
            b = Gtk.Button(label=label.replace("  ", " ").lower(), action_name=action, tooltip_text=tip,
                           valign=Gtk.Align.CENTER)
            b.add_css_class("tbtn")
            bar.append(b)
            self.file_btns.append(b)
        self.append(bar)
        self.bar = bar

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True,
                               hhomogeneous=False)  # breiteste Seite soll nicht das ganze Fenster aufblähen
        try:                # Zwei Finger nach rechts / Maustaste „zurück“ = eine Ebene zurück (wie filist)
            import swipe
            self.swipe = swipe.SwipeBack(self.stack, self.can_back, self.go_back, self._swipe_blocked,
                                         where=self.swipe_where, target=self.swipe_target,
                                         goto=self.swipe_goto)
            self.append(self.swipe.widget)
        except Exception:
            self.swipe = None
            self.append(self.stack)
        from home import HomePage
        self.home = HomePage(self)
        self.stack.add_named(self.home, "home")

        # --- Hauptseite ---
        main = Gtk.Box(spacing=16)
        self.stack.add_named(main, "main")

        self.drop = Gtk.Overlay(hexpand=True, vexpand=True)
        self.drop.add_css_class("dropzone")
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True)
        self.text_view = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                      monospace=True, top_margin=6, left_margin=6, right_margin=6)
        self.text_view.add_css_class("doc-text")
        tscroll = Gtk.ScrolledWindow(child=self.text_view)
        # hhomogeneous=False: nur die sichtbare Ansicht zählt (sonst bremst die PDF-Leiste das Verkleinern)
        self.preview_stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE,
                                       hhomogeneous=False, interpolate_size=False)
        self.preview_stack.add_named(self.picture, "pic")
        self.preview_stack.add_named(tscroll, "text")
        self.pdf_view = PdfView(editable=False, notify=self.notify, on_saved=self.on_pdf_saved)   # Bearbeiten: scourge-pdf
        self.preview_stack.add_named(self.pdf_view, "pdf")
        self.info_btn = Gtk.ToggleButton(label="󰋽", active=True, focus_on_click=False,
                                         tooltip_text="Infospalte ein/aus – mehr Platz fürs PDF")
        self.info_btn.add_css_class("pb"); self.info_btn.add_css_class("small")
        self.info_btn.connect("toggled", lambda b: self.info_scroll.set_visible(b.get_active()))
        self.pdf_view.add_to_bar(self.info_btn)
        edit_btn = Gtk.Button(label="󰏫 in scourge-pdf bearbeiten", focus_on_click=False,
                              tooltip_text="Anmerkungen, Text, Markieren: im schlanken PDF-Editor öffnen")
        edit_btn.add_css_class("pb"); edit_btn.add_css_class("small")
        edit_btn.connect("clicked", lambda *_: self.source and open_in_pdf_editor(self.source))
        self.pdf_view.add_to_bar(edit_btn)
        # Arbeitsfläche: PDF + Werkzeuge füllen das ganze Fenster (Kopf-/Statuszeile, Tab-Leiste, Infospalte weg)
        self.focused = False
        self.focus_btn = Gtk.ToggleButton(label="󰊓", focus_on_click=False,
                                          tooltip_text="Arbeitsfläche: PDF im ganzen Fenster (F11, Esc beendet)")
        self.focus_btn.add_css_class("pb"); self.focus_btn.add_css_class("small")
        self.focus_btn.connect("toggled", lambda b: self.set_focus_mode(b.get_active()))
        self.pdf_view.add_to_bar(self.focus_btn)
        esc = Gtk.EventControllerKey()
        esc.connect("key-pressed", self._tab_key)
        self.add_controller(esc)
        self.page_bar = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER, visible=False)
        self.prev_btn = Gtk.Button(label="󰅁", tooltip_text="Vorherige Seite (Bild ↑)")
        self.next_btn = Gtk.Button(label="󰅂", tooltip_text="Nächste Seite (Bild ↓)")
        self.page_label = Gtk.Label(label="")
        self.page_label.add_css_class("key")
        for b, d in ((self.prev_btn, -1), (self.next_btn, 1)):
            b.add_css_class("pb"); b.add_css_class("small")
            b.connect("clicked", lambda *_, d=d: self.turn_page(d))
        self.page_bar.append(self.prev_btn); self.page_bar.append(self.page_label); self.page_bar.append(self.next_btn)
        from player import MediaBar
        self.audio_controls = MediaBar(loop=True)
        self.audio_controls.set_visible(False)
        pv = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=14, margin_bottom=14,
                     margin_start=14, margin_end=14)
        self.pv = pv
        pv.append(self.preview_stack); pv.append(self.page_bar); pv.append(self.audio_controls)
        self.drop.set_child(pv)
        self.doc = None
        self.parents = []   # zurück zum Dokument, aus dem eine eingebettete Datei geöffnet wurde

        self.empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                             halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        for text, cls in (("󰈈", "empty-icon"), ("Datei hierher ziehen", "empty-title"),
                          ("Bilder, Videos, PDFs, Office, Audio, Archive und mehr. Oder Strg+V zum Einfügen, "
                           "Strg+O zum Öffnen.", "empty-hint")):
            l = Gtk.Label(label=text, wrap=True, max_width_chars=24, justify=Gtk.Justification.CENTER)
            l.add_css_class(cls)
            self.empty.append(l)
        self.error = Gtk.Label(label="", wrap=True, max_width_chars=50, justify=Gtk.Justification.CENTER)
        self.error.add_css_class("error")
        self.error.set_visible(False)
        self.empty.append(self.error)
        self.drop.add_overlay(self.empty)
        self.video_badge = Gtk.Label(halign=Gtk.Align.START, valign=Gtk.Align.END, visible=False, wrap=True)
        self.video_badge.add_css_class("vbadge")
        self.drop.add_overlay(self.video_badge)
        self.video = self.video_info = None

        target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        target.set_gtypes([Gdk.FileList.__gtype__, Gdk.Texture.__gtype__])
        target.connect("enter", lambda *_: (self.drop.add_css_class("drag-over"), Gdk.DragAction.COPY)[1])
        target.connect("leave", lambda *_: self.drop.remove_css_class("drag-over"))
        target.connect("drop", self.on_drop)
        self.drop.add_controller(target)
        main.append(self.drop)

        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.info_scroll = scroll
        scroll.set_size_request(380, -1)
        scroll.set_hexpand(False)
        scroll.set_margin_end(16); scroll.set_margin_bottom(16); scroll.set_margin_top(6)
        self.info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        scroll.set_child(self.info)
        main.append(scroll)

        # Farbfeld wird einmal gebaut und bei jedem Bild neu gefüllt
        self.swatch = Gtk.DrawingArea(content_height=74, content_width=74, valign=Gtk.Align.CENTER)
        self.swatch.set_draw_func(self.draw_swatch)
        self.colors, self.colors_from, self.color_sel = [], "eigen", 0
        self.show_info()

        # --- Meme-Editor und weitere Seiten ---
        self.editor = MemeEditor(self)
        self.stack.add_named(self.editor, "editor")
        self.stack.set_visible_child_name("home")
        self.stack.connect("notify::visible-child-name", lambda *_: self.on_page_changed())
        GLib.idle_add(lambda: (self.on_page_changed(), False)[-1])

    # ---------- Seiten & Zustand ----------
    def page(self):
        return self.stack.get_visible_child_name() or "home"

    def on_page_changed(self):
        if getattr(self, "focused", False) and not self.can_focus_pdf():
            self.set_focus_mode(False)
        page = self.page()
        pl = self.pages.get("player")
        if pl is not None and pl.full and page != "player":      # Vollbild des Players beim Verlassen beenden
            pl.set_full(False)
        has_file = self.image is not None or getattr(self, "doc", None) is not None
        self.bar.set_visible(not getattr(self, "focused", False))  # feste Kopfhöhe: Start rutscht beim Seitenwechsel nicht
        for b in self.file_btns:
            b.set_visible(page in FILE_PAGES or (page == "home" and b is self.file_btns[0]))
        if page == "home":
            self.set_title("Start", "Was möchtest du tun?")
        elif page in FILE_PAGES and has_file:
            name = os.path.basename(self.source) if self.source else "Aus der Zwischenablage"
            self.set_title(name, PAGE_TITLES.get(page, self.subtitle_text()))
        elif page.startswith("tool-") or page in ("record", "cutter", "player"):
            pg = self.stack.get_visible_child()
            self.set_title(getattr(pg, "title", "Werkzeug"), getattr(pg, "subtitle", ""))
        self.app_main.sync_actions(self)

    def subtitle_text(self):
        if getattr(self, "doc", None) is not None:
            return self.doc.label
        if getattr(self, "video", None):
            return "Video"
        return "Bild" if self.image is not None else ""

    def set_title(self, title, sub=""):
        self.title = title
        self.heading.set_label(title)
        self.subtitle.set_label(sub)
        self.app_main.update_tab_label(self)

    def action_states(self):
        page = self.page()
        has_file = (self.image is not None or getattr(self, "doc", None) is not None
                    or bool(getattr(self, "_pending", None)))
        typing = page in ("editor", "cutter") or page.startswith("tool-")  # dort gehören Strg+V/E den Feldern
        return {"edit": self.image is not None and getattr(self, "doc", None) is None and page in VIEW_PAGES,
                "paste": not typing, "open": True}

    def stop_media(self):
        try:
            self.editor.pause()
            stream = self.audio_controls.get_media_stream()
            if stream:
                stream.pause()
            for pg in self.pages.values():
                if hasattr(pg, "pause"):
                    pg.pause()
        except Exception:
            pass

    def can_back(self):
        if getattr(self, "focused", False):           # PDF-Arbeitsfläche: Wischen gehört dem Dokument
            return False
        return self.page() != "home"

    def go_back(self):
        page = self.page()
        if page == "editor":
            self.leave_editor()
        elif page == "main" and self.parents:
            self.go_parent()
        else:
            self.go_home()

    def _swipe_title(self, page, src):
        if page == "home":
            return "start"
        if page == "main":
            return os.path.basename(src) if src else "zwischenablage"
        if page == "editor":
            return "editor"
        if page == "osint":
            return "analyse"
        t = getattr(self, "PAGE_TITLES", {}).get(page) or PAGE_TITLES.get(page)
        return (t or page.replace("tool-", "")).lower()

    def swipe_where(self):
        """Für die 2fz-Vorschau: (schlüssel, titel) der aktuellen Seite."""
        page = self.page()
        src = self.source if page in ("main", "editor", "osint") else None
        return (page, src), self._swipe_title(page, src)

    def swipe_target(self):
        """Wohin go_back() führt – muss zur Logik dort passen."""
        page = self.page()
        if page == "editor":
            key = ("main", self.source)
        elif page == "osint":
            key = ("main", self.parents[-1]) if self.parents else ("main", self.source)
        elif page == "main" and self.parents:
            key = ("main", self.parents[-1])
        else:
            key = ("home", None)
        return key, self._swipe_title(*key)

    def swipe_goto(self, key):
        """2fz vorwärts: zu einer zuvor verlassenen Seite zurückkehren (Schlüssel aus swipe_where)."""
        page, src = key
        if page == "home":
            self.go_home()
        elif page == "main":
            if src and os.path.exists(src):
                self.load_path(src)
            else:
                return False
        elif page in self.pages:
            self.stop_media()
            self.stack.set_visible_child_name(page)
            self.on_page_changed()
        else:
            return False
        return True

    @staticmethod
    def _swipe_blocked(w):
        """Widgets, die waagerechtes Wischen selbst brauchen (Zeitleiste, Zeichenflächen mit Scroll-Steuerung)."""
        if isinstance(w, (Gtk.ScrolledWindow, Gtk.Scrollbar)):
            return False
        try:
            ctrls = w.observe_controllers()
            for i in range(ctrls.get_n_items()):
                c = ctrls.get_item(i)
                if isinstance(c, Gtk.EventControllerScroll) and (
                        c.get_flags() & Gtk.EventControllerScrollFlags.HORIZONTAL):
                    return True
        except Exception:
            pass
        return False

    def _cutter_guard(self, proceed, leaving=True):
        """Schnitt mit ungesicherten Änderungen: erst fragen (zwischenspeichern / verwerfen / bleiben)."""
        pg = self.pages.get("cutter")
        if pg is not None and pg.needs_ask() and (not leaving or self.page() == "cutter"):
            pg.ask_leave(proceed)
            return True
        return False

    def go_home(self, _go=False):
        if not _go and self._cutter_guard(lambda: self.go_home(_go=True)):
            return
        self.stop_media()
        self.stack.set_visible_child_name("home")
        self.home.refresh()

    def open_page(self, key, factory, _go=False):
        """Seite (Aufnahme, Schnitt, Werkzeug) bei Bedarf bauen und zeigen."""
        if not _go and key != "cutter" and self._cutter_guard(lambda: self.open_page(key, factory, _go=True)):
            return None
        self.stop_media()
        if key not in self.pages:
            self.pages[key] = factory()
            self.stack.add_named(self.pages[key], key)
        self.stack.set_visible_child_name(key)
        self.on_page_changed()
        return self.pages[key]

    def open_recorder(self):
        from recorder import RecorderPage
        return self.open_page("record", lambda: RecorderPage(self))

    def open_cutter(self, path, _go=False):
        from cutter import CutterPage
        if not _go and self._cutter_guard(lambda: self.open_cutter(path, _go=True), leaving=False):
            return self.pages.get("cutter")
        pg = self.open_page("cutter", lambda: CutterPage(self))
        pg.load(path)
        self.app_main.add_recent(path)
        self.on_page_changed()
        return pg

    def open_tool(self, key):
        import tools
        return self.open_page("tool-" + key, lambda: tools.make(key, self))

    def shutdown(self):
        self.stop_media()
        try:
            self.editor.stop_video()
            self.reset_preview()
            for pg in self.pages.values():
                if hasattr(pg, "shutdown"):
                    pg.shutdown()
        except Exception:
            pass

    # ---------- Infokarten ----------
    def card(self, title):
        box = ch.Card(title, spacing=10)
        self.info.append(box)
        return box

    def rows(self, parent, rows):
        grid = Gtk.Grid(column_spacing=14, row_spacing=6)
        for i, (k, v) in enumerate(rows):
            kl = Gtk.Label(label=k, xalign=0, yalign=0)
            kl.add_css_class("key")
            vl = Gtk.Label(label=str(v), xalign=0, wrap=True, wrap_mode=2, selectable=True, hexpand=True,
                           max_width_chars=34)  # lange Pfade sollen die Spalte nicht aufblähen
            vl.add_css_class("val")
            grid.attach(kl, 0, i, 1, 1)
            if k == "GPS-Position":
                box = Gtk.Box(spacing=8)
                box.append(vl)
                b = Gtk.Button(label="󰍎 Maps", tooltip_text="In Google Maps öffnen", valign=Gtk.Align.START)
                b.add_css_class("pb"); b.add_css_class("small")
                b.connect("clicked", lambda *_, v=v: self.open_maps(v))
                box.append(b)
                vl = box
            grid.attach(vl, 1, i, 1, 1)
        parent.append(grid)

    def copy_button(self, label, text):
        b = Gtk.Button(label=label, tooltip_text=f"{text} kopieren")
        b.add_css_class("pb"); b.add_css_class("small")
        b.connect("clicked", lambda *_: self.copy(text))
        return b

    def show_info(self, meta=None):
        while (c := self.info.get_first_child()):
            self.info.remove(c)
        if meta is None:
            c = self.card("Infos")
            l = Gtk.Label(label="Öffne eine Datei, dann stehen hier Inhalt, Dateityp, Metadaten und bei Bildern die Akzentfarbe.",
                          xalign=0, wrap=True)
            l.add_css_class("note")
            c.append(l)
            return

        # Farbe
        self.color_card = self.card("Akzentfarbe")
        self.fill_color_card()

        # Größe
        self.rows(self.card("Größe"), meta["size"])
        # Datei
        self.rows(self.card("Datei"), meta["file"])
        # Metadaten
        c = self.card("Metadaten")
        if meta["meta"]:
            self.rows(c, meta["meta"])
        else:
            l = Gtk.Label(label="Keine Metadaten im Bild gefunden.", xalign=0)
            l.add_css_class("note")
            c.append(l)

    def fill_color_card(self):
        c = self.color_card
        while (child := c.get_first_child()):     # Kartentitel wird gezeichnet, also alles leeren
            c.remove(child)
        row = Gtk.Box(spacing=14)
        if self.swatch.get_parent():
            self.swatch.get_parent().remove(self.swatch)
        row.append(self.swatch)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER)
        hexv = self.current_color()
        if hexv:
            r, g, b = (int(hexv[i:i + 2], 16) for i in (1, 3, 5))
            h, l, sat = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
            hx = Gtk.Label(label=hexv.upper(), xalign=0, selectable=True)
            hx.add_css_class("hex")
            col.append(hx)
            for k, v in (("RGB", f"{r}, {g}, {b}"), ("HSL", f"{round(h * 360)}°, {round(sat * 100)} %, {round(l * 100)} %")):
                lab = Gtk.Label(label=f"{k}  {v}", xalign=0, selectable=True)
                lab.add_css_class("val")
                col.append(lab)
            btns = Gtk.Box(spacing=6, margin_top=4)
            btns.append(self.copy_button("Hex kopieren", hexv))
            btns.append(self.copy_button("RGB kopieren", f"rgb({r}, {g}, {b})"))
            col.append(btns)
        else:
            l = Gtk.Label(label="Das Bild ist komplett transparent.", xalign=0)
            l.add_css_class("note")
            col.append(l)
        row.append(col)
        c.append(row)

        if len(self.colors) > 1:
            more = Gtk.Box(spacing=8)
            lab = Gtk.Label(label="Weitere", xalign=0)
            lab.add_css_class("key")
            more.append(lab)
            for i, colr in enumerate(self.colors):
                b = Gtk.Button(tooltip_text=colr.upper(), halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
                b.add_css_class("dot")
                if i == self.color_sel:
                    b.add_css_class("active")
                dot = Gtk.DrawingArea(content_width=20, content_height=20, can_target=False)
                dot.set_draw_func(lambda a, cr, w, h, colr=colr: (
                    cr.arc(w / 2, h / 2, min(w, h) / 2, 0, 2 * math.pi),
                    cr.set_source_rgb(*(int(colr[j:j + 2], 16) / 255 for j in (1, 3, 5))), cr.fill()))
                b.set_child(dot)
                b.connect("clicked", lambda *_, i=i: self.pick_color(i))
                more.append(b)
            c.append(more)
        src = ("Aus dem Bild ausgesucht wie von matugen, das auch dein Theme aus dem Wallpaper baut."
               if self.colors_from == "matugen" else "Selbst berechnet: die häufigste kräftige Farbe im Bild.")
        note = Gtk.Label(label=src, xalign=0, wrap=True)
        note.add_css_class("note")
        c.append(note)
        self.swatch.queue_draw()

    def current_color(self):
        return self.colors[self.color_sel] if self.colors else None

    def pick_color(self, i):
        self.color_sel = i
        self.fill_color_card()

    def start_matugen(self, data, path):
        """matugen listet die Quellfarben eines Bildes, ohne dein Theme zu ändern."""
        import shutil, tempfile
        if not shutil.which("matugen"):
            return
        self._color_token = token = getattr(self, "_color_token", 0) + 1
        tmp = None
        if not path:
            fd, tmp = tempfile.mkstemp(prefix="scourge-", suffix=".png")
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            path = tmp
        proc = Gio.Subprocess.new(["matugen", "image", path, "--show-source-colors"],
                                  Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)

        def done(p, res):
            if tmp:
                os.unlink(tmp)
            try:
                _, out, _ = p.communicate_utf8_finish(res)
            except GLib.Error:
                return
            found = re.findall(r"#[0-9a-fA-F]{6}", out or "")
            if token == self._color_token and found and p.get_successful():
                self.colors, self.colors_from, self.color_sel = [c.lower() for c in found[:5]], "matugen", 0
                self.fill_color_card()
        proc.communicate_utf8_async(None, None, done)

    def open_maps(self, value):
        coords = re.findall(r"-?\d+\.\d+", value)[:2]
        if len(coords) == 2:
            url = f"https://www.google.com/maps/search/?api=1&query={coords[0]},{coords[1]}"
            Gtk.UriLauncher(uri=url).launch(self.win, None, None)

    def draw_swatch(self, area, cr, w, h):
        r = 15
        cr.new_sub_path()
        cr.arc(w - r, r, r, -math.pi / 2, 0)
        cr.arc(w - r, h - r, r, 0, math.pi / 2)
        cr.arc(r, h - r, r, math.pi / 2, math.pi)
        cr.arc(r, r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()
        if self.current_color():
            hexv = self.current_color()
            cr.set_source_rgb(*(int(hexv[i:i + 2], 16) / 255 for i in (1, 3, 5)))
        else:
            cr.set_source_rgba(0.5, 0.5, 0.5, 0.2)
        cr.fill()

    # ---------- Laden ----------
    def load_path(self, path, keep_parents=False, force=False):
        if not force and self.pdf_view.dirty:
            return self.app_main.confirm_unsaved([self], lambda: self.load_path(path, keep_parents, force=True))
        self._doc_token = getattr(self, "_doc_token", 0) + 1   # laufende Analyse eines alten Dokuments verwerfen
        self._pending = None
        if not keep_parents:
            self.parents = []
            self.app_main.add_recent(path)
        ctype, _ = Gio.content_type_guess(path, None)
        if path.lower().endswith(VIDEO_EXT) or (ctype or "").startswith("video/"):
            return self.load_video(path, ctype)
        try:
            if os.path.isdir(path):
                return self.fail("Ordner kann Scourge nicht öffnen, nur Dateien. Für ganze Ordner gibt es auf der Startseite den Ordner-Scan.")
            is_image = False
            if not path.lower().endswith((".svg", ".pdf", ".eps", ".ai")):
                try:
                    Image.open(path).verify()
                    is_image = True
                except Exception:
                    pass
            if not is_image:
                return self.load_document(path)
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            return self.fail(f"Datei konnte nicht gelesen werden: {e.strerror}")
        self.load_bytes(data, path)

    def _tab_key(self, _c, kv, _code, _m):
        """Esc beendet die PDF-Arbeitsfläche; Leertaste startet/stoppt Musik in der Dateiprüfung."""
        if self.focused and kv == Gdk.KEY_Escape and self.page() != "player":
            self.set_focus_mode(False)
            return True
        if kv == Gdk.KEY_f and (_m & Gdk.ModifierType.CONTROL_MASK) and self.page() == "home":
            self.home.focus_search()
            return True
        if kv == Gdk.KEY_space and self.audio_controls.get_visible() and self.page() == "main":
            focus = self.app_main.win.get_focus()
            if not isinstance(focus, (Gtk.Text, Gtk.TextView, Gtk.Editable, Gtk.Button)):
                self.audio_controls.toggle()
                return True
        return False

    def on_open_player(self, *_):
        """Kachel „Öffnen“: Datei wählen und gleich ansehen/abspielen (PDFs gehen an scourge-pdf)."""
        from player import PlayerPage
        pg = self.open_page("player", lambda: PlayerPage(self))
        pg.ask()

    def open_media(self, path):
        import player
        k = player.kind(path)
        if k == "pdf":
            open_in_pdf_editor(path)
            return
        if k is None:
            return self.load_path(path)
        from player import PlayerPage
        pg = self.open_page("player", lambda: PlayerPage(self))
        self.app_main.add_recent(path)
        pg.load(path)

    def can_focus_pdf(self):
        return self.page() == "main" and self.preview_stack.get_visible_child_name() == "pdf"

    def set_focus_mode(self, on):
        """PDF-Arbeitsfläche ein/aus (nur innerhalb des Fensters, kein echtes Vollbild)."""
        on = bool(on) and self.can_focus_pdf()
        if self.focus_btn.get_active() != on:
            self.focus_btn.set_active(on)       # löst toggled → hierher zurück aus
            return
        if on == self.focused:
            return
        self.focused = on
        self.focus_btn.set_label("󰊔" if on else "󰊓")
        self.bar.set_visible(not on)
        self.info_btn.set_visible(not on)
        self.info_scroll.set_visible(not on and self.info_btn.get_active())
        if on:
            self._badge_was = self.video_badge.get_visible()
            self.video_badge.set_visible(False)
        elif getattr(self, "_badge_was", False):
            self.video_badge.set_visible(True)
        m = 4 if on else 14
        for side in ("top", "bottom", "start", "end"):
            getattr(self.pv, f"set_margin_{side}")(m)
        self.app_main.apply_chrome()
        if on:
            self.pdf_view.grab_focus()
        elif getattr(self, "_fs", False):         # per Doppelklick im Vollbild geöffnet → Vollbild mit beenden
            self._fs = False
            self.app_main.win.unfullscreen()

    def open_fullscreen(self, tries=20):
        """PDF von außen geöffnet (Doppelklick im Dateimanager): gleich als Arbeitsfläche im Vollbild."""
        if self.can_focus_pdf():
            self._fs = True
            self.app_main.win.fullscreen()
            self.set_focus_mode(True)
            return False
        if tries > 0:                              # Netzlaufwerk: PDF kommt erst nach dem Laden
            GLib.timeout_add(250, lambda: self.open_fullscreen(tries - 1))
        return False

    def reset_preview(self):
        if getattr(self, "focused", False):
            self.set_focus_mode(False)
        self._audio_token = getattr(self, "_audio_token", 0) + 1  # laufende Umwandlung verwerfen
        if hasattr(self, "info_scroll"):
            self.info_scroll.set_visible(True)
            self.info_btn.set_active(True)
        self.doc = None
        self.page_bar.set_visible(False)
        self.audio_controls.set_visible(False)
        stream = self.audio_controls.get_media_stream()
        if stream:
            stream.pause()
        self.audio_controls.set_media_stream(None)
        self.preview_stack.set_visible_child_name("pic")

    def load_document(self, path):
        """Dokument zeigen und im Hintergrund analysieren. PDFs erscheinen sofort im Viewer (die Analyse mit
        Versionen, verstecktem Text usw. dauert bei dicken PDFs Sekunden), andere Formate nach der Analyse."""
        token = self._doc_token = getattr(self, "_doc_token", 0) + 1
        pdf = None
        if formats.is_remote(path) and not getattr(self, "_net_loaded", None) == path:
            return self._fetch_remote(path, token)
        self._net_loaded = None
        try:
            if formats.is_remote(path):
                is_pdf = formats.file_bytes(path)[:5] == b"%PDF-" or path.lower().endswith(".pdf")
            else:
                with open(path, "rb") as fh:
                    is_pdf = fh.read(5) == b"%PDF-" or path.lower().endswith(".pdf")
        except OSError as e:
            return self.fail(f"Datei konnte nicht gelesen werden: {e.strerror}")
        if is_pdf:
            try:
                pdf = formats.poppler_doc(path)
            except GLib.Error:
                pdf = None            # geschützt oder kaputt: die Analyse erklärt es
        if pdf is not None:
            self.reset_preview()
            self.doc, self.image, self.source, self.data, self.video, self.video_info = None, None, path, None, None, None
            self.last_meta = []
            self._pending = path
            self.pdf_view.load(pdf, path)
            self.preview_stack.set_visible_child_name("pdf")
            n = pdf.get_n_pages()
            self.video_badge.set_label(f"PDF-Dokument · {n} Seite{'n' if n != 1 else ''} · Analyse läuft …")
            self.video_badge.set_visible(True)
            self.empty.set_visible(False)
            self.error.set_visible(False)
            self.drop.add_css_class("loaded")
            self.leave_editor()
            self.set_title(os.path.basename(path), "PDF-Dokument")
            while (c := self.info.get_first_child()):
                self.info.remove(c)
            note = Gtk.Label(label="Metadaten, Schriften, frühere Fassungen und versteckter Text werden geprüft …",
                             xalign=0, wrap=True)
            note.add_css_class("note")
            self.card("Analyse").append(note)
        self._analyze_async(path, token, keep_view=pdf is not None)

    def _fetch_remote(self, path, token):
        """Netzlaufwerk (z. B. ZIH über VPN): Datei einmal im Hintergrund holen, Oberfläche bleibt bedienbar."""
        import threading
        self.set_title(os.path.basename(path), "wird vom Netzlaufwerk geladen …")
        self.notify(f"Lade {os.path.basename(path)} vom Netzlaufwerk …")

        def work():
            try:
                formats.file_bytes(path)
                err = None
            except OSError as e:
                err = e

            def done():
                if token != getattr(self, "_doc_token", 0):
                    return False
                if err is not None:
                    self.fail(f"Datei konnte nicht gelesen werden: {err.strerror or err}")
                else:
                    self._net_loaded = path
                    self.load_document(path)
                return False
            GLib.idle_add(done)
        threading.Thread(target=work, daemon=True).start()

    def _analyze_async(self, path, token, keep_view=False):
        import threading

        def work():
            try:
                an, err = formats.analyze(path), None
            except Exception as e:  # noqa: BLE001
                an, err = None, e
            GLib.idle_add(lambda: (self._doc_ready(path, token, an, err, keep_view), False)[-1])
        threading.Thread(target=work, daemon=True).start()

    def _doc_ready(self, path, token, an, err, keep_view):
        if token != getattr(self, "_doc_token", 0):
            return                     # inzwischen wurde etwas anderes geöffnet
        self._pending = None
        if err is not None:
            if keep_view:
                self.video_badge.set_label("PDF-Dokument · Analyse fehlgeschlagen")
                return self.notify(f"Analyse fehlgeschlagen ({err.__class__.__name__}: {str(err)[:80]})")
            return self.fail(f"Datei konnte nicht analysiert werden ({err.__class__.__name__}: {str(err)[:80]}).")
        if not keep_view:
            self.reset_preview()
        self.doc = an
        self.image, self.source, self.data, self.video, self.video_info = an.preview, path, None, None, None
        self.last_meta = []
        self.page_i = 0
        if keep_view:
            pass                       # PDF ist schon im Viewer (mit Position und Anmerkungen)
        elif an.doc_obj is not None:   # PDF: richtiger Viewer mit Zoom und Suche
            self.pdf_view.load(an.doc_obj, path)
            self.preview_stack.set_visible_child_name("pdf")
        elif an.pages and an.render_page:
            self.show_page(0)
            self.page_bar.set_visible(an.pages > 1)
        elif an.preview is not None:
            self.picture.set_paintable(pil_to_texture(an.preview))
        else:
            self.text_view.get_buffer().set_text(an.text_preview or "(keine Vorschau für diesen Dateityp)")
            self.preview_stack.set_visible_child_name("text")
        if an.player:
            self.prepare_audio(an.player)
        badge = f"{an.label}" + (f" · {an.pages} Seite{'n' if an.pages != 1 else ''}" if an.pages else "")
        self.video_badge.set_label(badge)
        self.video_badge.set_visible(True)
        self.subtitle.set_label(os.path.basename(path))
        self.empty.set_visible(False)
        self.error.set_visible(False)
        self.drop.add_css_class("loaded")
        if self.page() not in VIEW_PAGES or not keep_view:
            self.leave_editor()
        else:
            self.on_page_changed()
        while (c := self.info.get_first_child()):
            self.info.remove(c)
        for title, rows in an.cards:
            self.rows(self.card(title), rows or [("–", "keine")])
        if self.parents:
            c = self.card("Eingebettet in")
            b = Gtk.Button(label=f"󰁍  {os.path.basename(self.parents[-1])}", halign=Gtk.Align.START)
            b.add_css_class("pb"); b.add_css_class("small")
            b.connect("clicked", lambda *_: self.go_parent())
            c.append(b)

    def on_pdf_saved(self, path):
        """Nach dem Speichern von Anmerkungen: Analyse neu (Viewer bleibt, wie er ist)."""
        if self.source == path or self.source is None:
            self.source = path
            self._doc_token = getattr(self, "_doc_token", 0) + 1
            self._analyze_async(path, self._doc_token, keep_view=True)
        self.app_main.add_recent(path)

    def prepare_audio(self, src):
        """Tonspur im Hintergrund nach Opus umwandeln und dann abspielbar machen.
        Grund: GStreamer stürzt auf diesem System bei jeder MP3 ab, und AAC/m4a fehlt ohne gst-libav."""
        import hashlib, subprocess, threading
        folder = os.path.expanduser("~/.cache/scourge/abspielen")
        os.makedirs(folder, exist_ok=True)
        st = os.stat(src)
        out = os.path.join(folder, hashlib.sha1(f"{src}|{st.st_mtime_ns}".encode()).hexdigest()[:12] + ".ogg")
        token = self._audio_token = getattr(self, "_audio_token", 0) + 1

        def ready():
            if token == self._audio_token and self.doc is not None:
                self.audio_controls.set_media_stream(Gtk.MediaFile.new_for_filename(out))
                self.audio_controls.set_visible(True)
            return False
        if os.path.exists(out):
            return ready()

        def work():
            r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-map", "0:a:0", "-vn", "-map_metadata", "-1",
                                "-c:a", "libopus", "-b:a", "128k", out + ".part.ogg"], capture_output=True)
            if r.returncode == 0:
                os.replace(out + ".part.ogg", out)
                GLib.idle_add(ready)
        threading.Thread(target=work, daemon=True).start()

    def show_page(self, i):
        an = self.doc
        self.page_i = max(0, min(i, an.pages - 1))
        self.picture.set_paintable(pil_to_texture(an.render_page(self.page_i)))
        self.page_label.set_label(f"Seite {self.page_i + 1} / {an.pages}")
        self.prev_btn.set_sensitive(self.page_i > 0)
        self.next_btn.set_sensitive(self.page_i < an.pages - 1)

    def turn_page(self, d):
        if self.doc and self.doc.pages and self.stack.get_visible_child_name() == "main":
            if self.doc.doc_obj is not None:
                self.pdf_view.goto(self.pdf_view.current_page() + d)
            else:
                self.show_page(self.page_i + d)

    def open_embedded(self, name, data):
        """Eingebettete Datei (Anhang, Bild, Dokument) in Scourge öffnen; Zurück führt zum Dokument."""
        folder = os.path.join(os.path.expanduser("~/.cache/scourge"), "eingebettet")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, os.path.basename(name) or "datei")
        with open(path, "wb") as fh:
            fh.write(data)
        parents = self.parents + [self.source]
        self.load_path(path, keep_parents=True)
        self.parents = parents

    def go_parent(self):
        if self.parents:
            parent = self.parents.pop()
            rest = list(self.parents)
            self.load_path(parent, keep_parents=True)
            self.parents = rest

    def load_bytes(self, data, path=None):
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
        except Exception:
            return self.fail("Diese Datei kann Scourge nicht lesen.")
        self.reset_preview()
        self.image, self.source, self.data = img, path, data
        self.video = self.video_info = None
        self.video_badge.set_visible(False)
        self.colors, self.colors_from, self.color_sel = accent_fallback(img), "eigen", 0
        self.start_matugen(data, path)

        w, h = img.size
        g = math.gcd(w, h) or 1
        size = [("Breite", f"{w} px"), ("Höhe", f"{h} px"),
                ("Megapixel", fmt_num(w * h / 1e6)),
                ("Seitenverhältnis", f"{w // g}:{h // g}" if w // g <= 32 else f"≈ {fmt_num(w / h)}:1"),
                ("Dateigröße", human_size(len(data)))]
        dpi = img.info.get("dpi")
        if dpi and dpi[0] and float(dpi[0]) > 1:
            dx, dy = float(dpi[0]), float(dpi[1] or dpi[0])
            size.append(("DPI", fmt_num(dx, 0)))
            size.append(("Druckgröße", f"{fmt_num(w / dx * 2.54, 1)} × {fmt_num(h / dy * 2.54, 1)} cm"))

        fmt = img.format or "?"
        file = [("Dateityp", getattr(img, "format_description", None) or fmt),
                ("MIME", Image.MIME.get(fmt, "unbekannt")),
                ("Farbmodus", MODES.get(img.mode, img.mode)),
                ("Transparenz", "ja" if ("A" in img.mode or "transparency" in img.info) else "nein")]
        frames = getattr(img, "n_frames", 1)
        if frames > 1:
            file.append(("Frames", f"{frames} (animiert)"))
        if path:
            st = os.stat(path)
            file.insert(0, ("Name", os.path.basename(path)))
            file.append(("Ordner", os.path.dirname(path).replace(os.path.expanduser("~"), "~", 1)))
            file.append(("Geändert", datetime.datetime.fromtimestamp(st.st_mtime).strftime("%d.%m.%Y %H:%M")))
            self.subtitle.set_label(os.path.basename(path))
        else:
            file.insert(0, ("Quelle", "Zwischenablage"))
            self.subtitle.set_label("Aus der Zwischenablage")

        texture = pil_to_texture(img)
        self.picture.set_paintable(texture)
        self.empty.set_visible(False)
        self.error.set_visible(False)
        self.drop.add_css_class("loaded")
        self.leave_editor()
        self.last_meta = read_metadata(img)
        self.show_info({"size": size, "file": file, "meta": self.last_meta})

    def load_video(self, path, ctype=None):
        try:
            info = probe_video(path)
            dur = info["duration"]
            frame = video_frame(path, min(1.0, dur * 0.1))
        except Exception as e:
            return self.fail(f"Video konnte nicht gelesen werden ({e.__class__.__name__}).")
        self.reset_preview()
        self.image, self.source, self.video, self.video_info = frame, path, path, info
        self.data, self.last_meta = None, []
        self.colors, self.colors_from, self.color_sel = accent_fallback(frame), "eigen", 0
        self._video_colors(path, frame, dur)

        w, h = info["w"], info["h"]
        g = math.gcd(w, h) or 1
        size = [("Breite", f"{w} px"), ("Höhe", f"{h} px"), ("Dauer", fmt_duration(dur)),
                ("Bildrate", f"{fmt_num(info['fps'])} fps"),
                ("Seitenverhältnis", f"{w // g}:{h // g}" if w // g <= 32 else f"≈ {fmt_num(w / h)}:1")]
        if info["bitrate"]:
            size.append(("Bitrate", f"{fmt_num(info['bitrate'] / 1e6, 1)} Mbit/s"))
        size.append(("Dateigröße", human_size(os.path.getsize(path))))
        vc = info["vcodec"] + (f" ({info['profile']})" if info.get("profile") else "")
        a = info["audio"]
        if a:
            ch = {1: "Mono", 2: "Stereo"}.get(a.get("channels"), f"{a.get('channels')} Kanäle")
            audio = f"{a.get('codec_long_name', a.get('codec_name', '?'))}, {ch}"
            if a.get("sample_rate"):
                audio += f", {fmt_num(int(a['sample_rate']) / 1000, 1)} kHz"
        else:
            audio = "keine Tonspur"
        st = os.stat(path)
        file = [("Name", os.path.basename(path)), ("Dateityp", info["format"]),
                ("MIME", Gio.content_type_get_mime_type(ctype) if ctype else "video"),
                ("Video", vc), ("Ton", audio),
                ("Ordner", os.path.dirname(path).replace(os.path.expanduser("~"), "~", 1)),
                ("Geändert", datetime.datetime.fromtimestamp(st.st_mtime).strftime("%d.%m.%Y %H:%M"))]
        self.subtitle.set_label(os.path.basename(path))
        self.picture.set_paintable(pil_to_texture(frame))
        self.video_badge.set_label(f"󰕧  Video · {fmt_duration(dur)} · abspielen unter „Bearbeiten“")
        self.video_badge.set_visible(True)
        self.empty.set_visible(False)
        self.error.set_visible(False)
        self.drop.add_css_class("loaded")
        self.leave_editor()
        self.show_info({"size": size, "file": file, "meta": video_metadata(info)})

    def _video_colors(self, path, frame, dur):
        """Akzentfarbe aus vier Szenen über das ganze Video – parallel im Hintergrund, die Ansicht ist schon da."""
        import threading
        from concurrent.futures import ThreadPoolExecutor
        token = self._vid_token = getattr(self, "_vid_token", 0) + 1

        def work():
            try:
                shots = [frame]
                if dur > 2:
                    with ThreadPoolExecutor(3) as ex:
                        shots += list(ex.map(lambda f: video_frame(path, dur * f, keyframe=True), (0.35, 0.6, 0.85)))
                sheet = Image.new("RGB", (640, 360 * ((len(shots) + 1) // 2)))
                for i, im in enumerate(shots):
                    sheet.paste(im.convert("RGB").resize((320, 360)), ((i % 2) * 320, (i // 2) * 360))
                buf = io.BytesIO()
                sheet.save(buf, "PNG", compress_level=1)
            except Exception:
                return

            def apply():
                if token == self._vid_token and self.video == path:
                    self.colors, self.colors_from, self.color_sel = accent_fallback(sheet), "eigen", 0
                    if getattr(self, "color_card", None) is not None:
                        self.fill_color_card()
                    self.start_matugen(buf.getvalue(), None)
                return False
            GLib.idle_add(apply)
        threading.Thread(target=work, daemon=True).start()

    def fail(self, msg):
        if self.image is None and self.doc is None:
            self.error.set_label(msg)
            self.error.set_visible(True)
        else:
            self.notify(msg)

    # ---------- Aktionen ----------
    def on_open(self, *_):
        """Strg+O / Knopf „öffnen“: gleiche Funktion wie die Kachel „Öffnen“ (Bild/Video/Musik/PDF direkt, alles
        andere landet in der Dateiprüfung)."""
        return self.on_open_player()

    def on_open_check(self, *_):
        """Datei wählen und durchleuchten (Kachel „Datei prüfen“)."""
        everything = Gtk.FileFilter(name="Alle Dateien")
        everything.add_pattern("*")
        media = Gtk.FileFilter(name="Bilder und Videos")
        media.add_mime_type("image/*")
        media.add_mime_type("video/*")
        docs = Gtk.FileFilter(name="Dokumente (PDF, Office, Text)")
        for pat in ("*.pdf", "*.doc*", "*.xls*", "*.ppt*", "*.od?", "*.epub", "*.txt", "*.md", "*.csv", "*.svg", "*.html"):
            docs.add_pattern(pat)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        for f in (everything, media, docs):
            filters.append(f)
        dlg = Gtk.FileDialog(title="Datei öffnen", filters=filters)
        if self.source:
            dlg.set_initial_folder(Gio.File.new_for_path(os.path.dirname(self.source)))
        dlg.open(self.win, None, self.on_open_done)

    def on_open_done(self, dlg, res):
        try:
            f = dlg.open_finish(res)
        except GLib.Error:
            return  # abgebrochen
        if f:
            self.load_path(f.get_path())

    def on_paste(self, *_):
        cb = self.win.get_clipboard()
        fmts = cb.get_formats()
        if fmts.contain_gtype(Gdk.FileList.__gtype__) or fmts.contain_mime_type("text/uri-list"):
            cb.read_value_async(Gdk.FileList.__gtype__, GLib.PRIORITY_DEFAULT, None, self.on_paste_files)
        elif fmts.contain_gtype(Gdk.Texture.__gtype__):
            cb.read_texture_async(None, self.on_paste_texture)
        else:
            cb.read_text_async(None, self.on_paste_text)

    def on_paste_files(self, cb, res):
        try:
            files = cb.read_value_finish(res).get_files()
        except Exception:
            files = []
        if files and files[0].get_path():
            self.load_path(files[0].get_path())
        else:
            cb.read_texture_async(None, self.on_paste_texture)

    def on_paste_texture(self, cb, res):
        try:
            tex = cb.read_texture_finish(res)
        except GLib.Error:
            tex = None
        if tex:
            self.load_bytes(tex.save_to_png_bytes().get_data())
        else:
            self.fail("In der Zwischenablage ist kein Bild.")

    def on_paste_text(self, cb, res):
        try:
            text = (cb.read_text_finish(res) or "").strip()
        except GLib.Error:
            text = ""
        path = Gio.File.new_for_uri(text).get_path() if text.startswith("file://") else os.path.expanduser(text)
        if text and path and os.path.isfile(path):
            self.load_path(path)
        else:
            self.fail("In der Zwischenablage ist kein Bild.")

    def on_drop(self, target, value, x, y):
        self.drop.remove_css_class("drag-over")
        if isinstance(value, Gdk.Texture):
            self.load_bytes(value.save_to_png_bytes().get_data())
            return True
        files = value.get_files() if value else []
        if files and files[0].get_path():
            self.load_path(files[0].get_path())
            return True
        return False

    def on_edit(self, *_):
        if self.image is not None and self.doc is None:
            self.editor.open(self.image, video=self.video, info=self.video_info)
            self.stack.set_visible_child_name("editor")
            self.editor.canvas.grab_focus()


    def leave_editor(self):
        self.editor.pause()  # Videos laufen nur im Bearbeiten-Modus
        self.stack.set_visible_child_name("main")
        self.on_page_changed()

    def copy(self, text):
        self.win.get_clipboard().set_content(
            Gdk.ContentProvider.new_for_bytes("text/plain;charset=utf-8", GLib.Bytes.new(text.encode())))
        self.notify(f"{text} kopiert")



# ======================================================================
# Fenster mit Tabs
# ======================================================================
class Scourge(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.win = None
        self._toast_id = None
        self.daemon = False         # mit --daemon gestartet: Fenster vorbauen, erst beim nächsten Aufruf zeigen
        self._held = False
        self._quitting = False
        self._css_src = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        act = Gio.SimpleAction.new("quit", None)          # scourge --quit: wirklich beenden
        act.connect("activate", lambda *_: self.really_quit())
        self.add_action(act)

    def keep_running(self):
        """Im Hintergrund weiterleben: nächster Start = Fenster sofort da (kein Python-/GTK-Start)."""
        if not self._held:
            self._held = True
            self.hold()

    def do_activate(self):
        if not self.win:
            self.build()
        # eigene App-Icons (software → suite-apps) erneut anwenden, falls ein Update sie überschrieben hat
        import threading, icons
        threading.Thread(target=lambda: icons.apply_all(), daemon=True).start()
        if self.daemon:
            self.daemon = False
            self.keep_running()
            GLib.idle_add(self._prewarm)
            return
        getattr(glass, "bring_here", lambda w: None)(self.win)   # offen auf anderem Arbeitsbereich → hierher holen
        self.win.present()
        if not getattr(self, "_news_told", False):     # nach einem Update einmal Bescheid sagen
            self._news_told = True
            try:
                import changelog
                import drawer
                if changelog.check_new("scourge", drawer.version()):
                    self.ver_btn.remove_css_class("off")
                    self.ver_btn.add_css_class("news")
                    self.ver_btn.set_label(f"󰋚 v{drawer.version()}")
                    GLib.timeout_add(700, lambda: (self.notify(f"neu in v{drawer.version()} – klick oben rechts auf "
                                                               f"„v{drawer.version()}“ für den changelog"), False)[-1])
            except Exception as e:
                print("changelog:", e, file=sys.stderr)
        self.check_update()
        osint_file, self._osint_file = getattr(self, "_osint_file", None), None

    def check_update(self):
        """Neuere Version auf GitHub? → Versionsknopf „󰚰 v2.7.0 → v2.8.0“ + einmal Hinweis (Cache 6 h)."""
        try:
            import changelog
            import drawer
            version = drawer.version()

            def has_update(latest):
                if getattr(self, "_update_told", None) != latest:
                    self._update_told = latest
                    GLib.timeout_add(1000, lambda: (self.notify(changelog.update_text(latest)), False)[-1])
            getattr(changelog, "check_update", lambda *a: None)("scourge", version, has_update)
            if getattr(self, "upd_btn", None):
                self.upd_btn.recheck()
        except Exception as e:
            print("update-check:", e, file=sys.stderr)


    def show_changelog(self):
        self.drawer.open_changelog()
        self.ver_btn.remove_css_class("news")
        self.ver_btn.add_css_class("off")
        self.ver_btn.set_label(f"v{__import__('drawer').version()}")

    def _prewarm(self):
        """Im Hintergrund schon mal laden, was beim ersten Öffnen einer Datei sonst Zeit kostet."""
        try:
            import cairo  # noqa: F401
            import formats as _f  # noqa: F401
            _poppler = formats.poppler_doc  # noqa: F841
            gi.require_version("Poppler", "0.18")
            from gi.repository import Poppler  # noqa: F401
            from PIL import ImageCms  # noqa: F401
            Image.init()                 # alle Bildformat-Module (sonst beim ersten Öffnen ~50 ms)
            import threading, concurrent.futures, hashlib, tempfile, difflib, zipfile  # noqa: F401
        except Exception:
            pass
        try:   # Stile, Schriften und Größen einmal durchrechnen: das erste Zeigen ist sonst ~0,3 s langsamer
            self.win.realize()
            for o in (Gtk.Orientation.HORIZONTAL, Gtk.Orientation.VERTICAL):
                self.win.measure(o, -1)
        except Exception:
            pass
        return False

    def on_close_request(self, *_):
        """Fenster schließen = nur ausblenden (Tabs bleiben, wie sie sind). Wirklich beenden: scourge --quit."""
        if self._quitting:
            return False
        if os.name == "nt":            # Windows: kein D-Bus → versteckte Instanz wäre nicht wiederzufinden
            self.really_quit()
            return True
        import settings_menu
        if not settings_menu.background_mode():
            self.really_quit()
            return True

        def hide():
            self.close_drawer()
            self.keep_running()
            self.win.set_visible(False)
        self.close_all_tabs(hide)           # Schließen = alle Tabs zu (Super+C, ✕, Strg+Q)
        return True

    def close_all_tabs(self, then=None):
        """Alle Tabs schließen (ungespeicherte PDFs: speichern/nachfragen), danach ein frischer Start-Tab."""
        def go():
            for tab in list(self.tabs()):
                self.close_tab(tab, force=True)
            if self.notebook.get_n_pages() == 0:
                self.new_tab()
            if then:
                then()
        self.confirm_unsaved(self.tabs(), go)

    def really_quit(self):
        def go():
            self._quitting = True
            if self._held:
                self._held = False
                self.release()
            if self.win:
                self.win.close()
            self.quit()
        if self.win:
            self.confirm_unsaved(self.tabs(), go)
        else:
            go()

    def confirm_unsaved(self, tabs, proceed):
        """Bei ungespeicherten PDF-Anmerkungen nachfragen: Abbrechen / Verwerfen / Speichern."""
        for t in tabs:                     # mit Datei: einfach sofort speichern (wie das automatische Speichern)
            v = t.pdf_view
            if v.dirty and v.path and v.editable:
                v._backup_original(v.path)
                v.save(quiet=True)
        dirty = [t for t in tabs if t.pdf_view.dirty]
        if not dirty:
            return proceed()
        if not self.win.get_visible():
            self.win.present()
        names = ", ".join(os.path.basename(t.pdf_view.path or "PDF") for t in dirty)
        dlg = Gtk.AlertDialog(message="Ungespeicherte Anmerkungen",
                              detail=f"{names}: Die Änderungen sind noch nicht gespeichert.",
                              buttons=["Abbrechen", "Verwerfen", "Speichern"], cancel_button=0, default_button=2)

        def done(d, res):
            try:
                i = d.choose_finish(res)
            except GLib.Error:
                return
            if i == 1:
                for t in dirty:
                    t.pdf_view.dirty = False
                    t.pdf_view._sync_tools()
                proceed()
            elif i == 2 and all(t.pdf_view.save() for t in dirty):
                proceed()
        dlg.choose(self.win, None, done)

    def do_open(self, files, n, hint):
        paths = [f.get_path() for f in files if f.get_path()]
        for p in [p for p in paths if p.lower().endswith(".pdf")]:     # PDFs gehören in den schlanken Editor
            open_in_pdf_editor(p)
        paths = [p for p in paths if not p.lower().endswith(".pdf")]
        if not paths:
            return
        self.do_activate()
        for p in paths:
            tab = self.current()
            if tab.page() != "home":
                tab = self.new_tab()
            tab.load_path(p)

    def _on_colors_changed(self, _mon, f, other, _ev):
        names = {x.get_basename() for x in (f, other) if x is not None}
        if os.path.basename(MATUGEN_CSS) not in names:
            return
        if self._css_timer:
            GLib.source_remove(self._css_timer)
        self._css_timer = GLib.timeout_add(250, self._colors_settled)

    def _colors_settled(self):
        self._css_timer = 0
        try:
            src = open(MATUGEN_CSS).read()
        except OSError:
            return False
        if "@define-color" not in src:          # matugen schreibt noch → gleich nochmal
            self._css_timer = GLib.timeout_add(250, self._colors_settled)
            return False
        self.load_css()
        return False

    def _redraw_all(self):
        """Selbst gezeichnete Teile (Kartentitel, Zeitleiste, Farbfelder …) lesen die Farbe beim Zeichnen –
        ihr eigener Stil ändert sich beim Farbwechsel aber nicht, also zeichnet GTK sie sonst nicht neu."""
        def walk(w):
            w.queue_draw()
            c = w.get_first_child()
            while c is not None:
                walk(c)
                c = c.get_next_sibling()
        for win in self.get_windows():
            walk(win)
        if getattr(self, "notebook", None) is not None:    # auch Tabs, die gerade nicht sichtbar sind
            for i in range(self.notebook.get_n_pages()):
                walk(self.notebook.get_nth_page(i))
        return False

    def load_css(self, *_):
        from home import HOME_CSS
        from cutter import CUTTER_CSS
        from recorder import RECORDER_CSS
        from studio import STUDIO_CSS
        from compress import COMPRESS_CSS
        from player import PLAYER_CSS
        css = FALLBACK_COLORS
        try:
            import istheme
            self._css_src, light = istheme.resolve()      # gewähltes Design, sonst matugen (hell/dunkel nach Wahl)
            istheme.apply_gtk(light)
        except ImportError:
            try:
                self._css_src = open(MATUGEN_CSS).read()
            except OSError:
                self._css_src = ""
        css += self._css_src
        import chrome
        from drawer import DRAWER_CSS
        PUBLISH_CSS = ""
        try:
            from swipe import CSS as SWIPE_CSS
        except ImportError:
            SWIPE_CSS = ""
        self.css.load_from_string(css + APP_CSS + EDITOR_CSS + meta.META_CSS + VIEWER_CSS + HOME_CSS + CUTTER_CSS + RECORDER_CSS + STUDIO_CSS + COMPRESS_CSS + PLAYER_CSS + glass.GLASS_CSS + chrome.CSS + CHROME_CSS + SUITE_CSS + DRAWER_CSS + PUBLISH_CSS + SWIPE_CSS)
        for tab in self.tabs():
            if tab.image is not None:
                tab.swatch.queue_draw()
            tab.editor.reload_colors()
        GLib.idle_add(self._redraw_all)

    def build(self):
        install_logo()
        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.css, Gtk.STYLE_PROVIDER_PRIORITY_USER)
        self.load_css()
        # Super+W: matugen schreibt colors.css (teils in mehreren Schritten) → entprellt neu laden.
        # Ordner statt Datei beobachten: übersteht auch Ersetzen per Umbenennen.
        self._css_timer = 0
        self.monitor = Gio.File.new_for_path(os.path.dirname(MATUGEN_CSS)).monitor_directory(
            Gio.FileMonitorFlags.WATCH_MOVES, None)
        self.monitor.set_rate_limit(100)
        self.monitor.connect("changed", self._on_colors_changed)
        try:
            import istheme
            self.theme_mon = istheme.watch(self.load_css)       # Design in den Einstellungen irgendeiner -ist-App
        except ImportError:
            pass

        self.win = Gtk.ApplicationWindow(application=self, title="Scourge")
        self.win.set_default_size(*getattr(glass, "fit_size", lambda w, h: (w, h))(1200, 780))   # kleine Bildschirme
        getattr(glass, "frame_window", lambda w: w.set_decorated(False))(self.win)   # Kachel-WM: rahmenlos
        self.win.add_css_class("pb-window")
        self.win.connect("close-request", self.on_close_request)
        cur = lambda: self.current()
        for name, cb, keys in (("open", lambda *_: cur().on_open(), ["<Control>o"]),
                               ("paste", lambda *_: cur().on_paste(), ["<Control>v"]),
                               ("edit", lambda *_: cur().on_edit(), ["<Control>e"]),
                               ("prev_page", lambda *_: cur().turn_page(-1), ["Page_Up"]),
                               ("next_page", lambda *_: cur().turn_page(1), ["Page_Down"]),
                               ("new_tab", lambda *_: self.new_tab(), ["<Control>t"]),
                               ("close_tab", lambda *_: self.close_tab(), ["<Control>w"]),
                               ("home", lambda *_: cur().go_home(), ["<Alt>Home"]),
                               ("pdf_focus", lambda *_: cur().set_focus_mode(not cur().focused), ["F11"]),
                               ("quit", lambda *_: self.win.close(), ["<Control>q"])):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", cb)
            self.win.add_action(act)
            self.set_accels_for_action(f"win.{name}", keys)

        import chrome as ch
        for i in range(1, 10):                      # Alt+1 … Alt+9: Tab wählen (wie die ¹²³ an den Tabs)
            act = Gio.SimpleAction.new(f"tab{i}", None)
            act.connect("activate", lambda *_, n=i - 1: n < self.notebook.get_n_pages() and self.notebook.set_current_page(n))
            self.win.add_action(act)
            self.set_accels_for_action(f"win.tab{i}", [f"<Alt>{i}"])

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        import drawer
        self.drawer = drawer.AppDrawer(self, root)
        self.win.set_child(self.drawer)
        self.notebook = Gtk.Notebook(scrollable=True, show_border=False, vexpand=True)
        self.notebook.add_css_class("tabs")
        root.append(self.notebook)

        # oben links: Marken-Chip (wie datist/musicalist) – Klick = Startseite des aktiven Tabs
        brand = Gtk.Button(label="scourge", tooltip_text="Apps-Leiste ein/aus (auch: Maus an den linken Fensterrand)",
                           valign=Gtk.Align.CENTER, focus_on_click=False, margin_end=6)
        brand.add_css_class("brand")
        brand.connect("clicked", lambda *_: self.drawer.toggle())
        self.notebook.set_action_widget(brand, Gtk.PackType.START)
        # oben rechts: neuer Tab, Formate/Hilfe, Glas, schließen
        right = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER, margin_start=10)
        right.append(ch.button("󰐕 tab", "Neuer Tab (Strg+T)", action="win.new_tab"))
        # Version wie bei notist/datist: Klick zeigt den Changelog, nach einem Update hervorgehoben
        self.ver_btn = Gtk.Button(label=f"v{drawer.version()}", tooltip_text="changelog – was ist neu?",
                                  valign=Gtk.Align.CENTER, focus_on_click=False)
        self.ver_btn.add_css_class("tflat")
        self.ver_btn.add_css_class("off")
        self.ver_btn.connect("clicked", lambda *_: self.show_changelog())
        right.append(self.ver_btn)
        import changelog
        self.upd_btn = changelog.update_button("scourge", drawer.version(), notify=self.notify,
                                               restart=lambda: self.drawer.restart_self())
        right.append(self.upd_btn)
        help_btn = Gtk.MenuButton(child=Gtk.Label(label="󰋗"), tooltip_text="Welche Dateien kann Scourge prüfen?",
                                  valign=Gtk.Align.CENTER)
        help_btn.add_css_class("tbtn")
        help_btn.set_popover(formats_popover())
        self.help_btn = help_btn
        right.append(help_btn)
        self.glass_toggle = glass.GlassToggle(self.win)       # hält Fenster + Datei synchron; Schalter im Zahnrad
        import settings_menu
        self.settings_btn = settings_menu.build(self, self.glass_toggle, version=drawer.version(),
                                                changelog_cb=self.show_changelog,
                                                update_cb=lambda: self.upd_btn.emit("clicked"))
        right.append(self.settings_btn)
        close = Gtk.Button(label="✕", tooltip_text="Schließen (Strg+Q) – Scourge wartet im Hintergrund und ist beim nächsten Aufruf sofort da", action_name="win.quit", valign=Gtk.Align.CENTER,
                           focus_on_click=False)
        close.add_css_class("tclose")
        right.append(close)
        self.notebook.set_action_widget(right, Gtk.PackType.END)
        self.notebook.connect("switch-page", lambda nb, page, i: GLib.idle_add(lambda: (self.sync_actions(page, force=True), False)[-1]))
        self.notebook.connect("switch-page", lambda nb, page, i: GLib.idle_add(lambda: (self.apply_chrome(), False)[-1]))
        for sig in ("page-added", "page-removed", "page-reordered"):
            self.notebook.connect(sig, lambda *_: GLib.idle_add(lambda: (self.number_tabs(), False)[-1]))

        # unten: Statuszeile mit Meldungen (▸ …) und Tastenhinweisen
        status = Gtk.Box(spacing=12)
        status.add_css_class("tstatus")
        self.toast = ch.label("", "msg", hexpand=True, ellipsize=True)
        self.toast.set_selectable(True)
        status.append(self.toast)
        hints = Gtk.Box(spacing=12)
        keys = [("strg+o", "öffnen"), ("strg+e", "bearbeiten")]
        keys += [("strg+t", "tab"), ("alt+1-9", "tab wählen"), ("strg+w", "tab zu")]
        for k, v in keys:
            hints.append(ch.hint(k, v))
        status.append(hints)
        self.hints = hints
        # Statuszeile bestimmt nie die Mindestbreite (sonst lässt sich das Fenster nicht halbieren)
        status_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
                                           vscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True)
        status_scroll.set_child(status)
        root.append(status_scroll)
        self.status_scroll = status_scroll
        # schmales Fenster: Tastenhinweise ausblenden (wie bei datist/musicalist)
        self.win.add_tick_callback(lambda w, _c: (self.hints.get_visible() != (w.get_width() >= 980)
                                                  and self.hints.set_visible(w.get_width() >= 980), True)[-1])
        self.new_tab()

    def apply_chrome(self):
        """Kopf- und Statuszeile nur zeigen, wenn der aktive Tab nicht in der PDF-Arbeitsfläche ist."""
        tab = self.current()
        show = not getattr(tab, "focused", False)
        self.notebook.set_show_tabs(show)
        self.status_scroll.set_visible(show)

    # ---------- Tabs ----------
    def tabs(self):
        nb = getattr(self, "notebook", None)
        return [nb.get_nth_page(i) for i in range(nb.get_n_pages())] if nb else []

    def current(self):
        return self.notebook.get_nth_page(self.notebook.get_current_page())

    def new_tab(self):
        tab = Tab(self)
        box = Gtk.Box(spacing=3)
        key = Gtk.Label(label="")
        key.add_css_class("key")
        # Reiter teilen sich die ganze Kopfzeile (tab-expand); wird es eng, kürzt der Name auf „Fa…“ und erst
        # danach erscheinen Pfeile. Name füllt den Reiter → das ✕ steht immer am rechten Rand des Reiters.
        lbl = Gtk.Label(label="start", ellipsize=3, max_width_chars=24, width_chars=3, hexpand=True, xalign=0)
        close = Gtk.Button(label="✕", tooltip_text="Tab schließen (Strg+W)", focus_on_click=False)
        close.add_css_class("tab-close")
        box.append(key)
        tab._tab_key = key
        close.connect("clicked", lambda *_: self.close_tab(tab))
        box.append(lbl)
        box.append(close)
        middle = Gtk.GestureClick(button=2)  # Mittelklick schließt wie im Browser
        middle.connect("released", lambda *_: self.close_tab(tab))
        box.add_controller(middle)
        tab._tab_label = lbl
        i = self.notebook.append_page(tab, box)
        self.notebook.set_tab_reorderable(tab, True)
        self.notebook.get_page(tab).set_property("tab-expand", True)
        self.notebook.set_current_page(i)
        return tab

    def number_tabs(self):
        import chrome as ch
        for i, tab in enumerate(self.tabs()):
            if getattr(tab, "_tab_key", None):
                tab._tab_key.set_label(ch.SUP[i + 1] if i < 9 else "")

    def close_drawer(self):
        if getattr(self, "drawer", None):
            self.drawer.close()

    def close_tab(self, tab=None, force=False):
        tab = tab or self.current()
        cp = tab.pages.get("cutter")
        if not force and cp is not None and cp.needs_ask():
            return cp.ask_leave(lambda: self.close_tab(tab, force=True))
        if not force and tab.pdf_view.dirty:
            return self.confirm_unsaved([tab], lambda: self.close_tab(tab, force=True))
        tab.shutdown()
        label = self.notebook.get_tab_label(tab)       # Reiter mit ✕ – liegt außerhalb des Tab-Baums
        self.notebook.remove_page(self.notebook.page_num(tab))
        if self.notebook.get_n_pages() == 0:
            self.new_tab()
        # erst nach dem sicheren Schließen der Videos (cutter.retire_media: 3 s + 2 s) die Kreise lösen,
        # sonst bliebe der Tab samt Bildern für immer im Speicher (siehe chrome.release_tree)
        GLib.timeout_add_seconds(6, lambda: (ch.release_tree(tab), label and ch.release_tree(label),
                                             ch.trim_memory(), False)[-1])

    def update_tab_label(self, tab):
        if getattr(tab, "_tab_label", None):
            text = tab.title.lower() if tab.title in ("Start", "Aufnahme") else tab.title
            tab._tab_label.set_label(text)
            tab._tab_label.set_tooltip_text(tab.title)

    def sync_actions(self, tab=None, force=False):
        tab = tab or self.current()
        if tab is not self.current() and not force:
            return
        for name, on in tab.action_states().items():
            act = self.win.lookup_action(name)
            if act:
                act.set_enabled(on)

    # ---------- Zuletzt geöffnet ----------
    def recent(self):
        try:
            return [p for p in json.load(open(os.path.join(CONFIG, "recent.json"))) if os.path.exists(p)]
        except (OSError, ValueError):
            return []

    def add_recent(self, path):
        if not path or path.startswith(os.path.expanduser("~/.cache")):
            return
        items = [path] + [p for p in self.recent() if p != path]
        os.makedirs(CONFIG, exist_ok=True)
        json.dump(items[:10], open(os.path.join(CONFIG, "recent.json"), "w"))

    def remove_recent(self, path):
        os.makedirs(CONFIG, exist_ok=True)
        json.dump([p for p in self.recent() if p != path], open(os.path.join(CONFIG, "recent.json"), "w"))

    def clear_recent(self):
        os.makedirs(CONFIG, exist_ok=True)
        json.dump([], open(os.path.join(CONFIG, "recent.json"), "w"))

    # ---------- Meldungen ----------
    def notify(self, msg):
        self.toast.set_label("▸ " + str(msg).strip().replace("\n", " "))
        self.toast.set_tooltip_text(str(msg))
        if self._toast_id:
            GLib.source_remove(self._toast_id)
        self._toast_id = GLib.timeout_add(6000, self._hide_toast)

    def _hide_toast(self):
        self.toast.set_label("")
        self._toast_id = None
        return False

    def do_shutdown(self):
        for tab in self.tabs():  # laufende Videos/Aufnahmen sauber beenden, bevor GTK abbaut
            tab.shutdown()
        Gtk.Application.do_shutdown(self)


if __name__ == "__main__":
    if "--quit" in sys.argv:                 # wirklich beenden (z. B. nach einem Update)
        app = Scourge()
        app.register(None)
        if app.get_is_remote():
            app.activate_action("quit", None)
        sys.exit(0)
    if "--daemon" in sys.argv:               # Autostart: im Hintergrund bereithalten (Fenster öffnet sofort)
        app = Scourge()
        app.register(None)
        if app.get_is_remote():
            sys.exit(0)
        app.daemon = True
        code = app.run([sys.argv[0]])
        sys.stdout.flush(); sys.stderr.flush()
        os._exit(code)
    app = Scourge()
    code = app.run(sys.argv)
    sys.stdout.flush(); sys.stderr.flush()
    # GStreamer-Threads können beim normalen Python-Abbau noch Rückrufe auslösen und abstürzen:
    # deshalb direkt beenden (alles ist zu diesem Zeitpunkt gespeichert)
    os._exit(code)
