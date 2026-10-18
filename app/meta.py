# Metadaten-Analyse für Scourge: was ein Bild oder Video über einen verrät (EXIF, GPS, Seriennummern,
# versteckte Anhänge) und bereinigte Kopien davon. Alles lokal.
import datetime, hashlib, io, os, re, subprocess, tempfile
import chrome as ch

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk
from PIL import ExifTags, Image, ImageChops, ImageOps

HIGH, MID, INFO, OK = "hoch", "mittel", "info", "ok"

META_CSS = """
.sev { font-size: 15px; min-width: 18px; }
.sev-hoch { color: #ff6b6b; }
.sev-mittel { color: #ffc857; }
.sev-info { color: alpha(@foreground, 0.5); }
.sev-ok { color: #6bd68a; }
.finding-title { font-size: 12px; font-weight: 800; }
.finding-detail { font-size: 11px; color: alpha(@foreground, 0.65); }
.summary { border-radius: 11px; padding: 2px 10px; font-size: 12px; font-weight: 800; }
.summary.bad { background-color: alpha(#ff6b6b, 0.18); color: #ff8f8f; }
.summary.warn { background-color: alpha(#ffc857, 0.18); color: #ffd27a; }
.summary.good { background-color: alpha(#6bd68a, 0.18); color: #8be3a4; }
.hash { font-size: 11px; }
button.link { padding: 0; min-height: 0; background: none; box-shadow: none; border: none; }
button.link > label { color: @color9; font-size: 12px; font-weight: 800; text-decoration: underline; }
button.link:hover > label { color: @foreground; }
"""

SERIAL_TAGS = {0xA431: "Kamera-Seriennummer", 0xA435: "Objektiv-Seriennummer", 0xA420: "Eindeutige Bild-ID"}
NAME_TAGS = {0xA430: "Kamerabesitzer", 0x013B: "Urheber", 0x8298: "Copyright", 0x9C9D: "Autor (Windows)"}
FILENAME_HINTS = [(r"^PXL_(\d{8})_(\d{6})", "Google Pixel"), (r"^IMG_(\d{8})_(\d{6})", "Android-Handy"),
                  (r"^IMG_\d{4}", "iPhone oder Kamera"), (r"^DSC[_F]?\d+", "Digitalkamera"),
                  (r"^Screenshot", "Bildschirmfoto"), (r"^signal-\d{4}", "Signal-Messenger"),
                  (r"^WhatsApp", "WhatsApp"), (r"^PXL_|^MVIMG_", "Google-Handy")]


def finding(level, title, detail, **extra):
    return {"level": level, "title": title, "detail": detail, **extra}


# ---------------- Datei-Ende & versteckte Anhänge ----------------
def jpeg_end(data):
    """Ende des eigentlichen JPEG-Bilds (alles danach ist angehängt)."""
    i, n = 2, len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xD9:
            return i + 2
        if marker == 0xDA:  # Bilddaten: bis zum nächsten echten Marker springen
            seg = int.from_bytes(data[i + 2:i + 4], "big")
            j = i + 2 + seg
            while j + 1 < n:
                if data[j] == 0xFF and data[j + 1] not in (0x00, *range(0xD0, 0xD8)):
                    break
                j += 1
            i = j
            continue
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            i += 2
            continue
        i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    return None


def png_end(data):
    k = data.rfind(b"IEND")
    return k + 8 if k > 0 else None


def trailer(data, fmt):
    end = jpeg_end(data) if fmt == "JPEG" else png_end(data) if fmt == "PNG" else None
    if end and len(data) - end > 16:
        return end, data[end:]
    return None, b""


def embedded_thumbnail(img):
    """EXIF-Vorschaubild (IFD1). Kann bei zugeschnittenen Fotos noch das ganze Original zeigen."""
    raw = img.info.get("exif")
    if not raw:
        return None
    try:
        ifd1 = img.getexif().get_ifd(ExifTags.IFD.IFD1)
        off, ln = ifd1.get(0x0201), ifd1.get(0x0202)
        if off and ln:
            base = 6 if raw.startswith(b"Exif\x00\x00") else 0
            t = Image.open(io.BytesIO(raw[base + off:base + off + ln]))
            t.load()
            return t
    except Exception:
        return None
    return None


