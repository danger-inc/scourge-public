# Bildoperationen für den Meme-Editor: drehen/spiegeln und einfarbigen Hintergrund entfernen (ohne KI-Modell,
# nur numpy). Alles arbeitet auf PIL-Bildern und ist deterministisch, damit der Editor es als Liste von
# Schritten merken und rückgängig machen kann.
import numpy as np
from PIL import Image, ImageFilter

GEOM = {"cw": Image.Transpose.ROTATE_270, "ccw": Image.Transpose.ROTATE_90,
        "fliph": Image.Transpose.FLIP_LEFT_RIGHT, "flipv": Image.Transpose.FLIP_TOP_BOTTOM}


def map_point(op, x, y, w, h):
    """Wohin wandert ein Punkt (x, y) im Bild w×h bei dieser Drehung/Spiegelung?"""
    if op == "cw":
        return h - y, x
    if op == "ccw":
        return y, w - x
    if op == "fliph":
        return w - x, y
    return x, h - y


def map_rect(op, rect, w, h):
    x, y, rw, rh = rect
    (x1, y1), (x2, y2) = map_point(op, x, y, w, h), map_point(op, x + rw, y + rh, w, h)
    return min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)


def _region(close, x, y):
    """Zusammenhängende Fläche (4er-Nachbarschaft) um (x, y) in der Bool-Maske – zeilenweise, ohne scipy."""
    h, w = close.shape
    if not close[y, x]:
        return np.zeros_like(close)
    pad = np.zeros((h, w + 2), dtype=np.int8)
    pad[:, 1:-1] = close
    d = np.diff(pad, axis=1)
    starts = [np.flatnonzero(d[r] == 1) for r in range(h)]          # erste Spalte jedes Laufs
    ends = [np.flatnonzero(d[r] == -1) for r in range(h)]            # Spalte hinter dem Lauf
    seen = [np.zeros(len(s), dtype=bool) for s in starts]
    i0 = int(np.searchsorted(starts[y], x, side="right") - 1)
    stack = [(y, i0)]
    seen[y][i0] = True
    out = np.zeros_like(close)
    while stack:
        r, i = stack.pop()
        s, e = starts[r][i], ends[r][i]
        out[r, s:e] = True
        for nr in (r - 1, r + 1):
            if not 0 <= nr < h or len(starts[nr]) == 0:
                continue
            ns, ne = starts[nr], ends[nr]
            lo = int(np.searchsorted(ne, s, side="right"))           # erster Lauf, der nach s endet
            hi = int(np.searchsorted(ns, e, side="left"))            # Läufe, die vor e beginnen
            for j in range(lo, hi):
                if not seen[nr][j]:
                    seen[nr][j] = True
                    stack.append((nr, j))
    return out


def remove_bg(img, x, y, tol=28, glob=False, feather=1.2):
    """Farbe an (x, y) samt Umgebung durchsichtig machen. glob=True: diese Farbe überall, sonst nur zusammenhängend."""
    im = img.convert("RGBA")
    w, h = im.size
    x, y = max(0, min(w - 1, int(x))), max(0, min(h - 1, int(y)))
    a = np.asarray(im)
    seed = a[y, x, :3].astype(np.int16)
    close = np.abs(a[:, :, :3].astype(np.int16) - seed).max(axis=2) <= tol
    mask = close if glob else _region(close, x, y)
    if not mask.any():
        return im
    m = Image.fromarray((mask * 255).astype(np.uint8), "L")
    if feather:
        m = m.filter(ImageFilter.GaussianBlur(feather))
    alpha = np.asarray(im.getchannel("A")).astype(np.float32)
    alpha = alpha * (1 - np.asarray(m).astype(np.float32) / 255)
    out = im.copy()
    out.putalpha(Image.fromarray(alpha.astype(np.uint8), "L"))
    return out


def apply_ops(img, ops):
    """Schrittliste anwenden: Strings = drehen/spiegeln, ("bg", x, y, tol, glob) = Hintergrund entfernen."""
    for op in ops:
        if isinstance(op, str):
            img = img.transpose(GEOM[op])
        else:
            _, x, y, tol, glob = op
            img = remove_bg(img, x, y, tol, glob)
    return img
