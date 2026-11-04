# PDF-Viewer und -Editor für Scourge: alle Seiten untereinander, Zoom, Seitensprung, Volltextsuche,
# Text auswählen/kopieren, Links folgen und Anmerkungen (Textmarker, Unterstreichen, Durchstreichen, Text,
# Stift, Rahmen, Notiz, Radierer) mit Rückgängig und Speichern.
# Anmerkungen sind echte PDF-Anmerkungen (Poppler) – andere PDF-Programme zeigen und bearbeiten sie weiter.
# Seiten werden erst gerendert, wenn sie ins Bild scrollen (auch dicke PDFs bleiben flüssig).
import ctypes
import threading
import os

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("PangoCairo", "1.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Gio, GLib, Graphene, Gtk, Pango, PangoCairo

from pdfextra import ExtraMixin

try:
    import glass
except ImportError:      # ohne Cursor-Ebene zeichnet der Direkt-Text seinen Cursor selbst
    glass = None

VIEWER_CSS = """
/* deckend: Hyprland rechnet den Glas-Blur nur für durchsichtige Flächen – eine durchsichtige Lesefläche kostete beim
   Scrollen ~5 ms je Bild (gemessen: 23 → 18 ms) */
scrolledwindow.pdf-bg { background-color: mix(@background, black, 0.22); border-radius: 10px; }
.pdf-page { background-color: white; box-shadow: 0 0 0 1px alpha(black, 0.35), 0 2px 0 1px alpha(black, 0.25); }
.pdf-bar { margin-bottom: 6px; }
.pgroup { background-color: alpha(@foreground, 0.06); border-radius: 9px; padding: 2px 4px; }
.pgroup button.pb:not(:checked), .pgroup button.pcolor:not(.active) { border-color: transparent; box-shadow: none; background: none; }
.pgroup button.pb:not(:checked):hover { background-color: alpha(@primary, 0.18); }
.pgroup button.pb:disabled { opacity: 0.35; }
.pgroup entry.pb, .pgroup searchentry.pb { background-color: alpha(@background, 0.35); border-color: transparent; }
.pgsep { background-color: alpha(@foreground, 0.18); min-width: 1px; }
.pdf-bar entry.pb { min-height: 24px; }
.pdf-tools { margin-bottom: 6px; }
.pdf-tools > flowboxchild { padding: 0; background: none; }
.pdf-tools button.pb.ptool { padding: 1px 6px; font-size: 14px; min-height: 0; min-width: 24px; }
.pdf-tools .tool-name { font-size: 12px; color: @secondary; }
.pdf-tools button.pcolor { padding: 2px; min-width: 0; min-height: 0; border-radius: 4px; background: none;
    border: 1px solid transparent; box-shadow: none; }
.pdf-tools button.pcolor:hover { border-color: alpha(@primary, 0.55); }
.pdf-tools button.pcolor.active { border-color: @primary; }
.pdf-tools .dirty { color: @tertiary; font-size: 12px; }
popover.pdf-pop > contents { padding: 2px; }
.pdf-pop-box { margin: 6px; }
/* kleinere Eingaben wie in filist: flache Zeile, 12 px Schrift */
.pb-window .pdf-tools spinbutton, .pb-window popover.pdf-pop spinbutton { min-height: 22px; font-size: 12px; }
.pb-window .pdf-tools spinbutton text, .pb-window popover.pdf-pop spinbutton text { min-width: 16px; padding: 0 4px; font-size: 12px; }
.pb-window .pdf-tools spinbutton > button, .pb-window popover.pdf-pop spinbutton > button { min-width: 16px; min-height: 0; padding: 0; }
.pb-window .pdf-tools dropdown > button, .pb-window popover.pdf-pop dropdown > button { min-height: 22px; padding: 0 6px; font-size: 12px; }
.pb-window .pdf-tools dropdown > button label, .pb-window popover.pdf-pop dropdown > button label { font-size: 12px; }
.pb-window .pdf-tools .tool-name { font-size: 12px; }
.pdf-color-preview { border: 1px solid alpha(@primary, 0.55); border-radius: 4px; min-width: 28px; min-height: 22px; }
.pdf-color-row .key { min-width: 12px; }
.pb-window .pdf-color-row scale.pb { min-height: 14px; margin: 0; }
.pdf-pop entry { min-width: 70px; }
popover.pdf-pop textview, popover.pdf-pop textview text { background-color: alpha(@background, 0.45); color: @foreground; }
popover.pdf-pop textview { border: 1px solid alpha(@primary, 0.45); border-radius: 4px; padding: 4px 6px; }
.hits { font-size: 11px; color: alpha(@foreground, 0.6); }
.diff-del { color: #ff8f8f; }
"""
GAP = 14
POS_FILE = os.path.expanduser("~/.config/scourge/pdf-pos.json")
_pos_cache = None


def _pos_all():
    global _pos_cache
    if _pos_cache is None:
        try:
            import json
            with open(POS_FILE) as f:
                _pos_cache = json.load(f)
        except (OSError, ValueError):
            _pos_cache = {}
    return _pos_cache


def pos_get(path):
    d = _pos_all().get(os.path.abspath(path)) if path else None
    return d if isinstance(d, dict) else None


def pos_put(path, d):
    import json
    allp = _pos_all()
    key = os.path.abspath(path)
    allp.pop(key, None)
    allp[key] = d                          # zuletzt benutzt ans Ende
    while len(allp) > 300:
        allp.pop(next(iter(allp)))
    try:
        os.makedirs(os.path.dirname(POS_FILE), exist_ok=True)
        with open(POS_FILE + ".tmp", "w") as f:
            json.dump(allp, f)
        os.replace(POS_FILE + ".tmp", POS_FILE)
    except OSError:
        pass

# (Schlüssel, Symbol, Name, Taste)
TOOLS = (("text", "󰊄", "text schreiben", "t"), ("select", "󰇀", "text auswählen", "v"),
         ("highlight", "󰸱", "markieren", "h"), ("underline", "󰊇", "unterstreichen", "u"),
         ("strike", "󰊀", "durchstreichen", "d"), ("ink", "󰏪", "stift", "p"), ("rect", "󰹟", "rahmen", "r"),
         ("note", "󰆉", "notiz", "n"), ("sign", "󰷻", "unterschrift", "s"), ("redact", "󰈉", "schwärzen", "x"),
         ("erase", "󰇾", "radierer", "e"))
TOOL_TIPS = {"select": "Text auswählen, kopieren, Links folgen", "highlight": "Text mit dem Textmarker markieren",
             "underline": "Text unterstreichen", "strike": "Text durchstreichen",
             "text": "Text auf die Seite setzen – Klick auf ein Textfeld bearbeitet es, Ziehen verschiebt es, Auswahl im Feld lässt sich einzeln formatieren",
             "ink": "Freihand zeichnen", "rect": "Rahmen aufziehen", "note": "Notiz anheften",
             "sign": "Gespeicherte Unterschrift per Klick auf die Seite setzen (Größe: Breite)",
             "redact": "Bereiche aufziehen, die endgültig geschwärzt werden sollen",
             "erase": "Anmerkung anklicken, um sie zu entfernen"}
MARKUP = ("highlight", "underline", "strike")
CUSTOM_COLORS = os.path.expanduser("~/.config/scourge/pdf-colors.json")
COLORS = (("gelb", "#ffd400"), ("grün", "#62d26f"), ("blau", "#3d8bff"), ("rot", "#e5322d"),
          ("pink", "#ff5fc4"), ("schwarz", "#1b1b1b"))
FONTS = (("Sans", "Sans"), ("Serif", "Serif"), ("Mono", "Monospace"))
DEFAULT_COLOR = {"sign": "#1a3d8f", "highlight": "#ffd400", "underline": "#e5322d", "strike": "#e5322d", "text": "#1b1b1b",
                 "ink": "#3d8bff", "rect": "#e5322d", "note": "#ffd400"}


def _poppler():
    gi.require_version("Poppler", "0.18")
    from gi.repository import Poppler
    return Poppler


def _rgb(hexcol):
    h = hexcol.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _pcolor(hexcol):
    P = _poppler()
    c = P.Color()
    r, g, b = _rgb(hexcol)
    c.red, c.green, c.blue = int(r * 65535), int(g * 65535), int(b * 65535)
    return c


def _hex_of(pc):
    if pc is None:
        return None
    return "#{:02x}{:02x}{:02x}".format(pc.red >> 8, pc.green >> 8, pc.blue >> 8)


def _prect(x1, y1, x2, y2):
    r = _poppler().Rectangle()
    r.x1, r.y1, r.x2, r.y2 = x1, y1, x2, y2
    return r


# ---- Freihand-Striche: poppler_path_new_from_array erwartet ein C-Feld, das PyGObject nicht bauen kann ----
class _Pt(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


_ink_lib = None


def _ink_api():
    global _ink_lib
    if _ink_lib is None:
        lib = ctypes.CDLL("libpoppler-glib.so.8")
        glib = ctypes.CDLL("libglib-2.0.so.0")
        glib.g_malloc.restype = ctypes.c_void_p
        glib.g_malloc.argtypes = [ctypes.c_size_t]
        lib.poppler_path_new_from_array.restype = ctypes.c_void_p
        lib.poppler_path_new_from_array.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        lib.poppler_path_free.argtypes = [ctypes.c_void_p]
        lib.poppler_annot_ink_set_ink_list.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
        ctypes.pythonapi.PyCapsule_GetPointer.restype = ctypes.c_void_p
        ctypes.pythonapi.PyCapsule_GetPointer.argtypes = [ctypes.py_object, ctypes.c_char_p]
        _ink_lib = (lib, glib)
    return _ink_lib


def set_ink(annot, strokes):
    """strokes: [[(x, y), …], …] in PDF-Koordinaten. Die Anmerkung muss schon auf der Seite liegen."""
    lib, glib = _ink_api()
    paths = (ctypes.c_void_p * len(strokes))()
    for k, s in enumerate(strokes):
        arr = (_Pt * len(s))(*[_Pt(x, y) for x, y in s])
        mem = glib.g_malloc(ctypes.sizeof(arr))   # Poppler übernimmt das Feld und gibt es mit g_free frei
        ctypes.memmove(mem, arr, ctypes.sizeof(arr))
        paths[k] = lib.poppler_path_new_from_array(mem, len(s))
    ptr = ctypes.pythonapi.PyCapsule_GetPointer(annot.__gpointer__, None)
    lib.poppler_annot_ink_set_ink_list(ptr, paths, len(strokes))
    for k in range(len(strokes)):
        lib.poppler_path_free(paths[k])


def border_width(annot, default=1.0):
    """poppler_annot_get_border_width hat einen Ausgabe-Parameter, den PyGObject nicht abbildet."""
    lib, _glib = _ink_api()
    try:
        fn = lib.poppler_annot_get_border_width
        fn.restype = ctypes.c_int
        fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_double)]
        w = ctypes.c_double(0)
        ptr = ctypes.pythonapi.PyCapsule_GetPointer(annot.__gpointer__, None)
        return w.value if fn(ptr, ctypes.byref(w)) else default
    except (AttributeError, OSError):
        return default


_GOBJ_SIZE = None


def annot_key(a):
    """Kennung der Poppler-internen Anmerkung (PopplerAnnot = GObject + Annot*). Nötig, weil jede Abfrage der
    Seite neue Python-Hüllen für dieselbe Anmerkung liefert und Poppler keine Namen vergibt."""
    global _GOBJ_SIZE
    if _GOBJ_SIZE is None:
        from gi.repository import GObject
        _GOBJ_SIZE = GObject.type_query(GObject.TYPE_OBJECT).instance_size
    _ink_api()
    ptr = ctypes.pythonapi.PyCapsule_GetPointer(a.__gpointer__, None)
    return ctypes.c_void_p.from_address(ptr + _GOBJ_SIZE).value


class _Ref:
    """Verweis auf „die“ Anmerkung über alle Rückgängig-Schritte hinweg (auch wenn sie durch eine Kopie ersetzt wird)."""
    __slots__ = ("a",)

    def __init__(self, a):
        self.a = a


def clone_annot(doc, page, a):
    """Frische Kopie einer Anmerkung auf die Seite legen. Grund: Poppler vergisst ein einmal entferntes Objekt
    beim Speichern, auch wenn es wieder eingefügt wurde – Rückgängig/Wiederholen braucht deshalb Kopien.
    Gibt None zurück, wenn der Typ nicht nachgebaut werden kann."""
    P = _poppler()
    T = P.AnnotType
    t = a.get_annot_type()
    r = a.get_rectangle()
    rect = _prect(r.x1, r.y1, r.x2, r.y2)
    color = a.get_color()
    if t in (T.HIGHLIGHT, T.UNDERLINE, T.STRIKE_OUT, T.SQUIGGLY):
        ctor = {T.HIGHLIGHT: P.AnnotTextMarkup.new_highlight, T.UNDERLINE: P.AnnotTextMarkup.new_underline,
                T.STRIKE_OUT: P.AnnotTextMarkup.new_strikeout, T.SQUIGGLY: P.AnnotTextMarkup.new_squiggly}[t]
        n = ctor(doc, rect, list(a.get_quadrilaterals()))
    elif t == T.FREE_TEXT:
        n = P.AnnotFreeText.new(doc, rect)
        fd = a.get_font_desc()
        lab = parse_label(a)
        if lab and fd:
            n.set_font_desc(font_desc(lab["font"], fd.size_pt or 14, lab["bold"], lab["italic"]))
            n.set_label(a.get_label())
        elif fd:
            n.set_font_desc(fd)
        fc = a.get_font_color()
        if fc:
            n.set_font_color(fc)
        n.set_border_width(border_width(a, 0))
    elif t == T.INK:
        n = P.AnnotInk.new(doc, rect)
        strokes = [[(pt.x, pt.y) for pt in (path.get_points() or [])] for path in (a.get_ink_list() or [])]
        if color:
            n.set_color(color)
        n.set_border_width(border_width(a))
        page.add_annot(n)
        set_ink(n, [st for st in strokes if st])
        n.set_contents(a.get_contents() or "")
        return n
    elif t in (T.SQUARE, T.CIRCLE):
        n = (P.AnnotSquare if t == T.SQUARE else P.AnnotCircle).new(doc, rect)
        ic = a.get_interior_color()
        if ic:
            n.set_interior_color(ic)
        n.set_border_width(border_width(a))
    elif t == T.TEXT:
        n = P.AnnotText.new(doc, rect)
        n.set_icon(a.get_icon() or "Note")
    else:
        return None
    if color and t != T.FREE_TEXT:
        n.set_color(color)
    if a.get_contents():
        n.set_contents(a.get_contents())
    try:
        n.set_opacity(a.get_opacity())
    except Exception:
        pass
    page.add_annot(n)
    return n


# ---------- Textfelder: Formatierung je Zeichen ----------
# Ein Textfeld ist für Poppler ein FreeText mit EINER Schrift. Teilformatierung (fett, kursiv, Farbe, Größe, Schrift
# je Zeichen) legt deshalb je Zeile und Lauf gleicher Formatierung einen eigenen FreeText ab. Zusammengehalten werden
# sie über das Label (/T) jeder Anmerkung: "SC1;<Feld>;<Zeile>;<Lauf>;<Schrift>;<bi>;<x>;<y>". Beim Bearbeiten setzt
# Scourge das Feld daraus wieder zusammen. (Poppler vergisst Schrift/Fett/Kursiv beim Speichern – das Label bewahrt sie.)
LABEL_TAG = "SC1"


def font_desc(font, size, bold=False, italic=False):
    P = _poppler()
    fd = P.FontDescription.new(font)
    fd.size_pt = size
    fd.weight = P.Weight.BOLD if bold else P.Weight.NORMAL
    fd.style = P.Style.ITALIC if italic else P.Style.NORMAL
    return fd


def make_label(gid, line, idx, st, x, y):
    return f"{LABEL_TAG};{gid};{line};{idx};{st[0]};{'b' if st[3] else ''}{'i' if st[4] else ''};{x:.2f};{y:.2f}"


