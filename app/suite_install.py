# Installieren und Aktualisieren der Suite-Apps direkt aus scourge (Apps-Leiste: kleiner Knopf je App +
# „updates installieren“ für alle).
#
# Ablauf je App: Repo nach ~/.local/share/ist-src/<app> holen (oder dort aktualisieren), dann dessen
# `install.sh --update` ohne Rückfragen ausführen. Vorher/nachher werden Reste früherer Namen aufgeräumt
# (wallpapaer → wallist, filewise → filist, kalender → datist, textwise/notes → notist, musicminus → musicalist):
# alte Prozesse beenden, alte Menüeinträge/Icons/Dienste entfernen, alte Befehle zeigen danach auf den neuen
# (damit eigene Tastenkürzel weiter gehen). Das klappt auch mit älteren Repo-Ständen, deren install.sh die
# alten Namen noch nicht kennt.
import json
import os
import re
import shutil
import subprocess
import threading
import time

from gi.repository import Gio, GLib

H = os.path.expanduser
SRC_ROOT = H("~/.local/share/ist-src")
LOG_DIR = H("~/.cache/scourge/install")
BIN = H("~/.local/bin")
APPS_DIR = H("~/.local/share/applications")
ICON_DIR = H("~/.local/share/icons/hicolor/scalable/apps")
WINDOWS = os.name == "nt"
SUPPORTED = WINDOWS or bool(shutil.which("bash"))
# Unter Windows gibt es (noch) Installer für diese Apps – install-windows.ps1 im Repo
WIN_APPS = ("scourge", "notist", "datist")
WIN_LINK = {"scourge": "Scourge", "notist": "notist", "datist": "datist", "musicalist": "musicalist"}

# Repo-Namen: der neue zuerst, dann die öffentliche Ausgabe, dann frühere Namen (GitHub leitet um).
OWNERS = ("danger-inc", "invincible-nix")

# key: Starter, Versionsdatei, Repo-Namen, Prozessmuster, alte Namen
APPS = {
    "scourge": dict(cmd="scourge", version="~/.local/share/scourge/VERSION",
                    repos=("scourge", "scourge-public"), proc=r"/scourge/scourge\.py"),
    "notist": dict(cmd="notist", version="~/.local/share/notes/app/VERSION",
                   repos=("notist", "notist-public", "notes", "textwise"), proc=r"/notes/app/notes\.py",
                   old=dict(bins=("textwise",), solo_bins=("notes",), desktop=("textwise.desktop", "notes.desktop"),
                            icons=("textwise.svg", "notes.svg"))),
    "filist": dict(cmd="filist", version="~/.local/share/filist/app/VERSION",
                   repos=("filist", "filewise"), proc=r"/filist/app/filist\.py",
                   old=dict(bins=("filewise",), desktop=("filewise.desktop",), icons=("filewise.svg",),
                            units=("filewise.service",), proc=r"/filewise/app/\w+\.py",
                            dirs=("~/.local/share/filewise",))),
    "musicalist": dict(cmd="musicalist", version="~/.local/share/rice-apps/music/VERSION",
                       repos=("musicalist", "musicalist-public", "musicminus"), proc=r"/rice-apps/music/musicd\.py",
                       old=dict(bins=("musicminus",), solo_bins=("rice-music",))),
    "datist": dict(cmd="datist", version="~/.local/share/rice-apps/calendar/VERSION",
                   repos=("datist", "kalender"), proc=r"/rice-apps/calendar/ui\.py",
                   old=dict(bins=("kalender",), desktop=("kalender.desktop",), icons=("kalender.svg",))),
    "wallist": dict(cmd="wallist", version="~/.local/share/wallist/VERSION",
                    repos=("wallist", "wallpapaer"), proc=r"/wallist/wallist\.py",
                    old=dict(bins=("wallpapaer",), desktop=("wallpapaer.desktop", "de.invincible.Wallpapaer.desktop"),
                             icons=("wallpapaer.svg",), proc=r"/wallpapaer/wallpapaer\.py",
                             dirs=("~/.local/share/wallpapaer",), keep=(("models", "~/.local/share/wallist/models"),))),
    "mailist": dict(cmd="mailist", version="~/.local/share/mailist/VERSION",
                    repos=("mailist", "mailist-public"), proc=r"/mailist/mailist\.py"),
    "sharist": dict(cmd="sharist", version="~/.local/share/sharist/VERSION",
                    repos=("sharist",), proc=r"/sharist/sharist\.py"),
    "linguist": dict(cmd="linguist", version="~/.local/share/linguist/VERSION",
                     repos=("linguist",), proc=r"/linguist/linguist\.py"),
}
ORDER = ("scourge", "notist", "filist", "musicalist", "datist", "wallist", "mailist", "sharist", "linguist")


