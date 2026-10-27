# Dokumente umwandeln: PDFs zusammenfügen, Seiten auswählen/teilen/drehen, PDF verkleinern und
# zwischen PDF, Bildern, Text und Office-Formaten umwandeln. Läuft lokal; wo es ein Programm braucht
# (qpdf, Ghostscript, LibreOffice, pandoc …), steht oben, was fehlt. Originale werden nie überschrieben.
import os, re, shutil, subprocess, tempfile, threading

import tools

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

PDF_EXT = (".pdf",)
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".avif", ".tif", ".tiff", ".bmp", ".gif", ".heic", ".heif")
TEXT_EXT = (".txt", ".md", ".markdown", ".log", ".csv")
OFFICE_EXT = (".doc", ".docx", ".odt", ".rtf", ".html", ".htm", ".epub", ".xls", ".xlsx", ".ods",
              ".ppt", ".pptx", ".odp")
ALL_EXT = PDF_EXT + IMG_EXT + TEXT_EXT + OFFICE_EXT

MODES = [("merge", "zusammenfügen"), ("convert", "umwandeln"), ("pages", "seiten"), ("shrink", "verkleinern")]
TARGETS = [("pdf", "PDF"), ("png", "PNG"), ("jpg", "JPG"), ("webp", "WebP"), ("txt", "Text"), ("html", "HTML"),
           ("docx", "Word"), ("odt", "ODT"), ("md", "Markdown")]
PAGE_OPS = [("extract", "seiten behalten"), ("delete", "seiten löschen"), ("split", "in einzelseiten teilen"),
            ("chunks", "in teile mit n seiten"), ("rotate", "drehen"), ("reverse", "umkehren")]
SHRINK = [("screen", "klein (72 dpi)"), ("ebook", "mittel (150 dpi)"), ("printer", "gut (300 dpi)")]

CONVERT_CSS = """
.cv-drop { border: 1px dashed alpha(@primary, 0.5); border-radius: 6px; padding: 18px; }
.cv-drop.drag-over { background-color: alpha(@primary, 0.12); border-color: @primary; }
.cv-ok { color: @tertiary; font-weight: bold; }
.cv-bad { color: alpha(@foreground, 0.55); }
.cv-miss { color: #ffb86b; }
"""


# ---------- Werkzeuge ----------
def have(name):
    return shutil.which(name) is not None


def soffice():
    return shutil.which("soffice") or shutil.which("libreoffice")


def tool_status():
    """[(Name, vorhanden, wofür)]"""
    return [("qpdf", have("qpdf"), "Seiten, Drehen, Zusammenfügen"),
            ("ghostscript", have("gs"), "PDF verkleinern"),
            ("libreoffice", bool(soffice()), "Word, Excel, PowerPoint, ODT"),
            ("pandoc", have("pandoc"), "Markdown, HTML, EPUB"),
            ("tesseract", have("tesseract"), "Text aus Bildern")]


def run(cmd, timeout=600):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or f"{cmd[0]} fehlgeschlagen").strip().splitlines()[-1][:200])
    return r


def kind(path):
    e = os.path.splitext(path)[1].lower()
    if e in PDF_EXT:
        return "pdf"
    if e in IMG_EXT:
        return "image"
    if e in TEXT_EXT:
        return "text"
    if e in OFFICE_EXT:
        return "office"
    return None


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024


def free_name(folder, stem, ext):
    """Nie überschreiben: name.ext, name (2).ext …"""
    p = os.path.join(folder, stem + ext)
    n = 2
    while os.path.exists(p):
        p = os.path.join(folder, f"{stem} ({n}){ext}")
        n += 1
    return p


# ---------- PDF-Grundlagen ----------
def page_count(path):
    if have("qpdf"):
        try:
            return int(run(["qpdf", "--show-npages", path]).stdout.strip())
        except (RuntimeError, ValueError):
            pass
    gi.require_version("Poppler", "0.18")
    from gi.repository import Poppler
    return Poppler.Document.new_from_file(GLib.filename_to_uri(os.path.abspath(path)), None).get_n_pages()


