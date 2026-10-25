# System- & Hardware-Einstellungen für Scourge (Startseite → "System-Einstellungen").
# Stellt echte Regler bereit: Helligkeit, Lautstärke (Ein-/Ausgabe + Gerätewahl), Maus-Sensitivität
# und -Beschleunigung, Touchpad, Tastaturlayout, WLAN und Bluetooth.
# Hyprland-Einstellungen werden sofort angewandt (hyprctl eval) und in die hyprland.lua geschrieben,
# damit sie einen Neustart überleben. Ton/Helligkeit speichert das System selbst.
import importlib.util, io, json, os, re, shutil, subprocess, sys, time

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk

from tools import ToolPage

HYPR_CONF = os.path.expanduser("~/.config/hypr/hyprland.lua")
LAYOUTS = [("de", "Deutsch (de)"), ("us", "Englisch US (us)"), ("gb", "Englisch UK (gb)"),
           ("fr", "Französisch (fr)"), ("es", "Spanisch (es)"), ("it", "Italienisch (it)"),
           ("ch", "Schweiz (ch)"), ("ru", "Russisch (ru)")]


def sh(args, timeout=6):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def hypr_get(opt):
    """Wert einer Hyprland-Option live auslesen (float/str/bool/int)."""
    out = sh(["hyprctl", "getoption", "-j", opt])
    try:
        d = json.loads(out)
    except ValueError:
        return None
    for k in ("float", "str", "bool", "int"):
        if k in d:
            return d[k]
    return None


def lua_value(value):
    """Python-/Textwert als Lua-Literal (bool, Zahl oder String)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    v = str(value)
    if v in ("true", "false"):
        return v
    try:
        float(v)
        return v
    except ValueError:
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def hypr_set(opt, value):
    """Option live setzen. Mit Lua-Konfiguration geht `hyprctl keyword` nicht mehr
    („keyword can't work with non-legacy parsers“) → hl.config({...}) per eval."""
    table = lua_value(value)
    for part in reversed(opt.split(":")):
        table = f"{{ {part} = {table} }}"
    sh(["hyprctl", "eval", f"hl.config({table})"])


def persist_lua(key, value, quoted=False):
    """Einen Schlüssel im input-Block der hyprland.lua auf einen neuen Wert setzen."""
    try:
        text = open(HYPR_CONF).read()
    except OSError:
        return False
    if quoted:
        pat = re.compile(rf'({key}\s*=\s*)"[^"]*"')
        rep = rf'\g<1>"{value}"'
    else:
        pat = re.compile(rf'({key}\s*=\s*)[^,\n]+')
        rep = rf'\g<1>{value}'
    new, n = pat.subn(rep, text, count=1)
    if n:
        try:
            open(HYPR_CONF, "w").write(new)
            return True
        except OSError:
            return False
    return False


SECTIONS = {
    "devices": ("Bildschirm, Ton & Funk", "Helligkeit, Lautstärke, Mikrofon, Ausgabegerät, WLAN und Bluetooth."),
    "display": ("Bildschirm", "Helligkeit des Displays."),
    "audio": ("Ton", "Lautstärke, Mikrofon und Ausgabegerät."),
    "input": ("Eingabe", "Maus-Empfindlichkeit, Touchpad und Tastaturlayout."),
    "radio": ("Funk", "WLAN und Bluetooth."),
    "cursor": ("Cursor", "Farbe, Form und Verhalten des Mauszeigers. Änderungen gelten sofort überall."),
    "waybar": ("Waybar", "Statusleiste zurückholen, wenn sie abgestürzt oder hängen geblieben ist."),
}

WAYBAR_RESTART = os.path.expanduser("~/.config/waybar/scripts/waybar-restart.sh")

# Cursor-Theme „ist-punkt“ wird von einem geteilten rice-apps-Skript gebaut (auch von Super+W)
CURSOR_GEN = os.path.expanduser("~/.local/share/rice-apps/cursor/make-cursor.py")
CURSOR_PREVIEW = [("default", "Zeiger"), ("pointer", "Link"), ("text", "Text"), ("wait", "Warten"),
                  ("move", "Verschieben"), ("crosshair", "Präzision"), ("ew-resize", "Größe"), ("not-allowed", "Verboten")]
CURSOR_COLORS = [("primary", "Hintergrund · Hauptfarbe"), ("secondary", "Hintergrund · Zweitfarbe"),
                 ("tertiary", "Hintergrund · Drittfarbe"), ("neutral", "Neutral (hell)"), ("custom", "Eigene Farbe")]


def cursor_gen():
    """Generator-Modul laden (None, wenn nicht installiert – z. B. bei Weitergabe ohne rice-apps)."""
    if "ist_cursor" in sys.modules:
        return sys.modules["ist_cursor"]
    if not os.path.exists(CURSOR_GEN):
        return None
    try:
        spec = importlib.util.spec_from_file_location("ist_cursor", CURSOR_GEN)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception:
        return None
    sys.modules["ist_cursor"] = mod
    return mod


def svg_texture(gen, svgtext, px=64):
    import cairo
    from gi.repository import Rsvg
    h = Rsvg.Handle.new_from_data(svgtext.encode())
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, px, px)
    r = Rsvg.Rectangle()
    r.x, r.y, r.width, r.height = 0, 0, px, px
    h.render_document(cairo.Context(surf), r)
    buf = io.BytesIO()
    surf.write_to_png(buf)
    return Gdk.Texture.new_from_bytes(GLib.Bytes.new(buf.getvalue()))


