# Bilder komprimieren: viele Bilder auf einmal kleiner machen – per Qualität oder Zielgröße,
# optional verkleinern, Format wechseln (JPEG/WebP/AVIF/PNG) und Metadaten entfernen.
# Läuft komplett lokal mit Pillow. Originale werden nie überschrieben.
import concurrent.futures, io, os, threading

import tools

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".avif", ".tif", ".tiff", ".bmp", ".gif", ".heic", ".heif")
FORMATS = [("same", "wie original"), ("jpeg", "JPEG"), ("webp", "WebP"), ("avif", "AVIF"), ("png", "PNG")]
EXT = {"jpeg": ".jpg", "webp": ".webp", "avif": ".avif", "png": ".png"}
SIZES = [(0, "original"), (3840, "3840 px"), (2560, "2560 px"), (1920, "1920 px"), (1280, "1280 px"), (800, "800 px")]
SCALES = [(100, "100 %"), (75, "75 %"), (50, "50 %"), (25, "25 %")]
DEFAULT_NAME = "{name}-klein"

COMPRESS_CSS = """
.cmp-drop { border: 1px dashed alpha(@primary, 0.5); border-radius: 6px; padding: 18px; }
.cmp-drop.drag-over { background-color: alpha(@primary, 0.12); border-color: @primary; }
.cmp-good { color: @tertiary; font-weight: bold; }
.cmp-bad { color: alpha(@foreground, 0.5); }
.cmp-prev { border: 1px solid alpha(@primary, 0.3); border-radius: 4px; }
"""


def _pics():
    try:
        import settings_menu
        return settings_menu.folder("pic_dir", "~/Pictures")
    except Exception:
        return os.path.expanduser("~/Pictures")


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024


def target_format(path, fmt):
    if fmt != "same":
        return fmt
    ext = os.path.splitext(path)[1].lower()
    return {".jpg": "jpeg", ".jpeg": "jpeg", ".webp": "webp", ".avif": "avif", ".png": "png"}.get(ext, "jpeg")


def load(path):
    """Bild laden, Drehung aus EXIF anwenden. -> (Bild, EXIF-Bytes oder None, ICC-Profil, animiert?)"""
    from PIL import Image, ImageOps
    img = Image.open(path)
    animated = getattr(img, "is_animated", False) and getattr(img, "n_frames", 1) > 1
    icc = img.info.get("icc_profile")
    img = ImageOps.exif_transpose(img)   # Drehung einrechnen; das Ergebnis hat keine Drehungs-Angabe mehr
    img.load()
    exif = None
    try:
        ex = img.getexif()
        if ex:
            exif = ex.tobytes()
    except Exception:
        pass
    return img, exif, icc, animated


def resize(img, edge, scale=100):
    from PIL import Image
    if scale and scale < 100:
        img = img.resize((max(1, round(img.width * scale / 100)), max(1, round(img.height * scale / 100))), Image.LANCZOS)
    if edge and max(img.size) > edge:
        f = edge / max(img.size)
        img = img.resize((max(1, round(img.width * f)), max(1, round(img.height * f))), Image.LANCZOS)
    return img


def encode(img, fmt, quality, strip, exif, icc, png_colors=0):
    """Ein Bild in Bytes kodieren."""
    from PIL import Image
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    if fmt == "jpeg":
        if has_alpha:                    # JPEG kann keine Transparenz → auf Weiß legen
            rgba = img.convert("RGBA")
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.getchannel("A"))
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
    elif img.mode not in ("RGB", "RGBA", "L", "LA") or (fmt == "png" and png_colors and img.mode == "P"):
        img = img.convert("RGBA" if has_alpha else "RGB")
    kw = {}
    if not strip:
        if exif:
            kw["exif"] = exif
        if icc:
            kw["icc_profile"] = icc
    elif icc and fmt != "png":           # Farbprofil verrät nichts, hält aber die Farben richtig
        kw["icc_profile"] = icc
    buf = io.BytesIO()
    if fmt == "jpeg":
        img.save(buf, "JPEG", quality=int(quality), optimize=True, progressive=True,
                 subsampling=0 if quality >= 90 else 2, **kw)
    elif fmt == "webp":
        img.save(buf, "WEBP", quality=int(quality), method=6, **kw)
    elif fmt == "avif":
        img.save(buf, "AVIF", quality=int(quality), speed=6, **kw)
    else:
        if png_colors:
            from PIL import features
            method = Image.Quantize.LIBIMAGEQUANT if features.check("libimagequant") else Image.Quantize.FASTOCTREE
            img = img.quantize(colors=png_colors, method=method, dither=Image.Dither.FLOYDSTEINBERG)
        img.save(buf, "PNG", optimize=True, **kw)
    return buf.getvalue()


