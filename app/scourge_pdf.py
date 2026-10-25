#!/usr/bin/env python3
# scourge-pdf: der schlanke PDF-Editor von Scourge. Eigener Prozess und eigenes Fenster, lädt nur viewer.py und die
# Chrome-Basis (kein OSINT, keine Werkzeuge, keine Bilder-Bibliotheken) und startet dadurch sofort.
# Farben kommen live aus ~/.cache/matugen/colors.css. Start: scourge-pdf [datei.pdf …] | --daemon | --quit
import ast, os, sys

if os.name == "nt":   # Windows/MSYS2: Programme liegen neben python.exe
    os.environ["PATH"] = os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", "")

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.expanduser("~/.local/share/rice-apps"))

APP_ID = "de.invincible.ScourgePdf"
MATUGEN_CSS = os.path.expanduser("~/.cache/matugen/colors.css")


def load_icon_font():
    path = os.path.join(HERE, "fonts", "notes-icons.ttf")
    if not os.path.exists(path):
        return
    try:
        gi.require_version("PangoCairo", "1.0")
        from gi.repository import PangoCairo
        fm = PangoCairo.FontMap.get_default()
        if hasattr(fm, "add_font_file") and fm.add_font_file(path):
            return
    except Exception:
        pass
    try:
        import ctypes, ctypes.util
        fc = ctypes.CDLL(ctypes.util.find_library("fontconfig") or "libfontconfig.so.1")
        fc.FcConfigGetCurrent.restype = ctypes.c_void_p
        fc.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        fc.FcConfigAppFontAddFile(fc.FcConfigGetCurrent(), path.encode())
    except Exception:
        pass


load_icon_font()
import chrome as ch  # noqa: E402
import glass  # noqa: E402
from viewer import VIEWER_CSS, PdfView  # noqa: E402
import netfs  # noqa: E402


