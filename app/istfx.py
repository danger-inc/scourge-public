#!/usr/bin/env python3
# istfx – Terminal-Visuals in den Farben des aktuellen Hintergrunds (matugen, Super+W).
# Ohne Zusatzpakete. Beenden mit q, Esc oder Strg+C. Liste: `istfx` ohne Argument.
import functools, math, os, random, re, select, shutil, signal, sys, termios, time, tty

EFFECTS = {}


def effect(name, desc):
    def deco(fn):
        EFFECTS[name] = (fn, desc)
        return fn
    return deco


# ---------------- Farben ----------------
COLORS_CSS = os.path.expanduser("~/.cache/matugen/colors.css")


def palette():
    cols = {"background": "#1a1b26", "foreground": "#c0caf5", "primary": "#7aa2f7", "secondary": "#bb9af7",
            "tertiary": "#9ece6a", "outline": "#565f89"}
    try:
        txt = open(COLORS_CSS).read()
        for k, v in re.findall(r"@define-color\s+(\w+)\s+(#[0-9a-fA-F]{6})", txt):
            cols.setdefault(k, v)
            cols[k] = v
    except OSError:
        pass
    return {k: tuple(int(v[i:i + 2], 16) for i in (1, 3, 5)) for k, v in cols.items()}


P = palette()
GEN = [0]             # zählt Farbwechsel; Effekte mit vorberechneten Verläufen fragen das ab


def colors_mtime():
    try:
        return os.stat(COLORS_CSS).st_mtime_ns
    except OSError:
        return 0


def reload_palette():
    """Super+W während ein Effekt läuft: neue Farben übernehmen (P bleibt dasselbe Objekt)."""
    new = palette()
    if len(new) < 6 or new == P:
        return False
    P.clear()
    P.update(new)
    GEN[0] += 1
    return True


def cached(fn):
    """Wert aus den Farben berechnen und bis zum nächsten Farbwechsel merken."""
    box = [None, None]

    def get():
        if box[0] != GEN[0]:
            box[0], box[1] = GEN[0], fn()
        return box[1]
    return get


def mix(a, b, t):
    t = max(0.0, min(1.0, t))
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def ramp(t, stops):
    """t in 0..1 über mehrere Farben verlaufen lassen (in 64 Stufen, damit Farbcodes sich wiederholen)."""
    t = int(max(0.0, min(0.9999, t)) * 64) / 64 * (len(stops) - 1)
    i = int(t)
    return mix(stops[i], stops[i + 1], t - i)


@functools.lru_cache(maxsize=4096)
def fg(c):
    return f"\x1b[38;2;{c[0]};{c[1]};{c[2]}m"


@functools.lru_cache(maxsize=4096)
def bg(c):
    return f"\x1b[48;2;{c[0]};{c[1]};{c[2]}m"


def half(top, bot, prev):
    """Halbblock-Zelle; Farbcodes nur, wenn sie sich ändern. prev = [fg, bg] der vorigen Zelle."""
    out = ""
    if top != prev[0]:
        out += fg(top)
        prev[0] = top
    if bot != prev[1]:
        out += bg(bot)
        prev[1] = bot
    return out + "▀"


# ---------------- Terminal ----------------
class Term:
    def __enter__(self):
        self.fd = sys.stdin.fileno()
        self.old = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)
        sys.stdout.write("\x1b[?1049h\x1b[?25l\x1b[2J")
        sys.stdout.flush()
        self.resized = True
        signal.signal(signal.SIGWINCH, lambda *_: setattr(self, "resized", True))
        return self

    def __exit__(self, *a):
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)
        sys.stdout.write("\x1b[0m\x1b[?25h\x1b[?1049l")
        sys.stdout.flush()

    def size(self):
        s = shutil.get_terminal_size((80, 24))
        return s.columns, s.lines

    def key(self, timeout=0.0):
        r, _, _ = select.select([sys.stdin], [], [], timeout)
        if r:
            ch = os.read(self.fd, 16).decode(errors="ignore")
            return ch
        return None

    def done(self, timeout):
        k = self.key(timeout)
        return k is not None and (k[:1] in ("q", "Q", "\x1b", "\x03"))