# ---------------- Zustand ----------------
def _vkey(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:4])


def launcher(cmd):
    if WINDOWS:                       # Startmenü-Verknüpfung, die install-windows.ps1 anlegt
        lnk = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs",
                           WIN_LINK.get(cmd, cmd) + ".lnk")
        return lnk if os.path.exists(lnk) else None
    p = os.path.join(BIN, cmd)
    return p if os.access(p, os.X_OK) else shutil.which(cmd)


def installed_version(key):
    try:
        return open(H(APPS[key]["version"])).read().strip()
    except OSError:
        return ""


def old_names(key):
    """Reste früherer Namen, die noch auf der Platte liegen (Starter, Ordner, Menüeinträge)."""
    old = APPS[key].get("old") or {}
    found = []
    for b in old.get("bins", ()) + old.get("solo_bins", ()):
        if os.path.lexists(os.path.join(BIN, b)):
            found.append(b)
    for d in old.get("dirs", ()):
        if os.path.isdir(H(d)):
            found.append(os.path.basename(d))
    for d in old.get("desktop", ()):
        if os.path.exists(os.path.join(APPS_DIR, d)):
            found.append(d.rsplit(".", 1)[0])
    return list(dict.fromkeys(found))


def state(key):
    """'ok' | 'old' (nur unter altem Namen da) | 'missing'."""
    a = APPS[key]
    if launcher(a["cmd"]) and installed_version(key):
        return "ok"
    if launcher(a["cmd"]):              # Starter da, aber keine Versionsdatei → sehr alte Installation
        return "old"
    return "old" if old_names(key) else "missing"


# ---------------- Repo holen ----------------
def _git_env():
    env = dict(os.environ)
    env.update(GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="/bin/false", SSH_ASKPASS="/bin/false", LC_ALL="C")
    env.pop("LD_PRELOAD", None)
    return env


def _git_auth():
    """git-Optionen, damit private Repos über ein angemeldetes gh-CLI gehen (sonst nur öffentliche)."""
    gh = shutil.which("gh")
    if gh:
        try:
            ok = subprocess.run([gh, "auth", "status"], capture_output=True, timeout=8).returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
        if ok:
            return ["-c", "credential.helper=", "-c", f"credential.helper=!{gh} auth git-credential"]
    return []


def candidates(key):
    out = []
    for name in APPS[key]["repos"]:
        for owner in OWNERS:
            out.append(f"https://github.com/{owner}/{name}.git")
    return out


def _run(argv, log, cwd=None, timeout=900, env=None):
    log.write(f"$ {' '.join(argv)}\n")
    log.flush()
    try:
        p = subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                           env=env or _git_env(), timeout=timeout, start_new_session=True)
        return p.returncode
    except subprocess.TimeoutExpired:
        log.write("… abgebrochen (zu lange)\n")
        return 124
    except OSError as e:
        log.write(f"… {e}\n")
        return 127


