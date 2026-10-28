# Kreativ-Studio für Scourge (Startseite → "Verlauf & ASCII"):
#  - Verlauf: ästhetische Farbverläufe (linear, radial, konisch, Raute, Mesh) als PNG/JPG
#  - ASCII: Bild hochladen und als ASCII-Kunst ausgeben (Text, ANSI, PNG)
# Gerechnet wird mit numpy im Hintergrund; Farben werden in OKLab gemischt, damit keine
# matschigen Grautöne in der Mitte entstehen.
import copy, datetime, io, json, os, re, subprocess, threading, time
import chrome as ch

import numpy as np
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk
from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

MATUGEN_CSS = os.path.expanduser("~/.cache/matugen/colors.css")

def _theme_text():
    """Farben des gewählten Designs (istheme: Auswahl + Hell/Dunkel), sonst die von matugen."""
    try:
        import istheme
        return istheme.resolve()[0]
    except ImportError:
        return open(MATUGEN_CSS).read()


STATE_FILE = os.path.expanduser("~/.config/scourge/studio-verlauf.json")      # zuletzt bearbeiteter Verlauf (Wiederherstellen)
PRESET_FILE = os.path.expanduser("~/.config/scourge/verlauf-vorlagen.json")  # eigene gespeicherte Verläufe
WALL_OUT = os.path.expanduser("~/Pictures/Verlaeufe")                        # hier landen Verläufe, die als Hintergrund gesetzt werden


def _read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except OSError:
        pass


STUDIO_CSS = """
.studio-top { margin: 6px 16px 0 16px; }
.studio-side { margin: 0 0 16px 16px; }
.studio-side > viewport > box { padding-right: 10px; }
.studio-preview {
    background-color: alpha(@surface_container, 0.6); border: 2px solid alpha(@color9, 0.35);
    border-radius: 15px; margin: 0 16px 16px 6px;
}
.studio-preview.drag-over { border-color: @color9; background-color: alpha(@color9, 0.12); }
.studio-pic { border-radius: 10px; }
.studio-val { font-size: 11px; color: @color9; font-weight: 800; }
button.pb.seg { padding: 0 10px; min-height: 24px; font-size: 12px; border-radius: 11px; }
button.stop { min-width: 30px; min-height: 30px; padding: 0; border-radius: 10px;
    border: 2px solid alpha(@foreground, 0.25); box-shadow: none; }
button.stop:hover { border-color: @foreground; }
button.palette { background: none; box-shadow: none; border: 2px solid transparent; border-radius: 10px; padding: 3px; }
button.palette:hover { border-color: @color9; }
button.stop-x { min-width: 0; min-height: 0; padding: 0 4px; background: none; border: none; box-shadow: none;
    color: alpha(@foreground, 0.5); font-size: 11px; }
button.stop-x:hover { color: @foreground; }
spinbutton.pb { background-color: @background; color: @foreground; border: 2px solid alpha(@color9, 0.45);
    border-radius: 11px; min-height: 28px; box-shadow: none; }
spinbutton.pb > text { padding: 0 6px; }
spinbutton.pb > button { background: none; border: none; box-shadow: none; color: @foreground; padding: 0 6px; }
spinbutton.pb > button:hover { color: @color9; }
.studio-empty { font-size: 13px; color: alpha(@foreground, 0.6); }
"""

PALETTES = [
    ("Sonnenuntergang", ["#2d1b4e", "#a83279", "#f05d5e", "#ffb26b"]),
    ("Aurora", ["#05081c", "#123c69", "#1fb5a8", "#a6f6c4"]),
    ("Vaporwave", ["#ff71ce", "#b967ff", "#01cdfe", "#05ffa1"]),
    ("Pfirsich", ["#ffd1b3", "#ff9a8b", "#ff6a88", "#ff99ac"]),
    ("Tiefsee", ["#020024", "#090979", "#0079c1", "#00d4ff"]),
    ("Mitternacht", ["#0f0c29", "#302b63", "#6b4c9a", "#24243e"]),
    ("Lava", ["#1a0000", "#7a0010", "#ff3d00", "#ffb300"]),
    ("Pastell", ["#a1c4fd", "#c2e9fb", "#fbc2eb", "#e0c3fc"]),
    ("Wald", ["#0b2b26", "#163832", "#235347", "#8eb69b"]),
    ("Holo", ["#c3f0ff", "#e8c6ff", "#ffd6e8", "#fff4c2", "#c7ffe3"]),
]

CHARSETS = [
    ("standard", "Standard", " .:-=+*#%@"),
    ("fine", "Fein", " .'`^\",:;Il!i><~+_-?][}{1)(|\\/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$"),
    ("blocks", "Blöcke", " ░▒▓█"),
    ("braille", "Braille", None),
    ("dots", "Punkte", " .·:•●"),
    ("custom", "Eigene", None),
]


# ======================================================================
# Farbräume
# ======================================================================
def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def rgb_hex(c):
    return "#{:02x}{:02x}{:02x}".format(*(int(round(max(0, min(1, v)) * 255)) for v in c))


def srgb_to_lin(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def lin_to_srgb(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


_M1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929],
                [0.2119034982, 0.6806995451, 0.1073969566],
                [0.0883024619, 0.2817188376, 0.6299787005]])
_M2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468],
                [1.9779984951, -2.4285922050, 0.4505937099],
                [0.0259040371, 0.7827717662, -0.8086757660]])
_M2I = np.array([[1, 0.3963377774, 0.2158037573],
                 [1, -0.1055613458, -0.0638541728],
                 [1, -0.0894841775, -1.2914855480]])
_M1I = np.array([[4.0767416621, -3.3077115913, 0.2309699292],
                 [-1.2684380046, 2.6097574011, -0.3413193965],
                 [-0.0041960863, -0.7034186147, 1.7076147010]])


def srgb_to_oklab(rgb):
    lms = srgb_to_lin(np.asarray(rgb, dtype=np.float64)) @ _M1.T
    return np.cbrt(lms) @ _M2.T


def oklab_to_srgb(lab):
    lms = (np.asarray(lab) @ _M2I.T) ** 3
    return lin_to_srgb(lms @ _M1I.T)


def oklch_hex(L, C, h):
    """OKLCH -> Hex; Chroma wird verringert, bis die Farbe darstellbar ist."""
    for _ in range(30):
        lab = np.array([L, C * np.cos(h), C * np.sin(h)])
        lin = ((lab @ _M2I.T) ** 3) @ _M1I.T
        if lin.min() >= -1e-4 and lin.max() <= 1 + 1e-4:
            break
        C *= 0.9
    return rgb_hex(lin_to_srgb(lin))


def random_palette(rng):
    n = int(rng.integers(3, 6))
    h0 = rng.uniform(0, 2 * np.pi)
    step = rng.choice([0.35, 0.5, 0.7, -0.45, 1.0]) * rng.uniform(0.7, 1.2)
    dark_start = rng.random() < 0.55
    out = []
    for i in range(n):
        f = i / (n - 1)
        L = (0.22 + 0.62 * f) if dark_start else (0.92 - 0.25 * abs(f - 0.5) * 2)
        out.append(oklch_hex(L, rng.uniform(0.09, 0.2), h0 + step * i))
    return out


