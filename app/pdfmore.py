"""PDF-Editor, Teil 2: Antworten auf Kommentare, Hover-Popups, Drucken, Eigenschaften, Lesezeichen, Zuschneiden,
Seitenzahlen/Kopf-/Fußzeile, Wasserzeichen. Wird als MoreMixin in viewer.PdfView eingemischt (neben pdfextra.ExtraMixin)."""
import getpass
import json
import math
import os
import subprocess
import tempfile
import time

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo

from pdfextra import QPDF, _poppler, page_count, run_qpdf

REPLY = "⁣"          # unsichtbares Zeichen vor dem Text einer Antwort (Poppler kann /IRT nicht schreiben)
SETTINGS = os.path.expanduser("~/.config/scourge/settings.json")
KINDS = {"TEXT": "Notiz", "HIGHLIGHT": "Markierung", "UNDERLINE": "Unterstreichung", "STRIKE_OUT": "Durchstreichung",
         "SQUIGGLY": "Wellenlinie", "FREE_TEXT": "Textfeld", "CARET": "Einfügemarke", "STAMP": "Stempel",
         "SQUARE": "Rahmen", "CIRCLE": "Kreis", "LINE": "Linie", "INK": "Zeichnung"}


# ======================================================================= Name für Kommentare
def author_name():
    try:
        with open(SETTINGS) as f:
            n = (json.load(f).get("pdf_author") or "").strip()
        if n:
            return n
    except (OSError, ValueError):
        pass
    try:
        return getpass.getuser()
    except Exception:
        return "Ich"


def set_author_name(name):
    try:
        with open(SETTINGS) as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d["pdf_author"] = name.strip()
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    with open(SETTINGS, "w") as f:
        json.dump(d, f, indent=1)


def fmt_date(d):
    """PDF-Datum (D:20260131120000+01'00' oder ISO) → 31.01.2026 12:00."""
    import re
    m = re.search(r"(\d{4})-?(\d{2})-?(\d{2})[T ]?(\d{2})?:?(\d{2})?", d or "")
    if not m:
        return d or ""
    y, mo, da, hh, mi = m.groups()
    return f"{da}.{mo}.{y}" + (f" {hh}:{mi}" if hh and mi else "")


# ======================================================================= Kommentare lesen / Antworten
def comment_items(view, i):
    """Alle lesbaren Anmerkungen der Seite i: dicts mit page, top, box (oben links), kind, author, date, text, quote,
    reply (True = Antwort), annot."""
    from viewer import parse_label
    P = _poppler()
    T = P.AnnotType
    skip = {T.LINK, T.WIDGET, T.POPUP}
    out = []
    try:
        page = view.page(i)
        h = view.sizes[i][1]
        maps = page.get_annot_mapping()
    except Exception:
        return out
    for m in maps:
        a = m.annot
        try:
            t = a.get_annot_type()
            if t in skip:
                continue
            text = (a.get_contents() or "").strip()
            if t == T.INK and text.startswith("SCU;"):
                continue
            lab = parse_label(a) if t == T.FREE_TEXT else None
            if lab:
                continue
            reply = t == T.TEXT and text.startswith(REPLY)
            if reply:
                text = text.lstrip(REPLY).strip()
            author = date = ""
            try:
                author = (a.get_label() or "").strip() if not parse_label(a) else ""
                d = a.get_modified()
                date = d if isinstance(d, str) else ""
            except Exception:
                pass
            quote = ""
            if t in (T.HIGHLIGHT, T.UNDERLINE, T.STRIKE_OUT, T.SQUIGGLY):
                r = P.Rectangle()
                r.x1, r.y1, r.x2, r.y2 = m.area.x1, h - m.area.y2, m.area.x2, h - m.area.y1
                quote = " ".join((page.get_text_for_area(r) or "").split())
            name = next((k for k in KINDS if getattr(T, k) == t), "")
            box = (m.area.x1, h - m.area.y2, m.area.x2, h - m.area.y1)
            out.append({"page": i, "top": box[1], "box": box, "kind": name, "author": author, "date": date,
                        "text": text, "quote": quote, "reply": reply, "annot": a})
        except Exception:
            continue
    return out


def threads(items):
    """[(Eltern, [Antworten])] – eine Antwort gehört zu dem Eintrag, dessen obere linke Ecke sie teilt."""
    parents = [it for it in items if not it["reply"]]
    replies = [it for it in items if it["reply"]]
    by = {id(p): [] for p in parents}
    orphans = []
    for r in replies:
        for p in parents:
            if abs(p["box"][0] - r["box"][0]) < 1.5 and abs(p["box"][1] - r["box"][1]) < 1.5:
                by[id(p)].append(r)
                break
        else:
            orphans.append(r)
    out = [(p, by[id(p)]) for p in parents]
    out += [(o, []) for o in orphans]
    out.sort(key=lambda t: (t[0]["page"], round(t[0]["top"]), t[0]["box"][0]))
    return out


def _labels(w):
    """Alle Gtk.Label unterhalb von w."""
    out, c = [], w.get_first_child()
    while c is not None:
        if isinstance(c, Gtk.Label):
            out.append(c)
        out += _labels(c)
        c = c.get_next_sibling()
    return out


