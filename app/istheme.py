# Shared design of the whole suite (notist, scourge, filist, datist, mailist, …).
#   · choice in ~/.config/ist-theme.json: {"theme": "auto" | <key>, "mode": "dark" | "light"}
#     – applies to all apps; a change in one app's settings takes effect live in all open ones.
#   · "auto" = colors of the desktop (matugen, ~/.cache/matugen/colors.css), otherwise the default design.
#     "mode" picks between dark and light; if the template does not fit, the scheme is derived from
#     the matugen hues.
#   · picker(...) provides the choice for the settings (cards + light/dark).
import colorsys
import json
import os
import re
import time

CONFIG = os.environ.get("IST_THEME_FILE") or os.path.expanduser("~/.config/ist-theme.json")
MATUGEN_CSS = os.path.expanduser("~/.cache/matugen/colors.css")

THEMES = {   # key: (name, light?, background, text, accent, surface, second color, third color)
    "nacht": ("Nacht", False, "#0f1417", "#dfe3e7", "#8fcef3", "#1c2023", "#b5c9d7", "#cac1ea"),
    "glut": ("Glut", False, "#1a120d", "#f0dfd7", "#ffb68e", "#271e19", "#e7bea9", "#cdc78f"),
    "wald": ("Wald", False, "#0f1511", "#dde5dc", "#8fd6a0", "#1a221c", "#b7ccb8", "#a3cddb"),
    "rose": ("Rosé", False, "#181114", "#f0dee4", "#ffb0cd", "#241a1e", "#e0bdc8", "#f0bc95"),
    "lavendel": ("Lavendel", False, "#13121a", "#e5e1ec", "#c8bfff", "#1e1c26", "#c9c3dc", "#ecb8cd"),
    "mitternacht": ("Mitternacht", False, "#000000", "#e8e8e8", "#7cc4ff", "#121212", "#b0c4d4", "#d0b0ff"),
    "papier": ("Papier", True, "#f6f4ef", "#1d1b18", "#3a6ea5", "#ffffff", "#5d6b78", "#7b5aa6"),
    "sand": ("Sand", True, "#fbf3e8", "#2b2118", "#a85a1c", "#fffaf3", "#7a6352", "#5d7a3a"),
    "minze": ("Minze", True, "#eff6f1", "#17221b", "#2e7d4f", "#ffffff", "#4f6a5a", "#2f6f86"),
}
DEFAULT = "nacht"


# ---------- read / write the choice ----------
def load():
    st = {"theme": "auto", "mode": "dark"}
    try:
        with open(CONFIG, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            if d.get("theme") == "auto" or d.get("theme") in THEMES:
                st["theme"] = d["theme"]
            if d.get("mode") in ("dark", "light"):
                st["mode"] = d["mode"]
    except (OSError, ValueError):
        pass
    return st


def exists():
    return os.path.isfile(CONFIG)


def save(theme=None, mode=None, at=None):
    st = load()
    if theme is not None:
        st["theme"] = theme
    if mode is not None:
        st["mode"] = mode
    if at:
        st["at"] = at     # shared switch time (without the switch animation)
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    tmp = CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f)
    os.replace(tmp, CONFIG)
    return st


# ---------- switch animation (scan bar) ----------
SWIPE_FILE = os.path.join(os.path.dirname(CONFIG), "ist-swipe.json")   # own file: sliders do not trigger a design change