def theme_colors():
    try:
        txt = _theme_text()
    except OSError:
        return {}
    return dict(re.findall(r"@define-color\s+(\w+)\s+(#[0-9a-fA-F]{6})", txt))


def theme_palette():
    t = theme_colors()
    keys = ["background", "color4", "primary", "tertiary", "secondary"]
    cols = [t[k] for k in keys if k in t]
    return cols if len(cols) >= 2 else None


# ======================================================================
# Verlauf rendern
# ======================================================================
def render_gradient(p, W, H):
    colors = p["colors"]
    kind = p["kind"]
    rng = np.random.default_rng(p["seed"])
    ar = W / H
    xs = ((np.arange(W) + 0.5) / W - 0.5) * ar
    ys = (np.arange(H) + 0.5) / H - 0.5
    X, Y = np.meshgrid(xs, ys)
    if p["warp"] > 0:
        a = p["warp"] * 0.14
        ph = rng.uniform(0, 2 * np.pi, 4)
        f = rng.uniform(0.8, 1.6, 4)
        X2 = X + a * np.sin(2 * np.pi * f[0] * Y + ph[0])
        Y2 = Y + a * np.sin(2 * np.pi * f[1] * X + ph[1])
        X = X2 + 0.6 * a * np.sin(2 * np.pi * f[2] * Y2 + ph[2])
        Y = Y2 + 0.6 * a * np.sin(2 * np.pi * f[3] * X2 + ph[3])
    labs = srgb_to_oklab([hex_rgb(c) for c in colors])
    cx, cy = (p["cx"] - 0.5) * ar, p["cy"] - 0.5

    if kind == "mesh":
        # Punkte reihum auf einem verwackelten Ring, damit jede Farbe sichtbar bleibt
        n = len(colors)
        ang = rng.uniform(0, 2 * np.pi) + 2 * np.pi * np.arange(n) / n + rng.uniform(-0.35, 0.35, n)
        rad = rng.uniform(0.25, 0.5, n)
        pts = np.stack([np.cos(ang) * rad * ar, np.sin(ang) * rad], axis=1)
        sig = 0.12 + 0.55 * p["soft"]
        lab = np.zeros(X.shape + (3,))
        wsum = np.full(X.shape, 1e-9)
        for (px, py), c in zip(pts, labs):
            w = np.exp(-((X - px) ** 2 + (Y - py) ** 2) / (2 * sig * sig))
            lab += w[..., None] * c
            wsum += w
        rgb = oklab_to_srgb(lab / wsum[..., None])
    else:
        th = np.radians(p["angle"])
        if kind == "linear":
            dx, dy = np.cos(th), np.sin(th)
            e = ar / 2 * abs(dx) + 0.5 * abs(dy)
            t = ((X * dx + Y * dy) + e) / (2 * e)
        elif kind == "radial":
            maxd = max(np.hypot(sx * ar / 2 - cx, sy * 0.5 - cy) for sx in (-1, 1) for sy in (-1, 1))
            t = np.hypot(X - cx, Y - cy) / (maxd * (0.25 + 1.5 * p["soft"]))
        elif kind == "conic":
            t = ((np.arctan2(Y - cy, X - cx) - th) / (2 * np.pi)) % 1.0
            labs = np.vstack([labs, labs[:1]])
        else:  # Raute
            t = (np.abs(X - cx) / (ar / 2) + np.abs(Y - cy) / 0.5) / (1.0 + 2.0 * p["soft"])
        t = np.clip(t, 0, 1)
        n = len(labs)
        k = np.linspace(0, n - 1, 2048)
        i = np.minimum(k.astype(int), n - 2)
        f = k - i
        if p["smooth"]:
            f = f * f * (3 - 2 * f)
        lut = oklab_to_srgb(labs[i] * (1 - f)[:, None] + labs[i + 1] * f[:, None])
        rgb = lut[(t * 2047).astype(np.int32)]

    if p["vignette"] > 0:
        r = np.hypot(X / (ar / 2), Y / 0.5) / np.sqrt(2)
        s = np.clip((r - 0.35) / 0.65, 0, 1)
        rgb = rgb * (1 - p["vignette"] * 0.75 * (s * s * (3 - 2 * s)))[..., None]
    out = rgb * 255.0
    noise = rng.uniform(-0.5, 0.5, (H, W, 1)) + rng.uniform(-0.5, 0.5, (H, W, 1))  # gegen Streifenbildung
    if p["grain"] > 0:
        noise = noise + rng.normal(0, p["grain"] * 40, (H, W, 1))
    out = np.clip(out + noise, 0, 255).astype(np.uint8)
    return Image.fromarray(out, "RGB")


# ======================================================================
# ASCII rendern
# ======================================================================
_FONTS = {}


def font_path():
    try:
        p = subprocess.run(["fc-match", "-f", "%{file}", "JetBrainsMono Nerd Font Mono:style=Regular"],
                           capture_output=True, text=True, timeout=3).stdout.strip()
        if p and os.path.exists(p):
            return p
    except Exception:
        pass
    return "DejaVuSansMono.ttf"


def get_font(size):
    if size not in _FONTS:
        f = ImageFont.truetype(_FONTS.setdefault("path", font_path()), size)
        asc, desc = f.getmetrics()
        _FONTS[size] = (f, max(1, round(f.getlength("M"))), asc + desc)
    return _FONTS[size]


CELL_ASPECT = None


def cell_aspect():
    global CELL_ASPECT
    if CELL_ASPECT is None:
        _, cw, ch = get_font(40)
        CELL_ASPECT = cw / ch
    return CELL_ASPECT


BAYER = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]) / 16 - 0.47