def parse_range(text, n):
    """„1-3, 5, 8-, z“ → [1,2,3,5,8,…,n]; „5-2“ = rückwärts. Leer = alle. Wirft ValueError bei Unsinn."""
    text = (text or "").strip().lower().replace(";", ",")
    if not text:
        return list(range(1, n + 1))
    out = []

    def num(t):
        t = t.strip()
        if t in ("z", "e", "ende", "last"):
            return n
        return int(t)
    for part in filter(None, (p.strip() for p in text.split(","))):
        if "-" in part:
            a, b = part.split("-", 1)
            a = num(a) if a.strip() else 1
            b = num(b) if b.strip() else n
        else:
            a = b = num(part)
        if not (1 <= a <= n and 1 <= b <= n):
            raise ValueError(f"Seite {a if not 1 <= a <= n else b} gibt es nicht (1–{n})")
        out += list(range(a, b + 1)) if a <= b else list(range(a, b - 1, -1))
    return out


def compact(pages):
    """[1,2,3,5] → '1-3,5' (für qpdf)"""
    out, i = [], 0
    while i < len(pages):
        j = i
        while j + 1 < len(pages) and pages[j + 1] == pages[j] + 1:
            j += 1
        out.append(str(pages[i]) if i == j else f"{pages[i]}-{pages[j]}")
        i = j + 1
    return ",".join(out)


def _need(name, what):
    if not have(name):
        raise RuntimeError(f"{what} braucht „{name}“ – bitte installieren")


def image_to_pdf(path, dst, size="fit"):
    """Ein Bild → einseitiges PDF. size: 'fit' = Bildgröße, 'a4' = A4 mit Rand."""
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(Image.open(path))
    if img.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", img.size, "white")
        rgba = img.convert("RGBA")
        bg.paste(rgba, mask=rgba.split()[-1])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")
    if size == "a4":
        W, H = 1654, 2339                                   # A4 bei 200 dpi
        if img.width > img.height:
            W, H = H, W
        m = 80
        img.thumbnail((W - 2 * m, H - 2 * m))
        page = Image.new("RGB", (W, H), "white")
        page.paste(img, ((W - img.width) // 2, (H - img.height) // 2))
        img, res = page, 200
    else:
        res = 150 if max(img.size) > 1500 else 96
    img.save(dst, "PDF", resolution=res)


def merge_pdfs(items, dst, a4=False):
    """items: [(pfad, bereich-text)]. Bilder werden zu Seiten, PDFs ggf. nur mit Bereich."""
    tmp = tempfile.mkdtemp(prefix="scourge-merge-")
    try:
        parts = []                                          # (pdf, [seiten] | None)
        for i, (p, rng) in enumerate(items):
            k = kind(p)
            if k == "image":
                t = os.path.join(tmp, f"img{i}.pdf")
                image_to_pdf(p, t, "a4" if a4 else "fit")
                parts.append((t, None))
            elif k == "pdf":
                pages = parse_range(rng, page_count(p)) if rng.strip() else None
                parts.append((p, pages))
            else:
                raise RuntimeError(f"{os.path.basename(p)}: nur PDFs und Bilder lassen sich zusammenfügen")
        if have("qpdf"):
            cmd = ["qpdf", "--empty", "--pages"]
            for p, pages in parts:
                cmd += [p] + ([compact(pages)] if pages else [])
            run(cmd + ["--", dst])
        elif have("pdfunite"):
            files = []
            for i, (p, pages) in enumerate(parts):
                if pages:
                    for n in pages:
                        t = os.path.join(tmp, f"p{i}-{n}.pdf")
                        run(["pdfseparate", "-f", str(n), "-l", str(n), p, t])
                        files.append(t)
                else:
                    files.append(p)
            run(["pdfunite"] + files + [dst])
        elif have("gs") and not any(pg for _p, pg in parts):
            run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pdfwrite", f"-sOutputFile={dst}"] + [p for p, _ in parts])
        else:
            raise RuntimeError("Zusammenfügen braucht „qpdf“ oder „poppler-utils“ (pdfunite) – bitte installieren")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def pages_op(src, op, rng, outdir, stem, n_chunk=1, angle=90):
    """Seitenwerkzeuge. Liefert die geschriebenen Dateien."""
    n = page_count(src)
    out = []
    if op in ("extract", "delete", "reverse") or op == "rotate":
        _need("qpdf", "Seiten bearbeiten")
    if op == "extract":
        pages = parse_range(rng, n)
        dst = free_name(outdir, f"{stem}-auswahl", ".pdf")
        run(["qpdf", "--empty", "--pages", src, compact(pages), "--", dst])
        out.append(dst)
    elif op == "delete":
        gone = set(parse_range(rng, n)) if rng.strip() else set()
        keep = [i for i in range(1, n + 1) if i not in gone]
        if not keep:
            raise RuntimeError("so bliebe keine Seite übrig")
        dst = free_name(outdir, f"{stem}-gekürzt", ".pdf")
        run(["qpdf", "--empty", "--pages", src, compact(keep), "--", dst])
        out.append(dst)
    elif op == "reverse":
        dst = free_name(outdir, f"{stem}-rückwärts", ".pdf")
        run(["qpdf", "--empty", "--pages", src, f"z-1", "--", dst])
        out.append(dst)
    elif op == "rotate":
        pages = parse_range(rng, n)
        dst = free_name(outdir, f"{stem}-gedreht", ".pdf")
        run(["qpdf", src, f"--rotate=+{angle}:{compact(pages)}", "--", dst])
        out.append(dst)
    elif op in ("split", "chunks"):
        size = 1 if op == "split" else max(1, n_chunk)
        pages = parse_range(rng, n)
        folder = free_name(outdir, f"{stem}-teile", "")
        os.makedirs(folder)
        width = len(str(len(pages)))
        for i in range(0, len(pages), size):
            grp = pages[i:i + size]
            label = f"{i // size + 1:0{width}d}"
            dst = os.path.join(folder, f"{stem}-{label}.pdf")
            if have("qpdf"):
                run(["qpdf", "--empty", "--pages", src, compact(grp), "--", dst])
            elif have("pdfseparate") and size == 1:
                run(["pdfseparate", "-f", str(grp[0]), "-l", str(grp[0]), src, dst])
            else:
                raise RuntimeError("Teilen braucht „qpdf“ – bitte installieren")
            out.append(dst)
    return out


def shrink_pdf(src, dst, preset):
    _need("gs", "Verkleinern")
    run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.5",
         f"-dPDFSETTINGS=/{preset}", "-dDetectDuplicateImages=true", f"-sOutputFile={dst}", src])


