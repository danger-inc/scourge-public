# Zusätze für den PDF-Editor (viewer.PdfView): Seitenleiste (Miniaturen, Gliederung, Treffer), Seiten verwalten
# (drehen, löschen, umsortieren, anhängen, teilen), Unterschrift, Formulare ausfüllen, Schwärzen, Passwortschutz
# und Texterkennung. Die Seitenarbeit macht qpdf; jede Änderung legt vorher eine Sicherung ab (Strg+Z macht sie rückgängig).
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

QPDF = shutil.which("qpdf")
SIGN_FILE = os.path.expanduser("~/.config/scourge/signatur.json")
VERSION_DIR = os.path.expanduser("~/.cache/scourge/versionen")
THUMB_W = 120


def _poppler():
    from viewer import _poppler as p
    return p()


# ======================================================================= qpdf
def run_qpdf(args, password=None):
    """(ok, Meldung). qpdf meldet Warnungen mit Code 3, schreibt aber trotzdem – das zählt als Erfolg."""
    if not QPDF:
        return False, "qpdf fehlt (sudo pacman -S qpdf)"
    cmd = [QPDF] + ([f"--password={password}"] if password else []) + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    if r.returncode in (0, 3):
        return True, ""
    return False, (r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip() else f"Code {r.returncode}"


def spec(pages):
    """[1, 2, 3, 5] → "1-3,5" (1-basiert, Reihenfolge bleibt erhalten)."""
    out, run = [], []
    for p in pages:
        if run and p == run[-1] + 1:
            run.append(p)
        else:
            if run:
                out.append(run)
            run = [p]
    if run:
        out.append(run)
    return ",".join(f"{r[0]}-{r[-1]}" if len(r) > 1 else str(r[0]) for r in out)


def op_pages(src, out, order, rotate=None, password=None):
    """Neue Seitenfolge (1-basiert, Wiederholungen erlaubt); rotate = {neue Seitennummer: Grad}."""
    args = [src, "--pages", ".", spec(order), "--"]
    by = {}
    for n, deg in (rotate or {}).items():
        by.setdefault(deg % 360, []).append(n)
    for deg, ns in by.items():
        if deg:
            args.append(f"--rotate=+{deg}:{spec(sorted(ns))}")
    return run_qpdf(args + [out], password)


def op_append(src, out, pos, others, password=None):
    """Weitere PDFs nach Seite pos (0 = vorne) einfügen."""
    n = page_count(src, password)
    args = ["--empty", "--pages"]
    if pos > 0:
        args += [src, f"1-{pos}"]
    for o in others:
        args += [o, "1-z"]
    if pos < n:
        args += [src, f"{pos + 1}-z"]
    return run_qpdf(args + ["--", out], password)


def op_replace(src, out, repl, password=None):
    """repl = {Seite (1-basiert): Pfad einer einseitigen PDF} → diese Seiten ersetzen."""
    n = page_count(src, password)
    args, start = ["--empty", "--pages"], 1
    for p in sorted(repl):
        if p > start:
            args += [src, f"{start}-{p - 1}"]
        args += [repl[p], "1"]
        start = p + 1
    if start <= n:
        args += [src, f"{start}-z"]
    return run_qpdf(args + ["--", out], password)


def page_count(path, password=None):
    if not QPDF:
        return 0
    cmd = [QPDF] + ([f"--password={password}"] if password else []) + ["--show-npages", path]
    try:
        return int(subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout.strip() or 0)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0


def is_encrypted(path):
    if not QPDF:
        return False
    try:
        return subprocess.run([QPDF, "--is-encrypted", path], capture_output=True, timeout=30).returncode == 0
    except OSError:
        return False


# ======================================================================= Unterschrift
def load_signature():
    try:
        with open(SIGN_FILE) as f:
            d = json.load(f)
        return d if d.get("strokes") else None
    except (OSError, ValueError):
        return None


def save_signature(strokes, w, h):
    os.makedirs(os.path.dirname(SIGN_FILE), exist_ok=True)
    with open(SIGN_FILE, "w") as f:
        json.dump({"strokes": [[[round(x, 2), round(y, 2)] for x, y in s] for s in strokes], "w": w, "h": h}, f)


class SignDialog(Gtk.Window):
    """Unterschrift mit Maus/Stift/Finger zeichnen; wird dauerhaft gespeichert und per Klick auf die Seite gesetzt."""

    def __init__(self, parent, on_done):
        super().__init__(title="Unterschrift", transient_for=parent, modal=True, default_width=460)
        self.on_done = on_done
        self.strokes = []
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=12, margin_bottom=12,
                      margin_start=12, margin_end=12)
        hint = Gtk.Label(label="Hier unterschreiben – die Unterschrift wird gespeichert und mit dem Unterschrift-Werkzeug "
                               "per Klick auf die Seite gesetzt.", xalign=0, wrap=True)
        hint.add_css_class("note")
        box.append(hint)
        self.area = Gtk.DrawingArea(content_width=420, content_height=170)
        self.area.add_css_class("sign-pad")
        self.area.set_draw_func(self._draw)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._begin)
        drag.connect("drag-update", self._update)
        self.area.add_controller(drag)
        box.append(self.area)
        row = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        clear = Gtk.Button(label="Löschen")
        clear.connect("clicked", lambda *_: (self.strokes.clear(), self.area.queue_draw()))
        ok = Gtk.Button(label="Speichern")
        ok.add_css_class("suggested-action")
        ok.connect("clicked", self._save)
        cancel = Gtk.Button(label="Abbrechen")
        cancel.connect("clicked", lambda *_: self.close())
        for b in (clear, cancel, ok):
            row.append(b)
        box.append(row)
        self.set_child(box)

    def _begin(self, g, x, y):
        self.strokes.append([(x, y)])
        self._o = (x, y)

    def _update(self, g, dx, dy):
        if self.strokes:
            self.strokes[-1].append((self._o[0] + dx, self._o[1] + dy))
            self.area.queue_draw()

    def _draw(self, area, cr, w, h):
        cr.set_source_rgba(1, 1, 1, 0.95)
        cr.paint()
        cr.set_source_rgba(0, 0, 0, 0.25)
        cr.set_line_width(1)
        cr.move_to(16, h - 36)
        cr.line_to(w - 16, h - 36)
        cr.stroke()
        cr.set_source_rgb(0.1, 0.24, 0.56)
        cr.set_line_width(2.4)
        cr.set_line_cap(1)
        cr.set_line_join(1)
        for s in self.strokes:
            if len(s) == 1:
                s = [s[0], (s[0][0] + 0.4, s[0][1] + 0.4)]
            cr.move_to(*s[0])
            for p in s[1:]:
                cr.line_to(*p)
            cr.stroke()

    def _save(self, *_):
        pts = [p for s in self.strokes for p in s]
        if len(pts) < 3:
            return self.close()
        x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
        y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
        w, h = max(1.0, x1 - x0), max(1.0, y1 - y0)
        norm = [[((x - x0) / w, (y - y0) / h) for x, y in s] for s in self.strokes]
        save_signature(norm, w, h)
        self.close()
        self.on_done()