def parse_label(a):
    try:
        p = (a.get_label() or "").split(";")
        if len(p) != 8 or p[0] != LABEL_TAG:
            return None
        return {"gid": p[1], "line": int(p[2]), "idx": int(p[3]), "font": p[4], "bold": "b" in p[5],
                "italic": "i" in p[5], "x": float(p[6]), "y": float(p[7])}
    except Exception:
        return None


def annot_style(a, fallback):
    """Formatierung eines FreeText als (Schrift, Größe, Farbe, fett, kursiv); fallback = Standard, wenn nichts bekannt ist."""
    font, size, color, bold, italic = fallback
    lab = parse_label(a)
    try:
        fd = a.get_font_desc()
    except Exception:
        fd = None
    if fd:
        if fd.size_pt:
            size = fd.size_pt
        if not lab and fd.font_name:
            f = next((f for _n, f in FONTS if f.lower() == fd.font_name.lower()), None)
            if f:
                font = f
    if lab:
        font, bold, italic = lab["font"], lab["bold"], lab["italic"]
    col = _hex_of(a.get_font_color())
    if col:
        color = col
    return (font, size, color, bold, italic)


_TEXT_CALIB = {}


def text_metrics(font, bold=False, italic=False):
    """Wo Poppler eine Freitext-Anmerkung zeichnet – relativ zu einem Pango-Layout derselben Schrift, als
    Vielfache der Schriftgröße: (dx, dy, Zeilenvorschub). Einmal pro Schrift an einem unsichtbaren Mini-PDF
    gemessen, damit die Live-Vorschau beim Tippen genau dort steht, wo der Text danach im PDF landet."""
    key = (font, bool(bold), bool(italic))
    if key in _TEXT_CALIB:
        return _TEXT_CALIB[key]
    res = (0.0, 0.0, 1.0)
    try:
        import io
        import cairo
        from PIL import Image
        P = _poppler()
        buf = io.BytesIO()
        surf = cairo.PDFSurface(buf, 700, 500)
        cairo.Context(surf).show_page()
        surf.finish()
        doc = P.Document.new_from_bytes(GLib.Bytes.new(buf.getvalue()), None)
        page = doc.get_page(0)
        W, H = page.get_size()
        S, x0, y0 = 100.0, 50.0, 50.0

        def box(text):
            a = P.AnnotFreeText.new(doc, _prect(x0, H - y0 - 400, x0 + 600, H - y0))
            a.set_font_desc(font_desc(font, S, bold, italic))
            a.set_font_color(_pcolor("#000000"))
            a.set_border_width(0)
            a.set_contents(text)
            page.add_annot(a)
            im = cairo.ImageSurface(cairo.FORMAT_RGB24, int(W), int(H))
            cr = cairo.Context(im)
            cr.set_source_rgb(1, 1, 1)
            cr.paint()
            page.render(cr)
            im.flush()
            page.remove_annot(a)
            pil = Image.frombuffer("RGBX", (im.get_width(), im.get_height()), bytes(im.get_data()), "raw", "BGRX",
                                   im.get_stride(), 1).convert("L")
            return pil.point(lambda v: 255 if v < 128 else 0).getbbox()
        one, two = box("H"), box("H\nH")
        lay = PangoCairo.create_layout(cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)))
        fd = Pango.FontDescription.from_string(font)
        fd.set_absolute_size(S * Pango.SCALE)
        if bold:
            fd.set_weight(Pango.Weight.BOLD)
        if italic:
            fd.set_style(Pango.Style.ITALIC)
        lay.set_font_description(fd)
        lay.set_text("H", -1)
        ink, _log = lay.get_pixel_extents()
        if one and two:
            res = ((one[0] - x0 - ink.x) / S, (one[1] - y0 - ink.y) / S, (two[3] - one[3]) / S)
    except Exception:
        pass
    _TEXT_CALIB[key] = res
    return res


def render(page, scale, marks=(), current=None):
    """Poppler-Seite -> Gdk.Texture (direkt aus dem Cairo-Puffer, ohne PNG-Umweg), optional mit Suchtreffern."""
    import cairo
    w, h = page.get_size()
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(1, int(w * scale)), max(1, int(h * scale)))
    cr = cairo.Context(surf)
    cr.set_source_rgb(1, 1, 1)
    cr.paint()
    cr.scale(scale, scale)
    page.render(cr)
    for i, (x1, y1, x2, y2) in enumerate(marks):
        cr.set_source_rgba(1, 0.55, 0, 0.45) if i == current else cr.set_source_rgba(1, 0.9, 0, 0.38)
        cr.rectangle(x1 - 1, y1 - 1, x2 - x1 + 2, y2 - y1 + 2)
        cr.fill()
    surf.flush()
    data = GLib.Bytes.new(bytes(surf.get_data()))
    return Gdk.MemoryTexture.new(surf.get_width(), surf.get_height(), Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED,
                                 data, surf.get_stride())


def _swatch(hexcol, size=14):
    da = Gtk.DrawingArea(content_width=size, content_height=size)
    r, g, b = _rgb(hexcol)

    def draw(_a, cr, w, h):
        cr.set_source_rgb(r, g, b)
        rad = 3
        cr.new_sub_path()
        cr.arc(w - rad, rad, rad, -1.5708, 0); cr.arc(w - rad, h - rad, rad, 0, 1.5708)
        cr.arc(rad, h - rad, rad, 1.5708, 3.1416); cr.arc(rad, rad, rad, 3.1416, 4.7124)
        cr.close_path()
        cr.fill()
    da.set_draw_func(draw)
    return da


class PageWidget(Gtk.Widget):
    """Eine Seite: Textur und Anmerkungs-Ebene in EINEM Widget (Picture + Overlay + DrawingArea pro Seite
    kostete beim Scrollen ~10 ms je Bild)."""
    __gtype_name__ = "ScourgePageWidget"

    def __init__(self, draw, active, i):
        super().__init__()
        self._active = active
        self.add_css_class("pdf-page")
        self._draw, self._i, self._tex = draw, i, None

    def set_paintable(self, tex):
        self._tex = tex
        self.queue_draw()

    def do_snapshot(self, snap):
        w, h = self.get_width(), self.get_height()
        snap.render_background(self.get_style_context(), 0, 0, w, h)
        if self._tex is not None:
            snap.append_texture(self._tex, Graphene.Rect().init(0, 0, w, h))
        if not self._active(self._i):      # leere Cairo-Ebene pro Seite kostete beim Scrollen ~6 ms je Bild
            return
        cr = snap.append_cairo(Graphene.Rect().init(0, 0, w, h))
        self._draw(self, cr, w, h, self._i)


