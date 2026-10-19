# Scourge: Analyse und Bereinigung für alle Formate außer Bild/Video (die macht scourge.py/meta.py).
# Jede Analyse liefert dasselbe Paket (Analysis): Infokarten, Vorschau, Funde, eingebettete Dateien,
# Links und eine passende Bereinigung. Alles läuft lokal.
import datetime, difflib, hashlib, io, json, math, os, re, shutil, subprocess, tempfile, time, zipfile

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib
from lxml import etree
from PIL import ExifTags, Image, ImageChops, ImageDraw, ImageOps

from meta import HIGH, INFO, MID, OK, analyze_image, clean_image_bytes, finding

CACHE = os.path.expanduser("~/.cache/scourge")
MAX_TEXT = 3_000_000


# ======================================================================
# Gemeinsames
# ======================================================================
class Analysis:
    def __init__(self, path, kind, label):
        self.path, self.kind, self.label = path, kind, label
        self.cards = []            # [(Titel, [(Schlüssel, Wert), ...])]
        self.findings = []
        self.embedded = []         # [{"name", "data", "note"}]
        self.links = []            # [(Text, Ziel)]
        self.views = []            # [(Name, PIL-Bild | callable | {"text": str}, Erklärung)]
        self.pages = 0
        self.render_page = None    # callable(i) -> PIL-Bild
        self.preview = None        # PIL-Bild, wenn es keine Seiten gibt
        self.text_preview = None   # Text, wenn es kein Bild gibt
        self.text = ""             # Volltext für die Inhaltssuche
        self.clean = None          # callable(dst) -> (ok, Meldung)
        self.clean_ext = None
        self.clean_note = ""
        self.extra_clean = None    # (Knopftext, callable(dst), Endung, Erklärung), z. B. Bild-PDF
        self.player = None         # Pfad für Audio-Wiedergabe
        self.doc_obj = None        # Poppler-Dokument (PDF-Viewer)


def human_size(n):
    for unit in ("Byte", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n} {unit}" if unit == "Byte" else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024


def file_rows(path, label, mime=None):
    st = os.stat(path)
    ctype, _ = Gio.content_type_guess(path, None)
    return [("Name", os.path.basename(path)), ("Dateityp", label),
            ("MIME", mime or Gio.content_type_get_mime_type(ctype) or "?"),
            ("Dateigröße", human_size(st.st_size)),
            ("Ordner", os.path.dirname(path).replace(os.path.expanduser("~"), "~", 1)),
            ("Geändert", datetime.datetime.fromtimestamp(st.st_mtime).strftime("%d.%m.%Y %H:%M"))]


def nice_date(s):
    """ISO-Datum oder PDF-Datum (D:20260729134010+02'00') lesbar machen."""
    if not s:
        return None
    s = str(s).strip()
    m = re.match(r"D?:?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?([Zz+\-])?(\d{2})?'?(\d{2})?", s)
    if m and not re.match(r"\d{4}-", s):
        y, mo, d, h, mi, se = (m.group(i) or "00" for i in range(1, 7))
        tz = "" if not m.group(7) else (" UTC" if m.group(7) in "Zz" else f" UTC{m.group(7)}{m.group(8)}:{m.group(9) or '00'}")
        return f"{d}.{mo}.{y} {h}:{mi}:{se}{tz}"
    try:
        dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.strftime("%d.%m.%Y %H:%M:%S") + (" UTC" if dt.tzinfo and dt.utcoffset().total_seconds() == 0 else "")
    except ValueError:
        return s


def zip_time_str(t):
    try:
        return datetime.datetime(*t).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return "?"


# ---------------- Inhaltssuche: was im Text selbst steht ----------------
SENSITIVE = [
    ("E-Mail-Adressen", HIGH, r"[\w.+-]+@[\w-]+\.[\w.-]{2,}"),
    ("Telefonnummern", HIGH, r"(?<![\w+])(?:\+|00)\d{1,3}[\s/-]?\(?\d{2,5}\)?[\s/-]?\d{3,}[\s/-]?\d{0,6}|(?<!\d)0\d{2,5}[\s/-]\d{4,9}(?!\d)"),
    ("IBAN", HIGH, r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,3})?\b"),
    ("Benutzerpfade", HIGH, r"[A-Za-z]:\\+(?:Users|Dokumente und Einstellungen|Documents and Settings)\\+[^\\/\s\"'<>]+|/(?:home|Users)/[a-z_][\w.-]*"),
    ("Netzwerkpfade", HIGH, r"\\\\[\w.-]+\\[\w$.-]+"),
    ("Private Schlüssel", HIGH, r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"),
    ("Zugangsschlüssel / Tokens", HIGH, r"\b(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{36,}|sk-[A-Za-z0-9_-]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{35})\b"),
    ("Passwörter im Klartext", HIGH, r"(?i)\b(?:passwort|password|passwd|pwd|kennwort)\s*[:=]\s*\S{4,}"),
    ("IP-Adressen", MID, r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"),
    ("Koordinaten", MID, r"\b-?\d{1,2}\.\d{4,},\s*-?\d{1,3}\.\d{4,}\b"),
    ("Geburtsdaten / Datumsangaben", INFO, r"\b(?:geb\.?|geboren|Geburtsdatum)\s*:?\s*\d{1,2}\.\d{1,2}\.\d{2,4}"),
]


def luhn(num):
    d = [int(c) for c in num if c.isdigit()]
    return len(d) >= 13 and sum(d[-1::-2] + [sum(divmod(2 * x, 10)) for x in d[-2::-2]]) % 10 == 0


def scan_text(text, where="im Text"):
    """Findet persönliche oder geheime Daten im Inhalt (nicht in den Metadaten)."""
    out = []
    text = text[:MAX_TEXT]
    ibans = [m.group(0).replace(" ", "") for m in re.finditer(SENSITIVE[2][2], text)]
    for label, level, pat in SENSITIVE:
        hits = []
        for m in re.finditer(pat, text):
            v = m.group(0).strip()
            if label == "IBAN" and not re.match(r"[A-Z]{2}\d{2}", v.replace(" ", "")):
                continue
            if label == "Telefonnummern" and (len(re.sub(r"\D", "", v)) < 8 or
                                              any(re.sub(r"\D", "", v) in i for i in ibans)):
                continue  # Ziffernblock aus einer IBAN ist keine Telefonnummer
            if label == "IP-Adressen" and (v.startswith(("0.", "255.")) or v.count(".") != 3):
                continue
            if v not in hits:
                hits.append(v)
        if hits:
            ex = ", ".join(hits[:4]) + (f" … (+{len(hits) - 4})" if len(hits) > 4 else "")
            out.append(finding(level, f"{label} {where}", f"{len(hits)}× gefunden: {ex}"))
    cards = [c for c in re.findall(r"\b(?:\d[ -]?){13,19}\b", text) if luhn(c)]
    if cards:
        out.append(finding(HIGH, f"Kreditkartennummern {where}", f"{len(cards)}× (Prüfziffer gültig): {cards[0][:4]} …"))
    return out


def printable_strings(data, minlen=6, limit=MAX_TEXT):
    """Wie das Unix-Werkzeug strings: lesbare Textstücke aus Binärdaten (ASCII und UTF-16)."""
    out = [m.decode("latin-1") for m in re.findall(rb"[\x20-\x7e\xc0-\xff]{%d,}" % minlen, data[:20_000_000])]
    out += [m.decode("utf-16-le", "ignore") for m in re.findall(rb"(?:[\x20-\x7e]\x00){%d,}" % minlen, data[:20_000_000])]
    return "\n".join(out)[:limit]


def image_findings(name, data):
    """EXIF-Check für eingebettete Bilder (Dokumente, Archive, Cover)."""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        return []
    gps = gps_text(img)
    res = [f for f in analyze_image(img, data, None, gps) if f["level"] in (HIGH, MID) and "XMP" not in f["title"]]
    return [dict(f, title=f"{name}: {f['title']}") for f in res]


def gps_text(img):
    try:
        g = img.getexif().get_ifd(ExifTags.IFD.GPSInfo)

        def deg(v, ref):
            d = float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600
            return -d if ref in ("S", "W") else d
        return f"{deg(g[2], g[1]):.5f}, {deg(g[4], g[3]):.5f}" if g else None
    except Exception:
        return None


def summarize_embedded_images(an, images):
    """images: [(Name, Bytes)] -> Funde + eingebettete Liste."""
    risky = 0
    for name, data in images:
        fs = image_findings(name, data)
        if fs:
            risky += 1
            an.findings.extend(fs[:3])
        an.embedded.append({"name": name, "data": data, "note": "⚠ enthält Metadaten" if fs else "Bild"})
    if images and not risky:
        an.findings.append(finding(OK, f"{len(images)} eingebettete Bilder ohne Ort/Gerät", "Kein GPS oder Kameramodell in den Bildern."))


def run(cmd, timeout=60, binary=False):
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, text=not binary)
        return r.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return b"" if binary else ""


# ======================================================================
# PDF
# ======================================================================
NET_FS = ("fuse.gvfsd-fuse", "cifs", "smb3", "nfs", "nfs4", "fuse.sshfs", "davfs", "fuse.rclone", "9p")
_net_cache = {}          # Pfad → (mtime, größe, bytes) – Netz-Dateien nur einmal übers Netz holen
# zum Nachstellen von „PDF (geschützt)“ auf SMB (trat zuerst bei Lukas' Abgabe auf): SCOURGE_NET_TEST=1
_NET_TEST_PDF = "/run/user/1000/gvfs/smb-share:server=fs.tu-berlin.de,share=home/lhoernig/Ana2/blatt07_abgabe.pdf"


def is_remote(path):
    """Liegt die Datei auf einem Netzlaufwerk (gvfs-SMB, CIFS, NFS …)? Dort kostet jeder Lesezugriff
    eine Netz-Rundreise – Poppler springt beim Lesen viel hin und her, das dauert dann Sekunden."""
    try:
        real = os.path.abspath(path)  # kein realpath: das stat()et jede Pfadebene – übers Netz je ~0,4 s
        best, typ = "", ""
        with open("/proc/self/mounts") as fh:
            for line in fh:
                parts = line.split()
                mnt = parts[1].replace("\\040", " ")
                if (real == mnt or real.startswith(mnt.rstrip("/") + "/")) and len(mnt) > len(best):
                    best, typ = mnt, parts[2]
        return typ in NET_FS
    except (OSError, IndexError):
        return False


def file_bytes(path):
    """Ganze Datei lesen; bei Netz-Dateien aus dem Speicher, solange sie sich nicht geändert hat."""
    if not is_remote(path):
        with open(path, "rb") as fh:
            return fh.read()
    hit = _net_cache.get(path)
    if hit and time.monotonic() - hit[3] < 30:      # frisch geholt: kein erneutes stat (übers Netz bis 1 s)
        return hit[2]
    st = os.stat(path)
    if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
        _net_cache[path] = hit[:3] + (time.monotonic(),)
        return hit[2]
    with open(path, "rb", buffering=1 << 20) as fh:
        data = fh.read()
    while len(_net_cache) >= 4:
        _net_cache.pop(next(iter(_net_cache)))
    _net_cache[path] = (st.st_mtime, st.st_size, data, time.monotonic())
    return data


def local_copy(path, data=None):
    """Lokaler Pfad mit demselben Inhalt: bei Netz-Dateien eine Kopie in ~/.cache/scourge/net, sonst path."""
    if not is_remote(path):
        return path
    data = file_bytes(path) if data is None else data
    d = os.path.join(os.path.expanduser("~/.cache/scourge/net"))
    os.makedirs(d, exist_ok=True)
    for f in os.listdir(d):                 # alte Kopien (> 1 Tag) wegräumen
        fp = os.path.join(d, f)
        try:
            if os.path.getmtime(fp) < time.time() - 86400:
                os.remove(fp)
        except OSError:
            pass
    ext = os.path.splitext(path)[1]
    dst = os.path.join(d, hashlib.sha1(path.encode() + data[:4096]).hexdigest()[:16] + ext)
    if not os.path.exists(dst) or os.path.getsize(dst) != len(data):
        with open(dst + ".tmp", "wb") as fh:
            fh.write(data)
        os.replace(dst + ".tmp", dst)
    return dst


def poppler_doc(path):
    gi.require_version("Poppler", "0.18")
    from gi.repository import Poppler
    if is_remote(path):        # einmal am Stück lesen statt vieler kleiner Netz-Zugriffe
        return Poppler.Document.new_from_bytes(GLib.Bytes.new(file_bytes(path)), None)
    # filename_to_uri statt Gio.File.get_uri(): Gio macht aus gvfs-Pfaden smb://…, Poppler will file://
    return Poppler.Document.new_from_file(GLib.filename_to_uri(os.path.abspath(path)), None)


