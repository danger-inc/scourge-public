# Öffnen: Bilder, Videos und Musik direkt ansehen/anhören – im Vollbild, mit Zeitleiste, Wiederholen und Leertaste.
# MediaBar (Abspielleiste) wird auch von der Dateiprüfung für Audio genutzt.
import hashlib, os, subprocess, threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

import chrome as ch

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif", ".heic", ".svg", ".ico")
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".3gp", ".mts", ".wmv", ".flv")
AUDIO_EXT = (".mp3", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus", ".wav", ".wma", ".aiff", ".aif", ".amr", ".3ga")
CACHE = os.path.expanduser("~/.cache/scourge/abspielen")

PLAYER_CSS = """
.mediabar { padding: 4px 2px; }
.mediabar scale trough { min-height: 6px; }
.mediabar .tcur { font-variant-numeric: tabular-nums; }
.player-name { font-weight: 800; }
.player-sub { color: alpha(@foreground, 0.6); font-size: 12px; }
.player-stage { background-color: alpha(black, 0.28); border-radius: 10px; }
"""


def kind(path):
    low = path.lower()
    if low.endswith(IMAGE_EXT):
        return "image"
    if low.endswith(VIDEO_EXT):
        return "video"
    if low.endswith(AUDIO_EXT):
        return "audio"
    if low.endswith(".pdf"):
        return "pdf"
    ctype = Gio.content_type_guess(path, None)[0] or ""
    for k in ("image", "video", "audio"):
        if ctype.startswith(k + "/"):
            return k
    return None


def fmt_t(sec, frac=True):
    sec = max(0.0, sec)
    m, s = divmod(sec, 60)
    h, m = divmod(int(m), 60)
    core = f"{h}:{m:02d}:{int(s):02d}" if h else f"{m}:{int(s):02d}"
    return core + (f".{int((s % 1) * 10)}" if frac else "")


# ---------------------------------------------------------------- abspielbar machen
def _gst_can_play(path):
    """Hat GStreamer einen Decoder für die Videospur? (ohne gst-libav meist nicht für H.264/H.265)"""
    codec = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name",
                            "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip()
    need = {"h264": ("avdec_h264", "openh264dec", "vah264dec"), "hevc": ("avdec_h265", "vah265dec"),
            "vp9": ("vp9dec",), "vp8": ("vp8dec",), "av1": ("dav1ddec", "av1dec")}.get(codec, ())
    if not need:
        return True
    try:
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        Gst.init(None)
        return any(Gst.ElementFactory.find(n) for n in need)
    except Exception:
        return False


def playable(src, what, done):
    """Datei in etwas umwandeln, das GStreamer hier sicher abspielt (im Hintergrund), dann done(pfad|None) im
    Hauptthread. Audio → Opus (GStreamer stürzt auf manchen Systemen bei MP3 ab, AAC fehlt ohne gst-libav);
    Video → bleibt, wenn ein Decoder da ist, sonst WebM (VP9)."""
    os.makedirs(CACHE, exist_ok=True)

    def work():
        try:
            st = os.stat(src)
            tag = hashlib.sha1(f"{src}|{st.st_mtime_ns}".encode()).hexdigest()[:12]
            if what == "audio":
                out = os.path.join(CACHE, tag + ".ogg")
                if not os.path.exists(out):
                    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-map", "0:a:0", "-vn",
                                        "-map_metadata", "-1", "-c:a", "libopus", "-b:a", "128k", out + ".part.ogg"],
                                       capture_output=True)
                    if r.returncode != 0:
                        return GLib.idle_add(done, None)
                    os.replace(out + ".part.ogg", out)
                return GLib.idle_add(done, out)
            if _gst_can_play(src):
                return GLib.idle_add(done, src)
            out = os.path.join(CACHE, tag + ".webm")
            if not os.path.exists(out):
                r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-map", "0:v:0", "-map", "0:a?",
                                    "-vf", "scale='min(1280,iw)':-2", "-c:v", "libvpx-vp9", "-deadline", "realtime",
                                    "-cpu-used", "8", "-row-mt", "1", "-crf", "32", "-b:v", "0", "-c:a", "libopus",
                                    out + ".part.webm"], capture_output=True)
                if r.returncode != 0:
                    return GLib.idle_add(done, None)
                os.replace(out + ".part.webm", out)
            GLib.idle_add(done, out)
        except OSError:
            GLib.idle_add(done, None)
    threading.Thread(target=work, daemon=True).start()


