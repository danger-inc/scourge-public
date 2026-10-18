# Fensterrahmen im Stil von datist/musicalist (rice-apps/tui.py): Marken-Chip, Tabs mit ¹²³-Tasten,
# schmale umrandete Textknöpfe, ✕ oben rechts, Statuszeile mit Tastenhinweisen unten und Kästen mit
# Titel im Rahmen (wie btop). Eigene Kopie (gleich wie in Notes), damit Scourge auch ohne rice-apps läuft.
import math

import gi
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo

SUP = "⁰¹²³⁴⁵⁶⁷⁸⁹"
FONT = "JetBrainsMono Nerd Font"

CSS = """
.tbar { padding: 10px 14px 6px 14px; }
.tbar label { font-size: 13px; font-weight: bold; }
button.brand { background-color: @primary; background-image: none; color: @on_primary; padding: 1px 9px;
    border-radius: 4px; border: none; box-shadow: none; min-height: 0; margin-right: 10px; }
button.brand label { color: @on_primary; }
button.brand:hover { background-color: alpha(@primary, 0.85); }
button.ttab, button.tbtn, menubutton.tbtn > button, button.tclose, button.tflat {
    background: none; background-image: none; border: none; box-shadow: none; min-height: 0; min-width: 0;
    outline: none; text-shadow: none; }
button.ttab { padding: 2px 9px; border-radius: 4px; color: alpha(@foreground, 0.8); }
button.ttab .key { color: @primary; font-size: 13px; }
button.ttab .ticon { color: @primary; }
button.ttab:hover { background-color: alpha(@primary, 0.12); color: @foreground; }
button.ttab:checked { background-color: alpha(@primary, 0.22); color: @foreground; }
button.ttab:checked .key { color: @foreground; }
button.tbtn, menubutton.tbtn {
    border: 1px solid alpha(@primary, 0.55); border-radius: 4px; padding: 1px 8px; color: @foreground; }
menubutton.tbtn { padding: 0; }
menubutton.tbtn > button { padding: 1px 8px; color: @foreground; border-radius: 4px; }
button.tbtn:hover, button.tbtn:focus-visible, menubutton.tbtn:hover, menubutton.tbtn > button:checked {
    background-color: @primary; color: @on_primary; border-color: @primary; }
menubutton.tbtn:hover > button, menubutton.tbtn > button:checked { color: @on_primary; }
button.tbtn:checked { background-color: alpha(@primary, 0.22); color: @foreground; border-color: @primary; }
button.tbtn:checked:hover { background-color: @primary; color: @on_primary; }
button.tbtn:disabled { opacity: 0.45; }
button.tbtn.danger { border-color: alpha(@tertiary, 0.6); color: @tertiary; }
button.tbtn.danger:hover { background-color: @tertiary; color: @background; }
button.tclose { padding: 1px 6px; border-radius: 4px; color: @foreground; border: 1px solid transparent; }
button.tclose:hover { background-color: @tertiary; color: @background; }
button.tflat { padding: 0 4px; border-radius: 4px; color: @primary; }
button.tflat:hover, button.tflat:focus-visible { background-color: alpha(@primary, 0.18); }
.tstatus { padding: 4px 14px 8px 14px; }
.tstatus label { font-size: 12px; font-weight: bold; }
.tstatus .dim, .dim { color: @secondary; }
.tstatus .accent, .accent { color: @primary; }
.tpanel { margin: 2px 0; }
.tpbody { border: 1px solid alpha(@primary, 0.45); border-top-width: 0; border-radius: 0 0 6px 6px;
    padding: 2px 10px 10px 10px; }
.tpanel.active > .tpbody { border-color: @primary; }
button.trow { background: none; background-image: none; border: none; box-shadow: none; border-radius: 4px;
    padding: 4px 6px; min-height: 0; color: @foreground; outline: none; }
button.trow:hover, button.trow:focus-visible { background-color: alpha(@primary, 0.12); }
button.trow .tr-title { font-size: 13px; font-weight: bold; }
button.trow .tr-when { font-size: 11px; color: @secondary; }
button.trow .tr-pin { color: @primary; font-size: 12px; }
.tempty { color: @secondary; padding: 10px 6px; font-size: 12px; }
.tcard { color: @primary; padding: 24px 12px 10px 12px; background: none; }
.tcard.notitle { padding-top: 10px; }
.tcard > * { color: @foreground; }
"""