def run(frame, fps=30):
    """frame(t, w, h, first) → Text des Bildes. Läuft, bis q/Esc gedrückt wird."""
    with Term() as term:
        t0 = time.time()
        mt, next_check = colors_mtime(), 0.0
        while True:
            now = time.time()
            if now >= next_check:              # Farbdatei 2× pro Sekunde prüfen (billig: nur stat)
                next_check = now + 0.5
                m = colors_mtime()
                if m != mt:
                    mt = m
                    if reload_palette():
                        sys.stdout.write("\x1b[0m" + bg(P["background"]) + "\x1b[2J")
            first = term.resized
            if first:
                term.resized = False
                sys.stdout.write("\x1b[0m\x1b[2J")
            w, h = term.size()
            out = frame(time.time() - t0, w, h, first)
            sys.stdout.write("\x1b[H" + out + "\x1b[0m")
            sys.stdout.flush()
            if term.done(1 / fps):
                return


# ---------------- Effekte ----------------
@effect("matrix", "fallende Zeichen in deinen Farben")
def matrix():
    chars = "ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉ0123456789$+-*<>=#"
    st = {}

    def frame(t, w, h, first):
        if first or st.get("w") != w:
            st.update(w=w, h=h, drops=[random.uniform(-h, 0) for _ in range(w)],
                      speed=[random.uniform(0.3, 1.1) for _ in range(w)],
                      grid=[[" "] * w for _ in range(h)], age=[[99] * w for _ in range(h)])
        g, age = st["grid"], st["age"]
        for x in range(w):
            st["drops"][x] += st["speed"][x]
            y = int(st["drops"][x])
            if 0 <= y < h:
                g[y][x] = random.choice(chars)
                age[y][x] = 0
            if y > h + random.randint(0, h):
                st["drops"][x] = random.uniform(-10, 0)
        rows = []
        for y in range(h):
            line, last = [], None
            for x in range(w):
                a = age[y][x]
                age[y][x] = a + 1
                if a > 28:
                    c, ch = None, " "
                else:
                    ch = g[y][x] if random.random() > 0.02 else random.choice(chars)
                    c = P["foreground"] if a == 0 else mix(P["primary"], P["background"], a / 28)
                if c != last:
                    line.append(fg(c) if c else "")
                    last = c
                line.append(ch)
            rows.append("".join(line))
        return bg(P["background"]) + "\r\n".join(rows)
    run(frame, 24)


@effect("fire", "Lagerfeuer (Doom-Feuer) von unten")
def fire():
    st = {}
    get_stops = cached(lambda: [P["background"], mix(P["primary"], P["background"], 0.6), P["primary"],
                                P["tertiary"], P["foreground"]])

    def frame(t, w, h, first):
        stops = get_stops()
        H = h * 2
        if first or st.get("w") != w:
            st.update(w=w, buf=[[0.0] * w for _ in range(H)])
        b = st["buf"]
        for x in range(w):
            b[H - 1][x] = 1.0 if random.random() > 0.15 else 0.6
        for y in range(H - 1):
            row, below = b[y], b[y + 1]
            for x in range(w):
                sx = min(w - 1, max(0, x + random.randint(-1, 1)))
                row[x] = max(0.0, below[sx] - random.uniform(0.0, 0.09) * (40 / max(20, H)) * 1.6)
        out = []
        for y in range(0, H, 2):
            prev = [None, None]
            out.append("".join(half(ramp(b[y][x], stops), ramp(b[y + 1][x], stops), prev) for x in range(w)))
        return "\r\n".join(out)
    run(frame, 30)