def quantize(L, levels, mode):
    """L in [0,1] -> Stufen 0..levels-1."""
    n = levels - 1
    if mode == "bayer":
        h, w = L.shape
        b = np.tile(BAYER, (h // 4 + 1, w // 4 + 1))[:h, :w]
        return np.clip(np.round(L * n + b), 0, n).astype(int)
    if mode == "fs":
        a = (L * n).astype(np.float64).copy()
        h, w = a.shape
        out = np.zeros((h, w), dtype=int)
        for y in range(h):
            row, nxt = a[y], a[y + 1] if y + 1 < h else None
            for x in range(w):
                old = row[x]
                q = min(n, max(0, int(old + 0.5)))
                out[y, x] = q
                e = old - q
                if x + 1 < w:
                    row[x + 1] += e * 0.4375
                if nxt is not None:
                    if x > 0:
                        nxt[x - 1] += e * 0.1875
                    nxt[x] += e * 0.3125
                    if x + 1 < w:
                        nxt[x + 1] += e * 0.0625
        return out
    return np.clip(np.round(L * n), 0, n).astype(int)


def sobel(g):
    p = np.pad(g, 1, mode="edge")
    gx = (p[:-2, 2:] + 2 * p[1:-1, 2:] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[1:-1, :-2] + p[2:, :-2])
    gy = (p[2:, :-2] + 2 * p[2:, 1:-1] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[:-2, 1:-1] + p[:-2, 2:])
    return gx, gy


def ascii_art(src, p):
    """-> (Zeilen, Farben (rows, cols, 3) uint8)"""
    braille = p["charset"] == "braille"
    cols = int(p["cols"])
    aspect = p["aspect"]
    w, h = src.size
    rows = max(1, round(cols * h / w * aspect))
    sx, sy = (2, 4) if braille else (1, 1)
    img = src
    if p["sat"] != 1.0:
        img = ImageEnhance.Color(img).enhance(p["sat"])
    color = np.asarray(img.resize((cols, rows), Image.Resampling.BOX), dtype=np.uint8)
    g = np.asarray(img.convert("L").resize((cols * sx, rows * sy), Image.Resampling.LANCZOS), dtype=np.float64) / 255
    L = np.clip((g - 0.5) * p["contrast"] + 0.5 + p["bright"], 0, 1) ** p["gamma"]
    gx, gy = sobel(g)
    mag = np.hypot(gx, gy)
    if mag.max() > 0:
        mag = np.clip(mag / np.percentile(mag, 99.5), 0, 1)
    if p["edges"] > 0:
        L = (1 - p["edges"]) * L + p["edges"] * mag
    if p["light_bg"]:
        L = 1 - L
    if p["invert"]:
        L = 1 - L

    if braille:
        q = quantize(L, 2, p["dither"])
        bits = np.zeros((rows, cols), dtype=int)
        for (dx, dy), v in {(0, 0): 1, (0, 1): 2, (0, 2): 4, (1, 0): 8, (1, 1): 16, (1, 2): 32,
                            (0, 3): 64, (1, 3): 128}.items():
            bits += q[dy::4, dx::2][:rows, :cols] * v
        chars = np.vectorize(lambda b: chr(0x2800 + b))(bits)
    else:
        cs = p["chars"] if len(p["chars"]) >= 2 else " #"
        q = quantize(L, len(cs), p["dither"])
        chars = np.array(list(cs))[q]
        if p["lines"] > 0:
            ang = (np.degrees(np.arctan2(gy, gx)) + 180) % 180
            # Kantenrichtung steht senkrecht zum Gradienten
            dirch = np.array(["|", "/", "-", "\\"])[((ang + 22.5) // 45).astype(int) % 4]
            mask = mag > (1.0 - p["lines"] * 0.9)
            chars = np.where(mask, dirch, chars)
    lines = ["".join(r) for r in chars]
    return lines, color


def palette_for(p):
    """Vorder-/Hintergrundfarbe je nach Einstellung."""
    t = theme_colors()
    bg = {"dark": "#0b0d10", "light": "#f4f1ea", "theme": t.get("background", "#0f1417"),
          "transparent": "#000000"}[p["bg"]]
    return hex_rgb(bg), hex_rgb(p["fg"])


def render_ascii_image(lines, color, p, size):
    font, cw, ch = get_font(size)
    rows, cols = len(lines), len(lines[0]) if lines else 0
    uniq = sorted(set("".join(lines)))
    lut = {c: i for i, c in enumerate(uniq)}
    masks = np.zeros((len(uniq), ch, cw), dtype=np.uint8)
    for c, i in lut.items():
        if c == " ":
            continue
        im = Image.new("L", (cw, ch))
        ImageDraw.Draw(im).text((0, 0), c, font=font, fill=255)
        masks[i] = np.asarray(im)
    idx = np.array([[lut[c] for c in line] for line in lines], dtype=np.int32)
    bg, fg = palette_for(p)
    transparent = p["bg"] == "transparent"
    if p["mode"] == "color":
        fgc = color.astype(np.float32)
        if p["light_bg"]:  # auf hellem Grund dunkler, damit es lesbar bleibt
            fgc = fgc * 0.8
    elif p["mode"] == "theme":
        t = theme_colors()
        fgc = np.broadcast_to(np.array(hex_rgb(t.get("primary", p["fg"]))) * 255, (rows, cols, 3)).astype(np.float32)
    else:
        fgc = np.broadcast_to(np.array(fg) * 255, (rows, cols, 3)).astype(np.float32)
    out = np.zeros((rows * ch, cols * cw, 4 if transparent else 3), dtype=np.uint8)
    bgv = np.array(bg, dtype=np.float32) * 255
    step = 24
    for r0 in range(0, rows, step):
        r1 = min(rows, r0 + step)
        m = masks[idx[r0:r1]].astype(np.float32) / 255  # (r, c, ch, cw)
        m = m.transpose(0, 2, 1, 3).reshape((r1 - r0) * ch, cols * cw)
        f = np.repeat(np.repeat(fgc[r0:r1], ch, axis=0), cw, axis=1)
        if transparent:
            out[r0 * ch:r1 * ch, :, :3] = f.astype(np.uint8)
            out[r0 * ch:r1 * ch, :, 3] = (m * 255).astype(np.uint8)
        else:
            out[r0 * ch:r1 * ch] = (bgv + (f - bgv) * m[..., None]).astype(np.uint8)
    return Image.fromarray(out, "RGBA" if transparent else "RGB")


def ansi_text(lines, color, p):
    if p["mode"] == "mono":
        return "\n".join(lines) + "\n"
    if p["mode"] == "theme":
        r, g, b = (int(v * 255) for v in hex_rgb(theme_colors().get("primary", p["fg"])))
        return f"\x1b[38;2;{r};{g};{b}m" + "\n".join(lines) + "\x1b[0m\n"
    out = []
    for y, line in enumerate(lines):
        last = None
        s = []
        for x, c in enumerate(line):
            col = tuple(int(v) for v in color[y, x])
            if c != " " and col != last:
                s.append("\x1b[38;2;%d;%d;%dm" % col)
                last = col
            s.append(c)
        out.append("".join(s) + "\x1b[0m")
    return "\n".join(out) + "\n"


# ======================================================================
# Oberfläche
# ======================================================================
def pil_texture(img):
    img = img if img.mode in ("RGB", "RGBA") else img.convert("RGB")
    fmt = Gdk.MemoryFormat.R8G8B8A8 if img.mode == "RGBA" else Gdk.MemoryFormat.R8G8B8
    bpp = 4 if img.mode == "RGBA" else 3
    return Gdk.MemoryTexture.new(img.width, img.height, fmt, GLib.Bytes.new(img.tobytes()), img.width * bpp)


def rgba_hex(rgba):
    return rgb_hex((rgba.red, rgba.green, rgba.blue))


def make_rgba(h):
    c = Gdk.RGBA()
    c.parse(h)
    return c


class Panel(Gtk.Box):
    """Gemeinsame Helfer: Seitenleiste mit Karten, Vorschau rechts, Rendern im Hintergrund."""

    def __init__(self, page):
        super().__init__(spacing=0, vexpand=True)
        self.page = page
        self.tab = page.tab
        self.gen = 0
        self._pending = None
        side = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        side.add_css_class("studio-side")
        side.set_size_request(440, -1)
        self.side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        side.set_child(self.side)
        self.append(side)
        self.frame = Gtk.Box(hexpand=True, vexpand=True)
        self.frame.add_css_class("studio-preview")
        self.append(self.frame)
        self.pic = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, hexpand=True, vexpand=True, can_shrink=True)
        self.pic.add_css_class("studio-pic")
        self.pic.set_overflow(Gtk.Overflow.HIDDEN)
        self.pic.set_margin_top(14); self.pic.set_margin_bottom(14)
        self.pic.set_margin_start(14); self.pic.set_margin_end(14)
        self.frame.append(self.pic)

    # --- Bausteine ---
    def card(self, title):
        c = ch.Card(title, spacing=8)
        self.side.append(c)
        return c

    def button(self, label, cb, tip=None, small=True):
        b = Gtk.Button(label=label, tooltip_text=tip, valign=Gtk.Align.CENTER)
        b.add_css_class("pb")
        if small:
            b.add_css_class("seg")
        b.connect("clicked", lambda *_: cb())
        return b

    def seg(self, parent, options, active, cb):
        """Umschalter-Gruppe; options = [(Schlüssel, Beschriftung)]."""
        fb = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=6, min_children_per_line=2,
                         column_spacing=4, row_spacing=4)
        first = None
        btns = {}
        for key, label in options:
            b = Gtk.ToggleButton(label=label)
            b.add_css_class("pb"); b.add_css_class("seg")
            if first:
                b.set_group(first)
            first = first or b
            b.set_active(key == active)  # vor connect, sonst feuert cb schon beim Aufbau
            b.connect("toggled", lambda b, key=key: b.get_active() and cb(key))
            fb.append(b)
            btns[key] = b
        parent.append(fb)
        return btns

    def slider(self, parent, name, lo, hi, val, cb, step=0.01, fmt="{:.2f}", digits=2):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        head = Gtk.Box()
        n = Gtk.Label(label=name, xalign=0, hexpand=True)
        n.add_css_class("key")
        v = Gtk.Label(label=fmt.format(val))
        v.add_css_class("studio-val")
        head.append(n); head.append(v)
        s = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, step)
        s.set_digits(digits)
        s.set_draw_value(False)
        s.add_css_class("pb")
        s.set_value(val)

        def changed(s):
            x = s.get_value()
            v.set_label(fmt.format(x))
            cb(x)
        s.connect("value-changed", changed)
        ch.reset_on_dblclick(n, s, val)
        box.append(head); box.append(s)
        parent.append(box)
        return box, s

    def switch_row(self, parent, name, val, cb):
        row = Gtk.Box(spacing=8)
        l = Gtk.Label(label=name, xalign=0, hexpand=True)
        l.add_css_class("key")
        sw = Gtk.Switch(active=val, valign=Gtk.Align.CENTER)
        sw.connect("notify::active", lambda sw, _: cb(sw.get_active()))
        row.append(l); row.append(sw)
        parent.append(row)
        return sw

    # --- Rendern ---
    def schedule(self, delay=70):
        if self._pending:
            GLib.source_remove(self._pending)
        self._pending = GLib.timeout_add(delay, self._kick)

    def _kick(self):
        self._pending = None
        self.gen += 1
        gen = self.gen
        job = self.preview_job()
        if job is None:
            return False

        def work():
            try:
                res = job()
            except Exception as e:  # Vorschau darf nie die App stören
                res = e
            GLib.idle_add(lambda: (self._done(gen, res), False)[-1])
        threading.Thread(target=work, daemon=True).start()
        return False

    def _done(self, gen, res):
        if gen != self.gen:
            return
        if isinstance(res, Exception):
            self.tab.notify(f"Vorschau fehlgeschlagen: {res}")
            return
        self.show_result(res)

    def save_dialog(self, title, name, done, folder="~/Pictures"):
        try:
            import settings_menu
            folder = settings_menu.folder("pic_dir", folder) if folder == "~/Pictures" else os.path.expanduser(folder)
        except Exception:
            folder = os.path.expanduser(folder)
        os.makedirs(folder, exist_ok=True)
        dlg = Gtk.FileDialog(title=title, initial_name=name, initial_folder=Gio.File.new_for_path(folder))

        def cb(d, res):
            try:
                done(d.save_finish(res).get_path())
            except GLib.Error:
                pass
        dlg.save(self.tab.win, None, cb)

    def in_background(self, work, done, busy="Wird erstellt …"):
        self.tab.notify(busy)

        def t():
            try:
                res = work()
            except Exception as e:
                res = e
            GLib.idle_add(lambda: (done(res), False)[-1])
        threading.Thread(target=t, daemon=True).start()


def stamp():
    return f"{datetime.datetime.now():%Y-%m-%d-%H%M%S}"


# ----------------------------------------------------------------------
# Verlauf
# ----------------------------------------------------------------------
class GradientPanel(Panel):
    KINDS = [("linear", "Linear"), ("radial", "Radial"), ("conic", "Konisch"), ("diamond", "Raute"), ("mesh", "Mesh")]

    def __init__(self, page):
        super().__init__(page)
        self.p = {"colors": list(PALETTES[0][1]), "kind": "mesh", "angle": 135.0, "cx": 0.5, "cy": 0.5,
                  "soft": 0.45, "warp": 0.35, "grain": 0.12, "vignette": 0.0, "smooth": True, "seed": 7}
        mon = self.monitor_size()
        self.W, self.H = mon
        self.hist, self.redo_stack = [], []
        self._hist_key, self._hist_t = None, 0.0
        self._loading = False
        self._save_id = 0
        self.presets = _read_json(PRESET_FILE, [])
        saved = _read_json(STATE_FILE, None)                 # genau da weitermachen, wo man aufgehört hat
        if isinstance(saved, dict) and isinstance(saved.get("p"), dict):
            try:
                self.p.update({k: v for k, v in saved["p"].items() if k in self.p})
                self.p["colors"] = [c for c in self.p["colors"] if re.fullmatch(r"#[0-9a-fA-F]{6}", str(c))][:8]
                if len(self.p["colors"]) < 2:
                    self.p["colors"] = list(PALETTES[0][1])
                self.W = max(16, min(16384, int(saved.get("W", self.W))))
                self.H = max(16, min(16384, int(saved.get("H", self.H))))
            except (TypeError, ValueError):
                self.p["colors"] = list(PALETTES[0][1])
        self.build()
        self.schedule(10)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)

    def monitor_size(self):
        try:
            disp = Gdk.Display.get_default()
            surf = self.tab.win.get_surface()
            mon = disp.get_monitor_at_surface(surf) if surf else disp.get_monitors().get_item(0)
            g = mon.get_geometry()
            s = mon.get_scale() if hasattr(mon, "get_scale") else mon.get_scale_factor()
            return int(round(g.width * s)), int(round(g.height * s))
        except Exception:
            return 2560, 1440

    def build(self):
        self.scales = {}
        # Rückgängig & eigene Verläufe
        c = self.card("Rückgängig & eigene Verläufe")
        row = Gtk.Box(spacing=6)
        self.undo_btn = self.button("󰕌  Rückgängig", self.undo, "Strg+Z")
        self.redo_btn = self.button("󰑎  Wiederholen", self.redo, "Strg+Umschalt+Z")
        row.append(self.undo_btn); row.append(self.redo_btn)
        c.append(row)
        row = Gtk.Box(spacing=6)
        self.preset_name = Gtk.Entry(placeholder_text="Name für diesen Verlauf", hexpand=True)
        self.preset_name.add_css_class("pb")
        self.preset_name.connect("activate", lambda *_: self.save_preset())
        row.append(self.preset_name)
        row.append(self.button("󰆓  speichern", self.save_preset, "Diesen Verlauf als eigene Vorlage merken"))
        c.append(row)
        self.preset_box = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4, min_children_per_line=2,
                                      column_spacing=2, row_spacing=2, homogeneous=True)
        c.append(self.preset_box)
        n = Gtk.Label(label="Der letzte Stand bleibt beim nächsten Start erhalten.", xalign=0, wrap=True)
        n.add_css_class("note")
        c.append(n)
        self.refresh_presets()
        self._update_hist_buttons()

        # Farben
        c = self.card("Farben")
        self.stops = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=8, min_children_per_line=4,
                                 column_spacing=2, row_spacing=4)
        c.append(self.stops)
        row = Gtk.Box(spacing=6)
        row.append(self.button("󰐕  Farbe", self.add_color, "Weitere Farbe hinzufügen (bis 8)"))
        row.append(self.button("󰒝  Zufall", self.randomize, "Zufällige, harmonische Palette"))
        row.append(self.button("󰓡  Umkehren", self.reverse, "Reihenfolge umdrehen"))
        c.append(row)
        self.rebuild_stops()

        c = self.card("Paletten")
        fb = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4, min_children_per_line=2,
                         column_spacing=2, row_spacing=2, homogeneous=True)
        pals = list(PALETTES)
        tp = theme_palette()
        if tp:
            pals.insert(0, ("Mein Theme", tp))
        for name, cols in pals:
            b = Gtk.Button(tooltip_text=name)
            b.add_css_class("palette")
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            strip = Gtk.Picture(content_fit=Gtk.ContentFit.FILL, can_shrink=True)
            strip.set_size_request(70, 18)
            strip.add_css_class("studio-pic")
            strip.set_overflow(Gtk.Overflow.HIDDEN)
            strip.set_paintable(pil_texture(render_gradient(
                {**self.p, "colors": cols, "kind": "linear", "angle": 0, "warp": 0, "grain": 0, "vignette": 0}, 140, 18)))
            lab = Gtk.Label(label=name, ellipsize=3, max_width_chars=10)
            lab.add_css_class("note")
            col.append(strip); col.append(lab)
            b.set_child(col)
            b.connect("clicked", lambda *_, cols=cols: self.set_colors(list(cols)))
            fb.append(b)
        c.append(fb)

        c = self.card("Form")
        self.kind_btns = self.seg(c, self.KINDS, self.p["kind"], self.set_kind)
        self.w_angle, self.scales["angle"] = self.slider(c, "Winkel", 0, 360, self.p["angle"], lambda v: self.set("angle", v), 1, "{:.0f}°", 0)
        self.w_cx, self.scales["cx"] = self.slider(c, "Mitte waagerecht", 0, 1, self.p["cx"], lambda v: self.set("cx", v))
        self.w_cy, self.scales["cy"] = self.slider(c, "Mitte senkrecht", 0, 1, self.p["cy"], lambda v: self.set("cy", v))
        self.w_soft, self.scales["soft"] = self.slider(c, "Weichheit / Größe", 0, 1, self.p["soft"], lambda v: self.set("soft", v))
        self.w_smooth = self.switch_row(c, "Weiche Übergänge", self.p["smooth"], lambda v: self.set("smooth", v))
        self.update_visibility()

        c = self.card("Feinschliff")
        self.scales["warp"] = self.slider(c, "Wellen / Verzerrung", 0, 1, self.p["warp"], lambda v: self.set("warp", v))[1]
        self.scales["grain"] = self.slider(c, "Körnung", 0, 1, self.p["grain"], lambda v: self.set("grain", v))[1]
        self.scales["vignette"] = self.slider(c, "Vignette", 0, 1, self.p["vignette"], lambda v: self.set("vignette", v))[1]
        c.append(self.button("󰒝  Neu würfeln (Mesh & Wellen)", self.reseed))

        c = self.card("Größe & Export")
        sizes = [("screen", f"Bildschirm {self.W}×{self.H}"), ("4k", "4K"), ("1080", "1080p"), ("phone", "Handy"),
                 ("square", "Quadrat")]
        self.seg(c, sizes, "screen", self.set_size_preset)
        row = Gtk.Box(spacing=6)
        self.sw = Gtk.SpinButton.new_with_range(16, 16384, 1)
        self.sh = Gtk.SpinButton.new_with_range(16, 16384, 1)
        for s, v in ((self.sw, self.W), (self.sh, self.H)):
            s.add_css_class("pb")
            s.set_value(v)
            s.connect("value-changed", lambda *_: self.size_changed())
        x = Gtk.Label(label="×")
        x.add_css_class("key")
        row.append(self.sw); row.append(x); row.append(self.sh)
        c.append(row)
        row = Gtk.Box(spacing=6)
        row.append(self.button("󰋩  PNG", lambda: self.export("png"), "Verlustfrei speichern", small=False))
        row.append(self.button("󰋩  JPG", lambda: self.export("jpg"), "Kleinere Datei, Qualität 95", small=False))
        row.append(self.button("󰆏  Kopieren", self.copy, "In voller Größe in die Zwischenablage", small=False))
        c.append(row)
        import wallpaper
        if wallpaper.available():
            c.append(self.button("󰸉  Als Hintergrundbild setzen", self.set_wallpaper,
                                 "Erstellt das Bild in Bildschirmgröße (~/Pictures/Verlaeufe) und setzt es als Hintergrund", small=False))
        n = Gtk.Label(label="Farben werden in OKLab gemischt; eine feine Körnung verhindert Streifen.", xalign=0, wrap=True)
        n.add_css_class("note")
        c.append(n)

    # --- Rückgängig / Wiederholen / Wiederherstellen ---
    def snap(self):
        return {"p": copy.deepcopy(self.p), "W": self.W, "H": self.H}

    def checkpoint(self, key=None):
        """Zustand VOR einer Änderung merken. Gleicher Regler kurz hintereinander (Ziehen) = ein Schritt."""
        if self._loading:
            return
        now = time.monotonic()
        if key is not None and key == self._hist_key and now - self._hist_t < 1.2:
            self._hist_t = now
            self._queue_save()
            return
        self._hist_key, self._hist_t = key, now
        self.hist.append(self.snap())
        del self.hist[:-100]
        self.redo_stack.clear()
        self._update_hist_buttons()
        self._queue_save()

    def _queue_save(self):
        if self._save_id:
            GLib.source_remove(self._save_id)
        self._save_id = GLib.timeout_add(800, self._save_now)

    def _save_now(self):
        self._save_id = 0
        _write_json(STATE_FILE, self.snap())
        return False

    def apply_state(self, st):
        """Zustand übernehmen und alle Bedienelemente nachziehen."""
        self._loading = True
        try:
            self.p.clear()
            self.p.update(copy.deepcopy(st["p"]))
            self.W, self.H = st["W"], st["H"]
            self.rebuild_stops()
            for key, scale in self.scales.items():
                scale.set_value(self.p[key])
            self.w_smooth.set_active(bool(self.p["smooth"]))
            if self.p["kind"] in self.kind_btns:
                self.kind_btns[self.p["kind"]].set_active(True)
            self.sw.set_value(self.W)
            self.sh.set_value(self.H)
            self.update_visibility()
        finally:
            self._loading = False
        self.schedule(10)
        self._queue_save()

    def undo(self):
        if not self.hist:
            return
        self.redo_stack.append(self.snap())
        self._hist_key = None
        self.apply_state(self.hist.pop())
        self._update_hist_buttons()

    def redo(self):
        if not self.redo_stack:
            return
        self.hist.append(self.snap())
        self._hist_key = None
        self.apply_state(self.redo_stack.pop())
        self._update_hist_buttons()

    def _update_hist_buttons(self):
        if hasattr(self, "undo_btn"):
            self.undo_btn.set_sensitive(bool(self.hist))
            self.redo_btn.set_sensitive(bool(self.redo_stack))

    def on_key(self, ctrl, keyval, code, mods):
        if not (mods & Gdk.ModifierType.CONTROL_MASK):
            return False
        focus = self.get_root().get_focus() if self.get_root() else None
        if isinstance(focus, (Gtk.Text, Gtk.TextView)):
            return False
        k = Gdk.keyval_name(keyval) or ""
        if k.lower() == "z":
            (self.redo if mods & Gdk.ModifierType.SHIFT_MASK else self.undo)()
            return True
        if k.lower() == "y":
            self.redo()
            return True
        return False

    # --- eigene Verläufe ---
    def refresh_presets(self):
        while (c := self.preset_box.get_first_child()):
            self.preset_box.remove(c)
        self.preset_box.set_visible(bool(self.presets))
        for i, pr in enumerate(self.presets):
            ov = Gtk.Overlay()
            b = Gtk.Button(tooltip_text=pr["name"])
            b.add_css_class("palette")
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            strip = Gtk.Picture(content_fit=Gtk.ContentFit.FILL, can_shrink=True)
            strip.set_size_request(70, 30)
            strip.add_css_class("studio-pic")
            strip.set_overflow(Gtk.Overflow.HIDDEN)
            try:
                strip.set_paintable(pil_texture(render_gradient(dict(pr["st"]["p"]), 140, 60)))
            except Exception:
                pass
            lab = Gtk.Label(label=pr["name"], ellipsize=3, max_width_chars=10)
            lab.add_css_class("note")
            col.append(strip); col.append(lab)
            b.set_child(col)
            b.connect("clicked", lambda *_, st=pr["st"]: self.use_preset(st))
            ov.set_child(b)
            x = Gtk.Button(label="󰅖", tooltip_text="Gespeicherten Verlauf löschen", halign=Gtk.Align.END, valign=Gtk.Align.START)
            x.add_css_class("stop-x")
            x.connect("clicked", lambda *_, i=i: self.delete_preset(i))
            ov.add_overlay(x)
            self.preset_box.append(ov)

    def use_preset(self, st):
        self.checkpoint()
        self.apply_state(st)

    def delete_preset(self, i):
        if 0 <= i < len(self.presets):
            del self.presets[i]
            _write_json(PRESET_FILE, self.presets)
            self.refresh_presets()

    def save_preset(self):
        name = self.preset_name.get_text().strip() or f"Verlauf {len(self.presets) + 1}"
        self.presets.insert(0, {"name": name[:30], "st": self.snap()})
        del self.presets[60:]
        _write_json(PRESET_FILE, self.presets)
        self.preset_name.set_text("")
        self.refresh_presets()
        self.tab.notify(f"„{name}“ gespeichert")

    # --- Hintergrundbild ---
    def set_wallpaper(self):
        import wallpaper
        if not wallpaper.available():
            return self.tab.notify("Diese Arbeitsumgebung kann Scourge nicht direkt setzen – speichere das Bild und stelle es von Hand ein.")
        job = self.full()
        folder = WALL_OUT

        def work():
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, f"verlauf-{stamp()}.png")
            job().save(path, "PNG", compress_level=6)
            ok, msg = wallpaper.apply(path)
            return ok, msg, path

        def done(r):
            if isinstance(r, Exception):
                return self.tab.notify(f"Fehler: {r}")
            ok, msg, path = r
            self.tab.notify(f"{msg} ({os.path.basename(path)} in ~/Pictures/Verlaeufe)" if ok else msg)
        self.in_background(work, done, "Hintergrundbild wird erstellt …")

    # --- Farben ---
    def rebuild_stops(self):
        while (ch := self.stops.get_first_child()):
            self.stops.remove(ch)
        for i, h in enumerate(self.p["colors"]):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False, title="Farbe wählen"))
            btn.set_rgba(make_rgba(h))
            btn.connect("notify::rgba", lambda b, _, i=i: self.color_changed(i, b))
            box.append(btn)
            if len(self.p["colors"]) > 2:
                x = Gtk.Button(label="󰅖", tooltip_text="Farbe entfernen")
                x.add_css_class("stop-x")
                x.connect("clicked", lambda *_, i=i: self.remove_color(i))
                box.append(x)
            self.stops.append(box)

    def color_changed(self, i, b):
        h = rgba_hex(b.get_rgba())
        if i < len(self.p["colors"]) and self.p["colors"][i] != h:
            self.checkpoint(("color", i))
            self.p["colors"][i] = h
            self.schedule()

    def set_colors(self, cols):
        self.checkpoint()
        self.p["colors"] = cols[:8]
        self.rebuild_stops()
        self.schedule()

    def add_color(self):
        cols = self.p["colors"]
        if len(cols) >= 8:
            self.tab.notify("Höchstens 8 Farben")
            return
        # neue Farbe: Mitte zwischen den letzten beiden, in OKLab
        a, b = srgb_to_oklab([hex_rgb(cols[-2]), hex_rgb(cols[-1])])
        cols.insert(len(cols) - 1, rgb_hex(oklab_to_srgb((a + b) / 2)))
        self.set_colors(cols)

    def remove_color(self, i):
        if len(self.p["colors"]) > 2:
            del self.p["colors"][i]
            self.set_colors(self.p["colors"])

    def randomize(self):
        rng = np.random.default_rng()
        self.checkpoint()
        old = self.p["seed"]
        self._hist_t = 0.0
        self.p["seed"] = int(rng.integers(1 << 30))
        self.set_colors_quiet(random_palette(rng))

    def set_colors_quiet(self, cols):
        self.p["colors"] = cols[:8]
        self.rebuild_stops()
        self.schedule()

    def reverse(self):
        self.set_colors(self.p["colors"][::-1])

    def reseed(self):
        self.checkpoint()
        self.p["seed"] = int(np.random.default_rng().integers(1 << 30))
        self.schedule()

    # --- Form ---
    def set(self, key, val):
        if self.p.get(key) != val:
            self.checkpoint(("set", key))
        self.p[key] = val
        self.schedule()

    def set_kind(self, kind):
        if self.p["kind"] != kind:
            self.checkpoint()
        self.p["kind"] = kind
        self.update_visibility()
        self.schedule()

    def update_visibility(self):
        k = self.p["kind"]
        self.w_angle.set_visible(k in ("linear", "conic"))
        self.w_cx.set_visible(k in ("radial", "conic", "diamond"))
        self.w_cy.set_visible(k in ("radial", "conic", "diamond"))
        self.w_soft.set_visible(k in ("radial", "diamond", "mesh"))
        self.w_smooth.get_parent().set_visible(k != "mesh")

    def set_size_preset(self, key):
        W, H = {"screen": self.monitor_size(), "4k": (3840, 2160), "1080": (1920, 1080), "phone": (1080, 2340),
                "square": (2048, 2048)}[key]
        self.sw.set_value(W)
        self.sh.set_value(H)

    def size_changed(self):
        new = (int(self.sw.get_value()), int(self.sh.get_value()))
        if new != (self.W, self.H):
            self.checkpoint(("size",))
        self.W, self.H = new
        self.schedule()

    # --- Vorschau & Export ---
    def preview_job(self):
        s = min(1.0, 1100 / max(self.W, self.H))
        w, h = max(8, int(self.W * s)), max(8, int(self.H * s))
        p = dict(self.p, colors=list(self.p["colors"]))
        return lambda: render_gradient(p, w, h)

    def show_result(self, img):
        self.pic.set_paintable(pil_texture(img))

    def full(self):
        p = dict(self.p, colors=list(self.p["colors"]))
        W, H = self.W, self.H
        return lambda: render_gradient(p, W, H)

    def export(self, ext):
        job = self.full()

        def go(path):
            if not path.lower().endswith((".png", ".jpg", ".jpeg")):
                path += "." + ext

            def work():
                img = job()
                if path.lower().endswith(".png"):
                    img.save(path, "PNG", optimize=False, compress_level=6)
                else:
                    img.save(path, "JPEG", quality=95, subsampling=0, optimize=True)
                return path
            self.in_background(work, lambda r: self.tab.notify(
                f"Fehler: {r}" if isinstance(r, Exception) else f"Gespeichert: {os.path.basename(r)}"))
        self.save_dialog("Verlauf speichern", f"verlauf-{stamp()}.{ext}", go)

    def copy(self):
        def done(img):
            if isinstance(img, Exception):
                self.tab.notify(f"Fehler: {img}")
                return
            buf = io.BytesIO()
            img.save(buf, "PNG")
            prov = Gdk.ContentProvider.new_for_bytes("image/png", GLib.Bytes.new(buf.getvalue()))
            self.tab.win.get_clipboard().set_content(prov)
            self.tab.notify("Verlauf kopiert")
        self.in_background(self.full(), done)


