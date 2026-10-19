# PDF-Viewer und -Editor für Scourge: alle Seiten untereinander, Zoom, Seitensprung, Volltextsuche,
# Text auswählen/kopieren, Links folgen und Anmerkungen (Textmarker, Unterstreichen, Durchstreichen, Text,
# Stift, Rahmen, Notiz, Radierer) mit Rückgängig und Speichern.
# Anmerkungen sind echte PDF-Anmerkungen (Poppler) – andere PDF-Programme zeigen und bearbeiten sie weiter.
# Seiten werden erst gerendert, wenn sie ins Bild scrollen (auch dicke PDFs bleiben flüssig).
import ctypes
import os

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango, PangoCairo

VIEWER_CSS = """
.pdf-bg { background-color: alpha(black, 0.18); border-radius: 10px; }
.pdf-page { background-color: white; box-shadow: 0 2px 10px alpha(black, 0.45); }
.pdf-bar { margin-bottom: 6px; }
.pdf-bar entry.pb { min-height: 24px; }
.pdf-tools { margin-bottom: 6px; }
.pdf-tools > flowboxchild { padding: 0; background: none; }
.pdf-tools button.pb.ptool { padding: 1px 7px; font-size: 14px; }
.pdf-tools .tool-name { font-size: 12px; color: @secondary; }
.pdf-tools button.pcolor { padding: 2px; min-width: 0; min-height: 0; border-radius: 4px; background: none;
    border: 1px solid transparent; box-shadow: none; }
.pdf-tools button.pcolor:hover { border-color: alpha(@primary, 0.55); }
.pdf-tools button.pcolor.active { border-color: @primary; }
.pdf-tools .dirty { color: @tertiary; font-size: 12px; }
popover.pdf-pop > contents { padding: 8px; }
popover.pdf-pop textview, popover.pdf-pop textview text { background-color: alpha(@background, 0.45); color: @foreground; }
popover.pdf-pop textview { border: 1px solid alpha(@primary, 0.45); border-radius: 4px; padding: 4px 6px; }
.hits { font-size: 11px; color: alpha(@foreground, 0.6); }
.diff-del { color: #ff8f8f; }
"""
GAP = 14

# (Schlüssel, Symbol, Name, Taste)
TOOLS = (("select", "󰗧", "auswählen", "v"), ("highlight", "󰸱", "markieren", "h"),
         ("underline", "󰊇", "unterstreichen", "u"), ("strike", "󰊀", "durchstreichen", "d"),
         ("text", "󰊄", "text", "t"), ("ink", "󰏪", "stift", "p"), ("rect", "󰹟", "rahmen", "r"),
         ("note", "󰆉", "notiz", "n"), ("erase", "󰇾", "radierer", "e"))
TOOL_TIPS = {"select": "Text auswählen, kopieren, Links folgen", "highlight": "Text mit dem Textmarker markieren",
             "underline": "Text unterstreichen", "strike": "Text durchstreichen",
             "text": "Text (Schrift) auf die Seite setzen – Klick auf vorhandenen Text bearbeitet ihn",
             "ink": "Freihand zeichnen", "rect": "Rahmen aufziehen", "note": "Notiz anheften",
             "erase": "Anmerkung anklicken, um sie zu entfernen"}
MARKUP = ("highlight", "underline", "strike")
COLORS = (("gelb", "#ffd400"), ("grün", "#62d26f"), ("blau", "#3d8bff"), ("rot", "#e5322d"),
          ("pink", "#ff5fc4"), ("schwarz", "#1b1b1b"))
