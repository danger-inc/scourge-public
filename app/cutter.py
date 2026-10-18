# Video-Schnitt für Scourge: eine sehr leichte Zeitleiste im Stil von DaVinci Resolve.
# Das Video wird in Teile zerlegt (Schneiden), Teile werden behalten oder entfernt, Kanten lassen sich
# verschieben. Die Vorschau überspringt entfernte Teile schon beim Abspielen. Export über ffmpeg.
import datetime, io, json, math, os, subprocess, tempfile, threading
import chrome as ch

import cairo
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

CUTTER_CSS = """
.timeline-frame { background-color: alpha(black, 0.25); border-radius: 12px; }
.seg-row { border-radius: 9px; padding: 3px 6px; }
.seg-row.removed { opacity: 0.45; }
.seg-row.selected { background-color: alpha(@color9, 0.16); }
.big-time { font-size: 15px; font-weight: 800; font-feature-settings: "tnum"; }
progressbar.pb trough { min-height: 6px; border-radius: 4px; background-color: alpha(@foreground, 0.15); }
progressbar.pb progress { min-height: 6px; border-radius: 4px; background-color: @color9; }
"""
RULER, THUMB_H, WAVE_H = 22, 64, 38
TL_H = RULER + THUMB_H + WAVE_H + 10


def fmt(t, frac=True):
    t = max(0.0, t)
    m, s = divmod(t, 60)
    h, m = divmod(int(m), 60)
    core = f"{h}:{m:02d}:{int(s):02d}" if h else f"{m}:{int(s):02d}"
    return core + (f".{int((s % 1) * 10)}" if frac else "")


_RETIRED = []  # alte Videos, die noch kurz am Leben bleiben müssen


def retire_media(media, picture):
    """Ein Video sicher loswerden. Sofortiges Freigeben, während GTK noch ein Bild davon zeichnet,
    ließ Scourge abstürzen. Deshalb: anhalten, aus der Anzeige nehmen, erst nach 3 s schließen."""
    if not media:
        return
    try:
        media.pause()
    except Exception:
        pass
    if picture is not None and picture.get_paintable() is media:
        picture.set_paintable(None)
    _RETIRED.append(media)

    def close():
        try:
            media.clear()
        except Exception:
            pass
        GLib.timeout_add(2000, lambda: (_RETIRED.remove(media) if media in _RETIRED else None, False)[-1])
        return False
    GLib.timeout_add(3000, close)


def surface_from_png(data):
    return cairo.ImageSurface.create_from_png(io.BytesIO(data)) if data else None


