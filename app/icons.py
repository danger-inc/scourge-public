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
}
EXTS = (".svg", ".png")


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
    png = os.path.join(HICOLOR, "256x256", "apps", name + ".png")
    dst, other = (svg, png) if src.endswith(".svg") else (png, svg)
    changed = False
    if os.path.exists(other):              # das jeweils andere Format würde sonst gewinnen
        os.remove(other)
        changed = True
    if not _same(src, dst):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
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
    marker = os.path.join(ORIG, name + ".icon")
    if not os.path.exists(marker):
        return
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
