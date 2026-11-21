# Netz-Monitor: welche Programme (Laptop, per ss) bzw. Apps (Handy, per adb) wohin Verbindungen aufbauen.
# Ohne Root: sieht Ziel, Port und Programm, nicht den Inhalt. Neue Ziele je Programm werden gegen eine Liste
# bekannter Ziele (~/.config/scourge/netwatch.json) markiert.
import concurrent.futures, ipaddress, json, os, re, shutil, socket, subprocess, time

from tools import ToolPage

from gi.repository import GLib, Gtk

KNOWN_FILE = os.path.expanduser("~/.config/scourge/netwatch.json")
COMMON_PORTS = {22, 53, 67, 68, 80, 123, 143, 443, 465, 587, 853, 993, 995, 5222, 5223, 5228, 3478, 8443}
ANDROID_UIDS = {0: "root", 1000: "android-system", 1001: "telefonie", 1013: "medien", 1051: "dns", 1052: "dns-tether",
                2000: "adb-shell", 9999: "nobody"}
USERS_RE = re.compile(r'\("([^"]+)",pid=(\d+)')
INTERPRETERS = {"python", "python3", "node", "java", "sh", "bash", "perl", "ruby", "electron"}


def split_addr(s):
    """'[::ffff:1.2.3.4]:443' / '1.2.3.4%wlan0:68' -> ('1.2.3.4', 443)"""
    host, _, port = s.rpartition(":")
    host = host.strip("[]").split("%")[0]
    if host.startswith("::ffff:"):
        host = host[7:]
    return host, int(port) if port.isdigit() else 0


def parse_ss(text):
    """ss -tunpiH -> [(proto, prog, local, ip, port, sent, recv)]"""
    out, cur = [], None
    for line in text.splitlines():
        if line[:1] in ("\t", " ") and cur:          # Infozeile mit bytes_sent/bytes_received zur Verbindung davor
            m_s, m_r = re.search(r"bytes_sent:(\d+)", line), re.search(r"bytes_received:(\d+)", line)
            cur[5], cur[6] = int(m_s.group(1)) if m_s else 0, int(m_r.group(1)) if m_r else 0
            continue
        f = line.split()
        if len(f) < 6 or f[0] not in ("tcp", "udp"):
            cur = None
            continue
        ip, port = split_addr(f[5])
        m = USERS_RE.search(line)
        cur = [f[0], prog_name(m.group(1), m.group(2)) if m else "(root/system)", f[4], ip, port, 0, 0]
        if ip not in ("*", "0.0.0.0", "::") and port:
            out.append(cur)
    return out


def prog_name(name, pid):
    """Bei python/node & Co. das Skript dazuschreiben, sonst sagt der Name nichts."""
    if name not in INTERPRETERS:
        return name
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            args = [a for a in f.read().decode(errors="ignore").split("\0")[1:] if a and not a.startswith("-")]
    except OSError:
        return name
    return f"{name} {os.path.basename(args[0])}" if args else name


def hex_ip(h):
    b = bytes.fromhex(h)
    if len(b) == 4:
        return str(ipaddress.IPv4Address(b[::-1]))
    a = ipaddress.IPv6Address(b"".join(b[i:i + 4][::-1] for i in range(0, 16, 4)))
    return str(a.ipv4_mapped or a)


def parse_proc_net(text, proto):
    """/proc/net/tcp[6]|udp[6] -> [(proto, uid, local, ip, port)] (nur verbundene Sockets)"""
    out = []
    for line in text.splitlines():
        f = line.split()
        if len(f) < 8 or ":" not in f[2] or f[0] == "sl":
            continue
        rh, rp = f[2].split(":")
        port = int(rp, 16)
        if not port or (proto == "tcp" and f[3] != "01"):     # 01 = ESTABLISHED
            continue
        out.append((proto, int(f[7]), f[1], hex_ip(rh), port))
    return out


def is_local(ip):
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return True


def domain(host):
    # shortcut: nimmt die letzten zwei Namensteile (co.uk & Co. werden zu grob), reicht zum Wiedererkennen
    if not host or host[0].isdigit() or ":" in host:
        return host
    return ".".join(host.rstrip(".").split(".")[-2:])


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def find_adb():
    return shutil.which("adb") or next((p for p in (os.path.expanduser(
        "~/.local/share/android-dev/sdk/platform-tools/adb"), os.path.expanduser("~/Android/Sdk/platform-tools/adb"))
        if os.path.exists(p)), None)


class Source:
    """Sammelt die Verbindungen einer Quelle (Laptop oder Handy) über die ganze Sitzung."""

    def __init__(self, name):
        self.name, self.seen, self.status = name, {}, ""

    def merge(self, conns, now):
        for proto, prog, local, ip, port, *traffic in conns:
            if is_local(ip):
                continue
            e = self.seen.setdefault((prog, ip, port, proto), {"first": now, "socks": {}, "new": None})
            e["last"] = now
            e["socks"][local] = traffic or (0, 0)