# ---------- Umwandeln ----------
def render_pdf(src, outdir, stem, fmt, dpi, rng):
    """PDF → Bilder (Poppler, kein Zusatzprogramm). Eine Seite = eine Datei, sonst Ordner."""
    gi.require_version("Poppler", "0.18")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Poppler
    import cairo
    from PIL import Image
    doc = Poppler.Document.new_from_file(GLib.filename_to_uri(os.path.abspath(src)), None)
    pages = parse_range(rng, doc.get_n_pages())
    ext = {"jpg": ".jpg", "png": ".png", "webp": ".webp"}[fmt]
    folder = outdir
    if len(pages) > 1:
        folder = free_name(outdir, f"{stem}-seiten", "")
        os.makedirs(folder)
    out, width = [], len(str(max(pages)))
    scale = dpi / 72.0
    for n in pages:
        pg = doc.get_page(n - 1)
        w, h = pg.get_size()
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(1, int(w * scale)), max(1, int(h * scale)))
        cr = cairo.Context(surf)
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
        cr.scale(scale, scale)
        pg.render(cr)
        img = Image.frombuffer("RGBA", (surf.get_width(), surf.get_height()), bytes(surf.get_data()), "raw", "BGRA", 0, 1)
        img = img.convert("RGB")
        dst = (free_name(folder, stem, ext) if len(pages) == 1
               else os.path.join(folder, f"{stem}-{n:0{width}d}{ext}"))
        img.save(dst, quality=92) if fmt != "png" else img.save(dst)
        out.append(dst)
    return out


def pdf_text(src, dst, layout=True):
    if have("pdftotext"):
        run(["pdftotext"] + (["-layout"] if layout else []) + [src, dst])
        return
    gi.require_version("Poppler", "0.18")
    from gi.repository import Poppler
    doc = Poppler.Document.new_from_file(GLib.filename_to_uri(os.path.abspath(src)), None)
    with open(dst, "w", encoding="utf-8") as f:
        for i in range(doc.get_n_pages()):
            f.write(doc.get_page(i).get_text() + "\n\f")


def text_to_pdf(src, dst, markdown=False):
    """Text → PDF: pandoc/LibreOffice, sonst eigener Satz mit Pango (A4, einfache Seiten)."""
    if markdown and have("pandoc") and (have("pdflatex") or have("xelatex") or have("wkhtmltopdf")):
        try:
            run(["pandoc", src, "-o", dst])
            return
        except RuntimeError:
            pass
    if soffice():
        office_convert(src, dst, "pdf")
        return
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Pango, PangoCairo
    import cairo
    text = open(src, encoding="utf-8", errors="replace").read().replace("\t", "    ")
    W, H, M = 595.0, 842.0, 56.0
    surf = cairo.PDFSurface(dst, W, H)
    cr = cairo.Context(surf)
    lay = PangoCairo.create_layout(cr)
    lay.set_font_description(Pango.FontDescription("Monospace 10"))
    lay.set_width(int((W - 2 * M) * Pango.SCALE))
    lay.set_wrap(Pango.WrapMode.WORD_CHAR)
    y = M
    for line in text.split("\n") or [""]:
        lay.set_text(line or " ", -1)
        _w, lh = lay.get_pixel_size()
        if y + lh > H - M:
            cr.show_page()
            y = M
        cr.set_source_rgb(0, 0, 0)
        cr.move_to(M, y)
        PangoCairo.show_layout(cr, lay)
        y += lh
    surf.finish()