def render_pdf_page(doc, i, scale=1.5):
    import cairo
    page = doc.get_page(i)
    w, h = page.get_size()
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(w * scale), int(h * scale))
    cr = cairo.Context(surf)
    cr.set_source_rgb(1, 1, 1)
    cr.paint()
    cr.scale(scale, scale)
    page.render(cr)
    surf.flush()
    return Image.frombuffer("RGBA", (surf.get_width(), surf.get_height()), bytes(surf.get_data()),
                            "raw", "BGRA", surf.get_stride(), 1).convert("RGB")


def page_format(w_pt, h_pt):
    w, h = round(w_pt / 72 * 25.4), round(h_pt / 72 * 25.4)
    names = {(210, 297): "A4", (148, 210): "A5", (297, 420): "A3", (216, 279): "US Letter", (216, 356): "US Legal"}
    for (a, b), n in names.items():
        if abs(min(w, h) - a) <= 2 and abs(max(w, h) - b) <= 2:
            return f"{n} ({'quer' if w > h else 'hoch'})"
    return f"{w} × {h} mm"


def hidden_text_check(doc, max_pages=30):
    """Text, den man nicht sieht:
    - unter einem Balken (Schwärzung wirkungslos): Fläche über dem Text ist einfarbig und hebt sich von der
      Umgebung darüber/darunter ab,
    - in Hintergrundfarbe (unsichtbarer Text): Fläche einfarbig und genauso wie die Umgebung."""
    from PIL import ImageStat
    covered, invisible, marks = [], [], {}
    scale = 1.5
    for i in range(min(doc.get_n_pages(), max_pages)):
        page = doc.get_page(i)
        text = page.get_text() or ""
        ok, rects = page.get_text_layout()
        if not ok or not rects or len(rects) != len(text):
            continue
        img = None
        for m in re.finditer(r"\S{2,}", text):
            rs = rects[m.start():m.end()]
            x1, y1 = min(r.x1 for r in rs), min(r.y1 for r in rs)
            x2, y2 = max(r.x2 for r in rs), max(r.y2 for r in rs)
            if x2 - x1 < 2 or y2 - y1 < 2:
                continue
            if img is None:
                img = render_pdf_page(doc, i, scale).convert("L")
            X1, Y1, X2, Y2 = int(x1 * scale), int(y1 * scale), int(x2 * scale) + 1, int(y2 * scale) + 1
            iy = max(1, (Y2 - Y1) // 4)  # Kern der Textfläche: Ränder ragen oft über einen Balken hinaus
            box = ImageStat.Stat(img.crop((X1 + 2, Y1 + iy, max(X1 + 3, X2 - 2), max(Y1 + iy + 1, Y2 - iy))))
            if box.stddev[0] > 6:
                continue  # Text ist sichtbar (Kontrast in der Fläche)
            hgt = Y2 - Y1
            bands = []
            for top, bot in ((Y1 - hgt - 4, Y1 - 4), (Y2 + 4, Y2 + hgt + 4)):
                top, bot = max(0, top), min(img.size[1], bot)
                if bot - top > 2:
                    bands.append(ImageStat.Stat(img.crop((X1, top, X2, bot))).mean[0])
            differs = bands and all(abs(b - box.mean[0]) > 40 for b in bands)
            if differs and box.mean[0] < 110:
                covered.append((i, m.group(0)))
                marks.setdefault(i, []).append((x1, y1, x2, y2))
            elif not differs and len(m.group(0)) >= 3:
                invisible.append((i, m.group(0)))
    return covered, invisible, marks


# ---------------- Frühere Fassungen eines PDFs ----------------
def pdf_revisions(path, raw, doc):
    """Jede gespeicherte Fassung (bis zum jeweiligen %%EOF) als eigenes, lesbares PDF."""
    ends = [m.end() for m in re.finditer(rb"%%EOF", raw)]
    if len(ends) < 2:
        return []
    linear = doc.is_linearized() if hasattr(doc, "is_linearized") else False
    key = hashlib.sha1(f"{path}|{len(raw)}".encode()).hexdigest()[:10]
    folder = os.path.join(CACHE, "versionen")
    os.makedirs(folder, exist_ok=True)
    revs = []
    for n, e in enumerate(ends[:-1]):
        if linear and n == 0:
            continue  # bei "Fast Web View"-PDFs ist das erste %%EOF keine eigene Fassung
        data = raw[:e] + b"\n"
        p = os.path.join(folder, f"{key}-fassung{len(revs) + 1}.pdf")
        with open(p, "wb") as fh:
            fh.write(data)
        try:
            d = poppler_doc(p)
            if d.get_n_pages() == 0:
                continue
        except GLib.Error:
            continue
        mods = re.findall(rb"/ModDate\s*\((D:[^)]{4,40})\)", data)
        revs.append({"path": p, "doc": d, "data": data,
                     "date": nice_date(mods[-1].decode("latin-1")) if mods else None})
    return revs


def text_diff(old, new, context=2):
    """Zeilen- und wortgenauer Vergleich -> [(art, text)] mit art in same/del/add."""
    a, b = old.splitlines(), new.splitlines()
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            lines = a[i1:i2]
            if len(lines) > 2 * context:
                lines = lines[:context] + ["…"] + lines[-context:] if out else ["…"] + lines[-context:]
            out += [("same", l + "\n") for l in lines if l.strip() or l == "…"]
        elif tag == "replace" and (i2 - i1) == (j2 - j1) and (i2 - i1) <= 3:
            for la, lb in zip(a[i1:i2], b[j1:j2]):  # wortgenau innerhalb der Zeile
                wa, wb = la.split(" "), lb.split(" ")
                for t, x1, x2, y1, y2 in difflib.SequenceMatcher(None, wa, wb).get_opcodes():
                    if t == "equal":
                        out.append(("same", " ".join(wa[x1:x2]) + " "))
                    if t in ("delete", "replace"):
                        out.append(("del", " ".join(wa[x1:x2])))
                        out.append(("same", " "))
                    if t in ("insert", "replace"):
                        out.append(("add", " ".join(wb[y1:y2])))
                        out.append(("same", " "))
                out.append(("same", "\n"))
        else:
            out += [("del", l + "\n") for l in a[i1:i2] if l.strip()]
            out += [("add", l + "\n") for l in b[j1:j2] if l.strip()]
    return out


def changed_regions(old_doc, new_doc, max_pages=60, scale=0.6):
    """Seitenweiser Bildvergleich: {Seite: [Rechtecke in Seitenkoordinaten]}."""
    out = {}
    for i in range(min(old_doc.get_n_pages(), new_doc.get_n_pages(), max_pages)):
        a, b = render_pdf_page(old_doc, i, scale).convert("L"), render_pdf_page(new_doc, i, scale).convert("L")
        if a.size != b.size:
            out[i] = [(0, 0, b.size[0] / scale, b.size[1] / scale)]
            continue
        diff = ImageChops.difference(a, b).point(lambda v: 255 if v > 40 else 0)
        if not diff.getbbox():
            continue
        cell = 12
        gw, gh = (diff.size[0] + cell - 1) // cell, (diff.size[1] + cell - 1) // cell
        small = diff.resize((gw, gh), Image.BOX)
        grid = {(x, y) for y in range(gh) for x in range(gw) if small.getpixel((x, y)) > 0}
        boxes = []
        while grid:  # zusammenhängende Bereiche zu Rechtecken
            stack = [grid.pop()]
            xs, ys = [], []
            while stack:
                x, y = stack.pop()
                xs.append(x); ys.append(y)
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        nb = (x + dx, y + dy)
                        if nb in grid:
                            grid.remove(nb)
                            stack.append(nb)
            boxes.append((min(xs) * cell / scale, min(ys) * cell / scale, (max(xs) + 1) * cell / scale, (max(ys) + 1) * cell / scale))
        out[i] = boxes
    return out


def mark_changes(doc, page, boxes, scale=1.5):
    im = render_pdf_page(doc, page, scale).convert("RGB")
    d = ImageDraw.Draw(im)
    for x1, y1, x2, y2 in boxes:
        d.rectangle((x1 * scale - 4, y1 * scale - 4, x2 * scale + 4, y2 * scale + 4), outline=(255, 70, 70), width=4)
    return im


def analyze_pdf(path):
    an = Analysis(path, "pdf", "PDF-Dokument")
    raw = file_bytes(path)
    src = local_copy(path, raw)    # Netzlaufwerk: Hilfsprogramme lesen die lokale Kopie
    try:
        doc = poppler_doc(path)
    except GLib.Error as e:
        an.cards = [("Datei", file_rows(path, "PDF (geschützt)", "application/pdf"))]
        if "password" in str(e).lower() or b"/Encrypt" in raw:
            an.findings.append(finding(INFO, "Passwortgeschützt", "Ohne Passwort lässt sich der Inhalt nicht prüfen."))
        else:
            an.findings.append(finding(MID, "Beschädigtes PDF", str(e)[:120]))
        an.text = printable_strings(raw)
        an.findings += scan_text(an.text, "in den Rohdaten")
        return an

    n = doc.get_n_pages()
    an.pages = n
    an.render_page = lambda i: render_pdf_page(doc, i)
    w, h = doc.get_page(0).get_size() if n else (0, 0)
    text = run(["pdftotext", "-layout", "-l", "300", src, "-"])
    an.text = text
    words = len(text.split())
    img_list = run(["pdfimages", "-list", "-l", "300", src]).splitlines()[2:]
    fonts = run(["pdffonts", "-l", "300", src]).splitlines()[2:]
    font_names = sorted({l.split()[0].split("+")[-1] for l in fonts if l.strip()})
    an.cards.append(("Dokument", [("Seiten", str(n)), ("Seitenformat", page_format(w, h)),
                                  ("Wörter", f"{words:,}".replace(",", ".")), ("Bilder", str(len(img_list))),
                                  ("Schriften", ", ".join(font_names[:6]) + (" …" if len(font_names) > 6 else "") or "–"),
                                  ("PDF-Version", doc.get_pdf_version_string() or "?"),
                                  ("Formular", "ja" if b"/AcroForm" in raw else "nein"),
                                  ("Verschlüsselt", "ja" if b"/Encrypt" in raw else "nein")]))
    an.cards.append(("Datei", file_rows(path, "PDF-Dokument", "application/pdf")))
    meta = [(k, v) for k, v in (("Titel", doc.get_title()), ("Autor", doc.get_author()), ("Thema", doc.get_subject()),
                                ("Stichworte", doc.get_keywords()), ("Erstellt mit", doc.get_creator()),
                                ("PDF-Erzeuger", doc.get_producer()),
                                ("Erstellt", nice_date(info_date(raw, b"CreationDate"))),
                                ("Geändert", nice_date(info_date(raw, b"ModDate")))) if v]
    an.cards.append(("Metadaten", meta))

    # --- Funde ---
    if doc.get_author():
        an.findings.append(finding(HIGH, f"Autor: {doc.get_author()}", "Steht in den Dokumenteigenschaften."))
    for label, v in (("Erstellt mit", doc.get_creator()), ("PDF-Erzeuger", doc.get_producer())):
        if v:
            an.findings.append(finding(MID if re.search(r"\d", v) else INFO, f"{label}: {v[:60]}",
                                       "Verrät Programm, Version und oft das Betriebssystem."))
    cd = info_date(raw, b"CreationDate")
    if cd:
        an.findings.append(finding(MID, "Erstellungszeit", f"{nice_date(cd)} (mit Zeitzone, falls angegeben)."))
    xmp = doc.get_metadata() or ""
    if xmp:
        hits = [lab for pat, lab in ((r"xmpMM:DocumentID|xmpMM:InstanceID", "Dokument-IDs (verknüpfen Versionen)"),
                                     (r"xmpMM:History|stEvt:", "Bearbeitungsverlauf"), (r"dc:creator", "Urheber"),
                                     (r"photoshop:|xmp:CreatorTool", "Programm"), (r"pdfx:|custom", "eigene Felder"))
                if re.search(pat, xmp)]
        an.findings.append(finding(MID, "XMP-Metadaten", "Enthält: " + (", ".join(hits) or "Standardfelder")))
    revs = pdf_revisions(src, raw, doc)
    an.revisions = revs
    if revs:
        versions = revs + [{"path": src, "doc": doc, "date": nice_date(info_date(raw, b"ModDate")), "data": raw}]
        texts_ = [run(["pdftotext", "-layout", "-l", "300", v["path"], "-"]) for v in versions]
        segs, changed_all = [], {}
        for k in range(len(versions) - 1):
            a_, b_ = versions[k], versions[k + 1]
            label_b = "aktuelle Fassung" if k + 1 == len(versions) - 1 else f"Fassung {k + 2}"
            segs.append(("head", f"Fassung {k + 1}{' (' + a_['date'] + ')' if a_['date'] else ''}  →  "
                                 f"{label_b}{' (' + b_['date'] + ')' if b_['date'] else ''}\n"))
            d = text_diff(texts_[k], texts_[k + 1])
            segs += d if any(t in ("del", "add") for t, _ in d) else [("same", "Text unverändert (nur Layout, Bilder, Formular oder Metadaten geändert).\n")]
            if a_["doc"].get_n_pages() != b_["doc"].get_n_pages():
                segs.append(("add", f"Seitenzahl: {a_['doc'].get_n_pages()} → {b_['doc'].get_n_pages()}\n"))
            if k == len(versions) - 2:
                changed_all = changed_regions(a_["doc"], b_["doc"])
        removed = [t.strip() for t_, t in segs if t_ == "del" and t.strip()]
        added = [t.strip() for t_, t in segs if t_ == "add" and t.strip()]
        det = f"{len(revs)} frühere Fassung{'en' if len(revs) > 1 else ''} stecken noch in der Datei und sind vollständig lesbar."
        if changed_all:
            det += f" Zuletzt geändert: Seite {', '.join(str(p + 1) for p in sorted(changed_all)[:8])}."
        if removed:
            det += f" Nur in der alten Fassung: »{' / '.join(removed[:3])[:180]}«"
        elif added:
            det += f" Neu hinzugekommen: »{' / '.join(added[:3])[:180]}«"
        an.findings.append(finding(HIGH, "Frühere Fassungen wiederherstellbar", det))
        prev = revs[-1]
        an.views.append(("Frühere Fassung", {"pdf": prev["doc"]},
                         f"Die Fassung vor der letzten Änderung{' (' + prev['date'] + ')' if prev['date'] else ''}, "
                         "komplett zum Durchblättern und Durchsuchen."))
        an.views.append(("Änderungen", {"diff": segs}, "Textvergleich zwischen den Fassungen: rot durchgestrichen = "
                         "entfernt, grün = neu. Gerade Entferntes ist oft das Interessante."))
        if changed_all:
            first = sorted(changed_all)[0]
            an.views.append(("Bildvergleich", lambda f=first: mark_changes(doc, f, changed_all[f]),
                             f"Rot umrandet: was sich auf Seite {first + 1} gegenüber der früheren Fassung verändert hat "
                             f"(geändert: Seite {', '.join(str(p + 1) for p in sorted(changed_all))})."))
        for n, r in enumerate(revs):
            an.embedded.append({"name": f"fassung-{n + 1}.pdf", "data": r["data"],
                                "note": f"frühere Fassung{' vom ' + r['date'] if r['date'] else ''}"})
    js = has_js(doc, raw)
    if js:
        an.findings.append(finding(HIGH, "JavaScript im PDF", "Kann beim Öffnen Code ausführen oder Daten nach außen senden. "
                                   "Wird beim Bereinigen entfernt."))
    if b"/OpenAction" in raw and not js:
        an.findings.append(finding(INFO, "Aktion beim Öffnen", "Meist nur die Startansicht (Seite/Zoom); hier ohne Skript."))
    for key, level, title, det in ((b"/Launch", HIGH, "Startet Programme", "Kann Programme oder Dateien auf dem Rechner öffnen."),
                                   (b"/SubmitForm", MID, "Formular sendet Daten", "Eingaben können an eine Adresse geschickt werden."),
                                   (b"/RichMedia", MID, "Eingebettete Medien", "Flash/Video-Objekte im PDF.")):
        if key in raw:
            an.findings.append(finding(level, title, det))
    # Anhänge
    for att in (doc.get_attachments() or []):
        data = attachment_bytes(att)
        an.embedded.append({"name": att.name, "data": data, "note": "Anhang"})
        an.findings.append(finding(HIGH, f"Angehängte Datei: {att.name}", f"{human_size(len(data))}. Reist unsichtbar mit dem PDF mit."))
    # Links
    for uri in dict.fromkeys(u.decode("latin-1") for u in re.findall(rb"/URI\s*\(([^)]{4,300})\)", raw)):
        an.links.append((uri, uri))
    if an.links:
        an.findings.append(finding(INFO, f"{len(an.links)} Links", "Auf Tracking-Parameter achten (z. B. ?utm_, ?id=)."))
    # Schwärzungen und unsichtbarer Text
    covered, invisible, marks = hidden_text_check(doc)
    if covered:
        pages = sorted({p + 1 for p, _ in covered})
        an.findings.append(finding(HIGH, "Schwärzung wirkungslos", f"Text unter schwarzen Balken ist noch lesbar (Seite "
                                   f"{', '.join(map(str, pages[:5]))}): »{' '.join(w for _, w in covered[:12])}«"))
        first = pages[0] - 1

        def show_marks(first=first):
            im = render_pdf_page(doc, first).convert("RGB")
            d = ImageDraw.Draw(im)
            for x1, y1, x2, y2 in marks[first]:
                d.rectangle((x1 * 1.5 - 3, y1 * 1.5 - 3, x2 * 1.5 + 3, y2 * 1.5 + 3), outline=(255, 70, 70), width=4)
            return im
        an.views.append(("Schwärzungen", show_marks, "Rot umrandet: Hier liegt noch lesbarer Text unter einem Balken. "
                         "Beim Kopieren oder Durchsuchen kommt er zum Vorschein."))
    if invisible:
        an.findings.append(finding(MID, "Unsichtbarer Text", f"{len(invisible)} Wörter in Hintergrundfarbe (z. B. weiß auf weiß), also unsichtbar, aber kopier- und durchsuchbar: "
                                   f"»{' '.join(w for _, w in invisible[:10])}«"))
    # Bilder im PDF
    if img_list:
        tmp = tempfile.mkdtemp(prefix="scourge-pdfimg-")
        run(["pdfimages", "-j", "-l", "60", src, os.path.join(tmp, "bild")], timeout=120)
        imgs = []
        for f in sorted(os.listdir(tmp))[:40]:
            with open(os.path.join(tmp, f), "rb") as fh:
                imgs.append((f, fh.read()))
        shutil.rmtree(tmp, ignore_errors=True)
        summarize_embedded_images(an, imgs)
    an.findings += scan_text(text + "\n" + "\n".join(v for _, v in meta), "im Text")
    an.findings += scan_text(xmp, "in den Metadaten") if xmp else []
    an.doc_obj = doc
    an.views.insert(0, ("Dokument", {"pdf": doc}, "Das ganze Dokument. Achte auch auf Sichtbares: Unterschriften, "
                        "Stempel, Briefköpfe, Aktenzeichen. Mit der Suche (Strg+F) findest du Stichworte."))

    an.clean = lambda dst: clean_pdf(path, dst)
    an.clean_ext = ".pdf"
    an.clean_note = ("Entfernt Autor und alle Metadaten (auch XMP), Dokument-IDs, JavaScript, Aktionen beim Öffnen, "
                     "Anhänge und frühere Versionen. Text und Layout bleiben. Achtung: unwirksame Schwärzungen "
                     "bleiben unwirksam, dafür „Als Bild-PDF“ nehmen.")
    an.extra_clean = ("󰋩  Als Bild-PDF speichern", lambda dst: pdf_to_image_pdf(doc, dst), ".pdf",
                      "Jede Seite wird zu einem Bild. Entfernt garantiert alles Versteckte (Text unter Balken, "
                      "Ebenen, Formulare), Text ist danach aber nicht mehr kopierbar.")
    return an


def info_date(raw, key):
    m = re.findall(rb"/" + key + rb"\s*\((D:[^)]{4,40})\)", raw)
    return m[-1].decode("latin-1") if m else None  # der letzte Eintrag gehört zur aktuellen Fassung


def has_js(doc, raw):
    try:
        if doc.has_javascript():
            return True
    except AttributeError:
        pass
    return b"/JavaScript" in raw or re.search(rb"/JS\s*[(<]", raw) is not None


def attachment_bytes(att):
    with tempfile.TemporaryDirectory() as tmp:
        f = os.path.join(tmp, "anhang")
        att.save(f)
        return open(f, "rb").read()


def clean_pdf(src, dst):
    """qpdf schreibt das PDF neu (dabei fallen frühere Versionen weg); per JSON fliegen Metadaten & Co. raus."""
    with tempfile.TemporaryDirectory() as tmp:
        js = os.path.join(tmp, "a.json")
        try:
            r = subprocess.run(["qpdf", "--json-output=2", src, js], capture_output=True, text=True)
        except FileNotFoundError:
            return False, "qpdf fehlt (Paket qpdf installieren)"
        if r.returncode not in (0, 3):
            return False, r.stderr.strip()[-120:]
        d = json.load(open(js))
        objs = d["qpdf"][1]
        trailer = objs["trailer"]["value"]
        trailer.pop("/Info", None)
        trailer.pop("/ID", None)

        def resolve(v):
            if isinstance(v, str) and v.endswith(" R"):
                o = objs.get("obj:" + v, {})
                return o.get("value")
            return v
        for key, o in objs.items():
            val = o.get("value") if isinstance(o, dict) else None
            if not isinstance(val, dict):
                continue
            for k in ("/Metadata", "/PieceInfo", "/AA", "/OpenAction", "/JavaScript"):
                val.pop(k, None)
            names = resolve(val.get("/Names")) if "/Type" in val and val.get("/Type") == "/Catalog" else None
            if isinstance(names, dict):
                for k in ("/EmbeddedFiles", "/JavaScript"):
                    names.pop(k, None)
            act = resolve(val.get("/A"))
            if isinstance(act, dict) and act.get("/S") in ("/JavaScript", "/Launch", "/SubmitForm"):
                val.pop("/A", None)
            if val.get("/Subtype") == "/FileAttachment":
                val["/Subtype"] = "/Text"
                val.pop("/FS", None)
        json.dump(d, open(js, "w"))
        r = subprocess.run(["qpdf", "--json-input", "--deterministic-id", "--remove-unreferenced-resources=yes", js, dst],
                           capture_output=True, text=True)
        if r.returncode not in (0, 3):
            return False, r.stderr.strip()[-120:]
    return True, "bereinigt"


def pdf_to_image_pdf(doc, dst, dpi=150):
    import cairo
    w0, h0 = doc.get_page(0).get_size()
    surf = cairo.PDFSurface(dst, w0, h0)
    for i in range(doc.get_n_pages()):
        page = doc.get_page(i)
        w, h = page.get_size()
        surf.set_size(w, h)
        im = render_pdf_page(doc, i, dpi / 72).convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "PNG")
        isurf = cairo.ImageSurface.create_from_png(io.BytesIO(buf.getvalue()))
        cr = cairo.Context(surf)
        cr.scale(72 / dpi, 72 / dpi)
        cr.set_source_surface(isurf, 0, 0)
        cr.paint()
        surf.show_page()
    surf.finish()
    return True, "als Bild-PDF gespeichert"


