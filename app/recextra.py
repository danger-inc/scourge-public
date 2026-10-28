# Zusätze für die Bildschirmaufnahme: Ton mischen (System + Mikrofon), Kamera-Blase und Maus-Hervorhebung.
# Alles wird nur während einer Aufnahme angelegt und danach wieder abgebaut (Audio-Module, Fenster).
import json
import os
import socket
import subprocess
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk

MIX_NAME = "scourge_rec_mix"
CAM_TITLE = "scourge-kamera"


# ---------------------------------------------------------------- Umgebung
WLROOTS = ("hyprland", "sway", "river", "labwc", "wayfire", "niri", "dwl", "qtile", "mango", "scroll")


def environment():
    """Was kann diese Arbeitsumgebung? -> (Art, Hinweis). Art: "ok", "maybe" (unbekannte Wayland-Umgebung, einfach
    versuchen) oder "no" (geht mit wf-recorder nicht; der Hinweis nennt, was stattdessen hilft)."""
    if os.name == "nt":
        return "no", ("Unter Windows nimmt Scourge den Bildschirm nicht selbst auf. Mit Windows+Alt+R (Xbox Game Bar) oder "
                      "dem Snipping-Tool geht es; die Aufnahme lässt sich danach hier im Schnitt öffnen.")
    desk = (os.environ.get("XDG_CURRENT_DESKTOP", "") + ":" + os.environ.get("XDG_SESSION_DESKTOP", "")
            + ":" + os.environ.get("DESKTOP_SESSION", "")).lower()
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") or os.environ.get("SWAYSOCK") or any(w in desk for w in WLROOTS):
        return "ok", ""
    if not os.environ.get("WAYLAND_DISPLAY"):
        return "no", ("Bildschirmaufnahme braucht eine Wayland-Sitzung mit wlroots (Hyprland, Sway …). Unter X11 nimm "
                      "OBS Studio oder SimpleScreenRecorder und öffne die Datei danach hier im Schnitt.")
    if "gnome" in desk:
        return "no", ("GNOME hat die Aufnahme eingebaut: Strg+Umschalt+Alt+R startet und stoppt sie, die Datei liegt "
                      "danach unter Videos/Bildschirmaufnahmen. Öffne sie hier im Schnitt.")
    if "kde" in desk or "plasma" in desk:
        return "no", ("KDE Plasma hat die Aufnahme in Spectacle (Umschalt+Druck, dann „Bildschirmaufnahme“). Die Datei lässt "
                      "sich danach hier im Schnitt öffnen.")
    return "maybe", ""


# ---------------------------------------------------------------- Audio
def _pactl(*args):
    try:
        return subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def devices(kind):
    """[(Name, Anzeigename)] der Mikrofone ("source") bzw. Ausgaben ("sink"); Monitor-Quellen werden ausgelassen."""
    out = []
    try:
        data = json.loads(_pactl("-f", "json", "list", f"{kind}s") or "[]")
    except ValueError:
        data = []
    for d in data:
        name = d.get("name", "")
        if kind == "source" and name.endswith(".monitor"):
            continue
        out.append((name, d.get("description") or name))
    return out


def default_device(kind):
    return _pactl(f"get-default-{kind}").strip() or None


class AudioMix:
    """Mischt Systemton und Mikrofon in eine eigene Senke („Scourge-Aufnahme“), deren Monitor wf-recorder aufnimmt."""

    def __init__(self):
        self.modules = []

    @staticmethod
    def cleanup_stale():
        """Reste einer abgestürzten Aufnahme wegräumen."""
        for line in _pactl("list", "short", "modules").splitlines():
            parts = line.split("\t")
            if len(parts) > 2 and MIX_NAME in line:
                _pactl("unload-module", parts[0])

    def start(self, sink, source):
        self.cleanup_stale()
        mod = _pactl("load-module", "module-null-sink", f"sink_name={MIX_NAME}",
                     f"sink_properties=device.description=Scourge-Aufnahme").strip()
        if not mod.isdigit():
            return None
        self.modules.append(mod)
        for src in (f"{sink}.monitor" if sink else None, source):
            if not src:
                continue
            m = _pactl("load-module", "module-loopback", f"source={src}", f"sink={MIX_NAME}", "latency_msec=30").strip()
            if m.isdigit():
                self.modules.append(m)
        return f"{MIX_NAME}.monitor"

    def stop(self):
        for m in reversed(self.modules):
            _pactl("unload-module", m)
        self.modules = []


# ---------------------------------------------------------------- Hyprland
def _hypr_socket(name):
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    rt = os.environ.get("XDG_RUNTIME_DIR")
    return f"{rt}/hypr/{sig}/{name}" if sig and rt else None