# ---------------- Analyse ----------------
def analyze_image(img, data, path=None, gps_text=None):
    out = []
    exif = img.getexif()
    tags = dict(exif)
    try:
        tags.update(exif.get_ifd(ExifTags.IFD.Exif))
    except Exception:
        pass

    if gps_text:
        out.append(finding(HIGH, "Genauer Aufnahmeort (GPS)", f"{gps_text}. Damit lässt sich z. B. deine Wohnung finden.",
                           gps=gps_text))
    for tag, label in NAME_TAGS.items():
        v = tags.get(tag)
        if isinstance(v, bytes):
            v = v.decode("utf-16-le" if tag == 0x9C9D else "utf-8", "ignore")
        if v and str(v).strip("\x00 "):
            out.append(finding(HIGH, f"Name im Bild: {label}", f"„{str(v).strip(chr(0)).strip()[:80]}“ steht in den Metadaten."))
    for tag, label in SERIAL_TAGS.items():
        v = tags.get(tag)
        if v and str(v).strip("\x00 0"):
            out.append(finding(HIGH, label, f"{str(v).strip()[:40]}: verknüpft alle Fotos, die mit diesem Gerät gemacht wurden."))

    make, model = str(tags.get(0x010F, "")).strip("\x00 "), str(tags.get(0x0110, "")).strip("\x00 ")
    if make or model:
        dev = model if model.lower().startswith(make.lower()) else f"{make} {model}".strip()
        lens = str(tags.get(0xA434, "")).strip("\x00 ")
        out.append(finding(MID, f"Gerät erkennbar: {dev}", "Verrät dein Handy- bzw. Kameramodell" +
                           (f", Objektiv: {lens}." if lens else ".")))
    when = tags.get(0x9003) or tags.get(0x0132)
    if when:
        off = tags.get(0x9011) or tags.get(0x9010)
        out.append(finding(MID, "Aufnahmezeitpunkt", f"{when}" + (f" (UTC{off}): verrät auch deine Zeitzone." if off else
                                                                  ": verrät, wann du wo warst.")))
    sw = str(tags.get(0x0131, "")).strip("\x00 ")
    if sw:
        out.append(finding(INFO, "Software", f"{sw}: zeigt, womit das Bild erstellt oder bearbeitet wurde."))
    if 0x927C in tags:
        out.append(finding(MID, "Herstellerdaten (MakerNote)", "Unlesbarer Zusatzblock des Herstellers, enthält oft "
                           "Seriennummern, Kameraeinstellungen und Gesichtserkennungs-Daten."))

    xmp = img.info.get("xmp") or img.info.get("XML:com.adobe.xmp")
    if xmp:
        x = xmp if isinstance(xmp, bytes) else str(xmp).encode()
        hits = []
        for pat, label in ((rb"dc:creator", "Urheber"), (rb"xmpMM:History", "Bearbeitungsverlauf"),
                           (rb"DerivedFrom|OriginalDocumentID", "Verweis aufs Originaldokument"),
                           (rb"Iptc4xmpCore:Location|photoshop:City", "Ortsangabe"),
                           (rb"MotionPhoto|MicroVideo", "Motion-Photo-Kennung"), (rb"CreatorTool", "Bearbeitungsprogramm")):
            if re.search(pat, x):
                hits.append(label)
        out.append(finding(MID if hits else INFO, "XMP-Zusatzdaten", ("Enthält: " + ", ".join(hits)) if hits else
                           "Weitere Metadaten im XMP-Format."))
    for key, v in img.info.items():
        if isinstance(v, str) and key not in ("xmp",) and len(v) > 20:
            out.append(finding(MID, f"Textfeld „{key}“", f"{v[:120]}{'…' if len(v) > 120 else ''} (z. B. KI-Prompt, Kommentar)."))

    thumb = embedded_thumbnail(img)
    if thumb:
        r_main = img.size[0] / img.size[1]
        o = exif.get(0x0112, 1)
        if o in (5, 6, 7, 8):
            r_main = 1 / r_main
        r_t = thumb.size[0] / thumb.size[1]
        if abs(r_main - r_t) > 0.05 and abs(1 / r_main - r_t) > 0.05:
            out.append(finding(HIGH, "Vorschaubild zeigt anderen Ausschnitt",
                               "Das eingebettete Vorschaubild hat ein anderes Format: weggeschnittene Bildteile sind dort "
                               "evtl. noch zu sehen. Schau es dir links an.", thumb=thumb))
        else:
            out.append(finding(INFO, "Eingebettetes Vorschaubild", f"{thumb.size[0]}×{thumb.size[1]} px. Wird beim Bereinigen entfernt.",
                               thumb=thumb))

    end, extra = trailer(data, img.format)
    if extra:
        k = extra.find(b"ftyp")
        if k >= 4:
            out.append(finding(HIGH, "Verstecktes Video im Foto (Motion Photo)",
                               f"{len(extra) // 1024} KB Video, aufgenommen kurz vor und nach dem Foto (je nach "
                               "Handy-Einstellung mit Ton). Wird beim Teilen oft mitgeschickt.", video=extra[k - 4:]))
        else:
            out.append(finding(HIGH, "Unbekannte Daten am Dateiende",
                               f"{len(extra) // 1024} KB hinter dem eigentlichen Bild angehängt (z. B. versteckte Datei)."))

    if path:
        name = os.path.basename(path)
        for pat, label in FILENAME_HINTS:
            m = re.match(pat, name)
            if m:
                det = f"„{name}“ verrät: {label}"
                if m.groups() and len(m.group(1)) == 8:
                    det += f", Datum {m.group(1)[6:8]}.{m.group(1)[4:6]}.{m.group(1)[:4]}"
                out.append(finding(INFO, "Dateiname", det + ". Vor dem Teilen umbenennen."))
                break
    if img.info.get("icc_profile"):
        out.append(finding(INFO, "Farbprofil", "Unkritisch, verrät höchstens die Geräteklasse."))
    return out