FONTS = (("Sans", "Sans"), ("Serif", "Serif"), ("Mono", "Monospace"))
DEFAULT_COLOR = {"highlight": "#ffd400", "underline": "#e5322d", "strike": "#e5322d", "text": "#1b1b1b",
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
        if fd:
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


_TEXT_CALIB = {}


def text_metrics(font):
    """Wo Poppler eine Freitext-Anmerkung zeichnet – relativ zu einem Pango-Layout derselben Schrift, als
    Vielfache der Schriftgröße: (dx, dy, Zeilenvorschub). Einmal pro Schrift an einem unsichtbaren Mini-PDF
    gemessen, damit die Live-Vorschau beim Tippen genau dort steht, wo der Text danach im PDF landet."""
    if font in _TEXT_CALIB:
        return _TEXT_CALIB[font]
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
            fd = P.FontDescription.new(font)
            fd.size_pt = S
            a.set_font_desc(fd)
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
        lay.set_font_description(fd)
        lay.set_text("H", -1)
        ink, _log = lay.get_pixel_extents()
        if one and two:
            res = ((one[0] - x0 - ink.x) / S, (one[1] - y0 - ink.y) / S, (two[3] - one[3]) / S)
    except Exception:
        pass
    _TEXT_CALIB[font] = res
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


class PdfView(Gtk.Box):
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
        self.edit = None        # Text, der gerade direkt auf der Seite getippt wird (dict)
        self.ghost = None       # (Seite, x, y): Vorschau-Cursor des Text-Werkzeugs unter der Maus
        self._blink_id = None
        self._caret_on = True
        self.im = Gtk.IMMulticontext()      # Tottasten, Compose usw. beim Tippen auf der Seite
        self.im.connect("commit", lambda _im, text: self._edit_insert(text))

        bar = Gtk.Box(spacing=6)
        bar.add_css_class("pdf-bar")
        self.prev = self._btn("󰅁", "Vorherige Seite (Bild ↑)", lambda: self.goto(self.current_page() - 1))
        self.page_lbl = Gtk.Label(label="")
        self.page_lbl.add_css_class("key")
        self.next = self._btn("󰅂", "Nächste Seite (Bild ↓)", lambda: self.goto(self.current_page() + 1))
        for w in (self.prev, self.page_lbl, self.next):
            bar.append(w)
        bar.append(Gtk.Box(hexpand=True))
        bar.append(self._btn("󰍴", "Verkleinern (Strg+−)", lambda: self.set_zoom(self.zoom / 1.25)))
        self.zoom_lbl = self._btn("Breite", "Auf Fensterbreite einpassen (Strg+0)", self.fit_width)
        bar.append(self.zoom_lbl)
        bar.append(self._btn("󰐕", "Vergrößern (Strg++)", lambda: self.set_zoom(self.zoom * 1.25)))
        bar.append(Gtk.Box(hexpand=True))
        self.search = Gtk.SearchEntry(placeholder_text="Im PDF suchen", width_chars=12)
        self.search.add_css_class("pb")
        self.search.connect("activate", lambda *_: self.find(self.search.get_text()))
        self.search.connect("next-match", lambda *_: self.step_hit(1))
        self.search.connect("previous-match", lambda *_: self.step_hit(-1))
        self.search.connect("stop-search", lambda *_: self.find(""))
        bar.append(self.search)
        self.hit_lbl = Gtk.Label(label="")
        self.hit_lbl.add_css_class("hits")
        bar.append(self.hit_lbl)
        self.bar = bar
        self.append(self._hscroll(bar))
        if editable:
            self.append(self._build_tools())

        self.scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self.scroll.add_css_class("pdf-bg")
        self.pages = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=GAP, halign=Gtk.Align.CENTER,
                             margin_top=GAP, margin_bottom=GAP, margin_start=GAP, margin_end=GAP)
        self.scroll.set_child(self.pages)
        self.scroll.get_child().set_scroll_to_focus(False)   # Klick auf die Seite darf die Ansicht nicht verschieben
        self.set_focusable(True)
        self.append(self.scroll)
        va = self.scroll.get_vadjustment()
        va.connect("value-changed", lambda *_: self.update())
        self.scroll.get_hadjustment().connect("changed", lambda *_: self.fit and GLib.idle_add(self._refit))
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)
        self.keys = keys
        zoom = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        zoom.connect("scroll", self.on_scroll)
        self.scroll.add_controller(zoom)
        self._build_popovers()

    @staticmethod
    def _hscroll(child):
        # Leiste darf beim Verkleinern schrumpfen (wird dann seitlich verschiebbar statt das Fenster zu sperren)
        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.EXTERNAL, vscrollbar_policy=Gtk.PolicyType.NEVER,
                                propagate_natural_height=True)
        sc.set_child(child)
        return sc

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

    def _build_tools(self):
        # drei Gruppen, die bei schmalem Fenster untereinander rutschen statt aus dem Bild zu laufen
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=False, max_children_per_line=3,
                           column_spacing=10, row_spacing=4)
        flow.add_css_class("pdf-tools")
        row = Gtk.Box(spacing=4)
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
        row = Gtk.Box(spacing=4)
        self.color_box = Gtk.Box(spacing=2, valign=Gtk.Align.CENTER)
        self.color_btns = {}
        for name, hexcol in COLORS:
            b = Gtk.Button(child=_swatch(hexcol), tooltip_text=name, focus_on_click=False, valign=Gtk.Align.CENTER)
            b.add_css_class("pcolor")
            b.connect("clicked", lambda *_, c=hexcol: self.set_color(c))
            self.color_btns[hexcol] = b
            self.color_box.append(b)
        row.append(self.color_box)
        self.size_spin = Gtk.SpinButton.new_with_range(1, 72, 1)
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
        row.append(self.size_lbl)
        row.append(self.size_spin)
        flow.append(row)
        row = Gtk.Box(spacing=4, halign=Gtk.Align.END, hexpand=True)
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
        flow.append(row)
        for child in (flow.get_child_at_index(i) for i in range(3)):
            child.set_focusable(False)
        flow.get_child_at_index(2).set_hexpand(True)
        flow.get_child_at_index(2).set_halign(Gtk.Align.END)
        self.tool_btns["select"].set_active(True)
        self._sync_tools()
        return flow

    def set_tool(self, key):
        if key != self.tool:
            self.commit_edit()
            self.tool = key
            self.sel = self.live = self.hover = self.ghost = None
            if self.editable and not self.tool_btns[key].get_active():
                self.tool_btns[key].set_active(True)
            self._redraw_all()
        cursor = {"select": "text", "highlight": "text", "underline": "text", "strike": "text", "text": "text",
                  "ink": "crosshair", "rect": "crosshair", "note": "copy", "erase": "default"}[key]
        for a in self.areas:
            a.set_cursor_from_name(cursor)
        if self.editable:
            self._sync_tools()

    def set_color(self, hexcol):
        if self.tool in self.colors:
            self.colors[self.tool] = hexcol
        self._sync_tools()
        self._edit_style()

    def _on_size(self, spin):
        if self.tool == "text":
            self.font_size = spin.get_value()
            self._edit_style()
        elif self.tool in ("ink", "rect"):
            self.width = spin.get_value()

    def _on_font(self, dd, _pspec):
        self.font = FONTS[dd.get_selected()][1]
        self._edit_style()

    def _sync_tools(self):
        if not self.editable:
            return
        self.tool_name.set_label(next(n for k, _, n, _ in TOOLS if k == self.tool))
        has_color = self.tool in self.colors
        self.color_box.set_visible(has_color)
        cur = self.colors.get(self.tool)
        for hexcol, b in self.color_btns.items():
            (b.add_css_class if hexcol == cur else b.remove_css_class)("active")
        sized = self.tool in ("text", "ink", "rect")
        self.font_dd.set_visible(self.tool == "text")
        want = next((k for k, (_, f) in enumerate(FONTS) if f == self.font), 0)
        if self.font_dd.get_selected() != want:
            self.font_dd.set_selected(want)
        self.size_spin.set_visible(sized)
        self.size_lbl.set_visible(sized)
        if self.tool == "text":
            self.size_lbl.set_label("pt")
            self.size_spin.set_range(6, 72)
            self.size_spin.set_value(self.font_size)
        elif sized:
            self.size_lbl.set_label("stärke")
            self.size_spin.set_range(1, 12)
            self.size_spin.set_value(self.width)
        self.undo_btn.set_sensitive(bool(self.undo_stack))
        self.redo_btn.set_sensitive(bool(self.redo_stack))
        self.save_btn.set_sensitive(self.dirty and bool(self.path))
        self.save_as_btn.set_sensitive(self.doc is not None)
        self.dirty_lbl.set_label("● ungespeichert" if self.dirty else "")
        self.save_btn.set_label("󰆓 speichern" if self.dirty else "󰆓")

    # ---------- Popover: Auswahl-Aktionen, Text-/Notizeingabe ----------
    def _build_popovers(self):
        self.sel_pop = Gtk.Popover(has_arrow=True, autohide=True)
        self.sel_pop.add_css_class("pdf-pop")
        self.sel_pop.set_parent(self)
        box = Gtk.Box(spacing=4)
        box.append(self._btn("󰆏 kopieren", "Auswahl kopieren (Strg+C)", lambda: (self.copy_selection(), self.sel_pop.popdown())))
        if self.editable:
            for kind, icon, name, _k in TOOLS[1:4]:
                box.append(self._btn(icon, name, lambda kind=kind: (self.markup_selection(kind), self.sel_pop.popdown())))
        self.sel_pop.set_child(box)

        self.in_pop = Gtk.Popover(has_arrow=True, autohide=True)
        self.in_pop.add_css_class("pdf-pop")
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
        self.in_size = Gtk.SpinButton.new_with_range(6, 72, 1)
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
        self._stop_edit()
        self.doc = doc
        self.path = path
        while (c := self.pages.get_first_child()):
            self.pages.remove(c)
        self.pics, self.areas, self.done, self.hits, self.hit_i = [], [], {}, [], -1
        self.page_objs, self.layouts = {}, {}
        self.sel = self.live = self.hover = None
        self.undo_stack, self.redo_stack = [], []
        self._refs = {}
        self.dirty = False
        self.sizes = [doc.get_page(i).get_size() for i in range(doc.get_n_pages())]
        for i, _ in enumerate(self.sizes):
            ov = Gtk.Overlay()
            p = Gtk.Picture(content_fit=Gtk.ContentFit.FILL, can_shrink=True)
            p.add_css_class("pdf-page")
            ov.set_child(p)
            da = Gtk.DrawingArea()
            da.set_draw_func(self._draw_overlay, i)
            ov.add_overlay(da)
            drag = Gtk.GestureDrag()
            drag.connect("drag-begin", self._drag_begin, i)
            drag.connect("drag-update", self._drag_update, i)
            drag.connect("drag-end", self._drag_end, i)
            da.add_controller(drag)
            motion = Gtk.EventControllerMotion()
            motion.connect("motion", self._motion, i)
            motion.connect("leave", lambda *_: (self._set_hover(None), self._clear_ghost()))
            da.add_controller(motion)
            self.pics.append(p)
            self.areas.append(da)
            self.pages.append(ov)
        self.set_tool(self.tool)
        self.search.set_text("")
        self.hit_lbl.set_label("")
        self.fit = True
        self.scroll.get_vadjustment().set_value(0)
        self._sync_tools()
        GLib.idle_add(self._refit)

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

    def _apply_zoom(self, z):
        page = self.current_page()
        self.zoom = z
        self.done = {}
        for p, (w, h) in zip(self.pics, self.sizes):
            p.set_size_request(int(w * z), int(h * z))
        self.zoom_lbl.set_label("Breite" if self.fit else f"{round(z * 100)} %")
        GLib.idle_add(lambda: (self.goto(page, render_only=False), False)[-1])

    # ---------- Position ----------
    def offsets(self):
        y, out = GAP, []
        for w, h in self.sizes:
            out.append(y)
            y += h * self.zoom + GAP
        return out

    def current_page(self):
        if not self.doc:
            return 0
        mid = self.scroll.get_vadjustment().get_value() + self.scroll.get_height() / 3
        offs = self.offsets()
        return max(0, sum(1 for o in offs if o <= mid) - 1)

    def goto(self, i, render_only=False, y_in_page=0):
        if not self.doc:
            return
        i = max(0, min(i, len(self.sizes) - 1))
        if not render_only:
            self.scroll.get_vadjustment().set_value(max(0, self.offsets()[i] - GAP + y_in_page * self.zoom))
        self.update()

    def update(self):
        if not self.doc:
            return
        va = self.scroll.get_vadjustment()
        top, bottom = va.get_value(), va.get_value() + max(va.get_page_size(), 400)
        offs = self.offsets()
        scale = self.zoom * self.get_scale_factor()
        for i, o in enumerate(offs):
            h = self.sizes[i][1] * self.zoom
            if o + h >= top - h and o <= bottom + h:  # sichtbar oder direkt daneben
                if self.done.get(i) != self.zoom:
                    marks = [r for p, r in self.hits if p == i]
                    cur = None
                    if 0 <= self.hit_i < len(self.hits) and self.hits[self.hit_i][0] == i:
                        cur = marks.index(self.hits[self.hit_i][1])
                    self.pics[i].set_paintable(render(self.page(i), scale, marks, cur))
                    self.done[i] = self.zoom
        cur = self.current_page()
        self.page_lbl.set_label(f"Seite {cur + 1} / {len(self.sizes)}")
        self.prev.set_sensitive(cur > 0)
        self.next.set_sensitive(cur < len(self.sizes) - 1)

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
        text = self.selected_text()
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
        if lines:
            a = self._make_markup(kind, i, lines, self.colors[kind])
            self._push([("add", i, a)])
            self.sel = None
            self._rerender(i)

    def _text_size(self, text, font, size):
        import cairo
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        lay = PangoCairo.create_layout(cairo.Context(surf))
        PangoCairo.context_set_resolution(lay.get_context(), 72)
        lay.set_font_description(Pango.FontDescription.from_string(f"{font} {size}"))
        lay.set_text(text, -1)
        _ink, logical = lay.get_pixel_extents()
        return logical.width, logical.height

    def _make_free_text(self, i, x, y, text, font, size, color):
        """Text an (x, y) = linke obere Ecke (Seitenkoordinaten). Rahmen wird an den Text angepasst."""
        P = _poppler()
        w, h = self.sizes[i]
        tw, th = self._text_size(text, font, size)
        pad = 3
        x2 = min(w, x + tw + 2 * pad + size * 0.6)    # etwas Luft: Poppler bricht sonst schon früher um
        y2 = min(h, y + th + 2 * pad + size * 0.2)
        a = P.AnnotFreeText.new(self.doc, _prect(x, h - y2, x2, h - y))
        fd = P.FontDescription.new(font)
        fd.size_pt = size
        a.set_font_desc(fd)
        a.set_font_color(_pcolor(color))
        a.set_border_width(0)
        a.set_contents(text)
        self.page(i).add_annot(a)
        return a

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
            if box[0] - tol <= x <= box[2] + tol and box[1] - tol <= y <= box[3] + tol:
                out.append((a, box))
        return out[::-1]

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
        if self.undo_stack:
            ops = self.undo_stack.pop()
            self._apply(ops, True)
            self.redo_stack.append(ops)
            self.dirty = True
            self._sync_tools()

    def redo(self):
        if self.redo_stack:
            ops = self.redo_stack.pop()
            self._apply(ops, False)
            self.undo_stack.append(ops)
            self.dirty = True
            self._sync_tools()

    # ---------- Speichern ----------
    def save(self, path=None):
        self.commit_edit()
        target = path or self.path
        if not self.doc or not target:
            return False
        tmp = os.path.join(os.path.dirname(target) or ".", f".{os.path.basename(target)}.scourge-tmp")
        try:
            import formats
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
        t = self.tool
        if t == "select" or t in MARKUP:
            k, d = self.char_at(i, px, py)
            self.sel = (i, k, k) if k is not None and d < 40 else None
            self._redraw_all()
        elif t == "ink":
            self.live = (i, "ink", [(px, py)])
        elif t == "rect":
            self.live = (i, "rect", (px, py, px, py))

    def _drag_update(self, g, dx, dy, i):
        if not self._drag:
            return
        _i, sx, sy = self._drag
        px, py = sx + dx / self.zoom, sy + dy / self.zoom
        t = self.tool
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
        self.areas[i].queue_draw()

    def _drag_end(self, g, dx, dy, i):
        if not self._drag:
            return
        _i, sx, sy = self._drag
        self._drag = None
        px, py = sx + dx / self.zoom, sy + dy / self.zoom
        click = abs(dx) < 3 and abs(dy) < 3
        t = self.tool
        if t == "select":
            if click:
                self.sel = None
                self._redraw_all()
                if not self._follow_link(i, px, py):
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
            e = self.edit
            if e and e["i"] == i and self._edit_hit(px, py):
                self._edit_click_cursor(px, py)
            else:
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
        elif t == "erase" and click:
            hits = self.annots_at(i, px, py)
            if hits:
                a = hits[0][0]
                self.page(i).remove_annot(a)
                self._push([("remove", i, a)])
                self.hover = None
                self._rerender(i)

    def _motion(self, ctrl, x, y, i):
        px, py = self._pt(i, x, y)
        if self.tool == "text":
            old = self.ghost
            self.ghost = (i, px, py)
            for g in (old, self.ghost):
                if g:
                    self.areas[g[0]].queue_draw()
        elif self.tool == "erase":
            hits = self.annots_at(i, px, py)
            self._set_hover((i, hits[0][1]) if hits else None)
        elif self.tool == "select" and not self._drag:
            link = self._link_at(i, px, py)
            self.areas[i].set_cursor_from_name("pointer" if link else "text")

    def _set_hover(self, hv):
        if hv != self.hover:
            old = self.hover
            self.hover = hv
            for x in (old, hv):
                if x:
                    self.areas[x[0]].queue_draw()

    # ---------- Text direkt auf der Seite tippen ----------
    def start_edit(self, i, x, y, old=None):
        """Eingabe an (x, y) = linke obere Ecke beginnen. old = vorhandener Freitext, der bearbeitet wird:
        er verschwindet solange aus der Seite und wird live neu gezeichnet."""
        text = ""
        if old is not None:
            r = old.get_rectangle()
            x, y = r.x1, self.sizes[i][1] - r.y2
            text = old.get_contents() or ""
            try:
                fd = old.get_font_desc()
                if fd and fd.font_name:
                    fam = fd.font_name
                    self.font = next((f for _n, f in FONTS if f.lower() == fam.lower()), fam)
                if fd and fd.size_pt:
                    self.font_size = fd.size_pt
            except Exception:
                pass
            col = _hex_of(old.get_font_color())
            if col:
                self.colors["text"] = col
            self.page(i).remove_annot(old)
            self._rerender(i)
        else:
            y -= self.font_size * 0.5          # Klickpunkt = Mitte der ersten Zeile
        self.edit = {"i": i, "x": x, "y": max(0, y), "text": text, "cur": len(text), "old": old,
                     "orig": (text, self.font, self.font_size, self.colors["text"])}
        self.ghost = None
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
        i, old, text = e["i"], e["old"], e["text"].rstrip()
        unchanged = (e["text"], self.font, self.font_size, self.colors["text"]) == e["orig"]
        if old is not None and unchanged:
            self._restore(i, old)
        else:
            ops = [("remove", i, old)] if old is not None else []
            if text.strip():
                a = self._make_free_text(i, e["x"], e["y"], text, self.font, self.font_size, self.colors["text"])
                ops.append(("add", i, a))
            if ops:
                self._push(ops)
        self._rerender(i)
        self._sync_tools()

    def cancel_edit(self):
        e = self.edit
        if not e:
            return
        self._stop_edit()
        if e["old"] is not None:
            self.font, self.font_size, self.colors["text"] = e["orig"][1:]
            self._restore(e["i"], e["old"])
            self._rerender(e["i"])
        self._sync_tools()

    def _edit_style(self):
        if self.edit:
            self.areas[self.edit["i"]].queue_draw()

    def _edit_insert(self, text):
        e = self.edit
        if not e:
            return
        e["text"] = e["text"][:e["cur"]] + text + e["text"][e["cur"]:]
        e["cur"] += len(text)
        self._caret_on = True
        self.areas[e["i"]].queue_draw()

    def _edit_key(self, k, keyval, ctrl, shift):
        e = self.edit
        t, cur = e["text"], e["cur"]
        if k in ("Return", "KP_Enter"):
            if ctrl:
                self.commit_edit()
            else:
                self._edit_insert("\n")
            return True
        if k == "Escape":
            self.commit_edit()
            return True
        if k == "BackSpace" and cur > 0:
            n = 1
            if ctrl:                                  # ganzes Wort
                j = cur
                while j > 0 and t[j - 1].isspace():
                    j -= 1
                while j > 0 and not t[j - 1].isspace():
                    j -= 1
                n = cur - j
            e["text"], e["cur"] = t[:cur - n] + t[cur:], cur - n
        elif k == "BackSpace":
            pass
        elif k == "Delete":
            e["text"] = t[:cur] + t[cur + 1:]
        elif k == "Left":
            e["cur"] = max(0, cur - 1)
        elif k == "Right":
            e["cur"] = min(len(t), cur + 1)
        elif k == "Home":
            e["cur"] = t.rfind("\n", 0, cur) + 1
        elif k == "End":
            j = t.find("\n", cur)
            e["cur"] = len(t) if j < 0 else j
        elif k in ("Up", "Down"):
            lines = t.split("\n")
            ln = t.count("\n", 0, cur)
            col = cur - (t.rfind("\n", 0, cur) + 1)
            ln2 = max(0, min(len(lines) - 1, ln + (-1 if k == "Up" else 1)))
            e["cur"] = sum(len(l) + 1 for l in lines[:ln2]) + min(col, len(lines[ln2]))
        elif ctrl and k.lower() == "a":
            e["cur"] = len(t)
        elif ctrl and k.lower() in ("s", "z", "y"):
            return False                              # Speichern/Rückgängig wie gewohnt
        elif ctrl or k in ("Tab", "ISO_Left_Tab", "Page_Up", "Page_Down", "Prior", "Next"):
            return False
        else:
            return True                               # sonstige Tasten nicht als Werkzeug-Kürzel deuten
        self._caret_on = True
        self.areas[e["i"]].queue_draw()
        return True

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

    def _layout(self, cr, text, size_px):
        lay = PangoCairo.create_layout(cr)
        fd = Pango.FontDescription.from_string(self.font)
        fd.set_absolute_size(max(1.0, size_px) * Pango.SCALE)
        lay.set_font_description(fd)
        lay.set_text(text, -1)
        return lay

    def _edit_lines(self):
        """[(Zeilentext, Ursprung x, Ursprung y)] in Seitenkoordinaten – genau wie Poppler den Text später setzt."""
        e = self.edit
        dx, dy, adv = text_metrics(self.font)
        sz = self.font_size
        return [(line, e["x"] + dx * sz, e["y"] + dy * sz + k * adv * sz)
                for k, line in enumerate(e["text"].split("\n"))]

    def _edit_bounds(self, cr=None):
        import cairo
        cr = cr or cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))
        lines = self._edit_lines()
        sz = self.font_size
        w = max([self._layout(cr, l or " ", sz).get_pixel_extents()[1].width for l, _, _ in lines] + [sz * 0.5])
        x1, y1 = lines[0][1], lines[0][2]
        return x1, y1, x1 + w, lines[-1][2] + self._layout(cr, "Hg", sz).get_pixel_extents()[1].height

    def _edit_hit(self, x, y):
        x1, y1, x2, y2 = self._edit_bounds()
        return x1 - 4 <= x <= x2 + 4 and y1 - 4 <= y <= y2 + 4

    def _edit_click_cursor(self, x, y):
        """Klick in den Text setzt den Cursor dorthin."""
        import cairo
        cr = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))
        lines = self._edit_lines()
        sz = self.font_size
        k = 0
        for n, (_l, _ox, oy) in enumerate(lines):
            if y >= oy:
                k = n
        line, ox, _oy = lines[k]
        lay = self._layout(cr, line, sz)
        _inside, idx, trailing = lay.xy_to_index(int((x - ox) * Pango.SCALE), 0)
        col = len(line.encode()[:idx].decode(errors="ignore")) + trailing
        self.edit["cur"] = sum(len(l) + 1 for l, _, _ in lines[:k]) + min(col, len(line))
        self._caret_on = True
        self.areas[self.edit["i"]].queue_draw()

    def _draw_edit(self, cr, z):
        e = self.edit
        r, g, b = _rgb(self.colors["text"])
        sz = self.font_size
        lines = self._edit_lines()
        x1, y1, x2, y2 = self._edit_bounds(cr)
        # dezenter Rahmen, damit man sieht, wo getippt wird (landet nicht im PDF)
        cr.set_source_rgba(0.24, 0.52, 1.0, 0.55)
        cr.set_line_width(1)
        cr.set_dash([3, 3])
        cr.rectangle(x1 * z - 4.5, y1 * z - 3.5, (x2 - x1) * z + 9, (y2 - y1) * z + 7)
        cr.stroke()
        cr.set_dash([])
        cr.set_source_rgb(r, g, b)
        pos = 0
        caret = None
        for line, ox, oy in lines:
            lay = self._layout(cr, line, sz * z)
            cr.move_to(ox * z, oy * z)
            PangoCairo.show_layout(cr, lay)
            if caret is None and e["cur"] <= pos + len(line):
                bi = len(line[:e["cur"] - pos].encode())
                strong, _weak = lay.get_cursor_pos(bi)
                caret = (ox * z + strong.x / Pango.SCALE, oy * z + strong.y / Pango.SCALE, strong.height / Pango.SCALE)
            pos += len(line) + 1
        if caret and self._caret_on:
            cx, cy, ch = caret
            cr.set_source_rgb(r, g, b)
            cr.rectangle(round(cx), cy, max(1.0, z * 0.9), ch)
            cr.fill()
        if not e["text"]:
            cr.set_source_rgba(r, g, b, 0.35)
            hint = self._layout(cr, "  tippen …", sz * z * 0.8)
            cr.move_to(lines[0][1] * z + 2, lines[0][2] * z + sz * z * 0.12)
            PangoCairo.show_layout(cr, hint)

    def _draw_ghost(self, cr, z):
        """Unter der Maus: so hoch wird der Text (Cursor in Schriftgröße + kleines „Aa“)."""
        _i, x, y = self.ghost
        sz = self.font_size
        r, g, b = _rgb(self.colors["text"])
        lay = self._layout(cr, "Aa", sz * z)
        _ink, log = lay.get_pixel_extents()
        top = (y - sz * 0.5) * z + text_metrics(self.font)[1] * sz * z
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
        if self.live and self.live[0] == i:
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
        if k in ("Page_Down", "Next"):
            self.goto(self.current_page() + 1)
        elif k in ("Page_Up", "Prior"):
            self.goto(self.current_page() - 1)
        elif c and k in ("plus", "equal", "KP_Add"):
            self.set_zoom(self.zoom * 1.25)
        elif c and k in ("minus", "KP_Subtract"):
            self.set_zoom(self.zoom / 1.25)
        elif c and k == "0":
            self.fit_width()
        elif c and k == "f":
            self.search.grab_focus()
        elif c and k == "c" and self.sel and not typing:
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

    def on_scroll(self, ctrl, dx, dy):
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
