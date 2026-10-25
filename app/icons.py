# Eigene App-Icons für die Suite: Lege <app>.svg oder <app>.png in ~/.config/ist-icons/ – scourge schreibt
# sie als echtes App-Symbol ins Icon-Theme (Starter, Taskleiste, Fenster). Das ursprüngliche Symbol wird vorher
# nach ~/.config/ist-icons/.original/ gesichert und beim Zurücksetzen wiederhergestellt.
import os
import shutil
import subprocess
import time

CUSTOM = os.path.expanduser("~/.config/ist-icons")
ORIG = os.path.join(CUSTOM, ".original")
OLD = os.path.join(CUSTOM, ".alt")           # zurückgesetzte eigene Entwürfe (nichts geht verloren)
HICOLOR = os.path.expanduser("~/.local/share/icons/hicolor")
APPS_DIR = os.path.expanduser("~/.local/share/applications")

# Schlüssel → (Icon-Name im Theme, .desktop-Datei)
SUITE = {
    "scourge": ("scourge", "scourge.desktop"),
    "notist": ("notist", "notist.desktop"),
    "filist": ("filist", "filist.desktop"),
    "musicalist": ("musicalist", "de.invincible.Music.desktop"),
    "datist": ("datist", "datist.desktop"),
    "wallist": ("wallist", "wallist.desktop"),
    "mailist": ("mailist", "mailist.desktop"),
    "sharist": ("sharist", "sharist.desktop"),
    "linguist": ("linguist", "linguist.desktop"),
    "inquirist": ("inquirist", "inquirist.desktop"),
}
EXTS = (".svg", ".png")
# Feste Größen fürs Theme: Starter (rofi), Taskleiste und die Apps-Leiste laden dann eine winzige PNG statt die
# eigene SVG mit eingebettetem 2048-px-Bild (8 MB, spürbar langsam bei jedem Super+R)
SIZES = (16, 22, 24, 32, 48, 64, 128, 256, 512)
SVG_MAX = 512           # eingebettete Bilder in der scalable-SVG höchstens so groß
STAMP = os.path.join(CUSTOM, ".stand")


def custom_file(key):
    for ext in EXTS:
        p = os.path.join(CUSTOM, key + ext)
        if os.path.isfile(p):
            return p
    return None


def has_custom(key):
    return custom_file(key) is not None


def theme_file(name):
    """Datei, die das Theme gerade für diesen Icon-Namen liefert (oder None)."""
    for p in (os.path.join(HICOLOR, "scalable", "apps", name + ".svg"),
              os.path.join(HICOLOR, "256x256", "apps", name + ".png")):
        if os.path.isfile(p):
            return p
    return None


def _png_path(name, size):
    return os.path.join(HICOLOR, f"{size}x{size}", "apps", name + ".png")


def small_file(key, size=48):
    """Kleine, schnell ladbare Fassung des eigenen Icons (erzeugte Theme-PNG), sonst die eigene Datei."""
    src = custom_file(key)
    if not src:
        return None
    name = SUITE[key][0]
    for s in SIZES:
        if s >= size and os.path.isfile(_png_path(name, s)):
            return _png_path(name, s)
    return src


def _light_svg(text):
    """Eingebettete Bilder auf SVG_MAX px verkleinern – Aussehen gleich, Datei winzig."""
    import base64
    import io
    import re
    from PIL import Image

    def repl(m):
        try:
            im = Image.open(io.BytesIO(base64.b64decode(m.group(2))))
            im.load()
        except Exception:  # noqa: BLE001
            return m.group(0)
        if max(im.size) <= SVG_MAX:
            return m.group(0)
        if im.mode not in ("RGB", "RGBA", "L", "LA"):
            im = im.convert("RGBA")
        im.thumbnail((SVG_MAX, SVG_MAX), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, "PNG", optimize=True)
        return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()
    return re.sub(r"data:image/(png|jpeg|jpg|webp|gif);base64,([A-Za-z0-9+/=\s]+)", repl, text)


def _render_pngs(src, name):
    """PNG je Größe aus der eigenen Datei erzeugen."""
    from PIL import Image
    if src.endswith(".png"):
        base = Image.open(src).convert("RGBA")
        render = lambda n: _square(base.copy(), n)            # noqa: E731
    else:
        def render(n):
            if shutil.which("rsvg-convert"):
                out = subprocess.run(["rsvg-convert", "-w", str(n), "-h", str(n), "-a", src],
                                     capture_output=True, check=True).stdout
                import io
                return _square(Image.open(io.BytesIO(out)).convert("RGBA"), n)
            import gi
            gi.require_version("GdkPixbuf", "2.0")
            from gi.repository import GdkPixbuf
            pb = GdkPixbuf.Pixbuf.new_from_file_at_size(src, n, n)
            im = Image.frombytes("RGBA" if pb.get_has_alpha() else "RGB", (pb.get_width(), pb.get_height()),
                                 pb.get_pixels(), "raw", "RGBA" if pb.get_has_alpha() else "RGB",
                                 pb.get_rowstride())
            return _square(im.convert("RGBA"), n)
    for n in SIZES:
        dst = _png_path(name, n)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        render(n).save(dst + ".tmp", "PNG", optimize=True)
        os.replace(dst + ".tmp", dst)


