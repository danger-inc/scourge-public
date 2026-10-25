# Netzlaufwerke und PDF-Laden – bewusst ohne PIL/lxml, damit scourge-pdf schlank startet (formats.py importiert das).
import hashlib, os, time

import gi
from gi.repository import GLib

NET_FS = ("fuse.gvfsd-fuse", "cifs", "smb3", "nfs", "nfs4", "fuse.sshfs", "davfs", "fuse.rclone", "9p")
_net_cache = {}          # Pfad → (mtime, größe, bytes) – Netz-Dateien nur einmal übers Netz holen


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