def hypr_query(cmd):
    """Befehl direkt an Hyprlands Socket (viel schneller als `hyprctl` als Prozess, nötig für die Maus-Hervorhebung)."""
    path = _hypr_socket(".socket.sock")
    if not path:
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            s.connect(path)
            s.sendall(cmd.encode())
            buf = b""
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
            return buf.decode()
    except OSError:
        return None


def hypr_eval(lua):
    return hypr_query(f"eval {lua}")


# ---------------------------------------------------------------- Kamera-Blase
class CamBubble(Gtk.Window):
    """Runde Kamera-Vorschau, die mit aufgenommen wird. Schwebt (Hyprland-Regel), mit Super+Ziehen verschiebbar."""
    SIZE = 240

    def __init__(self, device="/dev/video0", app=None):
        super().__init__(title=CAM_TITLE, decorated=False, resizable=False, default_width=self.SIZE,
                         default_height=self.SIZE, application=app)
        self.device = device
        self.set_focusable(False)
        self.add_css_class("cam-bubble")
        self.pic = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
        self.pic.set_size_request(self.SIZE, self.SIZE)
        self.pic.add_css_class("cam-pic")
        self.set_child(self.pic)
        self.pipe = None
        self._alive = False
        css = Gtk.CssProvider()
        css.load_from_string("window.cam-bubble { background: transparent; } "
                             ".cam-pic { border-radius: 9999px; border: 3px solid white; }")
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_USER)

    def start(self):
        """False, wenn die Kamera nicht startet."""
        try:
            gi.require_version("Gst", "1.0")
            gi.require_version("GstApp", "1.0")
            from gi.repository import Gst
            Gst.init(None)
            self.Gst = Gst
            self.pipe = Gst.parse_launch(
                f"v4l2src device={self.device} ! videoconvert ! videoscale ! "
                f"video/x-raw,format=RGBA,width=480,height=360 ! appsink name=s emit-signals=true max-buffers=1 drop=true sync=false")
        except Exception:
            return False
        sink = self.pipe.get_by_name("s")
        sink.connect("new-sample", self._sample)
        self._alive = True
        if self.pipe.set_state(self.Gst.State.PLAYING) == self.Gst.StateChangeReturn.FAILURE:
            self.stop()
            return False
        place_rule()
        self.present()
        return True

    def _sample(self, sink):
        smp = sink.emit("pull-sample")
        if not smp or not self._alive:
            return self.Gst.FlowReturn.OK
        buf = smp.get_buffer()
        ok, info = buf.map(self.Gst.MapFlags.READ)
        if ok:
            data = bytes(info.data)
            buf.unmap(info)
            c = smp.get_caps().get_structure(0)
            GLib.idle_add(self._show, data, c.get_value("width"), c.get_value("height"))
        return self.Gst.FlowReturn.OK

    def _show(self, data, w, h):
        if self._alive:
            self.pic.set_paintable(Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(data), w * 4))
        return False

    def stop(self):
        self._alive = False
        if self.pipe:
            self.pipe.set_state(self.Gst.State.NULL)
            self.pipe = None
        self.destroy()


def place_rule():
    """Regel: Kamera-Fenster schwebt, bleibt auf allen Arbeitsflächen und sitzt unten rechts."""
    hypr_eval('hl.window_rule({name="scourge-kamera", match={title="^%s$"}, float=true, pin=true, '
              'size={%d,%d}, move={"monitor_w-window_w-30","monitor_h-window_h-30"}, no_initial_focus=true})'
              % (CAM_TITLE, CamBubble.SIZE, CamBubble.SIZE))


# ---------------------------------------------------------------- Maus-Hervorhebung
class Halo:
    """Leuchtender Ring um den Mauszeiger. Läuft als eigener Prozess (halo_proc.py): die Layer-Shell-Bibliothek muss
    vor libwayland geladen werden (LD_PRELOAD), das geht im laufenden Scourge nicht mehr."""

    def __init__(self, app=None):
        self.proc = None

    def start(self):
        import ctypes.util
        import sys
        lib = ctypes.util.find_library("gtk4-layer-shell")
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "halo_proc.py")
        if not lib or not os.path.exists(script):
            return False
        path = lib
        for d in ("/usr/lib", "/usr/lib64", "/usr/lib/x86_64-linux-gnu"):
            if os.path.exists(os.path.join(d, lib)):
                path = os.path.join(d, lib)
                break
        env = dict(os.environ, LD_PRELOAD=path)
        try:
            self.proc = subprocess.Popen([sys.executable, script], env=env, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            return False
        return True

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None


def cam_devices():
    """Kameras, die wirklich Bilder liefern (video1 ist oft nur ein Metadaten-Knoten)."""
    out = []
    base = "/sys/class/video4linux"
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return out
    for n in names:
        try:
            with open(f"{base}/{n}/index") as f:
                if f.read().strip() != "0":
                    continue
            with open(f"{base}/{n}/name") as f:
                out.append((f"/dev/{n}", f.read().strip()))
        except OSError:
            pass
    return out