class SystemSettings(ToolPage):
    title = "System-Einstellungen"
    subtitle = ""
    privacy = "Ändert nur lokale Einstellungen auf diesem Rechner. Nichts verlässt das Gerät."

    def __init__(self, tab, section="input"):
        self._timers = {}
        self.section = section
        self.title, self.subtitle = SECTIONS.get(section, ("System-Einstellungen", ""))
        super().__init__(tab)

    def build(self):
        builders = {
            "devices": [self.display_card, self.audio_card, self.radio_card],
            "display": [self.display_card],
            "audio": [self.audio_card],
            "input": [self.mouse_card, self.keyboard_card],
            "radio": [self.radio_card],
            "cursor": [self.cursor_cards],
            "waybar": [self.waybar_cards],
        }
        for b in builders.get(self.section, [self.display_card, self.audio_card, self.mouse_card,
                                             self.keyboard_card, self.radio_card, self.cursor_cards]):
            b()

    # ---------- kleine Bausteine ----------
    def _debounce(self, key, fn, delay=80):
        if self._timers.get(key):
            GLib.source_remove(self._timers[key])
        self._timers[key] = GLib.timeout_add(delay, lambda: (self._timers.pop(key, None), fn(), False)[-1])

    def slider(self, parent, name, lo, hi, val, cb, step=1, fmt="{:.0f}", digits=0, unit=""):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head = Gtk.Box()
        n = Gtk.Label(label=name, xalign=0, hexpand=True)
        n.add_css_class("key")
        vlab = Gtk.Label(label=fmt.format(val) + unit)
        vlab.add_css_class("studio-val")
        head.append(n)
        head.append(vlab)
        s = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, step)
        s.set_digits(digits)
        s.set_draw_value(False)
        s.add_css_class("pb")
        s.set_value(val)
        s.connect("value-changed", lambda sc: (vlab.set_label(fmt.format(sc.get_value()) + unit), cb(sc.get_value())))
        import chrome
        chrome.reset_on_dblclick(n, s, val)
        box.append(head)
        box.append(s)
        parent.append(box)
        return s

    def switch_row(self, parent, name, val, cb, sub=None):
        row = Gtk.Box(spacing=8)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        l = Gtk.Label(label=name, xalign=0)
        l.add_css_class("key")
        col.append(l)
        if sub:
            s = Gtk.Label(label=sub, xalign=0, wrap=True)
            s.add_css_class("note")
            col.append(s)
        sw = Gtk.Switch(active=val, valign=Gtk.Align.CENTER)
        sw.connect("state-set", lambda sw, state: (cb(state), False)[-1])
        row.append(col)
        row.append(sw)
        parent.append(row)
        return sw

    def dropdown(self, parent, name, labels, active_index, cb):
        row = Gtk.Box(spacing=8)
        l = Gtk.Label(label=name, xalign=0, hexpand=True)
        l.add_css_class("key")
        dd = Gtk.DropDown.new_from_strings(labels)
        dd.add_css_class("pb")
        if 0 <= active_index < len(labels):
            dd.set_selected(active_index)
        dd.connect("notify::selected", lambda d, _: cb(d.get_selected()))
        row.append(l)
        row.append(dd)
        parent.append(row)
        return dd

    # ---------- Anzeige ----------
    def display_card(self):
        c = self.card("Anzeige")
        out = sh(["brightnessctl", "-m"]).strip()
        parts = out.split(",")
        pct = 50
        if len(parts) >= 4 and parts[3].endswith("%"):
            pct = int(parts[3][:-1])
        self.slider(c, "Helligkeit", 1, 100, pct, self.set_brightness, unit=" %")

    def set_brightness(self, v):
        self._debounce("bright", lambda: sh(["brightnessctl", "-q", "set", f"{int(v)}%"]), 40)

    # ---------- Ton ----------
    def audio_card(self):
        c = self.card("Ton – Ausgabe")
        vol, muted = self.get_volume("@DEFAULT_AUDIO_SINK@")
        self.sink_vol = self.slider(c, "Lautstärke", 0, 100, round(vol * 100),
                                    lambda v: self._debounce("sinkvol", lambda: self.set_volume("@DEFAULT_AUDIO_SINK@", v)), unit=" %")
        self.switch_row(c, "Stumm", muted, lambda s: sh(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if s else "0"]))
        sinks, names, cur = self.devices("sinks")
        if len(sinks) > 1:
            self.dropdown(c, "Ausgabegerät", sinks, names.index(cur) if cur in names else 0,
                          lambda i: self.set_default("sink", names[i], self.sink_vol))

        c = self.card("Ton – Eingabe (Mikrofon)")
        mics, mnames, mcur = self.devices("sources")
        if mics:
            vol, muted = self.get_volume("@DEFAULT_AUDIO_SOURCE@")
            self.src_vol = self.slider(c, "Mikrofon-Pegel", 0, 100, round(vol * 100),
                                       lambda v: self._debounce("srcvol", lambda: self.set_volume("@DEFAULT_AUDIO_SOURCE@", v)), unit=" %")
            self.switch_row(c, "Stumm", muted, lambda s: sh(["wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", "1" if s else "0"]))
            if len(mics) > 1:
                self.dropdown(c, "Mikrofon", mics, mnames.index(mcur) if mcur in mnames else 0,
                              lambda i: self.set_default("source", mnames[i], getattr(self, "src_vol", None)))
        else:
            n = Gtk.Label(label="Kein Mikrofon gefunden.", xalign=0)
            n.add_css_class("note")
            c.append(n)

    def get_volume(self, target):
        out = sh(["wpctl", "get-volume", target])
        m = re.search(r"Volume:\s*([\d.]+)", out)
        return (float(m.group(1)) if m else 0.0), ("MUTED" in out)

    def set_volume(self, target, pct):
        sh(["wpctl", "set-volume", target, f"{min(1.0, pct / 100):.2f}"])

    def devices(self, kind):
        """-> (Beschreibungen, Namen, aktueller Name). Monitore werden ausgelassen."""
        block = sh(["pactl", "list", kind])
        names, descs = [], []
        cur_name = None
        for b in block.split("\n\n"):
            nm = re.search(r"Name:\s*(.+)", b)
            de = re.search(r"Description:\s*(.+)", b)
            if nm and de and not nm.group(1).strip().endswith(".monitor"):
                names.append(nm.group(1).strip())
                descs.append(de.group(1).strip())
        cur_name = sh(["pactl", f"get-default-{kind[:-1]}"]).strip()
        return descs, names, cur_name

    def set_default(self, kind, name, vol_slider):
        sh(["pactl", f"set-default-{kind}", name])
        target = "@DEFAULT_AUDIO_SINK@" if kind == "sink" else "@DEFAULT_AUDIO_SOURCE@"
        if vol_slider is not None:
            v, _ = self.get_volume(target)
            vol_slider.set_value(round(v * 100))
        self.tab.notify("Gerät gewechselt")

    # ---------- Maus & Touchpad ----------
    def mouse_card(self):
        c = self.card("Maus & Touchpad")
        sens = hypr_get("input:sensitivity")
        self.slider(c, "Maus-Empfindlichkeit", -1.0, 1.0, float(sens) if sens is not None else 0.0,
                    self.set_sensitivity, step=0.05, fmt="{:+.2f}", digits=2)
        n = Gtk.Label(label="0 = unverändert. Minus = langsamer, Plus = schneller.", xalign=0, wrap=True)
        n.add_css_class("note")
        c.append(n)
        flat = hypr_get("input:accel_profile") == "flat"
        self.switch_row(c, "Beschleunigung aus (flat)", flat, self.set_accel,
                        sub="Rohe 1:1-Bewegung ohne Kurve – fühlt sich direkter an.")
        nat = bool(hypr_get("input:touchpad:natural_scroll"))
        self.switch_row(c, "Touchpad: natürliches Scrollen", nat, self.set_natural)

    def set_sensitivity(self, v):
        v = round(v, 2)
        self._debounce("sens", lambda: (hypr_set("input:sensitivity", v), persist_lua("sensitivity", v)), 120)

    def set_accel(self, on):
        prof = "flat" if on else "adaptive"
        hypr_set("input:accel_profile", prof)
        persist_lua("accel_profile", prof, quoted=True)

    def set_natural(self, on):
        hypr_set("input:touchpad:natural_scroll", "true" if on else "false")
        persist_lua("natural_scroll", "true" if on else "false")

    # ---------- Tastatur ----------
    def keyboard_card(self):
        c = self.card("Tastatur")
        cur = hypr_get("input:kb_layout") or "de"
        first = cur.split(",")[0]
        codes = [x for x, _ in LAYOUTS]
        if first not in codes:
            LAYOUTS.insert(0, (first, first))
            codes = [x for x, _ in LAYOUTS]
        self.dropdown(c, "Layout", [lbl for _, lbl in LAYOUTS], codes.index(first), self.set_layout)
        n = Gtk.Label(label="Setzt das Tastaturlayout sofort und dauerhaft.", xalign=0, wrap=True)
        n.add_css_class("note")
        c.append(n)

    def set_layout(self, i):
        code = LAYOUTS[i][0]
        hypr_set("input:kb_layout", code)
        persist_lua("kb_layout", code, quoted=True)
        self.tab.notify(f"Layout: {code}")

    # ---------- Funk ----------
    def radio_card(self):
        c = self.card("Funk")
        self.wifi_sw = self.switch_row(c, "WLAN", False, self.set_wifi)
        self.bt_sw = self.switch_row(c, "Bluetooth", False, self.set_bt)
        self._radio_cb = True  # verhindert, dass das Setzen des Anfangszustands gleich schaltet
        self.run(self.read_radio, self.show_radio)

    def read_radio(self):
        wifi = "enabled" in sh(["nmcli", "-t", "radio", "wifi"])
        bt = "Powered: yes" in sh(["bluetoothctl", "show"])
        return wifi, bt

    def show_radio(self, r):
        if isinstance(r, Exception):
            return
        wifi, bt = r
        self._radio_cb = False
        self.wifi_sw.set_active(wifi)
        self.bt_sw.set_active(bt)
        self._radio_cb = True

    def set_wifi(self, on):
        if self._radio_cb:
            self.run(lambda: sh(["nmcli", "radio", "wifi", "on" if on else "off"]), lambda *_: None)

    def set_bt(self, on):
        if self._radio_cb:
            self.run(lambda: sh(["bluetoothctl", "power", "on" if on else "off"]), lambda *_: None)

    # ---------- Cursor ----------
    def cursor_cards(self):
        g = cursor_gen()
        if not g:
            c = self.card("Cursor")
            n = Gtk.Label(label="Der Cursor-Generator (rice-apps/cursor/make-cursor.py) ist nicht installiert.", xalign=0, wrap=True)
            n.add_css_class("note")
            c.append(n)
            return
        self.cg, self.ccfg = g, g.load_config()
        self.c_frame, self.c_anim, self.c_busy, self.c_again = 0, None, False, False
        self.c_w = {}

        # Vorschau: echte Formen; Drüberfahren zeigt den tatsächlich aktiven Cursor
        c = self.card("Vorschau")
        fl = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4, min_children_per_line=4,
                         homogeneous=True, column_spacing=8, row_spacing=8)
        self.c_pics, self.c_tiles = {}, []
        for shape, name in CURSOR_PREVIEW:
            tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            tile.add_css_class("curtile")
            pic = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN)
            pic.set_size_request(48, 48)
            lab = Gtk.Label(label=name.lower())
            lab.add_css_class("key")
            tile.append(pic)
            tile.append(lab)
            tile.set_cursor_from_name(shape)
            tile.set_tooltip_text("Drüberfahren zeigt den echten Cursor")
            self.c_pics[shape] = pic
            self.c_tiles.append(tile)
            fl.append(tile)
        c.append(fl)
        row = Gtk.Box(spacing=6)
        l = Gtk.Label(label="Untergrund", xalign=0, hexpand=True)
        l.add_css_class("key")
        row.append(l)
        first = None
        for label, light in (("dunkel", False), ("hell", True)):
            b = Gtk.ToggleButton(label=label, active=not light)
            b.add_css_class("pb"); b.add_css_class("seg")
            if first:
                b.set_group(first)
            first = first or b
            b.connect("toggled", lambda b, light=light: b.get_active() and self.c_ground(light))
            row.append(b)
        c.append(row)

        # Farbe
        c = self.card("Farbe")
        keys = [k for k, _ in CURSOR_COLORS]
        cur = self.ccfg["color"] if self.ccfg["color"] in keys else "primary"
        self.c_w["color"] = self.dropdown(c, "Farbquelle", [lbl for _, lbl in CURSOR_COLORS], keys.index(cur),
                                          lambda i: self.c_color(keys[i]))
        self.c_custom_row = Gtk.Box(spacing=8)
        l = Gtk.Label(label="Eigene Farbe", xalign=0, hexpand=True)
        l.add_css_class("key")
        rgba = Gdk.RGBA()
        rgba.parse(self.ccfg["custom"])
        self.c_custom = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), rgba=rgba)
        self.c_custom.add_css_class("pb")
        self.c_custom.connect("notify::rgba", lambda b, _: self.c_set("custom", self.c_hex(b.get_rgba())))
        self.c_custom_row.append(l)
        self.c_custom_row.append(self.c_custom)
        self.c_custom_row.set_visible(cur == "custom")
        c.append(self.c_custom_row)
        n = Gtk.Label(label="Hintergrund-Farben wechseln bei Super+W automatisch mit.", xalign=0, wrap=True)
        n.add_css_class("note")
        c.append(n)

        # Form
        c = self.card("Form")
        S = lambda key, name, lo, hi, step, fmt, digits=2, unit="", k=1: self.c_w.__setitem__(key, self.slider(
            c, name, lo, hi, self.ccfg[key] * k, lambda v, key=key, k=k: self.c_set(key, round(v / k, 3)),
            step=step, fmt=fmt, digits=digits, unit=unit))
        S("scale", "Größe", 0.8, 1.8, 0.05, "×{:.2f}")
        S("dot", "Punkt", 2.0, 5.5, 0.1, "{:.1f}", 1)
        S("link", "Ring über Links", 4.5, 9.5, 0.1, "{:.1f}", 1)
        S("ring", "Ring um den Punkt", 0, 100, 5, "{:.0f}", 0, " %", k=100)
        S("outline", "Dunkle Kontur", 0, 2, 0.1, "{:.1f}", 1)

        # Verhalten
        c = self.card("Verhalten")
        self.c_w["wait_ms"] = self.slider(c, "Warte-Punkte: Bildwechsel", 120, 600, self.ccfg["wait_ms"],
                                          lambda v: self.c_set("wait_ms", int(v)), step=20, unit=" ms")
        t = hypr_get("cursor:inactive_timeout")
        self.c_w["timeout"] = self.slider(c, "Ausblenden nach Stillstand", 0, 10, float(t or 0), self.c_timeout, unit=" s")
        n = Gtk.Label(label="0 s = nie ausblenden.", xalign=0)
        n.add_css_class("note")
        c.append(n)
        self.switch_row(c, "Beim Tippen ausblenden", bool(hypr_get("cursor:hide_on_key_press")), self.c_hide_keys)
        reset = self.button("󰑐 zurücksetzen", self.c_reset, tip="Farbe und Form auf Standard", small=True)
        reset.set_halign(Gtk.Align.END)
        c.append(reset)

        self.c_render()
        self.connect("map", lambda *_: self.c_start_anim())
        self.connect("unmap", lambda *_: self.c_stop_anim())
        self.c_start_anim()

    @staticmethod
    def c_hex(rgba):
        return "#{:02x}{:02x}{:02x}".format(*(round(x * 255) for x in (rgba.red, rgba.green, rgba.blue)))

    def c_render(self):
        g, cfg = self.cg, self.ccfg
        c, k = g.colors(cfg)
        S = g.shapes(c, k, cfg)
        self.c_wait = [svg_texture(g, g.svg(f, cfg["scale"])) for f in S["wait"]]
        for shape, pic in self.c_pics.items():
            pic.set_paintable(self.c_wait[self.c_frame % len(self.c_wait)] if shape == "wait"
                              else svg_texture(g, g.svg(S[shape][0], cfg["scale"])))

    def c_tick(self):
        self.c_frame += 1
        self.c_pics["wait"].set_paintable(self.c_wait[self.c_frame % len(self.c_wait)])
        return True

    def c_start_anim(self):
        self.c_stop_anim()
        self.c_anim = GLib.timeout_add(max(60, int(self.ccfg["wait_ms"])), self.c_tick)

    def c_stop_anim(self):
        if self.c_anim:
            GLib.source_remove(self.c_anim)
            self.c_anim = None

    def c_ground(self, light):
        for t in self.c_tiles:
            (t.add_css_class if light else t.remove_css_class)("light")

    def c_color(self, key):
        self.c_custom_row.set_visible(key == "custom")
        self.c_set("color", key)

    def c_set(self, key, val):
        if self.ccfg.get(key) == val:
            return
        self.ccfg[key] = val
        self.c_render()
        if key == "wait_ms":
            self.c_start_anim()
        self._debounce("cursor", self.c_apply, 350)

    def c_apply(self):
        """Konfiguration speichern und Theme neu bauen (läuft nie doppelt)."""
        if self.c_busy:
            self.c_again = True
            return
        self.c_busy, self.c_again = True, False
        self.cg.save_config(self.ccfg)

        def done(res):
            self.c_busy = False
            if self.c_again:
                self.c_apply()
            elif isinstance(res, Exception) or (res is not None and res.returncode):
                self.tab.notify("Cursor konnte nicht gebaut werden")
        self.run(lambda: subprocess.run([sys.executable, CURSOR_GEN, "--apply"], capture_output=True, timeout=20), done)

    def c_timeout(self, v):
        v = int(v)
        self._debounce("ctimeout", lambda: (hypr_set("cursor:inactive_timeout", v), persist_lua("inactive_timeout", v)), 150)

    def c_hide_keys(self, on):
        val = "true" if on else "false"
        hypr_set("cursor:hide_on_key_press", val)
        persist_lua("hide_on_key_press", val)

    def c_reset(self):
        d = self.cg.DEFAULTS
        for key in ("scale", "dot", "link", "outline", "wait_ms"):
            self.c_w[key].set_value(d[key])
        self.c_w["ring"].set_value(d["ring"] * 100)
        self.c_w["color"].set_selected([k for k, _ in CURSOR_COLORS].index(d["color"]))
        self.tab.notify("Cursor zurückgesetzt")

    # ---------- Waybar ----------
    def waybar_cards(self):
        c = self.card("Waybar")
        self.wb_state = Gtk.Label(label="…", xalign=0, wrap=True)
        self.wb_state.add_css_class("key")
        c.append(self.wb_state)
        row = Gtk.Box(spacing=8)
        self.wb_btn = self.button("neu starten", self.waybar_restart, "Waybar beenden und frisch starten")
        row.append(self.wb_btn)
        c.append(row)
        n = Gtk.Label(label="Startet Waybar mit der aktuellen Konfiguration neu – auch wenn sie abgestürzt, "
                            "eingefroren oder verschwunden ist. Ausgabe: $XDG_RUNTIME_DIR/waybar.log", xalign=0, wrap=True)
        n.add_css_class("note")
        c.append(n)
        self.wb_crashes = self.card("Letzte Abstürze")
        self.waybar_refresh()
        # Status regelmäßig aktualisieren, solange die Seite sichtbar ist
        self._wb_tick = None
        self.connect("map", lambda *_: self._wb_watch(True))
        self.connect("unmap", lambda *_: self._wb_watch(False))

    def _wb_watch(self, on):
        if self._wb_tick:
            GLib.source_remove(self._wb_tick)
            self._wb_tick = None
        if on:
            self._wb_tick = GLib.timeout_add_seconds(3, lambda: (self.waybar_refresh(), True)[-1])

    @staticmethod
    def waybar_pid():
        out = sh(["pgrep", "-x", "waybar"]).split()
        return int(out[0]) if out else None

    def waybar_read(self):
        pid = self.waybar_pid()
        since = None
        if pid:
            try:
                ticks = int(open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[19])
                boot = float(open("/proc/uptime").read().split()[0])
                since = boot - ticks / os.sysconf("SC_CLK_TCK")
            except (OSError, ValueError, IndexError):
                pass
        crashes = []
        try:
            crashes = json.loads(sh(["coredumpctl", "list", "--json=short", "--no-pager", "waybar"]) or "[]")
        except ValueError:
            pass
        return pid, since, crashes[-5:][::-1]

    def waybar_refresh(self):
        self.run(self.waybar_read, self.waybar_show)

    def waybar_show(self, r):
        if isinstance(r, Exception) or not self.wb_state.get_root():
            return
        pid, since, crashes = r
        if pid:
            if since is None:
                t = ""
            elif since < 90:
                t = f" · seit {since:.0f} s"
            elif since < 5400:
                t = f" · seit {since / 60:.0f} min"
            else:
                t = f" · seit {since / 3600:.1f} h"
            self.wb_state.set_label(f"● läuft (PID {pid}){t}")
            self.wb_btn.set_label("neu starten")
        else:
            self.wb_state.set_label("○ läuft nicht")
            self.wb_btn.set_label("starten")
        box = self.wb_crashes
        while (ch_ := box.get_first_child()):
            box.remove(ch_)
        if not crashes:
            l = Gtk.Label(label="Keine Abstürze aufgezeichnet.", xalign=0)
            l.add_css_class("note")
            box.append(l)
            return
        sigs = {6: "SIGABRT", 9: "SIGKILL", 11: "SIGSEGV", 7: "SIGBUS"}
        for cr in crashes:
            when = time.strftime("%d.%m. %H:%M:%S", time.localtime(cr.get("time", 0) / 1e6))
            self.line(box, when, f"{sigs.get(cr.get('sig'), 'Signal %s' % cr.get('sig'))} · PID {cr.get('pid')}")
        n = Gtk.Label(label="Häufigste Ursache: GTK3 behält nach einem Cursor-Theme-Wechsel (Super+W, Cursor-Seite) "
                            "verwaiste Cursor und stürzt beim nächsten Hovern ab. Seit 19.10. behoben: Apps behalten "
                            "den festen Theme-Namen „ist-punkt“, Waybar-Module nutzen Hyprlands Cursor.", xalign=0, wrap=True)
        n.add_css_class("note")
        box.append(n)

    def waybar_restart(self):
        self.wb_btn.set_sensitive(False)
        self.wb_state.set_label("◌ startet neu …")

        def work():
            if os.access(WAYBAR_RESTART, os.X_OK):
                subprocess.run([WAYBAR_RESTART], timeout=10)
            else:  # Weitergabe ohne die Rice-Skripte
                subprocess.run(["pkill", "-x", "waybar"])
                time.sleep(0.4)
                subprocess.run(["pkill", "-KILL", "-x", "waybar"])
                subprocess.Popen(["waybar"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 stdin=subprocess.DEVNULL, start_new_session=True)
                time.sleep(0.5)
            return self.waybar_pid()

        def done(pid):
            self.wb_btn.set_sensitive(True)
            self.tab.notify("Waybar läuft wieder" if isinstance(pid, int) else "Waybar ließ sich nicht starten – siehe waybar.log")
            self.waybar_refresh()
        self.run(work, done)
