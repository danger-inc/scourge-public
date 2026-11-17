# PDF bearbeiten: Seitenübersicht wie in Acrobat – aber einfacher. Seiten hinzufügen (andere PDFs, Bilder, leere Seiten),
# löschen, drehen, duplizieren, per Ziehen umsortieren, Auswahl extrahieren. Danach speichern oder mit einem Klick
# weiter bearbeiten (Text, Markierungen, Unterschrift …). Läuft lokal mit qpdf + Poppler.
import hashlib, math, os, shutil, subprocess, tempfile, threading

import tools
from convert import compact, free_name, human, image_to_pdf, IMG_EXT

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk

THUMB_W = 150
POSITIONS = ["am Ende", "nach der Auswahl", "am Anfang"]

PAGES_CSS = """
.pp-drop { border: 1px dashed alpha(@primary, 0.5); border-radius: 6px; padding: 22px; }
.pp-drop.drag-over { background-color: alpha(@primary, 0.12); border-color: @primary; }
.pp-tile { padding: 6px; border-radius: 6px; border: 1px solid transparent; }
.pp-tile:hover { background-color: alpha(@foreground, 0.07); }
flowboxchild:selected .pp-tile { background-color: alpha(@primary, 0.22); border-color: @primary; }
flowboxchild, flowboxchild:selected, flowboxchild:focus { background: none; outline: none; box-shadow: none; }
.pp-pic { border: 1px solid alpha(@foreground, 0.25); background-color: white; }
.pp-new .pp-pic { border-color: @tertiary; border-width: 2px; }
.pp-nr { font-weight: bold; }
.pp-tag { color: @tertiary; font-size: 0.85em; }
.pp-drop-before { box-shadow: inset 3px 0 0 @primary; }
.pp-drop-after { box-shadow: inset -3px 0 0 @primary; }
.pp-status { color: alpha(@foreground, 0.7); }
"""


def qpdf(cmd, timeout=600):
    r = subprocess.run(["qpdf"] + cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode not in (0, 3):          # 3 = nur Warnungen
        msg = (r.stderr or r.stdout or "qpdf fehlgeschlagen").strip().splitlines()
        raise RuntimeError(msg[-1][:200] if msg else "qpdf fehlgeschlagen")


def blank_pdf(path, w, h):
    import cairo
    s = cairo.PDFSurface(path, w, h)
    cr = cairo.Context(s)
    cr.show_page()
    s.finish()


def render(doc, idx, rot, width):
    """Seite idx (0-basiert) um rot Grad (im Uhrzeigersinn) gedreht als Gdk.Texture."""
    import cairo
    page = doc.get_page(idx)
    w, h = page.get_size()
    s = width / max(1.0, w if rot % 180 == 0 else h)
    ow, oh = (w, h) if rot % 180 == 0 else (h, w)
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(1, int(ow * s)), max(1, int(oh * s)))
    cr = cairo.Context(surf)
    cr.set_source_rgb(1, 1, 1)
    cr.paint()
    if rot == 90:
        cr.translate(h * s, 0); cr.rotate(math.pi / 2)
    elif rot == 180:
        cr.translate(w * s, h * s); cr.rotate(math.pi)
    elif rot == 270:
        cr.translate(0, w * s); cr.rotate(-math.pi / 2)
    cr.scale(s, s)
    page.render(cr)
    surf.flush()
    return Gdk.MemoryTexture.new(surf.get_width(), surf.get_height(), Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED,
                                 GLib.Bytes.new(bytes(surf.get_data())), surf.get_stride())


def blank_texture(w, h, rot, width):
    if rot % 180:
        w, h = h, w
    pw, ph = max(1, int(width)), max(1, int(width * h / max(1.0, w)))
    return Gdk.MemoryTexture.new(pw, ph, Gdk.MemoryFormat.R8G8B8, GLib.Bytes.new(b"\xff" * (pw * ph * 3)), pw * 3)


def build_pdf(main, entries, files, dst, tmp):
    """Neue PDF aus der Seitenliste schreiben. entries: [{src|None, idx, rot, size}]; files: src → Pfad."""
    if not entries:
        raise RuntimeError("keine Seiten übrig")
    parts = []                                           # (pdf, [seiten ab 1])
    for n, e in enumerate(entries):
        if e["src"] is None:
            p = os.path.join(tmp, f"leer{n}.pdf")
            blank_pdf(p, *e["size"])
            parts.append((p, [1]))
            continue
        p = files[e["src"]]
        if parts and parts[-1][0] == p and parts[-1][1][-1] + 1 == e["idx"] + 1:
            parts[-1][1].append(e["idx"] + 1)
        else:
            parts.append((p, [e["idx"] + 1]))
    cmd = [main] if main else ["--empty"]
    cmd.append("--pages")
    for p, pages in parts:
        cmd += [p, compact(pages)]
    cmd.append("--")
    by_angle = {}
    for i, e in enumerate(entries):
        if e["rot"] % 360:
            by_angle.setdefault(e["rot"] % 360, []).append(i + 1)
    for a, pg in sorted(by_angle.items()):
        cmd.append(f"--rotate=+{a}:{compact(pg)}")
    qpdf(cmd + [dst])