class NetWatch(ToolPage):
    title = "Netz-Monitor"
    subtitle = "Welche Programme auf dem Laptop und Apps auf dem Handy wohin Daten schicken."
    privacy = ("Liest nur die Verbindungsliste deines Systems (ss) bzw. des Handys (adb), ohne Root und ohne Inhalte. "
               "Hostnamen kommen per Reverse-DNS über deinen normalen DNS-Server.")

    def build(self):
        try:
            with open(KNOWN_FILE) as f:
                self.known = set(json.load(f))
        except (OSError, ValueError):
            self.known = set()
        self.learning = not self.known          # erste Aufzeichnung: alles lernen statt alles „neu“ zu nennen
        self.names, self.dns = {}, concurrent.futures.ThreadPoolExecutor(4)
        self.pkgs, self.pkgs_at, self.adb = {}, 0, find_adb()
        self.sources = {"laptop": Source("laptop"), "handy": Source("handy")}
        self.busy_poll = False

        self.rec = Gtk.ToggleButton(label="󰑊 aufzeichnen", active=True)
        self.rec.add_css_class("pb")
        self.only_odd = Gtk.CheckButton(label="nur auffällige")
        self.only_odd.connect("toggled", lambda *_: self.redraw())
        self.input_row(self.rec, self.only_odd, self.button("󰃢 liste leeren", self.reset, small=True))
        hint = Gtk.Label(xalign=0, wrap=True, label=(
            "Gelb = ungewöhnlicher Port, rot = Ziel, das dieses Programm noch nie kontaktiert hat. "
            "Handy: USB-Debugging an und per Kabel oder „adb connect“ verbinden. Verbindungen, die kürzer als "
            "2 s leben, können durchrutschen."))
        hint.add_css_class("note")
        self.box.append(hint)
        if self.learning:
            l = Gtk.Label(xalign=0, wrap=True, label="Erste Aufzeichnung: alle Ziele werden als bekannt gelernt, "
                                                     "markiert wird erst ab dem nächsten Mal.")
            l.add_css_class("note")
            self.box.append(l)
        self.views = {}
        for key, title in (("laptop", "laptop"), ("handy", "handy (adb)")):
            c = self.card(title)
            st = Gtk.Label(xalign=0)
            st.add_css_class("note")
            lbl = Gtk.Label(xalign=0, yalign=0, selectable=True, wrap=False)
            lbl.add_css_class("monospace")
            c.append(st)
            c.append(lbl)
            self.views[key] = (st, lbl)
        self.poll()
        GLib.timeout_add_seconds(2, self.tick)

    def reset(self):
        for s in self.sources.values():
            s.seen.clear()
        self.redraw()

    # ---------- Sammeln (Hintergrund) ----------
    def tick(self):
        if self.get_root() is None and self.get_parent() is None:      # Tab geschlossen → aufhören
            dns = getattr(self, "dns", None)                           # release_tree hat die Attribute evtl. schon geleert
            if dns:
                dns.shutdown(wait=False)
            return False
        if self.rec.get_active() and not self.busy_poll:
            self.poll()
        return True

    def poll(self):
        self.busy_poll = True
        self.run(self.collect, self.collected)

    def collect(self):
        now, res = time.time(), {}
        try:
            res["laptop"] = (parse_ss(subprocess.run(["ss", "-tunpiH"], capture_output=True, text=True, timeout=5).stdout),
                             "")
        except (OSError, subprocess.SubprocessError) as e:
            res["laptop"] = ([], f"ss fehlt oder schlug fehl: {e}")
        res["handy"] = self.collect_phone()
        return now, res

    def collect_phone(self):
        if not self.adb:
            return [], "adb nicht gefunden (android-tools installieren)."
        try:
            r = subprocess.run([self.adb, "shell", "for f in tcp tcp6 udp udp6; do echo @$f; cat /proc/net/$f; done"],
                               capture_output=True, text=True, timeout=6)
        except (OSError, subprocess.SubprocessError) as e:
            return [], f"adb schlug fehl: {e}"
        if r.returncode:
            return [], "kein Handy verbunden" if "no devices" in r.stderr or "not found" in r.stderr \
                else r.stderr.strip()[:200]
        conns = []
        for chunk in r.stdout.split("@")[1:]:
            name, _, body = chunk.partition("\n")
            conns += parse_proc_net(body, name.strip().rstrip("6"))
        if any(uid not in self.pkgs for _, uid, *_ in conns) and time.time() - self.pkgs_at > 30:
            self.pkgs_at = time.time()
            out = subprocess.run([self.adb, "shell", "cmd package list packages -U"], capture_output=True, text=True,
                                 timeout=10).stdout
            for m in re.finditer(r"package:(\S+) uid:([\d,]+)", out):
                for uid in m.group(2).split(","):
                    self.pkgs.setdefault(int(uid), []).append(m.group(1))
        names = lambda uid: ANDROID_UIDS.get(uid) or "+".join(self.pkgs.get(uid, [])) or f"uid {uid}"
        status = "" if conns else "verbunden, aber keine Verbindungen lesbar (Android schränkt /proc/net evtl. ein)"
        return [(p, names(uid), local, ip, port) for p, uid, local, ip, port in conns], status

    def collected(self, res):
        self.busy_poll = False
        if isinstance(res, Exception):
            self.views["laptop"][0].set_label(f"Fehler: {res}")
            return
        now, data = res
        for key, (conns, status) in data.items():
            self.sources[key].merge(conns, now)
            self.sources[key].status = status
        self.redraw(now)

    def host(self, ip):
        if ip not in self.names:
            self.names[ip] = None
            fut = self.dns.submit(lambda: socket.gethostbyaddr(ip)[0])
            fut.add_done_callback(lambda f: self.names.__setitem__(ip, ip if f.exception() else f.result()))
        return self.names[ip]

    # ---------- Anzeige ----------
    def redraw(self, now=None):
        now = now or time.time()
        changed = False
        for key, src in self.sources.items():
            st, lbl = self.views[key]
            last = max((e["last"] for e in src.seen.values()), default=0)
            rows = []
            for (prog, ip, port, proto), e in src.seen.items():
                h = self.host(ip)
                if e["new"] is None and h is not None:     # erst entscheiden, wenn der Name da ist
                    k = f"{key}|{prog}|{domain(h)}"
                    e["new"] = not self.learning and k not in self.known
                    if k not in self.known:
                        self.known.add(k)
                        changed = True
                # shortcut: Ports ab 32768 gelten als Gegenstück eingehender Verbindungen und werden nicht markiert
                odd = port < 32768 and port not in COMMON_PORTS and not 19302 <= port <= 19309
                if self.only_odd.get_active() and not (odd or e["new"]):
                    continue
                sent = sum(s[0] for s in e["socks"].values())
                recv = sum(s[1] for s in e["socks"].values())
                rows.append((not e["new"], not odd, -e["last"], prog, h or ip, port, proto, sent, recv, e, odd))
            rows.sort()
            lines = []
            for _, _, _, prog, h, port, proto, sent, recv, e, odd in rows[:300]:
                live = e["last"] >= last - 0.5
                dot = '<span foreground="#6bd68a">●</span>' if live else '<span alpha="40%">○</span>'
                portm = f'<span foreground="#ffc857">{port}</span>' if odd else str(port)
                traffic = f"↑{human(sent)} ↓{human(recv)}" if sent or recv else ""
                ago = "jetzt" if live else f"vor {int(now - e['last'])} s"
                flag = ' <span foreground="#ff6b6b" weight="bold">NEU</span>' if e["new"] else ""
                esc = GLib.markup_escape_text
                lines.append(f"{dot} {esc(prog[:28]):<28} → {esc(f"[{h}]" if ":" in h else h[:48])}:{portm}  {proto}  {traffic}  "
                             f'<span alpha="55%">{ago}</span>{flag}')
            st.set_label(src.status or f"{len(src.seen)} Ziele in dieser Sitzung, {sum(1 for r in rows if r[9]['new'])} neu")
            lbl.set_markup("\n".join(lines) or '<span alpha="50%">noch nichts</span>')
        if changed:
            try:
                os.makedirs(os.path.dirname(KNOWN_FILE), exist_ok=True)
                with open(KNOWN_FILE + ".tmp", "w") as f:
                    json.dump(sorted(self.known), f)
                os.replace(KNOWN_FILE + ".tmp", KNOWN_FILE)
            except OSError:
                pass