def thread_widget(parent, replies, jump=None, on_reply=None, on_delete=None):
    """Eine Kommentar-Karte: Kopfzeile (Klick = springen), Zitat, Text, Antworten, Antworten-Knopf. Texte sind markierbar."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=5, margin_bottom=5, margin_start=6,
                  margin_end=6)

    def block(it, indent=0, is_reply=False):
        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, margin_start=indent)
        who = f" · {it['author']}" if it["author"] else ""
        if is_reply:
            head = Gtk.Label(label=f"↳ {it['author'] or 'Antwort'}" + (f" · {fmt_date(it['date'])}" if it["date"] else ""),
                             xalign=0, wrap=True)
        else:
            head = Gtk.Label(label=f"Seite {it['page'] + 1} · {KINDS.get(it['kind'], 'Anmerkung')}{who}", xalign=0, wrap=True)
        head.add_css_class("key")
        row = Gtk.Box(spacing=4)
        head.set_hexpand(True)
        row.append(head)
        if is_reply and on_delete:
            x = Gtk.Button(label="✕", tooltip_text="Antwort löschen", focus_on_click=False)
            x.add_css_class("pb"); x.add_css_class("small")
            x.connect("clicked", lambda *_, it=it: on_delete(it))
            row.append(x)
        b.append(row)
        if jump and not is_reply:
            g = Gtk.GestureClick()
            g.connect("released", lambda *_: jump(it))
            head.add_controller(g)
            head.set_cursor_from_name("pointer")
        if it["quote"]:
            q = Gtk.Label(label="„" + (it["quote"][:160] + "…" if len(it["quote"]) > 160 else it["quote"]) + "“",
                          xalign=0, wrap=True, selectable=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
            q.add_css_class("note")
            b.append(q)
        if it["text"]:
            b.append(Gtk.Label(label=it["text"], xalign=0, wrap=True, selectable=True, wrap_mode=Pango.WrapMode.WORD_CHAR))
        if it["date"] and not is_reply:
            dl = Gtk.Label(label=fmt_date(it["date"]), xalign=0)
            dl.add_css_class("note")
            b.append(dl)
        return b
    box.append(block(parent))
    for r in replies:
        box.append(block(r, 12, True))
    if on_reply:
        ent = Gtk.Entry(placeholder_text="Antwort schreiben …", margin_start=12, visible=False)
        btn = Gtk.Button(label="↩  Antworten", halign=Gtk.Align.START, margin_start=12, focus_on_click=False)
        btn.add_css_class("pb"); btn.add_css_class("small")

        def show(*_):
            ent.set_visible(True)
            btn.set_visible(False)
            ent.grab_focus()

        def send(*_):
            t = ent.get_text().strip()
            if t:
                on_reply(parent, t)

        def key(ctrl, kv, *_):
            if kv == Gdk.KEY_Escape:
                ent.set_text("")
                ent.set_visible(False)
                btn.set_visible(True)
                return True
            return False
        btn.connect("clicked", show)
        ent.connect("activate", send)
        kc = Gtk.EventControllerKey()
        kc.connect("key-pressed", key)
        ent.add_controller(kc)
        box.append(btn)
        box.append(ent)
    return box


# ======================================================================= qpdf-JSON
def op_json(src, out, mutate, password=None):
    """qpdf --json=2 lesen, mutate(objs, pages, info) → {"obj:N 0 R": {"value": …}} ändern, per --update-from-json schreiben."""
    if not QPDF:
        return False, "qpdf fehlt (sudo pacman -S qpdf)"
    pw = [f"--password={password}"] if password else []
    try:
        r = subprocess.run([QPDF] + pw + ["--json=2", "--json-key=qpdf", "--json-key=pages", src],
                           capture_output=True, text=True, timeout=300)
        if r.returncode not in (0, 3):
            return False, (r.stderr or "qpdf-JSON ging nicht").strip().splitlines()[-1]
        data = json.loads(r.stdout)
        head, objs = data["qpdf"][0], data["qpdf"][1]
        pages = [p["object"] for p in data["pages"]]
        upd = mutate(objs, pages, head)
        if not upd:
            return False, "nichts zu ändern"
        head2 = {"jsonversion": 2, "pdfversion": head.get("pdfversion", "1.7"),
                 "maxobjectid": max(head.get("maxobjectid", 0), max(int(k[4:].split()[0]) for k in upd))}
        doc = {"version": 2, "parameters": {"decodelevel": "generalized"}, "qpdf": [head2, upd]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(doc, f)
            tmp = f.name
        try:
            return run_qpdf(pw[:] + [src, f"--update-from-json={tmp}", out])
        finally:
            os.remove(tmp)
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as e:
        return False, str(e)


def read_outline(doc):
    """Gliederung als Baum: [{"title", "page" (1-basiert), "top" (PDF-y oder None), "children": [...]}]."""
    P = _poppler()

    def walk(it):
        nodes = []
        while True:
            try:
                act = it.get_action()
                title = act.any.title or "?"
                dest = None
                if act.type == P.ActionType.GOTO_DEST:
                    dest = act.goto_dest.dest
                    if dest.type == P.DestType.NAMED:
                        dest = doc.find_dest(dest.named_dest)
                page = dest.page_num if dest is not None else 0
                top = dest.top if (dest is not None and dest.top) else None      # change_top meldet Poppler hier fälschlich 0
                ch = it.get_child()
                kids = walk(ch) if ch is not None else []
                if page:
                    nodes.append({"title": title, "page": page, "top": top, "children": kids})
                else:
                    nodes.extend(kids)
            except Exception:
                pass
            if not it.next():
                break
        return nodes
    try:
        it = P.IndexIter.new(doc)
    except Exception:
        it = None
    return walk(it) if it is not None else []


def op_outline(src, out, tree, password=None):
    def mutate(objs, pages, head):
        nid = [head.get("maxobjectid", 0)]
        upd = {}

        def new():
            nid[0] += 1
            return nid[0]

        def count(nodes):
            return sum(1 + count(n["children"]) for n in nodes)

        def build(nodes, parent):
            ids = [new() for _ in nodes]
            for k, (n, oid) in enumerate(zip(nodes, ids)):
                d = {"/Title": "u:" + n["title"], "/Parent": parent}
                if 1 <= n["page"] <= len(pages):
                    d["/Dest"] = [pages[n["page"] - 1], "/XYZ", None, n["top"], None]
                if k > 0:
                    d["/Prev"] = f"{ids[k - 1]} 0 R"
                if k < len(ids) - 1:
                    d["/Next"] = f"{ids[k + 1]} 0 R"
                if n["children"]:
                    cids = build(n["children"], f"{oid} 0 R")
                    d["/First"], d["/Last"] = f"{cids[0]} 0 R", f"{cids[-1]} 0 R"
                    d["/Count"] = count(n["children"])
                upd[f"obj:{oid} 0 R"] = {"value": d}
            return ids
        root_ref = objs["trailer"]["value"]["/Root"]
        cat = dict(objs[f"obj:{root_ref}"]["value"])
        if tree:
            rid = new()
            ids = build(tree, f"{rid} 0 R")
            upd[f"obj:{rid} 0 R"] = {"value": {"/Type": "/Outlines", "/First": f"{ids[0]} 0 R", "/Last": f"{ids[-1]} 0 R",
                                                "/Count": count(tree)}}
            cat["/Outlines"] = f"{rid} 0 R"
            cat["/PageMode"] = "/UseOutlines"
        else:
            cat.pop("/Outlines", None)
        upd[f"obj:{root_ref}"] = {"value": cat}
        return upd
    return op_json(src, out, mutate, password)


def op_crop(src, out, margins, password=None):
    """margins = {Seite (1-basiert): (links, oben, rechts, unten) in pt, sichtbar gesehen} oder None → Zuschnitt aufheben."""
    def mutate(objs, pages, head):
        upd = {}

        def attr(val, key):
            seen = 0
            while val is not None and seen < 30:
                if key in val:
                    return val[key]
                par = val.get("/Parent")
                val = objs.get(f"obj:{par}", {}).get("value") if par else None
                seen += 1
            return None
        for nr, mg in margins.items():
            ref = pages[nr - 1]
            d = dict(objs[f"obj:{ref}"]["value"])
            if mg is None:
                d.pop("/CropBox", None)
            else:
                media = attr(d, "/MediaBox") or [0, 0, 612, 792]
                box = attr(d, "/CropBox") or media
                x0, x1 = sorted((box[0], box[2]))
                y0, y1 = sorted((box[1], box[3]))
                L, T, R, B = mg
                rot = int(attr(d, "/Rotate") or 0) % 360
                if rot == 90:
                    x0, y1, x1, y0 = x0 + T, y1 - R, x1 - B, y0 + L
                elif rot == 180:
                    x0, y1, x1, y0 = x0 + R, y1 - B, x1 - L, y0 + T
                elif rot == 270:
                    x0, y1, x1, y0 = x0 + B, y1 - L, x1 - T, y0 + R
                else:
                    x0, y1, x1, y0 = x0 + L, y1 - T, x1 - R, y0 + B
                if x1 - x0 < 20 or y1 - y0 < 20:
                    raise ValueError("Der Zuschnitt ist zu groß – es bliebe nichts übrig.")
                d["/CropBox"] = [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)]
            upd[f"obj:{ref}"] = {"value": d}
        return upd
    return op_json(src, out, mutate, password)


# ======================================================================= Überlagerung (Zahlen, Text, Wasserzeichen)
def op_overlay(src, out, sizes, draw, password=None):
    """Zeichnet je Seite per draw(cr, nr, w, h) auf eine durchsichtige Seite und legt alle über das PDF."""
    import cairo
    if not QPDF:
        return False, "qpdf fehlt (sudo pacman -S qpdf)"
    fd, ov = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    try:
        w0, h0 = sizes[0]
        surf = cairo.PDFSurface(ov, w0, h0)
        cr = cairo.Context(surf)
        for nr, (w, h) in enumerate(sizes):
            surf.set_size(w, h)
            cr.save()
            draw(cr, nr, w, h)
            cr.restore()
            surf.show_page()
        surf.finish()
        return run_qpdf([src, "--overlay", ov, "--", out], password)
    finally:
        try:
            os.remove(ov)
        except OSError:
            pass


def _layout(cr, text, size, bold=False):
    lay = PangoCairo.create_layout(cr)
    lay.set_font_description(Pango.FontDescription(f"Sans {'Bold ' if bold else ''}{size}"))
    lay.set_text(text, -1)
    return lay


# ======================================================================= Mixin
class MoreMixin:
    _hov = None
    _hov_pop = None
    _hov_key = None
    _hov_timer = 0
    _hov_last = 0

    # ---------------------------------------------------------- Hover-Popups
    def hov_invalidate(self, i=None):
        if self._hov is None:
            self._hov = {}
        if i is None:
            self._hov.clear()
        else:
            self._hov.pop(i, None)

    def _hov_threads(self, i):
        if self._hov is None:
            self._hov = {}
        if i not in self._hov:
            self._hov[i] = [t for t in threads(comment_items(self, i)) if t[0]["text"] or t[1]]
        return self._hov[i]

    def thread_at(self, i, px, py):
        """Kommentar-Verlauf (Eltern, Antworten) unter dem Punkt (Seitenkoordinaten) oder None."""
        for t in reversed(self._hov_threads(i)):
            b = t[0]["box"]
            if b[0] - 3 <= px <= b[2] + 3 and b[1] - 3 <= py <= b[3] + 3:
                return t
        return None

    def open_thread(self, i, th):
        """Klick auf eine Notiz/Markierung mit Kommentar: Verlauf in einem Popover, dessen Texte sich markieren und
        kopieren lassen (das Hover-Popup ist nur zum Ansehen)."""
        self.hov_hide()
        parent, replies = th
        pop = Gtk.Popover(has_arrow=True, autohide=True, position=Gtk.PositionType.BOTTOM)
        pop.add_css_class("pdf-pop")
        pop.add_css_class("formats")
        inner = thread_widget(parent, replies, None, self.add_reply if self.editable else None,
                              self.delete_reply if self.editable else None)
        inner.set_size_request(340, -1)
        for lab in _labels(inner):
            lab.set_max_width_chars(46)
        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True, max_content_height=380)
        sc.set_child(inner)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.add_css_class("pdf-pop-box")
        box.append(sc)
        row = Gtk.Box(spacing=4, margin_start=6, margin_end=6)

        def small(label, tip, cb):
            b = Gtk.Button(label=label, tooltip_text=tip, focus_on_click=False)
            b.add_css_class("pb"); b.add_css_class("small")
            b.connect("clicked", lambda *_: cb())
            row.append(b)
        allt = "\n\n".join(x for it in [parent] + list(replies) for x in (it["quote"] if not it["reply"] else "", it["text"]) if x)
        small("󰆏  alles kopieren", "Zitat und Kommentare in die Zwischenablage",
              lambda: (self.get_clipboard().set(allt), self.notify("Kommentar kopiert."), pop.popdown()))
        if self.editable and parent["kind"] == "TEXT" and parent["annot"] is not None:
            a = parent["annot"]
            x, y = parent["box"][0], parent["box"][1]
            small("󰏫  bearbeiten", "Notiz ändern", lambda: (pop.popdown(), self._open_input("note", i, x, y, a)))
        box.append(row)
        pop.set_child(box)
        pop.set_parent(self.areas[i])
        x1, y1, x2, y2 = parent["box"]
        z = self.zoom
        rc = Gdk.Rectangle()
        rc.x, rc.y, rc.width, rc.height = int(x1 * z), int(y1 * z), max(1, int((x2 - x1) * z)), max(1, int((y2 - y1) * z))
        pop.set_pointing_to(rc)
        pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
        pop.popup()

    def hov_motion(self, i, px, py):
        """Vom Zeiger über Seite i bei (px, py) in Seitenkoordinaten aufgerufen."""
        if not self.doc or self._drag or self.edit or self.tool == "erase":
            return self.hov_hide()
        now = time.monotonic()
        if now - self._hov_last < 0.04:
            return
        self._hov_last = now
        hit = self.thread_at(i, px, py)
        key = (i, round(hit[0]["box"][0]), round(hit[0]["top"])) if hit else None
        if key == self._hov_key:
            return
        self.hov_hide()
        if hit:
            self._hov_key = key
            self._hov_timer = GLib.timeout_add(320, self._hov_show, i, hit)

    def hov_hide(self, *_):
        if self._hov_timer:
            GLib.source_remove(self._hov_timer)
            self._hov_timer = 0
        self._hov_key = None
        pop, self._hov_pop = self._hov_pop, None
        if pop is not None:
            pop.popdown()

    def _hov_show(self, i, th):
        self._hov_timer = 0
        if i >= len(self.areas):
            return False
        parent, replies = th
        pop = Gtk.Popover(has_arrow=False, autohide=False, can_target=False, position=Gtk.PositionType.BOTTOM)
        pop.add_css_class("pdf-pop")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("pdf-pop-box")

        def line(it, reply=False):
            b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, margin_start=10 if reply else 0)
            who = it["author"] or ("Antwort" if reply else KINDS.get(it["kind"], ""))
            d = fmt_date(it["date"])
            h = Gtk.Label(label=("↳ " if reply else "") + who + (f" · {d}" if d else ""), xalign=0)
            h.add_css_class("key")
            b.append(h)
            if it["quote"] and not reply:
                q = Gtk.Label(label="„" + (it["quote"][:100] + "…" if len(it["quote"]) > 100 else it["quote"]) + "“",
                              xalign=0, wrap=True, max_width_chars=38)
                q.add_css_class("note")
                b.append(q)
            if it["text"]:
                b.append(Gtk.Label(label=it["text"], xalign=0, wrap=True, max_width_chars=38,
                                   wrap_mode=Pango.WrapMode.WORD_CHAR))
            return b
        box.append(line(parent))
        for r in replies:
            box.append(line(r, True))
        pop.set_child(box)
        pop.set_parent(self.areas[i])
        x1, y1, x2, y2 = parent["box"]
        z = self.zoom
        rc = Gdk.Rectangle()
        rc.x, rc.y, rc.width, rc.height = int(x1 * z), int(y1 * z), max(1, int((x2 - x1) * z)), max(1, int((y2 - y1) * z))
        pop.set_pointing_to(rc)
        pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
        self._hov_pop = pop
        pop.popup()
        return False

    # ---------------------------------------------------------- Antworten
    def add_reply(self, parent, text):
        P = _poppler()
        i = parent["page"]
        h = self.sizes[i][1]
        x1, top = parent["box"][0], parent["box"][1]
        a = P.AnnotText.new(self.doc, _rect(x1, h - top - 20, x1 + 20, h - top))
        a.set_icon("Comment")
        try:
            c = parent["annot"].get_color()
            if c:
                a.set_color(c)
        except Exception:
            pass
        a.set_label(author_name())
        a.set_contents(REPLY + text)
        self.page(i).add_annot(a)
        self._push([("add", i, a)])
        self._rerender(i)

    def delete_reply(self, item):
        i = item["page"]
        self.page(i).remove_annot(item["annot"])
        self._push([("remove", i, item["annot"])])
        self._rerender(i)

    # ---------------------------------------------------------- Drucken
    def print_dialog(self):
        if not self.doc:
            return
        self.commit_edit()
        op = Gtk.PrintOperation()
        op.set_n_pages(len(self.sizes))
        op.set_job_name(os.path.basename(self.path) if self.path else "PDF")
        op.set_embed_page_setup(True)
        op.set_use_full_page(False)

        def setup(op_, ctx, nr, ps):
            w, h = self.sizes[nr]
            ps.set_orientation(Gtk.PageOrientation.LANDSCAPE if w > h else Gtk.PageOrientation.PORTRAIT)

        def draw(op_, ctx, nr):
            cr = ctx.get_cairo_context()
            pw, ph = ctx.get_width(), ctx.get_height()
            page = self.page(nr)
            w, h = page.get_size()
            s = min(pw / w, ph / h)
            cr.translate((pw - w * s) / 2, (ph - h * s) / 2)
            cr.scale(s, s)
            page.render_for_printing(cr)
        op.connect("request-page-setup", setup)
        op.connect("draw-page", draw)
        op.connect("done", lambda o, res: self.notify("Druckauftrag gesendet.")
                   if res == Gtk.PrintOperationResult.APPLY else None)
        try:
            op.run(Gtk.PrintOperationAction.PRINT_DIALOG, self.get_root())
        except GLib.Error as e:
            self.notify(f"Drucken ging nicht: {e.message}")

    # ---------------------------------------------------------- Eigenschaften
    def props_dialog(self):
        if not self.doc:
            return
        d = self.doc
        win = Gtk.Window(title="Eigenschaften", transient_for=self.get_root(), modal=True, default_width=420)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=14, margin_bottom=14,
                      margin_start=14, margin_end=14)
        grid = Gtk.Grid(column_spacing=10, row_spacing=6)
        ents = {}
        r = 0
        for key, label, val in (("title", "Titel", d.get_title()), ("author", "Autor", d.get_author()),
                                ("subject", "Thema", d.get_subject()), ("keywords", "Stichwörter", d.get_keywords())):
            lb = Gtk.Label(label=label, xalign=0)
            lb.add_css_class("key")
            e = Gtk.Entry(text=val or "", hexpand=True)
            grid.attach(lb, 0, r, 1, 1)
            grid.attach(e, 1, r, 1, 1)
            ents[key] = e
            r += 1
        box.append(grid)
        info = []
        try:
            size = os.path.getsize(self.path)
            info.append(f"{size / 1048576:.1f} MB" if size > 1048576 else f"{size / 1024:.0f} kB")
        except Exception:
            pass
        info.append(f"{len(self.sizes)} Seite(n)")
        try:
            info.append("PDF " + (d.get_pdf_version_string() or ""))
        except Exception:
            pass
        for lab, fn in (("erstellt mit", d.get_creator), ("erzeugt von", d.get_producer)):
            try:
                if fn():
                    info.append(f"{lab} {fn()}")
            except Exception:
                pass
        try:
            ts = d.get_modification_date_time()
            if ts:
                info.append("geändert " + ts.to_local().format("%d.%m.%Y %H:%M"))
        except Exception:
            pass
        lbl = Gtk.Label(label=" · ".join(info), xalign=0, wrap=True, selectable=True)
        lbl.add_css_class("note")
        box.append(lbl)
        box.append(Gtk.Separator())
        row = Gtk.Box(spacing=10)
        nl = Gtk.Label(label="Mein Name bei Kommentaren", xalign=0)
        nl.add_css_class("key")
        me = Gtk.Entry(text=author_name(), hexpand=True)
        row.append(nl)
        row.append(me)
        box.append(row)
        btns = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        cancel = Gtk.Button(label="Abbrechen")
        ok = Gtk.Button(label="Übernehmen")
        ok.add_css_class("suggested-action")
        btns.append(cancel)
        btns.append(ok)
        box.append(btns)
        win.set_child(box)
        cancel.connect("clicked", lambda *_: win.close())

        def go(*_):
            changed = False
            for key, setter, getter in (("title", d.set_title, d.get_title), ("author", d.set_author, d.get_author),
                                        ("subject", d.set_subject, d.get_subject),
                                        ("keywords", d.set_keywords, d.get_keywords)):
                v = ents[key].get_text().strip()
                if v != (getter() or ""):
                    setter(v)
                    changed = True
            if me.get_text().strip() != author_name():
                set_author_name(me.get_text())
            win.close()
            if changed:
                self.dirty = True
                self._sync_tools()
        ok.connect("clicked", go)
        win.present()

    # ---------------------------------------------------------- Lesezeichen
    def outline_edit(self, change, label):
        """change(tree) verändert die Gliederung; dann per qpdf schreiben (Strg+Z macht es rückgängig)."""
        tree = read_outline(self.doc)
        change(tree)
        self.struct_apply(label, lambda s, o: op_outline(s, o, tree, self.password), after_page=self.current_page(),
                          done=self._outline_done)

    def _outline_done(self):
        if self.side_rev.get_reveal_child():
            self.side.rebuild_outline()

    def view_top(self):
        """(Seite, PDF-y der Oberkante des sichtbaren Bereichs) für ein Lesezeichen."""
        from viewer import GAP
        i = self.current_page()
        h = self.sizes[i][1]
        va = self.scroll.get_vadjustment()
        y = max(0.0, (va.get_value() + GAP - self.offsets()[i]) / self.zoom)
        return i, round(max(0.0, min(h, h - y)), 1)

    def bookmark_add(self, title):
        i, top = self.view_top()
        node = {"title": title, "page": i + 1, "top": top, "children": []}

        def change(tree):
            for k, n in enumerate(tree):
                if n["page"] > node["page"]:
                    tree.insert(k, node)
                    return
            tree.append(node)
        self.outline_edit(change, "Lesezeichen hinzufügen")

    def bookmark_rename(self, path, title):
        def change(tree):
            n = _node(tree, path)
            if n:
                n["title"] = title
        self.outline_edit(change, "Lesezeichen umbenennen")

    def bookmark_delete(self, path):
        def change(tree):
            lst = tree
            for k in path[:-1]:
                lst = lst[k]["children"]
            if path[-1] < len(lst):
                kids = lst[path[-1]]["children"]
                lst[path[-1]:path[-1] + 1] = kids          # Unterpunkte rücken nach oben
        self.outline_edit(change, "Lesezeichen löschen")

    # ---------------------------------------------------------- kleine Dialoge
    def _dialog(self, title, width=380):
        win = Gtk.Window(title=title, transient_for=self.get_root(), modal=True, default_width=width)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=14, margin_bottom=14,
                      margin_start=14, margin_end=14)
        win.set_child(box)
        return win, box

    @staticmethod
    def _buttons(box, win, label, go):
        row = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        cancel = Gtk.Button(label="Abbrechen")
        cancel.connect("clicked", lambda *_: win.close())
        ok = Gtk.Button(label=label)
        ok.add_css_class("suggested-action")
        ok.connect("clicked", lambda *_: go())
        row.append(cancel)
        row.append(ok)
        box.append(row)

    def _targets_row(self, box, side_sel=True):
        dd = Gtk.DropDown.new_from_strings(["Alle Seiten", "Nur diese Seite"])
        row = Gtk.Box(spacing=10)
        lb = Gtk.Label(label="Seiten", xalign=0)
        lb.add_css_class("key")
        row.append(lb)
        dd.set_hexpand(True)
        row.append(dd)
        box.append(row)
        return dd

    def crop_dialog(self):
        if not self._local_path():
            return
        win, box = self._dialog("Seiten zuschneiden")
        hint = Gtk.Label(label="Ränder, die abgeschnitten werden (in mm). Die Seiten werden nur beschnitten angezeigt – "
                               "der Inhalt bleibt im PDF, „Zuschnitt aufheben“ holt ihn zurück.", xalign=0, wrap=True)
        hint.add_css_class("note")
        box.append(hint)
        grid = Gtk.Grid(column_spacing=10, row_spacing=6)
        spins = {}
        for k, (key, label) in enumerate((("l", "Links"), ("t", "Oben"), ("r", "Rechts"), ("b", "Unten"))):
            lb = Gtk.Label(label=label, xalign=0)
            lb.add_css_class("key")
            sp = Gtk.SpinButton.new_with_range(0, 200, 1)
            sp.set_value(0)
            grid.attach(lb, (k % 2) * 2, k // 2, 1, 1)
            grid.attach(sp, (k % 2) * 2 + 1, k // 2, 1, 1)
            spins[key] = sp
        box.append(grid)
        dd = self._targets_row(box)

        def pages():
            return list(range(len(self.sizes))) if dd.get_selected() == 0 else [self.current_page()]

        def go():
            mm = 72 / 25.4
            mg = (spins["l"].get_value() * mm, spins["t"].get_value() * mm, spins["r"].get_value() * mm,
                  spins["b"].get_value() * mm)
            if not any(mg):
                return win.close()
            sel = pages()
            win.close()
            self.struct_apply("Seiten zuschneiden", lambda s, o: op_crop(s, o, {p + 1: mg for p in sel}, self.password),
                              after_page=self.current_page())

        def reset():
            sel = pages()
            win.close()
            self.struct_apply("Zuschnitt aufheben", lambda s, o: op_crop(s, o, {p + 1: None for p in sel}, self.password),
                              after_page=self.current_page())
        row = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        rb = Gtk.Button(label="Zuschnitt aufheben")
        rb.connect("clicked", lambda *_: reset())
        row.append(rb)
        box.append(row)
        self._buttons(box, win, "Zuschneiden", go)
        win.present()

    def numbers_dialog(self):
        if not self._local_path():
            return
        win, box = self._dialog("Seitenzahlen, Kopf- & Fußzeile", 460)
        hint = Gtk.Label(label="Platzhalter: {seite}  {seiten}  {datum}  {datei}", xalign=0)
        hint.add_css_class("note")
        box.append(hint)
        grid = Gtk.Grid(column_spacing=8, row_spacing=6)
        ents = {}
        for c, name in enumerate(("links", "mitte", "rechts")):
            lb = Gtk.Label(label=name)
            lb.add_css_class("key")
            grid.attach(lb, c + 1, 0, 1, 1)
        for r, (key, name) in enumerate((("h", "Kopfzeile"), ("f", "Fußzeile")), 1):
            lb = Gtk.Label(label=name, xalign=0)
            lb.add_css_class("key")
            grid.attach(lb, 0, r, 1, 1)
            for c in range(3):
                e = Gtk.Entry(hexpand=True, width_chars=10)
                if key == "f" and c == 1:
                    e.set_text("{seite} / {seiten}")
                grid.attach(e, c + 1, r, 1, 1)
                ents[(key, c)] = e
        box.append(grid)
        row = Gtk.Box(spacing=10)
        l1 = Gtk.Label(label="Ab Seite", xalign=0)
        l1.add_css_class("key")
        start = Gtk.SpinButton.new_with_range(1, max(1, len(self.sizes)), 1)
        l2 = Gtk.Label(label="Schrift (pt)", xalign=0, margin_start=10)
        l2.add_css_class("key")
        size = Gtk.SpinButton.new_with_range(6, 36, 1)
        size.set_value(10)
        for w in (l1, start, l2, size):
            row.append(w)
        box.append(row)
        name = os.path.basename(self.path or "") or "PDF"
        total = len(self.sizes)

        def go():
            if not any(e.get_text().strip() for e in ents.values()):
                return win.close()
            texts = {k: e.get_text() for k, e in ents.items()}
            first = int(start.get_value())
            pt = size.get_value()
            win.close()

            def sub(t, nr):
                import datetime
                return (t.replace("{seite}", str(nr + 1)).replace("{seiten}", str(total))
                        .replace("{datum}", datetime.date.today().strftime("%d.%m.%Y")).replace("{datei}", name))

            def draw(cr, nr, w, h):
                if nr + 1 < first:
                    return
                cr.set_source_rgb(0.1, 0.1, 0.1)
                m = 28
                for (key, c), t in texts.items():
                    if not t.strip():
                        continue
                    lay = _layout(cr, sub(t, nr), pt)
                    tw, th = lay.get_pixel_size()
                    x = m if c == 0 else (w - tw) / 2 if c == 1 else w - tw - m
                    y = m - th / 2 if key == "h" else h - m - th / 2
                    cr.move_to(x, max(2, y))
                    PangoCairo.show_layout(cr, lay)
            sizes = list(self.sizes)
            self.struct_apply("Seitenzahlen einfügen", lambda s, o: op_overlay(s, o, sizes, draw, self.password),
                              after_page=self.current_page())
        self._buttons(box, win, "Einfügen", go)
        win.present()

    def watermark_dialog(self):
        if not self._local_path():
            return
        win, box = self._dialog("Wasserzeichen")
        text = Gtk.Entry(text="ENTWURF", placeholder_text="Text")
        box.append(text)
        grid = Gtk.Grid(column_spacing=10, row_spacing=6)
        size = Gtk.SpinButton.new_with_range(10, 300, 5)
        size.set_value(90)
        opac = Gtk.SpinButton.new_with_range(5, 100, 5)
        opac.set_value(20)
        ang = Gtk.SpinButton.new_with_range(-90, 90, 5)
        ang.set_value(45)
        col = Gtk.DropDown.new_from_strings(["Grau", "Rot", "Blau", "Schwarz"])
        for r, (name, w) in enumerate((("Größe (pt)", size), ("Deckkraft (%)", opac), ("Winkel (°)", ang), ("Farbe", col))):
            lb = Gtk.Label(label=name, xalign=0)
            lb.add_css_class("key")
            grid.attach(lb, 0, r, 1, 1)
            w.set_hexpand(True)
            grid.attach(w, 1, r, 1, 1)
        box.append(grid)
        dd = self._targets_row(box)

        def go():
            t = text.get_text().strip()
            if not t:
                return win.close()
            pt, op, an = size.get_value(), opac.get_value() / 100, ang.get_value()
            rgb = [(0.4, 0.4, 0.4), (0.85, 0.1, 0.1), (0.1, 0.3, 0.85), (0, 0, 0)][col.get_selected()]
            only = None if dd.get_selected() == 0 else self.current_page()
            win.close()

            def draw(cr, nr, w, h):
                if only is not None and nr != only:
                    return
                lay = _layout(cr, t, pt, True)
                tw, th = lay.get_pixel_size()
                if tw > w * 0.9:                                  # zu breit: kleiner, damit es auf die Seite passt
                    lay = _layout(cr, t, pt * w * 0.9 / tw, True)
                    tw, th = lay.get_pixel_size()
                cr.translate(w / 2, h / 2)
                cr.rotate(-math.radians(an))
                cr.set_source_rgba(rgb[0], rgb[1], rgb[2], op)
                cr.move_to(-tw / 2, -th / 2)
                PangoCairo.show_layout(cr, lay)
            sizes = list(self.sizes)
            self.struct_apply("Wasserzeichen einfügen", lambda s, o: op_overlay(s, o, sizes, draw, self.password),
                              after_page=self.current_page())
        self._buttons(box, win, "Einfügen", go)
        win.present()


def _rect(x1, y1, x2, y2):
    P = _poppler()
    r = P.Rectangle()
    r.x1, r.y1, r.x2, r.y2 = x1, y1, x2, y2
    return r


def _node(tree, path):
    lst, n = tree, None
    for k in path:
        if k >= len(lst):
            return None
        n = lst[k]
        lst = n["children"]
    return n


# ======================================================================= Formen & Stempel (Striche in Seitenkoordinaten, y nach unten)
def shape_strokes(kind, x1, y1, x2, y2, width):
    """Linie oder Pfeil als Strich-Liste: [[(x, y), …], …]."""
    strokes = [[(x1, y1), (x2, y2)]]
    if kind == "arrow":
        ang = math.atan2(y2 - y1, x2 - x1)
        ln = max(9.0, 4.5 * width + 4)
        for d in (math.radians(155), -math.radians(155)):
            strokes.append([(x2, y2), (x2 + ln * math.cos(ang + d), y2 + ln * math.sin(ang + d))])
    return strokes


STAMPS = ("ENTWURF", "GEPRÜFT", "GENEHMIGT", "ABGELEHNT", "VERTRAULICH", "KOPIE", "ERLEDIGT", "{datum}")


def stamp_text(idx):
    import datetime
    t = STAMPS[max(0, min(idx, len(STAMPS) - 1))]
    return t.replace("{datum}", datetime.date.today().strftime("%d.%m.%Y"))


def stamp_strokes(text, size, cx, cy, tilt=-7.0):
    """Stempel (Schrift als Umriss + Rahmen), mittig um (cx, cy), leicht gekippt. Rückgabe: (Striche, Strichstärke)."""
    import cairo
    cr = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))
    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(size)
    ext = cr.text_extents(text)
    cr.new_path()
    cr.move_to(-ext.x_bearing - ext.width / 2, -ext.y_bearing - ext.height / 2)
    cr.text_path(text)
    raw = []
    for kind, pts in cr.copy_path_flat():
        if kind == cairo.PATH_MOVE_TO:
            raw.append([pts])
        elif kind == cairo.PATH_LINE_TO and raw:
            raw[-1].append(pts)
        elif kind == cairo.PATH_CLOSE_PATH and raw:
            raw[-1].append(raw[-1][0])
    pad = size * 0.32
    hw, hh = ext.width / 2 + pad, ext.height / 2 + pad
    raw.append([(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh), (-hw, -hh)])
    a = math.radians(tilt)
    ca, sa = math.cos(a), math.sin(a)
    strokes = [[(cx + x * ca - y * sa, cy + x * sa + y * ca) for x, y in st] for st in raw if len(st) > 1]
    return strokes, max(1.0, size / 16)
