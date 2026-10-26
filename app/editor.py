# Meme-Editor für Scourge: Zuschneiden, Stift, Text und klassischer Meme-Text oben/unten.
# Alles bleibt bis zum Export unabhängig vom Originalbild (Striche und Texte liegen in
# Bildkoordinaten darüber, der Zuschnitt ist nur ein Rechteck), deshalb geht Rückgängig immer.
import copy, datetime, io, math, os, re
import chrome as ch

import cairo
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Gio, GLib, Graphene, Gtk, Pango, PangoCairo
from PIL import Image, ImageOps

MATUGEN_CSS = os.path.expanduser("~/.cache/matugen/colors.css")

def _theme_text():
    """Farben des gewählten Designs (istheme: Auswahl + Hell/Dunkel), sonst die von matugen."""
    try:
        import istheme
        return istheme.resolve()[0]
    except ImportError:
        return open(MATUGEN_CSS).read()


PALETTE = ["#ffffff", "#000000", "#ff3b30", "#ffd60a", "#34c759", "#0a84ff", "#ff6ac1"]
RATIOS = [("Frei", None), ("1:1", 1.0), ("4:3", 4 / 3), ("16:9", 16 / 9), ("9:16", 9 / 16)]

EDITOR_CSS = """
.ed-bar { margin: 6px 16px 0 16px; }
.canvas-frame {
    background-color: @surface_container; border: 2px solid @color9; border-radius: 15px;
    margin: 0 0 16px 16px;
}
button.pb:checked { background-color: @color9; color: @background; }
button.pb.tool { padding: 4px 12px; }
button.swatch {
    min-width: 26px; min-height: 26px; padding: 0; border-radius: 999px;
    border: 2px solid alpha(@foreground, 0.25); background-image: none; box-shadow: none;
}
button.swatch:hover { border-color: @foreground; }
button.swatch.active { border: 3px solid @color9; }
button.swatch.custom { background-color: transparent; }
entry.pb {
    background-color: @background; color: @foreground; border: 2px solid alpha(@color9, 0.45);
    border-radius: 11px; padding: 2px 10px; min-height: 30px; box-shadow: none; outline: none;
}
entry.pb:focus-within { border-color: @color9; }
/* Flache, minimale Slider im Stil der musicalist-Meter: dünne Spur, Akzent-Füllung,
   schlanker Riegel statt dickem Kreis. */
scale.pb { min-height: 20px; }
scale.pb trough { background-color: alpha(@foreground, 0.12); border-radius: 2px; min-height: 4px; border: none; }
scale.pb highlight { background-color: @color9; border-radius: 2px; border: none; min-height: 4px; }
scale.pb slider {
    background-color: @color9; border: none; min-width: 5px; min-height: 16px;
    border-radius: 3px; box-shadow: none; margin: -6px -2px;
}
scale.pb:hover slider { background-color: @color9; min-width: 6px; }
scale.pb:disabled highlight, scale.pb:disabled slider { background-color: alpha(@foreground, 0.3); }
.key-hint { font-size: 11px; color: alpha(@foreground, 0.45); }
""" + "\n".join(f"button.sw-{c[1:]} {{ background-color: {c}; }}" for c in PALETTE)


def hex_to_rgb(h):
    return tuple(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))


def rgba_to_hex(rgba):
    return "#{:02x}{:02x}{:02x}".format(*(round(v * 255) for v in (rgba.red, rgba.green, rgba.blue)))


def pil_to_surface(img):
    """PIL-Bild -> cairo-Fläche (vormultipliziertes BGRA, so wie cairo es erwartet)."""
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    im = img.convert("RGBA").convert("RGBa")
    r, g, b, a = im.split()
    data = bytearray(Image.merge("RGBA", (b, g, r, a)).tobytes())
    w, h = im.size
    stride = cairo.ImageSurface.format_stride_for_width(cairo.FORMAT_ARGB32, w)
    if stride != w * 4:  # kommt bei ARGB32 praktisch nie vor, aber sicher ist sicher
        rows = [data[y * w * 4:(y + 1) * w * 4] + bytes(stride - w * 4) for y in range(h)]
        data = bytearray(b"".join(rows))
    return cairo.ImageSurface.create_for_data(data, cairo.FORMAT_ARGB32, w, h, stride), data


def rect(x, y, w, h):
    return Graphene.Rect().init(x, y, w, h)


def fmt_time(sec):
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


class Canvas(Gtk.Widget):
    """Zeichenfläche: Bild oder laufendes Video unten, Striche/Texte/Zuschnitt per cairo darüber."""

    def __init__(self, editor):
        super().__init__(hexpand=True, vexpand=True, focusable=True)
        self.editor = editor

    def do_snapshot(self, snap):
        self.editor.snapshot(snap, self.get_width(), self.get_height())


def pick_meme_font():
    names = {f.get_name() for f in PangoCairo.FontMap.get_default().list_families()}
    for name in ("Anton", "Impact", "Oswald", "DejaVu Sans Condensed"):
        if name in names:
            return name + (" Bold" if name.startswith("DejaVu") else "")
    return "Sans Bold"


