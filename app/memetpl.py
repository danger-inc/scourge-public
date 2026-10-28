# Eigene Meme-Vorlagen: Bilder, die man immer wieder braucht. Liegen als PNG in ~/.config/scourge/meme-vorlagen
# (ohne Text und Zeichnungen, nur das Bild). Die Seite zeigt sie als Kacheln; ein Klick öffnet den Meme-Editor damit.
import os, re, time

import tools

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

DIR = os.path.expanduser("~/.config/scourge/meme-vorlagen")
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")

TPL_CSS = """
button.tpl { background: none; box-shadow: none; border: 2px solid alpha(@color9, 0.3); border-radius: 12px; padding: 6px; }
button.tpl:hover { border-color: @color9; background-color: alpha(@color9, 0.12); }
button.tpl-x { min-width: 0; min-height: 0; padding: 0 6px; border-radius: 8px; background-color: alpha(@background, 0.8);
    border: none; box-shadow: none; color: alpha(@foreground, 0.7); }
button.tpl-x:hover { color: @foreground; background-color: alpha(@color9, 0.4); }
"""


def safe(name):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .")
    return name or "vorlage"


def listing():
    """[(Name, Pfad)], neueste zuerst."""
    try:
        files = [os.path.join(DIR, f) for f in os.listdir(DIR) if f.lower().endswith(IMG_EXT)]
    except OSError:
        return []
    files.sort(key=os.path.getmtime, reverse=True)
    return [(os.path.splitext(os.path.basename(p))[0], p) for p in files]


def unique_path(name):
    os.makedirs(DIR, exist_ok=True)
    base = safe(name)
    p = os.path.join(DIR, base + ".png")
    i = 2
    while os.path.exists(p):
        p = os.path.join(DIR, f"{base}-{i}.png")
        i += 1
    return p


def save_image(img, name):
    """PIL-Bild als Vorlage ablegen → Pfad."""
    p = unique_path(name)
    img.convert("RGBA").save(p, "PNG")
    return p


def import_file(path):
    from PIL import Image
    with Image.open(path) as im:
        im.seek(0)
        return save_image(im.copy(), os.path.splitext(os.path.basename(path))[0])


class TemplatePage(tools.ToolPage):
    title = "Meme-Vorlagen"
    subtitle = "Deine eigenen Bilder für Memes · Klick = im Editor öffnen"
    privacy = "Liegt nur auf deinem Rechner (~/.config/scourge/meme-vorlagen)."

    def build(self):
        row = Gtk.Box(spacing=8)
        row.append(self.button("󰐕  bilder hinzufügen", self.add_files))
        self.count = Gtk.Label(label="", xalign=0, hexpand=True)
        self.count.add_css_class("note")
        row.append(self.count)
        self.box.append(row)
        self.grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=6, min_children_per_line=2,
                                column_spacing=10, row_spacing=10, homogeneous=True)
        self.box.append(self.grid)
        self.empty = Gtk.Label(label="Noch keine Vorlagen. Im Meme-Editor unter „Fertig?“ gibt es „Als Vorlage“, oder "
                                     "oben Bilder hinzufügen – auch per Ziehen auf diese Seite.", xalign=0, wrap=True)
        self.empty.add_css_class("note")
        self.box.append(self.empty)
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", lambda t, v, x, y: (self.add_paths([f.get_path() for f in v.get_files() if f.get_path()]), True)[-1])
        self.add_controller(drop)
        self.refresh()

    def refresh(self):
        self.clear(self.grid)
        items = listing()
        self.count.set_label(f"{len(items)} Vorlage{'n' if len(items) != 1 else ''}" if items else "")
        self.empty.set_visible(not items)
        for name, path in items:
            ov = Gtk.Overlay()
            b = Gtk.Button(tooltip_text=name)
            b.add_css_class("tpl")
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            try:
                pic = Gtk.Picture.new_for_filename(path)
            except Exception:
                pic = Gtk.Picture()
            pic.set_content_fit(Gtk.ContentFit.COVER)
            pic.set_size_request(150, 110)
            col.append(pic)
            lbl = Gtk.Label(label=name, ellipsize=3, max_width_chars=16)
            lbl.add_css_class("note")
            col.append(lbl)
            b.set_child(col)
            b.connect("clicked", lambda *_, p=path: self.use(p))
            ov.set_child(b)
            x = Gtk.Button(label="✕", tooltip_text="Vorlage löschen", halign=Gtk.Align.END, valign=Gtk.Align.START,
                           margin_top=4, margin_end=4)
            x.add_css_class("tpl-x")
            x.connect("clicked", lambda *_, p=path: self.delete(p))
            ov.add_overlay(x)
            self.grid.append(ov)

    def use(self, path):
        from PIL import Image
        try:
            img = Image.open(path)
            img.load()
        except Exception as e:                     # noqa: BLE001
            return self.tab.notify(f"Vorlage lässt sich nicht öffnen: {e.__class__.__name__}")
        self.tab.open_in_editor(img)

    def delete(self, path):
        try:
            os.remove(path)
        except OSError:
            pass
        self.refresh()

    def add_files(self):
        f = Gtk.FileFilter(name="Bilder")
        f.add_mime_type("image/*")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(f)
        dlg = Gtk.FileDialog(title="Bilder als Vorlagen", filters=filters)

        def done(d, res):
            try:
                files = d.open_multiple_finish(res)
            except GLib.Error:
                return
            self.add_paths([files.get_item(i).get_path() for i in range(files.get_n_items())])
        dlg.open_multiple(self.tab.win, None, done)

    def add_paths(self, paths):
        n = 0
        for p in paths:
            if p and p.lower().endswith(IMG_EXT):
                try:
                    import_file(p)
                    n += 1
                except Exception:                 # noqa: BLE001
                    pass
        if n:
            self.tab.notify(f"{n} Vorlage{'n' if n != 1 else ''} gespeichert")
        self.refresh()