@effect("stars", "Flug durch ein Sternenfeld")
def stars():
    st = {"s": [[random.uniform(-1, 1), random.uniform(-1, 1), random.uniform(0.05, 1)] for _ in range(400)]}

    def frame(t, w, h, first):
        grid = {}
        cx, cy = w / 2, h / 2
        for s in st["s"]:
            s[2] -= 0.012
            if s[2] <= 0.02:
                s[:] = [random.uniform(-1, 1), random.uniform(-1, 1), 1.0]
            x = int(cx + s[0] / s[2] * cx * 0.6)
            y = int(cy + s[1] / s[2] * cy * 0.6)
            if 0 <= x < w and 0 <= y < h:
                d = 1 - s[2]
                ch = "·" if d < 0.4 else "•" if d < 0.75 else "●"
                grid[(x, y)] = (ch, mix(P["outline"], P["foreground"] if d > 0.8 else P["primary"], d))
        out = [bg(P["background"])]
        for y in range(h):
            for x in range(w):
                v = grid.get((x, y))
                out.append(fg(v[1]) + v[0] if v else " ")
            if y < h - 1:
                out.append("\r\n")
        return "".join(out)
    run(frame, 30)


@effect("donut", "der berühmte rotierende ASCII-Donut")
def donut():
    def frame(t, w, h, first):
        A, B = t * 1.0, t * 0.5
        zb = [0.0] * (w * h)
        lum = [-1.0] * (w * h)
        cA, sA, cB, sB = math.cos(A), math.sin(A), math.cos(B), math.sin(B)
        K2 = 5.0
        K1 = min(w, h * 2) * K2 * 3 / (8 * 3)
        th = 0.0
        while th < 6.28:
            ct, sth = math.cos(th), math.sin(th)
            ph = 0.0
            while ph < 6.28:
                cp, sp = math.cos(ph), math.sin(ph)
                cx, cy = 2 + ct, sth
                x = cx * (cB * cp + sA * sB * sp) - cy * cA * sB
                y = cx * (sB * cp - sA * cB * sp) + cy * cA * cB
                z = K2 + cA * cx * sp + cy * sA
                ooz = 1 / z
                xp = int(w / 2 + K1 * ooz * x)
                yp = int(h / 2 - K1 * ooz * y / 2)
                L = cp * ct * sB - cA * ct * sp - sA * sth + cB * (cA * sth - ct * sA * sp)
                if 0 <= xp < w and 0 <= yp < h and ooz > zb[yp * w + xp]:
                    zb[yp * w + xp] = ooz
                    lum[yp * w + xp] = L
                ph += 0.04
            th += 0.1
        chars = ".,-~:;=!*#$@"
        out = [bg(P["background"])]
        for y in range(h):
            last = None
            for x in range(w):
                L = lum[y * w + x]
                if L < -0.5:
                    out.append(" ")
                    continue
                v = max(0.0, L) / 1.42
                c = ramp(v, [P["outline"], P["primary"], P["tertiary"], P["foreground"]])
                if c != last:
                    out.append(fg(c))
                    last = c
                out.append(chars[min(11, int(v * 11.99))])
            if y < h - 1:
                out.append("\r\n")
        return "".join(out)
    run(frame, 30)


@effect("plasma", "fließende Farbwellen (Halbblock-Pixel)")
def plasma():
    get_stops = cached(lambda: [P["background"], mix(P["primary"], P["background"], 0.5), P["primary"],
                                P["secondary"], P["tertiary"], mix(P["tertiary"], P["background"], 0.6),
                                P["background"]])

    def val(x, y, t):
        v = math.sin(x * 0.09 + t) + math.sin((y * 0.12 + t) * 0.8) + math.sin((x * 0.05 + y * 0.07 + t) * 1.3)
        v += math.sin(math.hypot(x - 40 * math.sin(t * 0.3) - 40, y - 20) * 0.1)
        return (v + 4) / 8

    def frame(t, w, h, first):
        stops = get_stops()
        out = []
        for y in range(h):
            prev = [None, None]
            out.append("".join(half(ramp(val(x, y * 2, t), stops), ramp(val(x, y * 2 + 1, t), stops), prev)
                               for x in range(w)))
        return "\r\n".join(out)
    run(frame, 20)