def swipe_cfg():
    cfg = {"on": True, "sec": 1.5}
    try:
        with open(SWIPE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            cfg["on"] = bool(d.get("on", True))
            cfg["sec"] = min(4.0, max(0.5, float(d.get("sec", 1.5))))
    except (OSError, ValueError, TypeError):
        pass
    return cfg


def swipe_set(on=None, sec=None):
    cfg = swipe_cfg()
    if on is not None:
        cfg["on"] = bool(on)
    if sec is not None:
        cfg["sec"] = min(4.0, max(0.5, float(sec)))
    os.makedirs(os.path.dirname(SWIPE_FILE), exist_ok=True)
    with open(SWIPE_FILE + ".tmp", "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    os.replace(SWIPE_FILE + ".tmp", SWIPE_FILE)


def _swipe_script():
    for d in (os.path.dirname(os.path.abspath(__file__)), os.path.expanduser("~/.local/share/rice-apps")):
        p = os.path.join(d, "ist-swipe.py")
        if os.path.isfile(p):
            return p
    return None


def switch(theme=None, mode=None):
    """Switch design/mode – with the scan-bar animation (ist-swipe.py) if enabled and possible, otherwise
    immediately (all windows at the same moment). Returns True if the animation took over."""
    st = load()
    if (theme is None or theme == st["theme"]) and (mode is None or mode == st["mode"]):
        return False
    script = _swipe_script()
    if swipe_cfg()["on"] and script and os.environ.get("WAYLAND_DISPLAY"):
        import shutil
        import subprocess
        lib = "/usr/lib/libgtk4-layer-shell.so"
        if shutil.which("grim") and os.path.exists(lib):
            cmd = ["python3", script] + (["--theme", theme] if theme else []) + (["--mode", mode] if mode else [])
            env = dict(os.environ, LD_PRELOAD=lib)
            try:
                subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
                return True
            except OSError:
                pass
    save(theme, mode, at=time.time() + 0.45)
    return False


# ---------- colors ----------
def _rgb(h):
    return tuple(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))


def _hex(r, g, b):
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c * 255))) for c in (r, g, b))


def _hls(l, s, hue):
    return _hex(*colorsys.hls_to_rgb(hue, max(0, min(1, l)), max(0, min(1, s))))


def _build(bg, fg, acc, surf, sec, ter, light, on_primary=None):
    outline = "#7a7a7a" if light else "#8a9297"
    on = on_primary or bg
    lines = [f"@define-color background {bg};", f"@define-color foreground {fg};", f"@define-color cursor {fg};",
             f"@define-color primary {acc};", f"@define-color on_primary {on};", f"@define-color secondary {sec};",
             f"@define-color tertiary {ter};", f"@define-color surface_container {surf};",
             f"@define-color outline {outline};",
             f"@define-color color0 {bg};", f"@define-color color1 {acc};", f"@define-color color2 {sec};",
             f"@define-color color3 {ter};", f"@define-color color4 {acc};", f"@define-color color5 {sec};",
             f"@define-color color6 {ter};", f"@define-color color7 {fg};", f"@define-color color8 {outline};",
             f"@define-color color9 {acc};", f"@define-color color10 {sec};", f"@define-color color11 {ter};",
             f"@define-color color12 {acc};", f"@define-color color13 {ter};", f"@define-color color14 {sec};",
             f"@define-color color15 {fg};"]
    return "\n".join(lines) + "\n"


def css(key):
    name, light, bg, fg, acc, surf, sec, ter = THEMES.get(key, THEMES[DEFAULT])
    return _build(bg, fg, acc, surf, sec, ter, light)


def is_light(key):
    return THEMES.get(key, THEMES[DEFAULT])[1]


def parse(text):
    return {n: v for n, v in re.findall(r"@define-color\s+(\w+)\s+(#[0-9a-fA-F]{6})", text)}


def luminance(hexcol):
    r, g, b = (int(hexcol[i:i + 2], 16) for i in (1, 3, 5))
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


def derive(base_css, light):
    """Light or dark scheme with the hues of the template (matugen only delivers the scheme that fits the image)."""
    c = parse(base_css)
    hue = {}
    for k, dflt in (("primary", "#8fcef3"), ("secondary", "#b5c9d7"), ("tertiary", "#cac1ea")):
        r, g, b = _rgb(c.get(k, dflt))
        h, _l, s = colorsys.rgb_to_hls(r, g, b)
        hue[k] = (h, s)
    hp, sp = hue["primary"]
    hs, ss = hue["secondary"]
    ht, st = hue["tertiary"]
    if light:
        return _build(_hls(.965, .30, hp), _hls(.10, .18, hp), _hls(.33, min(.8, max(.45, sp)), hp),
                      _hls(.925, .26, hp), _hls(.36, min(.35, ss), hs), _hls(.38, min(.45, max(.25, st)), ht),
                      True, on_primary="#ffffff")
    return _build(_hls(.075, .20, hp), _hls(.89, .10, hp), _hls(.80, min(.9, max(.55, sp)), hp),
                  _hls(.11, .16, hp), _hls(.80, min(.30, ss), hs), _hls(.80, min(.45, max(.25, st)), ht),
                  False, on_primary=_hls(.16, .85, hp))