def compress(path, opts):
    """-> dict(data, fmt, size_in, size_out, dims, note). opts: fmt, mode, quality, target_kb, edge, strip, png_lossy."""
    size_in = os.path.getsize(path)
    img, exif, icc, animated = load(path)
    fmt = target_format(path, opts["fmt"])
    img = resize(img, opts["edge"], opts.get("scale", 100))
    note = "nur erstes Bild der Animation" if animated else ""

    if opts["mode"] == "quality":
        q = opts["quality"]
        colors = (256 if q >= 70 else 128 if q >= 40 else 64) if fmt == "png" and opts["png_lossy"] else 0
        data = encode(img, fmt, q, opts["strip"], exif, icc, colors)
    else:                                # Zielgröße: Qualität per Halbierung suchen, notfalls verkleinern
        goal = opts["target_kb"] * 1024
        data = None
        for _ in range(8):
            if fmt == "png":
                steps = [0] + ([256, 128, 64, 32] if opts["png_lossy"] else [])
                for c in steps:
                    data = encode(img, fmt, 0, opts["strip"], exif, icc, c)
                    if len(data) <= goal:
                        break
            else:
                lo, hi, best = 5, 95, None
                while lo <= hi:
                    mid = (lo + hi) // 2
                    d = encode(img, fmt, mid, opts["strip"], exif, icc)
                    if len(d) <= goal:
                        best, lo = d, mid + 1
                    else:
                        hi = mid - 1
                        data = d
                data = best or data
            if len(data) <= goal or min(img.size) < 64:
                break
            img = img.resize((max(1, int(img.width * 0.8)), max(1, int(img.height * 0.8))))
            note = f"verkleinert auf {img.width}×{img.height}, sonst nicht erreichbar"
        if len(data) > goal:
            note = "Zielgröße nicht erreichbar"
    return {"data": data, "fmt": fmt, "size_in": size_in, "size_out": len(data), "dims": img.size, "note": note}


def safe_name(text):
    """Dateiname ohne Pfadtrenner und Sonderzeichen, die Dateisysteme nicht mögen."""
    out = "".join("_" if c in '/\\:*?"<>|' or ord(c) < 32 else c for c in text).strip(" .")
    return out or "bild"


def out_path(path, fmt, folder, template=DEFAULT_NAME, nr=1, dims=None):
    """Zieldatei nach Vorlage: {name} Originalname, {nr} laufende Nummer, {datum}, {breite}, {höhe}. Nie überschreiben."""
    import datetime
    base = os.path.splitext(os.path.basename(path))[0]
    w, h = dims or (0, 0)
    try:
        name = (template or DEFAULT_NAME).format(name=base, nr=f"{nr:03d}", datum=f"{datetime.date.today():%Y-%m-%d}",
                                                 breite=w, **{"höhe": h})
    except (KeyError, IndexError, ValueError):
        name = base + "-klein"
    if name == base and not folder:               # sonst würde das Original ersetzt
        name = base + "-klein"
    name = safe_name(name)
    folder = folder or os.path.dirname(path)
    p = os.path.join(folder, f"{name}{EXT[fmt]}")
    i = 2
    while os.path.exists(p) or os.path.abspath(p) == os.path.abspath(path):
        p = os.path.join(folder, f"{name}-{i}{EXT[fmt]}")
        i += 1
    return p