def fetch_repo(key, log, step):
    """Neuesten Stand nach SRC_ROOT/key holen. Gibt den Ordner zurück oder wirft RuntimeError."""
    if not shutil.which("git"):
        raise RuntimeError("git fehlt – bitte „git“ installieren")
    auth = _git_auth()
    dest = os.path.join(SRC_ROOT, key)
    os.makedirs(SRC_ROOT, exist_ok=True)
    if os.path.isdir(os.path.join(dest, ".git")):
        step("lädt neuesten stand …")
        if (_run(["git"] + auth + ["-C", dest, "fetch", "--depth", "1", "origin", "HEAD"], log, timeout=300) == 0
                and _run(["git", "-C", dest, "reset", "--hard", "FETCH_HEAD"], log) == 0
                and _run(["git", "-C", dest, "clean", "-fdq"], log) == 0):
            return dest
        log.write("… vorhandene Kopie unbrauchbar, lade neu\n")
    tmp = dest + ".neu"
    shutil.rmtree(tmp, ignore_errors=True)
    step("lädt herunter …")
    for url in candidates(key):
        if _run(["git"] + auth + ["clone", "--depth", "1", "--quiet", url, tmp], log, timeout=600) == 0 \
                and os.path.isfile(os.path.join(tmp, "install.sh")):
            shutil.rmtree(dest, ignore_errors=True)
            os.replace(tmp, dest)
            return dest
        shutil.rmtree(tmp, ignore_errors=True)
    raise RuntimeError("kein zugriff aufs repo" + ("" if auth else " (privat? → „gh auth login“)"))


# ---------------- alte Namen ----------------
def _stop(pattern, log):
    if not pattern:
        return
    try:
        import software
        pids = [p for p, _r, _t in software.procs(pattern) if p != os.getpid()]
        if pids:
            log.write(f"beende alte prozesse {pids}\n")
            software.stop(pattern)
    except Exception as e:  # noqa: BLE001
        log.write(f"… prozesse: {e}\n")


def cleanup_before(key, log):
    """Vor der Installation: alte Prozesse/Dienste anhalten. Gibt die alten Starter zurück, die es gab."""
    old = APPS[key].get("old") or {}
    _stop(old.get("proc"), log)
    for unit in old.get("units", ()):
        f = H(f"~/.config/systemd/user/{unit}")
        if os.path.exists(f) and shutil.which("systemctl"):
            subprocess.run(["systemctl", "--user", "disable", "--now", unit], capture_output=True, timeout=15)
            try:
                os.remove(f)
            except OSError:
                pass
            log.write(f"alter dienst {unit} entfernt\n")
    return [b for b in old.get("bins", ()) if os.path.lexists(os.path.join(BIN, b))]


def cleanup_after(key, had_bins, log):
    """Nach erfolgreicher Installation: alte Menüeinträge/Icons weg, alte Befehle → neuer Starter."""
    a, old = APPS[key], APPS[key].get("old") or {}
    new = os.path.join(BIN, a["cmd"])
    if not os.path.lexists(new):
        return
    for b in old.get("bins", ()):
        p = os.path.join(BIN, b)
        if b in had_bins or os.path.lexists(p):
            try:
                if os.path.lexists(p):
                    os.remove(p)
                os.symlink(new, p)              # alter Befehl (z. B. in Tastenkürzeln) startet die neue App
                log.write(f"{b} → {a['cmd']}\n")
            except OSError as e:
                log.write(f"… {b}: {e}\n")
    for d in old.get("desktop", ()):
        _rm(os.path.join(APPS_DIR, d), log)
    for i in old.get("icons", ()):
        _rm(os.path.join(ICON_DIR, i), log)
    for d in old.get("dirs", ()):
        d = H(d)
        if not os.path.isdir(d) or os.path.isdir(os.path.join(d, "packaging")):   # eigene Entwicklerkopie: lassen
            continue
        for sub, target in old.get("keep", ()):
            s, t = os.path.join(d, sub), H(target)
            if os.path.isdir(s) and not os.path.exists(t):
                os.makedirs(os.path.dirname(t), exist_ok=True)
                shutil.move(s, t)
                log.write(f"{sub} übernommen → {target}\n")
        try:
            Gio.File.new_for_path(d).trash(None)          # Papierkorb statt löschen
            log.write(f"alter ordner {d} → papierkorb\n")
        except GLib.Error:
            shutil.rmtree(d, ignore_errors=True)
            log.write(f"alter ordner {d} entfernt\n")
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", APPS_DIR], capture_output=True, timeout=15)


def _rm(path, log):
    if os.path.lexists(path):
        try:
            os.remove(path)
            log.write(f"entfernt {path.replace(H('~'), '~')}\n")
        except OSError:
            pass