class MemeEditor(Gtk.Box):
    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.app = app
        self.font = pick_meme_font()
        self.accent = (1.0, 0.71, 0.56)
        self.reload_colors()

        self.img = None          # Original (PIL), nur zum Vergleichen
        self.surface = None      # volle Auflösung für den Export
        self._cache = None       # verkleinerte Fläche für die Anzeige
        self._cache_key = None
        self.W = self.H = 0

        self.objects = []        # {"type": "stroke"/"text", ...} in Bildkoordinaten
        self.crop = (0, 0, 0, 0)
        self.top_text = ""
        self.bottom_text = ""
        self.undo_stack, self.redo_stack = [], []
        self._undo_key = None
        self._restoring = False
        self._next_id = 1

        self.tool = "text"
        self.prev_tool = "text"
        self.draft = None        # Zuschnitt-Entwurf im Zuschneiden-Modus
        self.ratio = None
        self.pen_color = "#ff3b30"
        self.pen_width = 6       # Bildschirm-Pixel
        self.text_color = "#ffffff"
        self.sel_id = None
        self._bboxes = {}
        self._drag = None
        self._xf = (1, 0, 0, 0, 0)
        self.video = None        # Pfad, wenn ein Video bearbeitet wird
        self.media = None        # Gtk.MediaFile zum Abspielen
        self.video_info = {}
        self.exporting = None

        self.build()

    # ================= Aufbau =================
    def build(self):
        bar = Gtk.Box(spacing=8)
        bar.add_css_class("ed-bar")
        back = Gtk.Button(label="󰁍", tooltip_text="Zurück zur Bildinfo (Esc)")
        back.add_css_class("pb")
        back.connect("clicked", lambda *_: self.app.leave_editor())
        bar.append(back)

        self.tool_buttons = {}
        group = None
        for tool, label, key in (("crop", "󰆞  Zuschneiden", "C"), ("pen", "󰏪  Stift", "B"), ("text", "󰊄  Text", "T")):
            b = Gtk.ToggleButton(label=label, tooltip_text=f"Taste {key}")
            b.add_css_class("pb"); b.add_css_class("tool")
            if group:
                b.set_group(group)
            group = group or b
            b.connect("toggled", self.on_tool_toggled, tool)
            self.tool_buttons[tool] = b
            bar.append(b)
        spacer = Gtk.Box(hexpand=True)
        bar.append(spacer)
        self.undo_btn = Gtk.Button(label="󰕌", tooltip_text="Rückgängig (Strg+Z)")
        self.redo_btn = Gtk.Button(label="󰑎", tooltip_text="Wiederholen (Strg+Umschalt+Z)")
        for b, cb in ((self.undo_btn, self.undo), (self.redo_btn, self.redo)):
            b.add_css_class("pb")
            b.connect("clicked", lambda *_, cb=cb: cb())
            bar.append(b)
        self.append(bar)

        body = Gtk.Box(spacing=16, vexpand=True)
        self.append(body)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=True)
        frame = Gtk.Box(hexpand=True, vexpand=True)
        frame.add_css_class("canvas-frame")
        frame.set_overflow(Gtk.Overflow.HIDDEN)
        self.canvas = Canvas(self)
        frame.append(self.canvas)
        left.append(frame)
        left.append(self.build_video_bar())
        body.append(left)

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag_update)
        drag.connect("drag-end", self.on_drag_end)
        self.canvas.add_controller(drag)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self.on_motion)
        self.canvas.add_controller(motion)

        keys = Gtk.EventControllerKey()  # Bubble-Phase: Eingabefelder bekommen Tasten zuerst
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)

        # --- Seitenleiste ---
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_size_request(300, -1)
        scroll.set_hexpand(False)  # Schieberegler wollen sich ausdehnen, die Leiste soll schmal bleiben
        scroll.set_margin_end(16); scroll.set_margin_bottom(16)
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        scroll.set_child(side)
        body.append(scroll)

        # Meme-Text
        c = self.card(side, "Meme-Text")
        self.top_entry = self.entry("Text oben", lambda e: self.on_meme_text("top", e))
        self.bottom_entry = self.entry("Text unten", lambda e: self.on_meme_text("bottom", e))
        c.append(self.top_entry)
        c.append(self.bottom_entry)
        c.append(self.note("Wird automatisch groß, weiß mit schwarzer Kontur und passend skaliert."))

        # Werkzeug-Optionen
        self.tool_card = self.card(side, "Werkzeug")
        self.tool_title = None            # Titel zeichnet die Karte selbst (tool_card.set_title)
        self.panes = Gtk.Stack(vhomogeneous=False, transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.tool_card.append(self.panes)
        self.panes.add_named(self.build_crop_pane(), "crop")
        self.panes.add_named(self.build_pen_pane(), "pen")
        self.panes.add_named(self.build_text_pane(), "text")

        # Export
        c = self.card(side, "Fertig?")
        row = Gtk.Box(spacing=8, homogeneous=True)
        self.save_btn = Gtk.Button(label="󰆓  Speichern", tooltip_text="Als PNG oder JPG speichern (Strg+S)")
        self.save_btn.add_css_class("pb")
        self.save_btn.connect("clicked", lambda *_: self.save())
        self.copy_btn = Gtk.Button(label="󰆏  Kopieren", tooltip_text="In die Zwischenablage (Strg+Umschalt+C)")
        self.copy_btn.add_css_class("pb")
        self.copy_btn.connect("clicked", lambda *_: self.copy_image())
        row.append(self.save_btn); row.append(self.copy_btn)
        c.append(row)
        self.export_note = self.note("Kopieren legt das fertige Meme in die Zwischenablage, z. B. für Discord oder WhatsApp.")
        c.append(self.export_note)

    def build_video_bar(self):
        """Abspiel-Leiste, nur bei Videos sichtbar."""
        bar = Gtk.Box(spacing=8, visible=False, margin_start=16)
        self.play_btn = Gtk.Button(label="󰏤", tooltip_text="Abspielen/Pause (Leertaste)")
        self.play_btn.add_css_class("pb"); self.play_btn.add_css_class("small")
        self.play_btn.connect("clicked", lambda *_: self.toggle_play())
        bar.append(self.play_btn)
        self.seek = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 0.01)
        self.seek.set_draw_value(False)
        self.seek.set_hexpand(True)
        self.seek.add_css_class("pb")
        self._seek_sync = False
        self.seek.connect("value-changed", self.on_seek)
        bar.append(self.seek)
        self.time_label = Gtk.Label(label="0:00 / 0:00")
        self.time_label.add_css_class("key-hint")
        bar.append(self.time_label)
        self.mute_btn = Gtk.ToggleButton(label="󰕾", tooltip_text="Ton aus/an")
        self.mute_btn.add_css_class("pb"); self.mute_btn.add_css_class("small")
        self.mute_btn.connect("toggled", self.on_mute)
        bar.append(self.mute_btn)
        self.video_bar = bar
        return bar

    def card(self, parent, title):
        box = ch.Card(title, spacing=10)
        parent.append(box)
        return box

    def note(self, text):
        l = Gtk.Label(label=text, xalign=0, wrap=True, max_width_chars=30)  # sonst wird die Leiste so breit wie der Text
        l.add_css_class("note")
        return l

    def entry(self, placeholder, on_change):
        e = Gtk.Entry(placeholder_text=placeholder)
        e.add_css_class("pb")
        e.connect("changed", on_change)
        return e

    def slider(self, lo, hi, value, on_change):
        s = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, 1)
        s.set_draw_value(False)
        s.set_value(value)
        s.add_css_class("pb")
        s.set_hexpand(True)
        s.connect("value-changed", on_change)
        return s

    def labeled(self, text, widget):
        row = Gtk.Box(spacing=10)
        l = Gtk.Label(label=text, xalign=0, width_chars=7)
        l.add_css_class("key")
        if isinstance(widget, Gtk.Scale):
            ch.reset_on_dblclick(l, widget, widget.get_value())
        row.append(l)
        row.append(widget)
        return row

    def palette(self, on_pick):
        row = Gtk.Box(spacing=6)
        buttons = {}

        def circle(**kw):
            # feste Größe und zentriert, sonst streckt die Reihe die Kreise zu Ovalen
            b = Gtk.Button(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, **kw)
            b.set_size_request(26, 26)
            b.add_css_class("swatch")
            row.append(b)
            return b

        for col in PALETTE:
            b = circle(tooltip_text=col)
            b.add_css_class(f"sw-{col[1:]}")
            b.connect("clicked", lambda *_, col=col: on_pick(col))
            buttons[col] = b
        custom = circle(tooltip_text="Eigene Farbe")
        custom.add_css_class("custom")
        wheel = Gtk.DrawingArea(content_width=22, content_height=22, can_target=False)
        wheel.set_draw_func(self.draw_color_wheel)
        custom.set_child(wheel)
        custom.connect("clicked", lambda *_: self.choose_color(on_pick))
        buttons["custom"] = custom
        return row, buttons, custom

    def draw_color_wheel(self, area, cr, w, h):
        """Regenbogen-Kreis mit Plus für 'eigene Farbe'."""
        import colorsys
        cx, cy, r = w / 2, h / 2, min(w, h) / 2
        n = 36
        for i in range(n):
            cr.move_to(cx, cy)
            cr.arc(cx, cy, r, 2 * math.pi * i / n - math.pi / 2, 2 * math.pi * (i + 1.05) / n - math.pi / 2)
            cr.close_path()
            cr.set_source_rgb(*colorsys.hsv_to_rgb(i / n, 0.85, 1))
            cr.fill()
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        for lw, col in ((4.5, (0, 0, 0, 0.45)), (2.2, (1, 1, 1, 1))):
            cr.set_source_rgba(*col)
            cr.set_line_width(lw)
            cr.move_to(cx - r * 0.42, cy); cr.line_to(cx + r * 0.42, cy)
            cr.move_to(cx, cy - r * 0.42); cr.line_to(cx, cy + r * 0.42)
            cr.stroke()

    def choose_color(self, on_pick):
        dlg = Gtk.ColorDialog(with_alpha=False, title="Eigene Farbe")

        def done(d, res):
            try:
                on_pick(rgba_to_hex(d.choose_rgba_finish(res)))
            except GLib.Error:
                pass  # abgebrochen
        dlg.choose_rgba(self.app.win, None, None, done)

    def mark_swatch(self, buttons, col):
        # eigene Farben markieren den Regenbogen-Kreis
        key = col if col in buttons else "custom"
        for c, b in buttons.items():
            (b.add_css_class if c == key else b.remove_css_class)("active")

    def build_crop_pane(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.append(self.note("Rahmen ins Bild ziehen. Ecken ziehen zum Anpassen, innen ziehen zum Verschieben."))
        row = Gtk.Box(spacing=6)
        self.ratio_buttons = {}
        group = None
        for label, r in RATIOS:
            b = Gtk.ToggleButton(label=label)
            b.add_css_class("pb"); b.add_css_class("small")
            if group:
                b.set_group(group)
            group = group or b
            b.connect("toggled", lambda btn, r=r: btn.get_active() and self.set_ratio(r))
            self.ratio_buttons[label] = b
            row.append(b)
        self.ratio_buttons["Frei"].set_active(True)
        box.append(row)
        row = Gtk.Box(spacing=8, homogeneous=True)
        apply = Gtk.Button(label="󰄬  Anwenden", tooltip_text="Enter")
        apply.add_css_class("pb")
        apply.connect("clicked", lambda *_: self.apply_crop())
        reset = Gtk.Button(label="Ganzes Bild")
        reset.add_css_class("pb")
        reset.connect("clicked", lambda *_: self.reset_draft())
        row.append(apply); row.append(reset)
        box.append(row)
        self.crop_info = Gtk.Label(label="", xalign=0)
        self.crop_info.add_css_class("key-hint")
        box.append(self.crop_info)
        return box

    def build_pen_pane(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        row, self.pen_swatches, self.pen_custom = self.palette(self.set_pen_color)
        box.append(row)
        self.mark_swatch(self.pen_swatches, self.pen_color)
        box.append(self.labeled("Stärke", self.slider(2, 48, self.pen_width, self.on_pen_width)))
        box.append(self.note("Einfach ins Bild malen. Strg+Z nimmt den letzten Strich zurück."))
        return box

    def build_text_pane(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.text_hint = self.note("Ins Bild klicken, um einen Text zu setzen. Vorhandene Texte anklicken und ziehen zum Verschieben.")
        box.append(self.text_hint)
        self.text_edit = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.text_entry = self.entry("Text eingeben …", self.on_text_changed)
        self.text_edit.append(self.text_entry)
        self.size_slider = self.slider(2, 40, 8, self.on_text_size)
        self.text_edit.append(self.labeled("Größe", self.size_slider))
        row, self.text_swatches, self.text_custom = self.palette(self.set_text_color)
        self.text_edit.append(row)
        row = Gtk.Box(spacing=8, homogeneous=True)
        self.outline_btn = Gtk.ToggleButton(label="Kontur", active=True)
        self.outline_btn.add_css_class("pb"); self.outline_btn.add_css_class("small")
        self.outline_btn.connect("toggled", self.on_outline)
        self.caps_btn = Gtk.ToggleButton(label="GROSS", active=False)
        self.caps_btn.add_css_class("pb"); self.caps_btn.add_css_class("small")
        self.caps_btn.connect("toggled", self.on_caps)
        delete = Gtk.Button(label="󰆴  Löschen", tooltip_text="Entf")
        delete.add_css_class("pb"); delete.add_css_class("small")
        delete.connect("clicked", lambda *_: self.delete_selected())
        row.append(self.outline_btn); row.append(self.caps_btn); row.append(delete)
        self.text_edit.append(row)
        box.append(self.text_edit)
        return box

    # ================= Bild & Zustand =================
    def reload_colors(self):
        try:
            m = re.search(r"@define-color color9 (#[0-9a-fA-F]{6})", _theme_text())
            if m:
                self.accent = hex_to_rgb(m.group(1))
        except OSError:
            pass
        if hasattr(self, "canvas"):
            self.canvas.queue_draw()

    def open(self, img, video=None, info=None):
        if img is self.img:
            if self.media:
                self.media.play()
            return  # gleiches Bild: Bearbeitung bleibt erhalten
        self.stop_video()
        self.img = img
        self.video, self.video_info = video, info or {}
        self.tool, self.draft = "text", None  # alten Zuschnitt-Entwurf nicht aufs neue Bild anwenden
        self.surface, self._data = pil_to_surface(img)
        self.W, self.H = self.surface.get_width(), self.surface.get_height()
        self._cache = self._cache_key = None
        self.objects, self.undo_stack, self.redo_stack = [], [], []
        self.crop = (0, 0, self.W, self.H)
        self.sel_id = None
        self._restoring = True
        self.top_entry.set_text(""); self.bottom_entry.set_text("")
        self._restoring = False
        self.top_text = self.bottom_text = ""
        self.tool_buttons["text"].set_active(True)
        self.set_tool("text")
        self.update_buttons()
        self.video_bar.set_visible(bool(video))
        if video:
            self.save_btn.set_tooltip_text("Als MP4 oder GIF speichern (Strg+S)")
            self.export_note.set_label("Text, Zeichnungen und Zuschnitt werden ins ganze Video eingebrannt. "
                                       "Kopieren legt die fertige MP4-Datei in die Zwischenablage.")
            self.start_video(video)
        else:
            self.save_btn.set_tooltip_text("Als PNG oder JPG speichern (Strg+S)")
            self.export_note.set_label("Kopieren legt das fertige Meme in die Zwischenablage, z. B. für Discord oder WhatsApp.")
        self.canvas.queue_draw()

    # ================= Video =================
    def start_video(self, path):
        self.media = Gtk.MediaFile.new_for_filename(path)
        self.media.set_loop(True)
        self.media.connect("invalidate-contents", lambda *_: self.canvas.queue_draw())
        self.media.connect("notify::timestamp", self.on_media_time)
        self.media.connect("notify::error", self.on_media_error)
        self.media.connect("notify::playing", lambda m, _: self.play_btn.set_label("󰏤" if m.get_playing() else "󰐊"))
        self.media.set_muted(self.mute_btn.get_active())
        self.media.play()

    def stop_video(self):
        if self.media:
            from cutter import retire_media  # sicher freigeben, sonst kann GTK beim Zeichnen abstürzen
            m, self.media = self.media, None
            retire_media(m, None)
            self.canvas.queue_draw()

    def pause(self):
        if self.media:
            self.media.pause()

    def toggle_play(self):
        if self.media:
            self.media.pause() if self.media.get_playing() else self.media.play()

    def on_media_time(self, m, _):
        if m is not self.media:
            return
        dur = m.get_duration() or int(self.video_info.get("duration", 0) * 1e6)
        if dur:
            self._seek_sync = True
            self.seek.set_value(m.get_timestamp() / dur)
            self._seek_sync = False
            self.time_label.set_label(f"{fmt_time(m.get_timestamp() / 1e6)} / {fmt_time(dur / 1e6)}")

    def on_seek(self, scale):
        if self._seek_sync or not self.media:
            return
        dur = self.media.get_duration()
        if dur and self.media.is_seekable():
            self.media.seek(int(scale.get_value() * dur))

    def on_mute(self, btn):
        btn.set_label("󰖁" if btn.get_active() else "󰕾")
        if self.media:
            self.media.set_muted(btn.get_active())

    def on_media_error(self, m, _):
        err = m.get_error()
        if err:
            missing = "plug-in" in err.message.lower() or "plugin" in err.message.lower()
            self.time_label.set_label("Abspielen nicht möglich" + (": gst-libav fehlt" if missing else ""))
            self.play_btn.set_sensitive(False)
            self.app.notify("Zum Abspielen fehlt gst-libav. Bearbeiten und Exportieren gehen trotzdem."
                            if missing else f"Video lässt sich nicht abspielen: {err.message[:80]}")

    def state(self):
        return copy.deepcopy({"objects": self.objects, "crop": self.crop,
                              "top": self.top_text, "bottom": self.bottom_text})

    def push_undo(self, key=None):
        if key is not None and key == self._undo_key:
            return  # z. B. Tippen in einem Feld: nur ein Schritt pro Feld
        self._undo_key = key
        self.undo_stack.append(self.state())
        del self.undo_stack[:-100]
        self.redo_stack.clear()
        self.update_buttons()

    def restore(self, st):
        self._restoring = True
        self.objects, self.crop = st["objects"], st["crop"]
        self.top_text, self.bottom_text = st["top"], st["bottom"]
        self.top_entry.set_text(self.top_text)
        self.bottom_entry.set_text(self.bottom_text)
        self._restoring = False
        if self.tool == "crop":
            self.draft = list(self.crop)
        if not self.selected():
            self.sel_id = None
        self.sync_text_pane()
        self.update_buttons()
        self.canvas.queue_draw()

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(self.state())
            self.restore(self.undo_stack.pop())
            self._undo_key = None

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(self.state())
            self.restore(self.redo_stack.pop())
            self._undo_key = None

    def update_buttons(self):
        self.undo_btn.set_sensitive(bool(self.undo_stack))
        self.redo_btn.set_sensitive(bool(self.redo_stack))

    def selected(self):
        return next((o for o in self.objects if o.get("id") == self.sel_id), None)

    # ================= Werkzeuge =================
    def on_tool_toggled(self, btn, tool):
        if btn.get_active():
            self.set_tool(tool)

    def set_tool(self, tool):
        if self.tool == "crop" and tool != "crop":
            self.apply_crop(switch=False)  # Werkzeugwechsel übernimmt den Zuschnitt
        if tool == "crop":
            if self.tool != "crop":
                self.prev_tool = self.tool
            self.draft = list(self.crop)
            self.select(None)
        self.tool = tool
        self._undo_key = None
        names = {"crop": "Zuschneiden", "pen": "Stift", "text": "Text"}
        self.tool_card.set_title(names[tool])
        self.panes.set_visible_child_name(tool)
        self.canvas.set_cursor_from_name("crosshair" if tool in ("crop", "pen") else "text")
        self.sync_text_pane()
        self.update_crop_info()
        self.canvas.queue_draw()

    def set_ratio(self, r):
        self.ratio = r
        if self.draft and r:
            x, y, w, h = self.draft
            cx, cy = x + w / 2, y + h / 2
            if w / h > r:
                w = h * r
            else:
                h = w / r
            self.draft = [cx - w / 2, cy - h / 2, w, h]
            self.update_crop_info()
            self.canvas.queue_draw()

    def reset_draft(self):
        self.draft = [0, 0, self.W, self.H]
        self.ratio_buttons["Frei"].set_active(True)
        self.update_crop_info()
        self.canvas.queue_draw()

    def apply_crop(self, switch=True):
        if self.draft:
            new = tuple(round(v) for v in self.draft)
            if new != tuple(self.crop) and new[2] >= 4 and new[3] >= 4:
                self.push_undo()
                self.crop = new
        if switch and self.tool == "crop":
            self.tool_buttons[self.prev_tool].set_active(True)

    def update_crop_info(self):
        if self.tool == "crop" and self.draft:
            self.crop_info.set_label(f"{round(self.draft[2])} × {round(self.draft[3])} px")

    def set_pen_color(self, col):
        self.pen_color = col
        self.mark_swatch(self.pen_swatches, col)

    def on_pen_width(self, s):
        self.pen_width = s.get_value()

    def select(self, oid):
        old = self.selected()
        if old and old["type"] == "text" and not old["text"].strip() and old.get("id") != oid:
            self.objects.remove(old)  # leere Texte nicht liegen lassen
        self.sel_id = oid
        self._undo_key = None
        self.sync_text_pane()
        self.canvas.queue_draw()

    def sync_text_pane(self):
        o = self.selected()
        has = bool(o and o["type"] == "text")
        self.text_edit.set_visible(has)
        self.text_hint.set_visible(not has)
        if has:
            self._restoring = True
            if self.text_entry.get_text() != o["text"]:
                self.text_entry.set_text(o["text"])
            self.size_slider.set_value(o["size"] / max(1, self.crop[3]) * 100)
            self.outline_btn.set_active(o["outline"])
            self.caps_btn.set_active(o.get("caps", False))
            self.mark_swatch(self.text_swatches, o["color"])
            self._restoring = False

    def on_text_changed(self, e):
        o = self.selected()
        if self._restoring or not o:
            return
        self.push_undo(("text", o["id"]))
        o["text"] = e.get_text()
        self.canvas.queue_draw()

    def on_text_size(self, s):
        o = self.selected()
        if self._restoring or not o:
            return
        self.push_undo(("size", o["id"]))
        o["size"] = s.get_value() / 100 * self.crop[3]
        self.canvas.queue_draw()

    def set_text_color(self, col):
        self.text_color = col
        self.mark_swatch(self.text_swatches, col)
        o = self.selected()
        if o and not self._restoring:
            self.push_undo()
            o["color"] = col
            self.canvas.queue_draw()

    def on_outline(self, btn):
        o = self.selected()
        if o and not self._restoring:
            self.push_undo()
            o["outline"] = btn.get_active()
            self.canvas.queue_draw()

    def on_caps(self, btn):
        o = self.selected()
        if o and not self._restoring:
            self.push_undo()
            o["caps"] = btn.get_active()
            self.canvas.queue_draw()

    def delete_selected(self):
        o = self.selected()
        if o:
            self.push_undo()
            self.objects.remove(o)
            self.sel_id = None
            self.sync_text_pane()
            self.canvas.queue_draw()

    def on_meme_text(self, which, e):
        if self._restoring:
            return
        self.push_undo(("meme", which))
        setattr(self, f"{which}_text", e.get_text())
        self.canvas.queue_draw()

    # ================= Maus =================
    def to_image(self, x, y):
        s, ox, oy, vx, vy = self._xf
        return (x - ox) / s + vx, (y - oy) / s + vy

    def text_at(self, ix, iy):
        for o in reversed(self.objects):
            b = self._bboxes.get(o.get("id"))
            if o["type"] == "text" and b and b[0] <= ix <= b[0] + b[2] and b[1] <= iy <= b[1] + b[3]:
                return o
        return None

    def crop_handle_at(self, x, y):
        s, ox, oy, vx, vy = self._xf
        dx, dy, dw, dh = self.draft
        corners = {"tl": (dx, dy), "tr": (dx + dw, dy), "bl": (dx, dy + dh), "br": (dx + dw, dy + dh)}
        for name, (cx, cy) in corners.items():
            sx, sy = (cx - vx) * s + ox, (cy - vy) * s + oy
            if abs(sx - x) < 14 and abs(sy - y) < 14:
                return name
        return None

    def on_drag_begin(self, g, x, y):
        if not self.surface:
            return
        self.canvas.grab_focus()
        ix, iy = self.to_image(x, y)
        if self.tool == "pen":
            self.push_undo()
            self.objects.append({"type": "stroke", "id": self.new_id(), "color": self.pen_color,
                                 "width": self.pen_width / self._xf[0], "points": [(ix, iy)]})
            self._drag = ("pen", x, y)
        elif self.tool == "text":
            o = self.text_at(ix, iy)
            if o:
                self.select(o["id"])
                self._drag = ("move", o, o["x"], o["y"], False)
            else:
                if self.selected():
                    self.select(None)
                    return
                self.push_undo()
                o = {"type": "text", "id": self.new_id(), "text": "", "x": ix, "y": iy,
                     "size": self.crop[3] * 0.08, "color": self.text_color, "outline": True, "caps": False}
                self.objects.append(o)
                self.select(o["id"])
                self._undo_key = ("text", o["id"])  # das erste Tippen gehört zum Anlegen
                self.text_entry.grab_focus()
                self._drag = None
        elif self.tool == "crop":
            handle = self.crop_handle_at(x, y)
            dx, dy, dw, dh = self.draft
            if handle:
                anchor = {"tl": (dx + dw, dy + dh), "tr": (dx, dy + dh), "bl": (dx + dw, dy), "br": (dx, dy)}[handle]
                self._drag = ("resize", anchor)
            elif dx <= ix <= dx + dw and dy <= iy <= dy + dh and (dw, dh) != (self.W, self.H):
                self._drag = ("cmove", list(self.draft), ix, iy)
            else:
                self._drag = ("resize", (min(max(ix, 0), self.W), min(max(iy, 0), self.H)))
        self.canvas.queue_draw()

    def on_drag_update(self, g, offx, offy):
        if not self._drag:
            return
        ok, sx, sy = g.get_start_point()
        x, y = sx + offx, sy + offy
        ix, iy = self.to_image(x, y)
        kind = self._drag[0]
        if kind == "pen":
            o = self.objects[-1]
            _, lx, ly = self._drag
            if math.hypot(x - lx, y - ly) >= 1.5:
                o["points"].append((ix, iy))
                self._drag = ("pen", x, y)
        elif kind == "move":
            _, o, x0, y0, moved = self._drag
            sx0, sy0 = self.to_image(sx, sy)
            if not moved:
                self.push_undo()
                self._drag = ("move", o, x0, y0, True)
            o["x"], o["y"] = x0 + ix - sx0, y0 + iy - sy0
        elif kind == "resize":
            ax, ay = self._drag[1]
            px, py = min(max(ix, 0), self.W), min(max(iy, 0), self.H)
            w, h = abs(px - ax), abs(py - ay)
            if self.ratio:
                if h == 0 or w / max(h, 1e-6) > self.ratio:
                    w = h * self.ratio
                else:
                    h = w / self.ratio
            x0 = ax - w if px < ax else ax
            y0 = ay - h if py < ay else ay
            self.draft = [x0, y0, max(w, 1), max(h, 1)]
        elif kind == "cmove":
            _, d0, sx0, sy0 = self._drag
            nx = min(max(d0[0] + ix - sx0, 0), self.W - d0[2])
            ny = min(max(d0[1] + iy - sy0, 0), self.H - d0[3])
            self.draft = [nx, ny, d0[2], d0[3]]
        self.update_crop_info()
        self.canvas.queue_draw()

    def on_drag_end(self, g, offx, offy):
        if self._drag and self._drag[0] == "resize" and self.draft and (self.draft[2] < 8 or self.draft[3] < 8):
            self.draft = list(self.crop)  # nur geklickt: Entwurf nicht kaputt machen
        self._drag = None
        self.update_crop_info()
        self.canvas.queue_draw()

    def on_motion(self, ctrl, x, y):
        if self.tool == "text" and self.surface:
            ix, iy = self.to_image(x, y)
            self.canvas.set_cursor_from_name("move" if self.text_at(ix, iy) else "text")
        elif self.tool == "crop" and self.draft and self.surface:
            h = self.crop_handle_at(x, y)
            cur = {"tl": "nw-resize", "br": "se-resize", "tr": "ne-resize", "bl": "sw-resize"}.get(h, "crosshair")
            self.canvas.set_cursor_from_name(cur)

    def new_id(self):
        self._next_id += 1
        return self._next_id

    def on_key(self, ctrl, keyval, code, mods):
        ctrl_down = bool(mods & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(mods & Gdk.ModifierType.SHIFT_MASK)
        k = Gdk.keyval_name(keyval) or ""
        if ctrl_down and k.lower() == "z":
            self.redo() if shift else self.undo()
        elif ctrl_down and k.lower() == "y":
            self.redo()
        elif ctrl_down and k.lower() == "s":
            self.save()
        elif ctrl_down and shift and k.lower() == "c":
            self.copy_image()
        elif ctrl_down:
            return False
        elif k in ("Return", "KP_Enter") and self.tool == "crop":
            self.apply_crop()
        elif k == "Escape":
            if self.tool == "crop":
                self.draft = list(self.crop)
                self.tool_buttons[self.prev_tool].set_active(True)
            elif self.sel_id:
                self.select(None)
            else:
                self.app.leave_editor()
        elif k == "Delete" and self.sel_id:
            self.delete_selected()
        elif k == "space" and self.media:
            self.toggle_play()
        elif k.lower() in ("c", "b", "t"):
            self.tool_buttons[{"c": "crop", "b": "pen", "t": "text"}[k.lower()]].set_active(True)
        else:
            return False
        return True

    # ================= Zeichnen =================
    def snapshot(self, snap, W, H):
        if not self.surface or W < 40 or H < 40:
            return
        view = self.draft if self.tool == "crop" else self.crop
        vx, vy, vw, vh = (0, 0, self.W, self.H) if self.tool == "crop" else self.crop
        m = 18
        s = min((W - 2 * m) / vw, (H - 2 * m) / vh, 4)
        ox, oy = (W - vw * s) / 2, (H - vh * s) / 2
        self._xf = (s, ox, oy, vx, vy)

        # 1) Bild bzw. Standbild (bei Videos bis das erste Bild läuft)
        self.draw_base(snap.append_cairo(rect(0, 0, W, H)), s, ox, oy, vx, vy, vw, vh)
        # 2) laufendes Video
        if self.media and self.media.get_intrinsic_width() > 0:
            snap.push_clip(rect(ox, oy, vw * s, vh * s))
            snap.save()
            snap.translate(Graphene.Point().init(ox - vx * s, oy - vy * s))
            iw, ih = self.media.get_intrinsic_width(), self.media.get_intrinsic_height()
            if (iw > ih) != (self.W > self.H) and abs(iw - ih) > 2:
                # Hochkant-Handyvideo, das GStreamer ungedreht liefert: so drehen wie ffmpeg beim Export
                rot = self.video_info.get("rotation", -90) or -90
                snap.translate(Graphene.Point().init(self.W * s / 2, self.H * s / 2))
                snap.rotate(-rot)
                snap.translate(Graphene.Point().init(-self.H * s / 2, -self.W * s / 2))
                self.media.snapshot(snap, self.H * s, self.W * s)
            else:
                self.media.snapshot(snap, self.W * s, self.H * s)
            snap.restore()
            snap.pop()
        # 3) Striche, Texte, Zuschnitt
        cr = snap.append_cairo(rect(0, 0, W, H))
        cr.save()
        cr.rectangle(ox, oy, vw * s, vh * s)
        cr.clip()
        cr.translate(ox - vx * s, oy - vy * s)
        cr.scale(s, s)
        self.render_objects(cr, view, screen=True)
        cr.restore()
        if self.tool == "crop":
            self.draw_crop_overlay(cr, s, ox, oy)

    def draw_base(self, cr, s, ox, oy, vx, vy, vw, vh):
        # Schachbrett für transparente Bilder
        cr.save()
        cr.rectangle(ox, oy, vw * s, vh * s)
        cr.clip()
        if not getattr(self, "_checker", None):
            tile = cairo.ImageSurface(cairo.FORMAT_ARGB32, 20, 20)
            c = cairo.Context(tile)
            c.set_source_rgb(0.22, 0.22, 0.22); c.paint()
            c.set_source_rgb(0.30, 0.30, 0.30)
            c.rectangle(0, 0, 10, 10); c.rectangle(10, 10, 10, 10); c.fill()
            self._checker = cairo.SurfacePattern(tile)
            self._checker.set_extend(cairo.EXTEND_REPEAT)
        cr.set_source(self._checker)
        cr.paint()

        # Bild aus dem verkleinerten Zwischenspeicher
        key = round(s, 4)
        if self._cache_key != key:
            cw, ch = max(1, math.ceil(self.W * s)), max(1, math.ceil(self.H * s))
            self._cache = cairo.ImageSurface(cairo.FORMAT_ARGB32, cw, ch)
            c = cairo.Context(self._cache)
            c.scale(s, s)
            c.set_source_surface(self.surface, 0, 0)
            c.get_source().set_filter(cairo.FILTER_GOOD)
            c.paint()
            self._cache_key = key
        cr.set_source_surface(self._cache, ox - vx * s, oy - vy * s)
        cr.paint()
        cr.restore()

    def draw_crop_overlay(self, cr, s, ox, oy):
        dx, dy, dw, dh = self.draft
        X, Y, Wd, Hd = ox + dx * s, oy + dy * s, dw * s, dh * s
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.rectangle(ox, oy, self.W * s, self.H * s)
        cr.rectangle(X, Y, Wd, Hd)
        cr.set_source_rgba(0, 0, 0, 0.6)
        cr.fill()
        cr.set_fill_rule(cairo.FILL_RULE_WINDING)
        cr.set_source_rgba(1, 1, 1, 0.3)
        cr.set_line_width(1)
        for f in (1 / 3, 2 / 3):
            cr.move_to(X + Wd * f, Y); cr.line_to(X + Wd * f, Y + Hd)
            cr.move_to(X, Y + Hd * f); cr.line_to(X + Wd, Y + Hd * f)
        cr.stroke()
        cr.set_source_rgb(*self.accent)
        cr.set_line_width(2)
        cr.rectangle(X, Y, Wd, Hd)
        cr.stroke()
        L = min(18, Wd / 3, Hd / 3)
        cr.set_line_width(5)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        for cx, cy, sx, sy in ((X, Y, 1, 1), (X + Wd, Y, -1, 1), (X, Y + Hd, 1, -1), (X + Wd, Y + Hd, -1, -1)):
            cr.move_to(cx + sx * L, cy); cr.line_to(cx, cy); cr.line_to(cx, cy + sy * L)
        cr.stroke()

    def make_layout(self, cr, text, size, wrap_width=None):
        layout = PangoCairo.create_layout(cr)
        fo = cairo.FontOptions()
        fo.set_hint_metrics(cairo.HINT_METRICS_OFF)
        fo.set_hint_style(cairo.HINT_STYLE_NONE)
        PangoCairo.context_set_font_options(layout.get_context(), fo)
        layout.context_changed()
        desc = Pango.FontDescription.from_string(self.font)
        desc.set_absolute_size(max(1, size) * Pango.SCALE)
        layout.set_font_description(desc)
        layout.set_alignment(Pango.Alignment.CENTER)
        if wrap_width:
            layout.set_width(int(wrap_width * Pango.SCALE))
            layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        layout.set_text(text, -1)
        _, log = layout.get_extents()
        return layout, log.width / Pango.SCALE, log.height / Pango.SCALE

    def paint_text(self, cr, layout, x, y, size, color, outline, alpha=1.0):
        cr.move_to(x, y)
        PangoCairo.layout_path(cr, layout)
        if outline:
            cr.set_source_rgba(0, 0, 0, alpha)
            cr.set_line_width(size * 0.14)
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            cr.stroke_preserve()
        cr.set_source_rgba(*hex_to_rgb(color), alpha)
        cr.fill()

    def render_objects(self, cr, view, screen=False):
        """Zeichnet Striche, Texte und Meme-Text in Bildkoordinaten (Bildschirm und Export)."""
        vx, vy, vw, vh = view
        for o in self.objects:
            if o["type"] == "stroke":
                pts = o["points"]
                cr.set_source_rgb(*hex_to_rgb(o["color"]))
                cr.set_line_width(o["width"])
                cr.set_line_cap(cairo.LINE_CAP_ROUND)
                cr.set_line_join(cairo.LINE_JOIN_ROUND)
                cr.move_to(*pts[0])
                if len(pts) == 1:
                    cr.line_to(*pts[0])
                for i in range(1, len(pts) - 1):  # weich über Mittelpunkte
                    mx, my = (pts[i][0] + pts[i + 1][0]) / 2, (pts[i][1] + pts[i + 1][1]) / 2
                    cr.curve_to(*pts[i], *pts[i], mx, my)
                if len(pts) > 1:
                    cr.line_to(*pts[-1])
                cr.stroke()
            elif o["type"] == "text":
                selected = screen and o["id"] == self.sel_id and self.tool == "text"
                txt = o["text"].upper() if o.get("caps") else o["text"]
                placeholder = not txt.strip()
                if placeholder and not selected:
                    continue
                layout, w, h = self.make_layout(cr, "Text eingeben" if placeholder else txt, o["size"])
                x, y = o["x"] - w / 2, o["y"] - h / 2
                self.paint_text(cr, layout, x, y, o["size"], o["color"], o["outline"], 0.45 if placeholder else 1)
                if screen:
                    self._bboxes[o["id"]] = (x, y, w, h)
                if selected:
                    s = self._xf[0]
                    pad = 6 / s
                    cr.set_source_rgb(*self.accent)
                    cr.set_line_width(2 / s)
                    cr.set_dash([6 / s, 4 / s])
                    cr.rectangle(x - pad, y - pad, w + 2 * pad, h + 2 * pad)
                    cr.stroke()
                    cr.set_dash([])

        # klassischer Meme-Text: oben und unten, zentriert, schrumpft bis er passt
        for txt, top in ((self.top_text, True), (self.bottom_text, False)):
            txt = txt.strip().upper()
            if not txt:
                continue
            size = vh * 0.12
            wrap = vw * 0.92
            while True:
                layout, w, h = self.make_layout(cr, txt, size, wrap)
                if (h <= vh * 0.3 and w <= wrap) or size < vh * 0.03:
                    break
                size *= 0.9
            margin = vh * 0.025
            x = vx + (vw - wrap) / 2
            y = vy + margin if top else vy + vh - margin - h
            self.paint_text(cr, layout, x, y, size, "#ffffff", True)

    # ================= Export =================
    def render_final(self):
        x, y, w, h = (round(v) for v in self.crop)
        out = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(out)
        cr.translate(-x, -y)
        cr.set_source_surface(self.surface, 0, 0)
        cr.paint()
        self.render_objects(cr, (x, y, w, h))
        buf = io.BytesIO()
        out.write_to_png(buf)
        return buf.getvalue()

    def finish_editing(self):
        if self.tool == "crop":
            self.apply_crop()
        self.select(None)

    def save(self):
        if not self.surface or self.exporting:
            return
        self.finish_editing()
        folder = os.path.expanduser("~/Videos" if self.video else "~/Pictures")
        os.makedirs(folder, exist_ok=True)
        ext = "mp4" if self.video else "png"
        dlg = Gtk.FileDialog(title="Meme speichern",
                             initial_name=f"meme-{datetime.datetime.now():%Y-%m-%d-%H%M%S}.{ext}",
                             initial_folder=Gio.File.new_for_path(folder))
        dlg.save(self.app.win, None, self.on_save_done)

    def on_save_done(self, dlg, res):
        try:
            f = dlg.save_finish(res)
        except GLib.Error:
            return
        path = f.get_path()
        if self.video:
            if not path.lower().endswith((".mp4", ".gif")):
                path += ".mp4"
            self.export_video(path, lambda ok: ok and self.app.notify(f"Gespeichert: {os.path.basename(path)}"))
            return
        png = self.render_final()
        try:
            if path.lower().endswith((".jpg", ".jpeg")):
                Image.open(io.BytesIO(png)).convert("RGB").save(path, "JPEG", quality=92)
            elif path.lower().endswith(".webp"):
                Image.open(io.BytesIO(png)).save(path, "WEBP", quality=92)
            else:
                if not path.lower().endswith(".png"):
                    path += ".png"
                with open(path, "wb") as fh:
                    fh.write(png)
        except OSError as e:
            self.app.notify(f"Speichern fehlgeschlagen: {e.strerror}")
            return
        self.app.notify(f"Gespeichert: {os.path.basename(path)}")

    def copy_image(self):
        if not self.surface or self.exporting:
            return
        self.finish_editing()
        if self.video:
            folder = os.path.expanduser("~/.cache/scourge")
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, f"meme-{datetime.datetime.now():%Y-%m-%d-%H%M%S}.mp4")

            def done(ok):
                if ok:
                    f = Gio.File.new_for_path(path)
                    files = Gdk.ContentProvider.new_for_value(Gdk.FileList.new_from_list([f]))
                    uris = Gdk.ContentProvider.new_for_bytes("text/uri-list", GLib.Bytes.new((f.get_uri() + "\r\n").encode()))
                    self.app.win.get_clipboard().set_content(Gdk.ContentProvider.new_union([files, uris]))
                    self.app.notify("Video kopiert, einfach einfügen")
            self.export_video(path, done)
            return
        prov = Gdk.ContentProvider.new_for_bytes("image/png", GLib.Bytes.new(self.render_final()))
        self.app.win.get_clipboard().set_content(prov)
        self.app.notify("Meme kopiert")

    # ================= Video-Export (ffmpeg) =================
    def export_video(self, out, done):
        """Brennt Striche, Texte und Meme-Text als Ebene ins Video und schneidet zu. MP4 (mit Ton) oder GIF."""
        import subprocess, tempfile, threading
        x, y, w, h = (round(v) for v in self.crop)
        w, h = w - w % 2, h - h % 2  # H.264 braucht gerade Maße
        layer = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.W, self.H)
        self.render_objects(cairo.Context(layer), (x, y, w, h))
        fd, ov = tempfile.mkstemp(prefix="scourge-", suffix=".png")
        os.close(fd)
        layer.write_to_png(ov)
        base = f"[0:v][1:v]overlay=0:0,crop={w}:{h}:{x}:{y}"
        if out.lower().endswith(".gif"):
            fc = (base + ",fps=15,scale='min(720,iw)':-2:flags=lanczos,split[a][b];"
                  "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=sierra2_4a[v]")
            args = ["-filter_complex", fc, "-map", "[v]", "-loop", "0"]
        else:
            args = ["-filter_complex", base + ",format=yuv420p[v]", "-map", "[v]", "-map", "0:a?",
                    "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-c:a", "aac", "-b:a", "160k",
                    "-movflags", "+faststart"]
        tmp_out = out + ".part" + os.path.splitext(out)[1]
        cmd = ["ffmpeg", "-y", "-v", "error", "-nostats", "-progress", "pipe:1",
               "-i", self.video, "-i", ov] + args + [tmp_out]
        dur = float(self.video_info.get("duration") or 0)
        self.exporting = True
        for b in (self.save_btn, self.copy_btn):
            b.set_sensitive(False)
        self.app.notify("Video wird erstellt …")

        def work():
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            last = -1
            for line in p.stdout:
                if line.startswith("out_time_us=") and dur:
                    try:
                        pct = min(99, int(int(line.split("=")[1]) / 1e6 / dur * 100))
                    except ValueError:
                        continue
                    if pct != last:
                        last = pct
                        GLib.idle_add(self.app.notify, f"Video wird erstellt … {pct} %")
            err = p.stderr.read()
            ok = p.wait() == 0
            os.unlink(ov)
            if ok:
                os.replace(tmp_out, out)
            elif os.path.exists(tmp_out):
                os.unlink(tmp_out)
            GLib.idle_add(finish, ok, err)

        def finish(ok, err):
            self.exporting = None
            for b in (self.save_btn, self.copy_btn):
                b.set_sensitive(True)
            if not ok:
                self.app.notify("Export fehlgeschlagen: " + (err.strip().splitlines() or ["unbekannter Fehler"])[-1][:90])
            done(ok)
            return False
        threading.Thread(target=work, daemon=True).start()