SOFFICE_FILTER = {"pdf": "pdf", "docx": "docx", "odt": "odt", "txt": "txt:Text", "html": "html", "rtf": "rtf",
                  "xlsx": "xlsx", "ods": "ods", "csv": "csv", "pptx": "pptx", "odp": "odp"}


def office_convert(src, dst, fmt, from_pdf=False):
    exe = soffice()
    if not exe:
        raise RuntimeError("Office-Dateien brauchen LibreOffice – bitte installieren")
    tmp = tempfile.mkdtemp(prefix="scourge-lo-")
    try:
        cmd = [exe, "--headless", "--norestore", f"-env:UserInstallation=file://{tmp}/profile", "--convert-to",
               SOFFICE_FILTER[fmt], "--outdir", tmp]
        if from_pdf:
            cmd[1:1] = ["--infilter=writer_pdf_import"]
        run(cmd + [src], timeout=300)
        made = [f for f in os.listdir(tmp) if f != "profile"]
        if not made:
            raise RuntimeError("LibreOffice hat nichts erzeugt")
        shutil.move(os.path.join(tmp, made[0]), dst)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


PANDOC_FMT = {".md": "markdown", ".markdown": "markdown", ".html": "html", ".htm": "html", ".docx": "docx",
              ".odt": "odt", ".rtf": "rtf", ".epub": "epub", ".txt": "plain"}


def pandoc_convert(src, dst, fmt):
    _need("pandoc", "Diese Umwandlung")
    to = {"md": "gfm", "html": "html5", "txt": "plain", "docx": "docx", "odt": "odt"}[fmt]
    cmd = ["pandoc", src, "-f", PANDOC_FMT[os.path.splitext(src)[1].lower()], "-t", to]
    if fmt == "html":
        cmd.append("-s")
    if fmt in ("md", "txt"):
        cmd.append("--wrap=none")
    run(cmd + ["-o", dst])


def can_convert(path, fmt):
    """None = geht, sonst der Grund."""
    k, ext = kind(path), os.path.splitext(path)[1].lower()
    if k is None:
        return "Dateityp unbekannt"
    if k == "pdf":
        if fmt in ("png", "jpg", "webp", "txt"):
            return None
        if fmt in ("docx", "odt", "html"):
            if soffice() or (fmt == "html" and have("pdftohtml")):
                return None
            return "braucht LibreOffice" + (" oder pdftohtml" if fmt == "html" else "")
        if fmt == "md":
            return None if have("pdftotext") else "braucht pdftotext"
        return "ist schon PDF"
    if k == "image":
        if fmt in ("pdf", "png", "jpg", "webp"):
            return None if fmt != ext.lstrip(".").replace("jpeg", "jpg") else "ist schon dieses Format"
        if fmt == "txt":
            return None if have("tesseract") else "braucht tesseract (Texterkennung)"
        return "Bild → dieses Format geht nicht"
    if k == "text":
        if fmt == "pdf":
            return None
        if fmt in ("docx", "odt", "html"):
            return None if (have("pandoc") or soffice()) else "braucht pandoc oder LibreOffice"
        if fmt == "md":
            return "ist schon Text" if ext in (".md", ".markdown", ".txt") else ("braucht pandoc" if not have("pandoc") else None)
        if fmt in ("txt",):
            return None if ext not in (".txt",) else "ist schon Text"
        return "Text → dieses Format geht nicht"
    # Office
    if fmt == "pdf":
        return None if soffice() else "braucht LibreOffice"
    if fmt in ("docx", "odt", "html", "txt"):
        if ext == "." + fmt:
            return "ist schon dieses Format"
        return None if (soffice() or (have("pandoc") and ext in PANDOC_FMT)) else "braucht LibreOffice oder pandoc"
    if fmt == "md":
        return None if (have("pandoc") and ext in PANDOC_FMT) else "braucht pandoc"
    return "geht nicht"