# ======================================================================
# Office (Word, Excel, PowerPoint als .docx/.xlsx/.pptx)
# ======================================================================
NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "w15": "http://schemas.microsoft.com/office/word/2012/wordml",
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "ep": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties",
    "vt": "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes",
    "cust": "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "x15ac": "http://schemas.microsoft.com/office/spreadsheetml/2010/11/ac",
    "xtc": "http://schemas.microsoft.com/office/spreadsheetml/2018/threadedcomments",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
}
W = "{%s}" % NS["w"]
R_ID = "{%s}" % NS["r"]
OOXML_KIND = {"word/": ("docx", "Word-Dokument"), "xl/": ("xlsx", "Excel-Tabelle"), "ppt/": ("pptx", "PowerPoint-Präsentation")}


def xml(data):
    return etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True, recover=True))


def texts(root, tag="t", ns="w"):
    return [t.text or "" for t in root.iter("{%s}%s" % (NS[ns], tag))]


def rels_of(files, part):
    d, f = os.path.split(part)
    return os.path.join(d, "_rels", f + ".rels")


def rel_targets(files, part):
    """{rId: (Ziel, Typ, extern?)} für ein Teil."""
    rp = rels_of(files, part)
    out = {}
    if rp in files:
        for rel in xml(files[rp]).iter("{%s}Relationship" % NS["pr"]):
            ext = rel.get("TargetMode") == "External"
            tgt = rel.get("Target", "")
            if not ext:
                tgt = os.path.normpath(os.path.join(os.path.dirname(part), tgt)).lstrip("/") if not tgt.startswith("/") \
                    else tgt.lstrip("/")
            out[rel.get("Id")] = (tgt, rel.get("Type", "").rsplit("/", 1)[-1], ext)
    return out