class CutterPage(Gtk.Box):
    title = "Video schneiden"
    subtitle = ""

    def __init__(self, tab):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.tab = tab
        self.path = None
        self.dur = 0.0
        self.fps = 30.0
        self.segments = []      # [[Start, Ende, behalten]]
        self.sel = 0
        self.undo_stack = []
        self.zoom = 1.0
        self.playhead = 0.0
        self.thumbs = self.wave = None
        self.media = None
        self._drag = None
        self._seek_guard = False
        self.exporting = False
        self.load_token = 0     # verhindert, dass Vorschaubilder eines alten Videos im neuen landen
        self.build()

    # ================= Aufbau =================
    def build(self):
        body = Gtk.Box(spacing=16, vexpand=True, margin_start=16, margin_end=16)
        self.append(body)
        frame = Gtk.Box(hexpand=True, vexpand=True)
        frame.add_css_class("canvas-frame")
        frame.set_margin_start(0); frame.set_margin_bottom(0)
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, hexpand=True, vexpand=True)
        self.picture.set_margin_top(10); self.picture.set_margin_bottom(10)
        self.picture.set_margin_start(10); self.picture.set_margin_end(10)
        frame.append(self.picture)
        body.append(frame)

        side_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, hexpand=False)
        side_scroll.set_size_request(300, -1)
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        side_scroll.set_child(side)
        body.append(side_scroll)

        c = self.card(side, "Teile")
        self.result_lbl = Gtk.Label(xalign=0, wrap=True)
        self.result_lbl.add_css_class("note")
        c.append(self.result_lbl)
        self.seg_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        c.append(self.seg_list)
        row = Gtk.Box(spacing=6, homogeneous=True)
        row.append(self.button("󰑓  Von vorne", self.reset, "Alle Schnitte verwerfen, das Video bleibt"))
        row.append(self.button("󰈔  Anderes Video", self.pick_other, "Dieses Video verwerfen und ein anderes öffnen"))
        c.append(row)
        row = Gtk.Box(spacing=6, homogeneous=True)
        row.append(self.button("󰉋  Ordner öffnen", self.open_folder, "Ordner des Videos im Dateimanager zeigen"))
        c.append(row)

        c = self.card(side, "Exportieren")
        self.fmt_drop = Gtk.DropDown.new_from_strings(["MP4 (H.264)", "WebM (VP9)", "GIF", "Nur Ton (MP3)"])
        self.size_drop = Gtk.DropDown.new_from_strings(["Originalgröße", "1080p", "720p", "480p"])
        for lab, w in (("Format", self.fmt_drop), ("Größe", self.size_drop)):
            row = Gtk.Box(spacing=8)
            l = Gtk.Label(label=lab, xalign=0, width_chars=7)
            l.add_css_class("key")
            row.append(l)
            w.set_hexpand(True)
            row.append(w)
            c.append(row)
        self.audio_btn = Gtk.ToggleButton(label="󰕾  Ton behalten", active=True)
        self.audio_btn.add_css_class("pb"); self.audio_btn.add_css_class("small")
        c.append(self.audio_btn)
        self.export_btn = self.button("󰈇  Exportieren", self.export, small=False)
        c.append(self.export_btn)
        self.progress = Gtk.ProgressBar(visible=False)
        self.progress.add_css_class("pb")
        c.append(self.progress)
        self.after = Gtk.Box(spacing=6, homogeneous=True, visible=False)
        c.append(self.after)
        c.append(self.note("Exakte Schnitte: das Video wird dafür neu kodiert. Kurze Clips sind in Sekunden fertig."))

        # Transport + Werkzeuge
        tools = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=6, row_spacing=6,
                            max_children_per_line=20, margin_start=16, margin_end=16, homogeneous=False)
        self.play_btn = self.button("󰐊", self.toggle_play, "Abspielen/Pause (Leertaste)")
        self.time_lbl = Gtk.Label(label="0:00.0 / 0:00.0")
        self.time_lbl.add_css_class("big-time")
        items = [self.button("󰒮", lambda: self.seek(0), "Zum Anfang (Pos1)"),
                 self.button("󰒫", lambda: self.step(-1), "Ein Bild zurück (←, mit Umschalt 1 s)"),
                 self.play_btn,
                 self.button("󰒬", lambda: self.step(1), "Ein Bild vor (→, mit Umschalt 1 s)"),
                 self.time_lbl,
                 self.button("󰆐  Schneiden", lambda: self.split(announce=True), "Am Abspielkopf teilen (S)"),
                 self.button("󰆴  Entfernen", self.toggle_keep, "Markierten Teil entfernen oder zurückholen (Entf)"),
                 self.button("󰘏  Anfang hier", self.trim_start, "Alles vor dem Abspielkopf im Teil entfernen (I)"),
                 self.button("󰘖  Ende hier", self.trim_end, "Alles nach dem Abspielkopf im Teil entfernen (O)"),
                 self.button("󰕌", self.undo, "Rückgängig (Strg+Z)")]
        self.zoom_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 20, 0.5)
        self.zoom_scale.set_draw_value(False)
        self.zoom_scale.set_size_request(120, -1)
        self.zoom_scale.add_css_class("pb")
        self.zoom_scale.set_tooltip_text("Zeitleiste zoomen (auch Strg+Mausrad)")
        self.zoom_scale.connect("value-changed", lambda s: self.set_zoom(s.get_value()))
        items.append(Gtk.Label(label="󰍉"))
        items.append(self.zoom_scale)
        for w in items:
            tools.append(w)
        self.append(tools)

        tl_frame = Gtk.Box(margin_start=16, margin_end=16, margin_bottom=16)
        tl_frame.add_css_class("timeline-frame")
        self.tl_scroll = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER, hexpand=True)
        self.tl_scroll.set_size_request(-1, TL_H + 14)
        self.timeline = Gtk.DrawingArea(content_height=TL_H, focusable=True)
        self.timeline.set_draw_func(self.draw_timeline)
        self.tl_scroll.set_child(self.timeline)
        self.tl_scroll.get_hadjustment().connect("changed", lambda *_: self.resize_timeline())
        tl_frame.append(self.tl_scroll)
        self.append(tl_frame)

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag_update)
        drag.connect("drag-end", lambda *_: (setattr(self, "_drag", None), self.refresh_list()))
        self.timeline.add_controller(drag)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self.on_motion)
        self.timeline.add_controller(motion)
        zoom = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        zoom.connect("scroll", self.on_scroll)
        self.timeline.add_controller(zoom)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)

    def card(self, parent, title):
        box = ch.Card(title, spacing=8)
        parent.append(box)
        return box

    def note(self, text):
        l = Gtk.Label(label=text, xalign=0, wrap=True, max_width_chars=34)
        l.add_css_class("note")
        return l

    def button(self, label, cb, tip=None, small=True):
        b = Gtk.Button(label=label, tooltip_text=tip, valign=Gtk.Align.CENTER)
        b.add_css_class("pb")
        if small:
            b.add_css_class("small")
        b.connect("clicked", lambda *_: cb())
        return b

    # ================= Laden =================
    def load(self, path):
        if self.exporting:
            self.tab.notify("Bitte warten, bis der Export fertig ist.")
            return
        retire_media(self.media, self.picture)
        self.media = None
        self.load_token += 1
        self.path = path
        out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                             capture_output=True, text=True).stdout
        d = json.loads(out or "{}")
        v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
        self.has_audio = any(s.get("codec_type") == "audio" for s in d.get("streams", []))
        self.dur = float(d.get("format", {}).get("duration") or v.get("duration") or 0)
        num, _, den = v.get("avg_frame_rate", "30/1").partition("/")
        self.fps = float(num) / float(den) if float(den or 0) and float(num or 0) else 30.0
        self.segments = [[0.0, self.dur, True]]
        self.sel, self.undo_stack, self.playhead = 0, [], 0.0
        self.thumbs = self.wave = None
        self.title = f"Schnitt: {os.path.basename(path)}"
        self.subtitle = f"{fmt(self.dur, False)} · {v.get('width', '?')}×{v.get('height', '?')} · {self.fps:.0f} fps"
        self.audio_btn.set_sensitive(self.has_audio)
        self.after.set_visible(False)
        self.media = Gtk.MediaFile.new_for_filename(path)
        self.media.connect("notify::timestamp", self.on_time)
        self.media.connect("notify::playing", lambda m, _: m is self.media and self.play_btn.set_label("󰏤" if m.get_playing() else "󰐊"))
        self.media.connect("notify::error", self.on_error)
        self.picture.set_paintable(self.media)
        self.zoom_scale.set_value(1)
        self.refresh_list()
        self.resize_timeline()
        threading.Thread(target=self.make_strips, args=(path, self.dur, self.has_audio, self.load_token), daemon=True).start()
        GLib.timeout_add(300, lambda: (self.timeline.grab_focus(), False)[-1])

    def make_strips(self, path, dur, has_audio, token):
        """Vorschaubilder und Tonwelle für die Zeitleiste (im Hintergrund)."""
        n = max(4, min(60, int(dur / 2) + 4))
        thumbs = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf",
                                 f"fps={n / max(dur, 0.1):.5f},scale=-2:{THUMB_H * 2},tile={n}x1", "-frames:v", "1",
                                 "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True).stdout
        wave = b""
        if has_audio:
            wave = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-filter_complex",
                                   "aformat=channel_layouts=mono,showwavespic=s=2400x120:colors=0xffffff", "-frames:v", "1",
                                   "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True).stdout

        if wave:  # Helligkeit -> Transparenz, damit die Welle in der Akzentfarbe gemalt werden kann
            from PIL import Image
            im = Image.open(io.BytesIO(wave)).convert("L")
            mask = Image.new("RGBA", im.size, (255, 255, 255, 0))
            mask.putalpha(im)
            buf = io.BytesIO()
            mask.save(buf, "PNG")
            wave = buf.getvalue()

        def apply():
            if token != self.load_token:  # inzwischen ein anderes Video geladen
                return False
            try:
                self.thumbs = surface_from_png(thumbs)
                self.wave = surface_from_png(wave)
            except Exception:
                pass
            self.timeline.queue_draw()
            return False
        GLib.idle_add(apply)

    # ================= Abspielen =================
    def pause(self):
        if self.media:
            self.media.pause()

    def shutdown(self):
        retire_media(self.media, self.picture)
        self.media = None

    def reset(self):
        """Alle Schnitte verwerfen und mit demselben Video neu anfangen."""
        if not self.path:
            return
        self.pause()
        if len(self.segments) > 1 or not self.segments[0][2]:
            self.save_undo()
        self.segments = [[0.0, self.dur, True]]
        self.sel = 0
        self.seek(0)
        self.refresh_list()
        self.tab.notify("Alle Schnitte verworfen (Strg+Z holt sie zurück).")

    def pick_other(self):
        self.pause()
        dlg = Gtk.FileDialog(title="Anderes Video schneiden")
        f = Gtk.FileFilter(name="Videos")
        f.add_mime_type("video/*")
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(f)
        dlg.set_filters(store)
        if self.path:
            dlg.set_initial_folder(Gio.File.new_for_path(os.path.dirname(self.path)))

        def done(d, res):
            try:
                path = d.open_finish(res).get_path()
            except GLib.Error:
                return
            if path:
                self.tab.open_cutter(path)
        dlg.open(self.tab.win, None, done)

    def open_folder(self):
        if self.path:
            Gtk.FileLauncher(file=Gio.File.new_for_path(self.path)).open_containing_folder(self.tab.win, None, None)

    def toggle_play(self):
        if not self.media:
            return
        if self.media.get_playing():
            self.media.pause()
        else:
            if self.playhead >= self.last_kept_end() - 0.05:
                self.seek(self.next_kept(0.0))
            self.media.play()

    def seek(self, t):
        t = max(0.0, min(t, self.dur))
        self.playhead = t
        if self.media:
            self._seek_guard = True
            self.media.seek(int(t * 1e6))
        self.update_time()
        self.timeline.queue_draw()

    def step(self, frames):
        self.pause()
        self.seek(self.playhead + frames / self.fps)

    def on_time(self, m, _):
        if m is not self.media:  # altes, bereits verworfenes Video
            return
        t = m.get_timestamp() / 1e6
        if self._seek_guard:
            self._seek_guard = False
        elif m.get_playing():
            seg = self.seg_at(t)
            if seg is not None and not self.segments[seg][2]:  # entfernte Teile beim Abspielen überspringen
                nxt = self.next_kept(self.segments[seg][1])
                if nxt is None:
                    m.pause()
                    t = self.segments[seg][0]
                else:
                    t = nxt
                self.seek(t)
                return
        self.playhead = t
        self.update_time()
        self.timeline.queue_draw()
        self.keep_playhead_visible()

    def on_error(self, m, _):
        if m is self.media and m.get_error():
            self.tab.notify(f"Vorschau nicht möglich: {m.get_error().message[:80]}. Schneiden und Export gehen trotzdem.")

    def update_time(self):
        self.time_lbl.set_label(f"{fmt(self.playhead)} / {fmt(self.dur)}")

    # ================= Teile =================
    def seg_at(self, t):
        for i, (s, e, k) in enumerate(self.segments):
            if s <= t < e or (i == len(self.segments) - 1 and t >= e - 1e-6):
                return i
        return None

    def next_kept(self, t):
        for s, e, k in self.segments:
            if k and e > t + 1e-3:
                return max(s, t)
        return None

    def last_kept_end(self):
        return max((e for s, e, k in self.segments if k), default=0.0)

    def save_undo(self):
        self.undo_stack.append([list(s) for s in self.segments])
        del self.undo_stack[:-100]

    def undo(self):
        if self.undo_stack:
            self.segments = self.undo_stack.pop()
            self.sel = min(self.sel, len(self.segments) - 1)
            self.refresh_list()

    def split(self, t=None, announce=False):
        t = self.playhead if t is None else t
        i = self.seg_at(t)
        if i is None:
            if announce:
                self.tab.notify("Erst ein Video laden.")
            return None
        s, e, k = self.segments[i]
        if t - s < 0.05 or e - t < 0.05:
            if announce:  # häufigster Fall: Abspielkopf steht noch am Anfang / auf einer Schnittkante
                self.tab.notify("Abspielkopf in den Clip ziehen (nicht an den Rand/eine Kante), dann schneiden.")
            return i  # zu nah an einer Kante
        self.save_undo()
        self.segments[i:i + 1] = [[s, t, k], [t, e, k]]
        self.sel = i + 1
        self.refresh_list()
        return i + 1

    def toggle_keep(self):
        if 0 <= self.sel < len(self.segments):
            self.save_undo()
            self.segments[self.sel][2] = not self.segments[self.sel][2]
            if not any(k for _, _, k in self.segments):
                self.segments[self.sel][2] = True
                self.tab.notify("Mindestens ein Teil muss bleiben.")
                self.undo_stack.pop()
            self.refresh_list()

    def trim_start(self):
        """Alles vor dem Abspielkopf (im aktuellen Teil) entfernen."""
        i = self.seg_at(self.playhead)
        j = self.split()
        if i is not None and j is not None and j != i:
            self.segments[i][2] = False
            self.sel = j
        self.refresh_list()

    def trim_end(self):
        """Alles nach dem Abspielkopf (im aktuellen Teil) entfernen."""
        i = self.seg_at(self.playhead)
        j = self.split()
        if j is not None and j != i:
            self.segments[j][2] = False
            self.sel = i
        self.refresh_list()

    def refresh_list(self):
        while (c := self.seg_list.get_first_child()):
            self.seg_list.remove(c)
        kept = sum(e - s for s, e, k in self.segments if k)
        n_rm = sum(1 for s in self.segments if not s[2])
        self.result_lbl.set_label(f"Ergebnis: {fmt(kept)} von {fmt(self.dur)}" +
                                  (f" · {n_rm} Teil{'e' if n_rm != 1 else ''} entfernt" if n_rm else "") +
                                  ("\nTipp: Mit S am Abspielkopf schneiden, dann den Teil mit Entf entfernen." if len(self.segments) == 1 else ""))
        for i, (s, e, k) in enumerate(self.segments):
            b = Gtk.Button()
            b.add_css_class("recent"); b.add_css_class("seg-row")
            if not k:
                b.add_css_class("removed")
            if i == self.sel:
                b.add_css_class("selected")
            row = Gtk.Box(spacing=8)
            num = Gtk.Label(label=f"{i + 1}", width_chars=2)
            num.add_css_class("key")
            row.append(num)
            row.append(Gtk.Label(label=f"{fmt(s)} – {fmt(e)}", xalign=0, hexpand=True))
            st = Gtk.Label(label="󰄬" if k else "󰅖")
            st.add_css_class("sev-ok" if k else "sev-hoch")
            row.append(st)
            b.set_child(row)
            b.connect("clicked", lambda *_, i=i: (setattr(self, "sel", i), self.seek(self.segments[i][0]), self.refresh_list()))
            self.seg_list.append(b)
        self.timeline.queue_draw()

    # ================= Zeitleiste =================
    def resize_timeline(self):
        w = max(200, self.tl_scroll.get_width() - 4)
        self.timeline.set_content_width(int(w * self.zoom))
        self.timeline.queue_draw()

    def set_zoom(self, z):
        center = self.playhead
        self.zoom = z
        self.resize_timeline()
        GLib.idle_add(lambda: (self.keep_playhead_visible(force=True), False)[-1])

    def x_of(self, t):
        w = self.timeline.get_width()
        return 8 + (w - 16) * (t / self.dur if self.dur else 0)

    def t_of(self, x):
        w = self.timeline.get_width()
        return max(0.0, min(self.dur, (x - 8) / max(1, w - 16) * self.dur))

    def keep_playhead_visible(self, force=False):
        adj = self.tl_scroll.get_hadjustment()
        x = self.x_of(self.playhead)
        if force or x < adj.get_value() or x > adj.get_value() + adj.get_page_size() - 20:
            adj.set_value(max(0, x - adj.get_page_size() / 3))

    def draw_timeline(self, area, cr, w, h):
        if not self.dur:
            return
        x0, x1 = 8, w - 8
        acc = self.accent()
        # Zeitlineal
        cr.set_source_rgba(1, 1, 1, 0.55)
        cr.set_font_size(10)
        px_per_s = (x1 - x0) / self.dur
        step = next(s for s in (0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800) if s * px_per_s > 70)
        t = 0.0
        while t <= self.dur:
            x = self.x_of(t)
            cr.rectangle(x, RULER - 7, 1, 7)
            cr.fill()
            cr.move_to(x + 3, RULER - 9)
            cr.show_text(fmt(t, step < 1))
            t += step
        # Vorschaubilder
        y = RULER
        if self.thumbs:
            cr.save()
            cr.rectangle(x0, y, x1 - x0, THUMB_H)
            cr.clip()
            sx = (x1 - x0) / self.thumbs.get_width()
            sy = THUMB_H / self.thumbs.get_height()
            cr.translate(x0, y)
            cr.scale(sx, sy)
            cr.set_source_surface(self.thumbs, 0, 0)
            cr.get_source().set_filter(cairo.FILTER_GOOD)
            cr.paint()
            cr.restore()
        else:
            cr.set_source_rgba(1, 1, 1, 0.06)
            cr.rectangle(x0, y, x1 - x0, THUMB_H)
            cr.fill()
        # Tonwelle
        y2 = RULER + THUMB_H + 4
        cr.set_source_rgba(1, 1, 1, 0.05)
        cr.rectangle(x0, y2, x1 - x0, WAVE_H)
        cr.fill()
        if self.wave:
            cr.save()
            cr.rectangle(x0, y2, x1 - x0, WAVE_H)
            cr.clip()
            cr.translate(x0, y2)
            cr.scale((x1 - x0) / self.wave.get_width(), WAVE_H / self.wave.get_height())
            cr.set_source_rgba(*acc, 0.85)
            cr.mask_surface(self.wave, 0, 0)
            cr.restore()
        # Teile: entfernt = abgedunkelt + rot schraffiert, Kanten, Auswahl
        top, bot = RULER, RULER + THUMB_H + 4 + WAVE_H
        for i, (s, e, k) in enumerate(self.segments):
            xa, xb = self.x_of(s), self.x_of(e)
            if not k:
                cr.set_source_rgba(0, 0, 0, 0.62)
                cr.rectangle(xa, top, xb - xa, bot - top)
                cr.fill()
                cr.save()
                cr.rectangle(xa, top, xb - xa, bot - top)
                cr.clip()
                cr.set_source_rgba(1, 0.35, 0.35, 0.55)
                cr.set_line_width(2)
                xx = xa - (bot - top)
                while xx < xb:
                    cr.move_to(xx, bot)
                    cr.line_to(xx + (bot - top), top)
                    xx += 12
                cr.stroke()
                cr.restore()
            if i == self.sel:
                cr.set_source_rgba(*acc, 1)
                cr.set_line_width(2.5)
                cr.rectangle(xa + 1.25, top + 1.25, xb - xa - 2.5, bot - top - 2.5)
                cr.stroke()
            if i > 0:
                cr.set_source_rgba(1, 1, 1, 0.95)
                cr.rectangle(xa - 1, top - 2, 2, bot - top + 4)
                cr.fill()
        # Abspielkopf
        x = self.x_of(self.playhead)
        cr.set_source_rgba(*acc, 1)
        cr.rectangle(x - 1, 2, 2, h - 4)
        cr.fill()
        cr.move_to(x - 6, 2)
        cr.line_to(x + 6, 2)
        cr.line_to(x, 10)
        cr.close_path()
        cr.fill()

    def accent(self):
        import re
        try:
            m = re.search(r"@define-color color9 (#[0-9a-fA-F]{6})", open(os.path.expanduser("~/.cache/matugen/colors.css")).read())
            hx = m.group(1)
            return tuple(int(hx[i:i + 2], 16) / 255 for i in (1, 3, 5))
        except (OSError, AttributeError):
            return (1.0, 0.71, 0.56)

    def edge_at(self, x):
        for i in range(1, len(self.segments)):
            if abs(self.x_of(self.segments[i][0]) - x) < 7:
                return i
        return None

    def on_drag_begin(self, g, x, y):
        self.timeline.grab_focus()
        edge = self.edge_at(x)
        if edge is not None:
            self.save_undo()
            self._drag = ("edge", edge)
        else:
            self._drag = ("seek",)
            t = self.t_of(x)
            i = self.seg_at(t)
            if i is not None:
                self.sel = i
            self.pause()
            self.seek(t)
            self.refresh_list()

    def on_drag_update(self, g, dx, dy):
        if not self._drag:
            return
        ok, sx, sy = g.get_start_point()
        t = self.t_of(sx + dx)
        if self._drag[0] == "edge":
            i = self._drag[1]
            lo, hi = self.segments[i - 1][0] + 0.1, self.segments[i][1] - 0.1
            t = max(lo, min(hi, t))
            self.segments[i - 1][1] = self.segments[i][0] = t
            self.seek(t)
        else:
            self.seek(t)

    def on_motion(self, ctrl, x, y):
        self.timeline.set_cursor_from_name("col-resize" if self.edge_at(x) is not None else "pointer")

    def on_scroll(self, ctrl, dx, dy):
        if ctrl.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
            self.zoom_scale.set_value(max(1, min(20, self.zoom * (0.85 if dy > 0 else 1.18))))
            return True
        return False

    def on_key(self, ctrl, keyval, code, mods):
        k = Gdk.keyval_name(keyval) or ""
        shift = bool(mods & Gdk.ModifierType.SHIFT_MASK)
        c = bool(mods & Gdk.ModifierType.CONTROL_MASK)
        if isinstance(self.get_root().get_focus(), Gtk.Text):
            return False
        if k == "space":
            self.toggle_play()
        elif k in ("Left", "Right"):
            d = -1 if k == "Left" else 1
            self.pause()
            self.seek(self.playhead + (d if shift else d / self.fps))
        elif k == "Home":
            self.seek(0)
        elif k == "End":
            self.seek(self.dur)
        elif c and k.lower() == "z":
            self.undo()
        elif not c and k.lower() == "s":
            self.split(announce=True)
        elif k in ("Delete", "BackSpace"):
            self.toggle_keep()
        elif not c and k.lower() == "i":
            self.trim_start()
        elif not c and k.lower() == "o":
            self.trim_end()
        else:
            return False
        return True

    # ================= Export =================
    def export(self):
        if self.exporting or not self.path:
            return
        fmt_i = self.fmt_drop.get_selected()
        ext = [".mp4", ".webm", ".gif", ".mp3"][fmt_i]
        stem = os.path.splitext(os.path.basename(self.path))[0]
        folder = os.path.expanduser("~/Videos")
        os.makedirs(folder, exist_ok=True)
        dlg = Gtk.FileDialog(title="Schnitt exportieren", initial_name=f"{stem}-schnitt{ext}",
                             initial_folder=Gio.File.new_for_path(folder))

        def done(d, res):
            try:
                out = d.save_finish(res).get_path()
            except GLib.Error:
                return
            if not out.lower().endswith(ext):
                out += ext
            self.run_export(out, fmt_i)
        dlg.save(self.tab.win, None, done)

    def build_cmd(self, out, fmt_i):
        kept = [(s, e) for s, e, k in self.segments if k]
        audio = self.has_audio and self.audio_btn.get_active() and fmt_i != 2
        height = [None, 1080, 720, 480][self.size_drop.get_selected()]
        parts, labels = [], ""
        for i, (s, e) in enumerate(kept):
            if fmt_i != 3:
                parts.append(f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS[v{i}]")
                labels += f"[v{i}]"
            if audio or fmt_i == 3:
                parts.append(f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[a{i}]")
                labels += f"[a{i}]"
        n = len(kept)
        if fmt_i == 3:
            fc = ";".join(parts) + f";{labels}concat=n={n}:v=0:a=1[ao]"
            return ["-filter_complex", fc, "-map", "[ao]", "-c:a", "libmp3lame", "-q:a", "2"], sum(e - s for s, e in kept)
        fc = ";".join(parts) + f";{labels}concat=n={n}:v=1:a={1 if audio else 0}[vc]" + ("[ac]" if audio else "")
        scale = f"scale=-2:'min({height},ih)'" if height else "null"
        if fmt_i == 2:
            fc += f";[vc]fps=15,scale='min(720,iw)':-2:flags=lanczos,split[g1][g2];[g1]palettegen=stats_mode=diff[p];[g2][p]paletteuse=dither=sierra2_4a[vo]"
            return ["-filter_complex", fc, "-map", "[vo]", "-loop", "0"], sum(e - s for s, e in kept)
        fc += f";[vc]{scale},format=yuv420p[vo]"
        args = ["-filter_complex", fc, "-map", "[vo]"]
        if audio:
            args += ["-map", "[ac]"]
        if fmt_i == 0:
            args += ["-c:v", "libx264", "-crf", "20", "-preset", "medium", "-movflags", "+faststart"]
            args += ["-c:a", "aac", "-b:a", "160k"] if audio else []
        else:
            args += ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-deadline", "good", "-cpu-used", "4", "-row-mt", "1"]
            args += ["-c:a", "libopus", "-b:a", "128k"] if audio else []
        return args, sum(e - s for s, e in kept)

    def run_export(self, out, fmt_i):
        args, total = self.build_cmd(out, fmt_i)
        tmp = out + ".part" + os.path.splitext(out)[1]
        cmd = ["ffmpeg", "-y", "-v", "error", "-nostats", "-progress", "pipe:1", "-i", self.path] + args + [tmp]
        self.exporting = True
        self.export_btn.set_sensitive(False)
        self.progress.set_visible(True)
        self.progress.set_fraction(0)
        self.after.set_visible(False)

        def work():
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for line in p.stdout:
                if line.startswith("out_time_us=") and total:
                    try:
                        f = min(0.99, int(line.split("=")[1]) / 1e6 / total)
                    except ValueError:
                        continue
                    GLib.idle_add(self.progress.set_fraction, f)
            err = p.stderr.read()
            ok = p.wait() == 0
            if ok:
                os.replace(tmp, out)
            elif os.path.exists(tmp):
                os.unlink(tmp)
            GLib.idle_add(finish, ok, err)

        def finish(ok, err):
            self.exporting = False
            self.export_btn.set_sensitive(True)
            self.progress.set_visible(False)
            if not ok:
                self.tab.notify("Export fehlgeschlagen: " + (err.strip().splitlines() or ["?"])[-1][:90])
                return False
            self.tab.notify(f"Exportiert: {os.path.basename(out)}")
            while (c := self.after.get_first_child()):
                self.after.remove(c)
            self.after.append(self.button("󰈈  Prüfen", lambda: self.tab.load_path(out), "In Scourge öffnen und durchleuchten"))
            self.after.append(self.button("󰉋  Ordner", lambda: Gtk.FileLauncher(file=Gio.File.new_for_path(out)).open_containing_folder(
                self.tab.win, None, None), "Im Dateimanager zeigen"))
            self.after.set_visible(True)
            return False
        threading.Thread(target=work, daemon=True).start()