def convert_one(path, fmt, outdir, opts):
    """Wandelt eine Datei um; liefert die geschriebenen Pfade."""
    reason = can_convert(path, fmt)
    if reason:
        raise RuntimeError(reason)
    k, ext = kind(path), os.path.splitext(path)[1].lower()
    stem = os.path.splitext(os.path.basename(path))[0]
    if k == "pdf":
        if fmt in ("png", "jpg", "webp"):
            return render_pdf(path, outdir, stem, fmt, opts["dpi"], opts["range"])
        dst = free_name(outdir, stem, "." + fmt)
        if fmt == "txt":
            pdf_text(path, dst, opts["layout"])
        elif fmt == "md":
            pdf_text(path, dst, False)
        elif fmt == "html" and have("pdftohtml") and not soffice():
            base = free_name(outdir, stem, "")
            run(["pdftohtml", "-s", "-noframes", "-enc", "UTF-8", path, base])
            return [base + ".html"] if os.path.exists(base + ".html") else [base + "-html.html"]
        else:
            office_convert(path, dst, fmt, from_pdf=True)
        return [dst]
    if k == "image":
        stem_ext = {"pdf": ".pdf", "png": ".png", "jpg": ".jpg", "webp": ".webp", "txt": ".txt"}[fmt]
        dst = free_name(outdir, stem, stem_ext)
        if fmt == "pdf":
            image_to_pdf(path, dst, "a4" if opts["a4"] else "fit")
        elif fmt == "txt":
            r = run(["tesseract", path, "-", "-l", opts.get("lang", "deu+eng")])
            open(dst, "w", encoding="utf-8").write(r.stdout)
        else:
            from PIL import Image, ImageOps
            img = ImageOps.exif_transpose(Image.open(path))
            if fmt == "jpg" and img.mode != "RGB":
                bg = Image.new("RGB", img.size, "white")
                rgba = img.convert("RGBA")
                bg.paste(rgba, mask=rgba.split()[-1])
                img = bg
            img.save(dst, quality=92) if fmt != "png" else img.save(dst)
        return [dst]
    dst = free_name(outdir, stem, "." + fmt)
    if k == "text":
        if fmt == "pdf":
            text_to_pdf(path, dst, markdown=ext in (".md", ".markdown"))
        elif fmt == "txt":
            shutil.copyfile(path, dst)
        elif have("pandoc") and ext in PANDOC_FMT:
            pandoc_convert(path, dst, fmt)
        else:
            office_convert(path, dst, fmt)
        return [dst]
    # Office
    if fmt == "md" or (not soffice()):
        pandoc_convert(path, dst, fmt)
    else:
        office_convert(path, dst, fmt)
    return [dst]