def resolve(state=None):
    """(css, light?) for the current choice."""
    st = state or load()
    key = st["theme"]
    if key in THEMES:
        return css(key), is_light(key)
    want_light = st["mode"] == "light"
    try:
        base = open(MATUGEN_CSS, encoding="utf-8").read()
        bg = parse(base).get("background")
    except OSError:
        base, bg = "", None
    if not bg:
        base = css(DEFAULT)
        bg = parse(base)["background"]
    is_l = luminance(bg) > .55
    if is_l == want_light:
        return base, is_l
    return derive(base, want_light), want_light


def apply_gtk(light):
    """GTK base theme (selection lists, menus, scrollbars) matching light/dark."""
    try:
        from gi.repository import Gtk
        st = Gtk.Settings.get_default()
        if st.find_property("gtk-interface-color-scheme") is not None:      # GTK ≥ 4.20: die alte Eigenschaft warnt
            st.set_property("gtk-interface-color-scheme",
                            Gtk.InterfaceColorScheme.LIGHT if light else Gtk.InterfaceColorScheme.DARK)
        else:
            st.set_property("gtk-application-prefer-dark-theme", not light)
    except Exception:
        pass


SWITCH_PREP = 0.22      # s before the switch time the apps change their colors under the still image


def switch_delay():
    """Seconds until the shared switch time "at" (epoch) in the selection file – so all
    windows and bars change at the same moment. Without "at" (or expired): 0."""
    try:
        with open(CONFIG, encoding="utf-8") as f:
            at = float(json.load(f).get("at") or 0)
    except (OSError, ValueError, AttributeError, TypeError):
        return 0.0
    return min(max(at - time.time(), 0.0), 1.0)


def watch(callback):
    """Calls callback() when the choice changes (also from another app). Returns the monitor."""
    from gi.repository import Gio, GLib
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    mon = Gio.File.new_for_path(os.path.dirname(CONFIG)).monitor_directory(Gio.FileMonitorFlags.NONE, None)
    pending = [0]

    at_ = [0.0]

    def fire():
        pending[0] = 0
        fade = None
        try:
            import sys
            g = sys.modules.get("glass")
            fade = g.theme_fade_begin() if g and hasattr(g, "theme_fade_begin") else None
        except Exception:      # noqa: BLE001 – the cross-fade is decoration
            fade = None
        callback()
        if fade:
            fade(at_[0])     # the still image only fades out at the shared time – until then the switch is covered
        return False

    def changed(_m, f, _o, _ev):
        if f.get_basename() == os.path.basename(CONFIG) and not pending[0]:
            d = switch_delay()
            at_[0] = time.time() + d if d > 0 else 0.0
            # The colors are already changed under the still image BEFORE the time (SWITCH_PREP s), so even a
            # slow reload of the styles (large CSS) no longer shifts anything.
            pending[0] = GLib.timeout_add(int(max(0.05, d - SWITCH_PREP) * 1000), fire)
    mon.connect("changed", changed)
    return mon


# ---------- choice for the settings ----------
PICKER_CSS = """
button.theme-card { background: none; background-image: none; border: 1px solid alpha(@primary, 0.25);
    border-radius: 6px; padding: 6px; box-shadow: none; color: @foreground; }
button.theme-card label { font-size: 12px; font-weight: bold; }
button.theme-card:hover { border-color: alpha(@primary, 0.7); background-color: alpha(@primary, 0.06); }
button.theme-card:checked { border-color: @primary; background-color: alpha(@primary, 0.18); color: @foreground; }
.theme-mode { margin-top: 6px; }
.theme-mode button { background: none; background-image: none; box-shadow: none; color: @foreground;
    border: 1px solid alpha(@primary, 0.25); border-radius: 6px; padding: 3px 12px; }
.theme-mode button:hover { border-color: alpha(@primary, 0.7); }
.theme-mode button:checked { border-color: @primary; background-color: alpha(@primary, 0.18); }
"""
_picker_provider = None