def label(text="", *classes, xalign=0, hexpand=False, ellipsize=False):
    lbl = Gtk.Label(label=text, xalign=xalign, hexpand=hexpand)
    if ellipsize:
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
    for c in classes:
        lbl.add_css_class(c)
    return lbl


def hbox(*children, spacing=8, **kw):
    b = Gtk.Box(spacing=spacing, **kw)
    for c in children:
        if c is not None:
            b.append(c)
    return b


def tab(key, icon, name, tooltip=None):
    """¹󰎚 name – Tab-Knopf (Gtk.ToggleButton). .name_lbl lässt sich in schmalen Fenstern ausblenden."""
    b = Gtk.ToggleButton(tooltip_text=tooltip or name, focus_on_click=False, valign=Gtk.Align.CENTER)
    b.add_css_class("ttab")
    b.name_lbl = label(name)
    b.set_child(hbox(label(SUP[key] if key is not None else "", "key"), label(icon, "ticon"), b.name_lbl,
                     spacing=3))
    return b


def button(text, tooltip=None, action=None, callback=None, danger=False):
    """[ text ] – schmaler umrandeter Knopf."""
    b = Gtk.Button(label=text, tooltip_text=tooltip, valign=Gtk.Align.CENTER, focus_on_click=False)
    b.add_css_class("tbtn")
    if danger:
        b.add_css_class("danger")
    if action:
        b.set_action_name(action)
    if callback:
        b.connect("clicked", lambda *_: callback())
    return b


def hint(key, text):
    return hbox(label(key, "accent"), label(text, "dim"), spacing=5)


class Panel(Gtk.Box):
    """Kasten mit Titel im oberen Rahmen:  ╭─ titel ──────╮  (obere Kante gezeichnet, Rest per CSS)."""
    H = 22

    def __init__(self, title="", child=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("tpanel")
        self.text = ""
        self.head = Gtk.DrawingArea(content_height=self.H)
        self.head.set_draw_func(self._draw)
        self.append(self.head)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, vexpand=True)
        self.body.add_css_class("tpbody")
        self.append(self.body)
        self.font = Pango.FontDescription.from_string(f"{FONT} Bold 10")
        self.set_title(title)
        if child is not None:
            self.body.append(child)

    def set_title(self, markup):
        if markup != self.text:
            self.text = markup
            self.head.queue_draw()

    def _draw(self, area, cr, w, h):
        ctx = area.get_style_context()
        ok, p = ctx.lookup_color("primary")
        if not ok:
            ok, p = ctx.lookup_color("color9")
        if not ok:
            p = Gdk.RGBA()
            p.parse("#ffb68e")
        layout = area.create_pango_layout("")
        layout.set_font_description(self.font)
        layout.set_markup(f" {self.text} ", -1)
        tw, th = layout.get_pixel_size()
        r, y, x0, x1, tx = 6, h / 2, 0.5, w - 0.5, 12
        cr.set_line_width(1)
        cr.set_source_rgba(p.red, p.green, p.blue, 1.0 if self.has_css_class("active") else 0.45)
        cr.move_to(x0, h)
        cr.line_to(x0, y + r)
        cr.arc(x0 + r, y + r, r, math.pi, 1.5 * math.pi)
        cr.line_to(tx, y)
        cr.move_to(tx + tw, y)
        cr.line_to(x1 - r, y)
        cr.arc(x1 - r, y + r, r, 1.5 * math.pi, 2 * math.pi)
        cr.line_to(x1, h)
        cr.stroke()
        cr.set_source_rgba(p.red, p.green, p.blue, 1)
        cr.move_to(tx, (h - th) / 2)
        PangoCairo.show_layout(cr, layout)


