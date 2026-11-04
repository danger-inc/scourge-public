# Animationen: Desktop-Wechsel (Hyprland workspaces-Animation) und die Pille in der Waybar.
# Einstellungen liegen in ~/.config/ist-anim.json. Anwenden =
#   1. live per `hyprctl eval` (hl.curve / hl.animation),
#   2. dauerhaft als Block zwischen "-- ws-anim begin/end" in der hyprland.lua,
#   3. Waybar-Pille: ~/.config/waybar/variants/ws-anim.css (Waybar lädt das Stylesheet selbst neu).
import json, os, re, subprocess

CONF = os.path.expanduser("~/.config/ist-anim.json")
HYPR_CONF = os.path.expanduser("~/.config/hypr/hyprland.lua")
WAYBAR_CSS = os.path.expanduser("~/.config/waybar/variants/ws-anim.css")

# Kurven: Name → Lua-Definition (Hyprland-Kurven; "spring" = physikalische Feder)
CURVES = {
    "sanft":   ("wsSoft",   '{ type = "bezier", points = { {0.22, 1}, {0.36, 1} } }'),
    "gleiten": ("wsGlide",  '{ type = "bezier", points = { {0.32, 0.72}, {0, 1} } }'),
    "weich":   ("wsEase",   '{ type = "bezier", points = { {0.45, 0}, {0.15, 1} } }'),
    "straff":  ("wsStiff",  '{ type = "spring", mass = 1, stiffness = 250, dampening = 28, damping = 28 }'),
    "federnd": ("wsSpring", '{ type = "spring", mass = 1, stiffness = 120, dampening = 19, damping = 19 }'),
    "original": ("wsOrig",  '{ type = "bezier", points = { {0.5, 0.5}, {0.75, 1} } }'),
    "linear":  ("wsLinear", '{ type = "bezier", points = { {0, 0}, {1, 1} } }'),
}
CURVE_LABELS = {"sanft": "Sanft (bremst weich ab)", "gleiten": "Gleiten", "weich": "Weich (an & aus)",
                "straff": "Straffe Feder", "federnd": "Weiche Feder", "original": "Original (Hyprland-Standard)", "linear": "Linear"}
STYLES = [("slide", "Schieben"), ("slidefade", "Schieben + Überblenden"), ("slidevert", "Senkrecht schieben"),
          ("fade", "Überblenden")]

PILL_MODES = [("fade", "Überblenden"), ("trail", "Nachleuchten"), ("spring", "Federn (Feld wächst)"),
              ("glide", "Gleiten"), ("glidespring", "Gleiten mit Federn")]
DEFAULTS = {"ws_on": True, "ws_style": "slide", "ws_pct": 20, "ws_ms": 500, "ws_curve": "straff",
            "pill_on": True, "pill_ms": 260, "pill_mode": "spring"}
PRESETS = {   # Name → Werte, die gesetzt werden
    "Straffe Feder": {"ws_style": "slide", "ws_pct": 20, "ws_ms": 500, "ws_curve": "straff", "pill_mode": "spring", "pill_ms": 260},
    "Wischen":  {"ws_style": "slide", "ws_pct": 20, "ws_ms": 300, "ws_curve": "sanft"},
    "Original": {"ws_style": "fade", "ws_pct": 20, "ws_ms": 190, "ws_curve": "original"},
    "Sanft":   {"ws_style": "fade", "ws_pct": 20, "ws_ms": 300, "ws_curve": "weich"},
    "Schieben": {"ws_style": "slidefade", "ws_pct": 20, "ws_ms": 450, "ws_curve": "sanft"},
    "Flink":   {"ws_style": "slide", "ws_pct": 20, "ws_ms": 250, "ws_curve": "sanft"},
    "Federnd": {"ws_style": "slide", "ws_pct": 20, "ws_ms": 500, "ws_curve": "federnd"},
    "Blenden": {"ws_style": "fade", "ws_pct": 20, "ws_ms": 300, "ws_curve": "weich"},
}