@effect("life", "Game of Life, das sich immer neu besiedelt")
def life():
    st = {}

    def frame(t, w, h, first):
        H = h * 2
        if first or st.get("w") != w or st.get("gen", 0) > 600 or st.get("still", 0) > 30:
            st.update(w=w, gen=0, still=0, g=[[random.random() < 0.28 for _ in range(w)] for _ in range(H)],
                      age=[[0] * w for _ in range(H)])
        g, age = st["g"], st["age"]
        n = [[False] * w for _ in range(H)]
        alive = 0
        for y in range(H):
            for x in range(w):
                c = 0
                for dy in (-1, 0, 1):
                    row = g[(y + dy) % H]
                    c += row[(x - 1) % w] + row[x] + row[(x + 1) % w]
                c -= g[y][x]
                v = c == 3 or (g[y][x] and c == 2)
                n[y][x] = v
                age[y][x] = age[y][x] + 1 if v else 0
                alive += v
        st["still"] = st["still"] + 1 if alive == st.get("alive") else 0
        st["alive"], st["g"], st["gen"] = alive, n, st["gen"] + 1

        def col(y, x):
            if not n[y][x]:
                return P["background"]
            return ramp(min(1.0, age[y][x] / 40), [P["foreground"], P["primary"], P["secondary"], P["outline"]])
        out = []
        for y in range(0, H, 2):
            prev = [None, None]
            out.append("".join(half(col(y, x), col(y + 1, x), prev) for x in range(w)))
        return "\r\n".join(out)
    run(frame, 15)


DIGITS = {
    "0": ["███", "█ █", "█ █", "█ █", "███"], "1": ["  █", "  █", "  █", "  █", "  █"],
    "2": ["███", "  █", "███", "█  ", "███"], "3": ["███", "  █", "███", "  █", "███"],
    "4": ["█ █", "█ █", "███", "  █", "  █"], "5": ["███", "█  ", "███", "  █", "███"],
    "6": ["███", "█  ", "███", "█ █", "███"], "7": ["███", "  █", "  █", "  █", "  █"],
    "8": ["███", "█ █", "███", "█ █", "███"], "9": ["███", "█ █", "███", "  █", "███"],
    ":": [" ", "█", " ", "█", " "],
}