def printer_name(data):
    try:
        return data[:64].decode("utf-16-le").split("\x00")[0].strip() or None
    except UnicodeDecodeError:
        return None


def analyze_ooxml(path):
    z = zipfile.ZipFile(path)
    files = {n: z.read(n) for n in z.namelist() if z.getinfo(n).file_size < 200_000_000}
    prefix = next((p for p in OOXML_KIND if any(n.startswith(p) for n in files)), "word/")
    kind, label = OOXML_KIND[prefix]
    macro = path.lower().endswith(("m", ".dotm")) or any(n.endswith("vbaProject.bin") for n in files)
    if macro:
        label += " mit Makros"
    an = Analysis(path, "office", label)

    # --- Dokumenteigenschaften ---
    core = xml(files["docProps/core.xml"]) if "docProps/core.xml" in files else None
    app = xml(files["docProps/app.xml"]) if "docProps/app.xml" in files else None

    def cv(tag, ns):
        el = core.find(f"{{{NS[ns]}}}{tag}") if core is not None else None
        return el.text.strip() if el is not None and el.text and el.text.strip() else None

    def av(tag):
        el = app.find(f"{{{NS['ep']}}}{tag}") if app is not None else None
        return el.text.strip() if el is not None and el.text and el.text.strip() else None
    creator, lastmod, company, manager = cv("creator", "dc"), cv("lastModifiedBy", "cp"), av("Company"), av("Manager")
    total = av("TotalTime")
    meta = [(k, v) for k, v in (("Titel", cv("title", "dc")), ("Thema", cv("subject", "dc")), ("Autor", creator),
                                ("Zuletzt bearbeitet von", lastmod), ("Firma", company), ("Manager", manager),
                                ("Stichworte", cv("keywords", "cp")), ("Kommentar", cv("description", "dc")),
                                ("Erstellt", nice_date(cv("created", "dcterms"))), ("Geändert", nice_date(cv("modified", "dcterms"))),
                                ("Zuletzt gedruckt", nice_date(cv("lastPrinted", "cp"))), ("Version", cv("revision", "cp")),
                                ("Bearbeitungszeit", f"{int(total) // 60} h {int(total) % 60} min" if total and total.isdigit() else None),
                                ("Vorlage", av("Template")), ("Programm", " ".join(x for x in (av("Application"), av("AppVersion")) if x)))
            if v]
    for who, label_ in ((creator, "Autor"), (lastmod, "Zuletzt bearbeitet von")):
        if who:
            an.findings.append(finding(HIGH, f"{label_}: {who}", "Steht in den Dokumenteigenschaften."))
    if company or manager:
        an.findings.append(finding(HIGH, f"Firma: {company or manager}", "Verrät deinen Arbeitgeber oder deine Organisation."))
    if total and total.isdigit() and int(total) > 0:
        an.findings.append(finding(MID, f"Bearbeitungszeit {int(total) // 60} h {int(total) % 60} min",
                                   "Zeigt, wie lange am Dokument gearbeitet wurde."))
    if cv("lastPrinted", "cp"):
        an.findings.append(finding(MID, "Zuletzt gedruckt", nice_date(cv("lastPrinted", "cp"))))
    if cv("created", "dcterms"):
        an.findings.append(finding(MID, "Zeitstempel", f"Erstellt {nice_date(cv('created', 'dcterms'))}, "
                                   f"geändert {nice_date(cv('modified', 'dcterms'))}."))
    if av("Template") and av("Template") not in ("Normal.dotm", "Normal"):
        an.findings.append(finding(MID, f"Vorlage: {av('Template')}", "Name der Vorlage verrät oft Firma oder Abteilung."))
    if av("Application"):
        an.findings.append(finding(INFO, f"Programm: {av('Application')} {av('AppVersion') or ''}".strip(), "Office-Version."))
    if "docProps/custom.xml" in files:
        props = [(p.get("name"), "".join(p.itertext()).strip()) for p in xml(files["docProps/custom.xml"])]
        if props:
            det = "; ".join(f"{k}: {v[:40]}" for k, v in props[:5])
            label_ = MID
            if any(re.search(r"@|MSIP_Label|SiteId|ContentTypeId", f"{k}{v}") for k, v in props):
                label_ = HIGH
                det = "Enthält z. B. Firmen-Kennungen oder E-Mails (Vertraulichkeits-Label von Microsoft). " + det
            an.findings.append(finding(label_, f"{len(props)} eigene Eigenschaften", det))
            meta += [(k, v[:80]) for k, v in props[:8]]

    text_parts = []
    stats = []
    # --- Word ---
    if kind == "docx":
        body_parts = [n for n in files if re.match(r"word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml$", n)]
        authors, deleted, inserted_by = set(), [], set()
        hidden = []
        for n in body_parts:
            root = xml(files[n])
            for p in root.iter(W + "p"):
                t = "".join(x.text or "" for x in p.iter(W + "t"))
                if t.strip():
                    text_parts.append(t)
            for el in root.iter(W + "del", W + "moveFrom"):
                authors.add(el.get(W + "author"))
                d = "".join(x.text or "" for x in el.iter(W + "delText"))
                if d.strip():
                    deleted.append(d.strip())
            for el in root.iter(W + "ins", W + "moveTo"):
                inserted_by.add(el.get(W + "author"))
            for r in root.iter(W + "r"):
                rpr = r.find(W + "rPr")
                if rpr is not None and rpr.find(W + "vanish") is not None:
                    t = "".join(x.text or "" for x in r.iter(W + "t"))
                    if t.strip():
                        hidden.append(t.strip())
        changers = sorted(a for a in authors | inserted_by if a)
        if deleted or inserted_by:
            an.views.append(("Änderungen", {"diff": docx_change_view(files)},
                             "Das Dokument mit seinen verfolgten Änderungen: rot durchgestrichen = gelöscht (steht noch in "
                             "der Datei), grün = eingefügt, mit Namen und Zeit der Bearbeiter."))
        if deleted or inserted_by:
            det = f"Änderungen von: {', '.join(changers) or '?'}."
            if deleted:
                det += f" Gelöschter Text ist noch drin: »{' / '.join(deleted[:4])[:220]}«"
            an.findings.append(finding(HIGH, "Änderungsverfolgung mit alten Inhalten", det))
            an.text_extra = "\n".join(deleted)
        if hidden:
            an.findings.append(finding(MID, "Versteckter Text", f"Als „ausgeblendet“ formatiert, aber in der Datei: »{' '.join(hidden)[:200]}«"))
        if "word/comments.xml" in files:
            cm = xml(files["word/comments.xml"])
            cs = [(c.get(W + "author"), "".join(t.text or "" for t in c.iter(W + "t"))) for c in cm.iter(W + "comment")]
            if cs:
                who = sorted({a for a, _ in cs if a})
                an.findings.append(finding(HIGH, f"{len(cs)} Kommentare", f"Von {', '.join(who)}: »{cs[0][1][:120]}«"))
                text_parts += [t for _, t in cs]
        if "word/people.xml" in files:
            ppl = xml(files["word/people.xml"])
            ids = [(p.get("{%s}author" % NS["w15"]), (p.find("{%s}presenceInfo" % NS["w15"]).get("{%s}userId" % NS["w15"])
                    if p.find("{%s}presenceInfo" % NS["w15"]) is not None else None)) for p in ppl]
            if ids:
                an.findings.append(finding(HIGH, "Personenliste der Bearbeiter",
                                           "; ".join(f"{a} ({u})" if u else str(a) for a, u in ids[:5])))
        if "word/settings.xml" in files:
            st = xml(files["word/settings.xml"])
            rs = st.find(W + "rsids")
            if rs is not None:
                stats.append(("Bearbeitungssitzungen", str(len(rs))))
                an.findings.append(finding(INFO, f"{len(rs)} Bearbeitungs-Kennungen (RSIDs)",
                                           "Zufallsnummern pro Sitzung; damit lassen sich Dokumente desselben Verfassers verknüpfen."))
            if st.find(W + "mailMerge") is not None:
                src = " ".join(x.get(R_ID + "id", "") for x in st.iter(W + "dataSource"))
                tg = rel_targets(files, "word/settings.xml")
                an.findings.append(finding(HIGH, "Serienbrief-Datenquelle", "Pfad zur Adressdatei: " +
                                           ", ".join(tg.get(i, ("?",))[0] for i in src.split() if i) or "eingebettet"))
            at = st.find(W + "attachedTemplate")
            if at is not None:
                tgt = rel_targets(files, "word/settings.xml").get(at.get(R_ID + "id"), ("",))[0]
                if tgt:
                    an.findings.append(finding(HIGH if re.search(r"[\\/](Users|home)[\\/]|^\\\\|file:", tgt, re.I) else MID,
                                               "Pfad der Vorlage", tgt[:160]))
        if app is not None and av("Pages"):
            stats.append(("Seiten", av("Pages")))
    # --- Excel ---
    if kind == "xlsx":
        wb = xml(files.get("xl/workbook.xml", b"<x/>"))
        sheets = [(s.get("name"), s.get("state", "visible")) for s in wb.iter("{%s}sheet" % NS["x"])]
        stats += [("Tabellenblätter", str(len(sheets)))]
        hid = [n for n, s in sheets if s == "hidden"]
        vhid = [n for n, s in sheets if s == "veryHidden"]
        if vhid:
            an.findings.append(finding(HIGH, "Sehr versteckte Blätter", f"{', '.join(vhid)}: in Excel nicht einblendbar, aber in der Datei."))
        if hid:
            an.findings.append(finding(MID, "Ausgeblendete Blätter", ", ".join(hid)))
        ap = wb.find(".//{%s}absPath" % NS["x15ac"])
        if ap is not None and ap.get("url"):
            an.findings.append(finding(HIGH, "Speicherort auf dem PC", f"{ap.get('url')}: verrät Benutzername und Ordner."))
        if "xl/sharedStrings.xml" in files:
            text_parts += texts(xml(files["xl/sharedStrings.xml"]), "t", "x")
        hidden_rows = 0
        for n in files:
            if re.match(r"xl/worksheets/sheet\d+\.xml$", n):
                sh = xml(files[n])
                hidden_rows += sum(1 for r in sh.iter("{%s}row" % NS["x"]) if r.get("hidden") in ("1", "true"))
                hidden_rows += sum(1 for c in sh.iter("{%s}col" % NS["x"]) if c.get("hidden") in ("1", "true"))
                text_parts += [v.text for v in sh.iter("{%s}v" % NS["x"]) if v.text and not v.text.replace(".", "").isdigit()]
        if hidden_rows:
            an.findings.append(finding(MID, f"{hidden_rows} ausgeblendete Zeilen/Spalten", "Inhalte sind unsichtbar, aber vorhanden."))
        cmts = [n for n in files if re.match(r"xl/comments\d*\.xml$", n)]
        if cmts:
            authors = sorted({a.text for n in cmts for a in xml(files[n]).iter("{%s}author" % NS["x"]) if a.text})
            an.findings.append(finding(HIGH, f"Kommentare in {len(cmts)} Blättern", f"Von {', '.join(authors[:5])}"))
            for n in cmts:
                text_parts += texts(xml(files[n]), "t", "x")
        if "xl/persons/person.xml" in files:
            ps = [(p.get("displayName"), p.get("userId")) for p in xml(files["xl/persons/person.xml"])]
            an.findings.append(finding(HIGH, "Kommentar-Personen", "; ".join(f"{a} ({u})" for a, u in ps[:5])))
        ext = [n for n in files if n.startswith("xl/externalLinks/_rels/")]
        if ext:
            tg = [t for n in ext for t, _, e in rel_targets(files, n.replace("_rels/", "").replace(".rels", "")).values()]
            an.findings.append(finding(HIGH, "Verknüpfungen zu anderen Dateien", ", ".join(tg[:4])[:200]))
        if "xl/connections.xml" in files:
            con = xml(files["xl/connections.xml"])
            strs = [c.get("connection") for c in con.iter() if c.get("connection")]
            an.findings.append(finding(HIGH, "Datenbank-Verbindungen", "; ".join(strs)[:200] or "Verbindungen zu externen Datenquellen."))
        if any(n.startswith("xl/pivotCache/pivotCacheRecords") for n in files):
            an.findings.append(finding(MID, "Pivot-Zwischenspeicher", "Enthält eine Kopie der Quelldaten, auch wenn das Quellblatt gelöscht wurde."))
    # --- PowerPoint ---
    if kind == "pptx":
        slides = sorted(n for n in files if re.match(r"ppt/slides/slide\d+\.xml$", n))
        stats += [("Folien", str(len(slides)))]
        hidden_slides = [n for n in slides if xml(files[n]).get("show") == "0"]
        for n in slides:
            text_parts.append(" ".join(texts(xml(files[n]), "t", "a")))
        if hidden_slides:
            an.findings.append(finding(MID, f"{len(hidden_slides)} ausgeblendete Folien", "Werden nicht gezeigt, sind aber in der Datei."))
        notes = [n for n in files if re.match(r"ppt/notesSlides/notesSlide\d+\.xml$", n)]
        note_text = [" ".join(texts(xml(files[n]), "t", "a")).strip() for n in notes]
        note_text = [t for t in note_text if t and not t.isdigit()]
        if note_text:
            an.findings.append(finding(MID, f"Sprechernotizen auf {len(note_text)} Folien", f"»{note_text[0][:160]}«"))
            text_parts += note_text
        cm = [n for n in files if re.match(r"ppt/comments/.*\.xml$", n)]
        if cm:
            names = []
            for af in ("ppt/commentAuthors.xml", "ppt/authors.xml"):
                if af in files:
                    names += [a.get("name") for a in xml(files[af]) if a.get("name")]
            an.findings.append(finding(HIGH, f"Kommentare ({len(cm)} Dateien)", f"Von {', '.join(names[:5]) or '?'}"))

    # --- Alles, was in allen drei steckt ---
    if macro:
        an.findings.append(finding(HIGH, "Makros (VBA)", "Programmcode im Dokument. Bei fremden Dateien nie aktivieren."))
    printers = [printer_name(files[n]) for n in files if "printerSettings" in n]
    if any(printers):
        an.findings.append(finding(MID, "Druckername", ", ".join(p for p in printers if p)))
    for part in [n for n in files if n.endswith(".xml") and not n.startswith("[")]:
        for rid, (tgt, typ, ext) in rel_targets(files, part).items():
            if not ext:
                continue
            if re.match(r"^(\\\\|file:)", tgt, re.I):
                an.findings.append(finding(HIGH, "Netzwerk- oder Dateipfad", f"{tgt[:160]}: verrät interne Server/Ordner."))
            elif typ == "image":
                an.findings.append(finding(MID, "Externes Bild (Tracking möglich)", f"Wird beim Öffnen geladen: {tgt[:120]}"))
            an.links.append((tgt, tgt))
    an.links = list(dict.fromkeys(an.links))
    embeds = [n for n in files if "/embeddings/" in n]
    if embeds:
        an.findings.append(finding(MID, f"{len(embeds)} eingebettete Dateien/Objekte", ", ".join(os.path.basename(n) for n in embeds[:5])))
        for n in embeds:
            an.embedded.append({"name": os.path.basename(n), "data": files[n], "note": "eingebettet"})
    media = [(os.path.basename(n), files[n]) for n in files if "/media/" in n and
             n.lower().endswith((".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".gif", ".heic"))]
    summarize_embedded_images(an, media[:40])
    thumb = next((n for n in files if n.startswith("docProps/thumbnail")), None)
    if thumb:
        try:
            ti = Image.open(io.BytesIO(files[thumb]))
            ti.load()
            an.preview = ti
            an.views.append(("Vorschaubild", ti, "Dieses Vorschaubild speichert Office mit. Es kann einen älteren Stand zeigen."))
            an.findings.append(finding(INFO, "Vorschaubild der ersten Seite", "Wird beim Bereinigen entfernt."))
        except Exception:
            pass

    an.text = "\n".join(text_parts) + "\n" + getattr(an, "text_extra", "")
    words = len(an.text.split())
    an.text_preview = "\n".join(text_parts)[:20000] or "(kein Text)"
    an.views.append(("Text", {"text": an.text_preview}, "Der Text, wie er in der Datei steht, inklusive Kommentaren und Notizen."))
    an.cards.append(("Inhalt", stats + [("Wörter", f"{words:,}".replace(",", ".")), ("Bilder", str(len(media))),
                                        ("Makros", "ja" if macro else "nein")]))
    an.cards.append(("Datei", file_rows(path, label)))
    an.cards.append(("Metadaten", meta or [("Metadaten", "keine")]))
    an.findings += scan_text(an.text, "im Text")
    an.clean = lambda dst: clean_ooxml(path, dst, kind)
    an.clean_ext = os.path.splitext(path)[1]
    an.clean_note = ("Entfernt Autor, Bearbeiter, Firma, Zeiten, Vorlagenpfad, eigene Eigenschaften, Kommentare, "
                     "Bearbeiter-Liste, Bearbeitungs-Kennungen, Druckerdaten und das Vorschaubild. Änderungen werden "
                     "angenommen (gelöschter Text verschwindet), Bilder verlieren ihre EXIF-Daten. "
                     "Makros, ausgeblendete Blätter und Sprechernotizen bleiben: die bitte selbst prüfen.")
    return an


def docx_change_view(files):
    segs = []
    for n in ("word/document.xml",):
        root = xml(files[n])
        for p in root.iter(W + "p"):
            line = []
            for el in p:
                tag = etree.QName(el).localname
                if tag == "r":
                    line.append(("same", "".join(t.text or "" for t in el.iter(W + "t"))))
                elif tag in ("ins", "moveTo"):
                    who = f" [{el.get(W + 'author', '?')}, {nice_date(el.get(W + 'date')) or ''}]"
                    line += [("add", "".join(t.text or "" for t in el.iter(W + "t"))), ("head", who)]
                elif tag in ("del", "moveFrom"):
                    who = f" [{el.get(W + 'author', '?')}, {nice_date(el.get(W + 'date')) or ''}]"
                    line += [("del", "".join(t.text or "" for t in el.iter(W + "delText"))), ("head", who)]
            if any(t.strip() for _, t in line):
                segs += [x for x in line if x[1]] + [("same", "\n")]
    return segs


def clean_ooxml(src, dst, kind):
    z = zipfile.ZipFile(src)
    order = z.namelist()
    files = {n: z.read(n) for n in order}
    remove = {n for n in files if re.match(
        r"(word/(comments\w*\.xml|people\.xml)|xl/(comments\d*\.xml|threadedComments/.*|persons/.*|printerSettings/.*)|"
        r"ppt/(comments/.*|commentAuthors\.xml|authors\.xml)|docProps/custom\.xml|docProps/thumbnail\.\w+|"
        r"word/printerSettings/.*|ppt/printerSettings/.*|customXml/.*)$", n)}
    if any(re.match(r"xl/comments\d*\.xml$", n) for n in remove):
        remove |= {n for n in files if re.match(r"xl/drawings/vmlDrawing\d+\.vml$", n)}
    remove |= {rels_of(files, n) for n in remove if rels_of(files, n) in files}

    # Beziehungen auf entfernte Teile kappen; merken, welche rIds pro Teil weggefallen sind
    dropped = {}
    for n in list(files):
        if not n.endswith(".rels") or n in remove:
            continue
        root = xml(files[n])
        owner = n.replace("_rels/", "").rsplit(".rels", 1)[0]
        changed = False
        for rel in list(root):
            tgt = rel.get("Target", "")
            if rel.get("TargetMode") == "External":
                typ = rel.get("Type", "").rsplit("/", 1)[-1]
                if typ in ("attachedTemplate", "image", "frame", "subDocument") or \
                        (typ != "hyperlink" and re.match(r"^(file:|\\\\)", tgt, re.I)):
                    dropped.setdefault(owner, set()).add(rel.get("Id"))  # Vorlagenpfad, Tracking-Bilder, Netzwerkpfade
                    root.remove(rel)
                    changed = True
                continue
            full = (os.path.normpath(os.path.join(os.path.dirname(owner), tgt)) if not tgt.startswith("/") else tgt).lstrip("/")
            if full in remove:
                dropped.setdefault(owner, set()).add(rel.get("Id"))
                root.remove(rel)
                changed = True
        if changed:
            files[n] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    ct = xml(files["[Content_Types].xml"])
    for o in list(ct):
        if o.get("PartName", "").lstrip("/") in remove:
            ct.remove(o)
    files["[Content_Types].xml"] = etree.tostring(ct, xml_declaration=True, encoding="UTF-8", standalone=True)

    def strip_props(name, tags):
        if name in files:
            root = xml(files[name])
            for el in list(root):
                if etree.QName(el).localname in tags:
                    root.remove(el)
            files[name] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    strip_props("docProps/core.xml", {"creator", "lastModifiedBy", "lastPrinted", "revision", "created", "modified",
                                      "keywords", "description", "category", "contentStatus", "identifier"})
    strip_props("docProps/app.xml", {"Company", "Manager", "Template", "TotalTime", "HyperlinkBase", "Application", "AppVersion"})

    rsid_attrs = {W + a for a in ("rsidR", "rsidRPr", "rsidP", "rsidRDefault", "rsidDel", "rsidSect", "rsidTr")}
    for n in list(files):
        if n in remove or not n.endswith(".xml"):
            continue
        root = None
        if kind == "docx" and re.match(r"word/(document|header\d*|footer\d*|footnotes|endnotes|settings)\.xml$", n):
            root = xml(files[n])
            for el in list(root.iter(W + "del", W + "moveFrom", W + "rPrChange", W + "pPrChange", W + "sectPrChange",
                                     W + "tblPrChange", W + "trPrChange", W + "tcPrChange", W + "tblGridChange",
                                     W + "numberingChange", W + "moveFromRangeStart", W + "moveFromRangeEnd",
                                     W + "moveToRangeStart", W + "moveToRangeEnd", W + "commentRangeStart",
                                     W + "commentRangeEnd", W + "rsids", W + "attachedTemplate", W + "mailMerge",
                                     W + "trackRevisions", W + "dataBinding")):
                if el.getparent() is not None:
                    el.getparent().remove(el)
            for el in list(root.iter(W + "ins", W + "moveTo")):  # Einfügungen annehmen: Inhalt behalten
                parent = el.getparent()
                if parent is None:
                    continue
                idx = parent.index(el)
                for child in list(el):
                    parent.insert(idx, child)
                    idx += 1
                parent.remove(el)
            for r in list(root.iter(W + "r")):  # als "ausgeblendet" formatierter Text
                rpr = r.find(W + "rPr")
                if rpr is not None and rpr.find(W + "vanish") is not None and r.getparent() is not None:
                    r.getparent().remove(r)
            for ref in list(root.iter(W + "commentReference")):
                r = ref.getparent()
                if r is not None and r.getparent() is not None:
                    r.getparent().remove(r)
            for el in root.iter():
                for a in rsid_attrs & set(el.attrib):
                    del el.attrib[a]
            for el in list(root.iter("{http://schemas.microsoft.com/office/word/2010/wordml}docId",
                                     "{http://schemas.microsoft.com/office/word/2012/wordml}docId")):
                el.getparent().remove(el)
        if n in dropped:
            root = root if root is not None else xml(files[n])
            for el in list(root.iter()):
                for a in (R_ID + "id", R_ID + "embed", R_ID + "link"):
                    if el.get(a) in dropped[n]:
                        if etree.QName(el).localname in ("legacyDrawing", "legacyDrawingHF"):
                            el.getparent().remove(el)
                        else:
                            del el.attrib[a]
        if kind == "xlsx" and n == "xl/workbook.xml":
            root = root if root is not None else xml(files[n])
            for el in list(root.iter("{%s}absPath" % NS["x15ac"], "{http://schemas.microsoft.com/office/spreadsheetml/2014/revision}revisionPtr")):
                alt = el.getparent()
                while alt is not None and etree.QName(alt).localname not in ("AlternateContent", "workbook"):
                    alt = alt.getparent()
                if alt is not None and etree.QName(alt).localname == "AlternateContent":
                    alt.getparent().remove(alt)
                elif el.getparent() is not None:
                    el.getparent().remove(el)
        if root is not None:
            files[n] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

    for n in list(files):  # Bilder ohne EXIF
        if "/media/" in n and n.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            try:
                img = Image.open(io.BytesIO(files[n]))
                img.load()
                if img.getexif() or img.info.get("xmp") or img.info.get("exif"):
                    files[n] = clean_image_bytes(img, files[n])[0]
            except Exception:
                pass
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as out:
        for n in order:
            if n not in remove:
                zi = zipfile.ZipInfo(n, date_time=(1980, 1, 1, 0, 0, 0))
                zi.compress_type = zipfile.ZIP_DEFLATED
                out.writestr(zi, files[n])
    return True, "bereinigt"


# ======================================================================
# OpenDocument (LibreOffice: .odt, .ods, .odp)
# ======================================================================
ODF = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "meta": "urn:oasis:names:tc:opendocument:xmlns:meta:1.0",
    "dc": "http://purl.org/dc/elements/1.1/",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "config": "urn:oasis:names:tc:opendocument:xmlns:config:1.0",
    "manifest": "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0",
    "xlink": "http://www.w3.org/1999/xlink",
}
ODF_LABEL = {"text": "Textdokument (OpenDocument)", "spreadsheet": "Tabelle (OpenDocument)",
             "presentation": "Präsentation (OpenDocument)", "graphics": "Zeichnung (OpenDocument)"}


def analyze_odf(path):
    z = zipfile.ZipFile(path)
    files = {n: z.read(n) for n in z.namelist()}
    mt = files.get("mimetype", b"").decode()
    label = next((v for k, v in ODF_LABEL.items() if k in mt), "OpenDocument")
    an = Analysis(path, "odf", label)
    meta, stats = [], []
    if "meta.xml" in files:
        m = xml(files["meta.xml"])

        def g(tag, ns="meta"):
            el = m.find(f".//{{{ODF[ns]}}}{tag}")
            return el.text.strip() if el is not None and el.text else None
        first, last, printed = g("initial-creator"), g("creator", "dc"), g("printed-by")
        dur = g("editing-duration")
        meta = [(k, v) for k, v in (("Titel", g("title", "dc")), ("Autor", first), ("Zuletzt bearbeitet von", last),
                                    ("Gedruckt von", printed), ("Erstellt", nice_date(g("creation-date"))),
                                    ("Geändert", nice_date(g("date", "dc"))), ("Gedruckt", nice_date(g("print-date"))),
                                    ("Bearbeitungen", g("editing-cycles")), ("Bearbeitungszeit", dur),
                                    ("Programm", g("generator"))) if v]
        for who, lab in ((first, "Autor"), (last, "Zuletzt bearbeitet von"), (printed, "Gedruckt von")):
            if who:
                an.findings.append(finding(HIGH, f"{lab}: {who}", "Steht in den Dokumenteigenschaften."))
        if g("generator"):
            an.findings.append(finding(INFO, f"Programm: {g('generator')[:60]}", "Verrät Programm, Version und Betriebssystem."))
        if g("editing-cycles") or dur:
            an.findings.append(finding(MID, "Bearbeitungsverlauf", f"{g('editing-cycles') or '?'} Mal gespeichert, Gesamtzeit {dur or '?'}."))
        for ud in m.iter(f"{{{ODF['meta']}}}user-defined"):
            an.findings.append(finding(MID, f"Eigene Eigenschaft: {ud.get('{%s}name' % ODF['meta'])}", (ud.text or "")[:100]))
        st = m.find(f".//{{{ODF['meta']}}}document-statistic")
        if st is not None:
            de = {"page": "Seiten", "paragraph": "Absätze", "word": "Wörter (Statistik)", "character": "Zeichen",
                  "table": "Tabellen", "image": "Bilder (Statistik)", "object": "Objekte", "cell": "Zellen"}
            stats = [(de.get(k.split("}")[-1].replace("-count", ""), k.split("}")[-1]), v) for k, v in st.attrib.items()
                     if k.split("}")[-1].replace("-count", "") in de][:5]
    texts_ = []
    for part in ("content.xml", "styles.xml"):
        if part not in files:
            continue
        c = xml(files[part])
        if part == "content.xml":
            body = c.find(f"{{{ODF['office']}}}body")
            texts_.append("\n".join(t for t in (body.itertext() if body is not None else []) if t.strip()))
        tc = c.find(f".//{{{ODF['text']}}}tracked-changes")
        if tc is not None and len(tc):
            who = sorted({e.text for e in tc.iter(f"{{{ODF['dc']}}}creator") if e.text})
            deleted = [" ".join(" ".join(p.itertext()) for p in d.iter(f"{{{ODF['text']}}}p")).strip()
                       for d in tc.iter(f"{{{ODF['text']}}}deletion")]
            deleted = [d for d in deleted if d]
            an.findings.append(finding(HIGH, "Änderungsverfolgung mit alten Inhalten",
                                       f"Von {', '.join(who) or '?'}." + (f" Gelöscht: »{' / '.join(deleted[:3])[:200]}«" if deleted else "")))
            if deleted:
                an.views.append(("Änderungen", {"diff": [("head", f"Gelöscht von {', '.join(who) or '?'}:\n")] +
                                                 [("del", d + "\n") for d in deleted]},
                                 "Gelöschte Passagen, die noch in der Datei stecken."))
        notes = list(c.iter(f"{{{ODF['office']}}}annotation"))
        if notes:
            who = sorted({e.text for n_ in notes for e in n_.iter(f"{{{ODF['dc']}}}creator") if e.text})
            an.findings.append(finding(HIGH, f"{len(notes)} Kommentare", f"Von {', '.join(who) or '?'}: »{' '.join(' '.join(p.itertext()) for p in notes[0].iter('{urn:oasis:names:tc:opendocument:xmlns:text:1.0}p'))[:120]}«"))
        hidden = [e for e in c.iter() if e.get(f"{{{ODF['text']}}}display") == "none"
                  or etree.QName(e).localname in ("hidden-paragraph", "hidden-text")]
        if hidden:
            an.findings.append(finding(MID, "Versteckte Abschnitte", f"{len(hidden)} ausgeblendete Stellen mit Inhalt."))
        for e in c.iter():
            href = e.get(f"{{{ODF['xlink']}}}href")
            if href and not href.startswith(("#", "Pictures/", "./")):
                an.links.append((href, href))
                if re.match(r"^(file:|\\\\|/home/|/Users/|[A-Z]:\\)", href, re.I):
                    an.findings.append(finding(HIGH, "Dateipfad im Dokument", href[:160]))
    if "settings.xml" in files:
        s = xml(files["settings.xml"])
        pn = [e.text for e in s.iter(f"{{{ODF['config']}}}config-item") if e.get(f"{{{ODF['config']}}}name") == "PrinterName" and e.text]
        if pn:
            an.findings.append(finding(MID, f"Druckername: {pn[0]}", "Verrät deinen Drucker, oft mit Netzwerkname."))
    if any(n.startswith(("Basic/", "Scripts/")) and n.endswith((".xml", ".py", ".js")) and len(files[n]) > 300 for n in files):
        an.findings.append(finding(HIGH, "Makros", "Programmcode im Dokument. Bei fremden Dateien nie aktivieren."))
    pics = [(os.path.basename(n), files[n]) for n in files if n.startswith("Pictures/")]
    summarize_embedded_images(an, pics[:40])
    if "Thumbnails/thumbnail.png" in files:
        ti = Image.open(io.BytesIO(files["Thumbnails/thumbnail.png"]))
        ti.load()
        an.preview = ti
        an.views.append(("Vorschaubild", ti, "Dieses Vorschaubild speichert LibreOffice mit. Es kann einen älteren Stand zeigen."))
    an.links = list(dict.fromkeys(an.links))
    an.text = "\n".join(texts_)
    an.text_preview = an.text[:20000] or "(kein Text)"
    an.views.append(("Text", {"text": an.text_preview}, "Der Text, wie er in der Datei steht."))
    an.cards = [("Inhalt", stats + [("Wörter", str(len(an.text.split()))), ("Bilder", str(len(pics)))]),
                ("Datei", file_rows(path, label, mt)), ("Metadaten", meta or [("Metadaten", "keine")])]
    an.findings += scan_text(an.text, "im Text")
    an.clean = lambda dst: clean_odf(path, dst)
    an.clean_ext = os.path.splitext(path)[1]
    an.clean_note = ("Entfernt Autor, Bearbeiter, Drucker, Zeiten, Bearbeitungsverlauf, eigene Eigenschaften, "
                     "Kommentare und das Vorschaubild; Änderungen werden angenommen; Bilder verlieren ihre EXIF-Daten.")
    return an


def clean_odf(src, dst):
    z = zipfile.ZipFile(src)
    order = z.namelist()
    files = {n: z.read(n) for n in order}
    remove = {n for n in files if n.startswith("Thumbnails/")}
    if "meta.xml" in files:
        m = xml(files["meta.xml"])
        drop = {"initial-creator", "creator", "printed-by", "print-date", "user-defined", "editing-cycles",
                "editing-duration", "generator", "creation-date", "date", "template"}
        for el in list(m.iter()):
            if etree.QName(el).localname in drop and el.getparent() is not None:
                el.getparent().remove(el)
        files["meta.xml"] = etree.tostring(m, xml_declaration=True, encoding="UTF-8")
    for part in ("content.xml", "styles.xml"):
        if part in files:
            c = xml(files[part])
            for el in list(c.iter(f"{{{ODF['text']}}}tracked-changes", f"{{{ODF['text']}}}change",
                                  f"{{{ODF['text']}}}change-start", f"{{{ODF['text']}}}change-end",
                                  f"{{{ODF['office']}}}annotation", f"{{{ODF['office']}}}annotation-end")):
                if el.getparent() is not None:
                    tail = el.tail
                    parent = el.getparent()
                    prev = el.getprevious()
                    parent.remove(el)
                    if tail:  # Text nach dem Element nicht verlieren
                        if prev is not None:
                            prev.tail = (prev.tail or "") + tail
                        else:
                            parent.text = (parent.text or "") + tail
            files[part] = etree.tostring(c, xml_declaration=True, encoding="UTF-8")
    if "settings.xml" in files:
        s = xml(files["settings.xml"])
        for el in list(s.iter(f"{{{ODF['config']}}}config-item")):
            if el.get(f"{{{ODF['config']}}}name") in ("PrinterName", "PrinterSetup", "PrinterPaperFromSetup"):
                el.getparent().remove(el)
        files["settings.xml"] = etree.tostring(s, xml_declaration=True, encoding="UTF-8")
    if "META-INF/manifest.xml" in files:
        mf = xml(files["META-INF/manifest.xml"])
        for e in list(mf):
            if e.get(f"{{{ODF['manifest']}}}full-path", "").startswith("Thumbnails/"):
                mf.remove(e)
        files["META-INF/manifest.xml"] = etree.tostring(mf, xml_declaration=True, encoding="UTF-8")
    for n in list(files):
        if n.startswith("Pictures/") and n.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            try:
                img = Image.open(io.BytesIO(files[n]))
                img.load()
                files[n] = clean_image_bytes(img, files[n])[0]
            except Exception:
                pass
    with zipfile.ZipFile(dst, "w") as out:
        out.writestr(zipfile.ZipInfo("mimetype", date_time=(1980, 1, 1, 0, 0, 0)), files.get("mimetype", b""),
                     compress_type=zipfile.ZIP_STORED)  # muss als Erstes und unkomprimiert drin sein
        for n in order:
            if n != "mimetype" and n not in remove:
                out.writestr(zipfile.ZipInfo(n, date_time=(1980, 1, 1, 0, 0, 0)), files[n], compress_type=zipfile.ZIP_DEFLATED)
    return True, "bereinigt"


# ======================================================================
# E-Book (EPUB)
# ======================================================================
def analyze_epub(path):
    an = Analysis(path, "epub", "E-Book (EPUB)")
    z = zipfile.ZipFile(path)
    files = {n: z.read(n) for n in z.namelist() if z.getinfo(n).file_size < 50_000_000}
    cont = xml(files.get("META-INF/container.xml", b"<x/>"))
    opf_path = next((r.get("full-path") for r in cont.iter() if r.get("full-path")), None)
    meta = []
    if opf_path and opf_path in files:
        opf = xml(files[opf_path])
        for el in opf.iter():
            if not isinstance(el.tag, str):
                continue  # Kommentare
            ln = etree.QName(el).localname
            if el.text and el.text.strip() and etree.QName(el).namespace == ODF["dc"]:
                meta.append((ln, el.text.strip()[:100]))
            if ln == "meta" and el.get("name") in ("generator", "calibre:user_metadata") :
                meta.append((el.get("name"), (el.get("content") or "")[:80]))
        for k, v in meta:
            if k in ("creator", "contributor", "publisher"):
                an.findings.append(finding(INFO if k == "creator" else MID, f"{k}: {v}", "In den Buch-Metadaten."))
            if k == "identifier" and re.match(r"(urn:)?uuid", v, re.I):
                an.findings.append(finding(MID, "Eindeutige Buch-ID", f"{v}: verknüpft Kopien dieses E-Books."))
    texts_ = []
    for n, d in files.items():
        if n.endswith((".xhtml", ".html", ".htm")):
            texts_.append(" ".join(xml(d).itertext()))
    an.text = "\n".join(texts_)
    imgs = [(os.path.basename(n), d) for n, d in files.items() if n.lower().endswith((".jpg", ".jpeg", ".png"))]
    summarize_embedded_images(an, imgs[:30])
    cover = max(imgs, key=lambda x: len(x[1]), default=None)
    if cover:
        try:
            an.preview = Image.open(io.BytesIO(cover[1]))
            an.preview.load()
            an.views.append(("Cover", an.preview, "Das größte Bild im Buch, meist das Cover."))
        except Exception:
            pass
    an.text_preview = an.text[:20000]
    an.views.append(("Text", {"text": an.text_preview}, "Buchtext."))
    an.cards = [("Inhalt", [("Kapitel", str(sum(n.endswith((".xhtml", ".html")) for n in files))),
                            ("Wörter", str(len(an.text.split()))), ("Bilder", str(len(imgs)))]),
                ("Datei", file_rows(path, "E-Book (EPUB)")), ("Metadaten", meta or [("Metadaten", "keine")])]
    an.findings += scan_text(an.text, "im Text")
    return an


# ======================================================================
# Audio
# ======================================================================
def analyze_audio(path):
    an = Analysis(path, "audio", "Audiodatei")
    out = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path])
    d = json.loads(out or "{}")
    fmt = d.get("format", {})
    a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), {})
    cover = next((s for s in d.get("streams", []) if s.get("disposition", {}).get("attached_pic")), None)
    tags = {k.lower(): v for k, v in {**fmt.get("tags", {}), **a.get("tags", {})}.items()}
    dur = float(fmt.get("duration") or 0)
    an.label = f"Audio ({fmt.get('format_long_name', a.get('codec_name', '?'))})"
    ch = {1: "Mono", 2: "Stereo"}.get(a.get("channels"), f"{a.get('channels')} Kanäle")
    an.cards.append(("Ton", [("Dauer", f"{int(dur) // 60}:{int(dur) % 60:02d}"), ("Codec", a.get("codec_long_name", "?")),
                             ("Kanäle", ch), ("Abtastrate", f"{int(a.get('sample_rate', 0)) / 1000:.1f} kHz".replace(".", ",")),
                             ("Bitrate", f"{int(fmt.get('bit_rate', 0)) // 1000} kbit/s")]))
    an.cards.append(("Datei", file_rows(path, an.label)))
    nice = {"title": "Titel", "artist": "Künstler", "album": "Album", "date": "Datum", "genre": "Genre", "comment": "Kommentar",
            "encoder": "Encoder", "encoded_by": "Kodiert von", "creation_time": "Aufgenommen", "copyright": "Copyright"}
    an.cards.append(("Metadaten", [(nice.get(k, k), str(v)[:100]) for k, v in tags.items()] or [("Metadaten", "keine")]))
    loc = tags.get("location") or tags.get("com.apple.quicktime.location.iso6709")
    m = re.match(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)", loc or "")
    if m:
        g = f"{float(m.group(1)):.5f}, {float(m.group(2)):.5f}"
        an.findings.append(finding(HIGH, "Aufnahmeort (GPS)", f"{g}: z. B. bei Sprachaufnahmen vom Handy.", gps=g))
    for k in ("com.android.manufacturer", "com.android.model", "com.apple.quicktime.model", "make", "model"):
        if tags.get(k):
            an.findings.append(finding(MID, f"Gerät: {tags[k]}", "Verrät das Aufnahmegerät."))
    if tags.get("creation_time"):
        an.findings.append(finding(MID, "Aufnahmezeitpunkt", nice_date(tags["creation_time"])))
    for k in ("comment", "encoded_by", "copyright", "artist"):
        if tags.get(k):
            an.findings.append(finding(MID if k != "artist" else INFO, f"{nice.get(k, k)}: {str(tags[k])[:60]}", "Text-Tag in der Datei."))
    if tags.get("encoder"):
        an.findings.append(finding(INFO, f"Encoder: {tags['encoder']}", "Verrät die Aufnahme-App oder das Programm."))
    an.findings.append(finding(MID, "Tonspur selbst", "Stimmen, Namen, Durchsagen oder Hintergrundgeräusche können "
                                                     "Personen und Orte verraten. Das kann keine Metadaten-Bereinigung entfernen."))
    if cover:
        data = run(["ffmpeg", "-v", "error", "-i", path, "-map", f"0:{cover['index']}", "-c", "copy", "-f", "image2pipe", "-"],
                   binary=True)
        if data:
            try:
                ci = Image.open(io.BytesIO(data))
                ci.load()
                an.views.append(("Cover", ci, "Eingebettetes Cover-Bild."))
                an.embedded.append({"name": "cover." + (ci.format or "jpg").lower(), "data": data, "note": "Cover"})
                an.findings += image_findings("Cover", data)
            except Exception:
                pass
    wave = os.path.join(CACHE, "wellenform.png")
    os.makedirs(CACHE, exist_ok=True)
    run(["ffmpeg", "-v", "error", "-y", "-i", path, "-filter_complex",
         "aformat=channel_layouts=mono,showwavespic=s=1600x500:colors=0x8fcef3", "-frames:v", "1", wave])
    if os.path.exists(wave):
        wi = Image.open(wave).convert("RGBA")
        bg = Image.new("RGBA", wi.size, (24, 24, 28, 255))
        bg.alpha_composite(wi)
        an.preview = bg.convert("RGB")
        an.views.insert(0, ("Wellenform", an.preview, "Lautstärkeverlauf der Aufnahme."))
    an.player = path  # scourge.py wandelt zum Abspielen in Opus um (GStreamer stürzt hier bei MP3 ab, AAC fehlt)
    an.text = " ".join(str(v) for v in tags.values())
    an.findings += scan_text(an.text, "in den Tags")
    an.clean = lambda dst: (lambda r: (r.returncode == 0, r.stderr[-120:] or "bereinigt"))(
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-map", "0:a", "-map_metadata", "-1", "-c", "copy",
                        "-fflags", "+bitexact", "-flags:a", "+bitexact", dst], capture_output=True, text=True))
    an.clean_ext = os.path.splitext(path)[1]
    an.clean_note = "Entfernt alle Tags (Titel, Kommentare, Ort, Gerät, Zeit) und das Cover. Der Ton bleibt unverändert."
    return an