# ---------------- Installieren ----------------
def install_env():
    env = _git_env()
    env["PATH"] = BIN + os.pathsep + env.get("PATH", "/usr/bin:/bin")
    env.pop("GSK_RENDERER", None)
    env.pop("LC_ALL", None)
    env["UPDATE"] = "1"
    env["DEBIAN_FRONTEND"] = "noninteractive"
    return env


def log_path(key):
    return os.path.join(LOG_DIR, f"{key}.log")


def install(key, step):
    """Holt + installiert (blockiert – im Thread). step(text) meldet Zwischenschritte.
    Gibt (ok, text, hinweis) zurück; hinweis = 'packages' wenn Systempakete fehlen könnten."""
    os.makedirs(LOG_DIR, exist_ok=True)
    before = installed_version(key)
    if WINDOWS:
        import changelog
        ok, text = changelog.windows_install(key, update=state(key) == "ok", step=step)
        after = installed_version(key)
        if ok and not after:
            ok, text = False, "installiert, aber keine Version gefunden"
        return ok, (f"v{before} → v{after}" if ok and before and before != after else f"v{after} installiert") if ok else text, None
    with open(log_path(key), "w") as log:
        log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {key}  (vorher: {before or state(key)})\n")
        try:
            src = fetch_repo(key, log, step)
        except RuntimeError as e:
            log.write(f"FEHLER: {e}\n")
            return False, str(e), None
        had = cleanup_before(key, log)
        step("installiert …")
        rc = _run(["bash", "./install.sh", "--update"], log, cwd=src, timeout=1800, env=install_env())
        ok = rc == 0 and launcher(APPS[key]["cmd"]) is not None and bool(installed_version(key))
        if ok:
            cleanup_after(key, had, log)
        log.write(f"\nende: rc={rc}, version={installed_version(key) or '-'}\n")
    text = open(log_path(key), errors="replace").read()
    hint = "packages" if re.search(r"a terminal is required|password is required|sudo: |FEHLT|nicht vollständig"
                                   r"|ModuleNotFoundError|Namespace Gtk not available", text) else None
    if not ok:
        return False, f"installation fehlgeschlagen (code {rc})", hint or "packages"
    after = installed_version(key)
    return True, (f"v{before} → v{after}" if before and before != after else f"v{after} installiert"), hint


