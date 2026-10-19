# Startseite eines Scourge-Tabs: von hier geht es zu allen Funktionen.
import os, shutil

import chrome as ch

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

HOME_CSS = """
.home { margin: 6px 16px 16px 16px; }
.home-hero { font-size: 26px; font-weight: 800; color: @color9; }
.home-sub { font-size: 13px; color: alpha(@foreground, 0.6); }
.home-section { font-size: 12px; font-weight: bold; color: @primary; margin-top: 6px; }
button.home-card {
    background-color: @surface_container; background-image: none; box-shadow: none;
    border: 2px solid alpha(@color9, 0.3); border-radius: 16px; padding: 16px; color: @foreground;
    transition: all 180ms cubic-bezier(0.4, 0, 0.2, 1);
}
button.home-card:hover, button.home-card:focus-visible { border-color: @color9; background-color: alpha(@color9, 0.12); }
button.home-card.primary { border-color: @color9; }
.glass button.home-card:not(:hover) { background-color: alpha(@surface_container, 0.42); }
.card-icon { font-size: 30px; color: @color9; }
.card-name { font-size: 14px; font-weight: 800; }
.card-desc { font-size: 11px; color: alpha(@foreground, 0.6); }
button.recent { background: none; box-shadow: none; border: none; border-radius: 10px; padding: 4px 8px; color: @foreground; }
button.recent:hover { background-color: alpha(@color9, 0.12); }
.recent-path { font-size: 10px; color: alpha(@foreground, 0.45); }
button.recent-x { min-width: 0; min-height: 0; padding: 2px 8px; background: none; border: none; box-shadow: none;
    border-radius: 8px; color: alpha(@foreground, 0.0); }
.recent-row:hover button.recent-x { color: alpha(@foreground, 0.5); }
button.recent-x:hover, button.recent-x:focus-visible { color: @foreground; background-color: alpha(@color9, 0.15); }
.home.drag-over { background-color: alpha(@color9, 0.08); border-radius: 16px; }
.tabbar { padding: 6px 16px 2px 16px; }
.tab-heading { font-size: 16px; font-weight: 800; }
notebook.tabs > header { background: none; border: none; box-shadow: none; padding: 10px 12px 2px 12px; }
notebook.tabs > header > tabs > tab {
    background-color: @background; border: 2px solid alpha(@color9, 0.3); border-radius: 12px; margin: 0 3px;
    padding: 2px 4px 2px 12px; min-height: 0; outline: none; box-shadow: none; color: alpha(@foreground, 0.75); font-weight: 700;
}
notebook.tabs > header > tabs > tab:hover { border-color: @color9; box-shadow: none; }
notebook.tabs > header > tabs > tab:checked { background-color: @color9; color: @background; border-color: @color9; box-shadow: none; }
notebook.tabs > header > tabs > tab:focus, notebook.tabs > header > tabs > tab:focus-visible { box-shadow: none; outline: none; }
.glass notebook.tabs > header > tabs > tab:not(:checked) { background-color: alpha(@background, 0.3); }
notebook.tabs > stack { background: none; }
button.tab-close { min-width: 0; min-height: 0; padding: 0 5px; background: none; border: none; box-shadow: none; color: inherit; }
button.tab-close:hover { background-color: alpha(@foreground, 0.15); border-radius: 8px; }
"""

ACTIONS = [
    ("file", "󰈈", "Datei prüfen", "Bild, Video, PDF, Office, Audio, Archiv … öffnen oder hierher ziehen und durchleuchten", True),
    ("record", "󰑋", "Bildschirm aufnehmen", "Ganzer Bildschirm, Bereich oder Fenster, mit oder ohne Ton", True),
    ("cut", "󰆐", "Video schneiden", "Zeitleiste, Schneiden, Teile entfernen, Export als MP4, WebM oder GIF", True),
    ("meme", "󰏫", "Meme-Editor", "Bild oder Video wählen: Text oben/unten, Stift, Zuschneiden", False),
    ("studio", "󰏘", "Verlauf & ASCII", "Ästhetische Farbverläufe als PNG/JPG – und Bilder als ASCII-Kunst", False),
]