def _swatch(cols):
    from gi.repository import Gtk
    import math
    sw = Gtk.DrawingArea(content_width=96, content_height=40)

    def draw(area, cr, w, h):
        if not cols:      # automatic: hint of a rainbow
            for i, c in enumerate(("#8fcef3", "#ffb68e", "#8fd6a0", "#ffb0cd")):
                cr.set_source_rgb(*_rgb(c))
                cr.rectangle(i * w / 4, 0, w / 4 + 1, h)
                cr.fill()
            return
        bgc, sc, ac, fc = (_rgb(c) for c in cols)
        cr.set_source_rgb(*bgc)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        cr.set_source_rgb(*sc)
        cr.rectangle(w * 0.35, h * 0.18, w * 0.58, h * 0.64)
        cr.fill()
        cr.set_source_rgb(*ac)
        cr.arc(w * 0.18, h / 2, h * 0.22, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgb(*fc)
        cr.rectangle(w * 0.42, h * 0.38, w * 0.4, 3)
        cr.rectangle(w * 0.42, h * 0.56, w * 0.28, 3)
        cr.fill()
    sw.set_draw_func(draw)
    return sw


def picker(tr=lambda s: s, on_change=None, columns=5):
    """Cards for "Automatic" + all designs and – with "Automatic" – light/dark. Saves immediately in
    ~/.config/ist-theme.json (all apps reload live); on_change() afterwards for the app's own reload.
    tr = translator of the app for the visible texts."""
    global _picker_provider
    from gi.repository import Gdk, Gtk
    if _picker_provider is None:
        _picker_provider = Gtk.CssProvider()
        _picker_provider.load_from_string(PICKER_CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), _picker_provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER)
    st = load()
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=columns, min_children_per_line=min(2, columns),
                       column_spacing=8, row_spacing=8, homogeneous=True)
    state = {"busy": False}
    btns = {}
    group = None
    opts = [("auto", (tr("Automatisch"), False, None, None, None, None))] + list(THEMES.items())
    for key, (name, light, bg, fg, acc, surf, *_r) in opts:
        b = Gtk.ToggleButton(active=st["theme"] == key)
        b.add_css_class("theme-card")
        if group:
            b.set_group(group)
        group = group or b
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        v.append(_swatch([bg, surf, acc, fg] if bg else None))
        v.append(Gtk.Label(label=(tr(name) if key != "auto" else name) + (" ☀" if light else "")))
        b.set_child(v)
        flow.append(b)
        btns[key] = b
    outer.append(flow)

    mode = Gtk.Box(spacing=6, halign=Gtk.Align.START)
    mode.add_css_class("theme-mode")
    mode.append(Gtk.Label(label=tr("Automatisch in")))
    dark_b = Gtk.ToggleButton(label=tr("Dunkel"), active=st["mode"] == "dark")
    light_b = Gtk.ToggleButton(label=tr("Hell"), active=st["mode"] == "light", group=dark_b)
    mode.append(dark_b)
    mode.append(light_b)
    mode.set_visible(st["theme"] == "auto")
    outer.append(mode)

    cfg = swipe_cfg()
    anim = Gtk.Box(spacing=8, halign=Gtk.Align.START)
    anim.add_css_class("theme-mode")
    anim.append(Gtk.Label(label=tr("Wechsel-Animation")))
    onsw = Gtk.Switch(active=cfg["on"], valign=Gtk.Align.CENTER)
    anim.append(onsw)
    dur = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.5, 3.0, 0.1)
    dur.set_draw_value(False)
    dur.set_value(cfg["sec"])
    dur.set_size_request(150, -1)
    dur.set_sensitive(cfg["on"])
    durl = Gtk.Label(label=("%.1f s" % cfg["sec"]).replace(".", ","))
    anim.append(dur)
    anim.append(durl)
    outer.append(anim)

    def anim_on(sw, _p):
        swipe_set(on=sw.get_active())
        dur.set_sensitive(sw.get_active())

    def anim_dur(sc):
        v = round(sc.get_value(), 1)
        durl.set_text(("%.1f s" % v).replace(".", ","))
        swipe_set(sec=v)
    onsw.connect("notify::active", anim_on)
    dur.connect("value-changed", anim_dur)

    def changed():
        if on_change:
            on_change()

    for key, b in btns.items():
        def tog(btn, k=key):
            if btn.get_active() and not state["busy"]:
                switch(theme=k)
                mode.set_visible(k == "auto")
                changed()
        b.connect("toggled", tog)

    def mtog(btn, m):
        if btn.get_active() and not state["busy"]:
            switch(mode=m)
            changed()
    dark_b.connect("toggled", mtog, "dark")
    light_b.connect("toggled", mtog, "light")
    return outer