@effect("clock", "große Uhr mit Datum")
def clock():
    def frame(t, w, h, first):
        now = time.localtime()
        s = time.strftime("%H:%M:%S", now)
        scale = 2 if w >= 70 else 1
        rows = []
        for r in range(5):
            line = " ".join("".join(ch * scale for ch in DIGITS[c][r]) for c in s)
            rows.append(line)
        width = len(rows[0])
        top = max(0, h // 2 - 4)
        left = max(0, (w - width) // 2)
        days = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
        date = f"{days[now.tm_wday]}, {now.tm_mday}.{now.tm_mon}.{now.tm_year} · KW {time.strftime('%V', now)}"
        out = [bg(P["background"]), "\x1b[2J" if first else "", "\x1b[H"]
        for i, line in enumerate(rows):
            c = mix(P["primary"], P["tertiary"], i / 4)
            out.append(f"\x1b[{top + i + 1};{left + 1}H" + fg(c) + line)
        out.append(f"\x1b[{top + 7};{max(1, (w - len(date)) // 2 + 1)}H" + fg(P["secondary"]) + date)
        return "".join(out)
    run(frame, 4)


@effect("rain", "Regen mit kleinen Spritzern")
def rain():
    st = {"d": [], "sp": []}

    def frame(t, w, h, first):
        for _ in range(max(1, w // 12)):
            st["d"].append([random.randrange(w), 0.0, random.uniform(0.6, 1.4)])
        nd = []
        for d in st["d"]:
            d[1] += d[2]
            if d[1] >= h - 1:
                st["sp"].append([d[0], h - 1, 0])
            else:
                nd.append(d)
        st["d"] = nd
        st["sp"] = [s for s in st["sp"] if s[2] < 4]
        grid = {}
        for x, y, sp in st["d"]:
            grid[(x, int(y))] = ("│" if sp > 1.1 else "╎", mix(P["outline"], P["primary"], sp - 0.6))
        for s in st["sp"]:
            s[2] += 1
            for dx, ch in ((-s[2], "·"), (s[2], "·"), (0, "˙" if s[2] < 2 else " ")):
                grid[(s[0] + dx, s[1])] = (ch, P["secondary"])
        out = [bg(P["background"])]
        for y in range(h):
            for x in range(w):
                v = grid.get((x, y))
                out.append(fg(v[1]) + v[0] if v else " ")
            if y < h - 1:
                out.append("\r\n")
        return "".join(out)
    run(frame, 30)


@effect("fetch", "Systeminfo mit Farbpalette (einmal, kein Vollbild)")
def fetch():
    import platform, socket
    def read(p):
        try:
            return open(p).read()
        except OSError:
            return ""
    os_name = next((l.split("=", 1)[1].strip('"\n') for l in read("/etc/os-release").splitlines()
                    if l.startswith("PRETTY_NAME=")), platform.system())
    up = float(read("/proc/uptime").split()[0] or 0) if read("/proc/uptime") else 0
    mem = dict((l.split(":")[0], int(l.split()[1])) for l in read("/proc/meminfo").splitlines() if len(l.split()) > 1)
    used = (mem.get("MemTotal", 0) - mem.get("MemAvailable", 0)) / 1048576
    cpu = next((l.split(":", 1)[1].strip() for l in read("/proc/cpuinfo").splitlines() if l.startswith("model name")), "")
    rows = [("os", os_name), ("kernel", platform.release()), ("host", socket.gethostname()),
            ("uptime", f"{int(up // 3600)} h {int(up % 3600 // 60)} min"), ("wm", os.environ.get("XDG_CURRENT_DESKTOP", "")),
            ("shell", os.path.basename(os.environ.get("SHELL", ""))), ("cpu", re.sub(r"\s+", " ", cpu)[:40]),
            ("ram", f"{used:.1f} / {mem.get('MemTotal', 0) / 1048576:.1f} GiB")]
    logo = ["  ▄▄▄▄▄▄▄  ", " █  ist  █ ", " █ ▀▀▀▀▀ █ ", " █▄▄▄▄▄▄▄█ ", "   ▀▀ ▀▀   "]
    print()
    for i in range(max(len(logo), len(rows))):
        l = logo[i] if i < len(logo) else " " * 11
        c = mix(P["primary"], P["tertiary"], i / max(1, len(logo) - 1))
        r = rows[i] if i < len(rows) else None
        txt = f"{fg(P['primary'])}{r[0]:>7}  {fg(P['foreground'])}{r[1]}" if r else ""
        print(f"  {fg(c)}{l}\x1b[0m   {txt}\x1b[0m")
    sw = "".join(bg(P[k]) + "   " for k in ("background", "primary", "secondary", "tertiary", "outline", "foreground"))
    print(f"\n  {' ' * 11}   {sw}\x1b[0m\n")


def usage():
    print(f"\n  {fg(P['primary'])}istfx\x1b[0m – Terminal-Visuals in deinen Desktop-Farben (beenden: q / Esc)\n")
    for k, (_, d) in EFFECTS.items():
        print(f"  {fg(P['tertiary'])}istfx {k:<8}\x1b[0m {d}")
    print(f"\n  {fg(P['outline'])}istfx --list   nur die Namen\x1b[0m\n")


def main():
    args = sys.argv[1:]
    if args[:1] == ["--list"]:
        print("\n".join(EFFECTS))
        return
    if not args or args[0] not in EFFECTS:
        usage()
        return
    try:
        EFFECTS[args[0]][0]()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