def analyze_video(info, path):
    t = info["tags"]
    out = []
    gps = None
    loc = t.get("com.apple.quicktime.location.iso6709") or t.get("location") or t.get("location-eng")
    m = re.match(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)", loc or "")
    if m:
        gps = f"{float(m.group(1)):.5f}, {float(m.group(2)):.5f}"
        out.append(finding(HIGH, "Genauer Aufnahmeort (GPS)", f"{gps}. Damit lässt sich z. B. deine Wohnung finden.", gps=gps))
    make = t.get("com.apple.quicktime.make") or t.get("com.android.manufacturer")
    model = t.get("com.apple.quicktime.model") or t.get("com.android.model")
    if make or model:
        out.append(finding(MID, f"Gerät erkennbar: {' '.join(x for x in (make, model) if x)}", "Verrät dein Handymodell."))
    when = t.get("com.apple.quicktime.creationdate") or t.get("creation_time")
    if when:
        out.append(finding(MID, "Aufnahmezeitpunkt", f"{when}: verrät, wann du wo warst."))
    for key in ("com.android.version", "com.apple.quicktime.software", "encoder"):
        if t.get(key):
            out.append(finding(INFO, "Software", f"{t[key]}"))
            break
    for key in ("title", "artist", "comment", "author", "copyright"):
        if t.get(key):
            out.append(finding(MID, f"Textfeld „{key}“", t[key][:120]))
    if info.get("audio"):
        out.append(finding(MID, "Tonspur", "Im Hintergrund können Stimmen, Namen oder Ortsgeräusche zu hören sein."))
    name = os.path.basename(path)
    for pat, label in FILENAME_HINTS + [(r"^VID_(\d{8})_(\d{6})", "Android-Handy")]:
        if re.match(pat, name):
            out.append(finding(INFO, "Dateiname", f"„{name}“ verrät: {label}. Vor dem Teilen umbenennen."))
            break
    return out


# ---------------- Werkzeuge ----------------
def error_level(img, quality=90):
    """ELA: nachträglich eingefügte oder bearbeitete Bereiche leuchten anders als der Rest."""
    im = ImageOps.exif_transpose(img).convert("RGB")
    im.thumbnail((2000, 2000))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    diff = ImageChops.difference(im, Image.open(io.BytesIO(buf.getvalue())))
    mx = max(e[1] for e in diff.getextrema()) or 1
    return diff.point(lambda v: min(255, int(v * 255 / mx)))


def dhash(img):
    g = ImageOps.exif_transpose(img).convert("L").resize((9, 8), Image.LANCZOS)
    px = list(g.getdata())
    bits = "".join("1" if px[r * 9 + c] > px[r * 9 + c + 1] else "0" for r in range(8) for c in range(8))
    return f"{int(bits, 2):016x}"


def clean_image_bytes(img, data):
    """Kopie ohne Metadaten, Vorschaubild und Anhänge. JPEG behält seine Qualitätsstufe."""
    im = ImageOps.exif_transpose(img)
    fmt = img.format if img.format in ("JPEG", "PNG", "WEBP") else "PNG"
    mode = "RGB" if fmt == "JPEG" else ("RGBA" if im.mode in ("P", "LA", "PA") else im.mode)
    src = im.convert(mode)
    clean = Image.new(mode, src.size)  # frisches Bild: keine EXIF-, XMP-, ICC- oder Textfelder
    clean.paste(src)
    buf = io.BytesIO()
    if fmt == "JPEG":
        clean.save(buf, "JPEG", quality=95, optimize=True)
    elif fmt == "WEBP":
        clean.save(buf, "WEBP", quality=95)
    else:
        clean.save(buf, "PNG")
    return buf.getvalue(), {"JPEG": ".jpg", "WEBP": ".webp"}.get(fmt, ".png")


def clean_video(src, dst):
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-map", "0", "-map_metadata", "-1", "-map_chapters", "-1",
                        "-c", "copy", "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact", dst],
                       capture_output=True, text=True)
    return r.returncode == 0, r.stderr