def esc(text):
    return GLib.markup_escape_text(str(text))


class Card(Gtk.Box):
    """Kasten mit Titel in der oberen Rahmenlinie (wie Panel/btop), als normale Box: Kinder per append().
    Titel und Rahmen werden nach den Kindern selbst gezeichnet (kein Kind-Widget – so überlebt der Titel
    auch ein Leeren der Box) und ohne deckenden Hintergrund, damit es im Glas-Modus sauber aussieht.
    Farbe: CSS-color von .tcard (= @primary), die Kinder bekommen wieder @foreground."""
    TITLE_H = 20
    PAD = (24, 12, 10, 12)          # muss zu .tcard { padding } in CSS passen (oben, rechts, unten, links)
    PAD_NOTITLE = 10

    def __init__(self, title="", spacing=8):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
        self.add_css_class("tcard")
        self.font = Pango.FontDescription.from_string(f"{FONT} Bold 10")
        self.text = ""
        self.set_title(title)

    def set_title(self, title):
        self.text = (title or "").lower()
        (self.remove_css_class if self.text else self.add_css_class)("notitle")
        self.queue_draw()

    def do_snapshot(self, snap):
        Gtk.Box.do_snapshot(self, snap)
        w, h = self.get_width(), self.get_height()
        if w < 4 or h < 4:
            return
        gi.require_version("Graphene", "1.0")
        from gi.repository import Graphene
        col = self.get_color()
        # Zeichnen im Inhaltsbereich → um den CSS-Innenabstand nach außen verschieben
        top = self.PAD[0] if self.text else self.PAD_NOTITLE
        _t, pr, pb, pl = self.PAD
        W, H = w + pl + pr, h + top + pb
        snap.save()
        pt = Graphene.Point()
        pt.init(-pl, -top)
        snap.translate(pt)
        cr = snap.append_cairo(Graphene.Rect().init(0, 0, W, H))
        w, h = W, H
        r, x0, x1, y1 = 6, 0.5, w - 0.5, h - 0.5
        y0, gap = 0.5, None
        cr.set_line_width(1)
        if self.text:
            layout = self.create_pango_layout("")
            layout.set_font_description(self.font)
            layout.set_text(f" {self.text} ", -1)
            layout.set_width(max(1, w - 30) * Pango.SCALE)
            layout.set_ellipsize(Pango.EllipsizeMode.END)
            tw, th = layout.get_pixel_size()
            y0 = round(self.TITLE_H / 2) + 0.5
            gap = (12, 12 + tw)
            cr.set_source_rgba(col.red, col.green, col.blue, 1)
            cr.move_to(12, y0 - th / 2)
            PangoCairo.show_layout(cr, layout)
        cr.set_source_rgba(col.red, col.green, col.blue, 1.0 if self.has_css_class("active") else 0.45)
        cr.move_to(gap[1] if gap else x0 + r, y0)
        cr.line_to(x1 - r, y0)
        cr.arc(x1 - r, y0 + r, r, 1.5 * math.pi, 2 * math.pi)
        cr.line_to(x1, y1 - r)
        cr.arc(x1 - r, y1 - r, r, 0, 0.5 * math.pi)
        cr.line_to(x0 + r, y1)
        cr.arc(x0 + r, y1 - r, r, 0.5 * math.pi, math.pi)
        cr.line_to(x0, y0 + r)
        cr.arc(x0 + r, y0 + r, r, math.pi, 1.5 * math.pi)
        if gap:
            cr.line_to(gap[0], y0)
        else:
            cr.close_path()
        cr.stroke()
        snap.restore()