# ----------------------------------------------------------------------
# ASCII
# ----------------------------------------------------------------------
class AsciiPanel(Panel):
    def __init__(self, page):
        super().__init__(page)
        self.src = None
        self.src_path = None
        self.result = None
        self.p = {"cols": 140, "charset": "standard", "chars": CHARSETS[0][2], "custom": " .oO@",
                  "invert": False, "bright": 0.0, "contrast": 1.2, "gamma": 1.0, "edges": 0.0, "lines": 0.0,
                  "dither": "none", "mode": "color", "fg": "#e8e6e3", "sat": 1.2, "bg": "dark",
                  "aspect": round(cell_aspect(), 2), "size": 12, "light_bg": False}
        self.build()
        self.empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=True, vexpand=True,
                             valign=Gtk.Align.CENTER)
        i = Gtk.Label(label="󰋩")
        i.add_css_class("empty-icon")
        t = Gtk.Label(label="Bild hierher ziehen")
        t.add_css_class("empty-title")
        h = Gtk.Label(label="oder links „Bild wählen“ – daraus wird ASCII-Kunst")
        h.add_css_class("empty-hint")
        for w in (i, t, h):
            self.empty.append(w)
        self.frame.prepend(self.empty)
        self.pic.set_visible(False)
        target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        target.connect("enter", lambda *_: (self.frame.add_css_class("drag-over"), Gdk.DragAction.COPY)[1])
        target.connect("leave", lambda *_: self.frame.remove_css_class("drag-over"))
        target.connect("drop", self.on_drop)
        self.frame.add_controller(target)
        # Wenn im Tab schon ein Bild offen ist, gleich damit anfangen
        if getattr(self.tab, "image", None) is not None and getattr(self.tab, "doc", None) is None:
            self.set_image(self.tab.image.copy(), self.tab.source)

    def build(self):
        c = self.card("Bild")
        row = Gtk.Box(spacing=6)
        row.append(self.button("󰉋  Bild wählen", self.pick, small=False))
        c.append(row)
        self.src_label = Gtk.Label(label="Noch kein Bild", xalign=0, ellipsize=3)
        self.src_label.add_css_class("note")
        c.append(self.src_label)

        c = self.card("Zeichen")
        self.seg(c, [(k, n) for k, n, _ in CHARSETS], self.p["charset"], self.set_charset)
        self.custom = Gtk.Entry(text=self.p["custom"], placeholder_text="hell → dunkel, z. B.  .oO@")
        self.custom.add_css_class("pb")
        self.custom.connect("changed", lambda e: self.set_custom(e.get_text()))
        self.custom.set_visible(False)
        c.append(self.custom)
        self.slider(c, "Breite (Zeichen)", 20, 400, self.p["cols"], lambda v: self.set("cols", int(v)), 1, "{:.0f}", 0)
        self.slider(c, "Seitenverhältnis Zeichen", 0.3, 0.9, self.p["aspect"], lambda v: self.set("aspect", v))
        self.seg(c, [("none", "Kein Dithering"), ("bayer", "Bayer"), ("fs", "Floyd-Steinberg")], "none",
                 lambda k: self.set("dither", k))
        self.switch_row(c, "Invertieren", False, lambda v: self.set("invert", v))

        c = self.card("Bild anpassen")
        self.slider(c, "Helligkeit", -0.5, 0.5, 0.0, lambda v: self.set("bright", v))
        self.slider(c, "Kontrast", 0.3, 3.0, self.p["contrast"], lambda v: self.set("contrast", v))
        self.slider(c, "Gamma", 0.3, 2.5, 1.0, lambda v: self.set("gamma", v))
        self.slider(c, "Kanten betonen", 0, 1, 0.0, lambda v: self.set("edges", v))
        self.slider(c, "Konturlinien  | / - \\", 0, 1, 0.0, lambda v: self.set("lines", v))

        c = self.card("Farbe")
        self.seg(c, [("color", "Originalfarben"), ("mono", "Einfarbig"), ("theme", "Theme")], self.p["mode"],
                 lambda k: self.set("mode", k))
        self.slider(c, "Sättigung", 0, 2.5, self.p["sat"], lambda v: self.set("sat", v))
        row = Gtk.Box(spacing=8)
        l = Gtk.Label(label="Schriftfarbe (einfarbig)", xalign=0, hexpand=True)
        l.add_css_class("key")
        fgb = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False))
        fgb.set_rgba(make_rgba(self.p["fg"]))
        fgb.connect("notify::rgba", lambda b, _: self.set("fg", rgba_hex(b.get_rgba())))
        row.append(l); row.append(fgb)
        c.append(row)
        l = Gtk.Label(label="Hintergrund", xalign=0)
        l.add_css_class("key")
        c.append(l)
        self.seg(c, [("dark", "Dunkel"), ("light", "Hell"), ("theme", "Theme"), ("transparent", "Transparent")],
                 "dark", self.set_bg)

        c = self.card("Export")
        self.slider(c, "Schriftgröße (PNG)", 6, 32, self.p["size"], lambda v: self.set("size", int(v), False), 1,
                    "{:.0f} px", 0)
        fb = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3, min_children_per_line=2,
                         column_spacing=4, row_spacing=4)
        for label, cb, tip in (("󰆏  Text", self.copy_text, "Reinen Text kopieren"),
                               ("󰆍  ANSI", self.copy_ansi, "Mit Terminal-Farben kopieren"),
                               ("󰋩  PNG", self.save_png, "Als Bild speichern"),
                               ("󰈙  TXT", self.save_txt, "Als Textdatei speichern"),
                               ("󰈙  ANSI-Datei", self.save_ansi, "Farbig, z. B. mit cat im Terminal ansehen")):
            fb.append(self.button(label, cb, tip))
        c.append(fb)
        self.info = Gtk.Label(label="", xalign=0, wrap=True)
        self.info.add_css_class("note")
        c.append(self.info)

    # --- Bild ---
    def pick(self):
        f = Gtk.FileFilter(name="Bilder")
        f.add_mime_type("image/*")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(f)
        dlg = Gtk.FileDialog(title="Bild für ASCII", filters=filters)

        def cb(d, res):
            try:
                self.load(d.open_finish(res).get_path())
            except GLib.Error:
                pass
        dlg.open(self.tab.win, None, cb)

    def on_drop(self, target, value, x, y):
        self.frame.remove_css_class("drag-over")
        files = value.get_files() if value else []
        if files:
            self.load(files[0].get_path())
        return True

    def load(self, path):
        try:
            img = Image.open(path)
            img.load()
        except Exception as e:
            self.tab.notify(f"Kein lesbares Bild: {e}")
            return
        self.set_image(img, path)

    def set_image(self, img, path):
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            bg = Image.new("RGBA", img.size, (0, 0, 0, 255))
            img = Image.alpha_composite(bg, img)
        img = img.convert("RGB")
        img.thumbnail((2000, 2000))
        self.src = img
        self.src_path = path
        self.src_label.set_label(os.path.basename(path) if path else "Bild aus dem Tab")
        self.empty.set_visible(False)
        self.pic.set_visible(True)
        self.schedule(10)

    # --- Einstellungen ---
    def set(self, key, val, redraw=True):
        self.p[key] = val
        if redraw:
            self.schedule()

    def set_charset(self, key):
        self.p["charset"] = key
        self.custom.set_visible(key == "custom")
        chars = dict((k, c) for k, _, c in CHARSETS).get(key)
        self.p["chars"] = self.p["custom"] if key == "custom" else (chars or "")
        self.schedule()

    def set_custom(self, txt):
        self.p["custom"] = txt
        if self.p["charset"] == "custom":
            self.p["chars"] = txt
            self.schedule()

    def set_bg(self, key):
        self.p["bg"] = key
        self.p["light_bg"] = key == "light" or (key == "theme" and self.theme_is_light())
        if key == "light" and self.p["fg"].lower() == "#e8e6e3":
            self.p["fg"] = "#1a1a1a"
        elif key != "light" and self.p["fg"].lower() == "#1a1a1a":
            self.p["fg"] = "#e8e6e3"
        self.schedule()

    def theme_is_light(self):
        r, g, b = hex_rgb(theme_colors().get("background", "#000000"))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.5

    # --- Vorschau ---
    def preview_job(self):
        if self.src is None:
            return None
        p = dict(self.p)
        src = self.src

        def job():
            lines, color = ascii_art(src, p)
            size = p["size"]
            while size > 4 and len(lines[0]) * get_font(size)[1] > 2600:  # Vorschau nicht riesig rechnen
                size -= 1
            return lines, color, p, render_ascii_image(lines, color, p, size)
        return job

    def show_result(self, res):
        lines, color, p, img = res
        self.result = (lines, color, p)
        self.pic.set_paintable(pil_texture(img))
        _, cw, ch = get_font(p["size"])
        self.info.set_label(f"{len(lines[0])} × {len(lines)} Zeichen · PNG {len(lines[0]) * cw} × {len(lines) * ch} px")

    # --- Export ---
    def ready(self):
        if not self.result:
            self.tab.notify("Erst ein Bild wählen")
            return False
        return True

    def copy_str(self, text, msg):
        self.tab.win.get_clipboard().set_content(
            Gdk.ContentProvider.new_for_bytes("text/plain;charset=utf-8", GLib.Bytes.new(text.encode())))
        self.tab.notify(msg)

    def copy_text(self):
        if self.ready():
            self.copy_str("\n".join(self.result[0]) + "\n", "ASCII-Text kopiert")

    def copy_ansi(self):
        if self.ready():
            self.copy_str(ansi_text(*self.result), "ANSI-Text kopiert (fürs Terminal)")

    def base(self):
        return os.path.splitext(os.path.basename(self.src_path))[0] if self.src_path else "ascii"

    def save_text(self, ext, text):
        def go(path):
            if not path.lower().endswith("." + ext):
                path += "." + ext
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                self.tab.notify(f"Gespeichert: {os.path.basename(path)}")
            except OSError as e:
                self.tab.notify(f"Fehler: {e}")
        self.save_dialog("ASCII speichern", f"{self.base()}-ascii.{ext}", go, "~/Documents")

    def save_txt(self):
        if self.ready():
            self.save_text("txt", "\n".join(self.result[0]) + "\n")

    def save_ansi(self):
        if self.ready():
            self.save_text("ans", ansi_text(*self.result))

    def save_png(self):
        if not self.ready():
            return
        lines, color, _ = self.result
        p = dict(self.p)

        def go(path):
            if not path.lower().endswith(".png"):
                path += ".png"

            def work():
                render_ascii_image(lines, color, p, p["size"]).save(path, "PNG")
                return path
            self.in_background(work, lambda r: self.tab.notify(
                f"Fehler: {r}" if isinstance(r, Exception) else f"Gespeichert: {os.path.basename(r)}"))
        self.save_dialog("ASCII-Bild speichern", f"{self.base()}-ascii.png", go)


# ----------------------------------------------------------------------
# Seite
# ----------------------------------------------------------------------
class StudioPage(Gtk.Box):
    title = "Verlauf & ASCII"
    subtitle = "Farbverläufe erstellen · Bilder in ASCII-Kunst verwandeln"

    def __init__(self, tab, mode="gradient"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.tab = tab
        top = Gtk.Box(spacing=6)
        top.add_css_class("studio-top")
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True)
        self.panels = {}
        self.top_btns = {}
        first = None
        for key, label in (("gradient", "󰏘  Verlauf"), ("ascii", "󰈙  ASCII")):
            b = Gtk.ToggleButton(label=label)
            b.add_css_class("pb")
            if first:
                b.set_group(first)
            first = first or b
            b.connect("toggled", lambda b, key=key: b.get_active() and self.show(key))
            top.append(b)
            self.top_btns[key] = b
        self.append(top)
        self.append(self.stack)
        self.top_btns[mode].set_active(True)

    def show(self, key):
        if key not in self.panels:
            self.panels[key] = (GradientPanel if key == "gradient" else AsciiPanel)(self)
            self.stack.add_named(self.panels[key], key)
        self.stack.set_visible_child_name(key)

    def pause(self):
        pass