# ---------------------------------------------------------------- Abspielleiste
class MediaBar(Gtk.Box):
    """Abspielen/Pause, genaue Zeit (Zehntel), Zeitleiste mit Zeit unter dem Zeiger, Wiederholen, Lautstärke.
    Gleiche Schnittstelle wie Gtk.MediaControls (set_media_stream/get_media_stream)."""

    def __init__(self, loop=True, on_full=None):
        super().__init__(spacing=10, valign=Gtk.Align.CENTER)
        self.add_css_class("mediabar")
        self.stream = None
        self._ids = []
        self.loop = loop
        self.play_btn = ch.button("󰐊", "Abspielen / Pause (Leertaste)", callback=self.toggle)
        self.append(self.play_btn)
        self.cur = Gtk.Label(label="0:00.0", xalign=1, width_chars=7)
        self.cur.add_css_class("key"); self.cur.add_css_class("tcur")
        self.append(self.cur)
        self.scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, draw_value=False,
                               adjustment=Gtk.Adjustment(value=0, lower=0, upper=1, step_increment=0.005,
                                                         page_increment=0.05))
        self.scale.add_css_class("pb")
        self.scale.swipe_owns_h = True          # waagerecht = spulen (nicht „zurück“-Wischen)
        self.scale.connect("change-value", self._on_seek)
        mo = Gtk.EventControllerMotion()
        mo.connect("motion", self._hover)
        self.scale.add_controller(mo)
        self.append(self.scale)
        self.total = Gtk.Label(label="0:00.0", xalign=0, width_chars=7)
        self.total.add_css_class("key"); self.total.add_css_class("tcur")
        self.append(self.total)
        self.loop_btn = Gtk.ToggleButton(label="󰑖", active=loop, focus_on_click=False,
                                         tooltip_text="Wiederholen (an = Endlosschleife)")
        self.loop_btn.add_css_class("pb"); self.loop_btn.add_css_class("small")
        self.loop_btn.connect("toggled", self._on_loop)
        self.append(self.loop_btn)
        self.vol = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 0.05)
        self.vol.set_value(1.0)
        self.vol.set_draw_value(False)
        self.vol.set_size_request(80, -1)
        self.vol.set_tooltip_text("Lautstärke")
        self.vol.add_css_class("pb")
        self.vol.connect("value-changed", lambda s: self.stream and self.stream.set_volume(s.get_value()))
        self.append(Gtk.Label(label="󰕾"))
        self.append(self.vol)
        if on_full:
            self.append(ch.button("󰊓", "Vollbild (F)", callback=on_full))

    # ----- Schnittstelle wie MediaControls
    def get_media_stream(self):
        return self.stream

    def set_media_stream(self, stream):
        for sid in self._ids:
            try:
                self.stream.disconnect(sid)
            except Exception:
                pass
        self._ids = []
        self.stream = stream
        if stream is None:
            self._show(0, 0)
            return
        stream.set_loop(self.loop)
        stream.set_volume(self.vol.get_value())
        for sig in ("notify::timestamp", "notify::duration", "notify::prepared"):
            self._ids.append(stream.connect(sig, lambda *_: self._tick()))
        self._ids.append(stream.connect("notify::playing", lambda *_: self._playing()))
        self._playing()
        self._tick()

    # ----- Bedienung
    def toggle(self):
        s = self.stream
        if s is None:
            return
        if s.get_playing():
            s.pause()
        else:
            if s.get_ended():
                s.seek(0)
            s.play()

    def seek_by(self, sec):
        s = self.stream
        if s is not None and s.is_seekable():
            dur = s.get_duration()
            t = s.get_timestamp() + int(sec * 1e6)
            s.seek(max(0, min(t, dur - 1) if dur else max(0, t)))

    def _on_loop(self, btn):
        self.loop = btn.get_active()
        if self.stream is not None:
            self.stream.set_loop(self.loop)

    def _on_seek(self, scale, _scroll, value):
        s = self.stream
        if s is not None and s.is_seekable():
            dur = s.get_duration()
            s.seek(int(max(0.0, min(value, 1.0)) * dur))
        return False

    def _hover(self, _ctl, x, _y):
        s = self.stream
        w = self.scale.get_width()
        if s is not None and w > 0 and s.get_duration():
            frac = max(0.0, min(x / w, 1.0))
            self.scale.set_tooltip_text(fmt_t(frac * s.get_duration() / 1e6))

    def _playing(self):
        s = self.stream
        self.play_btn.set_label("󰏤" if s is not None and s.get_playing() else "󰐊")

    def _tick(self):
        s = self.stream
        if s is None:
            return
        self._show(s.get_timestamp(), s.get_duration())

    def _show(self, ts, dur):
        self.cur.set_label(fmt_t(ts / 1e6))
        self.total.set_label(fmt_t(dur / 1e6))
        if dur:
            adj = self.scale.get_adjustment()
            if abs(adj.get_value() - ts / dur) > 1e-4:
                self.scale.handler_block_by_func(self._on_seek)
                adj.set_value(ts / dur)
                self.scale.handler_unblock_by_func(self._on_seek)
        self.scale.set_sensitive(bool(dur))