# ---------- Seite ----------
class ConvertPage(tools.ToolPage):
    title = "Dokumente umwandeln"
    subtitle = "PDFs zusammenfügen · Seiten wählen, teilen, drehen · PDF ⇄ Bild ⇄ Text ⇄ Word"
    privacy = "Läuft komplett auf deinem Rechner. Originale bleiben unverändert, Ergebnisse bekommen neue Namen."

    def build(self):
        self.files = []                     # Pfade
        self.ranges = {}                    # Pfad → Seitenbereich (nur Zusammenfügen)
        self.out_folder = None
        self.mode = "merge"
        self.fmt = "pdf"
        self.op = "extract"
        self.shrink = "ebook"
        self.last_written = []
        self.opts = {"dpi": 150, "range": "", "layout": True, "a4": False, "chunk": 5, "angle": 90}
        self.cancel = threading.Event()

        # --- verfügbare Programme ---
        st = tool_status()
        miss = [f"{n} ({what})" for n, ok, what in st if not ok]
        if miss:
            l = Gtk.Label(label="󰀦  Optional, nicht installiert: " + " · ".join(miss), xalign=0, wrap=True)
            l.add_css_class("cv-miss")
            self.box.append(l)

        # --- Dateien ---
        c = self.card("dateien")
        drop = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        drop.add_css_class("cv-drop")
        row = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        row.append(self.button("󰈔  dateien wählen", self.pick_files))
        self.clear_btn = self.button("󰃢  leeren", self.clear_files, small=True)
        self.clear_btn.set_visible(False)
        row.append(self.clear_btn)
        drop.append(row)
        self.count_lbl = Gtk.Label(label="…oder PDFs, Bilder, Texte, Office-Dateien hierher ziehen", xalign=0.5)
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

        # --- Aufgabe ---
        c = self.settings_card = self.card("aufgabe")
        self.label(c, "was soll passieren?")
        self.seg(c, MODES, "merge", self.set_mode)

        # zusammenfügen
        self.merge_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.label(self.merge_box, "name der neuen datei")
        self.name_entry = Gtk.Entry(text="zusammengefügt", hexpand=True)
        self.name_entry.add_css_class("pb")
        self.merge_box.append(self.name_entry)
        self.merge_a4 = self.switch_row(self.merge_box, "Bilder auf A4-Seiten setzen (sonst Bildgröße)", False,
                                        lambda v: self.opts.__setitem__("a4", v))
        n = Gtk.Label(label="Reihenfolge mit ▲ ▼ ändern. Pro PDF kann ein Seitenbereich stehen, z. B. 1-3, 5, 8- "
                            "(leer = alle Seiten). Bilder werden eigene Seiten.", xalign=0, wrap=True)
        n.add_css_class("note")
        self.merge_box.append(n)
        c.append(self.merge_box)

        # umwandeln
        self.conv_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.label(self.conv_box, "umwandeln in")
        self.seg(self.conv_box, TARGETS, "pdf", self.set_fmt)
        self.dpi_box, _s = self.slider(self.conv_box, "auflösung für Bilder aus PDFs (dpi)", 72, 600, 150,
                                       lambda v: self.opts.__setitem__("dpi", int(v)))
        self.layout_sw = self.switch_row(self.conv_box, "PDF → Text: Spalten und Layout beibehalten", True,
                                         lambda v: self.opts.__setitem__("layout", v))
        self.a4_sw = self.switch_row(self.conv_box, "Bild → PDF: auf A4-Seite setzen (sonst Bildgröße)", False,
                                     lambda v: self.opts.__setitem__("a4", v))
        self.conv_range_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.label(self.conv_range_box, "nur diese seiten (PDF → Bilder), leer = alle")
        self.conv_range = Gtk.Entry(placeholder_text="z. B. 1-3, 7", hexpand=True)
        self.conv_range.add_css_class("pb")
        self.conv_range.connect("changed", lambda e: self.opts.__setitem__("range", e.get_text()))
        self.conv_range_box.append(self.conv_range)
        self.conv_box.append(self.conv_range_box)
        c.append(self.conv_box)

        # seiten
        self.pages_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.label(self.pages_box, "werkzeug (gilt für jede gewählte PDF)")
        self.seg(self.pages_box, PAGE_OPS, "extract", self.set_op)
        self.range_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.range_lbl = Gtk.Label(xalign=0)
        self.range_lbl.add_css_class("key")
        self.range_box.append(self.range_lbl)
        self.range_entry = Gtk.Entry(placeholder_text="z. B. 1-3, 5, 8-  (z = letzte Seite)", hexpand=True)
        self.range_entry.add_css_class("pb")
        self.range_box.append(self.range_entry)
        self.pages_box.append(self.range_box)
        self.chunk_box = Gtk.Box(spacing=8)
        l = Gtk.Label(label="seiten pro teil")
        l.add_css_class("key")
        self.chunk_spin = Gtk.SpinButton.new_with_range(1, 1000, 1)
        self.chunk_spin.add_css_class("pb")
        self.chunk_spin.set_value(5)
        self.chunk_spin.connect("value-changed", lambda s: self.opts.__setitem__("chunk", int(s.get_value())))
        self.chunk_box.append(l); self.chunk_box.append(self.chunk_spin)
        self.pages_box.append(self.chunk_box)
        self.angle_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.label(self.angle_box, "drehen um")
        self.seg(self.angle_box, [(90, "90° rechts"), (180, "180°"), (270, "90° links")], 90,
                 lambda k: self.opts.__setitem__("angle", k))
        self.pages_box.append(self.angle_box)
        c.append(self.pages_box)

        # verkleinern
        self.shrink_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.label(self.shrink_box, "qualität (Bilder in der PDF werden heruntergerechnet)")
        self.seg(self.shrink_box, SHRINK, "ebook", lambda k: setattr(self, "shrink", k))
        c.append(self.shrink_box)

        self.label(c, "speichern")
        self.save_btns = self.seg(c, [("next", "neben dem original"), ("folder", "in ordner …")], "next", self.set_save)
        self.folder_lbl = Gtk.Label(xalign=0, ellipsize=1)
        self.folder_lbl.add_css_class("note")
        self.folder_lbl.set_visible(False)
        c.append(self.folder_lbl)

        # --- Start ---
        row = Gtk.Box(spacing=6)
        self.go_btn = self.button("󰁄  los", self.start)
        self.go_btn.set_sensitive(False)
        self.open_btn = self.button("󰉋  ordner öffnen", self.open_folder)
        self.open_btn.set_visible(False)
        self.sum_lbl = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        self.sum_lbl.add_css_class("key")
        row.append(self.go_btn); row.append(self.open_btn); row.append(self.sum_lbl)
        self.box.append(row)
        self.box.reorder_child_after(row, self.settings_card)
        self.set_mode("merge")

    # --- Bausteine (wie in compress.py) ---
    def label(self, parent, text):
        l = Gtk.Label(label=text, xalign=0)
        l.add_css_class("key")
        parent.append(l)

    def seg(self, parent, options, active, cb):
        fb = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=9, min_children_per_line=2,
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
        return row

    # --- Einstellungen ---
    def set_mode(self, mode):
        self.mode = mode
        self.merge_box.set_visible(mode == "merge")
        self.conv_box.set_visible(mode == "convert")
        self.pages_box.set_visible(mode == "pages")
        self.shrink_box.set_visible(mode == "shrink")
        self.set_op(self.op)
        self.set_fmt(self.fmt)
        self.refresh_list()

    def set_fmt(self, fmt):
        self.fmt = fmt
        self.dpi_box.set_visible(fmt in ("png", "jpg", "webp"))
        self.conv_range_box.set_visible(fmt in ("png", "jpg", "webp"))
        self.layout_sw.set_visible(fmt == "txt")
        self.a4_sw.set_visible(fmt == "pdf")
        self.refresh_list()

    def set_op(self, op):
        self.op = op
        need_range = op in ("extract", "delete", "rotate", "split", "chunks")
        self.range_box.set_visible(need_range)
        self.range_lbl.set_label({"extract": "diese seiten behalten", "delete": "diese seiten löschen",
                                  "rotate": "diese seiten drehen (leer = alle)",
                                  "split": "nur diese seiten teilen (leer = alle)",
                                  "chunks": "nur diese seiten teilen (leer = alle)"}.get(op, ""))
        self.chunk_box.set_visible(op == "chunks")
        self.angle_box.set_visible(op == "rotate")

    def set_save(self, key):
        if key == "next":
            self.out_folder = None
            self.folder_lbl.set_visible(False)
            return
        dlg = Gtk.FileDialog(title="Zielordner")

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
        f = Gtk.FileFilter(name="Dokumente und Bilder")
        for e in ALL_EXT:
            f.add_suffix(e.lstrip("."))
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(f)
        dlg = Gtk.FileDialog(title="Dateien wählen", filters=filters)

        def done(d, res):
            try:
                files = d.open_multiple_finish(res)
            except GLib.Error:
                return
            self.add_paths([files.get_item(i).get_path() for i in range(files.get_n_items())])
        dlg.open_multiple(self.tab.win, None, done)

    def add_paths(self, paths):
        new = [p for p in paths if p and os.path.isfile(p) and kind(p) and p not in self.files]
        if not new:
            if paths:
                self.tab.notify("Keine (neuen) passenden Dateien gefunden")
            return
        self.files += new
        self.refresh_list()

    def clear_files(self):
        self.files, self.ranges = [], {}
        self.sum_lbl.set_label("")
        self.open_btn.set_visible(False)
        self.refresh_list()

    def move(self, i, d):
        j = i + d
        if 0 <= j < len(self.files):
            self.files[i], self.files[j] = self.files[j], self.files[i]
            self.refresh_list()

    def remove(self, i):
        self.ranges.pop(self.files[i], None)
        del self.files[i]
        self.refresh_list()

    def refresh_list(self):
        self.clear(self.list_box)
        n = len(self.files)
        total = sum(os.path.getsize(p) for p in self.files if os.path.exists(p))
        self.count_lbl.set_label(f"{n} datei{'en' if n != 1 else ''} · zusammen {human(total)}" if n
                                 else "…oder PDFs, Bilder, Texte, Office-Dateien hierher ziehen")
        self.clear_btn.set_visible(bool(n))
        self.go_btn.set_sensitive(bool(n))
        for i, p in enumerate(self.files[:300]):
            row = Gtk.Box(spacing=6)
            name = Gtk.Label(label=os.path.basename(p), xalign=0, hexpand=True, ellipsize=3)
            name.add_css_class("finding-title")
            name.set_tooltip_text(p)
            row.append(name)
            k = kind(p)
            if self.mode == "merge":
                if k not in ("pdf", "image"):
                    info = Gtk.Label(label="nur PDF/Bild")
                    info.add_css_class("cv-bad")
                    row.append(info)
                elif k == "pdf":
                    e = Gtk.Entry(placeholder_text="seiten", width_chars=9, text=self.ranges.get(p, ""))
                    e.add_css_class("pb")
                    e.connect("changed", lambda e, p=p: self.ranges.__setitem__(p, e.get_text()))
                    row.append(e)
                for sym, d in (("▲", -1), ("▼", 1)):
                    b = Gtk.Button(label=sym)
                    b.add_css_class("pb"); b.add_css_class("small")
                    b.connect("clicked", lambda _b, i=i, d=d: self.move(i, d))
                    row.append(b)
            elif self.mode == "convert":
                reason = can_convert(p, self.fmt)
                info = Gtk.Label(label="✓" if reason is None else reason)
                info.add_css_class("cv-ok" if reason is None else "cv-bad")
                row.append(info)
            elif self.mode in ("pages", "shrink"):
                if k != "pdf":
                    info = Gtk.Label(label="keine PDF")
                    info.add_css_class("cv-bad")
                    row.append(info)
            x = Gtk.Button(label="✕")
            x.add_css_class("pb"); x.add_css_class("small")
            x.connect("clicked", lambda _b, i=i: self.remove(i))
            row.append(x)
            self.list_box.append(row)
        if n > 300:
            l = Gtk.Label(label=f"… und {n - 300} weitere", xalign=0)
            l.add_css_class("note")
            self.list_box.append(l)

    # --- Ausführen ---
    def start(self):
        if not self.files:
            return
        files, opts, mode = list(self.files), dict(self.opts), self.mode
        fmt, op, shrink, folder = self.fmt, self.op, self.shrink, self.out_folder
        rng = self.range_entry.get_text()
        ranges = dict(self.ranges)
        name = self.name_entry.get_text().strip() or "zusammengefügt"
        self.go_btn.set_sensitive(False)
        self.open_btn.set_visible(False)
        self.sum_lbl.set_label("läuft …")

        def outdir(p):
            return folder or os.path.dirname(os.path.abspath(p))

        def work():
            written, errors = [], []
            if mode == "merge":
                od = outdir(files[0])
                dst = free_name(od, re.sub(r"[\\/]", "_", name), ".pdf")
                merge_pdfs([(p, ranges.get(p, "")) for p in files], dst, opts["a4"])
                written.append(dst)
                return written, errors, sum(os.path.getsize(p) for p in files if os.path.exists(p))
            for p in files:
                try:
                    stem = os.path.splitext(os.path.basename(p))[0]
                    if mode == "convert":
                        written += convert_one(p, fmt, outdir(p), opts)
                    elif kind(p) != "pdf":
                        raise RuntimeError("keine PDF")
                    elif mode == "pages":
                        written += pages_op(p, op, rng, outdir(p), stem, opts["chunk"], opts["angle"])
                    else:
                        dst = free_name(outdir(p), stem + "-klein", ".pdf")
                        shrink_pdf(p, dst, shrink)
                        if os.path.getsize(dst) >= os.path.getsize(p):
                            os.remove(dst)
                            raise RuntimeError("schon klein – übersprungen")
                        written.append(dst)
                except Exception as e:
                    errors.append(f"{os.path.basename(p)}: {e}")
            return written, errors, 0

        def done(res):
            self.go_btn.set_sensitive(bool(self.files))
            if isinstance(res, Exception):
                self.sum_lbl.set_label(f"Fehler: {res}")
                self.tab.notify(f"Umwandeln fehlgeschlagen: {res}")
                return
            written, errors, _size = res
            self.last_written = written
            self.open_btn.set_visible(bool(written))
            parts = [f"{len(written)} datei{'en' if len(written) != 1 else ''} gespeichert"] if written else []
            if mode == "shrink" and written:
                a = sum(os.path.getsize(p) for p in files)
                b = sum(os.path.getsize(w) for w in written)
                parts.append(f"{human(a)} → {human(b)}")
            if errors:
                parts.append(f"{len(errors)} Fehler: " + " · ".join(errors[:3]))
            self.sum_lbl.set_label(" · ".join(parts) or "nichts erzeugt")
            if written:
                self.tab.notify("Fertig: " + parts[0])
        self.run(work, done)

    def open_folder(self):
        if self.last_written:
            Gtk.FileLauncher(file=Gio.File.new_for_path(self.last_written[0])).open_containing_folder(
                self.tab.win, None, None)

    def shutdown(self):
        self.cancel.set()