def _square(im, n):
    from PIL import Image
    if im.size != (n, n):
        im.thumbnail((n, n), Image.LANCZOS)
        canvas = Image.new("RGBA", (n, n), (0, 0, 0, 0))
        canvas.paste(im, ((n - im.width) // 2, (n - im.height) // 2))
        im = canvas
    return im


def _stamp(src):
    st = os.stat(src)
    return f"{os.path.basename(src)}:{st.st_size}:{int(st.st_mtime)}:{SVG_MAX}:{','.join(map(str, SIZES))}"


def _remove_pngs(name):
    for n in SIZES:
        try:
            os.remove(_png_path(name, n))
        except OSError:
            pass


def preview_file(key):
    """Bild für die Vorschau in scourge: eigenes Icon, sonst das installierte."""
    return custom_file(key) or theme_file(SUITE[key][0])


def _same(a, b):
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


def _desktop_icon(desk, value=None):
    """Icon= einer .desktop-Datei lesen oder (mit value) setzen."""
    path = os.path.join(APPS_DIR, desk)
    try:
        lines = open(path).read().split("\n")
    except OSError:
        return None
    cur = next((l[5:] for l in lines if l.startswith("Icon=")), None)
    if value is not None and cur != value:
        lines = [f"Icon={value}" if l.startswith("Icon=") else l for l in lines]
        with open(path, "w") as f:
            f.write("\n".join(lines))
    return cur


def _backup(name, desk):
    """Ursprüngliches Symbol einmal sichern."""
    os.makedirs(ORIG, exist_ok=True)
    marker = os.path.join(ORIG, name + ".icon")
    if os.path.exists(marker):
        return
    for p in (os.path.join(HICOLOR, "scalable", "apps", name + ".svg"),
              os.path.join(HICOLOR, "256x256", "apps", name + ".png")):
        if os.path.isfile(p):
            shutil.copy2(p, os.path.join(ORIG, os.path.basename(p)))
    with open(marker, "w") as f:
        f.write(_desktop_icon(desk) or name)


def apply(key):
    """Eigenes Icon ins Theme schreiben. True, wenn sich etwas geändert hat."""
    src = custom_file(key)
    if not src:
        return False
    name, desk = SUITE[key]
    _backup(name, desk)
    svg = os.path.join(HICOLOR, "scalable", "apps", name + ".svg")
    stamp_file = os.path.join(STAMP, key)
    stamp = _stamp(src)
    try:
        done = open(stamp_file).read() == stamp and all(os.path.isfile(_png_path(name, n)) for n in SIZES)
    except OSError:
        done = False
    changed = False
    if not done:
        if src.endswith(".svg"):
            light = _light_svg(open(src, encoding="utf-8", errors="surrogateescape").read())
            os.makedirs(os.path.dirname(svg), exist_ok=True)
            with open(svg + ".tmp", "w", encoding="utf-8", errors="surrogateescape") as f:
                f.write(light)
            os.replace(svg + ".tmp", svg)
            _render_pngs(svg, name)
        else:
            if os.path.exists(svg):        # eine alte SVG würde sonst gewinnen
                os.remove(svg)
            _render_pngs(src, name)
        os.makedirs(STAMP, exist_ok=True)
        with open(stamp_file, "w") as f:
            f.write(stamp)
        changed = True
    if _desktop_icon(desk) not in (None, name):          # z. B. musicalist hatte ein System-Symbol
        _desktop_icon(desk, name)
        changed = True
    return changed


def set_custom(key, path):
    """Neues eigenes Icon übernehmen (aus einer beliebigen Datei) und anwenden."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in EXTS:
        raise ValueError("Bitte eine .svg- oder .png-Datei wählen")
    os.makedirs(CUSTOM, exist_ok=True)
    old = custom_file(key)
    if old and not _same(old, path):
        _park(old, key)
    shutil.copyfile(path, os.path.join(CUSTOM, key + ext))
    refresh(apply(key) or True)


def _park(path, key):
    os.makedirs(OLD, exist_ok=True)
    shutil.move(path, os.path.join(OLD, f"{key}-{time.strftime('%Y%m%d-%H%M%S')}{os.path.splitext(path)[1]}"))


def reset(key):
    """Eigenes Icon entfernen (landet in .alt/) und das ursprüngliche Symbol wiederherstellen."""
    name, desk = SUITE[key]
    src = custom_file(key)
    if src:
        _park(src, key)
    _remove_pngs(name)
    try:
        os.remove(os.path.join(STAMP, key))
    except OSError:
        pass
    marker = os.path.join(ORIG, name + ".icon")
    if not os.path.exists(marker):
        return refresh(True)
    for sub, ext in (("scalable", ".svg"), ("256x256", ".png")):
        dst = os.path.join(HICOLOR, sub, "apps", name + ext)
        bak = os.path.join(ORIG, name + ext)
        if os.path.isfile(bak):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(bak, dst)
        elif os.path.isfile(dst):
            os.remove(dst)
    try:
        _desktop_icon(desk, open(marker).read().strip())
    except OSError:
        pass
    for f in (marker, os.path.join(ORIG, name + ".svg"), os.path.join(ORIG, name + ".png")):
        if os.path.exists(f):
            os.remove(f)
    refresh(True)


def apply_all():
    """Alle eigenen Icons (erneut) anwenden – z. B. nach einem Update, das das Logo überschrieben hat."""
    changed = False
    for key in SUITE:
        try:
            changed |= apply(key)
        except OSError:
            pass
    refresh(changed)
    return changed


def refresh(changed):
    if not changed:
        return
    for cmd in (["gtk-update-icon-cache", "-f", "-t", "-q", HICOLOR], ["update-desktop-database", APPS_DIR]):
        if shutil.which(cmd[0]):
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