# ---------------------------------------------------------------- Seite „Öffnen“
class PlayerPage(Gtk.Box):
    title = "Öffnen"
    subtitle = ""

    def __init__(self, tab):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=10, margin_bottom=10,
                         margin_start=14, margin_end=14)
        self.tab = tab
        self.path = None
        self.files = []
        self.media = None
        self.full = False
        self.token = 0
        head = Gtk.Box(spacing=10)
        self.name = Gtk.Label(label="", xalign=0, hexpand=True, ellipsize=3)
        self.name.add_css_class("player-name")
        self.info = Gtk.Label(label="", xalign=1)
        self.info.add_css_class("player-sub")
        head.append(self.name)
        head.append(self.info)
        check = ch.button("󰈈 prüfen", "Diese Datei durchleuchten (Metadaten, versteckte Inhalte)",
                          callback=lambda: self.path and self.tab.load_path(self.path))
        head.append(check)
        self.head = head
        self.append(head)
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, hexpand=True, vexpand=True)
        self.audio_view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, valign=Gtk.Align.CENTER,
                                  halign=Gtk.Align.CENTER)
        big = Gtk.Label(label="󰎈")
        big.add_css_class("empty-icon")
        self.audio_name = Gtk.Label(label="", wrap=True, max_width_chars=40, justify=Gtk.Justification.CENTER)
        self.audio_name.add_css_class("empty-title")
        self.audio_view.append(big)
        self.audio_view.append(self.audio_name)
        self.wait = Gtk.Label(label="wird für die Wiedergabe vorbereitet …")
        self.wait.add_css_class("player-sub")
        self.empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER,
                             halign=Gtk.Align.CENTER)
        self.empty.append(ch.label("Bild, Video oder Musik öffnen", "empty-title"))
        self.empty.append(ch.button("󰝰 öffnen", "Datei wählen (Strg+O)", callback=self.ask))
        self.stack = Gtk.Stack(vexpand=True, hexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_css_class("player-stage")
        for n, w in (("empty", self.empty), ("pic", self.picture), ("audio", self.audio_view), ("wait", self.wait)):
            self.stack.add_named(w, n)
        self.append(self.stack)
        self.bar = MediaBar(on_full=lambda: self.set_full(not self.full))
        self.bar.set_visible(False)
        self.append(self.bar)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)
        self.set_focusable(True)
        click = Gtk.GestureClick()
        click.connect("pressed", lambda g, n, *_: n == 2 and self.set_full(not self.full))
        self.stage_click = click
        self.stack.add_controller(click)

    # ----- Dateien
    def ask(self):
        dlg = Gtk.FileDialog(title="Öffnen")
        flt = Gtk.FileFilter(name="Bilder, Videos, Musik, PDF")
        for pat in ["*" + e for e in IMAGE_EXT + VIDEO_EXT + AUDIO_EXT] + ["*.pdf"]:
            flt.add_suffix(pat[2:])
        everything = Gtk.FileFilter(name="Alle Dateien (Office, Archive … → Dateiprüfung)")
        everything.add_pattern("*")
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(flt)
        store.append(everything)
        dlg.set_filters(store)
        if self.path:
            dlg.set_initial_folder(Gio.File.new_for_path(os.path.dirname(self.path)))

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                if self.path is None:                   # abgebrochen, nichts offen → zurück zur Startseite
                    self.tab.go_home()
                return
            if f and f.get_path():
                self.tab.open_media(f.get_path())
        dlg.open(self.tab.app_main.win, None, done)

    def load(self, path):
        """Bild/Video/Musik zeigen (PDFs reicht Tab.open_media an scourge-pdf weiter)."""
        k = kind(path)
        self.token += 1
        token = self.token
        self.stop()
        self.path = path
        self.name.set_label(os.path.basename(path))
        self.tab.set_title(os.path.basename(path), {"image": "Bild", "video": "Video", "audio": "Musik"}.get(k, ""))
        self._siblings(path, k)
        self.bar.set_visible(k in ("video", "audio"))
        if k == "image":
            self.show_image(path)
        elif k in ("video", "audio"):
            self.stack.set_visible_child_name("wait")
            self.info.set_label("")

            def ready(out):
                if token != self.token:
                    return False
                if not out:
                    self.wait.set_label("Das ließ sich nicht abspielen.")
                    return False
                self.play(out, k)
                return False
            playable(path, k, ready)
        else:
            self.stack.set_visible_child_name("empty")
        self.grab_focus()

    def _siblings(self, path, k):
        """Gleichartige Dateien im Ordner → mit ← / → durchblättern."""
        try:
            d = os.path.dirname(path)
            names = sorted(os.listdir(d), key=str.lower)
            self.files = [os.path.join(d, n) for n in names if kind(n) == k and not n.startswith(".")]
        except OSError:
            self.files = [path]

    def show_image(self, path):
        try:
            tex = Gdk.Texture.new_from_filename(path)
        except GLib.Error:
            tex = self._pil_texture(path)
        if tex is None:
            self.wait.set_label("Dieses Bildformat lässt sich hier nicht anzeigen (Datei prüfen kann es).")
            self.stack.set_visible_child_name("wait")
            return
        self.picture.set_paintable(tex)
        self.info.set_label(f"{tex.get_width()} × {tex.get_height()} px"
                            + (f" · {self.files.index(path) + 1}/{len(self.files)}" if path in self.files else ""))
        self.stack.set_visible_child_name("pic")
        self.set_full(True)

    @staticmethod
    def _pil_texture(path):
        try:
            import io
            from PIL import Image, ImageOps
            im = ImageOps.exif_transpose(Image.open(path))
            buf = io.BytesIO()
            im.convert("RGBA").save(buf, "PNG", compress_level=1)
            return Gdk.Texture.new_from_bytes(GLib.Bytes.new(buf.getvalue()))
        except Exception:
            return None

    def play(self, out, k):
        from cutter import retire_media
        retire_media(self.media, self.picture)
        self.media = Gtk.MediaFile.new_for_filename(out)
        self.bar.set_media_stream(self.media)
        if k == "video":
            self.picture.set_paintable(self.media)
            self.stack.set_visible_child_name("pic")
            self.set_full(True)
        else:
            self.audio_name.set_label(os.path.basename(self.path))
            self.stack.set_visible_child_name("audio")
        self.media.play()

    def step(self, d):
        if self.path in self.files and len(self.files) > 1:
            i = (self.files.index(self.path) + d) % len(self.files)
            self.load(self.files[i])

    # ----- Vollbild
    def set_full(self, on):
        """Echtes Vollbild: Fenster, Tab-Leiste, Kopf- und Statuszeile weg. Esc / Doppelklick beendet."""
        on = bool(on)
        if on == self.full:
            return
        self.full = on
        t = self.tab
        t.focused = on
        win = t.app_main.win
        (win.fullscreen if on else win.unfullscreen)()
        t.bar.set_visible(not on)
        self.head.set_visible(not on)
        t.app_main.apply_chrome()
        self.grab_focus()

    # ----- Tasten
    def on_key(self, ctrl, keyval, code, mods):
        k = Gdk.keyval_name(keyval) or ""
        c = bool(mods & Gdk.ModifierType.CONTROL_MASK)
        if k == "space":
            self.bar.toggle()
        elif k == "Escape" and self.full:
            self.set_full(False)
        elif k in ("f", "F", "F11"):
            self.set_full(not self.full)
        elif k in ("Left", "Right"):
            d = -1 if k == "Left" else 1
            if self.bar.stream is not None:
                self.bar.seek_by(5 * d)
            else:
                self.step(d)
        elif k in ("Page_Up", "Page_Down", "Up", "Down") and self.bar.stream is None:
            self.step(-1 if k in ("Page_Up", "Up") else 1)
        elif k in ("Page_Up", "Page_Down") and self.bar.stream is not None:
            self.step(-1 if k == "Page_Up" else 1)
        else:
            return False
        return True

    # ----- Aufräumen
    def stop(self):
        if self.media is not None:
            from cutter import retire_media
            self.bar.set_media_stream(None)
            retire_media(self.media, self.picture)
            self.media = None

    def pause(self):
        if self.media is not None:
            self.media.pause()

    def shutdown(self):
        if self.full:
            self.set_full(False)
        self.stop()
