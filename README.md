
# scourge

inspect files (metadata/exif), edit pdfs, record screen,
videos cut (edit videos), build memes, generate gradients & ascii art – a gtk4 app for linux.

## install – linux

the repository is public – no account needed. clone it with git (package `git`):

```bash
git clone https://github.com/invincible-nix/scourge-public ~/scourge-src   # download once
cd ~/scourge-src
./install.sh
```

the script installs dependencies (asks beforehand, password once), copies scourge to
`~/.local/share/scourge` and creates the launcher `scourge` and a menu entry.
the icons are bundled with scourge (`app/fonts/notes-icons.ttf`). packages that don't exist on your distribution
are skipped and listed at the end.

## install – windows 10/11

1. download repo (on github **code → download zip** and extract, or `git clone …`).
2. double-click **`install-windows.bat`** in the folder.

the script sets up **msys2** once (provides gtk 4, python, poppler, gstreamer and ffmpeg for windows,
~1.5 gb, asks for admin rights), installs the font and adds scourge to the start menu.
removal: `uninstall-windows.bat`. on windows there is no screen recording and no
system settings (both linux/hyprland), and ✕ exits scourge completely.

## runs on

| system | status |
| --- | --- |
| ubuntu 24.04+, linux mint 22+, debian 13 | tested (ubuntu 24.04) |
| fedora 39+ | tested (fedora 44) |
| arch, endeavouros, cachyos, manjaro | tested (arch) |
| openSUSE Tumbleweed | should work (untested) |
| windows 10/11 | via msys2 – app start tested, installer on real windows still untested |

required is **gtk 4.12 or newer** (ubuntu 22.04 / mint 21 / debian 12 are too old – the installer says that).

## update

```bash
cd ~/scourge-src
./update.sh

```

fetches the latest version and installs it (an old version running in the background is terminated).
windows: get new version and run `install-windows.bat` again.

start afterwards:

```bash
scourge            # or via the application menu ("scourge")
scourge file.jpg   # open a file directly

```

## quick start & pdf editing

* **background mode:** ✕ or ctrl+q only hides scourge; the next call of `scourge` is instantly there
(the launcher communicates directly with the running instance via d-bus). exit completely: `scourge --quit`.
for immediate first start add to autostart, e.g. hyprland: `exec-once = scourge --daemon`.
* **pdfs** open immediately; analysis runs in the background. in the toolbar: select text &
copy, highlight, underline, strikethrough, insert text (font, size, color), pen, frame,
note, eraser, undo (ctrl+z) and save (ctrl+s, ctrl+shift+s = new file). the annotations
are real pdf annotations and remain editable in other pdf programs.
* **apps bar:** mouse to the left window edge or click on "scourge" top left – starter for
notist, filist, musicalist and datist (if installed).

## what runs where

scourge needs **gtk 4** (linux with wayland recommended, x11 works for most things; windows via msys2).
on gnome, kde and windows the window has a normal frame, on hyprland/sway & co. it is borderless.
the app automatically adapts to an existing **matugen** theme
(`~/.cache/matugen/colors.css`); if none is there, it uses its own color scheme.

these functions depend on additional programs:

| function | needs | note |
| --- | --- | --- |
| file check, meme editor, gradient & ascii | – | runs everywhere |
| view pdf | poppler | installed via package |
| video cut / export, thumbnails | `ffmpeg` | installed via package |
| copy to clipboard | `wl-clipboard` (wayland) | under x11 gtk uses xclip-like itself |
| **screen recording** | `wf-recorder`, `slurp` | **wlroots/hyprland/sway only** – not available under gnome/kde |
| **system settings** (mouse/keyboard) | `hyprctl` | **hyprland only** – the rest (brightness/audio/radio) goes via `brightnessctl`/`wpctl`/`nmcli`, if available |

short: **file analysis, editor, cutter and studio run on every linux.**
screen recording and mouse/keyboard settings are hyprland-specific
and simply remain inactive on other environments.

## manual dependencies (if package manager is not recognized)

**python/gtk core:** gtk4, pygobject, pycairo, pillow, numpy, lxml
**gi typelibs:** gtk-4.0, gst-1.0 (gstreamer), poppler-0.18
**gstreamer plugins:** base, good, libav (for video playback)
**external tools:** ffmpeg, wl-clipboard, poppler-utils (pdftotext/pdfimages), qpdf

* **debian/ubuntu/mint:** `python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-gst-plugins-base-1.0 gir1.2-poppler-0.18 gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-libav python3-pil python3-numpy python3-lxml ffmpeg wl-clipboard poppler-utils qpdf`
* **fedora:** `python3-gobject python3-cairo gtk4 gstreamer1-plugins-base gstreamer1-plugins-good gstreamer1-plugin-libav poppler-glib python3-pillow python3-numpy python3-lxml wl-clipboard ffmpeg-free poppler-utils qpdf`
* **openSUSE:** `python3-gobject typelib-1_0-Gtk-4_0 typelib-1_0-Poppler-0_18 typelib-1_0-Gst-1_0 gstreamer-plugins-base gstreamer-plugins-good python3-Pillow python3-numpy python3-lxml ffmpeg wl-clipboard poppler-tools qpdf`
* **arch:** `gtk4 python-gobject python-cairo gst-plugins-base gst-plugins-good poppler-glib python-pillow python-numpy python-lxml ffmpeg wl-clipboard qpdf ttf-jetbrains-mono-nerd`

## uninstall

```bash
./uninstall.sh

```

removes the app, the launcher, the menu entry and the icons. system packages and the
font remain.

## problems?

* **boxes instead of symbols:** `./update.sh` – the icon font comes along and is loaded automatically.
* **`scourge: command not found`:** `~/.local/bin` is missing in `PATH` (the installer shows what belongs in
`~/.bashrc`). from the application menu it starts anyway.
* **videos don't play:** gstreamer plugins missing (`gstreamer1.0-libav` or `gst-libav`) – `./install.sh`
again and for system packages "y".
