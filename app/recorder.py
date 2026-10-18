# Bildschirmaufnahme für Scourge (über wf-recorder, das Hyprlands Aufnahme-Schnittstelle nutzt).
# Ganzer Bildschirm, Bereich (Rahmen ziehen) oder Fenster (anklicken), mit oder ohne Ton.
# Stoppen: Knopf in Scourge oder Super+Umschalt+R. Danach geht es direkt in den Schnitt.
# Ausblenden passiert nur über GTK (Fenster verstecken), nie über Hyprland-Befehle: so wird garantiert
# kein anderes Fenster verschoben.
import datetime, json, os, shutil, signal, subprocess, threading, time
import chrome as ch

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk

OUT_DIR = os.path.expanduser("~/Videos/Aufnahmen")
RECORDER_CSS = """
.rec-dot { color: #ff5c5c; font-size: 22px; }
.rec-time { font-size: 34px; font-weight: 800; font-feature-settings: "tnum"; }
.countdown { font-size: 96px; font-weight: 800; color: @color9; }
"""


def hypr_json(what):
    try:
        return json.loads(subprocess.run(["hyprctl", what, "-j"], capture_output=True, text=True).stdout)
    except ValueError:
        return {}


def desktop_note(text, urgent=False):
    """Kleine Systembenachrichtigung (sichtbar, auch wenn Scourge ausgeblendet ist)."""
    if shutil.which("notify-send"):
        subprocess.Popen(["notify-send", "-a", "Scourge", "-i", "scourge", "-h", "string:x-canonical-private-synchronous:scourge-rec",
                          "-u", "critical" if urgent else "normal", "Scourge", text],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_folder(win, path=OUT_DIR):
    os.makedirs(path, exist_ok=True)
    Gtk.FileLauncher(file=Gio.File.new_for_path(path)).launch(win, None, None)


def show_in_folder(win, path):
    Gtk.FileLauncher(file=Gio.File.new_for_path(path)).open_containing_folder(win, None, None)


def pulse_default(kind):
    r = subprocess.run(["pactl", f"get-default-{kind}"], capture_output=True, text=True)
    return r.stdout.strip() or None


class RecorderPage(Gtk.ScrolledWindow):
    title = "Bildschirm aufnehmen"
    subtitle = "Bildschirm, Bereich oder Fenster"

    def __init__(self, tab):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.tab = tab
        self.proc = None
        self.out = None
        self.started = 0
        self.hidden = False
        self.busy = False      # Auswahl/Countdown/Aufnahme läuft: kein zweiter Start
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_start=16, margin_end=16, margin_bottom=16)
        box.add_css_class("home")
        self.set_child(box)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        box.append(self.stack)
        self.stack.add_named(self.build_setup(), "setup")
        self.stack.add_named(self.build_running(), "running")
        self.stack.add_named(self.build_missing(), "missing")
        self.recent_box = ch.Card("letzte aufnahmen", spacing=4)
        self.recent_box.set_visible(False)
        box.append(self.recent_box)
        self.check_tool()

    # ---------- Ansichten ----------
    def card(self, parent, title):
        c = ch.Card(title, spacing=10)
        parent.append(c)
        return c

    def toggles(self, parent, label, options):
        row = Gtk.Box(spacing=6)
        l = Gtk.Label(label=label, xalign=0, width_chars=10)
        l.add_css_class("key")
        row.append(l)
        group, btns = None, []
        for text, tip in options:
            b = Gtk.ToggleButton(label=text, tooltip_text=tip)
            b.add_css_class("pb"); b.add_css_class("small")
            if group:
                b.set_group(group)
            group = group or b
            btns.append(b)
            row.append(b)
        btns[0].set_active(True)
        parent.append(row)
        return btns

    def build_setup(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        c = self.card(box, "Was aufnehmen?")
        self.area = self.toggles(c, "Bereich", [("󰍹  Ganzer Bildschirm", "Alles, was auf dem Bildschirm passiert"),
                                                ("󰆞  Bereich wählen", "Nach dem Start einen Rahmen aufziehen"),
                                                ("󰖯  Fenster wählen", "Nach dem Start ein Fenster anklicken")])
        self.audio = self.toggles(c, "Ton", [("󰖁  Kein Ton", ""), ("󰓃  Systemton", "Was aus den Lautsprechern kommt"),
                                            ("󰍬  Mikrofon", "Deine Stimme")])
        self.quality = self.toggles(c, "Bildrate", [("30 fps", "Kleinere Dateien"), ("60 fps", "Flüssiger, z. B. für Spiele")])
        opts = Gtk.Box(spacing=6)
        self.hide_btn = Gtk.ToggleButton(label="󰘓  Scourge dabei ausblenden", active=True,
                                         tooltip_text="Scourge verschwindet während der Aufnahme und kommt danach zurück")
        self.count_btn = Gtk.ToggleButton(label="󱎫  3 s Countdown", active=True)
        for b in (self.hide_btn, self.count_btn):
            b.add_css_class("pb"); b.add_css_class("small")
            opts.append(b)
        c.append(opts)
        row = Gtk.Box(spacing=6)
        self.start_btn = Gtk.Button(label="󰑋  Aufnahme starten")
        self.start_btn.add_css_class("pb")
        self.start_btn.connect("clicked", lambda *_: self.prepare())
        row.append(self.start_btn)
        folder = Gtk.Button(label="󰉋  Aufnahme-Ordner öffnen", tooltip_text=OUT_DIR)
        folder.add_css_class("pb")
        folder.connect("clicked", lambda *_: open_folder(self.tab.win))
        row.append(folder)
        c.append(row)
        hint = Gtk.Label(label="Stoppen: Super+Umschalt+R oder der Knopf hier. Die Aufnahme landet in ~/Videos/Aufnahmen "
                               "und öffnet sich danach direkt im Schnitt.", xalign=0, wrap=True)
        hint.add_css_class("note")
        c.append(hint)
        self.count_lbl = Gtk.Label(label="", visible=False)
        self.count_lbl.add_css_class("countdown")
        box.append(self.count_lbl)
        return box

    def build_running(self):
        c = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, halign=Gtk.Align.CENTER, margin_top=40)
        row = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER)
        dot = Gtk.Label(label="●")
        dot.add_css_class("rec-dot")
        row.append(dot)
        self.rec_time = Gtk.Label(label="0:00")
        self.rec_time.add_css_class("rec-time")
        row.append(self.rec_time)
        c.append(row)
        self.rec_info = Gtk.Label(label="", wrap=True, justify=Gtk.Justification.CENTER)
        self.rec_info.add_css_class("note")
        c.append(self.rec_info)
        stop = Gtk.Button(label="󰓛  Aufnahme stoppen", halign=Gtk.Align.CENTER)
        stop.add_css_class("pb")
        stop.connect("clicked", lambda *_: self.stop())
        c.append(stop)
        return c

    def build_missing(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        c = self.card(box, "Aufnahme-Programm fehlt")
        t = Gtk.Label(label="Für Bildschirmaufnahmen unter Hyprland braucht Scourge das kleine Programm wf-recorder. "
                            "Einmal in einem Terminal installieren:", xalign=0, wrap=True)
        t.add_css_class("note")
        c.append(t)
        cmd = Gtk.Label(label="sudo pacman -S wf-recorder", xalign=0, selectable=True)
        cmd.add_css_class("hash")
        c.append(cmd)
        b = Gtk.Button(label="󰑐  Erneut prüfen", halign=Gtk.Align.START)
        b.add_css_class("pb"); b.add_css_class("small")
        b.connect("clicked", lambda *_: self.check_tool())
        c.append(b)
        return box

    def check_tool(self):
        self.stack.set_visible_child_name("setup" if shutil.which("wf-recorder") else "missing")
        self.refresh_recent()

    def refresh_recent(self):
        while (c := self.recent_box.get_first_child()):
            self.recent_box.remove(c)
        if not os.path.isdir(OUT_DIR):
            return
        files = sorted((os.path.join(OUT_DIR, f) for f in os.listdir(OUT_DIR) if f.endswith(".mp4")),
                       key=os.path.getmtime, reverse=True)[:6]
        self.recent_box.set_visible(bool(files))
        if not files:
            return
        head = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        fb = Gtk.Button(label="󰉋 ordner öffnen", tooltip_text=OUT_DIR, valign=Gtk.Align.CENTER)
        fb.add_css_class("pb"); fb.add_css_class("small")
        fb.connect("clicked", lambda *_: open_folder(self.tab.win))
        head.append(fb)
        self.recent_box.append(head)
        for f in files:
            row = Gtk.Box(spacing=8)
            n = Gtk.Label(label=os.path.basename(f), xalign=0, hexpand=True, ellipsize=3)
            n.add_css_class("finding-title")
            row.append(n)
            sz = Gtk.Label(label=f"{os.path.getsize(f) / 1e6:.1f} MB".replace(".", ","))
            sz.add_css_class("key")
            row.append(sz)
            for label, tip, cb in (("󰆐", "Schneiden", lambda f=f: self.tab.open_cutter(f)),
                                   ("󰈈", "Prüfen", lambda f=f: self.tab.load_path(f)),
                                   ("󰉋", "Im Ordner zeigen", lambda f=f: show_in_folder(self.tab.win, f))):
                b = Gtk.Button(label=label, tooltip_text=tip)
                b.add_css_class("pb"); b.add_css_class("small")
                b.connect("clicked", lambda *_, cb=cb: cb())
                row.append(b)
            self.recent_box.append(row)

    # ---------- Ablauf ----------
    def choice(self, btns):
        return next(i for i, b in enumerate(btns) if b.get_active())

    def prepare(self):
        if self.busy:
            return
        self.busy = True
        self.start_btn.set_sensitive(False)
        mode = self.choice(self.area)
        if mode == 0:
            self.after_select(None)
            return
        args, stdin = ["slurp", "-d"], None
        if mode == 2:  # nur sichtbare Fenster auf dem aktuellen Workspace anbieten (Scourge selbst nicht)
            ws = hypr_json("activeworkspace").get("id")
            boxes = [f"{c['at'][0]},{c['at'][1]} {c['size'][0]}x{c['size'][1]} {c['class']}"
                     for c in hypr_json("clients")
                     if c.get("workspace", {}).get("id") == ws and c.get("mapped") and c.get("pid") != os.getpid()]
            if not boxes:
                self.abort("Auf diesem Workspace ist kein anderes Fenster offen.")
                return
            args, stdin = ["slurp", "-r"], "\n".join(boxes) + "\n"
        # slurp läuft im Hintergrund: Scourge friert währenddessen nicht ein
        flags = Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE
        if stdin:
            flags |= Gio.SubprocessFlags.STDIN_PIPE
        try:
            proc = Gio.Subprocess.new(args, flags)
        except GLib.Error as e:
            self.abort(f"Auswahl nicht möglich: {e.message}")
            return

        def done(p, res):
            try:
                _, out, _ = p.communicate_utf8_finish(res)
            except GLib.Error:
                out = ""
            geometry = (out or "").strip()
            if not p.get_successful() or not geometry:
                self.abort("Auswahl abgebrochen.")
            else:
                self.after_select(geometry)
        proc.communicate_utf8_async(stdin, None, done)

    def abort(self, msg):
        self.busy = False
        self.start_btn.set_sensitive(True)
        self.count_lbl.set_visible(False)
        self.tab.notify(msg)

    def after_select(self, geometry):
        if self.count_btn.get_active():
            self.countdown(3, geometry)
        else:
            self.go(geometry)

    def countdown(self, n, geometry):
        self.count_lbl.set_visible(n > 0)
        self.count_lbl.set_label(str(n))
        if n == 0:
            self.go(geometry)
            return False
        GLib.timeout_add(1000, self.countdown, n - 1, geometry)
        return False

    def go(self, geometry):
        """Scourge ausblenden (nur das eigene Fenster) und kurz warten, bis es wirklich weg ist."""
        self.hidden = self.hide_btn.get_active()
        if self.hidden:
            self.tab.app_main.hold()  # App läuft weiter, obwohl kein Fenster sichtbar ist
            self.tab.win.set_visible(False)
            GLib.timeout_add(350, lambda: (self.start(geometry), False)[-1])
        else:
            self.start(geometry)

    def show_again(self):
        if self.hidden:
            self.hidden = False
            self.tab.win.set_visible(True)
            self.tab.app_main.release()
        self.tab.win.present()

    def start(self, geometry):
        os.makedirs(OUT_DIR, exist_ok=True)
        self.out = os.path.join(OUT_DIR, f"aufnahme-{datetime.datetime.now():%Y-%m-%d-%H%M%S}.mp4")
        cmd = ["wf-recorder", "-y", "-f", self.out, "-r", "60" if self.choice(self.quality) else "30"]
        if geometry:
            cmd += ["-g", geometry]
        else:
            mon = next((m["name"] for m in hypr_json("monitors") if m.get("focused")), None)
            if mon:
                cmd += ["-o", mon]
        a = self.choice(self.audio)
        if a == 1:
            sink = pulse_default("sink")
            cmd.append(f"--audio={sink}.monitor" if sink else "--audio")
        elif a == 2:
            src = pulse_default("source")
            cmd.append(f"--audio={src}" if src else "--audio")
        if os.path.exists("/dev/dri/renderD128"):  # Grafikkarte kodiert, CPU bleibt frei
            cmd += ["-c", "h264_vaapi", "-d", "/dev/dri/renderD128"]
        else:
            cmd += ["-c", "libx264", "-p", "preset=veryfast", "-p", "crf=23"]
        self.cmd = cmd
        self.count_lbl.set_visible(False)
        try:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.PIPE, text=True, start_new_session=True)
        except OSError as e:
            self.show_again()
            self.abort(f"Aufnahme konnte nicht starten: {e}")
            return
        self.started = time.time()
        self.stack.set_visible_child_name("running")
        self.rec_info.set_label("Super+Umschalt+R stoppt die Aufnahme, auch wenn Scourge ausgeblendet ist.")
        if self.hidden:
            desktop_note("Aufnahme läuft. Stoppen mit Super+Umschalt+R.")
        GLib.timeout_add(500, self.tick)
        threading.Thread(target=self.wait, args=(self.proc,), daemon=True).start()

    def tick(self):
        if not self.proc:
            return False
        t = int(time.time() - self.started)
        self.rec_time.set_label(f"{t // 60}:{t % 60:02d}")
        if self.out and os.path.exists(self.out):
            self.rec_info.set_label(f"{os.path.getsize(self.out) / 1e6:.1f} MB · Super+Umschalt+R stoppt".replace(".", ","))
        return True

    def wait(self, proc):
        err = proc.stderr.read()
        code = proc.wait()
        GLib.idle_add(self.finished, code, err)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGINT)
            # falls wf-recorder hängt: nach 8 s hart beenden, damit Scourge nie unsichtbar bleibt
            p = self.proc
            GLib.timeout_add(8000, lambda: (p.poll() is None and p.kill(), False)[-1])

    def finished(self, code, err):
        self.proc = None
        self.busy = False
        self.start_btn.set_sensitive(True)
        self.show_again()
        self.stack.set_visible_child_name("setup")
        ok = self.out and os.path.exists(self.out) and os.path.getsize(self.out) > 1000
        if ok:
            if "h264_vaapi" in self.cmd and code not in (0, 130, -2) and "vaapi" in (err or "").lower():
                self.tab.notify("Grafikkarte konnte nicht kodieren, nimm es bitte noch einmal auf.")
            else:
                self.tab.notify(f"Gespeichert in ~/Videos/Aufnahmen/{os.path.basename(self.out)}")
            desktop_note(f"Aufnahme gespeichert: ~/Videos/Aufnahmen/{os.path.basename(self.out)}")
            self.refresh_recent()
            GLib.idle_add(lambda out=self.out: (self.tab.open_cutter(out), False)[-1])
        else:
            last = (err or "").strip().splitlines()
            self.tab.notify("Aufnahme fehlgeschlagen: " + (last[-1][:90] if last else f"Code {code}"))
            self.refresh_recent()
        return False

    def pause(self):
        pass

    def shutdown(self):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