def _const_text(node):
    """Text einer Zuweisung: einfache Zeichenkette oder Summe aus Zeichenketten; Nicht-Konstantes wird übersprungen."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _const_text(node.left) + _const_text(node.right)
    return ""


def scourge_css():
    """Stile aus scourge.py und editor.py holen, ohne sie zu importieren (das lädt PIL, OSINT usw.): die Texte
    stehen dort als einfache Zuweisungen und lassen sich per ast auslesen."""
    want = {"scourge.py": {"FALLBACK_COLORS", "APP_CSS", "CHROME_CSS", "SUITE_CSS"}, "editor.py": {"EDITOR_CSS"}}
    out = {}
    for fname, names in want.items():
        try:
            with open(os.path.join(HERE, fname), encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for node in tree.body:
                if isinstance(node, ast.Assign) and len(node.targets) == 1 and getattr(node.targets[0], "id", "") in names:
                    out[node.targets[0].id] = _const_text(node.value)
        except (OSError, SyntaxError, ValueError):
            pass
    return out


PDF_CSS = """
.pdfwin .pdf-bar { margin: 0; }
"""


class PdfTab(Gtk.Box):
    """Ein Tab = ein PDF im Editor."""

    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0, margin_top=8, margin_bottom=8,
                         margin_start=14, margin_end=14)
        self.app = app
        self.path = None
        self.title = "start"
        self.view = PdfView(editable=True, notify=app.notify)
        self.empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER,
                             halign=Gtk.Align.CENTER, vexpand=True, hexpand=True)
        big = Gtk.Label(label="󰈦")
        big.add_css_class("card-icon")
        self.empty.append(big)
        self.empty.append(ch.label("PDF hierher ziehen oder öffnen", "dim"))
        self.empty.append(ch.button("󰝰 öffnen", "PDF öffnen (Strg+O)", action="win.open"))
        self.stack = Gtk.Stack(vexpand=True, hexpand=True)
        self.stack.add_named(self.empty, "empty")
        self.stack.add_named(self.view, "pdf")
        self.append(self.stack)
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self.on_drop)
        self.add_controller(drop)

    def on_drop(self, _t, files, *_):
        paths = [f.get_path() for f in files.get_files() if f.get_path()]
        for i, p in enumerate(paths):
            self.app.open_path(p, self if i == 0 and self.path is None else None)
        return bool(paths)

    def load(self, path, password=None):
        try:
            if netfs.is_remote(path):
                doc = netfs.poppler_doc(path)
            else:
                from viewer import _poppler
                doc = _poppler().Document.new_from_file(GLib.filename_to_uri(os.path.abspath(path)), password)
        except GLib.Error as e:
            if "ncrypt" in (e.message or "") or "assword" in (e.message or ""):
                self.ask_password(path)
            else:
                self.app.notify(f"PDF ließ sich nicht öffnen: {e.message}")
            return False
        self.path = path
        self.view.load(doc, path)
        self.stack.set_visible_child_name("pdf")
        self.title = os.path.basename(path)
        self.app.update_label(self)
        self.view.grab_focus()
        return True

    def ask_password(self, path):
        dlg = Gtk.Window(title="PDF geschützt", transient_for=self.app.win, modal=True, default_width=340)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=14, margin_bottom=14,
                      margin_start=14, margin_end=14)
        box.append(Gtk.Label(label=os.path.basename(path) + " ist passwortgeschützt.", xalign=0, wrap=True))
        ent = ch.password_entry()
        box.append(ent)
        ok = ch.button("öffnen")
        box.append(ok)
        dlg.set_child(box)

        def go(*_):
            pw = ent.get_text()
            dlg.close()
            self.load(path, pw)
        ent.connect("activate", go)
        ok.connect("clicked", go)
        dlg.present()

    def shutdown(self):
        try:
            self.view.cancel_edit()
        except Exception:
            pass


class PdfApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.win = None
        self.daemon = False
        self._held = False
        self._quitting = False
        self._toast_id = None

    # ---------- Start / Aufrufe ----------
    def do_startup(self):
        Gtk.Application.do_startup(self)
        act = Gio.SimpleAction.new("quit", None)          # scourge-pdf --quit
        act.connect("activate", lambda *_: self.really_quit())
        self.add_action(act)

    def do_activate(self):
        if not self.win:
            self.build()
        if self.daemon:
            self.daemon = False
            self.keep_running()
            return
        getattr(glass, "bring_here", lambda w: None)(self.win)
        self.win.present()
        self.check_update()

    def do_open(self, files, n, hint):
        self.do_activate()
        for f in files:
            if f.get_path():
                self.open_path(f.get_path())

    def keep_running(self):
        if not self._held:
            self._held = True
            self.hold()

    # ---------- Fenster ----------
    def load_css(self, *_):
        parts = scourge_css()
        css = parts.get("FALLBACK_COLORS", "")
        try:
            css += open(MATUGEN_CSS).read()
        except OSError:
            pass
        try:
            from swipe import CSS as SWIPE_CSS
        except ImportError:
            SWIPE_CSS = ""
        try:
            from home import HOME_CSS         # Tabs und Kopfleiste
        except Exception:
            HOME_CSS = ""
        css += (parts.get("APP_CSS", "") + parts.get("EDITOR_CSS", "") + VIEWER_CSS + HOME_CSS + glass.GLASS_CSS + ch.CSS + parts.get("CHROME_CSS", "")
                + parts.get("SUITE_CSS", "") + SWIPE_CSS + PDF_CSS)
        self.css.load_from_string(css)

    def build(self):
        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER)
        self.load_css()
        try:
            self.mon = Gio.File.new_for_path(os.path.dirname(MATUGEN_CSS)).monitor_directory(
                Gio.FileMonitorFlags.WATCH_MOVES, None)
            self.mon.connect("changed", lambda *_: GLib.timeout_add(250, lambda: (self.load_css(), False)[-1]))
        except GLib.Error:
            pass
        self.win = Gtk.ApplicationWindow(application=self, title="scourge-pdf")
        self.win.add_css_class("pb-window")       # Schrift, Hintergrund und Glas wie in Scourge
        self.win.add_css_class("pdfwin")
        self.win.set_default_size(*getattr(glass, "fit_size", lambda w, h: (w, h))(1100, 860))
        getattr(glass, "frame_window", lambda w: w.set_decorated(False))(self.win)
        self.win.connect("close-request", self.on_close_request)
        Gtk.Window.set_default_icon_name("scourge")
        for name, cb, keys in (("open", lambda *_: self.on_open(), ["<Control>o", "<Control>t"]),
                               ("close_tab", lambda *_: self.close_tab(), ["<Control>w"]),
                               ("quit", lambda *_: self.win.close(), ["<Control>q"])):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", cb)
            self.win.add_action(act)
            self.set_accels_for_action(f"win.{name}", keys)
        for i in range(1, 10):
            act = Gio.SimpleAction.new(f"tab{i}", None)
            act.connect("activate", lambda *_, n=i - 1: n < self.notebook.get_n_pages() and self.notebook.set_current_page(n))
            self.win.add_action(act)
            self.set_accels_for_action(f"win.tab{i}", [f"<Alt>{i}"])

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.win.set_child(root)
        glass.underlay(self.win)
        self.notebook = Gtk.Notebook(scrollable=True, show_border=False, vexpand=True)
        self.notebook.add_css_class("tabs")
        root.append(self.notebook)

        brand = Gtk.Button(label="scourge-pdf", valign=Gtk.Align.CENTER, focus_on_click=False, margin_end=6,
                           tooltip_text="Scourge (volle App) öffnen")
        brand.add_css_class("brand")
        brand.connect("clicked", lambda *_: self.open_scourge())
        self.notebook.set_action_widget(brand, Gtk.PackType.START)
        right = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER, margin_start=10)
        right.append(ch.button("󰐕 öffnen", "PDF in neuem Tab öffnen (Strg+O)", action="win.open"))
        try:
            self.version = open(os.path.join(HERE, "VERSION")).read().strip()
        except OSError:
            self.version = ""
        self.ver_btn = Gtk.Button(label=f"v{self.version}", tooltip_text="changelog – was ist neu?",
                                  valign=Gtk.Align.CENTER, focus_on_click=False)
        self.ver_btn.add_css_class("tflat")
        self.ver_btn.add_css_class("off")
        self.ver_btn.connect("clicked", lambda *_: self.show_changelog())
        right.append(self.ver_btn)
        import changelog
        self.upd_btn = changelog.update_button("scourge", self.version, notify=self.notify, restart=self.restart_self)
        right.append(self.upd_btn)
        self.glass_toggle = glass.GlassToggle(self.win)
        import settings_menu
        self.settings_btn = settings_menu.build(self, self.glass_toggle, cmd="scourge-pdf", name="scourge-pdf",
                                                bgkey="pdf_background", version=self.version,
                                                changelog_cb=self.show_changelog,
                                                update_cb=lambda: self.upd_btn.emit("clicked"))
        right.append(self.settings_btn)
        close = Gtk.Button(label="✕", tooltip_text="Schließen (Strg+Q)", action_name="win.quit",
                           valign=Gtk.Align.CENTER, focus_on_click=False)
        close.add_css_class("tclose")
        right.append(close)
        self.notebook.set_action_widget(right, Gtk.PackType.END)
        self.notebook.connect("switch-page", lambda nb, page, i: self.win.set_title(
            f"{page.title} – scourge-pdf" if page.path else "scourge-pdf"))
        for sig in ("page-added", "page-removed", "page-reordered"):
            self.notebook.connect(sig, lambda *_: GLib.idle_add(lambda: (self.number_tabs(), False)[-1]))

        status = Gtk.Box(spacing=12)
        status.add_css_class("tstatus")
        self.toast = ch.label("", "msg", hexpand=True, ellipsize=True)
        status.append(self.toast)
        for k, v in (("strg+o", "öffnen"), ("strg+w", "tab zu"), ("alt+1-9", "tab wählen")):
            status.append(ch.hint(k, v))
        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.EXTERNAL, vscrollbar_policy=Gtk.PolicyType.NEVER,
                                propagate_natural_height=True)
        sc.set_child(status)
        root.append(sc)
        self.new_tab()

    # ---------- Tabs ----------
    def tabs(self):
        nb = getattr(self, "notebook", None)
        return [nb.get_nth_page(i) for i in range(nb.get_n_pages())] if nb else []

    def current(self):
        i = self.notebook.get_current_page()
        return self.notebook.get_nth_page(i) if i >= 0 else None

    def new_tab(self):
        tab = PdfTab(self)
        box = Gtk.Box(spacing=3)
        key = Gtk.Label(label="")
        key.add_css_class("key")
        lbl = Gtk.Label(label="start", ellipsize=3, max_width_chars=24, width_chars=3, hexpand=True, xalign=0)
        close = Gtk.Button(label="✕", tooltip_text="Tab schließen (Strg+W)", focus_on_click=False)
        close.add_css_class("tab-close")
        close.connect("clicked", lambda *_: self.close_tab(tab))
        for w in (key, lbl, close):
            box.append(w)
        middle = Gtk.GestureClick(button=2)
        middle.connect("released", lambda *_: self.close_tab(tab))
        box.add_controller(middle)
        tab._tab_key, tab._tab_label = key, lbl
        i = self.notebook.append_page(tab, box)
        self.notebook.set_tab_reorderable(tab, True)
        self.notebook.get_page(tab).set_property("tab-expand", True)
        self.notebook.set_current_page(i)
        return tab

    def number_tabs(self):
        sup = "¹²³⁴⁵⁶⁷⁸⁹"
        for i, t in enumerate(self.tabs()):
            t._tab_key.set_label(sup[i] if i < 9 else "")

    def update_label(self, tab):
        tab._tab_label.set_label(tab.title)
        tab._tab_label.set_tooltip_text(tab.path or tab.title)
        if tab is self.current():
            self.win.set_title(f"{tab.title} – scourge-pdf")

    def open_path(self, path, tab=None):
        if not path or not os.path.exists(path):
            return self.notify("Datei nicht gefunden.")
        for t in self.tabs():                      # schon offen → dorthin springen
            if t.path == path:
                self.notebook.set_current_page(self.notebook.page_num(t))
                return t
        tab = tab or self.current()
        if tab is None or tab.path is not None:
            tab = self.new_tab()
        tab.load(path)
        return tab

    def on_open(self):
        dlg = Gtk.FileDialog(title="PDF öffnen")
        flt = Gtk.FileFilter(name="PDF-Dokumente")
        flt.add_mime_type("application/pdf")
        flt.add_suffix("pdf")
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(flt)
        dlg.set_filters(store)

        def done(d, res):
            try:
                files = d.open_multiple_finish(res)
            except GLib.Error:
                return
            for i in range(files.get_n_items()):
                p = files.get_item(i).get_path()
                if p:
                    self.open_path(p)
        dlg.open_multiple(self.win, None, done)

    def close_tab(self, tab=None, force=False):
        tab = tab or self.current()
        if tab is None:
            return
        if not force and tab.view.dirty:
            return self.confirm_unsaved([tab], lambda: self.close_tab(tab, force=True))
        tab.shutdown()
        label = self.notebook.get_tab_label(tab)
        self.notebook.remove_page(self.notebook.page_num(tab))
        if self.notebook.get_n_pages() == 0:
            self.new_tab()
        GLib.timeout_add_seconds(3, lambda: (ch.release_tree(tab), label and ch.release_tree(label),
                                             ch.trim_memory(), False)[-1])

    def close_all_tabs(self, then=None):
        def go():
            for tab in list(self.tabs()):
                self.close_tab(tab, force=True)
            if self.notebook.get_n_pages() == 0:
                self.new_tab()
            if then:
                then()
        self.confirm_unsaved(self.tabs(), go)

    def confirm_unsaved(self, tabs, proceed):
        """Ungespeichertes mit Pfad wird still gesichert (wie das automatische Speichern); sonst Nachfrage."""
        for t in tabs:
            v = t.view
            if v.dirty and v.path and v.editable:
                v._backup_original(v.path)
                v.save(quiet=True)
        dirty = [t for t in tabs if t.view.dirty]
        if not dirty:
            return proceed()
        if not self.win.get_visible():
            self.win.present()
        names = ", ".join(os.path.basename(t.view.path or "PDF") for t in dirty)
        dlg = Gtk.AlertDialog(message="Ungespeicherte Anmerkungen",
                              detail=f"{names}: Die Änderungen sind noch nicht gespeichert.",
                              buttons=["Abbrechen", "Verwerfen", "Speichern"], cancel_button=0, default_button=2)

        def done(d, res):
            try:
                i = d.choose_finish(res)
            except GLib.Error:
                return
            if i == 1:
                for t in dirty:
                    t.view.dirty = False
                    t.view._sync_tools()
                proceed()
            elif i == 2 and all(t.view.save() for t in dirty):
                proceed()
        dlg.choose(self.win, None, done)

    # ---------- Schließen ----------
    def on_close_request(self, *_):
        """Schließen = alle Tabs zu (nach Sichern) und im Hintergrund bleiben; Windows/Hintergrund aus = beenden."""
        if self._quitting:
            return False
        import settings_menu
        if not settings_menu.background_mode("pdf_background"):
            self.really_quit()
            return True

        def hide():
            self.keep_running()
            self.win.set_visible(False)
        self.close_all_tabs(hide)
        return True

    def really_quit(self):
        def go():
            self._quitting = True
            if self._held:
                self._held = False
                self.release()
            if self.win:
                self.win.close()
            self.quit()
        if self.win:
            self.confirm_unsaved(self.tabs(), go)
        else:
            go()

    # ---------- Sonstiges ----------
    def notify(self, msg):
        self.toast.set_label("▸ " + msg)
        if self._toast_id:
            GLib.source_remove(self._toast_id)
        self._toast_id = GLib.timeout_add_seconds(6, self._clear_toast)

    def _clear_toast(self):
        self._toast_id = None
        self.toast.set_label("")
        return False

    def open_scourge(self):
        import subprocess
        if os.name == "nt":
            subprocess.Popen([sys.executable.replace("python.exe", "pythonw.exe"), os.path.join(HERE, "scourge.py")])
        else:
            subprocess.Popen(["bash", os.path.join(HERE, "scourge.sh")], start_new_session=True)


    def show_changelog(self):
        import changelog
        changelog.open_window(self.win, "scourge-pdf", os.path.join(HERE, "CHANGELOG.md"), key="scourge",
                              version=self.version or None)

    def restart_self(self):
        import subprocess
        launcher = os.path.join(HERE, "scourge-pdf.sh")
        subprocess.Popen(["sh", "-c", f"sleep 1; '{launcher}' --quit; sleep 1; '{launcher}'"], start_new_session=True)

    def check_update(self):
        """Neuere Version? → der Update-Knopf neben der Versionsnummer meldet sich selbst (alle 6 h, Cache)."""
        if getattr(self, "upd_btn", None):
            self.upd_btn.recheck()


if __name__ == "__main__":
    if "--quit" in sys.argv:
        app = PdfApp()
        app.register(None)
        if app.get_is_remote():
            app.activate_action("quit", None)
        sys.exit(0)
    if "--daemon" in sys.argv:
        app = PdfApp()
        app.register(None)
        if app.get_is_remote():
            sys.exit(0)
        app.daemon = True
        code = app.run([sys.argv[0]])
        sys.stdout.flush(); sys.stderr.flush()
        os._exit(code)
    code = PdfApp().run(sys.argv)
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(code)