# ======================================================================
# Archive
# ======================================================================
JUNK = [(r"(^|/)\.DS_Store$", MID, "macOS-Ordnerdatei: verrät Dateinamen im Ordner, auch gelöschte"),
        (r"(^|/)__MACOSX/", MID, "macOS-Zusatzdaten (Ressourcen, Metadaten)"),
        (r"(^|/)Thumbs\.db$", MID, "Windows-Vorschaubilder des Ordners, auch von gelöschten Bildern"),
        (r"(^|/)desktop\.ini$", INFO, "Windows-Ordnereinstellungen"),
        (r"(^|/)\.git/", HIGH, "Git-Verlauf: die ganze Änderungsgeschichte samt E-Mail-Adressen"),
        (r"(^|/)\.(env|netrc|npmrc|pypirc|htpasswd)$|credentials|secrets?\.(json|ya?ml)", HIGH, "Zugangsdaten-Datei"),
        (r"(^|/)(id_rsa|id_ed25519|id_ecdsa)$|\.(pem|key|p12|pfx|kdbx)$", HIGH, "Schlüssel oder Passwort-Datenbank"),
        (r"(^|/)\.(bash|zsh)_history$|\.(sqlite|db)$", MID, "Verlauf oder Datenbank")]


def analyze_archive(path):
    is_zip = zipfile.is_zipfile(path)
    an = Analysis(path, "archive", "ZIP-Archiv" if is_zip else "Archiv")
    entries = []
    if is_zip:
        z = zipfile.ZipFile(path)
        infos = z.infolist()
        systems = {0: "Windows/DOS", 3: "Unix/Linux", 19: "macOS"}
        made = {systems.get(i.create_system, str(i.create_system)) for i in infos}
        def extra_ids(e):
            ids = set()
            while len(e) >= 4:
                hid, ln = int.from_bytes(e[:2], "little"), int.from_bytes(e[2:4], "little")
                ids.add(hid)
                e = e[4 + ln:]
            return ids
        uid = any(extra_ids(i.extra) & {0x7875, 0x5855} for i in infos)
        enc = any(i.flag_bits & 1 for i in infos)
        for i in infos:
            entries.append((i.filename, i.file_size, zip_time_str(i.date_time)))
        if z.comment:
            an.findings.append(finding(MID, "Archiv-Kommentar", z.comment.decode("utf-8", "ignore")[:160]))
        if any(i.comment for i in infos):
            an.findings.append(finding(MID, "Datei-Kommentare", "; ".join(i.comment.decode("utf-8", "ignore") for i in infos if i.comment)[:160]))
        an.findings.append(finding(INFO, f"Erstellt unter {', '.join(sorted(made))}", "Verrät das Betriebssystem."))
        if uid:
            an.findings.append(finding(MID, "Unix-Benutzerkennungen", "Die Einträge speichern Benutzer- und Gruppen-ID des Erstellers."))
        if enc:
            an.findings.append(finding(MID, "Verschlüsselte Einträge", "Alte ZIP-Verschlüsselung ist leicht zu knacken; Dateinamen sind trotzdem lesbar."))
        times = sorted(i.date_time for i in infos)
        if times and not all(t[:3] == (1980, 1, 1) for t in times):  # 1980-01-01 = absichtlich geleert
            an.findings.append(finding(MID, "Zeitstempel", f"Dateien von {zip_time_str(times[0])} bis {zip_time_str(times[-1])} (Ortszeit des Erstellers)."))
        nested = [i for i in infos if i.filename.lower().endswith((".pdf", ".docx", ".xlsx", ".pptx", ".odt", ".jpg", ".jpeg",
                                                                  ".png", ".heic", ".mp4", ".mov", ".zip")) and i.file_size < 80_000_000]
        for i in nested[:40]:
            if not i.flag_bits & 1 and "__MACOSX/" not in i.filename and not os.path.basename(i.filename).startswith("._"):
                an.embedded.append({"name": os.path.basename(i.filename), "data": z.read(i), "note": i.filename})
        imgs = [(e["name"], e["data"]) for e in an.embedded if e["name"].lower().endswith((".jpg", ".jpeg", ".png", ".heic"))]
        for name, data in imgs:
            an.findings += image_findings(name, data)[:2]
        an.clean = lambda dst: clean_zip(path, dst)
        an.clean_ext = ".zip"
        an.clean_note = ("Packt neu ohne Systemmüll (.DS_Store, __MACOSX, Thumbs.db, desktop.ini), ohne Kommentare, "
                         "Benutzer-IDs und Zeitstempel. Bilder darin verlieren ihre EXIF-Daten. Inhalte wie .git "
                         "oder Schlüsseldateien bitte selbst entfernen.")
    else:
        out = run(["7z", "l", "-slt", "-ba", path])
        cur = {}
        for line in out.splitlines() + [""]:
            if not line.strip():
                if cur.get("Path"):
                    entries.append((cur["Path"], int(cur.get("Size") or 0), cur.get("Modified", "")[:16]))
                cur = {}
            elif " = " in line:
                k, v = line.split(" = ", 1)
                cur[k] = v
        an.label = f"Archiv ({os.path.splitext(path)[1].lstrip('.').upper()})"
        an.clean_note = "Bereinigen geht nur bei ZIP-Archiven. Tipp: entpacken und als ZIP neu packen."
    if not is_zip:  # 7z/RAR/TAR: kleine Dateien kurz entpacken, um hineinzuschauen
        inner = extract_small(path, [e for e in entries if e[1] < 20_000_000])
        for name, data in inner.items():
            if name.lower().endswith((".pdf", ".docx", ".xlsx", ".pptx", ".odt", ".jpg", ".jpeg", ".png", ".mp4", ".zip", ".eml")):
                an.embedded.append({"name": os.path.basename(name), "data": data, "note": name})
            if name.lower().endswith((".jpg", ".jpeg", ".png")):
                an.findings += image_findings(os.path.basename(name), data)[:2]
    else:
        inner = {i.filename: z.read(i) for i in infos if not i.flag_bits & 1 and i.file_size < 2_000_000
                 and is_textish(i.filename)}
    hits = 0
    for name, data in inner.items():
        if is_textish(name) and len(data) < 2_000_000 and hits < 15:
            found = scan_text(data.decode("utf-8", "replace"), f"in {os.path.basename(name)}")
            hits += bool(found)
            an.findings += found
    names = [e[0] for e in entries]
    for pat, level, what in JUNK:
        hit = [n for n in names if re.search(pat, n, re.I)]
        if hit:
            an.findings.append(finding(level, f"{what.split(':')[0]} ({len(hit)})", f"{what}. Z. B. {hit[0]}"))
    users = sorted({m.group(0) for n in names for m in [re.search(r"(?:Users|home|Benutzer)[\\/][^\\/]+", n)] if m})
    if users:
        an.findings.append(finding(HIGH, "Benutzername in Pfaden", ", ".join(users[:4])))
    total = sum(e[1] for e in entries)
    an.cards = [("Inhalt", [("Einträge", str(len(entries))), ("Ausgepackt", human_size(total)),
                            ("Ordner", str(len({os.path.dirname(n) for n in names if os.path.dirname(n)})))]),
                ("Datei", file_rows(path, an.label))]
    listing = "\n".join(f"{n}    {human_size(s)}    {t}" for n, s, t in entries[:2000])
    an.text_preview = listing or "(leer)"
    an.views.append(("Inhalt", {"text": listing}, "Alle Dateien im Archiv mit Größe und Zeitstempel."))
    an.text = "\n".join(names)
    an.findings += scan_text(an.text, "in Dateinamen")
    return an