def _selftest():
    assert split_addr("[::ffff:1.2.3.4]:443") == ("1.2.3.4", 443)
    assert split_addr("192.168.1.43%wlan0:68") == ("192.168.1.43", 68)
    assert hex_ip("0100007F") == "127.0.0.1"
    assert hex_ip("0000000000000000FFFF00000401A8C0") == "192.168.1.4"
    ss = ('tcp ESTAB 0 0 192.168.1.43:40822 160.79.104.10:443 users:(("claude",pid=1,fd=3))\n'
          '\t cubic bytes_sent:2706 bytes_acked:2707 bytes_received:8234\n'
          'udp UNCONN 0 0 0.0.0.0:5353 0.0.0.0:*\n')
    assert parse_ss(ss) == [["tcp", "claude", "192.168.1.43:40822", "160.79.104.10", 443, 2706, 8234]]
    proc = ("  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
            "   0: 0100007F:1F90 2F5A3AB0:01BB 01 00000000:00000000 00:00000000 00000000 10123 0 1\n"
            "   1: 0100007F:1F90 00000000:0000 0A 00000000:00000000 00:00000000 00000000 10123 0 2\n")
    assert parse_proc_net(proc, "tcp") == [("tcp", 10123, "0100007F:1F90", "176.58.90.47", 443)]
    assert domain("lhr25s34-in-f14.1e100.net") == "1e100.net" and domain("1.2.3.4") == "1.2.3.4"
    print("ok")


if __name__ == "__main__":
    _selftest()