def load():
    cfg = dict(DEFAULTS)
    try:
        with open(CONF, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            for k, v in d.items():
                if k in cfg and type(v) is type(cfg[k]):
                    cfg[k] = v
    except (OSError, ValueError):
        pass
    if cfg["ws_curve"] not in CURVES:
        cfg["ws_curve"] = DEFAULTS["ws_curve"]
    if cfg["pill_mode"] not in dict(PILL_MODES):
        cfg["pill_mode"] = DEFAULTS["pill_mode"]
    if cfg["ws_style"] not in dict(STYLES):
        cfg["ws_style"] = DEFAULTS["ws_style"]
    return cfg


def save(cfg):
    os.makedirs(os.path.dirname(CONF), exist_ok=True)
    with open(CONF + ".tmp", "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=1)
    os.replace(CONF + ".tmp", CONF)


def _style(cfg):
    s = cfg["ws_style"]
    return f"{s} {int(cfg['ws_pct'])}%" if s == "slidefade" else s


def lua_block(cfg):
    """Die Zeilen für die hyprland.lua (Kurven + workspaces-Blätter)."""
    name, definition = CURVES[cfg["ws_curve"]]
    speed = round(cfg["ws_ms"] / 100, 2)       # Hyprland: Einheit = 100 ms
    key = "spring" if definition.startswith('{ type = "spring"') else "bezier"
    lines = [f'hl.curve("{name}", {definition})']
    for leaf in ("workspaces", "workspacesIn", "workspacesOut"):
        lines.append(f'hl.animation({{ leaf = "{leaf}", enabled = {"true" if cfg["ws_on"] else "false"}, '
                     f'speed = {speed}, {key} = "{name}", style = "{_style(cfg)}" }})')
    return lines


MARK = re.compile(r"-- ws-anim begin[^\n]*\n.*-- ws-anim end\n", re.S)   # gierig: räumt auch Reste älterer Läufe auf
OLD = re.compile(r'(?:hl\.animation\(\{ leaf = "workspaces(?:In|Out)?",[^\n]*\}\)\n){3}')


def persist(cfg):
    try:
        text = open(HYPR_CONF, encoding="utf-8").read()
    except OSError:
        return False
    block = "-- ws-anim begin (Scourge → Animationen)\n" + "\n".join(lua_block(cfg)) + "\n-- ws-anim end\n"
    new, n = MARK.subn(lambda m: block, text, count=1)
    if not n:
        new, n = OLD.subn(lambda m: block, text, count=1)
    if not n:
        return False
    if new != text:
        try:
            open(HYPR_CONF, "w", encoding="utf-8").write(new)
        except OSError:
            return False
    return True


def apply_live(cfg):
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return
    try:
        subprocess.run(["hyprctl", "eval", "\n".join(lua_block(cfg))], capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


SLOT_W = 28.0      # Breite eines Desktop-Felds in der Waybar (px): Padding + eine Ziffer (gerade Zahl gegen Nähte)
GLYPH_W = 8.4      # Breite einer Ziffer (JetBrainsMono 14 px)


def waybar_css(cfg):
    """Übergang der Desktop-Felder in der Waybar (workspace.py gibt je Feld active / pl / pr aus).
    fade/trail: Farbübergang. glide/spring: jedes Feld hat eine Füllung, die per background-position hinein- und
    hinausgeschoben wird – die Pille wirkt, als wanderte sie von Feld zu Feld (Felder links vom aktiven parken
    ihre Füllung rechts = zur Pille hin, Felder rechts davon links)."""
    base = int(cfg["pill_ms"])
    mode = cfg["pill_mode"]
    ids = [f"#custom-ws{i}" for i in range(1, 5)]
    if not cfg["pill_on"] or base <= 0:
        css_body = ", ".join(ids) + " { transition: none; }\n"
    elif mode in ("glide", "glidespring"):
        # Ein durchgehender Pillenkörper: jedes Feld zeichnet dieselbe Pille (Füllung + 4 Eckmasken in Leistenfarbe),
        # verschoben um d Feldbreiten (d = aktives Feld - dieses Feld). Felder ohne Lücke → die Pille setzt sich an
        # der Grenze zweier Felder nahtlos zusammen, während sie wandert.
        ease = "cubic-bezier(0.22, 1, 0.36, 1)" if mode == "glide" else "cubic-bezier(0.34, 1.56, 0.64, 1)"
        ms = round(base * 1.5)
        S, R = SLOT_W, 10
        w = [f"window#waybar {i}" for i in ids]

        def circ(cx, cy):
            return f"radial-gradient(circle at {cx}px {cy}px, transparent {R - 0.5}px, @surface_container {R + 0.5}px)"

        layers = ", ".join([circ(R, R), circ(0, R), circ(R, 0), circ(0, 0), "linear-gradient(@primary, @primary)"])
        sizes = ", ".join([f"{R}px {R}px"] * 4 + [f"{S}px 100%"])

        def pos(x):
            xs = [x, x + S - R, x, x + S - R, x]
            ys = ["0", "0", "100%", "100%", "0"]
            return ", ".join(f"{a:g}px {b}" for a, b in zip(xs, ys))
        classes = {"p0": 0}
        for d in range(1, 4):
            classes[f"pp{d}"] = d * S
            classes[f"pm{d}"] = -d * S
        css_body = (", ".join(w) + " { margin: 3px 0; padding: 0 " + f"{(S - GLYPH_W) / 2:g}" + "px; border-radius: 0; min-width: 0; "
                    "background-color: transparent; background-image: " + layers + "; background-repeat: no-repeat; "
                    "background-size: " + sizes + "; background-position: " + pos(0) + "; "
                    "transition: background-position " + f"{ms}ms {ease}" + ", color 60ms ease-out; }\n")
        for cls, x in classes.items():
            css_body += ", ".join(f"{x_}.{cls}" for x_ in w) + " { background-position: " + pos(x) + "; }\n"
        # Zahl auf der Pille: Farbe wechselt erst, wenn die Pille ankommt (abgehende Zahlen wechseln sofort, s. o.)
        css_body += ", ".join(x_ + ".p0" for x_ in w) + (" { transition: background-position " + f"{ms}ms {ease}"
                     + ", color 80ms ease " + f"{round(ms * 0.3)}ms" + "; }\n")
        css_body += (", ".join(x_ + ":hover" for x_ in w) + " { background-color: transparent; }\n"
                     + ", ".join(x_ + ".active" for x_ in w) + " { background-color: transparent; box-shadow: none; }\n")
    else:
        # fade / trail / spring (= Variante C der Vorschau: Farbe federt über, das aktive Feld wächst um 5 px).
        # Das Wachsen läuft über padding in GANZEN Pixelstufen (steps): GTK rundet Zwischenwerte zweier gegenläufiger
        # Paddings sonst einzeln ab, und die Leiste wurde beim Wechsel 1–2 px schmaler. Mit ganzzahligen Stufen bleibt
        # die Summe aus schrumpfendem und wachsendem Feld immer gleich. Schatten/Unterstrich entfallen (Variante C hat
        # keinen; GTK blendet Schatten beim Verlassen nicht sauber aus).
        ease, grow = "cubic-bezier(0.22, 1, 0.36, 1)", 0
        t_in = t_out = base
        if mode == "trail":
            t_in, t_out = round(base * 0.46), round(base * 2.3)
        elif mode == "spring":
            ease, grow = "cubic-bezier(0.34, 1.56, 0.64, 1)", 5
            t_in = t_out = round(base * 1.23)
        w = [f"window#waybar {i}" for i in ids]

        def tr(ms):
            t = f"background-color {ms}ms {ease}, color {ms}ms {ease}"
            if grow:
                t += f", padding-left {ms}ms steps({grow}, end), padding-right {ms}ms steps({grow}, end)"
            return t
        pad_on = f" padding-left: {11 + grow}px; padding-right: {11 + grow}px;" if grow else ""
        css_body = (", ".join(w) + " { background-color: alpha(@primary, 0); box-shadow: none; transition: " + tr(t_out) + "; }\n"
                    + ", ".join(x + ":hover" for x in w) + " { background-color: alpha(@primary, 0.2); }\n"
                    + ", ".join(x + ".active" for x in w) + " { background-color: @primary; box-shadow: none;" + pad_on
                    + " transition: " + tr(t_in) + "; }\n")
    css = "/* generiert von Scourge → Animationen (anim.py) – nicht von Hand ändern */\n" + css_body
    try:
        os.makedirs(os.path.dirname(WAYBAR_CSS), exist_ok=True)
        if not os.path.exists(WAYBAR_CSS) or open(WAYBAR_CSS, encoding="utf-8").read() != css:
            open(WAYBAR_CSS, "w", encoding="utf-8").write(css)
            return True
    except OSError:
        pass
    return False


def apply(cfg, live=True):
    save(cfg)
    persist(cfg)
    if live:
        apply_live(cfg)
    if waybar_css(cfg):
        # Waybars Live-Nachladen wendet geänderte Regeln nicht vollständig an (alte Unterstriche/Übergänge bleiben):
        # kurz entprellt neu starten
        script = os.path.expanduser("~/.config/waybar/scripts/waybar-restart.sh")
        if os.path.exists(script) and subprocess.run(["pgrep", "-x", "waybar"], capture_output=True).returncode == 0:
            subprocess.Popen([script, "--soon", "1.0"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, start_new_session=True)