def is_textish(name):
    base = os.path.basename(name).lower()
    return base.endswith(TEXT_EXT) or (base.startswith(".") and "." not in base[1:]) or base in ("config", "credentials", "id_rsa")


def extract_small(path, entries, limit=60_000_000):
    """Kleine Dateien aus 7z/RAR/TAR in den Speicher holen (über 7z, ohne Ordnerstruktur auf der Platte)."""
    out, total = {}, 0
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["7z", "x", "-y", "-bso0", "-bsp0", "-p", f"-o{tmp}", path], capture_output=True, timeout=120)
        for root, _, files in os.walk(tmp):
            for f in files:
                full = os.path.join(root, f)
                size = os.path.getsize(full)
                if total + size > limit:
                    return out
                total += size
                out[os.path.relpath(full, tmp)] = open(full, "rb").read()
    return out


def clean_zip(src, dst):
    z = zipfile.ZipFile(src)
    junk = re.compile(r"(^|/)(\.DS_Store|Thumbs\.db|desktop\.ini)$|(^|/)__MACOSX/", re.I)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as out:
        for i in z.infolist():
            if junk.search(i.filename) or i.flag_bits & 1:
                continue
            data = z.read(i)
            if i.filename.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                try:
                    img = Image.open(io.BytesIO(data))
                    img.load()
                    data = clean_image_bytes(img, data)[0]
                except Exception:
                    pass
            zi = zipfile.ZipInfo(i.filename, date_time=(1980, 1, 1, 0, 0, 0))
            zi.create_system = 0
            zi.external_attr = 0o40755 << 16 | 0x10 if i.is_dir() else 0o644 << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            out.writestr(zi, b"" if i.is_dir() else data)
    return True, "bereinigt"