class PdfView(ExtraMixin, Gtk.Box):
    bold = italic = False       # Formatierung der Werkzeugleiste (Text-Werkzeug)
    _syncing = False            # Werkzeugleiste wird gerade vom Programm gesetzt (löst keine Änderung aus)
    _tmode = None               # laufende Maus-Aktion im Textfeld: ("sel"|"word"|"move"|"pick", …)
    _lastclick = None
    fhover = None               # (Seite, Box): Textfeld unter der Maus (blauer Rahmen + Griff)

    def __init__(self, editable=False, notify=None, on_saved=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.editable = editable
        self.notify = notify or (lambda msg: None)
        self.on_saved = on_saved
        self.doc = None
        self.path = None
        self.zoom = 1.0
        self.fit = True
        self.pics, self.areas, self.sizes = [], [], []
        self.page_objs = {}
        self.layouts = {}       # Seite -> (Text, Zeichen-Rechtecke) für Auswahl und Markieren
        self.done = {}          # Seite -> Zoom, mit dem sie gerendert ist
        self._gen = 0           # zählt hoch, wenn fertige Renderings veralten (Zoom, Suche, neues Dokument)
        self._doc_id = 0
        self._jobs, self._inflight, self._wlock = [], {}, threading.Condition()
        self._worker = None
        self.hits = []          # [(Seite, (x1, y1, x2, y2))] in Seitenkoordinaten (oben links)
        self.hit_i = -1
        self.tool = "select"
        self.colors = dict(DEFAULT_COLOR)
        self.width = 2.0        # Strichstärke für Stift und Rahmen
        self.font_size = 14.0
        self.font = "Sans"
        self.sel = None         # (Seite, Index A, Index B)
        self.live = None        # laufende Zeichnung: (Seite, "ink", [Punkte]) oder (Seite, "rect", (x1, y1, x2, y2))
        self.hover = None       # (Seite, (x1, y1, x2, y2)) Anmerkung unter dem Radierer
        self.undo_stack, self.redo_stack = [], []
        self._refs = {}
        self.dirty = False
        self._drag = None
        self._caret_at = None
        self.edit = None        # Text, der gerade direkt auf der Seite getippt wird (dict)
        self.ghost = None       # (Seite, x, y): Vorschau-Cursor des Text-Werkzeugs unter der Maus
        self._blink_id = None
        self._caret_on = True
        self.im = Gtk.IMMulticontext()      # Tottasten, Compose usw. beim Tippen auf der Seite
        self.im.connect("commit", lambda _im, text: self._edit_insert(text))

        # Kopfzeile in Behältern: Seiten | Zoom | Suche | Aktionen, darunter Werkzeuge | Stil
        bar = Gtk.Box(spacing=8)
        bar.add_css_class("pdf-bar")
        self.nav = self._group()
        self.prev = self._btn("󰅁", "Vorherige Seite (Bild ↑)", lambda: self.skip_page(-1))
        self.page_lbl = Gtk.Label(label="", tooltip_text="Klick: zu einer Seite springen (Strg+G)", margin_start=2, margin_end=2)
        self.page_lbl.add_css_class("key")
        self.page_lbl.set_cursor_from_name("pointer")
        jump = Gtk.GestureClick()
        jump.connect("released", lambda *_: self.ask_page())
        self.page_lbl.add_controller(jump)
        self.next = self._btn("󰅂", "Nächste Seite (Bild ↓)", lambda: self.skip_page(1))
        for w in (self.prev, self.page_lbl, self.next):
            self.nav.append(w)
        bar.append(self.nav)
        zoomg = self._group()
        zoomg.append(self._btn("󰍴", "Verkleinern (Strg+−)", lambda: self.set_zoom(self.zoom / 1.25)))
        self.zoom_lbl = self._btn("Breite", "Auf Fensterbreite einpassen (Strg+0)", self.fit_width)
        zoomg.append(self.zoom_lbl)
        zoomg.append(self._btn("󰐕", "Vergrößern (Strg++)", lambda: self.set_zoom(self.zoom * 1.25)))
        bar.append(zoomg)
        bar.append(Gtk.Box(hexpand=True))
        sg = self._group()
        self.search = Gtk.SearchEntry(placeholder_text="Im PDF suchen", width_chars=12)
        self.search.add_css_class("pb")
        self.search.connect("activate", lambda *_: self.find(self.search.get_text()))
        self.search.connect("next-match", lambda *_: self.step_hit(1))
        self.search.connect("previous-match", lambda *_: self.step_hit(-1))
        self.search.connect("stop-search", lambda *_: self.find(""))
        sg.append(self.search)
        self.hit_lbl = Gtk.Label(label="", margin_end=4)
        self.hit_lbl.add_css_class("hits")
        sg.append(self.hit_lbl)
        bar.append(sg)
        self.bar = bar
        self.append(self._hscroll(bar))
        if editable:
            self.append(self._build_tools())
            bar.append(self.action_group)

        self.scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self.scroll.add_css_class("pdf-bg")
        self.pages = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=GAP, halign=Gtk.Align.CENTER,
                             margin_top=GAP, margin_bottom=GAP, margin_start=GAP, margin_end=GAP)
        self.scroll.set_child(self.pages)
        self.scroll.get_child().set_scroll_to_focus(False)   # Klick auf die Seite darf die Ansicht nicht verschieben
        self.set_focusable(True)
        body = Gtk.Box(vexpand=True)
        self.body = body
        body.append(self.scroll)
        self.append(body)
        self.extra_init(body)
        va = self.scroll.get_vadjustment()
        va.connect("value-changed", lambda *_: self.update())
        self.scroll.get_hadjustment().connect("changed", lambda *_: self.fit and GLib.idle_add(self._refit))
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)   # vor dem ScrolledWindow, das Pfeile sonst als Rollen nimmt
        self.add_controller(keys)
        self.keys = keys
        zoom = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        zoom.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        zoom.connect("scroll", self.on_scroll)
        self.scroll.add_controller(zoom)
        self._pinch_t = 0.0
        # Zwei Finger auseinander/zusammen (Touchpad/Touchscreen) = Zoom
        pinch = Gtk.GestureZoom()
        pinch.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        pinch.connect("begin", self._pinch_begin)
        pinch.connect("end", lambda *_: setattr(self, "_pinch_t", GLib.get_monotonic_time()))
        pinch.connect("cancel", lambda *_: setattr(self, "_pinch_t", GLib.get_monotonic_time()))
        pinch.connect("scale-changed", self._on_pinch)
        self.scroll.add_controller(pinch)
        self._pinch0, self._pinch_id = 1.0, None
        self._build_popovers()

    @staticmethod
    def _hscroll(child):
        # Leiste darf beim Verkleinern schrumpfen (wird dann seitlich verschiebbar statt das Fenster zu sperren)
        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.EXTERNAL, vscrollbar_policy=Gtk.PolicyType.NEVER,
                                propagate_natural_height=True)
        sc.set_child(child)
        return sc

    def set_bars_visible(self, on):
        """Kopfzeile und Werkzeuge aus-/einblenden (Vollbild: nur das PDF)."""
        c = self.get_first_child()
        while c is not None and c is not self.body:      # alles vor dem Seitenbereich (Popover-Kinder bleiben)
            c.set_visible(on)
            c = c.get_next_sibling()

    @staticmethod
    def _group(spacing=2):
        g = Gtk.Box(spacing=spacing, valign=Gtk.Align.CENTER)
        g.add_css_class("pgroup")
        return g

    @staticmethod
    def _sep():
        sp = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, margin_top=4, margin_bottom=4, margin_start=3, margin_end=3)
        sp.add_css_class("pgsep")
        return sp

    def _btn(self, label, tip, cb, *classes):
        b = Gtk.Button(label=label, tooltip_text=tip, focus_on_click=False)
        b.add_css_class("pb"); b.add_css_class("small")
        for c in classes:
            b.add_css_class(c)
        b.connect("clicked", lambda *_: cb())
        return b

    # ---------- Werkzeugleiste ----------
    def add_to_bar(self, widget):
        """Zusätzlicher Knopf rechts (z. B. Infospalte ein/aus): bei der Bearbeitung neben Speichern."""
        (self.action_row if self.editable else self.bar).append(widget)
        if self.editable and not widget.has_css_class("pb"):
            widget.add_css_class("pb")

    def _build_tools(self):
        # drei Gruppen, die bei schmalem Fenster untereinander rutschen statt aus dem Bild zu laufen
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=False, max_children_per_line=2,
                           column_spacing=8, row_spacing=4)
        flow.add_css_class("pdf-tools")
        row = self._group(spacing=2)
        self.tool_btns = {}
        group = None
        for key, icon, name, k in TOOLS:
            b = Gtk.ToggleButton(label=icon, tooltip_text=f"{name[:1].upper() + name[1:]} ({k.upper()}) – {TOOL_TIPS[key]}",
                                 focus_on_click=False)
            b.add_css_class("pb"); b.add_css_class("ptool")
            if group:
                b.set_group(group)
            group = group or b
            b.connect("toggled", lambda btn, key=key: btn.get_active() and self.set_tool(key))
            self.tool_btns[key] = b
            row.append(b)
        self.tool_name = Gtk.Label(label="auswählen", xalign=0, margin_start=4)
        self.tool_name.add_css_class("tool-name")
        row.append(self.tool_name)
        flow.append(row)
        row = self._group(spacing=4)
        self.style_group = row
        self.color_box = Gtk.Box(spacing=2, valign=Gtk.Align.CENTER)
        self.color_btns = {}
        for name, hexcol in COLORS:
            b = Gtk.Button(child=_swatch(hexcol), tooltip_text=name, focus_on_click=False, valign=Gtk.Align.CENTER)
            b.add_css_class("pcolor")
            b.connect("clicked", lambda *_, c=hexcol: self.set_color(c))
            self.color_btns[hexcol] = b
            self.color_box.append(b)
        for hexcol in self._load_custom():
            self._add_custom(hexcol)
        add = Gtk.Button(label="󰐕", tooltip_text="Eigene Farbe hinzufügen", focus_on_click=False,
                         valign=Gtk.Align.CENTER)
        add.add_css_class("pcolor")
        add.connect("clicked", lambda *_: self._pick_color())
        self.color_add = add
        self.color_box.append(add)
        row.append(self.color_box)
        row.append(self._sep())
        self.size_spin = Gtk.SpinButton.new_with_range(1, 500, 1)
        self.size_spin.set_width_chars(2)
        self.size_spin.add_css_class("pb")
        self.size_spin.set_valign(Gtk.Align.CENTER)
        self.size_spin.connect("value-changed", self._on_size)
        self.size_lbl = Gtk.Label(label="", margin_start=4)
        self.size_lbl.add_css_class("key")
        self.font_dd = Gtk.DropDown.new_from_strings([n for n, _ in FONTS])
        self.font_dd.set_tooltip_text("Schrift")
        self.font_dd.set_valign(Gtk.Align.CENTER)
        self.font_dd.connect("notify::selected", self._on_font)
        row.append(self.font_dd)
        self.bold_btn = self._fmt_btn("<b>B</b>", "Fett (Strg+B) – gilt für die Auswahl im Textfeld", "bold")
        self.italic_btn = self._fmt_btn("<i>I</i>", "Kursiv (Strg+I) – gilt für die Auswahl im Textfeld", "italic")
        row.append(self.bold_btn)
        row.append(self.italic_btn)
        row.append(self.size_lbl)
        row.append(self.size_spin)
        flow.append(row)
        row = self._group()
        self.action_group = row
        self.action_row = row
        self.dirty_lbl = Gtk.Label(label="")
        self.dirty_lbl.add_css_class("dirty")
        row.append(self.dirty_lbl)
        self.undo_btn = self._btn("󰕌", "Rückgängig (Strg+Z)", self.undo)
        self.redo_btn = self._btn("󰑎", "Wiederholen (Strg+Umschalt+Z)", self.redo)
        self.save_btn = self._btn("󰆓", "Anmerkungen ins PDF speichern (Strg+S)", lambda: self.save())
        self.save_as_btn = self._btn("󰳻", "Als neue Datei speichern (Strg+Umschalt+S)", self.save_as)
        for b in (self.undo_btn, self.redo_btn, self.save_btn, self.save_as_btn):
            row.append(b)
        for child in (flow.get_child_at_index(i) for i in range(2)):
            child.set_focusable(False)
        self.tool_btns["select"].set_active(True)
        self._sync_tools()
        return flow

    def _fmt_btn(self, markup, tip, which):
        b = Gtk.ToggleButton(tooltip_text=tip, focus_on_click=False, valign=Gtk.Align.CENTER)
        lb = Gtk.Label()
        lb.set_markup(markup)
        b.set_child(lb)
        b.add_css_class("pb")
        b.add_css_class("ptool")
        b.connect("toggled", lambda btn: self._on_fmt(which, btn.get_active()))
        return b

    @staticmethod
    def _load_custom():
        try:
            import json
            with open(CUSTOM_COLORS) as f:
                return [c for c in json.load(f) if isinstance(c, str) and len(c) == 7][:12]
        except (OSError, ValueError):
            return []

    def _save_custom(self):
        import json
        cols = [c for c, b in self.color_btns.items() if getattr(b, "custom", False)]
        try:
            os.makedirs(os.path.dirname(CUSTOM_COLORS), exist_ok=True)
            with open(CUSTOM_COLORS, "w") as f:
                json.dump(cols, f)
        except OSError:
            pass

    def _add_custom(self, hexcol):
        if hexcol in self.color_btns:
            return self.color_btns[hexcol]
        b = Gtk.Button(child=_swatch(hexcol), tooltip_text=f"{hexcol} – Rechtsklick entfernt die Farbe",
                       focus_on_click=False, valign=Gtk.Align.CENTER)
        b.add_css_class("pcolor")
        b.custom = True
        b.connect("clicked", lambda *_, c=hexcol: self.set_color(c))
        rc = Gtk.GestureClick(button=3)
        rc.connect("pressed", lambda *_, c=hexcol: self._remove_custom(c))
        b.add_controller(rc)
        self.color_btns[hexcol] = b
        add = getattr(self, "color_add", None)
        if add is None:
            self.color_box.append(b)
        else:                                      # vor dem „+“ einsortieren
            self.color_box.insert_child_after(b, add.get_prev_sibling())
        return b

    def _remove_custom(self, hexcol):
        b = self.color_btns.pop(hexcol, None)
        if b is not None:
            self.color_box.remove(b)
            self._save_custom()
            self._sync_tools()

    PRESETS = ("#ff8a00", "#a855f7", "#14b8a6", "#8b5a2b", "#9ca3af", "#ffffff", "#7c3aed", "#0ea5e9",
               "#84cc16", "#f43f5e", "#fde68a", "#111111")

    def _pick_color(self):
        """Eigene Farbe im -ist-Look: Vorschau, Hex-Feld, drei flache Regler, ein paar Vorschläge."""
        pop = Gtk.Popover(has_arrow=True, autohide=True)
        pop.add_css_class("pdf-pop")
        pop.add_css_class("formats")
        pop.set_parent(self.color_add)
        cur = _rgb(self.colors.get(self.tool, "#e5322d"))
        rgb = [round(c * 255) for c in cur]
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.add_css_class("pdf-pop-box")
        title = Gtk.Label(label="eigene farbe", xalign=0)
        title.add_css_class("card-title")
        box.append(title)
        top = Gtk.Box(spacing=8)
        prev = Gtk.DrawingArea(content_width=28, content_height=22)
        prev.add_css_class("pdf-color-preview")
        hexent = Gtk.Entry(max_width_chars=8, width_chars=8, placeholder_text="#rrggbb")
        hexent.add_css_class("pb")
        top.append(prev)
        top.append(hexent)
        box.append(top)
        state = {"busy": False}

        def hexcol():
            return "#{:02x}{:02x}{:02x}".format(*rgb)

        def draw(_a, cr, w, h):
            r, g, b = (c / 255 for c in rgb)
            cr.set_source_rgb(r, g, b)
            cr.rectangle(0, 0, w, h)
            cr.fill()
        prev.set_draw_func(draw)
        scales = []

        def sync(from_hex=False):
            state["busy"] = True
            for sc, v in zip(scales, rgb):
                sc.set_value(v)
            if not from_hex:
                hexent.set_text(hexcol())
            prev.queue_draw()
            state["busy"] = False
        for i, name in enumerate("rgb"):
            row = Gtk.Box(spacing=8)
            row.add_css_class("pdf-color-row")
            lb = Gtk.Label(label=name, xalign=0)
            lb.add_css_class("key")
            sc = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 255, 1)
            sc.set_draw_value(False)
            sc.set_hexpand(True)
            sc.set_size_request(150, -1)
            sc.add_css_class("pb")

            def moved(sc, i=i):
                if not state["busy"]:
                    rgb[i] = int(sc.get_value())
                    sync()
            sc.connect("value-changed", moved)
            scales.append(sc)
            row.append(lb)
            row.append(sc)
            box.append(row)

        def typed(e):
            t = e.get_text().strip().lstrip("#")
            if len(t) == 6 and all(c in "0123456789abcdefABCDEF" for c in t):
                rgb[:] = [int(t[j:j + 2], 16) for j in (0, 2, 4)]
                sync(from_hex=True)
        hexent.connect("changed", typed)
        presets = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=6, column_spacing=2,
                              row_spacing=2, homogeneous=False)
        for hc in self.PRESETS:
            b = Gtk.Button(child=_swatch(hc), focus_on_click=False, tooltip_text=hc)
            b.add_css_class("pcolor")
            b.connect("clicked", lambda *_, hc=hc: (rgb.__setitem__(slice(None), [round(c * 255) for c in _rgb(hc)]), sync()))
            presets.append(b)
        box.append(presets)

        def add(*_):
            hc = hexcol()
            if self._add_custom(hc) is not None:
                self._save_custom()
            self.set_color(hc)
            pop.popdown()
        ok = self._btn("󰐕 hinzufügen", "Farbe merken und benutzen", add)
        hexent.connect("activate", add)
        box.append(ok)
        pop.set_child(box)
        pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
        sync()
        pop.popup()
        hexent.grab_focus()

    def set_tool(self, key):
        if key != self.tool:
            self.commit_edit()
            self.tool = key
            self.sel = self.live = self.hover = self.ghost = self.fhover = None
            if self.editable and not self.tool_btns[key].get_active():
                self.tool_btns[key].set_active(True)
            self._redraw_all()
        cursor = {"select": "text", "highlight": "text", "underline": "text", "strike": "text", "text": "text",
                  "ink": "crosshair", "rect": "crosshair", "note": "copy", "erase": "default", "sign": "copy",
                  "redact": "crosshair"}[key]
        for a in self.areas:
            a.set_cursor_from_name(cursor)
        if self.editable:
            self._sync_tools()

    def set_color(self, hexcol):
        if self.tool in self.colors:
            self.colors[self.tool] = hexcol
        if self.tool == "text":
            self._fmt_apply(2, hexcol)
        self._sync_tools()

    def _on_size(self, spin):
        if self._syncing:
            return
        if self.tool == "text":
            self.font_size = spin.get_value()
            self._fmt_apply(1, self.font_size)
        elif self.tool in ("ink", "rect"):
            self.width = spin.get_value()
        elif self.tool == "sign":
            self.sign_width = spin.get_value()

    def _on_font(self, dd, _pspec):
        if self._syncing:
            return
        self.font = FONTS[dd.get_selected()][1]
        self._fmt_apply(0, self.font)

    def _sync_tools(self):
        if not self.editable:
            return
        self.tool_name.set_label(next(n for k, _, n, _ in TOOLS if k == self.tool))
        has_color = self.tool in self.colors
        self.color_box.set_visible(has_color)
        cur = self.colors.get(self.tool)
        for hexcol, b in self.color_btns.items():
            (b.add_css_class if hexcol == cur else b.remove_css_class)("active")
        sized = self.tool in ("text", "ink", "rect", "sign")
        self.font_dd.set_visible(self.tool == "text")
        self.bold_btn.set_visible(self.tool == "text")
        self.italic_btn.set_visible(self.tool == "text")
        self.size_spin.set_visible(sized)
        self.size_lbl.set_visible(sized)
        self._syncing = True
        try:
            if self.tool == "text":
                self.size_lbl.set_label("pt")
                self.size_spin.set_range(6, 500)
            elif self.tool == "sign":
                self.size_lbl.set_label("breite")
                self.size_spin.set_range(60, 400)
                self.size_spin.set_value(self.sign_width)
            elif sized:
                self.size_lbl.set_label("stärke")
                self.size_spin.set_range(1, 12)
                self.size_spin.set_value(self.width)
        finally:
            self._syncing = False
        self._sync_style_widgets()
        sg = getattr(self, "style_group", None)
        if sg is not None:       # leerer Behälter (z. B. beim Auswählen-Werkzeug) wird ganz ausgeblendet
            sg.set_visible(any(w.get_visible() for w in (self.color_box, self.font_dd, self.bold_btn, self.italic_btn,
                                                          self.size_lbl, self.size_spin)))
        self.undo_btn.set_sensitive(bool(self.undo_stack))
        self.redo_btn.set_sensitive(bool(self.redo_stack))
        self.save_btn.set_sensitive(self.dirty and bool(self.path))
        self.save_as_btn.set_sensitive(self.doc is not None)
        self.dirty_lbl.set_label(("● speichert gleich …" if self.path else "● ungespeichert") if self.dirty else "")
        if self.dirty:
            self._queue_autosave()
        self.save_btn.set_label("󰆓 speichern" if self.dirty else "󰆓")

    def _sync_style_widgets(self):
        """Schrift, Größe, Fett/Kursiv und Farbe der Leiste auf self.font … abstimmen (ohne Änderung auszulösen)."""
        if not self.editable:
            return
        self._syncing = True
        try:
            want = next((k for k, (_, f) in enumerate(FONTS) if f == self.font), 0)
            if self.font_dd.get_selected() != want:
                self.font_dd.set_selected(want)
            if self.tool == "text":
                self.size_spin.set_value(self.font_size)
            self.bold_btn.set_active(self.bold)
            self.italic_btn.set_active(self.italic)
            cur = self.colors.get(self.tool)
            for hexcol, b in self.color_btns.items():
                (b.add_css_class if hexcol == cur else b.remove_css_class)("active")
        finally:
            self._syncing = False

    # ---------- Popover: Auswahl-Aktionen, Text-/Notizeingabe ----------
    def _build_popovers(self):
        self.sel_pop = Gtk.Popover(has_arrow=True, autohide=True)
        self.sel_pop.add_css_class("pdf-pop")
        self.sel_pop.add_css_class("formats")
        self.sel_pop.set_parent(self)
        box = Gtk.Box(spacing=4)
        box.append(self._btn("󰆏 kopieren", "Auswahl kopieren (Strg+C)", lambda: (self.copy_selection(), self.sel_pop.popdown())))
        if self.editable:
            for kind, icon, name, _k in [t for t in TOOLS if t[0] in MARKUP]:
                box.append(self._btn(icon, name, lambda kind=kind: (self.markup_selection(kind), self.sel_pop.popdown())))
        self.sel_pop.set_child(box)
        pk = Gtk.EventControllerKey()          # der Auswahl-Popover nimmt den Fokus → Strg+C muss auch dort kopieren
        pk.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        pk.connect("key-pressed", lambda _c, kv, _kc, m: (self.copy_selection(), self.sel_pop.popdown(), True)[-1]
                   if (m & Gdk.ModifierType.CONTROL_MASK and Gdk.keyval_name(kv) in ("c", "C")) else False)
        self.sel_pop.add_controller(pk)

        self.in_pop = Gtk.Popover(has_arrow=True, autohide=True)
        self.in_pop.add_css_class("pdf-pop")
        self.in_pop.add_css_class("formats")
        self.in_pop.set_parent(self)
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.in_title = Gtk.Label(label="", xalign=0)
        self.in_title.add_css_class("card-title")
        v.append(self.in_title)
        self.in_text = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False)
        self.in_text.set_size_request(280, 80)
        ik = Gtk.EventControllerKey()
        ik.connect("key-pressed", self._in_key)
        self.in_text.add_controller(ik)
        v.append(self.in_text)
        row = Gtk.Box(spacing=6)
        self.in_font = Gtk.DropDown.new_from_strings([n for n, _ in FONTS])
        self.in_font.set_tooltip_text("Schrift")
        self.in_size = Gtk.SpinButton.new_with_range(6, 500, 1)
        self.in_size.set_tooltip_text("Schriftgröße (pt)")
        self.in_font_row = Gtk.Box(spacing=6)
        self.in_font_row.append(self.in_font)
        self.in_font_row.append(self.in_size)
        row.append(self.in_font_row)
        row.append(Gtk.Box(hexpand=True))
        self.in_del = self._btn("󰧧", "Entfernen", self._in_delete)
        row.append(self.in_del)
        row.append(self._btn("übernehmen", "Übernehmen (Strg+Enter)", self._in_commit))
        v.append(row)
        hint = Gtk.Label(label="strg+enter übernehmen · esc abbrechen", xalign=0)
        hint.add_css_class("hits")
        v.append(hint)
        self.in_pop.set_child(v)
        self._in_ctx = None

    def _point_to(self, pop, i, x, y):
        """Popover auf einen Punkt (Seitenkoordinaten, oben links) der Seite i zeigen lassen."""
        ok, pt = self.areas[i].compute_point(self, _gpoint(x * self.zoom, y * self.zoom))
        r = Gdk.Rectangle()
        r.x, r.y, r.width, r.height = int(pt.x), int(pt.y), 1, 1
        pop.set_pointing_to(r)
        pop.popup()

    def _open_input(self, kind, i, x, y, annot=None):
        """kind = "text" (Freitext) oder "note" (angeheftete Notiz)."""
        self._in_ctx = (kind, i, x, y, annot)
        self.in_title.set_label({"text": "text einfügen", "note": "notiz"}[kind] if annot is None
                                else {"text": "text bearbeiten", "note": "notiz bearbeiten"}[kind])
        self.in_font_row.set_visible(kind == "text")
        self.in_del.set_visible(annot is not None)
        text = (annot.get_contents() or "") if annot is not None else ""
        self.in_text.get_buffer().set_text(text)
        size, font = self.font_size, self.font
        if annot is not None and kind == "text":
            try:
                fd = annot.get_font_desc()
                size, font = fd.size_pt or size, fd.font_name or font
            except Exception:
                pass
        self.in_size.set_value(size)
        self.in_font.set_selected(next((k for k, (_, f) in enumerate(FONTS) if f == font), 0))
        self._point_to(self.in_pop, i, x, y)
        self.in_text.grab_focus()

    def _in_key(self, ctrl, keyval, code, mods):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and mods & Gdk.ModifierType.CONTROL_MASK:
            self._in_commit()
            return True
        if keyval == Gdk.KEY_Escape:
            self.in_pop.popdown()
            return True
        return False

    def _in_text(self):
        buf = self.in_text.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip("\n")

    def _in_commit(self):
        if not self._in_ctx:
            return
        kind, i, x, y, old = self._in_ctx
        text = self._in_text()
        self.in_pop.popdown()
        ops = []
        if old is not None:
            if old.get_annot_type() == _poppler().AnnotType.FREE_TEXT:
                r = old.get_rectangle()
                x, y = r.x1, self.sizes[i][1] - r.y2          # alte Lage beibehalten
            ops.append(("remove", i, old))
            self.page(i).remove_annot(old)
        if text.strip():
            if kind == "text":
                self.font_size = self.in_size.get_value()
                self.font = FONTS[self.in_font.get_selected()][1]
                a = self._make_free_text(i, x, y, text, self.font, self.font_size, self.colors["text"])
            else:
                a = self._make_note(i, x, y, text)
            ops.append(("add", i, a))
        if ops:
            self._push(ops)
            self._rerender(i)
        self._in_ctx = None
        if self.editable:
            self._sync_tools()

    def _in_delete(self):
        if self._in_ctx and self._in_ctx[4] is not None:
            _kind, i, _x, _y, old = self._in_ctx
            self.in_pop.popdown()
            self.page(i).remove_annot(old)
            self._push([("remove", i, old)])
            self._rerender(i)
            self._in_ctx = None

    # ---------- Laden & Zoom ----------
    def load(self, doc, path=None):
        if self.dirty and self.path and self.doc and not self.edit:      # vorige Datei noch schnell sichern
            self._autosave()
        self._stop_edit()
        self.pos_save()
        self.doc = doc
        self.path = path
        while (c := self.pages.get_first_child()):
            self.pages.remove(c)
        self.pics, self.areas, self.done, self.hits, self.hit_i = [], [], {}, [], -1
        self._gen += 1
        self._doc_id += 1
        self._async_failed = False
        self.page_objs, self.layouts = {}, {}
        self.sel = self.live = self.hover = None
        self._last_text = ""
        self.undo_stack, self.redo_stack = [], []
        self._refs = {}
        self.dirty = False
        self._off_key = None
        self.sizes = [doc.get_page(i).get_size() for i in range(doc.get_n_pages())]
        for i, _ in enumerate(self.sizes):
            da = PageWidget(self._draw_overlay, self._overlay_active, i)
            p = ov = da
            drag = Gtk.GestureDrag()
            drag.connect("drag-begin", self._drag_begin, i)
            drag.connect("drag-update", self._drag_update, i)
            drag.connect("drag-end", self._drag_end, i)
            da.add_controller(drag)
            motion = Gtk.EventControllerMotion()
            motion.connect("motion", self._motion, i)
            motion.connect("leave", lambda *_: (self._set_hover(None), self._set_fhover(None), self._clear_ghost()))
            da.add_controller(motion)
            self.pics.append(p)
            self.areas.append(da)
            self.pages.append(da)
        self.set_tool(self.tool)
        self.search.set_text("")
        self.hit_lbl.set_label("")
        self.fit = True
        self.scroll.get_vadjustment().set_value(0)
        self._sm_goal = None
        self._sync_tools()
        self._pos_ready = False
        want = pos_get(path)               # zuletzt gelesene Stelle dieser Datei wiederherstellen
        if want and want.get("i", 0) < len(self.sizes) and (want.get("i", 0) or want.get("f", 0) > 0.02):
            self._want_pos = (int(want["i"]), float(want.get("f", 0)))
            if not want.get("fit", True) and want.get("zoom"):
                self.fit = False
                GLib.idle_add(lambda: (self._apply_zoom(max(0.2, min(float(want["zoom"]), 6))), False)[-1])
            else:
                GLib.idle_add(self._refit)
            GLib.timeout_add(1500, self._pos_arm)
        else:
            self._want_pos = None
            GLib.idle_add(self._refit)
            GLib.timeout_add(700, self._pos_arm)
        self.extra_loaded()

    def page(self, i):
        if i not in self.page_objs:
            self.page_objs[i] = self.doc.get_page(i)
        return self.page_objs[i]

    def _refit(self):
        if not self.doc or not self.fit:
            return False
        avail = self.scroll.get_width() - 2 * GAP - 16
        if avail > 50 and self.sizes:
            z = avail / max(w for w, _ in self.sizes)
            if abs(z - self.zoom) > 0.01 or not self.done:
                self._apply_zoom(z)
        return False

    def fit_width(self):
        self.fit = True
        self._refit()

    def set_zoom(self, z):
        self.fit = False
        self._apply_zoom(max(0.2, min(z, 6)))

    def _anchor(self):
        """Bildmitte als (Seite, Anteil in der Seite) – bleibt beim Zoomen an derselben Stelle."""
        import bisect
        va = self.scroll.get_vadjustment()
        mid = va.get_value() + va.get_page_size() / 2
        offs = self.offsets()
        i = max(0, bisect.bisect_right(offs, mid) - 1)
        h = max(1.0, self.sizes[i][1] * self.zoom)
        return i, max(0.0, min((mid - offs[i]) / h, 1.0))

    def _apply_zoom(self, z):
        page = self.current_page()
        anchor = self._anchor() if self.doc and self.sizes else None
        if getattr(self, "_want_pos", None):
            anchor, self._want_pos = self._want_pos, None
        self.zoom = z
        self.done = {}
        self._gen += 1
        for p, (w, h) in zip(self.pics, self.sizes):
            p.set_size_request(int(w * z), int(h * z))
        self.zoom_lbl.set_label("Breite" if self.fit else f"{round(z * 100)} %")
        if anchor:
            GLib.idle_add(self._restore_anchor, *anchor)
        else:
            GLib.idle_add(lambda: (self.goto(page, render_only=False), False)[-1])

    def _restore_anchor(self, i, frac, tries=0):
        va = self.scroll.get_vadjustment()
        mid = self.offsets()[i] + frac * self.sizes[i][1] * self.zoom
        want = max(0, mid - va.get_page_size() / 2)
        if tries < 30 and va.get_upper() - va.get_page_size() < want - 1:   # Seiten noch nicht gelayoutet → gleich nochmal
            GLib.timeout_add(40, self._restore_anchor, i, frac, tries + 1)
            return False
        va.set_value(want)
        self.update()
        return False

    # ---------- Leseposition merken ----------
    def _pos_arm(self):
        self._pos_ready = True
        return False

    def pos_save(self):
        """Seite + Stelle + Zoom je Datei merken (auch nach Schließen/Hintergrund/neu Öffnen)."""
        self._pos_id = None
        if not (self.doc and self.path and self.sizes and getattr(self, "_pos_ready", False)):
            return False
        try:
            i, f = self._anchor()
            pos_put(self.path, {"i": i, "f": round(f, 4), "fit": self.fit, "zoom": round(self.zoom, 3)})
        except Exception:
            pass
        return False

    def _pos_queue(self):
        if getattr(self, "_pos_ready", False) and not getattr(self, "_pos_id", None):
            self._pos_id = GLib.timeout_add(800, self.pos_save)

    # ---------- Position ----------
    def offsets(self):
        key = (self.zoom, id(self.sizes), len(self.sizes))
        if getattr(self, "_off_key", None) == key:
            return self._off_val
        out = self._offsets()
        self._off_key, self._off_val = key, out
        return out

    def _offsets(self):
        y, out = GAP, []
        for w, h in self.sizes:
            out.append(y)
            y += int(h * self.zoom) + GAP          # wie set_size_request der Seiten (sonst driftet es pro Seite um <1 px)
        return out

    def current_page(self):
        if not self.doc:
            return 0
        import bisect
        mid = self.scroll.get_vadjustment().get_value() + self.scroll.get_height() / 3
        return max(0, bisect.bisect_right(self.offsets(), mid) - 1)

    def ask_page(self):
        """Kleines Feld unter der Seitenzahl: Nummer eintippen, Enter springt hin."""
        if not self.doc:
            return
        pop = Gtk.Popover(has_arrow=True, autohide=True)
        pop.add_css_class("pdf-pop")
        pop.add_css_class("formats")
        pop.set_parent(self.page_lbl)
        n = len(self.sizes)
        box = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        box.add_css_class("pdf-pop-box")
        lbl = Gtk.Label(label="zu seite")
        lbl.add_css_class("key")
        ent = Gtk.Entry(text=str(self.current_page() + 1), width_chars=4, max_width_chars=5, xalign=0.5)
        ent.add_css_class("pb")
        of = Gtk.Label(label=f"/ {n}")
        of.add_css_class("key")
        go_btn = self._btn("󰁔", "Springen (Enter)", lambda: go())

        def go(*_):
            try:
                i = int(ent.get_text().strip()) - 1
            except ValueError:
                return ent.add_css_class("error")
            self.goto(max(0, min(i, n - 1)))
            pop.popdown()
        ent.connect("activate", go)
        ent.connect("changed", lambda e: e.remove_css_class("error"))
        for w in (lbl, ent, of, go_btn):
            box.append(w)
        pop.set_child(box)
        pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
        pop.popup()
        ent.grab_focus()
        ent.select_region(0, -1)

    def _nav_ok(self, focus):
        """Pfeiltasten blättern, außer der Fokus liegt auf einem Regler/Auswahlfeld, das die Pfeile selbst braucht."""
        w = focus
        while w is not None and w is not self:
            if isinstance(w, (Gtk.DropDown, Gtk.SpinButton, Gtk.Scale, Gtk.Entry, Gtk.Text, Gtk.TextView)):
                return False
            w = w.get_parent()
        return True

    def skip_page(self, d):
        """Eine Seite vor/zurück. Ausgangsseite = zuletzt angesprungene, solange nicht dazwischen gescrollt wurde –
        sonst kippt die Zählung, wenn die letzten Seiten nicht mehr bündig nach oben passen."""
        va = self.scroll.get_vadjustment()
        nav = getattr(self, "_nav", None)
        base = nav[0] if nav and abs(va.get_value() - nav[1]) < 1.5 else self.current_page()
        i = max(0, min(base + d, len(self.sizes) - 1))
        self.goto(i)
        self._nav = (i, va.get_value())

    def smooth_by(self, dy):
        """Pfeiltasten: weich auf ein Ziel zugleiten (Tastenwiederholung addiert nur das Ziel) statt in 80-px-Sprüngen."""
        va = self.scroll.get_vadjustment()
        lo, hi = va.get_lower(), max(va.get_lower(), va.get_upper() - va.get_page_size())
        base = self._sm_goal if getattr(self, "_sm_goal", None) is not None else va.get_value()
        self._sm_goal = max(lo, min(base + dy, hi))
        if not getattr(self, "_sm_id", None):
            self._sm_last = 0
            self._sm_id = self.scroll.add_tick_callback(self._sm_tick)

    def _sm_tick(self, _w, clock):
        import math
        va = self.scroll.get_vadjustment()
        t = clock.get_frame_time()
        dt = min((t - self._sm_last) / 1e6, 0.05) if self._sm_last else 1 / 60
        self._sm_last = t
        goal, cur = self._sm_goal, va.get_value()
        if goal is None:
            self._sm_id = None
            return GLib.SOURCE_REMOVE
        if abs(goal - cur) < 0.5:
            va.set_value(goal)
            self._sm_goal = self._sm_id = None
            return GLib.SOURCE_REMOVE
        va.set_value(cur + (goal - cur) * (1 - math.exp(-dt / 0.07)))
        return GLib.SOURCE_CONTINUE

    def goto(self, i, render_only=False, y_in_page=0):
        if not self.doc:
            return
        i = max(0, min(i, len(self.sizes) - 1))
        self._sm_goal = None
        if not render_only:
            self.scroll.get_vadjustment().set_value(max(0, self.offsets()[i] - GAP + y_in_page * self.zoom))
        self.update()

    def update(self):
        if not self.doc:
            return
        self._pos_queue()
        va = self.scroll.get_vadjustment()
        top, bottom = va.get_value(), va.get_value() + max(va.get_page_size(), 400)
        offs = self.offsets()
        mid = (top + bottom) / 2
        want = []
        for i, o in enumerate(offs):
            h = self.sizes[i][1] * self.zoom
            m = max(2 * h, bottom - top)
            if o + h >= top - m and o <= bottom + m and self.done.get(i) != self.zoom:  # sichtbar oder in der Nähe
                vis = o + h >= top and o <= bottom
                want.append((0 if vis else 1, abs(o + h / 2 - mid), i))
            elif i in self.done and (o + h < top - 3 * m or o > bottom + 3 * m) and i < len(self.pics):
                self.pics[i].set_paintable(None)       # weit weg: Textur freigeben (19-MB-PDF belegte >1 GB)
                del self.done[i]
        want.sort()
        if self._async_ok():
            self._request([i for _v, _d, i in want])
        else:
            # sichtbare Seiten sofort, Nachbarn einzeln im Leerlauf (sonst ruckelt das Scrollen bei mehreren neuen Seiten)
            for vis, _d, i in want:
                if vis == 0:
                    self._render_page(i)
            if any(v == 1 for v, _d, _i in want) and not getattr(self, "_idle_render", None):
                self._idle_render = GLib.idle_add(self._render_next, priority=GLib.PRIORITY_LOW)
        cur = self.current_page()
        if getattr(self, "_cur_shown", None) != (cur, len(self.sizes)):
            self._cur_shown = (cur, len(self.sizes))
            self.page_lbl.set_label(f"Seite {cur + 1} / {len(self.sizes)}")
            self.prev.set_sensitive(cur > 0)
            self.next.set_sensitive(cur < len(self.sizes) - 1)

    # ---------- Rendern im Hintergrund ----------
    # Poppler rechnet eine Seite in ~30 ms – im Hauptthread war jede neue Seite ein verlorenes Bild. Der Arbeiter hat ein
    # EIGENES Dokument (Poppler ist pro Dokument nicht thread-sicher). Sobald das Dokument verändert wurde (Anmerkungen,
    # Rückgängig), zeigt nur das Hauptdokument den aktuellen Stand → dann wieder synchron.
    def _px_scale(self):
        """Bildschirmpixel je Layout-Pixel (auch gebrochen, z. B. 1,5) – bei 2 statt 1,5 wären die Texturen 78 % größer."""
        try:
            return max(1.0, self.get_native().get_surface().get_scale())
        except (AttributeError, TypeError):
            return float(self.get_scale_factor())

    def _async_ok(self):
        return bool(self.path and not self._async_failed and not self.dirty and not self.undo_stack
                    and not self.redo_stack and not self.edit and os.path.exists(self.path))

    def _request(self, order):
        scale = self.zoom * self._px_scale()
        jobs = []
        with self._wlock:
            for i in order:
                if self._inflight.get(i) == (self._gen, self.zoom):
                    continue
                marks = [r for p, r in self.hits if p == i]
                cur = None
                if 0 <= self.hit_i < len(self.hits) and self.hits[self.hit_i][0] == i:
                    cur = marks.index(self.hits[self.hit_i][1])
                jobs.append((self._gen, self._doc_id, self.path, i, self.zoom, scale, marks, cur))
            self._jobs = jobs
            self._wlock.notify()
        if jobs and self._worker is None:
            self._worker = threading.Thread(target=self._work, daemon=True)
            self._worker.start()

    def _work(self):
        wdoc, wkey, wpages = None, None, {}
        while True:
            with self._wlock:
                while not self._jobs:
                    self._wlock.wait()
                gen, did, path, i, zoom, scale, marks, cur = self._jobs.pop(0)
                self._inflight[i] = (gen, zoom)
            tex = None
            try:
                if wkey != (did, path):
                    wdoc = _poppler().Document.new_from_file(GLib.filename_to_uri(os.path.abspath(path)), None)
                    wkey, wpages = (did, path), {}
                if i not in wpages:
                    wpages[i] = wdoc.get_page(i)
                tex = render(wpages[i], scale, marks, cur)
            except Exception:
                wkey = None
            GLib.idle_add(self._got, gen, did, i, zoom, tex)

    def _got(self, gen, did, i, zoom, tex):
        with self._wlock:
            self._inflight.pop(i, None)
        if tex is None:
            self._async_failed = True                 # z. B. Passwort-PDF: Hauptdokument rendert synchron
            self.update()
        elif did == self._doc_id and gen == self._gen and zoom == self.zoom and i < len(self.pics):
            self.pics[i].set_paintable(tex)
            self.done[i] = zoom
        return False

    def _render_page(self, i):
        scale = self.zoom * self._px_scale()
        marks = [r for p, r in self.hits if p == i]
        cur = None
        if 0 <= self.hit_i < len(self.hits) and self.hits[self.hit_i][0] == i:
            cur = marks.index(self.hits[self.hit_i][1])
        self.pics[i].set_paintable(render(self.page(i), scale, marks, cur))
        self.done[i] = self.zoom

    def _render_next(self):
        self._idle_render = None
        if not self.doc:
            return False
        va = self.scroll.get_vadjustment()
        top, bottom = va.get_value(), va.get_value() + max(va.get_page_size(), 400)
        mid = (top + bottom) / 2
        offs = self.offsets()
        near = sorted((abs(offs[i] + self.sizes[i][1] * self.zoom / 2 - mid), i) for i in range(len(offs)))
        for _d, i in near:
            o = offs[i]
            h = self.sizes[i][1] * self.zoom
            m = max(2 * h, bottom - top)
            if o + h >= top - m and o <= bottom + m and self.done.get(i) != self.zoom:
                self._render_page(i)
                self._idle_render = GLib.idle_add(self._render_next, priority=GLib.PRIORITY_LOW)
                break
        return False

    def _rerender(self, i):
        self.done.pop(i, None)
        self.layouts.pop(i, None)
        self.update()
        self.areas[i].queue_draw()

    def _redraw_all(self):
        for a in self.areas:
            a.queue_draw()

    # ---------- Suche ----------
    def find(self, text):
        self.hits, self.hit_i = [], -1
        if text.strip() and self.doc:
            for i, (w, h) in enumerate(self.sizes):
                for r in self.page(i).find_text(text) or []:
                    self.hits.append((i, (r.x1, h - max(r.y1, r.y2), r.x2, h - min(r.y1, r.y2))))  # unten-links -> oben-links
        self.done = {}
        self._gen += 1
        self.hits_changed(text)
        if self.hits:
            self.step_hit(1)
        else:
            self.hit_lbl.set_label("nicht gefunden" if text.strip() else "")
            self.update()

    def step_hit(self, d):
        if not self.hits:
            return
        self.hit_i = (self.hit_i + d) % len(self.hits)
        self.hit_lbl.set_label(f"{self.hit_i + 1} / {len(self.hits)}")
        page, (x1, y1, x2, y2) = self.hits[self.hit_i]
        self.done = {}
        self._gen += 1
        self.goto(page, y_in_page=max(0, y1 - 120))

    # ---------- Text auf der Seite ----------
    def layout(self, i):
        if i not in self.layouts:
            page = self.page(i)
            text = page.get_text() or ""
            ok, rects = page.get_text_layout()
            rects = list(rects or []) if ok else []
            n = min(len(text), len(rects))
            self.layouts[i] = (text[:n], rects[:n])
        return self.layouts[i]

    def char_at(self, i, x, y):
        """Nächstes Zeichen zum Punkt (Seitenkoordinaten) -> (Index, Abstand)."""
        text, rects = self.layout(i)
        best, bd = None, 1e9
        for k, r in enumerate(rects):
            if text[k] in "\n\r":
                continue
            dy = 0 if r.y1 <= y <= r.y2 else min(abs(y - r.y1), abs(y - r.y2))
            dx = 0 if r.x1 <= x <= r.x2 else min(abs(x - r.x1), abs(x - r.x2))
            d = dy * 4 + dx                           # gleiche Zeile zählt mehr als gleiche Spalte
            if d < bd:
                best, bd = k, d
        return best, bd

    def sel_range(self):
        if not self.sel:
            return None
        i, a, b = self.sel
        return i, min(a, b), max(a, b)

    def sel_lines(self, i, lo, hi):
        """Zeilenweise Rechtecke der Zeichen lo..hi (oben-links)."""
        text, rects = self.layout(i)
        lines = []
        for k in range(lo, hi + 1):
            if text[k] in "\n\r":
                continue
            r = rects[k]
            if r.x2 - r.x1 <= 0.01:
                continue
            if lines:
                L = lines[-1]
                same = abs((r.y1 + r.y2) / 2 - (L[1] + L[3]) / 2) < max(2.0, (L[3] - L[1]) * 0.5) and r.x1 >= L[0] - 2
                if same:
                    L[0], L[1], L[2], L[3] = min(L[0], r.x1), min(L[1], r.y1), max(L[2], r.x2), max(L[3], r.y2)
                    continue
            if text[k].isspace():
                continue                                # Leerzeichen am Zeilenanfang nicht markieren
            lines.append([r.x1, r.y1, r.x2, r.y2])
        return [tuple(l) for l in lines]

    def selected_text(self):
        rng = self.sel_range()
        if not rng:
            return ""
        i, lo, hi = rng
        return self.layout(i)[0][lo:hi + 1].strip()

    def copy_selection(self):
        text = self.selected_text() or getattr(self, "_last_text", "")
        if text:
            self.get_clipboard().set_content(Gdk.ContentProvider.new_for_bytes(
                "text/plain;charset=utf-8", GLib.Bytes.new(text.encode())))
            n = len(text.split())
            self.notify(f"{n} Wort{'e' if n != 1 else ''} kopiert")

    # ---------- Anmerkungen ----------
    def _make_markup(self, kind, i, lines, color):
        P = _poppler()
        h = self.sizes[i][1]
        quads = []
        for x1, y1, x2, y2 in lines:
            q = P.Quadrilateral()
            for name, (px, py) in zip(("p1", "p2", "p3", "p4"), ((x1, h - y1), (x2, h - y1), (x1, h - y2), (x2, h - y2))):
                pt = P.Point()
                pt.x, pt.y = px, py
                setattr(q, name, pt)
            quads.append(q)
        rect = _prect(min(l[0] for l in lines), h - max(l[3] for l in lines),
                      max(l[2] for l in lines), h - min(l[1] for l in lines))
        ctor = {"highlight": P.AnnotTextMarkup.new_highlight, "underline": P.AnnotTextMarkup.new_underline,
                "strike": P.AnnotTextMarkup.new_strikeout}[kind]
        a = ctor(self.doc, rect, quads)
        a.set_color(_pcolor(color))
        self.page(i).add_annot(a)
        return a

    def markup_selection(self, kind):
        rng = self.sel_range()
        if not rng or not self.editable:
            return
        i, lo, hi = rng
        lines = self.sel_lines(i, lo, hi)
        self._last_text = self.selected_text()      # nach dem Markieren ist die Auswahl weg – Strg+C kopiert den Text trotzdem
        if lines:
            a = self._make_markup(kind, i, lines, self.colors[kind])
            self._push([("add", i, a)])
            self.sel = None
            self._rerender(i)

    def _text_size(self, text, st):
        lay = self._lay(self._mc(), text, st)
        w, h = lay.get_size()
        return w / Pango.SCALE, h / Pango.SCALE

    def _ft(self, i, x, y, text, st, label):
        """Eine FreeText-Anmerkung; (x, y) = linke obere Ecke (Seitenkoordinaten). Rahmen eng am Text."""
        P = _poppler()
        w, h = self.sizes[i]
        tw, th = self._text_size(text, st)
        pad = 3
        # Luft nach rechts (proportional zur Länge): Poppler bricht sonst schon vor dem Seitenrand/Rahmenende um, und
        # lange Zeilen dürfen über den Rand hinausragen statt umgebrochen zu werden – so bleibt das Ergebnis wie die Vorschau
        x2 = x + tw * 1.06 + 2 * pad + st[1] * 0.8
        y2 = min(h, y + th + 2 * pad + st[1] * 0.2)
        a = P.AnnotFreeText.new(self.doc, _prect(x, h - y2, x2, h - y))
        a.set_font_desc(font_desc(st[0], st[1], st[3], st[4]))
        a.set_font_color(_pcolor(st[2]))
        a.set_border_width(0)
        a.set_contents(text)
        a.set_label(label)
        self.page(i).add_annot(a)
        return a

    def _make_field(self, i, x, y, text, fmt):
        """Textfeld aus Text + Formatierung je Zeichen als Anmerkung(en) auf die Seite legen -> Liste.
        Einheitliche Formatierung = eine Anmerkung; gemischte = je Zeile und Lauf eine (siehe parse_label)."""
        import uuid
        gid = uuid.uuid4().hex[:8]
        if len(set(fmt)) <= 1:
            st = fmt[0]
            return [self._ft(i, x, y, text, st, make_label(gid, 0, 0, st, x, y))]
        out = []
        for L in self._geom(text, fmt, x, y)["lines"]:
            for n, r in enumerate(L["runs"]):
                st = r["st"]
                dx, dy, _a = text_metrics(st[0], st[3], st[4])
                out.append(self._ft(i, r["x"] - dx * st[1], r["y"] - dy * st[1], r["text"], st,
                                    make_label(gid, L["k"], n, st, x, y)))
        return out

    def _make_free_text(self, i, x, y, text, font, size, color):
        st = (font, size, color, False, False)
        return self._make_field(i, x, y, text, [st] * len(text))[0]

    def _make_note(self, i, x, y, text):
        P = _poppler()
        h = self.sizes[i][1]
        a = P.AnnotText.new(self.doc, _prect(x, h - y - 20, x + 20, h - y))
        a.set_icon("Comment")
        a.set_color(_pcolor(self.colors["note"]))
        a.set_contents(text)
        self.page(i).add_annot(a)
        return a

    def _make_ink(self, i, pts, color, width):
        P = _poppler()
        h = self.sizes[i][1]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        m = width + 2
        a = P.AnnotInk.new(self.doc, _prect(min(xs) - m, h - max(ys) - m, max(xs) + m, h - min(ys) + m))
        a.set_color(_pcolor(color))
        a.set_border_width(width)
        self.page(i).add_annot(a)
        set_ink(a, [[(x, h - y) for x, y in pts]])
        return a

    def _make_rect(self, i, box, color, width):
        P = _poppler()
        h = self.sizes[i][1]
        x1, y1, x2, y2 = box
        a = P.AnnotSquare.new(self.doc, _prect(min(x1, x2), h - max(y1, y2), max(x1, x2), h - min(y1, y2)))
        a.set_color(_pcolor(color))
        a.set_border_width(width)
        self.page(i).add_annot(a)
        return a

    def annots_at(self, i, x, y, tol=3):
        """Anmerkungen unter dem Punkt, oberste zuerst: [(annot, (x1, y1, x2, y2) oben-links)]."""
        P = _poppler()
        skip = {P.AnnotType.LINK, P.AnnotType.WIDGET, P.AnnotType.POPUP}
        h = self.sizes[i][1]
        out = []
        for m in self.page(i).get_annot_mapping():
            a = m.annot
            if a.get_annot_type() in skip:
                continue
            r = m.area
            box = (r.x1, h - r.y2, r.x2, h - r.y1)
            if a.get_annot_type() == P.AnnotType.FREE_TEXT:          # Textfeld: enge Umrandung statt Rahmen mit Luft
                box = self._annot_box(i, a)
            if box[0] - tol <= x <= box[2] + tol and box[1] - tol <= y <= box[3] + tol:
                out.append((a, box))
        return out[::-1]

    def _annot_box(self, i, a):
        """Enge Umrandung eines FreeText (oben-links): so groß wie der gesetzte Text, ohne die Luft des PDF-Rahmens."""
        st = annot_style(a, self._cur_style())
        r = a.get_rectangle()
        dx, dy, adv = text_metrics(st[0], st[3], st[4])
        lines = (a.get_contents() or "").split("\n")
        cr = self._mc()
        w = max(self._lay(cr, l or " ", st).get_size()[0] for l in lines) / Pango.SCALE
        hh = self._lay(cr, "Hg", st).get_size()[1] / Pango.SCALE
        x, y = r.x1 + dx * st[1], self.sizes[i][1] - r.y2 + dy * st[1]
        return (x, y, x + w, y + (len(lines) - 1) * adv * st[1] + hh)

    def field_members(self, i, a):
        """Alle Anmerkungen des Textfelds, zu dem a gehört (bei Teilformatierung mehrere), nach Zeile/Lauf sortiert."""
        lab = parse_label(a)
        if not lab:
            return [a]
        P = _poppler()
        mem = []
        for m in self.page(i).get_annot_mapping():
            b = m.annot
            if b.get_annot_type() == P.AnnotType.FREE_TEXT:
                l = parse_label(b)
                if l and l["gid"] == lab["gid"]:
                    mem.append((l["line"], l["idx"], b))
        mem.sort(key=lambda t: (t[0], t[1]))
        return [b for _l, _n, b in mem] or [a]

    def _field_box(self, i, mem):
        bs = [self._annot_box(i, a) for a in mem]
        return (min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs))

    def fields_at(self, i, x, y, tol=2):
        """Textfelder unter dem Punkt, oberstes zuerst: [(Mitglieder, enge Box)]."""
        P = _poppler()
        out, seen = [], set()
        for a, _box in self.annots_at(i, x, y, tol):
            if a.get_annot_type() != P.AnnotType.FREE_TEXT:
                continue
            mem = self.field_members(i, a)
            if annot_key(mem[0]) in seen:
                continue
            seen.add(annot_key(mem[0]))
            out.append((mem, self._field_box(i, mem)))
        return out

    def _ref(self, a):
        k = annot_key(a)
        r = self._refs.get(k)
        if r is None:
            r = self._refs[k] = _Ref(a)
        return r

    def _push(self, ops):
        ops = [(kind, i, self._ref(a)) for kind, i, a in ops]
        self.undo_stack.append(ops)
        self.redo_stack.clear()
        self.dirty = True
        self._sync_tools()

    def _apply(self, ops, inverse):
        pages = set()
        for kind, i, ref in (reversed(ops) if inverse else ops):
            add = (kind == "add") != inverse
            page = self.page(i)
            if add:
                n = clone_annot(self.doc, page, ref.a)
                if n is None:              # unbekannter Typ: Original zurücklegen (bestmöglich)
                    page.add_annot(ref.a)
                else:
                    self._refs.pop(annot_key(ref.a), None)
                    ref.a = n
                    self._refs[annot_key(n)] = ref
            else:
                page.remove_annot(ref.a)
            pages.add(i)
        for i in pages:
            self._rerender(i)

    def undo(self):
        if self.edit:                    # beim Tippen: Eingabe verwerfen
            return self.cancel_edit()
        if self.tool == "redact" and self.redact_undo():
            return
        if not self.undo_stack and self.struct_undo():
            return
        if self.undo_stack:
            ops = self.undo_stack.pop()
            self._apply(ops, True)
            self.redo_stack.append(ops)
            self.dirty = True
            self._sync_tools()

    def redo(self):
        if not self.redo_stack and self.struct_redo and self.struct_undo(redo=True):
            return
        if self.redo_stack:
            ops = self.redo_stack.pop()
            self._apply(ops, False)
            self.undo_stack.append(ops)
            self.dirty = True
            self._sync_tools()

    # ---------- Speichern ----------
    # ---------- automatisch speichern ----------
    # Anmerkungen landen ohne Strg+S direkt in der geöffneten Datei (keine Kopie). Vor dem ersten Überschreiben
    # wird das Original einmal nach ~/.cache/scourge/originale gesichert.
    AUTOSAVE_MS = 1200
    AUTOSAVE_REMOTE_MS = 4000

    def _queue_autosave(self):
        if not (self.editable and self.dirty and self.path and self.doc):
            return
        if getattr(self, "_auto_id", None):
            GLib.source_remove(self._auto_id)
        import netfs as formats
        ms = self.AUTOSAVE_REMOTE_MS if formats.is_remote(self.path) else self.AUTOSAVE_MS
        self._auto_id = GLib.timeout_add(ms, self._autosave)

    def _autosave(self):
        self._auto_id = None
        if not (self.dirty and self.path and self.doc):
            return False
        if self.edit:                     # tippt gerade direkt auf der Seite → später
            self._queue_autosave()
            return False
        self._backup_original(self.path)
        self.save(quiet=True)
        return False

    def _backup_original(self, path):
        import hashlib, shutil
        done = self.__dict__.setdefault("_backed_up", set())
        if path in done:
            return
        done.add(path)
        d = os.path.expanduser("~/.cache/scourge/originale")
        try:
            os.makedirs(d, exist_ok=True)
            tag = hashlib.sha1(path.encode()).hexdigest()[:8]
            dest = os.path.join(d, f"{tag}-{os.path.basename(path)}")
            if not os.path.exists(dest):
                shutil.copy2(path, dest)
            # alte Sicherungen nach 30 Tagen wegräumen
            import time
            for f in os.listdir(d):
                fp = os.path.join(d, f)
                if time.time() - os.path.getmtime(fp) > 30 * 86400:
                    os.remove(fp)
        except OSError:
            pass

    def save(self, path=None, quiet=False):
        if getattr(self, "_auto_id", None):
            GLib.source_remove(self._auto_id)
            self._auto_id = None
        self.commit_edit()
        target = path or self.path
        if not self.doc or not target:
            return False
        tmp = os.path.join(os.path.dirname(target) or ".", f".{os.path.basename(target)}.scourge-tmp")
        try:
            import netfs as formats
            if formats.is_remote(target):
                # Netzlaufwerk: lokal schreiben, dann am Stück hochladen (Poppler schreibt in vielen kleinen Stücken)
                import shutil, tempfile
                fd, local = tempfile.mkstemp(suffix=".pdf")
                os.close(fd)
                try:
                    self.doc.save(GLib.filename_to_uri(local))
                    shutil.copyfile(local, tmp)
                finally:
                    os.remove(local)
            else:
                self.doc.save(GLib.filename_to_uri(os.path.abspath(tmp)))
            os.replace(tmp, target)
            formats._net_cache.pop(target, None)     # gespeicherte Fassung neu lesen
        except (GLib.Error, OSError) as e:
            try:
                os.remove(tmp)
            except OSError:
                pass
            self.notify(f"Speichern fehlgeschlagen: {getattr(e, 'message', None) or e}")
            return False
        self.path = target
        self.dirty = False
        self._sync_tools()
        if quiet:                         # automatisch: nur leise im Status, keine Neu-Analyse bei jedem Strich
            self.dirty_lbl.set_label("󰄬 automatisch gespeichert")
            return True
        self.notify(f"Gespeichert: {os.path.basename(target)}")
        if self.on_saved:
            self.on_saved(target)
        return True

    def save_as(self):
        if not self.doc:
            return
        dlg = Gtk.FileDialog(title="PDF speichern unter")
        if self.path:
            dlg.set_initial_folder(Gio.File.new_for_path(os.path.dirname(self.path)))
            base, ext = os.path.splitext(os.path.basename(self.path))
            dlg.set_initial_name(f"{base} (bearbeitet){ext or '.pdf'}")
        else:
            dlg.set_initial_name("dokument.pdf")

        def done(d, res):
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                self.save(f.get_path())
        dlg.save(self.get_root(), None, done)

    # ---------- Maus ----------
    def _pt(self, i, x, y):
        return x / self.zoom, y / self.zoom

    def _drag_begin(self, g, x, y, i):
        self.grab_focus()
        px, py = self._pt(i, x, y)
        self._drag = (i, px, py)
        self._tmode = None
        t = self.tool
        e = self.edit
        if self.editable and e and e["i"] == i and t == "text":
            g = self._geom_e()
            zone = self._edit_zone(g, px, py)
            if zone == "grip":
                self._tmode = ("move", e["x"], e["y"])
                return
            if zone == "text":
                n = self._count_click(i, x, y)
                idx = self._index_at(g, e["text"], px, py)
                if n == 1:
                    e["cur"], e["sel"] = idx, idx
                    self._tmode = ("sel",)
                elif n == 2:
                    lo, hi = self._word_bounds(e["text"], idx)
                    e["sel"], e["cur"] = lo, hi
                    self._tmode = ("word", lo, hi)
                else:
                    t_ = e["text"]
                    e["sel"], e["cur"] = t_.rfind("\n", 0, idx) + 1, (t_.find("\n", idx) if "\n" in t_[idx:] else len(t_))
                    self._tmode = ("line",)
                self._caret_on = True
                self._edit_changed()
                return
        elif self.editable and t in ("text", "select") and self.fields_at(i, px, py):
            self._tmode = ("pick", self.fields_at(i, px, py)[0][0])       # Ziehen = verschieben, Klick = bearbeiten
            self._set_fhover(None)
            return
        if t == "select" or t in MARKUP:
            k, d = self.char_at(i, px, py)
            self.sel = (i, k, k) if k is not None and d < 40 else None
            self._redraw_all()
        elif t == "ink":
            self.live = (i, "ink", [(px, py)])
        elif t == "rect":
            self.live = (i, "rect", (px, py, px, py))
        elif t == "redact":
            self.live = (i, "redact", (px, py, px, py))

    def _drag_update(self, g, dx, dy, i):
        if not self._drag:
            return
        _i, sx, sy = self._drag
        px, py = sx + dx / self.zoom, sy + dy / self.zoom
        t = self.tool
        m = self._tmode
        if m:
            e = self.edit
            if m[0] == "pick" and (abs(dx) > 3 or abs(dy) > 3) and self.editable:
                self.commit_edit()
                self.start_edit(i, sx, sy, m[1][0], typing=False)
                e = self.edit
                self._tmode = m = ("move", e["x"], e["y"], True)
            if m[0] == "move" and e:
                g = self._geom_e()
                bx1, by1, bx2, by2 = g["bounds"]
                W, H = self.sizes[i]
                nx, ny = m[1] + dx / self.zoom, m[2] + dy / self.zoom
                ox, oy = bx1 - e["x"], by1 - e["y"]          # Lage der Umrandung relativ zum Ursprung
                nx = max(-ox + 1, min(nx, W - (bx2 - e["x"]) - 1)) if (bx2 - bx1) < W else nx
                ny = max(-oy + 1, min(ny, H - (by2 - e["y"]) - 1)) if (by2 - by1) < H else ny
                e["x"], e["y"] = nx, ny
                self.areas[i].queue_draw()
            elif m[0] in ("sel", "word", "line") and e:
                g = self._geom_e()
                idx = self._index_at(g, e["text"], px, py)
                if m[0] == "sel":
                    e["cur"] = idx
                elif m[0] == "word":
                    lo, hi = self._word_bounds(e["text"], idx)
                    if idx >= m[2]:
                        e["sel"], e["cur"] = m[1], hi
                    else:
                        e["sel"], e["cur"] = m[2], lo
                self._caret_on = True
                self._edit_changed()
            return
        if (t == "select" or t in MARKUP) and self.sel:
            k, _d = self.char_at(i, px, py)
            if k is not None:
                self.sel = (i, self.sel[1], k)
        elif t == "ink" and self.live:
            pts = self.live[2]
            lx, ly = pts[-1]
            if (px - lx) ** 2 + (py - ly) ** 2 > 0.6:
                pts.append((px, py))
        elif t == "rect" and self.live:
            self.live = (i, "rect", (sx, sy, px, py))
        elif t == "redact" and self.live:
            self.live = (i, "redact", (sx, sy, px, py))
        self.areas[i].queue_draw()

    def _drag_end(self, g, dx, dy, i):
        if not self._drag:
            return
        _i, sx, sy = self._drag
        self._drag = None
        px, py = sx + dx / self.zoom, sy + dy / self.zoom
        click = abs(dx) < 3 and abs(dy) < 3
        t = self.tool
        m, self._tmode = self._tmode, None
        if m and m[0] in ("sel", "word", "line"):
            return
        if m and m[0] == "move" and self.edit:
            e = self.edit
            if click and len(m) == 3:                          # nur angeklickt: nichts verschoben
                e["x"], e["y"] = m[1], m[2]
            if len(m) == 4:                                    # aufgenommenes Feld: fertig verschoben → ablegen
                self.commit_edit()
            else:
                self.areas[e["i"]].queue_draw()
            return
        if t == "select":
            if click:
                self.sel = None
                self._redraw_all()
                if not (self.form_click(i, px, py) or self._follow_link(i, px, py)):
                    hit = [a for a, _ in self.annots_at(i, px, py)
                           if a.get_annot_type() in (_poppler().AnnotType.FREE_TEXT, _poppler().AnnotType.TEXT)]
                    if hit and self.editable:
                        if hit[0].get_annot_type() == _poppler().AnnotType.FREE_TEXT:
                            self.set_tool("text")
                            self.start_edit(i, px, py, hit[0])
                        else:
                            self._open_input("note", i, px, py, hit[0])
            elif self.selected_text():
                self._point_to(self.sel_pop, i, px, py)
        elif t in MARKUP:
            if not click:
                self.markup_selection(t)
            else:
                self.sel = None
                self._redraw_all()
        elif t == "text" and click:
            self.commit_edit()
            hit = [a for a, _ in self.annots_at(i, px, py)
                   if a.get_annot_type() == _poppler().AnnotType.FREE_TEXT]
            self.start_edit(i, px, py, hit[0] if hit else None)
        elif t == "note" and click:
            hit = [a for a, _ in self.annots_at(i, px, py) if a.get_annot_type() == _poppler().AnnotType.TEXT]
            self._open_input("note", i, px, py, hit[0] if hit else None)
        elif t == "ink" and self.live:
            pts = self.live[2]
            self.live = None
            if len(pts) == 1:
                pts = [pts[0], (pts[0][0] + 0.5, pts[0][1] + 0.5)]   # Punkt
            a = self._make_ink(i, pts, self.colors["ink"], self.width)
            self._push([("add", i, a)])
            self._rerender(i)
        elif t == "rect" and self.live:
            box = self.live[2]
            self.live = None
            if abs(box[2] - box[0]) > 3 and abs(box[3] - box[1]) > 3:
                a = self._make_rect(i, box, self.colors["rect"], self.width)
                self._push([("add", i, a)])
                self._rerender(i)
            else:
                self.areas[i].queue_draw()
        elif t == "sign" and click:
            self.place_signature(i, px, py)
        elif t == "redact" and self.live:
            box = self.live[2]
            self.live = None
            if abs(box[2] - box[0]) > 3 and abs(box[3] - box[1]) > 3:
                self.redact_add(i, box)
            else:
                self.areas[i].queue_draw()
        elif t == "erase" and click:
            hits = self.annots_at(i, px, py)
            if hits:
                a = hits[0][0]
                grp = self.field_members(i, a) if a.get_annot_type() == _poppler().AnnotType.FREE_TEXT else [a]
                for b in grp:
                    self.page(i).remove_annot(b)
                self._push([("remove", i, b) for b in grp])
                self.hover = None
                self._rerender(i)

    def _count_click(self, i, x, y):
        """Wievielter schneller Klick an derselben Stelle (Doppel-/Dreifachklick)."""
        now = GLib.get_monotonic_time() / 1000
        try:
            limit = Gtk.Settings.get_default().props.gtk_double_click_time
        except Exception:
            limit = 400
        l = self._lastclick
        n = l[3] + 1 if l and l[0] == i and now - l[1] < limit and abs(x - l[2][0]) < 6 and abs(y - l[2][1]) < 6 else 1
        self._lastclick = (i, now, (x, y), n)
        return n

    def _motion(self, ctrl, x, y, i):
        px, py = self._pt(i, x, y)
        t, e = self.tool, self.edit
        if self.editable and t in ("text", "select") and not self._drag:
            cursor, over = None, None
            if e and e["i"] == i:
                zone = self._edit_zone(self._geom_e(), px, py)
                cursor = {"grip": "grab", "text": "text"}.get(zone)
                over = zone
                self._set_fhover(None)
            else:
                f = self.fields_at(i, px, py)
                over = f[0] if f else None
                self._set_fhover((i, f[0][1]) if f else None)
                cursor = "grab" if f else None
            if t == "text":
                old = self.ghost
                self.ghost = None if over else (i, px, py)
                for g in (old, self.ghost):
                    if g:
                        self.areas[g[0]].queue_draw()
            else:
                link = None if over else self._link_at(i, px, py)
                cursor = cursor or ("pointer" if link else "text")
            self.areas[i].set_cursor_from_name(cursor or "text")
        elif t == "text":
            old = self.ghost
            self.ghost = (i, px, py)
            for g in (old, self.ghost):
                if g:
                    self.areas[g[0]].queue_draw()
        elif t == "erase":
            hits = self.annots_at(i, px, py)
            box = None
            if hits:
                a, box = hits[0]
                if a.get_annot_type() == _poppler().AnnotType.FREE_TEXT:
                    box = self._field_box(i, self.field_members(i, a))
            self._set_hover((i, box) if box else None)
        elif t == "select" and not self._drag:
            link = self._link_at(i, px, py)
            self.areas[i].set_cursor_from_name("pointer" if link else "text")

    def _set_fhover(self, hv):
        if hv != self.fhover:
            old = self.fhover
            self.fhover = hv
            for x in (old, hv):
                if x and x[0] < len(self.areas):
                    self.areas[x[0]].queue_draw()

    def _set_hover(self, hv):
        if hv != self.hover:
            old = self.hover
            self.hover = hv
            for x in (old, hv):
                if x:
                    self.areas[x[0]].queue_draw()

    # ---------- Text direkt auf der Seite tippen ----------
    # Ein Feld (self.edit) = {"i", "x", "y" (linke obere Ecke), "text", "fmt" (Formatierung je Zeichen), "cur",
    # "sel" (Anker der Auswahl oder None), "olds" (Anmerkungen, aus denen es entstand), "orig", "pickup"}.
    # Formatierung = (Schrift, Größe, Farbe, fett, kursiv).
    def _cur_style(self):
        return (self.font, self.font_size, self.colors["text"], self.bold, self.italic)

    def _mc(self):
        if getattr(self, "_mctx", None) is None:
            import cairo
            self._mctx = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))
        return self._mctx

    _FOPTS = None

    def _lay(self, cr, text, st, size=None):
        """Pango-Layout für einen Lauf. Ohne gerundete Schriftmetrik, damit Breiten mit dem Zoom linear wachsen."""
        lay = PangoCairo.create_layout(cr)
        try:
            if PdfView._FOPTS is None:
                import cairo
                o = cairo.FontOptions()
                o.set_hint_metrics(cairo.HINT_METRICS_OFF)
                o.set_hint_style(cairo.HINT_STYLE_NONE)
                PdfView._FOPTS = o
            PangoCairo.context_set_font_options(lay.get_context(), PdfView._FOPTS)
            try:                                   # ohne Runden der Zeichenbreiten: bei langem Text driftet sonst der Cursor
                lay.get_context().set_round_glyph_positions(False)
            except AttributeError:
                pass
            lay.context_changed()
        except Exception:
            pass
        fd = Pango.FontDescription.from_string(st[0])
        fd.set_absolute_size(max(1.0, st[1] if size is None else size) * Pango.SCALE)
        if st[3]:
            fd.set_weight(Pango.Weight.BOLD)
        if st[4]:
            fd.set_style(Pango.Style.ITALIC)
        lay.set_font_description(fd)
        lay.set_text(text, -1)
        return lay

    def _layout(self, cr, text, size_px):
        return self._lay(cr, text, self._cur_style(), size_px)

    def _geom_e(self):
        e = self.edit
        return self._geom(e["text"], e["fmt"], e["x"], e["y"])

    def _geom(self, text, fmt, x, y, cr=None):
        """Aufbau eines Feldes in Seitenkoordinaten – genau so, wie Poppler den Text später setzt (einfarbiges Feld: eine
        Anmerkung, Zeilenvorschub aus text_metrics; gemischtes Feld: Läufe auf gemeinsamer Grundlinie).
        -> {"lines": [{k, start, text, top, h, x, x2, st, runs: [{s, e, st, text, x, y, w, h}]}], "bounds", "ref"}"""
        cr = cr or self._mc()
        S = Pango.SCALE
        dflt = fmt[0] if fmt else self._cur_style()
        info, pos = [], 0
        for line in text.split("\n"):
            runs, s = [], pos
            for j in range(pos, pos + len(line)):
                if fmt[j] != fmt[s]:
                    runs.append((s, j))
                    s = j
            if line:
                runs.append((s, pos + len(line)))
            nl = fmt[pos] if pos < len(fmt) else (fmt[-1] if fmt else dflt)
            info.append((pos, line, runs, nl))
            pos += len(line) + 1
        first = [fmt[s] for s, _e in info[0][2]] or [info[0][3]]
        ref = max(first, key=lambda s: s[1])
        dx0, dy0, _a = text_metrics(ref[0], ref[3], ref[4])
        ox, top = x + dx0 * ref[1], y + dy0 * ref[1]
        lines = []
        for k, (start, line, runs, nl) in enumerate(info):
            parts = []
            if runs:
                for s, e in runs:
                    st = fmt[s]
                    lay = self._lay(cr, text[s:e], st)
                    parts.append((s, e, st, lay.get_baseline() / S, lay.get_size()[0] / S, lay.get_size()[1] / S))
            else:
                lay = self._lay(cr, " ", nl)
                parts.append((start, start, nl, lay.get_baseline() / S, 0.0, lay.get_size()[1] / S))
            maxb = max(p[3] for p in parts)
            adv = max(text_metrics(p[2][0], p[2][3], p[2][4])[2] * p[2][1] for p in parts)
            cx, rs = ox, []
            for s, e, st, b, w, h in parts:
                rs.append({"s": s, "e": e, "st": st, "text": text[s:e], "x": cx, "y": top + (maxb - b), "w": w, "h": h})
                cx += w
            lines.append({"k": k, "start": start, "text": line, "top": top, "x": ox, "x2": cx, "st": nl,
                          "h": max(r["y"] - top + r["h"] for r in rs), "runs": rs if runs else []})
            top += adv
        x2 = max(max(L["x2"] for L in lines), ox + ref[1] * 0.5)
        return {"lines": lines, "bounds": (ox, lines[0]["top"], x2, lines[-1]["top"] + lines[-1]["h"]), "ref": ref}

    def _caret_pos(self, g, text, c):
        """(x, y, Höhe, Format) des Cursors vor Zeichen c."""
        L = g["lines"][text.count("\n", 0, c)]
        if not L["runs"]:
            return L["x"], L["top"], L["h"], L["st"]
        r = next((r for r in L["runs"] if r["s"] <= c <= r["e"]), L["runs"][-1])
        lay = self._lay(self._mc(), r["text"], r["st"])
        pos, _w = lay.get_cursor_pos(len(r["text"][:max(0, c - r["s"])].encode()))
        S = Pango.SCALE        # Cursor-Rechteck von Pango (Schrifthöhe), nicht die ganze Zeilenbox → sitzt auf den Buchstaben
        return r["x"] + pos.x / S, r["y"] + pos.y / S, (pos.height / S) or r["h"], r["st"]

    def _index_at(self, g, text, x, y):
        k = 0
        for n, L in enumerate(g["lines"]):
            if y >= L["top"]:
                k = n
        L = g["lines"][k]
        if not L["runs"] or x <= L["x"]:
            return L["start"]
        for r in L["runs"]:
            if x < r["x"] + r["w"] or r is L["runs"][-1]:
                lay = self._lay(self._mc(), r["text"], r["st"])
                _in, idx, trailing = lay.xy_to_index(int((x - r["x"]) * Pango.SCALE), 0)
                col = len(r["text"].encode()[:idx].decode(errors="ignore")) + trailing
                return r["s"] + min(col, len(r["text"]))
        return L["start"] + len(L["text"])

    def _sel_rects(self, g, text, lo, hi):
        """Rechtecke der Auswahl lo..hi (Seitenkoordinaten, oben-links)."""
        out = []
        for L in g["lines"]:
            end = L["start"] + len(L["text"])
            size = L["st"][1]
            if not L["runs"]:
                if lo <= L["start"] < hi:
                    out.append((L["x"], L["top"], L["x"] + size * 0.3, L["top"] + L["h"]))
                continue
            for r in L["runs"]:
                a, b = max(lo, r["s"]), min(hi, r["e"])
                if a < b:
                    lay = self._lay(self._mc(), r["text"], r["st"])
                    xa = r["x"] + lay.get_cursor_pos(len(r["text"][:a - r["s"]].encode()))[0].x / Pango.SCALE
                    xb = r["x"] + lay.get_cursor_pos(len(r["text"][:b - r["s"]].encode()))[0].x / Pango.SCALE
                    out.append((xa, L["top"], xb, L["top"] + L["h"]))
            if lo <= end < hi:                                  # Zeilenumbruch mit ausgewählt
                out.append((L["x2"], L["top"], L["x2"] + size * 0.3, L["top"] + L["h"]))
        return out

    def _selrange(self):
        e = self.edit
        a = e.get("sel") if e else None
        if a is None or a == e["cur"]:
            return None
        return min(a, e["cur"]), max(a, e["cur"])

    @staticmethod
    def _kind(ch):
        return 0 if ch.isspace() else (1 if (ch.isalnum() or ch == "_") else 2)

    def _word_bounds(self, t, c):
        if not t:
            return 0, 0
        j = min(c, len(t) - 1)
        if c > 0 and self._kind(t[j]) == 0 and self._kind(t[c - 1]) != 0:
            j = c - 1
        kd = self._kind(t[j])
        lo, hi = j, j + 1
        while lo > 0 and self._kind(t[lo - 1]) == kd and t[lo - 1] != "\n":
            lo -= 1
        while hi < len(t) and self._kind(t[hi]) == kd and t[hi] != "\n":
            hi += 1
        return lo, hi

    @staticmethod
    def _word_left(t, c):
        j = c
        while j > 0 and t[j - 1].isspace():
            j -= 1
        while j > 0 and not t[j - 1].isspace():
            j -= 1
        return j

    @staticmethod
    def _word_right(t, c):
        j = c
        while j < len(t) and not t[j].isspace():
            j += 1
        while j < len(t) and t[j].isspace():
            j += 1
        return j

    def start_edit(self, i, x, y, old=None, typing=True):
        """Eingabe an (x, y) = linke obere Ecke beginnen. old = vorhandenes Textfeld (eine seiner Anmerkungen), das
        bearbeitet wird: es verschwindet solange aus der Seite und wird live neu gezeichnet."""
        text, fmt, olds = "", [], []
        if old is not None:
            olds = self.field_members(i, old)
            fb = self._cur_style()
            if len(olds) == 1:
                a = olds[0]
                st = annot_style(a, fb)
                text = a.get_contents() or ""
                fmt = [st] * len(text)
                r = a.get_rectangle()
                x, y = r.x1, self.sizes[i][1] - r.y2
            else:
                rows = {}
                for a in olds:
                    lab = parse_label(a)
                    rows.setdefault(lab["line"], []).append((lab["idx"], a.get_contents() or "", annot_style(a, fb)))
                prev = annot_style(olds[0], fb)
                for k in range(max(rows) + 1):
                    if k:
                        text += "\n"
                        fmt.append(prev)
                    for _n, t, st in sorted(rows.get(k, []), key=lambda r: r[0]):
                        text += t
                        fmt += [st] * len(t)
                        prev = st
                lab = parse_label(olds[0])
                x, y = lab["x"], lab["y"]
            if fmt:
                self.font, self.font_size, self.colors["text"], self.bold, self.italic = fmt[0]
            for a in olds:
                self.page(i).remove_annot(a)
            self._rerender(i)
        else:
            y -= self.font_size * 0.5          # Klickpunkt = Mitte der ersten Zeile
        y = max(0, y)
        self.edit = {"i": i, "x": x, "y": y, "text": text, "fmt": fmt, "cur": len(text), "sel": None, "olds": olds,
                     "orig": (text, tuple(fmt), round(x, 2), round(y, 2)), "pickup": not typing}
        self.ghost = None
        if typing:
            self.keys.set_im_context(self.im)
            self.im.set_client_widget(self.areas[i])
            self.im.focus_in()
            self.grab_focus()
            self._caret_on = True
            if not self._blink_id:
                self._blink_id = GLib.timeout_add(530, self._blink)
        self._sync_tools()
        self.areas[i].queue_draw()

    def _stop_edit(self):
        if self.edit:
            i = self.edit["i"]
            self.edit = None
            self.im.focus_out()
            self.im.set_client_widget(None)
            self.keys.set_im_context(None)
            if i < len(self.areas):
                self.areas[i].queue_draw()
        if self._blink_id:
            GLib.source_remove(self._blink_id)
            self._blink_id = None

    def _restore(self, i, a):
        """Entfernte Anmerkung als Kopie zurücklegen und Rückgängig-Verweise umbiegen."""
        n = clone_annot(self.doc, self.page(i), a)
        if n is None:
            self.page(i).add_annot(a)
            return a
        ref = self._refs.pop(annot_key(a), None)
        if ref is not None:
            ref.a = n
            self._refs[annot_key(n)] = ref
        return n

    def commit_edit(self):
        e = self.edit
        if not e:
            return
        self._stop_edit()
        i, olds = e["i"], e["olds"]
        n = len(e["text"].rstrip())
        text, fmt = e["text"][:n], e["fmt"][:n]
        unchanged = (e["text"], tuple(e["fmt"]), round(e["x"], 2), round(e["y"], 2)) == e["orig"]
        if olds and unchanged:
            for a in olds:
                self._restore(i, a)
        else:
            ops = [("remove", i, a) for a in olds]
            if text.strip():
                ops += [("add", i, a) for a in self._make_field(i, e["x"], e["y"], text, fmt)]
            if ops:
                self._push(ops)
        self._rerender(i)
        self._sync_tools()

    def cancel_edit(self):
        e = self.edit
        if not e:
            return
        self._stop_edit()
        if e["olds"]:
            if e["orig"][1]:
                self.font, self.font_size, self.colors["text"], self.bold, self.italic = e["orig"][1][0]
            for a in e["olds"]:
                self._restore(e["i"], a)
            self._rerender(e["i"])
        self._sync_tools()

    def _edit_changed(self):
        """Nach jeder Änderung an Cursor/Auswahl/Text: neu zeichnen, Werkzeugleiste auf die Auswahl abstimmen."""
        e = self.edit
        if not e:
            return
        rng = self._selrange()
        if e["fmt"]:
            st = e["fmt"][rng[0] if rng else 0]
            cur = self._cur_style()
            if st != cur:
                self.font, self.font_size, self.colors["text"], self.bold, self.italic = st
                self._sync_style_widgets()
        self.areas[e["i"]].queue_draw()

    def _fmt_apply(self, idx, val):
        """Eine Eigenschaft (0 Schrift, 1 Größe, 2 Farbe, 3 fett, 4 kursiv) auf die Auswahl setzen – ohne Auswahl aufs ganze Feld."""
        e = self.edit
        if not e or not e["fmt"]:
            return
        lo, hi = self._selrange() or (0, len(e["fmt"]))
        for j in range(lo, hi):
            s = list(e["fmt"][j])
            s[idx] = val
            e["fmt"][j] = tuple(s)
        self.areas[e["i"]].queue_draw()

    def _toggle_fmt(self, which):
        val = not getattr(self, which)
        setattr(self, which, val)
        self._fmt_apply(3 if which == "bold" else 4, val)
        self._sync_style_widgets()

    def _on_fmt(self, which, active):
        if self._syncing or getattr(self, which) == active:
            return
        setattr(self, which, active)
        if self.tool == "text":
            self._fmt_apply(3 if which == "bold" else 4, active)

    def _edit_replace(self, lo, hi, text):
        e = self.edit
        if e["fmt"]:
            st = e["fmt"][lo] if (hi > lo or lo == 0) else e["fmt"][lo - 1]     # Auswahl ersetzen: Stil ihres Anfangs
        else:
            st = self._cur_style()
        e["text"] = e["text"][:lo] + text + e["text"][hi:]
        e["fmt"][lo:hi] = [st] * len(text)
        e["cur"], e["sel"] = lo + len(text), None

    def _edit_insert(self, text):
        e = self.edit
        if not e:
            return
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        lo, hi = self._selrange() or (e["cur"], e["cur"])
        self._edit_replace(lo, hi, text)
        self._caret_on = True
        self._edit_changed()

    def _edit_delete_sel(self):
        rng = self._selrange()
        if not rng:
            return False
        self._edit_replace(rng[0], rng[1], "")
        return True

    def _edit_copy(self, cut=False):
        e = self.edit
        rng = self._selrange()
        if not rng:
            return
        self.get_clipboard().set_content(Gdk.ContentProvider.new_for_bytes(
            "text/plain;charset=utf-8", GLib.Bytes.new(e["text"][rng[0]:rng[1]].encode())))
        if cut:
            self._edit_delete_sel()

    def _edit_paste(self):
        def got(cb, res):
            try:
                text = cb.read_text_finish(res)
            except GLib.Error:
                return
            if text and self.edit:
                self._edit_insert(text)
        self.get_clipboard().read_text_async(None, got)

    def _edit_key(self, k, keyval, ctrl, shift):
        e = self.edit
        t, cur = e["text"], e["cur"]
        rng = self._selrange()
        lk = k.lower()
        if k in ("Return", "KP_Enter"):
            if ctrl:
                self.commit_edit()
            else:
                self._edit_insert("\n")
            return True
        if k == "Escape":
            self.commit_edit()
            return True
        new = None
        if ctrl and lk == "a":
            e["sel"], e["cur"] = 0, len(t)
        elif ctrl and lk == "c":
            self._edit_copy()
            return True
        elif ctrl and lk == "x":
            self._edit_copy(cut=True)
        elif ctrl and lk == "v":
            self._edit_paste()
            return True
        elif ctrl and lk in ("b", "i"):
            self._toggle_fmt("bold" if lk == "b" else "italic")
            return True
        elif k == "BackSpace":
            if not self._edit_delete_sel() and cur > 0:
                n = cur - self._word_left(t, cur) if ctrl else 1
                self._edit_replace(cur - n, cur, "")
        elif k == "Delete":
            if not self._edit_delete_sel() and cur < len(t):
                n = self._word_right(t, cur) - cur if ctrl else 1
                self._edit_replace(cur, cur + n, "")
        elif k == "Left":
            new = rng[0] if (rng and not shift and not ctrl) else (self._word_left(t, cur) if ctrl else max(0, cur - 1))
        elif k == "Right":
            new = rng[1] if (rng and not shift and not ctrl) else (self._word_right(t, cur) if ctrl else min(len(t), cur + 1))
        elif k == "Home":
            new = 0 if ctrl else t.rfind("\n", 0, cur) + 1
        elif k == "End":
            j = t.find("\n", cur)
            new = len(t) if (ctrl or j < 0) else j
        elif k in ("Up", "Down"):
            lines = t.split("\n")
            ln = t.count("\n", 0, cur)
            col = cur - (t.rfind("\n", 0, cur) + 1)
            if k == "Up" and ln == 0:
                new = 0
            elif k == "Down" and ln == len(lines) - 1:
                new = len(t)
            else:
                ln2 = ln + (-1 if k == "Up" else 1)
                new = sum(len(l) + 1 for l in lines[:ln2]) + min(col, len(lines[ln2]))
        elif ctrl and lk in ("s", "z", "y"):
            return False                              # Speichern/Rückgängig wie gewohnt
        elif ctrl or k in ("Tab", "ISO_Left_Tab", "Page_Up", "Page_Down", "Prior", "Next"):
            return False
        else:
            return True                               # sonstige Tasten nicht als Werkzeug-Kürzel deuten
        if new is not None:
            if shift:
                if e["sel"] is None:
                    e["sel"] = cur
            else:
                e["sel"] = None
            e["cur"] = new
        self._caret_on = True
        self._edit_changed()
        return True

    def _grip_box(self, g):
        z = self.zoom
        s, pad = 13 / z, 3
        x1, y1 = g["bounds"][0], g["bounds"][1]
        if y1 - pad - s >= 0:
            return (x1 - pad - s * 0.5, y1 - pad - s, x1 - pad + s * 0.5, y1 - pad)
        return (x1 - pad - s, y1 - pad, x1 - pad, y1 - pad + s)

    def _edit_zone(self, g, x, y):
        """Wo liegt der Punkt am Feld? "text" (Eingabe/Auswahl), "grip" (Griff und Rand: verschieben) oder None."""
        z = self.zoom
        gb = self._grip_box(g)
        if gb[0] <= x <= gb[2] and gb[1] <= y <= gb[3]:
            return "grip"
        x1, y1, x2, y2 = g["bounds"]
        if x1 - 1 <= x <= x2 + 1 and y1 - 1 <= y <= y2 + 1:
            return "text"
        m = 3 + 6 / z
        if x1 - m <= x <= x2 + m and y1 - m <= y <= y2 + m:
            return "grip"
        return None

    def ist_caret(self):
        """Für die animierte Cursor-Ebene (glass.py): Cursor des Direkt-Textes auf der Seite."""
        return self._caret_at if self.edit else None

    def _blink(self):
        if not self.edit:
            self._blink_id = None
            return False
        self._caret_on = not self._caret_on
        self.areas[self.edit["i"]].queue_draw()
        return True

    def _clear_ghost(self):
        if self.ghost:
            i = self.ghost[0]
            self.ghost = None
            self.areas[i].queue_draw()

    def _draw_grip(self, cr, g, z, edit):
        x1, y1, x2, y2 = self._grip_box(g)
        cr.set_source_rgba(0.24, 0.52, 1.0, 0.9 if edit else 0.6)
        rad = 3
        X, Y, W, H = x1 * z, y1 * z, (x2 - x1) * z, (y2 - y1) * z
        cr.new_sub_path()
        cr.arc(X + W - rad, Y + rad, rad, -1.5708, 0)
        cr.arc(X + W - rad, Y + H - rad, rad, 0, 1.5708)
        cr.arc(X + rad, Y + H - rad, rad, 1.5708, 3.1416)
        cr.arc(X + rad, Y + rad, rad, 3.1416, 4.7124)
        cr.close_path()
        cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.95)
        for cx in (X + W * 0.35, X + W * 0.65):
            for cy in (Y + H * 0.25, Y + H * 0.5, Y + H * 0.75):
                cr.arc(cx, cy, 1.0, 0, 6.2832)
                cr.fill()

    def _draw_frame(self, cr, g, z, alpha=0.55):
        x1, y1, x2, y2 = g["bounds"]
        pad = 3
        cr.set_source_rgba(0.24, 0.52, 1.0, alpha)
        cr.set_line_width(1)
        cr.set_dash([3, 3])
        cr.rectangle((x1 - pad) * z + 0.5, (y1 - pad) * z + 0.5, (x2 - x1 + 2 * pad) * z - 1, (y2 - y1 + 2 * pad) * z - 1)
        cr.stroke()
        cr.set_dash([])

    def _draw_edit(self, cr, z):
        e = self.edit
        g = self._geom_e()
        rng = self._selrange()
        if rng:
            cr.set_source_rgba(0.24, 0.52, 1.0, 0.35)
            for a, b, c, d in self._sel_rects(g, e["text"], *rng):
                cr.rectangle(a * z, b * z, (c - a) * z, (d - b) * z)
            cr.fill()
        self._draw_frame(cr, g, z)
        for L in g["lines"]:
            for r in L["runs"]:
                st = r["st"]
                cr.set_source_rgb(*_rgb(st[2]))
                cr.move_to(r["x"] * z, r["y"] * z)
                PangoCairo.show_layout(cr, self._lay(cr, r["text"], st, st[1] * z))
        cx, cy, ch, cst = self._caret_pos(g, e["text"], e["cur"])
        cx, cy, ch = cx * z, cy * z, ch * z
        r, gg, b = _rgb(cst[2])
        old = self._caret_at
        self._caret_at = (self.areas[e["i"]], cx, cy, ch, (r, gg, b)) if not e["pickup"] else None
        if self._caret_at and (not old or old[1:4] != self._caret_at[1:4]) and glass and hasattr(glass, "caret_kick"):
            glass.caret_kick(self)             # Cursor-Ebene sofort nachziehen statt im 40-ms-Takt
        if self._caret_at and self._caret_on and not self._selrange() and not (glass and glass.caret_active(self)):
            cr.set_source_rgb(r, gg, b)       # sonst zeichnet die Cursor-Ebene (glass.py)
            cr.rectangle(round(cx), cy, max(1.0, z * 0.9), ch)
            cr.fill()
        if not e["text"]:
            cr.set_source_rgba(r, gg, b, 0.35)
            hint = self._layout(cr, "  tippen …", cst[1] * z * 0.8)
            cr.move_to(g["lines"][0]["x"] * z + 2, g["lines"][0]["top"] * z + cst[1] * z * 0.12)
            PangoCairo.show_layout(cr, hint)
        self._draw_grip(cr, g, z, True)

    def _draw_ghost(self, cr, z):
        """Unter der Maus: so hoch wird der Text (Cursor in Schriftgröße + kleines „Aa“)."""
        _i, x, y = self.ghost
        sz = self.font_size
        r, g, b = _rgb(self.colors["text"])
        lay = self._layout(cr, "Aa", sz * z)
        _ink, log = lay.get_pixel_extents()
        top = (y - sz * 0.5) * z + text_metrics(self.font, self.bold, self.italic)[1] * sz * z
        cr.set_source_rgba(r, g, b, 0.85)
        cr.rectangle(round(x * z), top, max(1.0, z * 0.9), log.height)
        cr.fill()
        cr.set_source_rgba(r, g, b, 0.28)
        cr.move_to(x * z + 4, top)
        PangoCairo.show_layout(cr, lay)

    # ---------- Links ----------
    def _link_at(self, i, x, y):
        h = self.sizes[i][1]
        for m in self.page(i).get_link_mapping() or []:
            r = m.area
            if r.x1 <= x <= r.x2 and h - r.y2 <= y <= h - r.y1:
                return m.action
        return None

    def _follow_link(self, i, x, y):
        act = self._link_at(i, x, y)
        if act is None:
            return False
        P = _poppler()
        t = act.type
        if t == P.ActionType.URI:
            uri = act.uri.uri
            if uri:
                Gtk.UriLauncher.new(uri).launch(self.get_root(), None, None)
                self.notify(f"Öffne {uri[:80]}")
            return True
        if t == P.ActionType.GOTO_DEST:
            dest = act.goto_dest.dest
            if dest and dest.type == P.DestType.NAMED:
                dest = self.doc.find_dest(dest.named_dest)
            if dest and dest.page_num > 0:
                n = dest.page_num - 1
                top = (self.sizes[n][1] - dest.top) if dest.change_top else 0
                self.goto(n, y_in_page=max(0, top - 20))
            return True
        return False

    # ---------- Zeichnen über der Seite ----------
    def _overlay_active(self, i):
        """Hat Seite i gerade etwas über der Textur zu zeichnen? (Auswahl, Live-Strich, Text, Hover)"""
        rng = self.sel_range()
        return bool((rng and rng[0] == i) or (self.live and self.live[0] == i) or (self.edit and self.edit["i"] == i)
                    or (self.ghost and self.ghost[0] == i and self.tool == "text")
                    or (self.hover and self.hover[0] == i) or (self.fhover and self.fhover[0] == i) or self.extra_active(i))

    def _draw_overlay(self, area, cr, w, h, i):
        z = self.zoom
        pr, pg, pb = 0.24, 0.52, 1.0
        rng = self.sel_range()
        if rng and rng[0] == i:
            if self.tool in MARKUP:
                pr, pg, pb = _rgb(self.colors[self.tool])
            cr.set_source_rgba(pr, pg, pb, 0.32)
            for x1, y1, x2, y2 in self.sel_lines(i, rng[1], rng[2]):
                cr.rectangle(x1 * z, y1 * z, (x2 - x1) * z, (y2 - y1) * z)
            cr.fill()
        if self.live and self.live[0] == i and self.live[1] != "redact":
            kind, data = self.live[1], self.live[2]
            r, g, b = _rgb(self.colors[kind])
            cr.set_source_rgb(r, g, b)
            cr.set_line_width(max(1, self.width * z))
            cr.set_line_cap(1)  # rund
            cr.set_line_join(1)
            if kind == "ink" and data:
                cr.move_to(data[0][0] * z, data[0][1] * z)
                for x, y in data[1:]:
                    cr.line_to(x * z, y * z)
                cr.stroke()
            elif kind == "rect":
                x1, y1, x2, y2 = data
                cr.rectangle(min(x1, x2) * z, min(y1, y2) * z, abs(x2 - x1) * z, abs(y2 - y1) * z)
                cr.stroke()
        if self.edit and self.edit["i"] == i:
            self._draw_edit(cr, z)
        elif self.ghost and self.ghost[0] == i and self.tool == "text":
            self._draw_ghost(cr, z)
        if self.fhover and self.fhover[0] == i and not self.edit:
            x1, y1, x2, y2 = self.fhover[1]
            g = {"bounds": (x1, y1, x2, y2)}
            self._draw_frame(cr, g, z, 0.5)
            self._draw_grip(cr, g, z, False)
        self.extra_draw(cr, z, i)
        if self.hover and self.hover[0] == i:
            x1, y1, x2, y2 = self.hover[1]
            cr.set_source_rgba(0.9, 0.2, 0.2, 0.9)
            cr.set_line_width(1.5)
            cr.set_dash([4, 3])
            cr.rectangle(x1 * z - 2, y1 * z - 2, (x2 - x1) * z + 4, (y2 - y1) * z + 4)
            cr.stroke()

    # ---------- Tasten & Strg+Mausrad ----------
    def on_key(self, ctrl, keyval, code, mods):
        k = Gdk.keyval_name(keyval) or ""
        c = bool(mods & Gdk.ModifierType.CONTROL_MASK)
        s = bool(mods & Gdk.ModifierType.SHIFT_MASK)
        if self.edit and self._edit_key(k, keyval, c, s):
            return True
        focus = self.get_root().get_focus() if self.get_root() else None
        typing = isinstance(focus, Gtk.Text) or isinstance(focus, Gtk.TextView)
        if not typing and not c and k in ("Left", "Right") and not (mods & Gdk.ModifierType.ALT_MASK) \
                and self._nav_ok(focus):
            self.skip_page(1 if k == "Right" else -1)
        elif not typing and not c and k in ("Up", "Down") and self._nav_ok(focus):
            self.smooth_by((400 if s else 80) * (1 if k == "Down" else -1))
        elif k in ("Page_Down", "Next"):
            self.skip_page(1)
        elif k in ("Page_Up", "Prior"):
            self.skip_page(-1)
        elif c and k in ("plus", "equal", "KP_Add"):
            self.set_zoom(self.zoom * 1.25)
        elif c and k in ("minus", "KP_Subtract"):
            self.set_zoom(self.zoom / 1.25)
        elif c and k == "0":
            self.fit_width()
        elif k == "F9":
            self.toggle_side()
        elif c and k == "f":
            self.search.grab_focus()
        elif c and k == "g":
            self.ask_page()
        elif c and k == "c" and (self.sel or getattr(self, "_last_text", "")) and not typing:
            self.copy_selection()
        elif self.editable and c and k.lower() == "z" and not typing:
            (self.redo if s else self.undo)()
        elif self.editable and c and k == "y" and not typing:
            self.redo()
        elif self.editable and c and k.lower() == "s":
            self.save_as() if (s or not self.path) else self.save()
        elif k == "Escape" and not typing and (self.sel or self.tool != "select"):
            self.sel = None
            self._redraw_all()
            if self.editable:
                self.set_tool("select")
        elif self.editable and not c and not typing and len(k) == 1 and not (mods & Gdk.ModifierType.ALT_MASK):
            tool = next((key for key, _i, _n, kk in TOOLS if kk == k.lower()), None)
            if not tool:
                return False
            self.set_tool(tool)
        else:
            return False
        return True

    def _pinch_begin(self, *_):
        self._pinch0 = self.zoom
        self._pinch_t = float("inf")

    def _on_pinch(self, g, scale):
        target = max(0.2, min(self._pinch0 * scale, 6))
        self._pinch_to = target
        if self._pinch_id is None:                      # höchstens ~20 Neuberechnungen pro Sekunde
            self._pinch_id = GLib.timeout_add(50, self._pinch_apply)

    def _pinch_apply(self):
        self._pinch_id = None
        if abs(self._pinch_to - self.zoom) > 0.004:
            self.fit = False
            self._apply_zoom(self._pinch_to)
        return False

    def on_scroll(self, ctrl, dx, dy):
        # Während (und kurz nach) dem Pinch rutschen Restbewegungen als Scrollen durch → verschlucken
        if GLib.get_monotonic_time() - self._pinch_t < 300000:
            return True
        if ctrl.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
            self.set_zoom(self.zoom * (0.9 if dy > 0 else 1.1))
            return True
        return False


def _gpoint(x, y):
    gi.require_version("Graphene", "1.0")
    from gi.repository import Graphene
    p = Graphene.Point()
    p.x, p.y = x, y
    return p


def diff_view(segments):
    """Textansicht für Änderungen: [(art, text)] mit art in 'same', 'del', 'add', 'head'."""
    tv = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR, monospace=True)
    tv.add_css_class("doc-text")
    fill_diff(tv, segments)
    return tv


def fill_diff(tv, segments):
    buf = tv.get_buffer()
    buf.set_text("")
    tags = buf.get_tag_table()
    if not tags.lookup("del"):
        buf.create_tag("del", foreground="#ff8f8f", background="#5a1e1e", strikethrough=True)
        buf.create_tag("add", foreground="#9ff0b4", background="#1d4a2a")
        buf.create_tag("same", foreground="#9a9a9a")
        buf.create_tag("head", weight=800, foreground="#ffd27a", pixels_above_lines=10, pixels_below_lines=4)
    for kind, text in segments:
        buf.insert_with_tags_by_name(buf.get_end_iter(), text, kind)
