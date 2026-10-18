# System- & Hardware-Einstellungen für Scourge (Startseite → "System-Einstellungen").
# Stellt echte Regler bereit: Helligkeit, Lautstärke (Ein-/Ausgabe + Gerätewahl), Maus-Sensitivität
# und -Beschleunigung, Touchpad, Tastaturlayout, WLAN und Bluetooth.
# Hyprland-Einstellungen werden sofort angewandt (hyprctl keyword) und in die hyprland.lua geschrieben,
# damit sie einen Neustart überleben. Ton/Helligkeit speichert das System selbst.
import json, os, re, subprocess

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

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


def hypr_set(opt, value):
    sh(["hyprctl", "keyword", opt, str(value)])


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
    "display": ("Bildschirm", "Helligkeit des Displays."),
    "audio": ("Ton", "Lautstärke, Mikrofon und Ausgabegerät."),
    "input": ("Eingabe", "Maus-Empfindlichkeit, Touchpad und Tastaturlayout."),
    "radio": ("Funk", "WLAN und Bluetooth."),
}


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
            "display": [self.display_card],
            "audio": [self.audio_card],
            "input": [self.mouse_card, self.keyboard_card],
            "radio": [self.radio_card],
        }
        for b in builders.get(self.section, [self.display_card, self.audio_card, self.mouse_card,
                                             self.keyboard_card, self.radio_card]):
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