# ======================================================================
# Text, Code, SVG, HTML
# ======================================================================
TEXT_EXT = (".txt", ".md", ".csv", ".tsv", ".json", ".yaml", ".yml", ".xml", ".html", ".htm", ".svg", ".log", ".ini",
            ".conf", ".cfg", ".toml", ".py", ".js", ".ts", ".sh", ".c", ".h", ".cpp", ".java", ".rs", ".go", ".lua",
            ".tex", ".rtf", ".vcf", ".ics", ".srt", ".env", ".sql", ".css", ".gpx", ".kml", ".eml")


def analyze_text(path):
    raw = open(path, "rb").read()[:MAX_TEXT]
    text = raw.decode("utf-8", "replace")
    ext = os.path.splitext(path)[1].lower()
    labels = {".svg": "SVG-Grafik", ".html": "Webseite (HTML)", ".htm": "Webseite (HTML)", ".csv": "Tabelle (CSV)",
              ".json": "JSON-Daten", ".md": "Markdown-Text", ".gpx": "GPS-Track (GPX)", ".kml": "Karte (KML)",
              ".vcf": "Kontakt (vCard)", ".ics": "Kalender (iCal)", ".eml": "E-Mail", ".rtf": "RTF-Dokument"}
    an = Analysis(path, "text", labels.get(ext, "Textdatei"))
    lines = text.count("\n") + 1
    an.cards = [("Inhalt", [("Zeilen", str(lines)), ("Wörter", str(len(text.split()))), ("Zeichen", str(len(text))),
                            ("Kodierung", "UTF-8" if b"\xef\xbb\xbf" not in raw[:3] else "UTF-8 mit BOM")]),
                ("Datei", file_rows(path, an.label))]
    an.text = text
    an.text_preview = text[:30000]
    an.views.append(("Text", {"text": an.text_preview}, "Inhalt der Datei."))
    if ext in (".gpx", ".kml"):
        pts = re.findall(r'lat="(-?[\d.]+)"\s+lon="(-?[\d.]+)"|<coordinates>\s*(-?[\d.]+),(-?[\d.]+)', text)
        if pts:
            p = pts[0]
            g = f"{float(p[0] or p[3]):.5f}, {float(p[1] or p[2]):.5f}"
            an.findings.append(finding(HIGH, f"{len(pts)} GPS-Punkte", f"Bewegungsprofil; Startpunkt {g} (oft die eigene Wohnung).", gps=g))
    if ext == ".eml":
        hops = re.findall(r"^Received: from .*?\[(\d+\.\d+\.\d+\.\d+)\]", text, re.M)
        if hops:
            an.findings.append(finding(HIGH, "Absender-IP in den Kopfzeilen", f"{hops[-1]}: kann Ort/Anbieter verraten."))
        for h in ("From", "Reply-To", "X-Mailer", "User-Agent", "Message-ID"):
            m = re.search(rf"^{h}:\s*(.+)$", text, re.M)
            if m:
                an.findings.append(finding(MID if h in ("X-Mailer", "User-Agent", "Message-ID") else INFO, h, m.group(1)[:120]))
    if ext in (".svg", ".html", ".htm", ".xml"):
        for pat, level, title in ((r'(?:inkscape:export-filename|sodipodi:docname|xlink:href)="([^"]*(?:/home/|/Users/|[A-Z]:\\\\)[^"]*)"', HIGH, "Lokaler Dateipfad"),
                                  (r"<!--(.*?)-->", INFO, "Kommentare im Quelltext"),
                                  (r'<meta\s+name="generator"\s+content="([^"]+)"', INFO, "Erstellt mit"),
                                  (r"<script\b", MID, "Skripte"),
                                  (r'<(?:dc:creator|cc:Agent)>.*?<dc:title>([^<]+)', MID, "Urheber in den Metadaten")):
            hits = re.findall(pat, text, re.S | re.I)
            if hits:
                ex = hits[0] if isinstance(hits[0], str) else hits[0][0]
                an.findings.append(finding(level, f"{title} ({len(hits)})", ex.strip()[:140] or "vorhanden"))
        an.clean = lambda dst: clean_markup(path, dst)
        an.clean_ext = ext
        an.clean_note = "Entfernt Kommentare, Editor-Daten (Inkscape/Sodipodi), Metadaten-Blöcke und Generator-Angaben."
    if ext in (".csv", ".tsv", ".json", ".yaml", ".yml", ".env", ".ini", ".conf", ".cfg", ".toml", ".sql", ".log"):
        an.findings.append(finding(INFO, "Daten- oder Konfigurationsdatei", "Hier stehen oft Zugangsdaten oder Listen mit Personen."))
    an.findings += scan_text(text, "im Inhalt")
    for uri in dict.fromkeys(re.findall(r"https?://[^\s\"'<>)]{4,200}", text)):
        if not re.match(r"https?://(www\.)?(w3\.org|inkscape\.org|sodipodi\.sourceforge\.net|purl\.org|creativecommons\.org|ns\.adobe\.com)/", uri):
            an.links.append((uri, uri))  # Namensraum-Adressen sind keine echten Links
    return an


