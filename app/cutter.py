# Video-Schnitt für Scourge: eine leichte Mehrspur-Zeitleiste im Stil von DaVinci Resolve.
# Clips (Videos und Musik) liegen als Stücke auf Spuren; mit dem Verschieben-Werkzeug zieht man sie (magnetisch
# an Kanten und Abspielkopf, wählbar), über- und untereinander (Musik unter Gameplay), mit dem Schneiden-Werkzeug
# teilt man per Klick. Die Vorschau läuft über eine Zeituhr: alle Stücke unter dem Abspielkopf spielen zugleich.
# Export über ffmpeg (Bild: oberste Spur gewinnt, Ton: alle Spuren gemischt). Zwischenstände werden gespeichert.
import hashlib, io, json, os, subprocess, threading, time

import cairo
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

import chrome as ch
import player

CUTTER_CSS = """
.timeline-frame { background-color: alpha(black, 0.25); border-radius: 12px; }
.seg-row { border-radius: 9px; padding: 3px 6px; }
.seg-row.selected { background-color: alpha(@color9, 0.16); }
.big-time { font-size: 15px; font-weight: 800; font-feature-settings: "tnum"; }
.tl-handle { min-height: 12px; }
.tl-handle > separator { min-height: 3px; min-width: 60px; border-radius: 2px; background-color: alpha(@foreground, 0.3); }
.tl-handle:hover > separator { background-color: @color9; }
.audio-hint { font-size: 18px; font-weight: 800; color: alpha(@foreground, 0.7); }
progressbar.pb trough { min-height: 6px; border-radius: 4px; background-color: alpha(@foreground, 0.15); }
progressbar.pb progress { min-height: 6px; border-radius: 4px; background-color: @color9; }
"""
RULER = 22
GAP = 3                    # Abstand zwischen Spuren
MAX_LANES = 6
EDGE = 7                   # so nah (px) an einer Kante = Kante greifen
SNAP_PX = 9                # magnetisch ab so vielen Pixeln
MIN_LEN = 0.1
DRAFTS = os.path.expanduser("~/.config/scourge/schnitt")


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
        self.sources = []       # [{path, dur, fps, has_video, has_audio, w, h, thumbs, wave, media, play}]
        self.items = []         # [{id, src, s, e, pos, lane}]  – Stücke auf der Zeitleiste
        self.path = None
        self.sel = None         # ausgewähltes Stück (dict) oder None
        self.undo_stack = []
        self.dirty = False      # Änderungen seit dem letzten Zwischenspeichern
        self.draft_id = None
        self.dur = 0.0          # Länge = Ende des letzten Stücks
        self.span = 10.0        # sichtbare Zeitspanne der Zeitleiste
        self.fps = 30.0
        self.has_audio = self.has_video = False
        self.zoom = 1.0
        self.playhead = 0.0
        self.playing = False
        self.loop = True
        self.magnet = True
        self.ripple = True
        self.tool = "drag"
        self.cur_src = None     # Quelle, deren Player gerade im Bild steht
        self._driving = {}      # Quelle → Stück-id, das ihren Player gerade steuert
        self._tick_id = None
        self._t0 = self._T0 = 0.0
        self._next_id = 1
        self._drag = None
        self._snap_x = None
        self._hover_x = None
        self.exporting = False
        self.load_token = 0
        self._tl_pending = None
        self.tl_h = 180
        try:
            import settings_menu
            self.tl_h = int(max(100, min(520, float(settings_menu.get("timeline_h", 180)))))
        except Exception:
            pass
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
        self.audio_hint = Gtk.Label(label="", visible=False, wrap=True, max_width_chars=40)
        self.audio_hint.add_css_class("audio-hint")
        ov = Gtk.Overlay(child=self.picture, hexpand=True, vexpand=True)
        ov.add_overlay(self.audio_hint)
        frame.append(ov)
        body.append(frame)

        side_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, hexpand=False)
        side_scroll.set_size_request(300, -1)
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        side_scroll.set_child(side)
        body.append(side_scroll)

        c = self.card(side, "Clips")
        self.clip_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        c.append(self.clip_list)
        c.append(self.button("󰐕  Video oder Musik hinzufügen", self.pick_add,
                             "Weitere Dateien auf die Zeitleiste legen (auch per Ziehen ins Fenster)", small=False))
        self.draft_card = self.card(side, "Zwischenstand")
        self.draft_lbl = Gtk.Label(xalign=0, wrap=True)
        self.draft_lbl.add_css_class("note")
        self.draft_card.append(self.draft_lbl)
        self.draft_btn = self.button("󰦛  Zwischenstand laden", self.load_draft, "Letzten gespeicherten Schnitt wiederherstellen")
        self.draft_card.append(self.draft_btn)
        self.draft_card.set_visible(False)

        c = self.card(side, "Zeitleiste")
        self.result_lbl = Gtk.Label(xalign=0, wrap=True)
        self.result_lbl.add_css_class("note")
        c.append(self.result_lbl)
        self.seg_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        c.append(self.seg_list)
        row = Gtk.Box(spacing=6, homogeneous=True)
        row.append(self.button("󰑓  Von vorne", self.reset, "Alle Schnitte verwerfen, die Clips bleiben"))
        row.append(self.button("󰈔  Anderes Video", self.pick_other, "Dieses Projekt verwerfen und ein anderes öffnen"))
        c.append(row)
        row = Gtk.Box(spacing=6, homogeneous=True)
        row.append(self.button("󰉋  Ordner öffnen", self.open_folder, "Ordner des Videos im Dateimanager zeigen"))
        row.append(self.button("󰆓  Zwischenspeichern", self.save_draft_click, "Schnitt merken, um später weiterzumachen"))
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
        self.audio_btn = Gtk.ToggleButton(label="󰕾  Ton behalten", active=True, focus_on_click=False)
        self.audio_btn.add_css_class("keep-toggle")
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
                            max_children_per_line=24, margin_start=16, margin_end=16, homogeneous=False)
        self.play_btn = self.button("󰐊", self.toggle_play, "Abspielen/Pause (Leertaste)")
        self.time_lbl = Gtk.Label(label="0:00.0 / 0:00.0")
        self.time_lbl.add_css_class("big-time")
        self.tool_btns = {}
        group = None
        for key, label, tip in (("drag", "󰇀  Verschieben", "Stücke ziehen (auch auf andere Spuren), Kanten ziehen kürzt (V)"),
                                ("cut", "󰆐  Schneiden", "Klick auf ein Stück teilt es an der Stelle (C)")):
            b = Gtk.ToggleButton(label=label, tooltip_text=tip, focus_on_click=False, valign=Gtk.Align.CENTER)
            b.add_css_class("pb"); b.add_css_class("small")
            if group:
                b.set_group(group)
            group = group or b
            b.connect("toggled", lambda btn, key=key: btn.get_active() and self.set_tool(key))
            self.tool_btns[key] = b
        self.tool_btns["drag"].set_active(True)
        self.magnet_btn = self.toggle("Magnetisch", True, "Stücke rasten an Kanten und am Abspielkopf ein",
                                      lambda b: setattr(self, "magnet", b.get_active()))
        self.ripple_btn = self.toggle("Lücke schließen", True, "Beim Entfernen rücken die folgenden Stücke der Spur nach",
                                      lambda b: setattr(self, "ripple", b.get_active()))
        items = [self.button("󰒮", lambda: self.seek(0), "Zum Anfang (Pos1)"),
                 self.button("󰒫", lambda: self.step(-1), "Ein Bild zurück (←, mit Umschalt 1 s)"),
                 self.play_btn,
                 self.button("󰒬", lambda: self.step(1), "Ein Bild vor (→, mit Umschalt 1 s)"),
                 self.time_lbl,
                 self.tool_btns["drag"], self.tool_btns["cut"], self.magnet_btn,
                 self.button("󰆴  Entfernen", self.delete_sel, "Markiertes Stück entfernen (Entf)"),
                 self.ripple_btn,
                 self.button("󰘏  Anfang hier", self.trim_start, "Alles vor dem Abspielkopf im Stück entfernen (I)"),
                 self.button("󰘖  Ende hier", self.trim_end, "Alles nach dem Abspielkopf im Stück entfernen (O)"),
                 self.button("󰕌", self.undo, "Rückgängig (Strg+Z)")]
        self.loop_btn = self.toggle("󰑖", True, "Wiederholen: nach dem Ende wieder von vorn",
                                    lambda b: setattr(self, "loop", b.get_active()))
        items.append(self.loop_btn)
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

        handle = Gtk.Box(halign=Gtk.Align.FILL,
                         tooltip_text="Zeitleiste höher/niedriger ziehen (Doppelklick: zurücksetzen)")
        handle.add_css_class("tl-handle")
        grip = Gtk.Separator(halign=Gtk.Align.CENTER, hexpand=True, valign=Gtk.Align.CENTER)
        handle.append(grip)
        handle.set_cursor_from_name("row-resize")
        hd = Gtk.GestureDrag()
        hd.connect("drag-begin", self._resize_begin)
        hd.connect("drag-update", lambda g, dx, dy: self._resize_to(self._tl0 - dy))
        hd.connect("drag-end", self._resize_end)
        handle.add_controller(hd)
        hc = Gtk.GestureClick()
        hc.connect("pressed", lambda g, n, *_: n == 2 and (self._resize_to(180, now=True), self._resize_end()))
        handle.add_controller(hc)
        self.append(handle)
        tl_frame = Gtk.Box(margin_start=16, margin_end=16, margin_bottom=16)
        tl_frame.add_css_class("timeline-frame")
        self.tl_scroll = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.AUTOMATIC, hexpand=True)
        self.tl_scroll.set_size_request(-1, self.tl_h)
        self.timeline = Gtk.DrawingArea(content_height=self.tl_h - 14, focusable=True)
        self.timeline.set_draw_func(self.draw_timeline)
        self.tl_scroll.set_child(self.timeline)
        self.tl_scroll.get_hadjustment().connect("changed", lambda *_: self.resize_timeline())
        tl_frame.append(self.tl_scroll)
        self.append(tl_frame)

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag_update)
        drag.connect("drag-end", self.on_drag_end)
        self.timeline.add_controller(drag)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self.on_motion)
        motion.connect("leave", lambda *_: (setattr(self, "_hover_x", None), self.timeline.queue_draw()))
        self.timeline.add_controller(motion)
        zoom = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        zoom.connect("scroll", self.on_scroll)
        self.timeline.add_controller(zoom)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)       # Leertaste soll immer abspielen/stoppen
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self.on_drop)
        self.add_controller(drop)

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

    def toggle(self, label, active, tip, cb):
        b = Gtk.ToggleButton(label=label, active=active, valign=Gtk.Align.CENTER, focus_on_click=False, tooltip_text=tip)
        b.add_css_class("pb"); b.add_css_class("small")
        b.connect("toggled", cb)
        return b

    # ================= Laden / Quellen =================
    def probe(self, path):
        """ffprobe → Quelle (Dauer, Bildrate, Ton/Bild vorhanden, Größe)."""
        out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                             capture_output=True, text=True).stdout
        d = json.loads(out or "{}")
        streams = d.get("streams", [])
        v = next((s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)   # Titelbild einer MP3 ist kein Video
        dur = float(d.get("format", {}).get("duration") or (v or {}).get("duration") or 0)
        num, _, den = (v or {}).get("avg_frame_rate", "30/1").partition("/")
        fps = float(num) / float(den) if float(den or 0) and float(num or 0) else 30.0
        return {"path": path, "dur": dur, "fps": fps, "has_video": v is not None,
                "has_audio": any(s.get("codec_type") == "audio" for s in streams),
                "w": int((v or {}).get("width") or 0), "h": int((v or {}).get("height") or 0),
                "thumbs": None, "wave": None, "media": None, "play": None}

    def load(self, path):
        """Neues Projekt mit einer Datei (Video oder Musik)."""
        if self.exporting:
            self.tab.notify("Bitte warten, bis der Export fertig ist.")
            return
        self.stop()
        self.shutdown()
        self.load_token += 1
        self.sources, self.items, self.sel, self.undo_stack = [], [], None, []
        self.playhead, self.dirty = 0.0, False
        self.add_clips([path], fresh=True)

    def add_clips(self, paths, fresh=False):
        """Weitere Videos/Musik auf die Zeitleiste legen: Videos hinten auf Spur 1, Musik auf eine eigene Spur."""
        if self.exporting:
            self.tab.notify("Bitte warten, bis der Export fertig ist.")
            return
        added = []
        for p in paths:
            if player.kind(p) not in ("video", "audio"):
                self.tab.notify(f"{os.path.basename(p)}: kein Video und keine Musik.")
                continue
            c = self.probe(p)
            if c["dur"] <= 0:
                self.tab.notify(f"{os.path.basename(p)} ließ sich nicht lesen.")
                continue
            added.append(c)
        if not added:
            return
        if not fresh:
            self.save_undo()
        for c in added:
            self.sources.append(c)
            si = len(self.sources) - 1
            if c["has_video"]:
                c["play"] = c["path"]
                lane, pos = 0, self.lane_end(0)
            else:                                           # Musik: erst in Opus umwandeln (GStreamer-MP3-Absturz)
                player.playable(c["path"], "audio", lambda out, c=c: self._playable(c, out))
                lane, pos = self.free_lane_for_audio(), 0.0
            self.new_item(si, 0.0, c["dur"], pos, lane)
        if fresh:
            self.recalc()                     # ein frisch geöffnetes Video ist noch keine Änderung
        else:
            self.mark_dirty()
        self.afterload(fresh)
        token = self.load_token
        for c in added:
            threading.Thread(target=self.make_strips, args=(c, token), daemon=True).start()

    def new_item(self, src, s, e, pos, lane):
        it = {"id": self._next_id, "src": src, "s": s, "e": e, "pos": pos, "lane": lane}
        self._next_id += 1
        self.items.append(it)
        return it

    def lane_end(self, lane):
        return max((i["pos"] + i["e"] - i["s"] for i in self.items if i["lane"] == lane), default=0.0)

    def free_lane_for_audio(self):
        used = {i["lane"] for i in self.items if self.sources[i["src"]]["has_video"]}
        for lane in range(1, MAX_LANES):
            if lane not in used and not any(i["lane"] == lane for i in self.items):
                return lane
        return 1

    def _playable(self, c, out):
        c["play"] = out
        if c in self.sources:
            self.sync_players(self.playhead, force=True)
        return False

    def afterload(self, fresh):
        self.fps = next((c["fps"] for c in self.sources if c["has_video"]), 30.0)
        self.has_audio = any(c["has_audio"] for c in self.sources)
        self.has_video = any(c["has_video"] for c in self.sources)
        self.path = self.sources[0]["path"]
        extra = len(self.sources) - 1
        self.title = f"Schnitt: {os.path.basename(self.path)}" + (f" + {extra}" if extra else "")
        c0 = self.sources[0]
        self.recalc()
        self.subtitle = (f"{fmt(self.dur, False)} · " + (f"{c0['w']}×{c0['h']} · {self.fps:.0f} fps" if self.has_video
                                                          else "nur Ton") + (f" · {len(self.sources)} Clips" if extra else ""))
        self.audio_btn.set_sensitive(self.has_audio)
        self.after.set_visible(False)
        self.sync_export()
        if fresh:
            self.zoom_scale.set_value(1)
            self.seek(0)
            self.find_drafts()
        self.refresh_list()
        self.refresh_clips()
        self.resize_timeline()
        self.tab.set_title(self.title, self.subtitle)
        GLib.timeout_add(300, lambda: (self.timeline.grab_focus(), False)[-1])

    def sync_export(self):
        """Nur Musik im Projekt: es gibt nur Ton-Export (MP3)."""
        if not self.has_video:
            self.fmt_drop.set_selected(3)
        self.fmt_drop.set_sensitive(self.has_video)
        self.size_drop.set_sensitive(self.has_video)

    def make_strips(self, c, token):
        """Vorschaubilder und Tonwelle für die Zeitleiste (im Hintergrund), je Quelle."""
        path, dur = c["path"], c["dur"]
        n = max(4, min(80, int(dur / 2) + 4))
        thumbs = b""
        if c["has_video"]:
            thumbs = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf",
                                     f"fps={n / max(dur, 0.1):.5f},scale=-2:96,tile={n}x1", "-frames:v", "1",
                                     "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True).stdout
        wave = b""
        if c["has_audio"]:
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
            if token != self.load_token:  # inzwischen ein anderes Projekt geladen
                return False
            try:
                c["thumbs"] = surface_from_png(thumbs)
                c["wave"] = surface_from_png(wave)
            except Exception:
                pass
            self.timeline.queue_draw()
            return False
        GLib.idle_add(apply)

    # ================= Clips-Liste =================
    def refresh_clips(self):
        while (c := self.clip_list.get_first_child()):
            self.clip_list.remove(c)
        for i, c in enumerate(self.sources):
            row = Gtk.Box(spacing=4)
            num = Gtk.Label(label=str(i + 1), width_chars=2)
            num.add_css_class("key")
            row.append(num)
            row.append(Gtk.Label(label=os.path.basename(c["path"]), xalign=0, hexpand=True, ellipsize=3,
                                 tooltip_text=c["path"]))
            row.append(Gtk.Label(label=("󰎈 " if not c["has_video"] else "") + fmt(c["dur"], False)))
            if len(self.sources) > 1:
                row.append(self.button("󰅖", lambda i=i: self.remove_source(i), "Clip samt allen Stücken entfernen"))
            self.clip_list.append(row)

    def remove_source(self, si):
        if len(self.sources) < 2:
            return self.tab.notify("Ein Clip muss bleiben.")
        self.stop()
        self.save_undo()
        gone = self.sources[si]
        retire_media(gone["media"], self.picture)
        self.items = [i for i in self.items if i["src"] != si]
        for i in self.items:
            if i["src"] > si:
                i["src"] -= 1
        del self.sources[si]
        self.undo_stack = []                    # Quellen-Nummern haben sich verschoben
        self.sel = None
        self.cur_src, self._driving = None, {}
        self.mark_dirty()
        self.afterload(False)
        self.seek(min(self.playhead, self.dur))

    def pick_add(self):
        self.pause()
        dlg = Gtk.FileDialog(title="Videos oder Musik hinzufügen")
        f = Gtk.FileFilter(name="Videos und Musik")
        f.add_mime_type("video/*")
        f.add_mime_type("audio/*")
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(f)
        dlg.set_filters(store)
        if self.path:
            dlg.set_initial_folder(Gio.File.new_for_path(os.path.dirname(self.path)))

        def done(d, res):
            try:
                files = d.open_multiple_finish(res)
            except GLib.Error:
                return
            paths = [files.get_item(i).get_path() for i in range(files.get_n_items())]
            self.add_clips([p for p in paths if p])
        dlg.open_multiple(self.tab.win, None, done)

    def on_drop(self, _t, files, *_):
        paths = [f.get_path() for f in files.get_files() if f.get_path()]
        if not paths:
            return False
        if self.sources:
            self.add_clips(paths)
        else:
            self.load(paths[0])
            if len(paths) > 1:
                self.add_clips(paths[1:])
        return True

    def pick_other(self):
        self.pause()
        dlg = Gtk.FileDialog(title="Anderes Video schneiden")
        f = Gtk.FileFilter(name="Videos und Musik")
        f.add_mime_type("video/*")
        f.add_mime_type("audio/*")
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

    # ================= Zwischenstände =================
    def _draft_key(self):
        paths = sorted(c["path"] for c in self.sources)
        return hashlib.sha1("\n".join(paths).encode()).hexdigest()[:12]

    def project_dict(self):
        return {"sources": [c["path"] for c in self.sources],
                "items": [{k: i[k] for k in ("src", "s", "e", "pos", "lane")} for i in self.items],
                "playhead": self.playhead, "saved": time.time()}

    def save_draft(self, quiet=False):
        if not self.sources:
            return
        os.makedirs(DRAFTS, exist_ok=True)
        data = self.project_dict()
        tmp = os.path.join(DRAFTS, f".{self._draft_key()}.tmp")
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, os.path.join(DRAFTS, self._draft_key() + ".json"))
        # zusätzlich unter dem Pfad der ersten Datei auffindbar machen (Dateien neu öffnen → Hinweis)
        idx = os.path.join(DRAFTS, "index.json")
        try:
            index = json.load(open(idx))
        except (OSError, ValueError):
            index = {}
        for c in self.sources:
            index[c["path"]] = self._draft_key()
        with open(idx, "w") as f:
            json.dump(index, f)
        self.dirty = False
        if not quiet:
            self.tab.notify("Zwischenstand gespeichert.")

    def save_draft_click(self):
        self.save_draft()
        self.find_drafts()

    def find_drafts(self):
        """Gibt es zu dieser Datei einen gespeicherten Schnitt? → Karte „Zwischenstand“."""
        self.draft_file = None
        self.draft_card.set_visible(False)
        try:
            index = json.load(open(os.path.join(DRAFTS, "index.json")))
            key = index.get(self.sources[0]["path"]) if self.sources else None
            f = os.path.join(DRAFTS, f"{key}.json") if key else None
            if f and os.path.exists(f) and not (self.dirty is False and key == self._draft_key() and
                                                json.load(open(f)).get("items") == self.project_dict()["items"]):
                data = json.load(open(f))
                n = len(data.get("items", []))
                self.draft_file = f
                self.draft_lbl.set_label(f"Gespeichert {time.strftime('%d.%m. %H:%M', time.localtime(data.get('saved', 0)))}"
                                         f" · {len(data.get('sources', []))} Clips · {n} Stücke")
                self.draft_card.set_visible(True)
        except (OSError, ValueError):
            pass

    def load_draft(self):
        f = getattr(self, "draft_file", None)
        if not f:
            return
        try:
            data = json.load(open(f))
        except (OSError, ValueError):
            return self.tab.notify("Zwischenstand ließ sich nicht lesen.")
        paths = [p for p in data.get("sources", []) if os.path.exists(p)]
        if len(paths) != len(data.get("sources", [])):
            return self.tab.notify("Nicht alle Dateien des Zwischenstands sind noch da.")
        self.stop()
        self.shutdown()
        self.load_token += 1
        self.sources, self.items, self.sel, self.undo_stack = [], [], None, []
        for p in paths:
            c = self.probe(p)
            if c["has_video"]:
                c["play"] = c["path"]
            else:
                player.playable(p, "audio", lambda out, c=c: self._playable(c, out))
            self.sources.append(c)
        for i in data.get("items", []):
            self.new_item(i["src"], i["s"], i["e"], i["pos"], i["lane"])
        self.dirty = False
        self.afterload(False)
        token = self.load_token
        for c in self.sources:
            threading.Thread(target=self.make_strips, args=(c, token), daemon=True).start()
        self.seek(float(data.get("playhead", 0)))
        self.draft_card.set_visible(False)
        self.tab.notify("Zwischenstand geladen.")

    def needs_ask(self):
        return bool(self.items) and self.dirty and not self.exporting

    def ask_leave(self, proceed):
        """Beim Verlassen: Zwischenspeichern, verwerfen oder bleiben."""
        self.pause()
        dlg = Gtk.AlertDialog(message="Schnitt zwischenspeichern?",
                              detail="Dann kannst du später genau hier weitermachen (Karte „Zwischenstand“, sobald du "
                                     "dieselbe Datei wieder öffnest).",
                              buttons=["Abbrechen", "Verwerfen", "Zwischenspeichern"], cancel_button=0, default_button=2)

        def done(d, res):
            try:
                i = d.choose_finish(res)
            except GLib.Error:
                return
            if i == 2:
                self.save_draft(quiet=True)
            if i in (1, 2):
                self.dirty = False
                proceed()
        dlg.choose(self.tab.win, None, done)

    # ================= Zeit / Spuren =================
    def recalc(self):
        self.dur = max((i["pos"] + i["e"] - i["s"] for i in self.items), default=0.0)
        self.span = max(10.0, self.dur * 1.12)

    def lane_count(self):
        top = max((i["lane"] for i in self.items), default=0)
        return min(MAX_LANES, max(2, top + 2))

    def lane_h(self):
        n = self.lane_count()
        return max(26.0, min(140.0, (self.tl_h - 14 - RULER - 8) / n - GAP))

    def content_h(self):
        return int(RULER + self.lane_count() * (self.lane_h() + GAP) + 8)

    def lane_y(self, lane):
        return RULER + lane * (self.lane_h() + GAP)

    def lane_at(self, y):
        return int(max(0, min(self.lane_count() - 1, (y - RULER) // (self.lane_h() + GAP))))

    def length(self, it):
        return it["e"] - it["s"]

    def item_at(self, t, lane=None):
        """Stück an der Zeit t (auf der Spur lane, sonst das oberste)."""
        hits = [i for i in self.items if i["pos"] <= t < i["pos"] + self.length(i)
                and (lane is None or i["lane"] == lane)]
        return min(hits, key=lambda i: i["lane"]) if hits else None

    def mark_dirty(self, undoable=True):
        self.dirty = True
        self.recalc()

    # ================= Abspielen =================
    def media_of(self, si):
        c = self.sources[si]
        if c["media"] is None and c["play"]:
            m = Gtk.MediaFile.new_for_filename(c["play"])
            m.connect("notify::error", self.on_error)
            c["media"] = m
        return c["media"]

    def on_error(self, m, _):
        if m.get_error():
            self.tab.notify(f"Vorschau nicht möglich: {m.get_error().message[:80]}. Schneiden und Export gehen trotzdem.")

    def pause(self):
        self.stop()

    def stop(self):
        """Abspielen anhalten (Zeituhr aus, alle Player pausieren)."""
        if self._tick_id is not None:
            self.timeline.remove_tick_callback(self._tick_id)
            self._tick_id = None
        self.playing = False
        for si in list(self._driving):
            m = self.sources[si]["media"] if si < len(self.sources) else None
            if m:
                m.pause()
        self._driving = {}
        self.play_btn.set_label("󰐊")

    def toggle_play(self):
        if self.playing:
            return self.stop()
        if not self.items:
            return
        if self.playhead >= self.dur - 0.05:
            self.seek(0)
        self.playing = True
        self._T0 = self.playhead
        self._t0 = time.monotonic()
        self.play_btn.set_label("󰏤")
        self.sync_players(self.playhead, force=True)
        self._tick_id = self.timeline.add_tick_callback(self._tick)

    def _tick(self, widget, clock):
        T = self._T0 + (time.monotonic() - self._t0)
        if T >= self.dur:
            if self.loop and self.dur > 0:
                T = 0.0
                self._T0, self._t0 = 0.0, time.monotonic()
                self._driving_reset()
            else:
                self.playhead = self.dur
                self._tick_id = None                 # wir entfernen uns gleich selbst
                self.stop()
                self.update_time()
                self.timeline.queue_draw()
                return GLib.SOURCE_REMOVE
        self.playhead = T
        self.sync_players(T)
        self.update_time()
        self.timeline.queue_draw()
        self.keep_playhead_visible()
        return GLib.SOURCE_CONTINUE

    def _driving_reset(self):
        for si in list(self._driving):
            m = self.sources[si]["media"]
            if m:
                m.pause()
        self._driving = {}

    def sync_players(self, T, force=False):
        """Alle Stücke unter dem Abspielkopf: ihre Player an die richtige Stelle, Bild vom obersten Videostück."""
        active = [i for i in self.items if i["pos"] <= T < i["pos"] + self.length(i)]
        active.sort(key=lambda i: i["lane"])
        want = {}
        for it in active:
            want.setdefault(it["src"], it)
        for si in list(self._driving):
            if si not in want or want[si]["id"] != self._driving[si]:
                m = self.sources[si]["media"]
                if m:
                    m.pause()
                del self._driving[si]
        for si, it in want.items():
            m = self.media_of(si)
            if m is None:
                continue
            local = it["s"] + (T - it["pos"])
            if self.playing:
                if self._driving.get(si) != it["id"] or force:
                    m.seek(int(local * 1e6))
                    m.play()
                    self._driving[si] = it["id"]
                elif abs(m.get_timestamp() / 1e6 - local) > 0.45:
                    m.seek(int(local * 1e6))
            elif force or self.cur_src != si:
                m.seek(int(local * 1e6))
        vit = next((i for i in active if self.sources[i["src"]]["has_video"]), None)
        vsi = vit["src"] if vit else None
        if vsi != self.cur_src or force:
            self.cur_src = vsi
            m = self.media_of(vsi) if vsi is not None else None
            if vsi is not None and not self.playing and m:
                m.seek(int((vit["s"] + T - vit["pos"]) * 1e6))
            if not getattr(self, "_resizing", False):
                self.picture.set_paintable(m)
            hint = [i for i in active if not self.sources[i["src"]]["has_video"]]
            self.audio_hint.set_label("󰎈  " + os.path.basename(self.sources[hint[0]["src"]]["path"]) if hint and vsi is None else "")
            self.audio_hint.set_visible(bool(hint) and vsi is None)

    def seek(self, t):
        if not self.sources:
            return
        t = max(0.0, min(t, max(self.dur, 0.0)))
        self.playhead = t
        if self.playing:
            self._T0, self._t0 = t, time.monotonic()
            self.sync_players(t, force=True)
        else:
            self.sync_players(t, force=True)
        self.update_time()
        self.timeline.queue_draw()

    def step(self, frames):
        self.stop()
        self.seek(self.playhead + frames / self.fps)

    def update_time(self):
        self.time_lbl.set_label(f"{fmt(self.playhead)} / {fmt(self.dur)}")

    def shutdown(self):
        self.stop()
        for c in getattr(self, "sources", []):
            retire_media(c["media"], self.picture)
            c["media"] = None
        self.cur_src = None
        self._driving = {}
        if self.items and self.dirty:                       # nichts verlieren: still zwischenspeichern
            try:
                self.save_draft(quiet=True)
            except OSError:
                pass

    # ================= Bearbeiten =================
    def save_undo(self):
        self.undo_stack.append([dict(i) for i in self.items])
        del self.undo_stack[:-100]

    def undo(self):
        if self.undo_stack:
            self.stop()
            self.items = self.undo_stack.pop()
            self.sel = None
            self.mark_dirty()
            self.refresh_list()
            self.update_time()

    def reset(self):
        """Alle Schnitte verwerfen: jede Quelle wieder ungeschnitten (Videos hintereinander, Musik darunter)."""
        if not self.sources:
            return
        self.stop()
        self.save_undo()
        self.items, self.sel = [], None
        for si, c in enumerate(self.sources):
            if c["has_video"]:
                self.new_item(si, 0.0, c["dur"], self.lane_end(0), 0)
            else:
                self.new_item(si, 0.0, c["dur"], 0.0, self.free_lane_for_audio())
        self.mark_dirty()
        self.seek(0)
        self.refresh_list()
        self.tab.notify("Alle Schnitte verworfen (Strg+Z holt sie zurück).")

    def select(self, it):
        self.sel = it
        self.refresh_list()
        self.timeline.queue_draw()

    def split_item(self, it, T):
        """it an der Zeit T teilen; liefert das neue rechte Stück (oder None, wenn zu nah an einer Kante)."""
        if not (it["pos"] + 0.05 < T < it["pos"] + self.length(it) - 0.05):
            return None
        local = it["s"] + (T - it["pos"])
        new = dict(it, id=self._next_id, s=local, pos=T)
        self._next_id += 1
        it["e"] = local
        self.items.insert(self.items.index(it) + 1, new)
        return new

    def split(self, t=None, announce=False):
        """S / Knopf: das markierte Stück am Abspielkopf teilen, sonst alle Stücke unter dem Abspielkopf."""
        t = self.playhead if t is None else t
        if not self.items:
            if announce:
                self.tab.notify("Erst ein Video laden.")
            return None
        targets = [self.sel] if self.sel and self.sel["pos"] < t < self.sel["pos"] + self.length(self.sel) else \
            [i for i in self.items if i["pos"] < t < i["pos"] + self.length(i)]
        targets = [i for i in targets if i["pos"] + 0.05 < t < i["pos"] + self.length(i) - 0.05]
        if not targets:
            if announce:
                self.tab.notify("Abspielkopf in ein Stück ziehen (nicht an eine Kante), dann schneiden.")
            return None
        self.save_undo()
        new = None
        for it in targets:
            new = self.split_item(it, t) or new
        self.sel = new
        self.mark_dirty()
        self.refresh_list()
        self.timeline.queue_draw()
        return new

    def delete_sel(self):
        it = self.sel
        if it is None:
            return self.tab.notify("Erst ein Stück anklicken, dann entfernen.")
        if len(self.items) == 1:
            return self.tab.notify("Mindestens ein Stück muss bleiben.")
        self.stop()
        self.save_undo()
        ln = self.length(it)
        self.items.remove(it)
        if self.ripple:
            for o in self.items:
                if o["lane"] == it["lane"] and o["pos"] >= it["pos"] + ln - 1e-6:
                    o["pos"] = max(0.0, o["pos"] - ln)
        self.sel = None
        self.mark_dirty()
        self.refresh_list()
        self.seek(min(self.playhead, self.dur))

    def _under_playhead(self):
        if self.sel and self.sel["pos"] < self.playhead < self.sel["pos"] + self.length(self.sel):
            return self.sel
        return self.item_at(self.playhead)

    def trim_start(self):
        """Alles vor dem Abspielkopf (im Stück) entfernen."""
        it = self._under_playhead()
        if not it:
            return
        cut = self.playhead - it["pos"]
        if cut < 0.05 or self.length(it) - cut < MIN_LEN:
            return
        self.save_undo()
        it["s"] += cut
        it["pos"] = self.playhead
        self.mark_dirty()
        self.refresh_list()
        self.timeline.queue_draw()

    def trim_end(self):
        """Alles nach dem Abspielkopf (im Stück) entfernen."""
        it = self._under_playhead()
        if not it:
            return
        keep = self.playhead - it["pos"]
        if keep < MIN_LEN or self.length(it) - keep < 0.05:
            return
        self.save_undo()
        it["e"] = it["s"] + keep
        self.mark_dirty()
        self.refresh_list()
        self.timeline.queue_draw()

    def refresh_list(self):
        while (c := self.seg_list.get_first_child()):
            self.seg_list.remove(c)
        lanes = self.lane_count()
        self.result_lbl.set_label(f"Länge: {fmt(self.dur)} · {len(self.items)} Stück{'e' if len(self.items) != 1 else ''}"
                                  + (" · Tipp: Stücke auf andere Spuren ziehen, um sie zu überlagern (Musik unter Video)"
                                     if len(self.items) < 3 and lanes < 3 else ""))
        for n, it in enumerate(sorted(self.items, key=lambda i: (i["lane"], i["pos"]))):
            b = Gtk.Button()
            b.add_css_class("recent"); b.add_css_class("seg-row")
            if it is self.sel:
                b.add_css_class("selected")
            row = Gtk.Box(spacing=8)
            tag = Gtk.Label(label=f"{it['lane'] + 1}", width_chars=2)
            tag.add_css_class("key")
            row.append(tag)
            row.append(Gtk.Label(label=f"{fmt(it['pos'])} – {fmt(it['pos'] + self.length(it))}", xalign=0, hexpand=True))
            nm = Gtk.Label(label=os.path.basename(self.sources[it["src"]]["path"]), ellipsize=3, max_width_chars=10)
            nm.add_css_class("key")
            row.append(nm)
            b.set_child(row)
            b.connect("clicked", lambda *_, it=it: (self.select(it), self.seek(it["pos"])))
            self.seg_list.append(b)
        self.timeline.queue_draw()

    def set_tool(self, key):
        self.tool = key
        if hasattr(self, "timeline"):
            self.timeline.set_cursor_from_name("crosshair" if key == "cut" else "default")
            self.timeline.queue_draw()

    # ================= Zeitleiste =================
    def resize_timeline(self):
        w = max(200, self.tl_scroll.get_width() - 4)
        self.timeline.set_content_width(int(w * self.zoom))
        self.timeline.set_content_height(max(self.tl_h - 14, self.content_h()))
        self.timeline.queue_draw()

    def set_zoom(self, z):
        self.zoom = z
        self.resize_timeline()
        GLib.idle_add(lambda: (self.keep_playhead_visible(force=True), False)[-1])

    def x_of(self, t):
        w = self.timeline.get_width()
        return 8 + (w - 16) * (t / self.span if self.span else 0)

    def t_of(self, x):
        w = self.timeline.get_width()
        return max(0.0, x - 8) / max(1, w - 16) * self.span

    def keep_playhead_visible(self, force=False):
        adj = self.tl_scroll.get_hadjustment()
        x = self.x_of(self.playhead)
        if force or x < adj.get_value() or x > adj.get_value() + adj.get_page_size() - 20:
            adj.set_value(max(0, x - adj.get_page_size() / 3))

    def accent(self):
        import re
        try:
            m = re.search(r"@define-color color9 (#[0-9a-fA-F]{6})", open(os.path.expanduser("~/.cache/matugen/colors.css")).read())
            hx = m.group(1)
            return tuple(int(hx[i:i + 2], 16) / 255 for i in (1, 3, 5))
        except (OSError, AttributeError):
            return (1.0, 0.71, 0.56)

    def draw_timeline(self, area, cr, w, h):
        acc = self.accent()
        x0, x1 = 8, w - 8
        # Zeitlineal
        cr.set_source_rgba(1, 1, 1, 0.55)
        cr.set_font_size(10)
        px_per_s = (x1 - x0) / self.span
        step = next(s for s in (0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600) if s * px_per_s > 70)
        t = 0.0
        while t <= self.span:
            x = self.x_of(t)
            cr.rectangle(x, RULER - 7, 1, 7)
            cr.fill()
            cr.move_to(x + 3, RULER - 9)
            cr.show_text(fmt(t, step < 1))
            t += step
        lh = self.lane_h()
        for lane in range(self.lane_count()):          # Spuren als leichte Streifen
            y = self.lane_y(lane)
            cr.set_source_rgba(1, 1, 1, 0.045 if lane % 2 == 0 else 0.025)
            cr.rectangle(x0, y, x1 - x0, lh)
            cr.fill()
        for it in self.items:
            self.draw_item(cr, it, lh, acc)
        if self._hover_x is not None and self.tool == "cut" and self.item_at(self.t_of(self._hover_x)):
            cr.set_source_rgba(1, 1, 1, 0.8)           # Schnittlinie unter dem Zeiger
            cr.set_dash([4.0, 3.0])
            cr.set_line_width(1)
            cr.move_to(self._hover_x + 0.5, RULER)
            cr.line_to(self._hover_x + 0.5, h)
            cr.stroke()
            cr.set_dash([])
        if self._snap_x is not None:                    # magnetische Führungslinie
            cr.set_source_rgba(*acc, 0.9)
            cr.rectangle(self._snap_x - 0.5, RULER - 4, 1, h - RULER + 4)
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

    def draw_item(self, cr, it, lh, acc):
        src = self.sources[it["src"]]
        xa, xb = self.x_of(it["pos"]), self.x_of(it["pos"] + self.length(it))
        y = self.lane_y(it["lane"])
        wpx = xb - xa
        if wpx < 1:
            return
        cr.save()
        cr.rectangle(xa, y, wpx, lh)
        cr.clip()
        cr.set_source_rgba(1, 1, 1, 0.10 if src["has_video"] else 0.14)
        cr.paint()
        th = lh * 0.62 if src["has_video"] else 0
        wh = lh - th
        if src["thumbs"] and src["dur"]:
            sw, sh = src["thumbs"].get_width(), src["thumbs"].get_height()
            scale_x = wpx / ((it["e"] - it["s"]) / src["dur"] * sw)
            cr.save()
            cr.rectangle(xa, y, wpx, th)
            cr.clip()
            cr.translate(xa - it["s"] / src["dur"] * sw * scale_x, y)
            cr.scale(scale_x, th / sh)
            cr.set_source_surface(src["thumbs"], 0, 0)
            cr.get_source().set_filter(cairo.FILTER_FAST)
            cr.paint()
            cr.restore()
        if src["wave"] and src["dur"]:
            sw, sh = src["wave"].get_width(), src["wave"].get_height()
            scale_x = wpx / ((it["e"] - it["s"]) / src["dur"] * sw)
            cr.save()
            cr.rectangle(xa, y + th, wpx, wh)
            cr.clip()
            cr.translate(xa - it["s"] / src["dur"] * sw * scale_x, y + th)
            cr.scale(scale_x, wh / sh)
            cr.set_source_rgba(*acc, 0.85)
            cr.mask_surface(src["wave"], 0, 0)
            cr.restore()
        if wpx > 46 and lh > 30:                                 # Name
            cr.set_font_size(10)
            label = os.path.basename(src["path"])
            cr.set_source_rgba(0, 0, 0, 0.75)
            cr.move_to(xa + 5, y + 12)
            cr.show_text(label)
            cr.set_source_rgba(1, 1, 1, 0.95)
            cr.move_to(xa + 4, y + 11)
            cr.show_text(label)
        cr.restore()
        cr.set_source_rgba(1, 1, 1, 0.55)                          # Rahmen
        cr.set_line_width(1)
        cr.rectangle(xa + 0.5, y + 0.5, wpx - 1, lh - 1)
        cr.stroke()
        if it is self.sel:
            cr.set_source_rgba(*acc, 1)
            cr.set_line_width(2.5)
            cr.rectangle(xa + 1.25, y + 1.25, wpx - 2.5, lh - 2.5)
            cr.stroke()
            cr.rectangle(xa, y, 3, lh)                              # Greifkanten
            cr.rectangle(xb - 3, y, 3, lh)
            cr.fill()

    # ----- Maus
    def hit_edge(self, it, x):
        xa, xb = self.x_of(it["pos"]), self.x_of(it["pos"] + self.length(it))
        if abs(x - xa) <= EDGE:
            return "l"
        if abs(x - xb) <= EDGE:
            return "r"
        return None

    def item_hit(self, x, y):
        if y < RULER:
            return None
        t = self.t_of(x)
        lane = self.lane_at(y)
        for it in self.items:
            if it["lane"] == lane and it["pos"] - EDGE / max(1e-6, self._px_s()) <= t <= it["pos"] + self.length(it) + EDGE / max(1e-6, self._px_s()):
                return it
        return None

    def _px_s(self):
        return (self.timeline.get_width() - 16) / self.span

    def snap_targets(self, skip):
        pts = [0.0, self.playhead]
        for o in self.items:
            if o is skip:
                continue
            pts.append(o["pos"])
            pts.append(o["pos"] + self.length(o))
        return pts

    def snap(self, t, skip, also_end=0.0):
        """t (und t+also_end, z. B. das Ende eines Stücks) an nahe Kanten einrasten; liefert (t', Führungslinie-x)."""
        if not self.magnet:
            return t, None
        lim = SNAP_PX / max(1e-6, self._px_s())
        best, guide = None, None
        for p in self.snap_targets(skip):
            for cand, edge in ((t, p), (t + also_end, p)):
                d = abs(cand - p)
                if d < lim and (best is None or d < best[0]):
                    best, guide = (d, p - (cand - t)), p
        if best:
            return best[1], self.x_of(guide)
        return t, None

    def on_motion(self, ctrl, x, y):
        self._hover_x = x
        it = self.item_hit(x, y)
        if self.tool == "cut":
            self.timeline.set_cursor_from_name("crosshair" if it else "default")
        else:
            edge = self.hit_edge(it, x) if it else None
            self.timeline.set_cursor_from_name("col-resize" if edge else ("grab" if it else "default"))
        if self.tool == "cut":
            self.timeline.queue_draw()

    def on_drag_begin(self, g, x, y):
        self.timeline.grab_focus()
        if not self.sources:
            return
        if y < RULER:                                   # Lineal: Abspielkopf setzen/ziehen
            self._drag = {"mode": "seek"}
            self.stop()
            self.seek(self.t_of(x))
            return
        it = self.item_hit(x, y)
        if self.tool == "cut":
            if it:
                T = self.t_of(x)
                if self.magnet:
                    T, _g = self.snap(T, it)
                self.stop()
                self.save_undo()
                new = self.split_item(it, T)
                if new:
                    self.sel = new
                    self.mark_dirty()
                    self.refresh_list()
                else:
                    self.undo_stack.pop()
                    self.tab.notify("Nicht direkt an einer Kante schneiden.")
            return
        if it is None:
            self.sel = None
            self.stop()
            self._drag = {"mode": "seek"}
            self.seek(self.t_of(x))
            self.refresh_list()
            return
        self.sel = it
        edge = self.hit_edge(it, x)
        self._drag = {"mode": "trim" + edge if edge else "move", "it": it, "x0": x, "y0": y, "t0": self.t_of(x),
                      "orig": dict(it), "undo": [dict(i) for i in self.items], "moved": False}
        self.stop()
        self.timeline.set_cursor_from_name("col-resize" if edge else "grabbing")
        self.refresh_list()

    def on_drag_update(self, g, dx, dy):
        d = self._drag
        if not d:
            return
        ok, sx, sy = g.get_start_point()
        x, y = sx + dx, sy + dy
        if d["mode"] == "seek":
            return self.seek(self.t_of(x))
        it, o = d["it"], d["orig"]
        if abs(dx) > 2 or abs(dy) > 2:
            d["moved"] = True
        dt = self.t_of(x) - d["t0"]
        self._snap_x = None
        if d["mode"] == "move":
            ln = self.length(it)
            pos, gx = self.snap(max(0.0, o["pos"] + dt), it, also_end=ln)
            self._snap_x = gx
            it["pos"] = max(0.0, pos)
            it["lane"] = self.lane_at(y)
        elif d["mode"] == "trimr":
            e = o["e"] + dt
            end_t, gx = self.snap(it["pos"] + (e - it["s"]), it)
            e = it["s"] + (end_t - it["pos"])
            it["e"] = max(it["s"] + MIN_LEN, min(e, self.sources[it["src"]]["dur"]))
            self._snap_x = gx
        else:                                            # trimml: linke Kante, rechte bleibt stehen
            right = o["pos"] + (o["e"] - o["s"])
            pos, gx = self.snap(o["pos"] + dt, it)
            pos = min(pos, right - MIN_LEN)
            s = o["s"] + (pos - o["pos"])
            if s < 0:
                pos, s = pos - s, 0.0
            it["pos"], it["s"] = max(0.0, pos), max(0.0, s)
            self._snap_x = gx
        self.timeline.queue_draw()

    def on_drag_end(self, g, dx, dy):
        d, self._drag = self._drag, None
        self._snap_x = None
        if not d or d["mode"] == "seek":
            self.timeline.queue_draw()
            return
        it = d["it"]
        if not d["moved"]:
            self.timeline.queue_draw()
            self.seek(self.playhead)
            return
        self.undo_stack.append(d["undo"])
        del self.undo_stack[:-100]
        it["pos"] = self.free_pos(it)                        # nie zwei Stücke auf derselben Spur übereinander
        self.mark_dirty()
        self.after_edit()

    def after_edit(self):
        self.afterload_light()
        self.refresh_list()

    def afterload_light(self):
        self.recalc()
        self.subtitle = f"{fmt(self.dur, False)}" + (f" · {len(self.sources)} Clips" if len(self.sources) > 1 else "")
        self.tab.set_title(self.title, self.subtitle)
        self.resize_timeline()
        self.update_time()
        self.seek(self.playhead)

    def free_pos(self, it):
        """Nächste freie Stelle für it auf seiner Spur (ohne Überlappung)."""
        ln = self.length(it)
        others = sorted((o for o in self.items if o is not it and o["lane"] == it["lane"]), key=lambda o: o["pos"])
        pos = it["pos"]

        def clash(p):
            return any(p < o["pos"] + self.length(o) - 1e-6 and p + ln > o["pos"] + 1e-6 for o in others)
        if not clash(pos):
            return pos
        cands = [0.0] + [o["pos"] + self.length(o) for o in others] + [max(0.0, o["pos"] - ln) for o in others]
        ok = [c for c in cands if c >= 0 and not clash(c)]
        return min(ok, key=lambda c: abs(c - pos)) if ok else pos

    # ----- Tasten, Rad
    def on_scroll(self, ctrl, dx, dy):
        if ctrl.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
            self.zoom_scale.set_value(max(1, min(20, self.zoom * (0.85 if dy > 0 else 1.18))))
            return True
        return False

    def on_key(self, ctrl, keyval, code, mods):
        k = Gdk.keyval_name(keyval) or ""
        shift = bool(mods & Gdk.ModifierType.SHIFT_MASK)
        c = bool(mods & Gdk.ModifierType.CONTROL_MASK)
        focus = self.get_root().get_focus()
        if isinstance(focus, (Gtk.Text, Gtk.TextView, Gtk.Editable, Gtk.DropDown, Gtk.SpinButton)):
            return False
        if k == "space":
            self.toggle_play()
        elif k in ("Left", "Right"):
            d = -1 if k == "Left" else 1
            self.stop()
            self.seek(self.playhead + (d if shift else d / self.fps))
        elif k == "Home":
            self.seek(0)
        elif k == "End":
            self.seek(self.dur)
        elif c and k.lower() == "z":
            self.undo()
        elif not c and k.lower() == "s":
            self.split(announce=True)
        elif not c and k.lower() == "v":
            self.tool_btns["drag"].set_active(True)
        elif not c and k.lower() == "c":
            self.tool_btns["cut"].set_active(True)
        elif k in ("Delete", "BackSpace"):
            self.delete_sel()
        elif not c and k.lower() == "i":
            self.trim_start()
        elif not c and k.lower() == "o":
            self.trim_end()
        else:
            return False
        return True

    # ----- Zeitleistenhöhe (Griff über der Zeitleiste)
    def _resize_begin(self, *_):
        self._tl0 = self.tl_h
        self._resizing = True
        self._saved_pic = self.picture.get_paintable()
        self.picture.set_paintable(None)          # Video während des Ziehens ausblenden: das Ruckeln kam vom Neuberechnen

    def _resize_to(self, h, now=False):
        self._tl_pending = int(max(100, min(520, h)))
        if now:
            self._apply_resize()
            return
        if not getattr(self, "_resize_tick", None):
            self._resize_tick = self.add_tick_callback(self._resize_frame)

    def _resize_frame(self, widget, clock):
        self._apply_resize()
        self._resize_tick = None
        return GLib.SOURCE_REMOVE

    def _apply_resize(self):
        if self._tl_pending is None or self._tl_pending == self.tl_h:
            return
        self.tl_h = self._tl_pending
        self.tl_scroll.set_size_request(-1, self.tl_h)
        self.timeline.set_content_height(max(self.tl_h - 14, self.content_h()))
        self.timeline.queue_draw()

    def _resize_end(self, *_):
        self._apply_resize()
        self._resizing = False
        self.cur_src = None
        self.sync_players(self.playhead, force=True)         # Bild wieder einsetzen
        try:
            import settings_menu
            settings_menu.put("timeline_h", self.tl_h)
        except Exception:
            pass

    # ================= Export =================
    def export(self):
        if self.exporting or not self.items:
            return
        fmt_i = self.fmt_drop.get_selected() if self.has_video else 3
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
        """ffmpeg-Argumente: Bild = oberste Spur zuoberst über Schwarz, Ton = alle Stücke gemischt."""
        items = sorted(self.items, key=lambda i: (-i["lane"], i["pos"]))      # unterste Spur zuerst zeichnen
        total = max(self.dur, 0.1)
        if not self.has_video:
            fmt_i = 3
        audio = self.has_audio and self.audio_btn.get_active() and fmt_i != 2
        height = [None, 1080, 720, 480][self.size_drop.get_selected()]
        vsrc = [c for c in self.sources if c["has_video"]]
        W = (vsrc[0]["w"] // 2) * 2 if vsrc else 0
        H = (vsrc[0]["h"] // 2) * 2 if vsrc else 0
        parts = []
        if fmt_i != 3:
            parts.append(f"color=c=black:s={W}x{H}:r={self.fps:.3f}:d={total:.3f},format=yuv420p[base0]")
            n = 0
            for it in items:
                c = self.sources[it["src"]]
                if not c["has_video"]:
                    continue
                ln = self.length(it)
                parts.append(f"[{it['src']}:v]trim=start={it['s']:.3f}:end={it['e']:.3f},setpts=PTS-STARTPTS+{it['pos']:.3f}/TB,"
                             f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,"
                             f"setsar=1,fps={self.fps:.3f},format=yuv420p[v{n}]")
                parts.append(f"[base{n}][v{n}]overlay=eof_action=pass:enable='between(t,{it['pos']:.3f},{it['pos'] + ln:.3f})'[base{n + 1}]")
                n += 1
            vlabel = f"[base{n}]"
        labels = []
        if audio or fmt_i == 3:
            for k, it in enumerate(items):
                c = self.sources[it["src"]]
                if not c["has_audio"]:
                    continue
                ms = int(it["pos"] * 1000)
                parts.append(f"[{it['src']}:a]atrim=start={it['s']:.3f}:end={it['e']:.3f},asetpts=PTS-STARTPTS,"
                             f"aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,adelay={ms}|{ms}[a{k}]")
                labels.append(f"[a{k}]")
            if labels:
                m = len(labels)
                parts.append("".join(labels) + f"amix=inputs={m}:duration=longest:dropout_transition=0,volume={m}[ao]")
        if fmt_i == 3:
            if not labels:
                raise RuntimeError("Kein Ton in diesem Schnitt.")
            return ["-filter_complex", ";".join(parts), "-map", "[ao]", "-t", f"{total:.3f}", "-c:a", "libmp3lame",
                    "-q:a", "2"], total
        scale = f"scale=-2:'min({height},ih)'" if height else "null"
        if fmt_i == 2:
            parts.append(f"{vlabel}fps=15,scale='min(720,iw)':-2:flags=lanczos,split[g1][g2];[g1]palettegen=stats_mode=diff[p];"
                         f"[g2][p]paletteuse=dither=sierra2_4a[vo]")
            return ["-filter_complex", ";".join(parts), "-map", "[vo]", "-t", f"{total:.3f}", "-loop", "0"], total
        parts.append(f"{vlabel}{scale},format=yuv420p[vo]")
        args = ["-filter_complex", ";".join(parts), "-map", "[vo]"]
        if audio and labels:
            args += ["-map", "[ao]"]
        args += ["-t", f"{total:.3f}"]
        if fmt_i == 0:
            args += ["-c:v", "libx264", "-crf", "20", "-preset", "medium", "-movflags", "+faststart"]
            args += ["-c:a", "aac", "-b:a", "160k"] if audio and labels else []
        else:
            args += ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-deadline", "good", "-cpu-used", "4", "-row-mt", "1"]
            args += ["-c:a", "libopus", "-b:a", "128k"] if audio and labels else []
        return args, total

    def run_export(self, out, fmt_i):
        try:
            args, total = self.build_cmd(out, fmt_i)
        except RuntimeError as e:
            return self.tab.notify(str(e))
        tmp = out + ".part" + os.path.splitext(out)[1]
        inputs = [x for c in self.sources for x in ("-i", c["path"])]
        cmd = ["ffmpeg", "-y", "-v", "error", "-nostats", "-progress", "pipe:1"] + inputs + args + [tmp]
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