class PdfPagesPage(tools.ToolPage):
    title = "PDF bearbeiten"
    subtitle = "Seiten hinzufügen, löschen, drehen und umsortieren – dann direkt weiter bearbeiten"
    privacy = "Läuft komplett auf deinem Rechner. Beim Speichern wird das Original vorher gesichert."

    def build(self):
        self.main = None                    # Pfad der geöffneten PDF (None = neues Dokument)
        self.docs = {}                      # Quelle (Pfad) → Poppler-Dokument
        self.files = {}                     # Quelle → Pfad (hier identisch, Bilder: temporäre PDF)
        self.pages = []                     # Seitenliste (Dicts)
        self.undo, self.redo = [], []
        self.dirty = False
        self.cache = {}                     # (src, idx, rot) → Gdk.Texture
        self.tmp = tempfile.mkdtemp(prefix="scourge-pages-")
        self.written = None                 # zuletzt gespeicherte Datei (der Editor lädt sie beim Zurückwechseln neu)
        self.loaded_mtime = None
        self._uid = 0
        self.sel_ids = set()
        self._drag = []
        self._thumb_gen = 0
        self.busy = False

        if not shutil.which("qpdf"):
            l = Gtk.Label(label="󰀦  Für dieses Werkzeug fehlt „qpdf“ – bitte installieren (z. B. sudo pacman -S qpdf).",
                          xalign=0, wrap=True)
            l.add_css_class("cv-miss")
            self.box.append(l)

        # --- Datei ---
        c = self.file_card = self.card("datei")
        self.empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.empty.add_css_class("pp-drop")
        row = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        row.append(self.button("\U000F0226  PDF öffnen", self.pick_main))
        row.append(self.button("\U000F02E9  neu aus Bildern", lambda: self.pick_images(new=True),
                               "Aus Fotos oder Scans eine neue PDF machen"))
        self.empty.append(row)
        hint = Gtk.Label(label="…oder eine PDF (und Bilder) hierher ziehen", xalign=0.5)
        hint.add_css_class("note")
        self.empty.append(hint)
        c.append(self.empty)
        self.head = Gtk.Box(spacing=8)
        self.name_lbl = Gtk.Label(xalign=0, hexpand=True, wrap=True, selectable=False)
        self.name_lbl.add_css_class("key")
        self.head.append(self.name_lbl)
        self.head.append(self.button("\U000F0226  andere PDF öffnen", self.pick_main, small=True))
        self.head.set_visible(False)
        c.append(self.head)

        # --- Seiten ---
        c = self.pages_card = self.card("seiten")
        self.pages_card.set_visible(False)

        add = self.tool_flow(c)
        add.append(self.button("\U000F0415  PDF anhängen", self.add_pdf, "Seiten einer anderen PDF einfügen"))
        add.append(self.button("\U000F02E9  Bilder als Seiten", lambda: self.pick_images(), "Fotos/Scans als neue Seiten einfügen"))
        add.append(self.button("\U000F0224  leere Seite", self.add_blank))
        pos = Gtk.Box(spacing=6)
        pl = Gtk.Label(label="einfügen:", xalign=0)
        pl.add_css_class("note")
        self.pos_dd = Gtk.DropDown.new_from_strings(POSITIONS)
        self.pos_dd.add_css_class("pb")
        pos.append(pl); pos.append(self.pos_dd)
        add.append(pos)
        self.a4 = Gtk.CheckButton(label="Bilder auf A4", active=True,
                                  tooltip_text="Bilder mittig auf eine A4-Seite setzen (sonst: Seite in Bildgröße)")
        add.append(self.a4)

        ops = self.tool_flow(c)
        self.op_btns = []
        for label, cb, tip in [("\U000F0465  links", lambda: self.rotate(-90), "Auswahl nach links drehen"),
                               ("\U000F0467  rechts", lambda: self.rotate(90), "Auswahl nach rechts drehen (R)"),
                               ("\U000F018F  duplizieren", self.duplicate, None),
                               ("\U000F01B4  löschen", self.delete, "Entf"),
                               ("\U000F004D  früher", lambda: self.move(-1), "Auswahl eine Position nach vorn"),
                               ("\U000F0054  später", lambda: self.move(1), "Auswahl eine Position nach hinten"),
                               ("\U000F0207  auswahl extrahieren", self.extract, "Ausgewählte Seiten als neue PDF speichern")]:
            b = self.button(label, cb, tip, small=True)
            ops.append(b)
            self.op_btns.append(b)
        ops.append(self.button("\U000F0486  alle", self.select_all, "Strg+A", small=True))
        self.undo_btn = self.button("\U000F054C", self.do_undo, "Rückgängig (Strg+Z)", small=True)
        self.redo_btn = self.button("\U000F044E", self.do_redo, "Wiederholen (Strg+Y)", small=True)
        ops.append(self.undo_btn); ops.append(self.redo_btn)

        self.status = Gtk.Label(xalign=0, wrap=True)
        self.status.add_css_class("pp-status")
        c.append(self.status)

        self.grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.MULTIPLE, activate_on_single_click=False,
                                homogeneous=True, min_children_per_line=2, max_children_per_line=12,
                                column_spacing=4, row_spacing=4, valign=Gtk.Align.START)
        self.grid.connect("selected-children-changed", self.on_selection)
        gap = Gtk.GestureClick(button=3)
        gap.connect("pressed", lambda g, n, x, y: (g.set_state(Gtk.EventSequenceState.CLAIMED), self.gap_menu(self.grid, x, y)))
        self.grid.add_controller(gap)
        c.append(self.grid)
        hint = Gtk.Label(label="Klicken wählt eine Seite, Strg/Umschalt mehrere. Seiten lassen sich per Ziehen umsortieren; "
                               "Doppelklick dreht nach rechts; Rechtsklick auf eine Seite oder zwischen die Seiten öffnet ein Menü. Neu eingefügte Seiten haben einen grünen Rahmen.", xalign=0, wrap=True)
        hint.add_css_class("note")
        c.append(hint)

        # --- Speichern ---
        c = self.save_card = self.card("fertig")
        self.save_card.set_visible(False)
        row = Gtk.Box(spacing=6)
        self.edit_btn = Gtk.Button(label="\U000F03EB  weiter bearbeiten", valign=Gtk.Align.CENTER,
                                   tooltip_text="Speichert die Änderungen und wechselt in den Modus „bearbeiten“ (dieselbe Datei)")
        self.edit_btn.add_css_class("pb")
        self.edit_btn.add_css_class("suggested")
        self.edit_btn.connect("clicked", lambda *_: self.save_then_edit())
        row.append(self.edit_btn)
        self.save_btn = self.button("\U000F0193  speichern", self.save, "Strg+S – überschreibt die geöffnete PDF (Original wird gesichert)")
        row.append(self.save_btn)
        row.append(self.button("speichern unter …", self.save_as))
        c.append(row)
        self.save_note = Gtk.Label(xalign=0, wrap=True)
        self.save_note.add_css_class("note")
        c.append(self.save_note)

        # Ablegen von Dateien auf der ganzen Seite
        target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        target.connect("enter", lambda *_: (self.empty.add_css_class("drag-over"), Gdk.DragAction.COPY)[1])
        target.connect("leave", lambda *_: self.empty.remove_css_class("drag-over"))
        target.connect("drop", self.on_files_dropped)
        self.add_controller(target)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)
        self.refresh()

    # ---------- kleine Helfer ----------
    def tool_flow(self, parent):
        fl = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=20, min_children_per_line=1,
                         column_spacing=6, row_spacing=6, homogeneous=False)
        parent.append(fl)
        return fl

    def new_id(self):
        self._uid += 1
        return self._uid

    def entry(self, src, idx, size, rot=0):
        return {"id": self.new_id(), "src": src, "idx": idx, "rot": rot, "size": size}

    def notify(self, msg):
        try:
            self.tab.notify(msg)
        except Exception:
            pass

    def name(self):
        return os.path.basename(self.main) if self.main else "neue PDF"

    # ---------- Quellen laden ----------
    def register(self, path, key=None):
        """PDF als Quelle laden → Anzahl Seiten. Wirft RuntimeError bei defekten/geschützten Dateien."""
        import netfs
        key = key or path
        if key not in self.docs:
            try:
                doc = netfs.poppler_doc(path)
            except GLib.Error as e:
                raise RuntimeError("geschützt oder beschädigt – " + str(e.message).split(":")[-1].strip()) from None
            if doc.get_n_pages() == 0:
                raise RuntimeError("enthält keine Seiten")
            self.docs[key] = doc
            self.files[key] = path
        return self.docs[key].get_n_pages()

    def entries_of(self, key):
        doc = self.docs[key]
        out = []
        for i in range(doc.get_n_pages()):
            w, h = doc.get_page(i).get_size()
            out.append(self.entry(key, i, (w, h)))
        return out

    # ---------- Öffnen ----------
    def pick_main(self):
        if self.dirty:
            return self.confirm_discard(self.pick_main_now)
        self.pick_main_now()

    def pick_main_now(self):
        f = Gtk.FileFilter(name="PDF")
        f.add_mime_type("application/pdf")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(f)
        dlg = Gtk.FileDialog(title="PDF öffnen", filters=filters)

        def done(d, res):
            try:
                p = d.open_finish(res).get_path()
            except GLib.Error:
                return
            if p:
                self.open_main(p)
        dlg.open(self.tab.win, None, done)

    def confirm_discard(self, then):
        dlg = Gtk.AlertDialog(message="Änderungen verwerfen?", detail="Es gibt ungespeicherte Änderungen an den Seiten.",
                              buttons=["Abbrechen", "Verwerfen"], cancel_button=0, default_button=0)

        def done(d, res):
            try:
                if d.choose_finish(res) == 1:
                    then()
            except GLib.Error:
                pass
        dlg.choose(self.tab.win, None, done)

    def open_main(self, path):
        """Eine PDF zum Bearbeiten laden (ersetzt die aktuelle Arbeit)."""
        try:
            self.docs, self.files, self.cache = {}, {}, {}
            self.register(path)
        except RuntimeError as e:
            self.docs, self.files = {}, {}
            self.main, self.pages = None, []
            self.refresh()
            return self.notify(f"{os.path.basename(path)}: {e}")
        self.main = path
        try:
            self.loaded_mtime = os.path.getmtime(path)
        except OSError:
            self.loaded_mtime = None
        self.pages = self.entries_of(path)
        self.undo, self.redo, self.sel_ids = [], [], set()
        self.dirty = False
        try:
            self.tab.app_main.add_recent(path)
        except Exception:
            pass
        self.refresh()

    def on_files_dropped(self, t, v, x, y):
        self.empty.remove_css_class("drag-over")
        paths = [f.get_path() for f in v.get_files() if f.get_path()]
        self.take_paths(paths)
        return True

    def take_paths(self, paths):
        """Dateien einsortieren: PDFs/Bilder. Ohne geöffnete Datei wird die erste PDF das Dokument."""
        paths = [p for p in paths if os.path.isfile(p)]
        pdfs = [p for p in paths if p.lower().endswith(".pdf")]
        imgs = [p for p in paths if p.lower().endswith(IMG_EXT)]
        if not pdfs and not imgs:
            return self.notify("Nur PDFs und Bilder lassen sich hier einfügen")
        if not self.pages and not self.main and pdfs:
            self.open_main(pdfs.pop(0))
        self.add_sources(pdfs, imgs)

    # ---------- Hinzufügen ----------
    def insert_at(self):
        n = len(self.pages)
        p = self.pos_dd.get_selected()
        if p == 2:
            return 0
        if p == 1:
            sel = [i for i, e in enumerate(self.pages) if e["id"] in self.sel_ids]
            if sel:
                return max(sel) + 1
        return n

    def add_pdf(self, at=None):
        f = Gtk.FileFilter(name="PDF")
        f.add_mime_type("application/pdf")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(f)
        dlg = Gtk.FileDialog(title="PDF anhängen", filters=filters)

        def done(d, res):
            try:
                files = d.open_multiple_finish(res)
            except GLib.Error:
                return
            self.add_sources([files.get_item(i).get_path() for i in range(files.get_n_items())], [], at)
        dlg.open_multiple(self.tab.win, None, done)

    def pick_images(self, new=False, at=None):
        f = Gtk.FileFilter(name="Bilder")
        for e in IMG_EXT:
            f.add_suffix(e.lstrip("."))
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(f)
        dlg = Gtk.FileDialog(title="Bilder wählen", filters=filters)

        def done(d, res):
            try:
                files = d.open_multiple_finish(res)
            except GLib.Error:
                return
            paths = [files.get_item(i).get_path() for i in range(files.get_n_items())]
            if new and (self.pages or self.main):
                paths = paths           # einfach anhängen
            self.add_sources([], paths, at)
        dlg.open_multiple(self.tab.win, None, done)

    def add_sources(self, pdfs, imgs, at=None):
        """PDFs und Bilder an der gewählten Stelle einfügen. Bilder werden im Hintergrund in PDF-Seiten verwandelt."""
        if not pdfs and not imgs:
            return
        at = self.insert_at() if at is None else at
        a4 = self.a4.get_active()
        self.set_busy(True, "füge Seiten ein …")

        def work():
            new, errs = [], []
            for p in pdfs:
                try:
                    key = p if p not in self.docs else f"{p}#{self.new_id()}"
                    self._register_threadsafe(p, key)
                    new += self.entries_of(key)
                except RuntimeError as e:
                    errs.append(f"{os.path.basename(p)}: {e}")
            for p in imgs:
                try:
                    t = os.path.join(self.tmp, f"bild{self.new_id()}.pdf")
                    image_to_pdf(p, t, "a4" if a4 else "fit")
                    self._register_threadsafe(t, t)
                    new += self.entries_of(t)
                except Exception as e:
                    errs.append(f"{os.path.basename(p)}: {e}")
            GLib.idle_add(finish, new, errs)

        def finish(new, errs):
            self.set_busy(False)
            if new:
                self.push_undo()
                # Bei „neues Dokument ohne Datei“ ist die erste Quelle kein Original: Seiten gelten als neu
                self.pages[at:at] = new
                self.sel_ids = {e["id"] for e in new}
                self.dirty = True
                self.refresh()
                self.notify(f"{len(new)} Seite{'n' if len(new) != 1 else ''} eingefügt")
            if errs:
                self.notify(" · ".join(errs[:2]))
            return False
        threading.Thread(target=work, daemon=True).start()

    def _register_threadsafe(self, path, key):
        self.register(path, key)

    def add_blank(self, at=None):
        at = self.insert_at() if at is None else at
        ref = self.pages[min(max(at - 1, 0), len(self.pages) - 1)] if self.pages else None
        size = ref["size"] if ref else (595.0, 842.0)
        if ref and ref["rot"] % 180:
            size = (size[1], size[0])
        self.push_undo()
        e = self.entry(None, 0, size)
        self.pages.insert(at, e)
        self.sel_ids = {e["id"]}
        self.dirty = True
        self.refresh()

    # ---------- Bearbeiten ----------
    def snapshot(self):
        return [dict(e) for e in self.pages]

    def push_undo(self):
        self.undo.append(self.snapshot())
        del self.undo[:-100]
        self.redo.clear()

    def do_undo(self):
        if self.undo:
            self.redo.append(self.snapshot())
            self.pages = self.undo.pop()
            self.dirty = True
            self.refresh()

    def do_redo(self):
        if self.redo:
            self.undo.append(self.snapshot())
            self.pages = self.redo.pop()
            self.dirty = True
            self.refresh()

    def selected(self):
        return [i for i, e in enumerate(self.pages) if e["id"] in self.sel_ids]

    def need_sel(self):
        s = self.selected()
        if not s:
            self.notify("Erst Seiten anklicken")
        return s

    def rotate(self, deg, ids=None):
        sel = [i for i, e in enumerate(self.pages) if e["id"] in ids] if ids else self.need_sel()
        if not sel:
            return
        self.push_undo()
        for i in sel:
            self.pages[i]["rot"] = (self.pages[i]["rot"] + deg) % 360
        self.dirty = True
        self.refresh()

    def delete(self):
        sel = self.need_sel()
        if not sel:
            return
        if len(sel) == len(self.pages):
            return self.notify("Mindestens eine Seite muss bleiben")
        self.push_undo()
        gone = set(sel)
        self.pages = [e for i, e in enumerate(self.pages) if i not in gone]
        self.sel_ids = set()
        self.dirty = True
        self.refresh()

    def duplicate(self):
        sel = self.need_sel()
        if not sel:
            return
        self.push_undo()
        new = []
        for i in reversed(sel):
            e = dict(self.pages[i], id=self.new_id())
            self.pages.insert(i + 1, e)
            new.append(e["id"])
        self.sel_ids = set(new)
        self.dirty = True
        self.refresh()

    def move(self, d):
        sel = self.need_sel()
        if not sel:
            return
        if (d < 0 and sel[0] == 0) or (d > 0 and sel[-1] == len(self.pages) - 1):
            return
        self.push_undo()
        order = sel if d < 0 else list(reversed(sel))
        for i in order:
            self.pages[i], self.pages[i + d] = self.pages[i + d], self.pages[i]
        self.dirty = True
        self.refresh()

    def select_all(self):
        self.sel_ids = {e["id"] for e in self.pages}
        self.refresh(keep_scroll=True)

    def drop_move(self, ids, target_id, after):
        """Per Ziehen: Seiten ids vor/nach die Seite target_id setzen."""
        if target_id in ids:
            return
        moving = [e for e in self.pages if e["id"] in ids]
        rest = [e for e in self.pages if e["id"] not in ids]
        pos = next(i for i, e in enumerate(rest) if e["id"] == target_id) + (1 if after else 0)
        new = rest[:pos] + moving + rest[pos:]
        if [e["id"] for e in new] == [e["id"] for e in self.pages]:
            return
        self.push_undo()
        self.pages = new
        self.dirty = True
        self.refresh()

    # ---------- Speichern ----------
    def make(self, entries, dst):
        build_pdf(self.main, entries, self.files, dst, self.tmp)

    def run_bg(self, label, work, done):
        """work() im Hintergrund; done(ergebnis, fehler) im Hauptthread."""
        self.set_busy(True, label)

        def th():
            try:
                res, err = work(), None
            except Exception as e:
                res, err = None, str(e)
            GLib.idle_add(fin, res, err)

        def fin(res, err):
            self.set_busy(False)
            done(res, err)
            return False
        threading.Thread(target=th, daemon=True).start()

    def backup(self, path):
        d = os.path.expanduser("~/.cache/scourge/originale")
        try:
            os.makedirs(d, exist_ok=True)
            tag = hashlib.sha1(path.encode()).hexdigest()[:8]
            dest = os.path.join(d, f"{tag}-{os.path.basename(path)}")
            if not os.path.exists(dest):
                shutil.copy2(path, dest)
        except OSError:
            pass

    def save(self, then=None):
        """In die geöffnete Datei speichern (Original vorher sichern); ohne Datei: speichern unter."""
        if not self.main:
            return self.save_as(then)
        if not self.dirty:
            self.save_note.set_label("Keine Änderungen.")
            return then(self.main) if then else None
        path, entries = self.main, self.snapshot()

        def work():
            self.backup(path)
            out = os.path.join(self.tmp, "ergebnis.pdf")
            build_pdf(path, entries, self.files, out, self.tmp)
            try:
                os.replace(out, path)
            except OSError:
                shutil.copyfile(out, path)               # anderes Laufwerk / Netz
            return path

        def done(res, err):
            if err:
                return self.notify("Speichern fehlgeschlagen: " + err)
            self.reload_saved(path)
            self.notify("Gespeichert: " + os.path.basename(path))
            if then:
                then(path)
        self.run_bg("speichere …", work, done)

    def save_as(self, then=None):
        f = Gtk.FileFilter(name="PDF")
        f.add_mime_type("application/pdf")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(f)
        base = os.path.dirname(self.main) if self.main else os.path.expanduser("~/Documents")
        if not os.path.isdir(base):
            base = os.path.expanduser("~")
        stem = os.path.splitext(self.name())[0] + "-bearbeitet"
        dlg = Gtk.FileDialog(title="Speichern unter", filters=filters,
                             initial_name=os.path.basename(free_name(base, stem, ".pdf")),
                             initial_folder=Gio.File.new_for_path(base))

        def chosen(d, res):
            try:
                p = d.save_finish(res).get_path()
            except GLib.Error:
                return
            if not p:
                return
            if not p.lower().endswith(".pdf"):
                p += ".pdf"
            self.write_to(p, self.snapshot(), reload=True, then=then)
        dlg.save(self.tab.win, None, chosen)

    def write_to(self, dst, entries, reload=False, then=None):
        main = self.main

        def work():
            out = os.path.join(self.tmp, "ergebnis.pdf")
            build_pdf(main, entries, self.files, out, self.tmp)
            shutil.copyfile(out, dst)
            return dst

        def done(res, err):
            if err:
                return self.notify("Speichern fehlgeschlagen: " + err)
            if reload:
                self.reload_saved(dst)
            self.notify("Gespeichert: " + os.path.basename(dst))
            if then:
                then(dst)
        self.run_bg("speichere …", work, done)

    def reload_saved(self, path):
        """Nach dem Speichern die fertige Datei als neuen Ausgangspunkt laden."""
        self.open_main(path)
        self.written = path
        self.save_note.set_label(f"Gespeichert in {path.replace(os.path.expanduser('~'), '~', 1)}")

    def extract(self):
        sel = self.need_sel()
        if not sel:
            return
        entries = [dict(self.pages[i]) for i in sel]
        f = Gtk.FileFilter(name="PDF")
        f.add_mime_type("application/pdf")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(f)
        base = os.path.dirname(self.main) if self.main else os.path.expanduser("~")
        stem = os.path.splitext(self.name())[0] + "-auswahl"
        dlg = Gtk.FileDialog(title="Ausgewählte Seiten speichern", filters=filters,
                             initial_name=os.path.basename(free_name(base, stem, ".pdf")),
                             initial_folder=Gio.File.new_for_path(base))

        def chosen(d, res):
            try:
                p = d.save_finish(res).get_path()
            except GLib.Error:
                return
            if p:
                self.write_to(p if p.lower().endswith(".pdf") else p + ".pdf", entries)
        dlg.save(self.tab.win, None, chosen)

    def save_then_edit(self):
        self.save(then=self.tab.edit_here)

    # ---------- Anzeige ----------
    def set_busy(self, on, text=""):
        self.busy = on
        for w in (self.edit_btn, self.save_btn):
            w.set_sensitive(not on)
        if on:
            self.status.set_label("󰔟  " + text)

    def on_key(self, ctl, keyval, code, state):
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace) and self.pages_card.get_visible():
            self.delete()
        elif ctrl and keyval in (Gdk.KEY_z, Gdk.KEY_Z):
            self.do_undo()
        elif ctrl and keyval in (Gdk.KEY_y, Gdk.KEY_Y):
            self.do_redo()
        elif ctrl and keyval in (Gdk.KEY_a, Gdk.KEY_A) and self.pages:
            self.select_all()
        elif ctrl and keyval in (Gdk.KEY_s, Gdk.KEY_S) and self.pages:
            self.save()
        elif keyval in (Gdk.KEY_r, Gdk.KEY_R) and not ctrl and self.selected():
            self.rotate(90)
        else:
            return False
        return True

    def on_selection(self, fb):
        if getattr(self, "_building", False):
            return
        self.sel_ids = set()
        for ch in fb.get_selected_children():
            e = getattr(ch, "entry_id", None)
            if e is not None:
                self.sel_ids.add(e)
        # Auswahl = Ziel für „einfügen“: nach der Auswahl, sonst am Ende
        self.pos_dd.set_selected(1 if self.sel_ids else 0)
        self.update_status()

    def update_status(self):
        n, s = len(self.pages), len(self.sel_ids)
        new = sum(1 for e in self.pages if e["src"] != self.main)
        txt = f"{n} Seite{'n' if n != 1 else ''}"
        if s:
            txt += f" · {s} ausgewählt"
        if new:
            txt += f" · {new} neu"
        if self.dirty:
            txt += " · ungespeichert"
        self.status.set_label(txt)
        for b in self.op_btns:
            b.set_sensitive(bool(s) or b.get_label().strip().endswith("alle"))
        self.undo_btn.set_sensitive(bool(self.undo))
        self.redo_btn.set_sensitive(bool(self.redo))

    def tile(self, i, e):
        child = Gtk.FlowBoxChild()
        child.entry_id = e["id"]
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, halign=Gtk.Align.CENTER)
        box.add_css_class("pp-tile")
        if e["src"] != self.main:
            box.add_css_class("pp-new")
        w, h = e["size"]
        if e["rot"] % 180:
            w, h = h, w
        pw, ph = max(8, int(w * self.scale)), max(8, int(h * self.scale))
        pic = Gtk.Picture(content_fit=Gtk.ContentFit.FILL, can_shrink=True, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        pic.set_size_request(pw, ph)
        pic.add_css_class("pp-pic")
        slot = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)     # gleich große Felder, Seite mittig in echter Proportion
        slot.set_size_request(self.slot[0], self.slot[1])
        slot.append(pic)
        box.append(slot)
        child.pw = pw
        nr = Gtk.Label(label=str(i + 1))
        nr.add_css_class("pp-nr")
        box.append(nr)
        if e["src"] != self.main:
            src = os.path.basename(self.files.get(e["src"], "")) if e["src"] else "leere Seite"
            tag = Gtk.Label(label="leer" if e["src"] is None else ("Bild/PDF" if src.startswith("bild") else src))
            tag.add_css_class("pp-tag")
            tag.set_max_width_chars(16)
            tag.set_ellipsize(3)
            box.append(tag)
            child.set_tooltip_text("leere Seite" if e["src"] is None else f"{src} · Seite {e['idx'] + 1}")
        else:
            child.set_tooltip_text(f"Seite {e['idx'] + 1}")
        child.set_child(box)
        child.pic = pic
        child.entry = e

        rc = Gtk.GestureClick(button=3)
        rc.connect("pressed", lambda g, n, x, y, b=box, eid=e["id"]: (g.set_state(Gtk.EventSequenceState.CLAIMED),
                                                                      self.tile_menu(b, eid, x, y)))
        box.add_controller(rc)

        # Doppelklick = nach rechts drehen
        click = Gtk.GestureClick()
        click.connect("pressed", lambda g, n, x, y, eid=e["id"]: n == 2 and self.rotate(90, {eid}))
        box.add_controller(click)

        # Ziehen zum Umsortieren
        ds = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        ds.connect("prepare", lambda s, x, y, eid=e["id"]: self.drag_prepare(eid))
        box.add_controller(ds)
        dt = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        dt.connect("motion", lambda t, x, y, b=box: self.drag_motion(b, x))
        dt.connect("leave", lambda t, b=box: self.drag_clear(b))
        dt.connect("drop", lambda t, v, x, y, b=box, eid=e["id"]: self.drag_drop(b, eid, x))
        box.add_controller(dt)
        return child

    # ---------- Rechtsklick-Menüs ----------
    def popup_menu(self, anchor, x, y, items):
        """items: (Beschriftung, Aktion) oder None für eine Trennlinie."""
        pop = Gtk.Popover(has_arrow=False, autohide=True, halign=Gtk.Align.START)
        pop.add_css_class("pdf-pop")
        pop.add_css_class("formats")
        pop.set_parent(anchor)
        r = Gdk.Rectangle()
        r.x, r.y, r.width, r.height = int(x), int(y), 1, 1
        pop.set_pointing_to(r)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.add_css_class("pdf-pop-box")
        for it in items:
            if it is None:
                box.append(Gtk.Separator())
                continue
            b = Gtk.Button(label=it[0], focus_on_click=False, halign=Gtk.Align.FILL)
            b.add_css_class("pb"); b.add_css_class("small")
            b.get_child().set_xalign(0)
            b.connect("clicked", lambda _b, cb=it[1]: (pop.popdown(), GLib.idle_add(lambda: (cb(), False)[-1])))
            box.append(b)
        pop.set_child(box)
        pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
        pop.popup()

    def insert_items(self, at):
        return [("\U000F0224  leere Seite einfügen", lambda: self.add_blank(at)),
                ("\U000F0415  PDF einfügen …", lambda: self.add_pdf(at)),
                ("\U000F02E9  Bilder einfügen …", lambda: self.pick_images(at=at))]

    def tile_menu(self, box, eid, x, y):
        """Rechtsklick auf eine Seite: sie wird (falls nötig) ausgewählt, das Menü wirkt auf die Auswahl."""
        if eid not in self.sel_ids:
            self.grid.unselect_all()
            ch = box.get_parent()
            if ch is not None:
                self.grid.select_child(ch)
        i = next((k for k, e in enumerate(self.pages) if e["id"] == eid), 0)
        n = len(self.sel_ids) or 1
        many = f" ({n} Seiten)" if n > 1 else ""
        first = min((k for k, e in enumerate(self.pages) if e["id"] in self.sel_ids), default=i)
        last = max((k for k, e in enumerate(self.pages) if e["id"] in self.sel_ids), default=i)
        items = [("\U000F0465  links drehen" + many, lambda: self.rotate(-90)),
                 ("\U000F0467  rechts drehen" + many, lambda: self.rotate(90)),
                 ("\U000F018F  duplizieren" + many, self.duplicate),
                 ("\U000F01B4  löschen" + many, self.delete),
                 None,
                 ("\U000F004D  früher", lambda: self.move(-1)),
                 ("\U000F0054  später", lambda: self.move(1)),
                 ("\U000F0207  als neue PDF extrahieren" + many, self.extract),
                 None,
                 ("\U000F0224  leere Seite davor", lambda: self.add_blank(first)),
                 ("\U000F0224  leere Seite danach", lambda: self.add_blank(last + 1)),
                 ("\U000F0415  PDF danach einfügen …", lambda: self.add_pdf(last + 1)),
                 ("\U000F02E9  Bilder danach einfügen …", lambda: self.pick_images(at=last + 1))]
        self.popup_menu(box, x, y, items)

    def gap_menu(self, grid, x, y):
        """Rechtsklick zwischen die Seiten: dort einfügen."""
        if grid.get_child_at_pos(x, y) is not None:
            return
        best, bd, at = None, 1e18, len(self.pages)
        for k in range(len(self.pages)):
            ch = grid.get_child_at_index(k)
            ok, rc = ch.compute_bounds(grid) if ch else (False, None)
            if not ok:
                continue
            cx, cy = rc.get_x() + rc.get_width() / 2, rc.get_y() + rc.get_height() / 2
            d = (cx - x) ** 2 + 4 * (cy - y) ** 2                  # gleiche Zeile zählt mehr
            if d < bd:
                bd, at = d, (k + 1 if x > cx else k)
        self.popup_menu(grid, x, y, self.insert_items(at))

    def drag_prepare(self, eid):
        ids = set(self.sel_ids) if eid in self.sel_ids else {eid}
        self._drag = ids
        return Gdk.ContentProvider.new_for_value(",".join(str(i) for i in sorted(ids)))

    def drag_motion(self, box, x):
        self.drag_clear(box)
        box.add_css_class("pp-drop-after" if x > box.get_width() / 2 else "pp-drop-before")
        return Gdk.DragAction.MOVE

    def drag_clear(self, box):
        box.remove_css_class("pp-drop-after")
        box.remove_css_class("pp-drop-before")

    def drag_drop(self, box, eid, x):
        self.drag_clear(box)
        ids = set(self._drag)
        if not ids:
            return False
        self.drop_move(ids, eid, x > box.get_width() / 2)
        return True

    def refresh(self, keep_scroll=False):
        has = bool(self.pages)
        self.empty.set_visible(not has)
        self.head.set_visible(has)
        self.pages_card.set_visible(has)
        self.save_card.set_visible(has)
        if has:
            self.name_lbl.set_label(f"\U000F0226  {self.name()}" + (f"  ·  {human(os.path.getsize(self.main))}" if self.main and os.path.exists(self.main) else ""))
        self.edit_btn.set_label("\U000F03EB  weiter bearbeiten")
        self.save_btn.set_sensitive(has and not self.busy)
        self._thumb_gen += 1
        gen = self._thumb_gen
        adj = self.get_vadjustment()
        pos = adj.get_value() if keep_scroll else None
        dims = [(e["size"][::-1] if e["rot"] % 180 else e["size"]) for e in self.pages] or [(595.0, 842.0)]
        maxw, maxh = max(d[0] for d in dims), max(d[1] for d in dims)
        # ein gemeinsamer Maßstab: schmale/kleine Seiten bleiben schmal/klein, nichts wird gestreckt
        self.scale = min(THUMB_W / max(1.0, maxw), THUMB_W * 1.5 / max(1.0, maxh))
        self.slot = (int(maxw * self.scale) + 2, int(maxh * self.scale) + 2)
        self._building = True
        self.grid.remove_all()
        self.tiles = []
        for i, e in enumerate(self.pages):
            t = self.tile(i, e)
            self.grid.append(t)
            self.tiles.append(t)
        for t in self.tiles:
            if t.entry_id in self.sel_ids:
                self.grid.select_child(t)
        self._building = False
        self.update_status()
        if pos is not None:
            GLib.idle_add(lambda: adj.set_value(pos) and False)
        GLib.idle_add(self._thumb_step, gen, 0)

    def _thumb_step(self, gen, start):
        """Miniaturen scheibchenweise zeichnen, damit die Oberfläche flüssig bleibt."""
        import time
        if gen != self._thumb_gen:
            return False
        t0 = time.monotonic()
        i = start
        while i < len(self.tiles) and time.monotonic() - t0 < 0.015:
            t, e = self.tiles[i], self.tiles[i].entry
            key = (e["src"], e["idx"], e["rot"], t.pw)
            tex = self.cache.get(key)
            if tex is None:
                try:
                    tex = (blank_texture(*e["size"], e["rot"], t.pw) if e["src"] is None
                           else render(self.docs[e["src"]], e["idx"], e["rot"], t.pw))
                except Exception:
                    tex = None
                if tex is not None:
                    self.cache[key] = tex
            if tex is not None:
                t.pic.set_paintable(tex)
            i += 1
        if i < len(self.tiles):
            GLib.idle_add(self._thumb_step, gen, i)
        return False

    def shutdown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