def clean_markup(src, dst):
    t = open(src, encoding="utf-8", errors="replace").read()
    t = re.sub(r"<!--.*?-->", "", t, flags=re.S)
    t = re.sub(r"<metadata\b.*?</metadata>", "", t, flags=re.S | re.I)
    t = re.sub(r"<sodipodi:namedview\b.*?(/>|</sodipodi:namedview>)", "", t, flags=re.S)
    t = re.sub(r'\s(?:inkscape|sodipodi):[\w-]+="[^"]*"', "", t)
    t = re.sub(r'<meta\s+name="generator"[^>]*>', "", t, flags=re.I)
    open(dst, "w", encoding="utf-8").write(t)
    return True, "bereinigt"


# ======================================================================
# Alles andere (auch altes .doc/.xls, Programme, unbekannte Formate)
# ======================================================================
def analyze_generic(path):
    raw = open(path, "rb").read(30_000_000)
    ctype, _ = Gio.content_type_guess(path, raw[:4096])
    desc = Gio.content_type_get_description(ctype) if ctype else "Datei"
    label = desc[:1].upper() + desc[1:] if desc and desc.lower() not in ("unknown", "unbekannt") else "Unbekannter Dateityp"
    if raw.startswith(b"\xd0\xcf\x11\xe0"):
        label = "Altes Office-Format (.doc/.xls/.ppt)"
    an = Analysis(path, "other", label)
    an.cards = [("Datei", file_rows(path, label)), ("Kennung", [("Dateisignatur", raw[:8].hex(" ")),
                                                              ("Typ laut System", ctype or "?")])]
    an.text = printable_strings(raw)
    an.text_preview = an.text[:20000] or "(keine lesbaren Texte)"
    an.views.append(("Lesbare Texte", {"text": an.text_preview}, "Alle lesbaren Textstücke in der Datei, wie beim Werkzeug „strings“."))
    if raw.startswith(b"\xd0\xcf\x11\xe0"):
        an.findings.append(finding(MID, "Altes Office-Format", "Autor, Firma und Druckerdaten stehen oft drin. Zum Bereinigen "
                                   "erst als .docx/.xlsx/.pptx speichern, dann in Scourge öffnen."))
        names = re.findall(r"\x00([A-ZÄÖÜ][a-zäöüß]+ [A-ZÄÖÜ][a-zäöüß]+)\x00", raw.decode("latin-1"))
        if names:
            an.findings.append(finding(MID, "Mögliche Namen", ", ".join(dict.fromkeys(names[:6]))))
    an.findings += scan_text(an.text, "in den Rohdaten")
    an.clean_note = "Für diesen Dateityp gibt es keine automatische Bereinigung."
    return an


# ======================================================================
# Einstieg
# ======================================================================
AUDIO_EXT = (".mp3", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus", ".wav", ".wma", ".aiff", ".aif", ".amr", ".3ga")
ARCHIVE_EXT = (".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".jar", ".apk")


def analyze(path):
    """Passende Analyse für die Datei. Bilder und Videos übernimmt scourge.py selbst."""
    low = path.lower()
    with open(path, "rb") as fh:
        head = fh.read(8)
    if head.startswith(b"%PDF") or low.endswith(".pdf"):
        return analyze_pdf(path)
    if zipfile.is_zipfile(path):
        names = zipfile.ZipFile(path).namelist()
        if "[Content_Types].xml" in names and any(n.startswith(("word/", "xl/", "ppt/")) for n in names):
            return analyze_ooxml(path)
        if "mimetype" in names and b"opendocument" in zipfile.ZipFile(path).read("mimetype"):
            return analyze_odf(path)
        if low.endswith(".epub") or ("mimetype" in names and b"epub" in zipfile.ZipFile(path).read("mimetype")):
            return analyze_epub(path)
        return analyze_archive(path)
    if low.endswith(AUDIO_EXT):
        return analyze_audio(path)
    if low.endswith(ARCHIVE_EXT):
        return analyze_archive(path)
    if low.endswith(TEXT_EXT):
        return analyze_text(path)
    ctype, _ = Gio.content_type_guess(path, head)
    if ctype and ctype.startswith("audio/"):
        return analyze_audio(path)
    if ctype and (ctype.startswith("text/") or Gio.content_type_is_a(ctype, "text/plain")):
        return analyze_text(path)
    return analyze_generic(path)