# (icon, name, beschreibung, sektion)
SYSTEM = [
    ("󰃟", "Bildschirm", "Helligkeit des Displays regeln", "display"),
    ("󰕾", "Ton", "Lautstärke, Mikrofon und Ausgabegerät wählen", "audio"),
    ("󰍽", "Eingabe", "Maus-Empfindlichkeit, Touchpad und Tastaturlayout", "input"),
    ("󰖩", "Funk", "WLAN und Bluetooth ein- und ausschalten", "radio"),
    ("\U000F01BF", "Cursor", "Farbe, Größe und Verhalten des Mauszeigers", "cursor"),
    ("󰕮", "Waybar", "Statusleiste neu starten, wenn sie abgestürzt ist", "waybar"),
]


def card(icon, name, desc, cb, primary=False):
    b = Gtk.Button()
    b.add_css_class("home-card")
    if primary:
        b.add_css_class("primary")
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    box.set_size_request(200, 78)
    i = Gtk.Label(label=icon, xalign=0)
    i.add_css_class("card-icon")
    n = Gtk.Label(label=name.lower(), xalign=0, hexpand=True, ellipsize=3)
    n.add_css_class("card-name")
    d = Gtk.Label(label=desc, xalign=0, wrap=True, max_width_chars=26, lines=2, ellipsize=3)
    d.add_css_class("card-desc")
    box.append(ch.hbox(i, n, spacing=8))
    box.append(d)
    b.set_child(box)
    b.connect("clicked", lambda *_: cb())
    return b


def flow():
    return Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=12, row_spacing=12,
                       max_children_per_line=6, min_children_per_line=1, homogeneous=True)