# ======================================================================= Seitenleiste
class SidePanel(Gtk.Box):
    """Links neben den Seiten: Miniaturen (verschieben, drehen, löschen …), Gliederung (Lesezeichen), Suchtreffer."""

    def __init__(self, view):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.view = view
        self.set_size_request(190, -1)
        self.add_css_class("pdf-side")
        tabs = Gtk.Box(spacing=4)
        self.stack = Gtk.Stack(vexpand=True)
        self.tab_btns = {}
        group = None
        for key, label, tip in (("pages", "󰕮  Seiten", "Miniaturen, Seiten verwalten"),
                                ("outline", "󰙅  Gliederung", "Lesezeichen des Dokuments"),
                                ("hits", "󰍉  Treffer", "Alle Suchtreffer im Dokument")):
            b = Gtk.ToggleButton(label=label, tooltip_text=tip, focus_on_click=False)
            b.add_css_class("pb"); b.add_css_class("small")
            if group:
                b.set_group(group)
            group = group or b
            b.connect("toggled", lambda btn, k=key: btn.get_active() and self.stack.set_visible_child_name(k))
            self.tab_btns[key] = b
            tabs.append(b)
        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vscrollbar_policy=Gtk.PolicyType.NEVER)
        sc.set_child(tabs)
        self.append(sc)
        self.append(self.stack)

        # --- Seiten
        pbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.pages_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.MULTIPLE, activate_on_single_click=False)
        self.pages_list.add_css_class("thumbs")
        self.pages_list.connect("selected-rows-changed", self._selected)
        psc = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        psc.set_child(self.pages_list)
        pbox.append(psc)
        if view.editable:
            pbox.append(self._page_tools())
        self.stack.add_named(pbox, "pages")
        # --- Gliederung
        self.outline_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, activate_on_single_click=True)
        self.outline_list.add_css_class("thumbs")
        self.outline_list.connect("row-activated", lambda lb, row: self.view.goto(row.page, y_in_page=row.top))
        osc = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        osc.set_child(self.outline_list)
        self.outline_empty = Gtk.Label(label="Dieses PDF hat keine Lesezeichen.", wrap=True, margin_top=12)
        self.outline_empty.add_css_class("note")
        obox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        obox.append(osc)
        obox.append(self.outline_empty)
        self.stack.add_named(obox, "outline")
        # --- Treffer
        self.hits_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, activate_on_single_click=True)
        self.hits_list.add_css_class("thumbs")
        self.hits_list.connect("row-activated", lambda lb, row: self.view.show_hit(row.index))
        hsc = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        hsc.set_child(self.hits_list)
        self.hits_head = Gtk.Label(label="Mit Strg+F suchen.", xalign=0, wrap=True)
        self.hits_head.add_css_class("note")
        hbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        hbox.append(self.hits_head)
        hbox.append(hsc)
        if view.editable:
            self.redact_hits = Gtk.Button(label="󰏫  Alle Treffer schwärzen")
            self.redact_hits.add_css_class("pb"); self.redact_hits.add_css_class("small")
            self.redact_hits.connect("clicked", lambda *_: self.view.redact_hits())
            self.redact_hits.set_visible(False)
            hbox.append(self.redact_hits)
        self.stack.add_named(hbox, "hits")
        self.tab_btns["pages"].set_active(True)
        self._thumb_id = 0
        self._thumb_gen = 0
        self._rows = []
        self._dragging = []

    # ---------- Seiten-Werkzeuge
    def _page_tools(self):
        grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4, column_spacing=3,
                           row_spacing=3, homogeneous=False)
        for label, tip, cb in (("↺", "Seiten links herum drehen", lambda: self.view.pages_rotate(-90)),
                               ("↻", "Seiten rechts herum drehen", lambda: self.view.pages_rotate(90)),
                               ("󰆴", "Seiten löschen (Entf)", lambda: self.view.pages_delete()),
                               ("󰆏", "Seiten verdoppeln", lambda: self.view.pages_duplicate()),
                               ("󰈔+", "PDF nach der Auswahl einfügen …", lambda: self.view.pages_append()),
                               ("󰆐", "Auswahl als neue PDF speichern …", lambda: self.view.pages_extract()),
                               ("󰈢", "PDF in Teile zerlegen …", lambda: self.view.pages_split())):
            b = Gtk.Button(label=label, tooltip_text=tip, focus_on_click=False)
            b.add_css_class("pb"); b.add_css_class("small")
            b.connect("clicked", lambda *_, cb=cb: cb())
            grid.append(b)
        for i in range(7):
            ch = grid.get_child_at_index(i)
            ch.set_focusable(False)
        return grid

    def selected_pages(self):
        return sorted(r.page for r in self.pages_list.get_selected_rows())

    def _selected(self, lb):
        sel = self.selected_pages()
        if len(sel) == 1 and not getattr(self, "_quiet", False):
            self.view.goto(sel[0])

    # ---------- Aufbau
    def rebuild(self):
        v = self.view
        self._thumb_gen += 1
        if self._thumb_id:
            GLib.source_remove(self._thumb_id)
            self._thumb_id = 0
        self._quiet = True
        while (r := self.pages_list.get_first_child()):
            self.pages_list.remove(r)
        self._rows = []
        for i, (w, h) in enumerate(v.sizes):
            row = Gtk.ListBoxRow()
            row.page = i
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=4, margin_bottom=4,
                          halign=Gtk.Align.CENTER)
            th = Gtk.Picture(content_fit=Gtk.ContentFit.FILL, can_shrink=False)
            th.set_size_request(THUMB_W, max(20, int(THUMB_W * h / max(1, w))))
            th.add_css_class("pdf-page")
            lbl = Gtk.Label(label=str(i + 1))
            lbl.add_css_class("key")
            box.append(th)
            box.append(lbl)
            row.set_child(box)
            row.pic, row.lbl = th, lbl
            if v.editable:
                src = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
                src.connect("prepare", self._drag_prepare, row)
                row.add_controller(src)
                dst = Gtk.DropTarget.new(str, Gdk.DragAction.MOVE)
                dst.connect("drop", self._drop, row)
                row.add_controller(dst)
            self.pages_list.append(row)
            self._rows.append(row)
        self._quiet = False
        self._next_thumb = 0
        self._thumb_id = GLib.idle_add(self._thumb_step, self._thumb_gen)
        self.rebuild_outline()

    def _drag_prepare(self, src, x, y, row):
        sel = self.selected_pages()
        pages = sel if row.page in sel else [row.page]
        return Gdk.ContentProvider.new_for_value(json.dumps(pages))

    def _drop(self, target, value, x, y, row):
        try:
            moving = sorted(json.loads(value))
        except ValueError:
            return False
        before = row.page if y < row.get_height() / 2 else row.page + 1
        self.view.pages_move(moving, before)
        return True

    def _thumb_step(self, gen):
        if gen != self._thumb_gen:
            return False
        v = self.view
        t0 = time.monotonic()
        while self._next_thumb < len(self._rows) and time.monotonic() - t0 < 0.012:
            i = self._next_thumb
            self._next_thumb += 1
            try:
                self._rows[i].pic.set_paintable(render_thumb(v.page(i), THUMB_W))
            except Exception:
                pass
        if self._next_thumb >= len(self._rows):
            self._thumb_id = 0
            return False
        return True

    def mark_page(self, cur):
        for r in self._rows:
            (r.lbl.add_css_class if r.page == cur else r.lbl.remove_css_class)("tertiary")

    # ---------- Gliederung
    def rebuild_outline(self):
        while (r := self.outline_list.get_first_child()):
            self.outline_list.remove(r)
        n = 0
        try:
            P = _poppler()
            it = P.IndexIter.new(self.view.doc)
        except Exception:
            it = None
        if it is not None:
            n = self._walk(it, 0)
        self.outline_empty.set_visible(n == 0)

    def _walk(self, it, level):
        P = _poppler()
        count = 0
        while True:
            act = it.get_action()
            try:
                title = act.any.title or "?"
                dest = None
                if act.type == P.ActionType.GOTO_DEST:
                    dest = act.goto_dest.dest
                    if dest.type == P.DestType.NAMED:
                        dest = self.view.doc.find_dest(dest.named_dest)
                if dest is not None and dest.page_num:
                    row = Gtk.ListBoxRow()
                    row.page, row.top = max(0, dest.page_num - 1), 0
                    lab = Gtk.Label(label=title, xalign=0, wrap=True, margin_start=6 + 12 * level, margin_top=3,
                                    margin_bottom=3)
                    row.set_child(lab)
                    self.outline_list.append(row)
                    count += 1
            except Exception:
                pass
            child = it.get_child()
            if child is not None:
                count += self._walk(child, level + 1)
            if not it.next():
                break
        return count

    # ---------- Treffer
    def show_hits(self, text):
        v = self.view
        while (r := self.hits_list.get_first_child()):
            self.hits_list.remove(r)
        if not text.strip():
            self.hits_head.set_label("Mit Strg+F suchen.")
            if v.editable:
                self.redact_hits.set_visible(False)
            return
        self.hits_head.set_label(f"{len(v.hits)} Treffer für „{text}“" if v.hits else f"Nichts gefunden für „{text}“")
        if v.editable:
            self.redact_hits.set_visible(bool(v.hits))
        pat = re.compile(re.escape(text.strip()), re.I)
        seen = {}
        for idx, (page, _box) in enumerate(v.hits[:500]):
            k = seen.get(page, 0)
            seen[page] = k + 1
            snippet = ""
            try:
                txt = " ".join(v.page(page).get_text().split())
                ms = list(pat.finditer(txt))
                if k < len(ms):
                    m = ms[k]
                    snippet = ("…" if m.start() > 36 else "") + txt[max(0, m.start() - 36):m.end() + 36] + "…"
            except Exception:
                pass
            row = Gtk.ListBoxRow()
            row.index = idx
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=3, margin_bottom=3, margin_start=6,
                          margin_end=6)
            head = Gtk.Label(label=f"Seite {page + 1}", xalign=0)
            head.add_css_class("key")
            body = Gtk.Label(label=snippet, xalign=0, wrap=True)
            body.add_css_class("note")
            box.append(head)
            if snippet:
                box.append(body)
            row.set_child(box)
            self.hits_list.append(row)
        if len(v.hits) > 500:
            self.hits_head.set_label(self.hits_head.get_label() + " (die ersten 500 gelistet)")


