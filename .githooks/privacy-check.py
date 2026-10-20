#!/usr/bin/env python3
"""Datenschutz-Prüfung für das öffentliche Scourge-Repo. Bricht ab, wenn persönliche Daten mitkommen würden.

  privacy-check.py           prüft alle Dateien im Arbeitsverzeichnis
  privacy-check.py --staged  prüft die vorgemerkten Dateien (läuft als pre-commit-Hook)

Eigene Suchbegriffe (Klarname, Adresse, Mail, …) stehen NICHT im Repo, sondern in
~/.config/notes-publish/blocklist.txt (ein Begriff pro Zeile, Groß/klein egal, # = Kommentar).
"""
import os, re, subprocess, sys

ROOT = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip() \
    or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLOCKLIST = os.path.expanduser("~/.config/notes-publish/blocklist.txt")

ALLOWED = re.compile(r"^([^/]+\.(ps1|bat|ico)|app/logo/[^/]+\.ico|app/[^/]+\.py|app/scourge\.sh|app/logo/[^/]+\.svg|app/fonts/[^/]+\.(ttf|txt)|app/VERSION|app/CHANGELOG\.md|fonts/[^/]+\.ttf|"
                     r"[^/]+\.(sh|md|in)|\.gitignore|\.githooks/[^/]+)$")
SECRETS = [
    (r"/home/[A-Za-z0-9_.-]+", "absoluter Pfad mit Benutzername"),
    (r"/Users/[A-Za-z0-9_.-]+", "absoluter Pfad mit Benutzername"),
    (r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}", "E-Mail-Adresse"),
    (r"\b(gh[opsur]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})", "GitHub-Token"),
    (r"\baas_et/[A-Za-z0-9_-]{10,}", "Google-Master-Token"),
    (r"\bya29\.[A-Za-z0-9_-]{10,}|\b1//0[A-Za-z0-9_-]{20,}", "Google-OAuth-Token"),
    (r"\bAIza[0-9A-Za-z_-]{30,}", "Google-API-Schlüssel"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "privater Schlüssel"),
    (r"\bsk-[A-Za-z0-9_-]{20,}", "API-Schlüssel"),
]
OK_MAIL = re.compile(r"@(users\.noreply\.github\.com|example\.(com|org|net)|beispiel\.(de|com|org)|localhost)$", re.I)


# Eingebettete Bilder (data:…;base64,… in SVGs) und .ico-Dateien sind Binärdaten: Darin findet die Textsuche
# zufällig „Tokens“ oder Sperrbegriffe (Fehlalarm). Stattdessen wird geprüft, ob die Bilder Metadaten tragen
# (Autor, Kamera, Software, GPS …) – nur die könnten Persönliches verraten.
DATA_URI = re.compile(r"data:([a-z]+/[a-z0-9.+-]+);base64,([A-Za-z0-9+/=\s]+)", re.I)
PNG_SIG = b"\x89PNG\r\n\x1a\n"
PNG_META = {b"tEXt", b"iTXt", b"zTXt", b"eXIf"}


def image_meta(raw):
    """Gründe, warum ein Bild Metadaten enthält (leer = nur Pixel)."""
    found = set()
    start = raw.find(PNG_SIG)
    while start != -1:
        i = start + 8
        while i + 8 <= len(raw):
            length = int.from_bytes(raw[i:i + 4], "big")
            kind = raw[i + 4:i + 8]
            if kind in PNG_META:
                found.add("PNG-Metadaten (" + kind.decode() + ")")
            if kind == b"IEND" or length > len(raw):
                break
            i += 12 + length
        start = raw.find(PNG_SIG, i)
    if raw[:2] == b"\xff\xd8" and (b"Exif\x00\x00" in raw[:65536] or b"http://ns.adobe.com/xap" in raw[:65536]):
        found.add("JPEG-Metadaten (EXIF/XMP)")
    return sorted(found)


def strip_images(text):
    """Base64-Bilder aus dem Text nehmen; liefert (Text ohne Bilddaten, Metadaten-Funde)."""
    import base64
    found = []

    def repl(m):
        try:
            found.extend(image_meta(base64.b64decode(re.sub(r"\s+", "", m.group(2)))))
        except ValueError:
            found.append("eingebettete Daten nicht lesbar")
        return "data:" + m.group(1) + ";base64,…"
    return DATA_URI.sub(repl, text), found


def personal_terms():
    terms = set()
    try:
        for line in open(BLOCKLIST, encoding="utf-8"):
            line = line.split("#", 1)[0].strip()
            if len(line) >= 3:
                terms.add(line.lower())
    except OSError:
        pass
    for key in ("user.email", "user.name"):          # globale Git-Identität (nicht die anonyme im Repo)
        v = subprocess.run(["git", "config", "--global", key], capture_output=True, text=True).stdout.strip()
        if len(v) >= 3:
            terms.add(v.lower())
    try:
        import pwd
        gecos = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0].strip()
        if len(gecos) >= 3:
            terms.add(gecos.lower())
    except Exception:
        pass
    return terms


def files(staged):
    if staged:
        out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
                             capture_output=True, text=True, cwd=ROOT).stdout
        names = [n for n in out.split("\0") if n]
        for n in names:
            data = subprocess.run(["git", "show", ":" + n], capture_output=True, cwd=ROOT).stdout
            yield n, data
    else:
        for d, dirs, fs in os.walk(ROOT):
            dirs[:] = [x for x in dirs if x not in (".git", "__pycache__")]
            for f in fs:
                p = os.path.join(d, f)
                with open(p, "rb") as fh:
                    yield os.path.relpath(p, ROOT), fh.read()


def main():
    staged = "--staged" in sys.argv
    terms = personal_terms()
    problems = []
    if not os.path.exists(BLOCKLIST):
        problems.append(("-", f"{BLOCKLIST} fehlt – bitte anlegen (eigener Name, Mail, …)"))
    mail = subprocess.run(["git", "config", "user.email"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    if not mail.endswith("@users.noreply.github.com"):
        problems.append(("git config", f"Commit-Mail ist „{mail}“ – erlaubt ist nur eine …@users.noreply.github.com-Adresse"))
    for name, data in files(staged):
        if not ALLOWED.match(name):
            problems.append((name, "Dateityp/Ort nicht freigegeben (z. B. Notizdaten, Caches, Sicherungen)"))
            continue
        if name.endswith(".ttf"):
            continue
        if name.endswith(".ico"):                  # Binärbild: nur auf Metadaten prüfen
            problems += [(name, m) for m in image_meta(data)]
            continue
        text, meta = strip_images(data.decode("utf-8", "replace"))
        problems += [(name, m) for m in meta]
        low = text.lower()
        for i, line in enumerate(text.splitlines(), 1):
            for rx, what in SECRETS:
                for m in re.finditer(rx, line):
                    if what == "E-Mail-Adresse" and OK_MAIL.search(m.group(0)):
                        continue
                    problems.append((f"{name}:{i}", f"{what}: {m.group(0)[:40]}"))
        for t in terms:
            if t in low:
                problems.append((name, "persönlicher Begriff aus der Sperrliste gefunden"))
    if problems:
        print("\033[1;31mDATENSCHUTZ-PRÜFUNG FEHLGESCHLAGEN – nichts wurde veröffentlicht:\033[0m", file=sys.stderr)
        for where, what in problems:
            print(f"  {where}: {what}", file=sys.stderr)
        return 1
    print("Datenschutz-Prüfung: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