def terminal_install(key):
    """Interaktive Installation im Terminal (Systempakete brauchen sudo). False, wenn nichts ging."""
    src = os.path.join(SRC_ROOT, key)
    if not os.path.isfile(os.path.join(src, "install.sh")):
        return False
    try:
        import software
        term = software.terminal()
    except Exception:  # noqa: BLE001
        term = None
    if not term:
        return False
    script = (f'cd "{src}" && ./install.sh; echo; read -rp "fertig – Enter schließt das Fenster " _')
    try:
        subprocess.Popen(term.split() + ["-e", "bash", "-c", script], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def running(key):
    try:
        import software
        return bool(software.procs(APPS[key]["proc"]))
    except Exception:  # noqa: BLE001
        return False


def restart_background(key):
    """Läuft die App nur im Hintergrund (kein Fenster), gleich mit dem neuen Code neu starten.
    True = neu gestartet, False = nichts zu tun, None = Fenster offen (der User entscheidet)."""
    if key == "scourge":
        return None
    try:
        import software
        ps = software.procs(APPS[key]["proc"])
        if not ps:
            return False
        if any(p in software.window_pids() for p, _r, _t in ps):
            return None
        a = software.ADMIN.get(key, {})
        if software.unit_exists(a.get("unit")):
            subprocess.run(["systemctl", "--user", "restart", a["unit"]], capture_output=True, timeout=15)
            return True
        software.stop(a.get("proc") or APPS[key]["proc"])
        if a.get("restart"):
            software.run_detached(a["restart"])
        return True
    except Exception:  # noqa: BLE001
        return False


# ---------------- Manager (gemeinsamer Zustand für alle Leisten) ----------------
class Manager:
    """Ein Zustand für alle Ansichten. Listener bekommen (key) bei jeder Änderung (key=None = alles)."""

    def __init__(self):
        self.info = {k: {} for k in ORDER}   # status, msg, latest, busy, hint
        self.listeners = []
        self.queue = []
        self.worker = None
        self.checked = 0
        self.checking = False

    def listen(self, fn):
        self.listeners.append(fn)

    def _emit(self, key=None):
        for fn in list(self.listeners):
            try:
                fn(key)
            except Exception as e:  # noqa: BLE001
                print("suite_install:", e)
        return False

    # Updates
    def latest(self, key):
        return self.info[key].get("latest")

    def has_update(self, key):
        lat, cur = self.latest(key), installed_version(key)
        return bool(lat and cur and _vkey(lat) > _vkey(cur))

    def updates(self):
        return [k for k in ORDER if self.has_update(k)]

    def check(self, force=False):
        """Neueste Versionen von GitHub (6-h-Cache wie der Update-Hinweis in den Apps)."""
        if self.checking or not SUPPORTED:
            return
        try:
            import changelog
        except ImportError:
            return
        cache = {}
        try:
            cache = json.load(open(changelog.UPDATE_FILE))
        except (OSError, ValueError, AttributeError):
            pass
        todo = []
        for k in ORDER:
            c = cache.get(k) or {}
            if c.get("latest"):
                self.info[k]["latest"] = c["latest"]
            if force or time.time() - c.get("t", 0) >= getattr(changelog, "UPDATE_EVERY", 6 * 3600):
                todo.append(k)
        self._emit()
        if not todo:
            self.checked = max(self.checked, max((c.get("t", 0) for c in cache.values() if isinstance(c, dict)),
                                                 default=0))
            return
        self.checking = True
        self._emit()

        def work():
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(4) as ex:
                res = dict(zip(todo, ex.map(changelog.latest_version, todo)))
            try:
                data = json.load(open(changelog.UPDATE_FILE))
            except (OSError, ValueError):
                data = {}
            for k, v in res.items():
                if v:
                    data[k] = {"t": time.time(), "latest": v}
            try:
                os.makedirs(os.path.dirname(changelog.UPDATE_FILE), exist_ok=True)
                tmp = f"{changelog.UPDATE_FILE}.{os.getpid()}"
                json.dump(data, open(tmp, "w"), indent=1)
                os.replace(tmp, changelog.UPDATE_FILE)
            except OSError:
                pass

            def done():
                for k, v in res.items():
                    if v:
                        self.info[k]["latest"] = v
                self.checking = False
                self.checked = time.time()
                self._emit()
                return False
            GLib.idle_add(done)
        threading.Thread(target=work, daemon=True).start()

    # Installieren (nacheinander, nie zwei Installer gleichzeitig)
    def busy(self, key=None):
        if key:
            return bool(self.info[key].get("busy"))
        return bool(self.worker) or bool(self.queue)

    def install(self, keys):
        if not SUPPORTED:
            return
        for k in keys:
            if WINDOWS and k not in WIN_APPS:
                continue
            if k not in self.queue and not self.info[k].get("busy"):
                self.queue.append(k)
                self.info[k].update(busy=True, msg="wartet …", status=None, hint=None)
        self._emit()
        if not self.worker:
            self.worker = threading.Thread(target=self._work, daemon=True)
            self.worker.start()

    def _work(self):
        while self.queue:
            key = self.queue[0]

            def step(text, k=key):
                self.info[k]["msg"] = text
                GLib.idle_add(self._emit, k)
            try:
                ok, text, hint = install(key, step)
            except Exception as e:  # noqa: BLE001
                ok, text, hint = False, f"fehler: {e}", None
            restarted = restart_background(key) if ok else False
            self.queue.pop(0)
            self.info[key].update(busy=False, status="ok" if ok else "err", msg=text, hint=hint,
                                  restart=(restarted is None and running(key)) or (ok and key == "scourge"))
            GLib.idle_add(self._emit, key)
        self.worker = None
        GLib.idle_add(self._emit)


_manager = None


def manager():
    global _manager
    if _manager is None:
        _manager = Manager()
    return _manager