class HomePage(Gtk.ScrolledWindow):
    def __init__(self, tab):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.tab = tab
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.add_css_class("home")
        self.box = box
        self.set_child(box)
        page = box

        def heading(title, desc):
            """Rubrik als Kasten mit Titel im Rahmen; liefert den Kasten, Inhalt kommt per append() hinein."""
            c = ch.Card(title, spacing=8)
            d = Gtk.Label(label=desc, xalign=0, wrap=True)
            d.add_css_class("home-sub")
            c.append(d)
            page.append(c)
            return c

        box = heading("misc", "Dateien durchleuchten, Bildschirm aufnehmen, Videos schneiden und Memes gestalten. "
                              "Dateien lassen sich auch einfach hierher ziehen.")
        fl = flow()
        cbs = {"file": tab.on_open, "record": tab.open_recorder, "cut": self.pick_video, "meme": self.pick_meme,
               "studio": self.open_studio}
        for key, icon, name, desc, primary in ACTIONS:
            fl.append(card(icon, name, desc, cbs[key], primary))
        box.append(fl)

        self.recent_title = ch.Card("zuletzt geöffnet", spacing=4)
        self.wipe_btn = Gtk.Button(label="󰃢 liste leeren", tooltip_text="Alle Einträge aus der Liste entfernen (Dateien bleiben)",
                                   halign=Gtk.Align.END)
        self.wipe_btn.add_css_class("pb"); self.wipe_btn.add_css_class("small")
        self.wipe_btn.connect("clicked", lambda *_: self.wipe())
        self._wipe_armed = None
        self.recent_box = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3,
                                      column_spacing=6, row_spacing=2, homogeneous=True)
        self.recent_title.append(self.recent_box)
        self.recent_title.append(self.wipe_btn)
        page.append(self.recent_title)


        box = heading("system & hardware", "Bildschirm, Ton, Eingabegeräte, Funk, Cursor und Waybar dieses Rechners steuern.")
        fl = flow()
        for icon, name, desc, section in SYSTEM:
            if section == "waybar" and not shutil.which("waybar"):
                continue
            fl.append(card(icon, name, desc, lambda section=section: self.open_settings(section)))
        try:
            import publish
            if publish.available():
                fl.append(card("󰅧", "Veröffentlichen", "Eigene Apps prüfen und einzeln oder alle auf GitHub hochladen",
                               lambda: tab.open_page("tool-publish", lambda: publish.PublishPage(tab))))
        except Exception:
            pass
        box.append(fl)

        target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        target.set_gtypes([Gdk.FileList.__gtype__, Gdk.Texture.__gtype__])
        target.connect("enter", lambda *_: (page.add_css_class("drag-over"), Gdk.DragAction.COPY)[1])
        target.connect("leave", lambda *_: page.remove_css_class("drag-over"))
        target.connect("drop", lambda t, v, x, y: (page.remove_css_class("drag-over"), tab.on_drop(t, v, x, y))[1])
        self.add_controller(target)
        GLib.idle_add(lambda: (self.refresh(), False)[-1])

    def refresh(self):
        while (c := self.recent_box.get_first_child()):
            self.recent_box.remove(c)
        items = self.tab.app_main.recent()
        self.recent_title.set_visible(bool(items))
        for p in items[:9]:
            row = Gtk.Box(spacing=0)
            row.add_css_class("recent-row")
            b = Gtk.Button(halign=Gtk.Align.FILL, hexpand=True)
            b.add_css_class("recent")
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            n = Gtk.Label(label=os.path.basename(p), xalign=0, ellipsize=3, max_width_chars=30)
            n.add_css_class("finding-title")
            d = Gtk.Label(label=os.path.dirname(p).replace(os.path.expanduser("~"), "~", 1), xalign=0, ellipsize=1,
                          max_width_chars=34)
            d.add_css_class("recent-path")
            col.append(n)
            col.append(d)
            b.set_child(col)
            b.connect("clicked", lambda *_, p=p: self.tab.load_path(p))
            x = Gtk.Button(label="󰅖", tooltip_text="Aus der Liste entfernen", valign=Gtk.Align.CENTER)
            x.add_css_class("recent-x")
            x.connect("clicked", lambda *_, p=p: (self.tab.app_main.remove_recent(p), self.refresh()))
            row.append(b)
            row.append(x)
            self.recent_box.append(row)

    def wipe(self):
        # Zweistufig, damit ein Fehlklick nicht gleich alles löscht
        if self._wipe_armed is None:
            self.wipe_btn.set_label("󰃢 wirklich leeren?")
            self._wipe_armed = GLib.timeout_add(3000, self._disarm)
            return
        GLib.source_remove(self._wipe_armed)
        self._disarm()
        self.tab.app_main.clear_recent()
        self.refresh()
        self.tab.notify("Liste geleert")

    def _disarm(self):
        self._wipe_armed = None
        self.wipe_btn.set_label("󰃢 liste leeren")
        return False

    def open_studio(self):
        from studio import StudioPage
        self.tab.open_page("tool-studio", lambda: StudioPage(self.tab))

    def open_settings(self, section="input"):
        from system import SystemSettings
        self.tab.open_page("tool-settings-" + section, lambda: SystemSettings(self.tab, section))

    def pick(self, title, mimes, done):
        f = Gtk.FileFilter(name=title)
        for m in mimes:
            f.add_mime_type(m)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(f)
        dlg = Gtk.FileDialog(title=title, filters=filters)

        def cb(d, res):
            try:
                path = d.open_finish(res).get_path()
            except GLib.Error:
                return
            done(path)
        dlg.open(self.tab.win, None, cb)

    def pick_video(self):
        self.pick("Video zum Schneiden", ["video/*"], self.tab.open_cutter)

    def pick_meme(self):
        def go(path):
            self.tab.load_path(path)
            if self.tab.image is not None and self.tab.doc is None:
                self.tab.on_edit()
        self.pick("Bild oder Video für ein Meme", ["image/*", "video/*"], go)