class CompressPage(tools.ToolPage):
    title = "Bilder komprimieren"
    subtitle = "Viele Bilder auf einmal kleiner machen · WebP, AVIF, Zielgröße, Metadaten weg"
    privacy = "Läuft komplett auf deinem Rechner. Originale bleiben unverändert, neue Dateien heißen „…-klein“."

    def build(self):
        self.files = []
        self.results = {}
        self.out_folder = None
        self.cancel = threading.Event()
        self.preview_path = None
        self._prev_timer = 0
        self._prev_token = 0
        self.opts = {"fmt": "same", "mode": "quality", "quality": 80, "target_kb": 500, "edge": 0, "scale": 100,
                     "strip": True, "png_lossy": True}
        self.name_tpl = DEFAULT_NAME

        # --- Dateien ---
        c = self.card("bilder")
        drop = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        drop.add_css_class("cmp-drop")
        row = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        row.append(self.button("󰋩  bilder wählen", self.pick_files))
        row.append(self.button("󰉋  ordner wählen", self.pick_folder))
        self.clear_btn = self.button("󰃢  leeren", self.clear_files, small=True)
        self.clear_btn.set_visible(False)
        row.append(self.clear_btn)
        drop.append(row)
        self.count_lbl = Gtk.Label(label="…oder Bilder hierher ziehen", xalign=0.5)
        self.count_lbl.add_css_class("note")
        drop.append(self.count_lbl)
        c.append(drop)
        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        c.append(self.list_box)

        target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        target.connect("enter", lambda *_: (drop.add_css_class("drag-over"), Gdk.DragAction.COPY)[1])
        target.connect("leave", lambda *_: drop.remove_css_class("drag-over"))
        target.connect("drop", lambda t, v, x, y: (drop.remove_css_class("drag-over"),
                                                   self.add_paths([f.get_path() for f in v.get_files() if f.get_path()]),
                                                   True)[-1])
        self.add_controller(target)

        # --- Einstellungen ---
        c = self.settings_card = self.card("einstellungen")
        self.label(c, "format")
        self.seg(c, FORMATS, "same", lambda k: self.set_opt("fmt", k))
        self.label(c, "ziel")
        self.seg(c, [("quality", "qualität"), ("target", "zielgröße je bild")], "quality", self.set_mode)
        self.q_box, self.q_scale = self.slider(c, "qualität", 10, 100, 80, lambda v: self.set_opt("quality", int(v)))
        self.t_box = Gtk.Box(spacing=8)
        l = Gtk.Label(label="höchstens", xalign=0)
        l.add_css_class("key")
        self.t_spin = Gtk.SpinButton.new_with_range(20, 50000, 50)
        self.t_spin.add_css_class("pb")
        self.t_spin.set_value(500)
        self.t_spin.connect("value-changed", lambda s: self.set_opt("target_kb", int(s.get_value())))
        kb = Gtk.Label(label="KB")
        kb.add_css_class("key")
        self.t_box.append(l); self.t_box.append(self.t_spin); self.t_box.append(kb)
        self.t_box.set_visible(False)
        c.append(self.t_box)
        self.label(c, "längste seite")
        self.seg(c, SIZES, 0, lambda k: self.set_opt("edge", k))
        self.label(c, "oder verkleinern auf")
        self.seg(c, SCALES, 100, lambda k: self.set_opt("scale", k))
        self.switch_row(c, "metadaten entfernen (GPS, Kamera, Datum …)", True, lambda v: self.set_opt("strip", v))
        self.switch_row(c, "PNG: farben reduzieren (verlustbehaftet, oft 60–80 % kleiner)", True,
                        lambda v: self.set_opt("png_lossy", v))
        self.label(c, "speichern")
        self.save_btns = self.seg(c, [("next", "neben dem original"), ("folder", "in ordner …")], "next", self.set_save)
        self.folder_lbl = Gtk.Label(xalign=0, ellipsize=1)
        self.folder_lbl.add_css_class("note")
        self.folder_lbl.set_visible(False)
        c.append(self.folder_lbl)
        self.label(c, "dateiname")
        name_row = Gtk.Box(spacing=8)
        self.name_entry = Gtk.Entry(text=DEFAULT_NAME, hexpand=True,
                                    tooltip_text="Platzhalter: {name} Originalname · {nr} laufende Nummer · {datum} · {breite} · {höhe}")
        self.name_entry.add_css_class("pb")
        self.name_entry.connect("changed", lambda e: self.set_name(e.get_text()))
        name_row.append(self.name_entry)
        for label, tpl in (("-klein", "{name}-klein"), ("nummeriert", "bild-{nr}"), ("mit Datum", "{name}-{datum}")):
            b = self.button(label, lambda t=tpl: self.name_entry.set_text(t), small=True)
            name_row.append(b)
        c.append(name_row)
        self.name_lbl = Gtk.Label(xalign=0, ellipsize=1)
        self.name_lbl.add_css_class("note")
        c.append(self.name_lbl)
        self.set_name(DEFAULT_NAME)

        # --- Vorschau ---
        self.prev_card = self.card("vorschau")
        self.prev_pic = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, height_request=300)
        self.prev_pic.add_css_class("cmp-prev")
        self.prev_lbl = Gtk.Label(xalign=0, wrap=True)
        self.prev_lbl.add_css_class("key")
        self.prev_card.append(self.prev_pic)
        self.prev_card.append(self.prev_lbl)
        self.prev_card.set_visible(False)

        # --- Start ---
        row = Gtk.Box(spacing=6)
        self.go_btn = self.button("󰁄  komprimieren", self.start)
        self.go_btn.set_sensitive(False)
        self.stop_btn = self.button("󰓛  stopp", lambda: self.cancel.set())
        self.stop_btn.set_visible(False)
        self.open_btn = self.button("󰉋  ordner öffnen", self.open_folder)
        self.open_btn.set_visible(False)
        self.sum_lbl = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        self.sum_lbl.add_css_class("key")
        row.append(self.go_btn); row.append(self.stop_btn); row.append(self.open_btn); row.append(self.sum_lbl)
        self.box.append(row)
        self.box.reorder_child_after(row, self.settings_card)     # Knopf direkt unter die Einstellungen

    # --- kleine Bausteine (wie im Studio) ---
    def label(self, parent, text):
        l = Gtk.Label(label=text, xalign=0)
        l.add_css_class("key")
        parent.append(l)

    def seg(self, parent, options, active, cb):
        fb = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=6, min_children_per_line=2,
                         column_spacing=4, row_spacing=4)
        first, btns = None, {}
        for key, label in options:
            b = Gtk.ToggleButton(label=label)
            b.add_css_class("pb"); b.add_css_class("seg")
            if first:
                b.set_group(first)
            first = first or b
            b.set_active(key == active)
            b.connect("toggled", lambda b, key=key: b.get_active() and cb(key))
            fb.append(b)
            btns[key] = b
        parent.append(fb)
        return btns

    def slider(self, parent, name, lo, hi, val, cb):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head = Gtk.Box()
        n = Gtk.Label(label=name, xalign=0, hexpand=True)
        n.add_css_class("key")
        v = Gtk.Label(label=str(val))
        v.add_css_class("studio-val")
        head.append(n); head.append(v)
        s = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, 1)
        s.set_draw_value(False)
        s.add_css_class("pb")
        s.set_value(val)
        s.connect("value-changed", lambda s: (v.set_label(str(int(s.get_value()))), cb(s.get_value())))
        import chrome
        chrome.reset_on_dblclick(n, s, val)
        box.append(head); box.append(s)
        parent.append(box)
        return box, s

    def switch_row(self, parent, name, val, cb):
        row = Gtk.Box(spacing=8)
        l = Gtk.Label(label=name, xalign=0, hexpand=True, wrap=True)
        l.add_css_class("key")
        sw = Gtk.Switch(active=val, valign=Gtk.Align.CENTER)
        sw.connect("notify::active", lambda sw, _: cb(sw.get_active()))
        row.append(l); row.append(sw)
        parent.append(row)
        return sw

    # --- Einstellungen ---
    def set_opt(self, key, val):
        self.opts[key] = val
        self.schedule_preview()

    def set_mode(self, mode):
        self.q_box.set_visible(mode == "quality")
        self.t_box.set_visible(mode == "target")
        self.set_opt("mode", mode)

    def set_name(self, text):
        self.name_tpl = text.strip() or DEFAULT_NAME
        ex = out_path(self.files[0] if self.files else "/tmp/beispiel.jpg", "jpeg", "/nonexistent-preview",
                      self.name_tpl, 1, (1920, 1080))
        self.name_lbl.set_label("z. B. " + os.path.basename(ex))

    def set_save(self, key):
        if key == "next":
            self.out_folder = None
            self.folder_lbl.set_visible(False)
            return
        dlg = Gtk.FileDialog(title="Zielordner für die kleinen Bilder")

        def done(d, res):
            try:
                self.out_folder = d.select_folder_finish(res).get_path()
            except GLib.Error:
                self.save_btns["next"].set_active(True)
                return
            self.folder_lbl.set_label("→ " + self.out_folder.replace(os.path.expanduser("~"), "~", 1))
            self.folder_lbl.set_visible(True)
        dlg.select_folder(self.tab.win, None, done)

    # --- Dateien ---
    def pick_files(self):
        f = Gtk.FileFilter(name="Bilder")
        f.add_mime_type("image/*")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(f)
        dlg = Gtk.FileDialog(title="Bilder zum Komprimieren", filters=filters,
                             initial_folder=Gio.File.new_for_path(_pics()))

        def done(d, res):
            try:
                files = d.open_multiple_finish(res)
            except GLib.Error:
                return
            self.add_paths([files.get_item(i).get_path() for i in range(files.get_n_items())])
        dlg.open_multiple(self.tab.win, None, done)

    def pick_folder(self):
        dlg = Gtk.FileDialog(title="Ordner mit Bildern", initial_folder=Gio.File.new_for_path(_pics()))

        def done(d, res):
            try:
                self.add_paths([d.select_folder_finish(res).get_path()])
            except GLib.Error:
                pass
        dlg.select_folder(self.tab.win, None, done)

    def add_paths(self, paths):
        found = []
        for p in paths:
            if p and os.path.isdir(p):
                for root, dirs, names in os.walk(p):
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    found += [os.path.join(root, n) for n in sorted(names)
                              if n.lower().endswith(IMG_EXT) and "-klein" not in n]
            elif p and p.lower().endswith(IMG_EXT):
                found.append(p)
        new = [p for p in found if p not in self.files][:2000 - len(self.files)]
        if not new:
            if paths:
                self.tab.notify("Keine (neuen) Bilder gefunden")
            return
        self.files += new
        if self.preview_path is None:
            self.preview_path = self.files[0]
        self.refresh_list()
        self.schedule_preview()

    def remove_file(self, path):
        if path in self.files:
            self.files.remove(path)
            self.results.pop(path, None)
        if self.preview_path == path:
            self.preview_path = self.files[0] if self.files else None
            if self.preview_path:
                self.schedule_preview(0)
            else:
                self.prev_card.set_visible(False)
        self.refresh_list()

    def clear_files(self):
        self.files, self.results, self.preview_path = [], {}, None
        self.prev_card.set_visible(False)
        self.sum_lbl.set_label("")
        self.open_btn.set_visible(False)
        self.refresh_list()

    def refresh_list(self):
        self.clear(self.list_box)
        n = len(self.files)
        total = sum(os.path.getsize(p) for p in self.files if os.path.exists(p))
        self.count_lbl.set_label(f"{n} bild{'er' if n != 1 else ''} · zusammen {human(total)}" if n
                                 else "…oder Bilder hierher ziehen")
        self.clear_btn.set_visible(bool(n))
        self.go_btn.set_sensitive(bool(n))
        for p in self.files[:200]:
            b = Gtk.Button()
            b.add_css_class("recent")
            row = Gtk.Box(spacing=10)
            name = Gtk.Label(label=os.path.basename(p), xalign=0, hexpand=True, ellipsize=3)
            name.add_css_class("finding-title")
            row.append(name)
            r = self.results.get(p)
            if r is None:
                info = Gtk.Label(label=human(os.path.getsize(p)) if os.path.exists(p) else "?")
                info.add_css_class("cmp-bad")
            elif isinstance(r, str):
                info = Gtk.Label(label=r)
                info.add_css_class("cmp-bad")
            else:
                pct = 100 - r["size_out"] * 100 / max(1, r["size_in"])
                info = Gtk.Label(label=f"{human(r['size_in'])} → {human(r['size_out'])}  −{pct:.0f} %"
                                 + (f"  ({r['note']})" if r["note"] else ""))
                info.add_css_class("cmp-good")
            row.append(info)
            rm = Gtk.Button(label="✕", tooltip_text="Aus der Liste nehmen", valign=Gtk.Align.CENTER)
            rm.add_css_class("pb"); rm.add_css_class("small")
            rm.connect("clicked", lambda *_, p=p: self.remove_file(p))
            row.append(rm)
            b.set_child(row)
            b.set_tooltip_text("Vorschau mit den aktuellen Einstellungen")
            b.connect("clicked", lambda *_, p=p: self.show_preview(p))
            self.list_box.append(b)
        if n > 200:
            l = Gtk.Label(label=f"… und {n - 200} weitere", xalign=0)
            l.add_css_class("note")
            self.list_box.append(l)

    # --- Vorschau: aktuelles Bild mit den aktuellen Einstellungen ---
    def show_preview(self, path):
        self.preview_path = path
        self.schedule_preview(0)

    def schedule_preview(self, delay=300):
        if not self.preview_path:
            return
        if self._prev_timer:
            GLib.source_remove(self._prev_timer)
        self._prev_timer = GLib.timeout_add(delay, self._run_preview)

    def _run_preview(self):
        self._prev_timer = 0
        self._prev_token += 1
        token, path, opts = self._prev_token, self.preview_path, dict(self.opts)
        self.prev_card.set_visible(True)
        self.prev_card.set_title("vorschau · " + os.path.basename(path))
        self.prev_lbl.set_label("rechne …")

        def work():
            from PIL import Image
            r = compress(path, opts)
            img = Image.open(io.BytesIO(r["data"]))
            img.thumbnail((1600, 1600))
            img = img.convert("RGBA")
            return r, img

        def done(res):
            if token != self._prev_token:
                return
            if isinstance(res, Exception):
                self.prev_lbl.set_label(f"geht nicht: {res}")
                return
            r, img = res
            tex = Gdk.MemoryTexture.new(img.width, img.height, Gdk.MemoryFormat.R8G8B8A8,
                                        GLib.Bytes.new(img.tobytes()), img.width * 4)
            self.prev_pic.set_paintable(tex)
            pct = 100 - r["size_out"] * 100 / max(1, r["size_in"])
            w, h = r["dims"]
            self.prev_lbl.set_label(f"{human(r['size_in'])} → {human(r['size_out'])}  (−{pct:.0f} %)  ·  "
                                    f"{r['fmt'].upper()}  ·  {w}×{h}" + (f"  ·  {r['note']}" if r["note"] else ""))
        self.run(work, done)
        return False

    # --- Los ---
    def start(self):
        if not self.files:
            return
        self.cancel.clear()
        self.go_btn.set_visible(False)
        self.stop_btn.set_visible(True)
        self.open_btn.set_visible(False)
        files, opts, folder, tpl = list(self.files), dict(self.opts), self.out_folder, self.name_tpl
        numbers = {p: i for i, p in enumerate(files, 1)}
        state = {"n": 0, "in": 0, "out": 0, "written": [], "skipped": 0, "errors": 0}
        self.sum_lbl.set_label(f"0 von {len(files)} …")

        name_lock = threading.Lock()

        def one(p):
            if self.cancel.is_set():
                return p, None
            try:
                r = compress(p, opts)
            except Exception as e:
                return p, f"fehler: {e.__class__.__name__}"
            if r["size_out"] >= r["size_in"] and r["fmt"] == target_format(p, "same") and not opts["edge"] \
                    and opts.get("scale", 100) >= 100:
                return p, "schon klein – übersprungen"
            with name_lock:
                dst = out_path(p, r["fmt"], folder, tpl, numbers[p], r["dims"])
                open(dst, "wb").close()               # Name sofort belegen, sonst nehmen parallele Läufe denselben
            with open(dst, "wb") as fh:
                fh.write(r["data"])
            r["dst"] = dst
            del r["data"]
            return p, r

        def work():
            with concurrent.futures.ThreadPoolExecutor(min(4, os.cpu_count() or 2)) as ex:
                for p, r in ex.map(one, files):
                    if r is None:
                        continue
                    self.results[p] = r
                    state["n"] += 1
                    if isinstance(r, dict):
                        state["in"] += r["size_in"]; state["out"] += r["size_out"]
                        state["written"].append(r["dst"])
                    elif r.startswith("fehler"):
                        state["errors"] += 1
                    else:
                        state["skipped"] += 1
                    GLib.idle_add(lambda n=state["n"]: (self.sum_lbl.set_label(f"{n} von {len(files)} …"), False)[-1])
            return state

        def done(st):
            self.go_btn.set_visible(True)
            self.stop_btn.set_visible(False)
            self.refresh_list()
            if isinstance(st, Exception):
                self.sum_lbl.set_label(f"abgebrochen: {st}")
                return
            self.last_written = st["written"]
            self.open_btn.set_visible(bool(st["written"]))
            parts = [f"{len(st['written'])} gespeichert"]
            if st["in"]:
                parts.append(f"{human(st['in'])} → {human(st['out'])} (−{100 - st['out'] * 100 / st['in']:.0f} %)")
            if st["skipped"]:
                parts.append(f"{st['skipped']} schon klein")
            if st["errors"]:
                parts.append(f"{st['errors']} fehler")
            if self.cancel.is_set():
                parts.append("gestoppt")
            self.sum_lbl.set_label(" · ".join(parts))
            self.tab.notify("Komprimieren fertig: " + parts[0])
        self.run(work, done)

    def open_folder(self):
        if getattr(self, "last_written", None):
            Gtk.FileLauncher(file=Gio.File.new_for_path(self.last_written[0])).open_containing_folder(
                self.tab.win, None, None)

    def shutdown(self):
        self.cancel.set()