def render_thumb(page, width):
    """Miniatur als Gdk.Texture."""
    import cairo
    w, h = page.get_size()
    s = width / max(1.0, w)
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(1, int(w * s)), max(1, int(h * s)))
    cr = cairo.Context(surf)
    cr.set_source_rgb(1, 1, 1)
    cr.paint()
    cr.scale(s, s)
    page.render(cr)
    surf.flush()
    return Gdk.MemoryTexture.new(surf.get_width(), surf.get_height(), Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED,
                                 GLib.Bytes.new(bytes(surf.get_data())), surf.get_stride())


# ======================================================================= Mixin für PdfView
class ExtraMixin:
    """Wird in viewer.PdfView eingemischt. Zustand: side (SidePanel), redactions, form-Cache, Sicherungsstapel."""

    password = None

    def extra_init(self, body):
        """body = Box, in der die Seiten liegen; die Seitenleiste kommt links davon."""
        self.side = SidePanel(self)
        self.side_rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_RIGHT, reveal_child=False)
        self.side_rev.set_child(self.side)
        body.prepend(self.side_rev)
        self.redactions = {}            # Seite -> [(x1, y1, x2, y2)] in Seitenkoordinaten (oben links), noch nicht angewendet
        self.struct_stack, self.struct_redo = [], []
        self.show_forms = True
        self._form_cache = {}
        self.side_btn = self._btn("󰕮", "Seitenleiste: Miniaturen, Gliederung, Treffer (F9)", self.toggle_side)
        self.bar.prepend(self.side_btn)
        if self.editable:
            self._build_extra_menu()
            self._build_redact_bar()
        self.scroll.get_vadjustment().connect("value-changed", lambda *_: self._mark_current())

    def toggle_side(self, show=None):
        show = (not self.side_rev.get_reveal_child()) if show is None else show
        self.side_rev.set_reveal_child(show)
        (self.side_btn.add_css_class if show else self.side_btn.remove_css_class)("active")
        if show and self.doc and not self.side._rows:
            self.side.rebuild()

    def _mark_current(self):
        if self.doc and self.side_rev.get_reveal_child():
            self.side.mark_page(self.current_page())

    def extra_loaded(self):
        """Nach jedem load(): Seitenleiste und Zwischenspeicher neu aufbauen."""
        self.redactions = {}
        self._form_cache = {}
        self._redact_refresh()
        self.side._rows = []
        if self.side_rev.get_reveal_child():
            self.side.rebuild()

    def hits_changed(self, text):
        self.side.show_hits(text)

    def show_hit(self, idx):
        if 0 <= idx < len(self.hits):
            self.hit_i = idx - 1
            self.step_hit(1)

    # ------------------------------------------------------------ Menü „Werkzeuge“
    def _build_extra_menu(self):
        pop = Gtk.Popover(has_arrow=True)
        pop.add_css_class("pdf-pop")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.add_css_class("pdf-pop-box")

        def item(label, cb, tip=""):
            b = Gtk.Button(label=label, tooltip_text=tip, halign=Gtk.Align.FILL)
            b.add_css_class("pb"); b.add_css_class("small")
            b.get_child().set_xalign(0)
            b.connect("clicked", lambda *_: (pop.popdown(), cb()))
            box.append(b)
            return b
        item("󰈔  PDFs anhängen …", lambda: self.pages_append(end=True), "Weitere PDFs ans Ende setzen")
        item("󰏫  Unterschrift ändern …", self.edit_signature)
        item("󰁨  Formularfelder hervorheben", self.toggle_forms, "Felder zum Ausfüllen farbig markieren (an/aus)")
        item("󰈛  Text erkennen (OCR) …", self.ocr_dialog, "Gescannte Seiten durchsuchbar machen")
        item("󰌾  Mit Passwort schützen …", self.protect_dialog)
        self.unprotect_item = item("󰌿  Passwortschutz entfernen", self.unprotect, "")
        pop.set_child(box)
        btn = Gtk.MenuButton(label="󰇙", tooltip_text="Mehr: Seiten anhängen, Unterschrift, Formulare, Schutz, OCR",
                             direction=Gtk.ArrowType.DOWN, focus_on_click=False)
        btn.set_popover(pop)
        btn.add_css_class("pb"); btn.add_css_class("small")
        pop.connect("show", lambda *_: self.unprotect_item.set_visible(bool(self.path) and is_encrypted(self.path)))
        self.action_row.prepend(btn)

    # ------------------------------------------------------------ Datei umbauen (qpdf) + Sicherung
    def _local_path(self):
        if not (self.doc and self.path and os.path.exists(self.path)):
            self.notify("Das geht nur mit einer gespeicherten PDF-Datei.")
            return None
        import netfs
        if netfs.is_remote(self.path):
            self.notify("Seiten ändern geht nur bei lokalen Dateien (Netzlaufwerk: erst kopieren).")
            return None
        if not QPDF:
            self.notify("Dafür braucht Scourge das Programm qpdf (sudo pacman -S qpdf).")
            return None
        return self.path

    def struct_apply(self, label, build, after_page=None, done=None, new_password=False):
        """build(src, out) → (ok, Meldung). Vorher wird gespeichert und eine Sicherung angelegt."""
        path = self._local_path()
        if not path:
            return
        self.commit_edit()
        if self.dirty:
            self.save(quiet=True)
        cur = self.current_page() if after_page is None else after_page
        os.makedirs(VERSION_DIR, exist_ok=True)
        snap = os.path.join(VERSION_DIR, f"{uuid.uuid4().hex[:10]}-{os.path.basename(path)}")
        out = os.path.join(os.path.dirname(path), f".{os.path.basename(path)}.scourge-new")
        self.notify(f"{label} …")

        def work():
            try:
                shutil.copy2(path, snap)
                ok, msg = build(path, out)
            except Exception as e:     # noqa: BLE001
                ok, msg = False, str(e)
            GLib.idle_add(finish, ok, msg)

        def finish(ok, msg):
            if not ok or not os.path.exists(out):
                self.notify(f"{label} ging nicht: {msg}")
                for p in (out, snap):
                    if os.path.exists(p):
                        os.remove(p)
                return False
            self._backup_original(path)
            os.replace(out, path)
            self.struct_stack.append((label, snap))
            self.struct_redo.clear()
            del self.struct_stack[:-12]
            if new_password is not False:
                self.password = new_password
            self._reload_file(cur)
            self.notify(f"{label} – Strg+Z macht es rückgängig")
            if done:
                done()
            return False
        threading.Thread(target=work, daemon=True).start()

    def _reload_file(self, page):
        P = _poppler()
        try:
            doc = P.Document.new_from_file(GLib.filename_to_uri(os.path.abspath(self.path)), self.password)
        except GLib.Error as e:
            self.notify(f"Neu laden ging nicht: {e.message}")
            return
        keep = (self.struct_stack, self.struct_redo, self.password)
        zoom, fit = self.zoom, self.fit
        self.dirty = False
        self.load(doc, self.path)
        self.struct_stack, self.struct_redo, self.password = keep
        if not fit:
            self.set_zoom(zoom)
        GLib.idle_add(lambda: (self.goto(min(page, max(0, len(self.sizes) - 1))), False)[-1])
        if self.on_saved:
            try:
                self.on_saved(self.path)
            except Exception:
                pass

    def struct_undo(self, redo=False):
        src, dst = (self.struct_redo, self.struct_stack) if redo else (self.struct_stack, self.struct_redo)
        if not src or not self.path:
            return False
        label, snap = src.pop()
        if not os.path.exists(snap):
            return False
        cur = self.current_page()
        self.save(quiet=True) if self.dirty else None
        os.makedirs(VERSION_DIR, exist_ok=True)
        keep = os.path.join(VERSION_DIR, f"{uuid.uuid4().hex[:10]}-{os.path.basename(self.path)}")
        shutil.copy2(self.path, keep)
        shutil.copy2(snap, self.path + ".scourge-new")
        os.replace(self.path + ".scourge-new", self.path)
        dst.append((label, keep))
        self._reload_file(cur)
        self.notify(("Wiederholt: " if redo else "Rückgängig: ") + label)
        return True

    # ------------------------------------------------------------ Seiten verwalten
    def _target_pages(self):
        sel = self.side.selected_pages() if self.side_rev.get_reveal_child() else []
        return sel or [self.current_page()]

    def pages_rotate(self, deg):
        pages = self._target_pages()
        n = len(self.sizes)
        order = list(range(1, n + 1))
        self.struct_apply("Seiten drehen", lambda s, o: op_pages(s, o, order, {p + 1: deg for p in pages}, self.password),
                          after_page=pages[0])

    def pages_delete(self):
        pages = set(self._target_pages())
        n = len(self.sizes)
        if len(pages) >= n:
            return self.notify("Es muss mindestens eine Seite übrig bleiben.")
        order = [p + 1 for p in range(n) if p not in pages]
        self.struct_apply("Seiten löschen", lambda s, o: op_pages(s, o, order, None, self.password),
                          after_page=min(pages))

    def pages_duplicate(self):
        pages = self._target_pages()
        order = []
        for p in range(len(self.sizes)):
            order.append(p + 1)
            if p in pages:
                order.append(p + 1)
        self.struct_apply("Seiten verdoppeln", lambda s, o: op_pages(s, o, order, None, self.password),
                          after_page=pages[-1] + 1)

    def pages_move(self, moving, before):
        n = len(self.sizes)
        moving = sorted(set(moving))
        rest = [p for p in range(n) if p not in moving]
        at = sum(1 for p in rest if p < before)
        order = rest[:at] + moving + rest[at:]
        if order == list(range(n)):
            return
        self.struct_apply("Seiten verschieben", lambda s, o: op_pages(s, o, [p + 1 for p in order], None, self.password),
                          after_page=at)

    def _choose_pdfs(self, cb, multiple=True):
        dlg = Gtk.FileDialog(title="PDF auswählen")
        f = Gtk.FileFilter()
        f.set_name("PDF")
        f.add_mime_type("application/pdf")
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(f)
        dlg.set_filters(store)
        if self.path:
            dlg.set_initial_folder(Gio.File.new_for_path(os.path.dirname(self.path)))

        def done(d, res):
            try:
                files = d.open_multiple_finish(res)
            except GLib.Error:
                return
            paths = [files.get_item(i).get_path() for i in range(files.get_n_items())]
            cb([p for p in paths if p])
        dlg.open_multiple(self.get_root(), None, done)

    def pages_append(self, end=False):
        pos = len(self.sizes) if end else self._target_pages()[-1] + 1

        def go(paths):
            if paths:
                self.struct_apply("PDF einfügen", lambda s, o: op_append(s, o, pos, paths, self.password), after_page=pos)
        self._choose_pdfs(go)

    def pages_extract(self):
        pages = self._target_pages()
        if not self._local_path():
            return
        dlg = Gtk.FileDialog(title="Ausgewählte Seiten speichern unter")
        base, _ = os.path.splitext(os.path.basename(self.path))
        dlg.set_initial_name(f"{base} (Auszug).pdf")
        dlg.set_initial_folder(Gio.File.new_for_path(os.path.dirname(self.path)))

        def done(d, res):
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return
            ok, msg = run_qpdf([self.path, "--pages", ".", spec([p + 1 for p in pages]), "--", f.get_path()], self.password)
            self.notify(f"{len(pages)} Seite(n) gespeichert: {os.path.basename(f.get_path())}" if ok else f"Auszug ging nicht: {msg}")
        dlg.save(self.get_root(), None, done)

    def pages_split(self):
        if not self._local_path():
            return
        n = len(self.sizes)
        pop = Gtk.Popover(has_arrow=True)
        pop.add_css_class("pdf-pop")
        pop.set_parent(self.side)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("pdf-pop-box")
        box.append(Gtk.Label(label=f"{n} Seiten in Teile zerlegen: wie viele Seiten pro Teil?", xalign=0, wrap=True))
        spin = Gtk.SpinButton.new_with_range(1, max(1, n - 1), 1)
        spin.set_value(max(1, min(n - 1, n // 2)))
        box.append(spin)
        go = Gtk.Button(label="Zerlegen")
        go.add_css_class("pb")
        box.append(go)
        pop.set_child(box)

        def run(*_):
            pop.popdown()
            per = int(spin.get_value())
            base, _ = os.path.splitext(self.path)
            outdir = f"{base} (Teile)"
            os.makedirs(outdir, exist_ok=True)
            made = 0
            for k, start in enumerate(range(1, n + 1, per), 1):
                end = min(n, start + per - 1)
                ok, _msg = run_qpdf([self.path, "--pages", ".", f"{start}-{end}", "--",
                                     os.path.join(outdir, f"{os.path.basename(base)}-{k:02d}.pdf")], self.password)
                made += ok
            self.notify(f"{made} Teile in {outdir}")
        go.connect("clicked", run)
        pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
        pop.popup()

    # ------------------------------------------------------------ Passwortschutz
    def protect_dialog(self):
        if not self._local_path():
            return
        win = Gtk.Window(title="PDF schützen", transient_for=self.get_root(), modal=True, default_width=340)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=14, margin_bottom=14,
                      margin_start=14, margin_end=14)
        box.append(Gtk.Label(label="Mit diesem Passwort lässt sich die Datei öffnen. Es kann nicht wiederhergestellt "
                                   "werden – bitte merken. Strg+Z macht es rückgängig.", xalign=0, wrap=True))
        e1 = Gtk.PasswordEntry(show_peek_icon=True, placeholder_text="Passwort")
        e2 = Gtk.PasswordEntry(show_peek_icon=True, placeholder_text="Wiederholen")
        err = Gtk.Label(label="", xalign=0)
        err.add_css_class("dirty")
        ok = Gtk.Button(label="Schützen")
        ok.add_css_class("suggested-action")
        for w in (e1, e2, err, ok):
            box.append(w)
        win.set_child(box)

        def go(*_):
            a, b = e1.get_text(), e2.get_text()
            if not a:
                return err.set_label("Bitte ein Passwort eingeben.")
            if a != b:
                return err.set_label("Die Passwörter sind verschieden.")
            win.close()
            self.struct_apply("Passwortschutz setzen",
                              lambda s, o: run_qpdf([s, "--encrypt", a, a, "256", "--", o], self.password),
                              new_password=a)
        ok.connect("clicked", go)
        e2.connect("activate", go)
        win.present()

    def unprotect(self):
        self.struct_apply("Passwortschutz entfernen",
                          lambda s, o: run_qpdf(["--decrypt", s, o], self.password),
                          new_password=None)

    # ------------------------------------------------------------ Unterschrift
    def edit_signature(self, then=None):
        SignDialog(self.get_root(), lambda: (self.notify("Unterschrift gespeichert."), then and then())).present()

    def place_signature(self, i, px, py):
        sig = load_signature()
        if not sig:
            return self.edit_signature(then=lambda: self.place_signature(i, px, py))
        width = max(60.0, self.sign_width)
        height = width * sig["h"] / max(1.0, sig["w"])
        x0, y0 = px - width / 2, py - height / 2
        color = self.colors.get("sign", "#1a3d8f")
        ops = []
        for stroke in sig["strokes"]:
            pts = [(x0 + x * width, y0 + y * height) for x, y in stroke]
            if len(pts) == 1:
                pts = [pts[0], (pts[0][0] + 0.4, pts[0][1] + 0.4)]
            ops.append(("add", i, self._make_ink(i, pts, color, max(0.8, width / 110))))
        if ops:
            self._push(ops)
            self._rerender(i)

    sign_width = 150.0

    # ------------------------------------------------------------ Schwärzen
    def _build_redact_bar(self):
        self.redact_bar = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        row = Gtk.Box(spacing=8, margin_bottom=4)
        row.add_css_class("redact-bar")
        self.redact_lbl = Gtk.Label(label="", hexpand=True, xalign=0)
        self.redact_lbl.add_css_class("dirty")
        apply = Gtk.Button(label="󰏫  Endgültig schwärzen …")
        apply.add_css_class("pb"); apply.add_css_class("small")
        apply.connect("clicked", lambda *_: self.redact_apply())
        drop = Gtk.Button(label="Verwerfen")
        drop.add_css_class("pb"); drop.add_css_class("small")
        drop.connect("clicked", lambda *_: self.redact_clear())
        for w in (self.redact_lbl, apply, drop):
            row.append(w)
        self.redact_bar.set_child(row)
        self.insert_child_after(self.redact_bar, self.get_first_child().get_next_sibling())

    def _redact_refresh(self):
        if not self.editable:
            return
        n = sum(len(v) for v in self.redactions.values())
        self.redact_bar.set_reveal_child(n > 0)
        self.redact_lbl.set_label(f"{n} Bereich(e) markiert" if n else "")
        self.redact_lbl.set_tooltip_text("Erst „Endgültig schwärzen“ entfernt den Inhalt wirklich aus der Datei")
        self._redraw_all()

    def redact_add(self, i, box):
        x1, y1, x2, y2 = box
        self.redactions.setdefault(i, []).append((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))
        self._redact_refresh()

    def redact_hits(self):
        for page, (x1, y1, x2, y2) in self.hits:
            self.redactions.setdefault(page, []).append((x1 - 1, y1 - 1, x2 + 1, y2 + 1))
        self._redact_refresh()
        self.set_tool("redact")

    def redact_clear(self):
        self.redactions = {}
        self._redact_refresh()

    def redact_undo(self):
        for p in sorted(self.redactions, reverse=True):
            if self.redactions[p]:
                self.redactions[p].pop()
                if not self.redactions[p]:
                    del self.redactions[p]
                self._redact_refresh()
                return True
        return False

    def redact_apply(self):
        if not self._local_path():
            return
        pages = {p: list(b) for p, b in self.redactions.items() if b}
        if not pages:
            return
        dlg = Gtk.AlertDialog(message=f"{sum(len(b) for b in pages.values())} Bereich(e) auf {len(pages)} Seite(n) endgültig schwärzen?",
                              detail="Diese Seiten werden zu Bildern: der markierte Inhalt ist danach wirklich weg (auch nicht "
                                     "mehr kopierbar), der übrige Text dieser Seiten aber ebenfalls nicht mehr durchsuchbar. "
                                     "Mit Strg+Z kannst du es danach zurücknehmen.",
                              buttons=["Abbrechen", "Schwärzen"], cancel_button=0, default_button=0)

        def done(d, res):
            try:
                if d.choose_finish(res) != 1:
                    return
            except GLib.Error:
                return
            self.redactions = {}
            self._redact_refresh()
            first = min(pages)

            def build(src, out):
                tmp = tempfile.mkdtemp(prefix="scourge-redact-")
                try:
                    repl = {}
                    for p, boxes in pages.items():
                        f = os.path.join(tmp, f"p{p}.pdf")
                        self._flatten_page(p, boxes, f)
                        repl[p + 1] = f
                    return op_replace(src, out, repl, self.password)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
            self.struct_apply("Schwärzen", build, after_page=first)
        dlg.choose(self.get_root(), None, done)

    def _flatten_page(self, i, boxes, out_pdf, dpi=200):
        """Seite als Bild (mit schwarzen Kästen) in eine einseitige PDF – Text/Vektoren darunter sind dann weg."""
        import cairo
        import io
        page = self.page(i)
        w, h = page.get_size()
        s = dpi / 72.0
        surf = cairo.ImageSurface(cairo.FORMAT_RGB24, max(1, int(w * s)), max(1, int(h * s)))
        cr = cairo.Context(surf)
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
        cr.scale(s, s)
        page.render(cr)
        cr.set_source_rgb(0, 0, 0)
        for x1, y1, x2, y2 in boxes:
            cr.rectangle(x1, y1, x2 - x1, y2 - y1)
        cr.fill()
        surf.flush()
        pdf = cairo.PDFSurface(out_pdf, w, h)
        c2 = cairo.Context(pdf)
        try:
            from PIL import Image
            img = Image.frombuffer("RGBA", (surf.get_width(), surf.get_height()), bytes(surf.get_data()), "raw", "BGRA", surf.get_stride(), 1).convert("RGB")
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=88)
            surf.set_mime_data("image/jpeg", buf.getvalue())
        except Exception:
            pass
        c2.scale(1 / s, 1 / s)
        c2.set_source_surface(surf, 0, 0)
        c2.paint()
        pdf.finish()

    # ------------------------------------------------------------ OCR
    @staticmethod
    def ocr_langs():
        exe = shutil.which("tesseract")
        if not exe:
            return None
        try:
            have = subprocess.run([exe, "--list-langs"], capture_output=True, text=True, timeout=20).stdout.split()[1:]
        except (OSError, subprocess.TimeoutExpired):
            return None
        want = [l for l in ("deu", "eng") if l in have]
        return "+".join(want) or (have[0] if have else None)

    def ocr_dialog(self):
        langs = self.ocr_langs()
        if not langs:
            dlg = Gtk.AlertDialog(message="Texterkennung braucht tesseract",
                                  detail="Einmal im Terminal installieren:\n\nsudo pacman -S tesseract tesseract-data-deu tesseract-data-eng\n\n"
                                         "(Debian/Ubuntu: sudo apt install tesseract-ocr tesseract-ocr-deu)",
                                  buttons=["OK"])
            return dlg.choose(self.get_root(), None, None)
        if not self._local_path():
            return
        todo = [i for i in range(len(self.sizes)) if not (self.page(i).get_text() or "").strip()]
        if not todo:
            return self.notify("Alle Seiten haben schon Text – nichts zu erkennen.")

        def build(src, out):
            tmp = tempfile.mkdtemp(prefix="scourge-ocr-")
            try:
                repl = {}
                for i in todo:
                    png = os.path.join(tmp, f"p{i}.png")
                    self._render_png(i, png, 300)
                    base = os.path.join(tmp, f"o{i}")
                    r = subprocess.run([shutil.which("tesseract"), png, base, "-l", langs, "pdf"],
                                       capture_output=True, text=True, timeout=600)
                    if r.returncode != 0 or not os.path.exists(base + ".pdf"):
                        return False, (r.stderr.strip().splitlines() or ["tesseract-Fehler"])[-1]
                    repl[i + 1] = base + ".pdf"
                return op_replace(src, out, repl, self.password)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        self.struct_apply(f"Text erkennen ({len(todo)} Seiten)", build)

    def _render_png(self, i, path, dpi):
        import cairo
        page = self.page(i)
        w, h = page.get_size()
        s = dpi / 72.0
        surf = cairo.ImageSurface(cairo.FORMAT_RGB24, int(w * s), int(h * s))
        cr = cairo.Context(surf)
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
        cr.scale(s, s)
        page.render(cr)
        surf.write_to_png(path)

    # ------------------------------------------------------------ Formulare
    def toggle_forms(self):
        self.show_forms = not self.show_forms
        self._redraw_all()
        self.notify("Formularfelder werden hervorgehoben." if self.show_forms else "Formularfelder nicht mehr hervorgehoben.")

    def form_fields(self, i):
        if i not in self._form_cache:
            try:
                h = self.sizes[i][1]
                out = []
                for m in self.page(i).get_form_field_mapping():
                    r = m.area
                    out.append((m.field, (r.x1, h - r.y2, r.x2, h - r.y1)))
                self._form_cache[i] = out
            except Exception:
                self._form_cache[i] = []
        return self._form_cache[i]

    def has_forms(self):
        return any(self.form_fields(i) for i in range(min(len(self.sizes), 40)))

    def form_click(self, i, px, py):
        """Klick auf ein Formularfeld (nur bearbeitbar). True, wenn behandelt."""
        if not self.editable:
            return False
        P = _poppler()
        for field, (x1, y1, x2, y2) in self.form_fields(i):
            if not (x1 <= px <= x2 and y1 <= py <= y2):
                continue
            try:
                if field.is_read_only():
                    continue
                t = field.get_field_type()
                if t == P.FormFieldType.BUTTON:
                    bt = field.button_get_button_type()
                    if bt in (P.FormButtonType.CHECK, P.FormButtonType.RADIO):
                        field.button_set_state(not field.button_get_state() or bt == P.FormButtonType.RADIO)
                        self._form_changed(i)
                        return True
                elif t == P.FormFieldType.TEXT:
                    self._form_text(i, field, (x1, y1, x2, y2))
                    return True
                elif t == P.FormFieldType.CHOICE:
                    self._form_choice(i, field, (x1, y1, x2, y2))
                    return True
            except Exception as e:     # noqa: BLE001
                self.notify(f"Feld ließ sich nicht ändern: {e}")
        return False

    def _form_changed(self, i):
        self.dirty = True
        self._sync_tools()
        self._rerender(i)

    def _anchor_pop(self, pop, i, box):
        z = self.zoom
        pop.set_parent(self.areas[i])
        pop.set_pointing_to(Gdk.Rectangle())
        r = Gdk.Rectangle()
        r.x, r.y, r.width, r.height = int(box[0] * z), int(box[1] * z), max(1, int((box[2] - box[0]) * z)), max(1, int((box[3] - box[1]) * z))
        pop.set_pointing_to(r)
        pop.connect("closed", lambda p: GLib.idle_add(p.unparent))

    def _form_text(self, i, field, box):
        P = _poppler()
        multi = field.text_get_text_type() == P.FormTextType.MULTILINE
        pop = Gtk.Popover(has_arrow=True, autohide=True)
        pop.add_css_class("pdf-pop")
        self._anchor_pop(pop, i, box)
        if multi:
            tv = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, width_request=260, height_request=110)
            tv.get_buffer().set_text(field.text_get_text() or "")
            pop.set_child(tv)
            get = lambda: tv.get_buffer().get_text(tv.get_buffer().get_start_iter(), tv.get_buffer().get_end_iter(), False)
        else:
            ent = Gtk.Entry(text=field.text_get_text() or "", width_chars=24)
            ent.connect("activate", lambda *_: pop.popdown())
            pop.set_child(ent)
            get = ent.get_text
            GLib.idle_add(lambda: (ent.grab_focus(), False)[-1])

        def closed(*_):
            new = get()
            if new != (field.text_get_text() or ""):
                field.text_set_text(new)
                self._form_changed(i)
        pop.connect("closed", closed)
        pop.popup()

    def _form_choice(self, i, field, box):
        pop = Gtk.Popover(has_arrow=True, autohide=True)
        pop.add_css_class("pdf-pop")
        self._anchor_pop(pop, i, box)
        lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        for k in range(field.choice_get_n_items()):
            row = Gtk.ListBoxRow()
            row.k = k
            lab = Gtk.Label(label=("● " if field.choice_is_item_selected(k) else "   ") + (field.choice_get_item(k) or ""),
                            xalign=0, margin_start=6, margin_end=6, margin_top=3, margin_bottom=3)
            row.set_child(lab)
            lb.append(row)

        def pick(_lb, row):
            field.choice_unselect_all()
            field.choice_select_item(row.k)
            pop.popdown()
            self._form_changed(i)
        lb.connect("row-activated", pick)
        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True, max_content_height=220)
        sc.set_child(lb)
        pop.set_child(sc)
        pop.popup()

    # ------------------------------------------------------------ Zeichnen
    def extra_active(self, i):
        return bool(self.redactions.get(i) or (self.live and self.live[0] == i and self.live[1] == "redact")
                    or (self.show_forms and self.editable and self.form_fields(i)))

    def extra_draw(self, cr, z, i):
        if self.show_forms and self.editable:
            cr.set_source_rgba(0.24, 0.52, 1.0, 0.13)
            for _f, (x1, y1, x2, y2) in self.form_fields(i):
                cr.rectangle(x1 * z, y1 * z, (x2 - x1) * z, (y2 - y1) * z)
            cr.fill()
        for x1, y1, x2, y2 in self.redactions.get(i, ()):
            cr.rectangle(x1 * z, y1 * z, (x2 - x1) * z, (y2 - y1) * z)
            cr.set_source_rgba(0, 0, 0, 0.88)
            cr.fill_preserve()
            cr.set_source_rgba(0.9, 0.2, 0.2, 1)
            cr.set_line_width(1.5)
            cr.stroke()
        if self.live and self.live[0] == i and self.live[1] == "redact":
            x1, y1, x2, y2 = self.live[2]
            cr.rectangle(min(x1, x2) * z, min(y1, y2) * z, abs(x2 - x1) * z, abs(y2 - y1) * z)
            cr.set_source_rgba(0, 0, 0, 0.5)
            cr.fill_preserve()
            cr.set_source_rgba(0.9, 0.2, 0.2, 1)
            cr.set_line_width(1.5)
            cr.stroke()
