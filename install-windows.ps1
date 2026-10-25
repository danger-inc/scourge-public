# install-windows.ps1 – installiert scourge, notist, datist oder musicalist unter Windows 10/11.
# Starten: Doppelklick auf install-windows.bat   (Entfernen: uninstall-windows.bat)
# Ablauf: MSYS2 (liefert GTK 4 + Python für Windows) einrichten → Pakete → App nach %USERPROFILE%\.local\share
# kopieren → Startmenü-Verknüpfung. Admin-Rechte braucht nur die einmalige MSYS2-Installation.
# Kanonische Quelle: rice-apps-packaging/windows/ – Kopien liegen in den Repos.
param([switch]$Uninstall, [switch]$Update)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'          # Fortschrittsbalken bremst Downloads in PowerShell 5 stark
$Src = Split-Path -Parent $MyInvocation.MyCommand.Path
# Fehler sichtbar machen: das Konsolenfenster des Update-Knopfs schloss sich sonst sofort wieder.
trap {
    Write-Host ''
    Write-Host "FEHLER: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray
    if ($Update) { Read-Host 'Enter schliesst das Fenster' | Out-Null }
    exit 1
}

function Say($t)  { Write-Host $t -ForegroundColor Cyan }
function Warn($t) { Write-Host $t -ForegroundColor Yellow }
function Ask($q)  { if ($Update) { return $false }; $a = Read-Host "$q [J/n]"; return -not ($a -match '^[nN]') }

# ------------------------------------------------------------------ welche App?
$Share = Join-Path $HOME '.local\share'
$Base = @('python', 'python-gobject', 'python-cairo', 'gtk4', 'adwaita-icon-theme', 'librsvg')
if (Test-Path "$Src\app\scourge.py") {
    $App = @{ Name = 'Scourge'; Dir = "$Share\scourge"; Main = 'scourge.py'; Icon = 'logo\scourge.ico'; Fonts = "$Src\fonts"
              Pkgs = $Base + @('poppler', 'gstreamer', 'gst-plugins-base', 'gst-plugins-good', 'gst-libav',
                               'python-pillow', 'python-numpy', 'python-lxml', 'ffmpeg') }
    $Copy = { Copy-Item "$Src\app\*" $App.Dir -Recurse -Force }
} elseif (Test-Path "$Src\app\notes.py") {
    $App = @{ Name = 'notist'; Dir = "$Share\notes\app"; Main = 'notes.py'; Icon = 'logo\notist.ico'; Fonts = "$Src\fonts"
              Pkgs = $Base + @('poppler', 'enchant') }
    $Copy = { Copy-Item "$Src\app\*" $App.Dir -Recurse -Force }          # Daten liegen daneben in notes\data
} elseif (Test-Path "$Src\app\calendar\ui.py") {
    $App = @{ Name = 'datist'; Dir = "$Share\rice-apps\calendar"; Main = 'ui.py'; Icon = 'datist.ico'; Fonts = $null
              Pkgs = $Base }
    $Copy = {
        Copy-Item "$Src\app\calendar\*" $App.Dir -Recurse -Force
        Copy-Item "$Src\app\tui.py", "$Src\app\glass.py", "$Src\app\changelog.py", "$Src\app\swipe.py" "$Share\rice-apps" -Force
        New-Item -ItemType Directory -Force "$Share\rice-apps\fonts" | Out-Null
        Copy-Item "$Src\app\fonts\*" "$Share\rice-apps\fonts" -Force
        Copy-Item "$Src\datist.ico" $App.Dir -Force
    }
} elseif (Test-Path "$Src\app\music\musicd.py") {
    # musicalist: Kernfunktionen (Player, Suche, Bibliothek, Lyrics, Verlauf); ohne Hover-Vorschau, Spektrum, Medientasten.
    # Python-Pakete (ytmusicapi, yt-dlp …) kommen per pip in <App>\pylibs; Anmeldung/Zustand bleiben bei Updates erhalten.
    $App = @{ Name = 'musicalist'; Dir = "$Share\rice-apps\music"; Main = 'musicd.py'; Args = '--show'; Icon = 'musicalist.ico'
              Fonts = $null
              Keep = @('auth', 'state.json', 'settings.json', 'history.json', 'plays.json')
              Pip = @('ytmusicapi>=1.10', 'yt-dlp', 'yt-dlp-ejs', 'requests')
              Pkgs = $Base + @('python-pip', 'mpv', 'nodejs', 'python-certifi', 'python-pillow', 'python-numpy') }
    $Copy = {
        Copy-Item "$Src\app\music\*" $App.Dir -Recurse -Force
        Copy-Item "$Src\app\*.py" "$Share\rice-apps" -Force
        New-Item -ItemType Directory -Force "$Share\rice-apps\fonts" | Out-Null
        Copy-Item "$Src\app\fonts\*" "$Share\rice-apps\fonts" -Force
        Copy-Item "$Src\musicalist.ico" $App.Dir -Force
    }
} else {
    throw 'Dieses Skript gehört in den Ordner von scourge, notist, datist oder musicalist.'
}
$Programs = [Environment]::GetFolderPath('Programs')
$Desktop = [Environment]::GetFolderPath('Desktop')
$Lnk = "$($App.Name).lnk"

# ------------------------------------------------------------------ entfernen
if ($Uninstall) {
    Say "== $($App.Name) entfernen =="
    if ($App.Keep) {      # Anmeldung/Zustand bleiben liegen
        Get-ChildItem $App.Dir -Force -ErrorAction SilentlyContinue | Where-Object { $App.Keep -notcontains $_.Name } |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    } else {
        Remove-Item -Recurse -Force $App.Dir -ErrorAction SilentlyContinue
    }
    Remove-Item -Force "$Programs\$Lnk", "$Desktop\$Lnk" -ErrorAction SilentlyContinue
    Say "$($App.Name) entfernt. Deine Daten/Einstellungen und MSYS2 (C:\msys64) bleiben."
    return
}

Say "== $($App.Name) für Windows installieren =="

# ------------------------------------------------------------------ MSYS2 (GTK 4 + Python)
function Find-Msys {
    foreach ($p in @("$env:SystemDrive\msys64", 'C:\msys64', "$env:LOCALAPPDATA\msys64")) {
        if (Test-Path "$p\usr\bin\bash.exe") { return $p }
    }
    return $null
}
$Msys = Find-Msys
if (-not $Msys) {
    Say 'MSYS2 wird installiert (liefert GTK 4 und Python für Windows, einmalig ~1 GB) ...'
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id MSYS2.MSYS2 -e --silent --accept-source-agreements --accept-package-agreements | Out-Host
        $Msys = Find-Msys
    }
    if (-not $Msys) {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $exe = Join-Path $env:TEMP 'msys2-x86_64-latest.exe'
        Invoke-WebRequest 'https://github.com/msys2/msys2-installer/releases/latest/download/msys2-x86_64-latest.exe' `
            -OutFile $exe -UseBasicParsing
        & $exe in --confirm-command --accept-messages --root C:/msys64 | Out-Host
        $Msys = Find-Msys
    }
    if (-not $Msys) {
        throw 'MSYS2 ließ sich nicht installieren. Bitte von https://www.msys2.org installieren und das Skript erneut starten.'
    }
}
Say "MSYS2: $Msys"
$env:MSYSTEM = 'UCRT64'
$env:CHERE_INVOKING = '1'
function Msys($cmd) {
    & "$Msys\usr\bin\bash.exe" -lc $cmd | Out-Host
    return $LASTEXITCODE
}

if (-not $Update -or -not (Test-Path "$Msys\ucrt64\bin\pythonw.exe")) {
    Say 'Pakete aktualisieren und installieren (dauert beim ersten Mal einige Minuten) ...'
    # Das erste Update erneuert oft MSYS2 selbst und beendet sich dann – deshalb zweimal.
    Msys 'pacman -Syuu --noconfirm' | Out-Null
    Msys 'pacman -Syuu --noconfirm' | Out-Null
    $pk = ($App.Pkgs | ForEach-Object { "mingw-w64-ucrt-x86_64-$_" }) -join ' '
    if ((Msys "pacman -S --needed --noconfirm $pk") -ne 0) {
        Warn 'Nicht alle Pakete ließen sich installieren – siehe Meldungen oben. Weiter.'
    }
}
$Py = "$Msys\ucrt64\bin\python.exe"
$PyW = "$Msys\ucrt64\bin\pythonw.exe"
if (-not (Test-Path $PyW)) { throw "Python fehlt in MSYS2 ($PyW) – Paketinstallation fehlgeschlagen?" }

# ------------------------------------------------------------------ App kopieren
Say "Kopiere $($App.Name) nach $($App.Dir) ..."
# Anmeldung, Zustand und Einstellungen (App.Keep) überleben die Neuinstallation
$KeepDir = Join-Path $env:TEMP ('keep-' + $App.Name)
if ($App.Keep) {
    Remove-Item -Recurse -Force $KeepDir -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $KeepDir | Out-Null
    foreach ($k in $App.Keep) { if (Test-Path "$($App.Dir)\$k") { Copy-Item "$($App.Dir)\$k" $KeepDir -Recurse -Force } }
}
# Nicht den Ordner selbst löschen: Ist die App gerade offen (Update-Knopf), hält Windows ihn als Arbeitsordner
# fest und Remove-Item scheiterte. Inhalt leeren, was nicht gesperrt ist, der Rest wird überschrieben.
if (Test-Path $App.Dir) {
    Get-ChildItem $App.Dir -Force | ForEach-Object { Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
}
New-Item -ItemType Directory -Force $App.Dir | Out-Null
& $Copy
if ($App.Keep) {
    foreach ($k in $App.Keep) { if (Test-Path "$KeepDir\$k") { Copy-Item "$KeepDir\$k" $App.Dir -Recurse -Force } }
    Remove-Item -Recurse -Force $KeepDir -ErrorAction SilentlyContinue
}
if ($App.Pip) {
    Say 'Python-Pakete (pip) ...'
    & $Py -m pip install --upgrade --target (Join-Path $App.Dir 'pylibs') $App.Pip | Out-Host
    if ($LASTEXITCODE -ne 0) { Warn 'pip meldete Fehler – ytmusicapi/yt-dlp fehlen evtl.; Skript erneut starten.' }
}

# ------------------------------------------------------------------ Schrift (Oberfläche) – nur für diesen Benutzer
if ($App.Fonts -and (Test-Path $App.Fonts)) {
    $fdir = "$env:LOCALAPPDATA\Microsoft\Windows\Fonts"
    $reg = 'HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Fonts'
    New-Item -ItemType Directory -Force $fdir | Out-Null
    if (-not (Test-Path $reg)) { New-Item -Force $reg | Out-Null }
    foreach ($f in Get-ChildItem "$($App.Fonts)\*.ttf") {
        $dst = Join-Path $fdir $f.Name
        if (-not (Test-Path $dst)) {
            Copy-Item $f.FullName $dst -Force
            New-ItemProperty -Path $reg -Name "$($f.BaseName) (TrueType)" -Value $dst -PropertyType String -Force | Out-Null
        }
    }
}

# ------------------------------------------------------------------ Verknüpfungen
# Windows merkt sich Symbole je Dateipfad (Icon-Cache). Bliebe der Pfad gleich, zeigten Startmenü und Taskleiste
# nach einem Update mit neuem Logo weiter das alte. Deshalb trägt die Kopie für die Verknüpfung den Inhalts-Hash
# im Namen: neues Logo = neuer Pfad = neues Symbol.
$IconPath = Join-Path $App.Dir $App.Icon
if (Test-Path $IconPath) {
    $hash = (Get-FileHash $IconPath -Algorithm SHA256).Hash.Substring(0, 8).ToLower()
    $named = Join-Path (Split-Path $IconPath) ('{0}-{1}.ico' -f [IO.Path]::GetFileNameWithoutExtension($IconPath), $hash)
    Copy-Item $IconPath $named -Force
    $IconPath = $named
}
function New-Link($path) {
    $ws = New-Object -ComObject WScript.Shell
    $l = $ws.CreateShortcut($path)
    $l.TargetPath = $PyW
    $l.Arguments = '"' + (Join-Path $App.Dir $App.Main) + '"' + $(if ($App.Args) { ' ' + $App.Args } else { '' })
    $l.WorkingDirectory = $App.Dir
    $l.IconLocation = $IconPath + ',0'
    $l.Description = $App.Name
    $l.Save()
}
New-Link "$Programs\$Lnk"
# scourge-pdf (schlanker PDF-Editor) bekommt einen eigenen Startmenü-Eintrag
if (Test-Path (Join-Path $App.Dir 'scourge_pdf.py')) {
    $ws2 = New-Object -ComObject WScript.Shell
    $l2 = $ws2.CreateShortcut("$Programs\scourge-pdf.lnk")
    $l2.TargetPath = $PyW
    $l2.Arguments = '"' + (Join-Path $App.Dir 'scourge_pdf.py') + '"'
    $l2.WorkingDirectory = $App.Dir
    $l2.IconLocation = $IconPath + ',0'
    $l2.Description = 'scourge-pdf'
    $l2.Save()
}
if ((Test-Path "$Desktop\$Lnk") -or (Ask 'Auch eine Verknüpfung auf dem Desktop anlegen?')) { New-Link "$Desktop\$Lnk" }
# Explorer die Symbole neu einlesen lassen (sonst erst nach Neuanmeldung)
try { & "$env:SystemRoot\System32\ie4uinit.exe" -show 2>$null | Out-Null } catch { }

# ------------------------------------------------------------------ Selbsttest
Say 'Prüfe ...'
& $Py -c "import gi; gi.require_version('Gtk', '4.0'); from gi.repository import Gtk; print('  GTK %d.%d: ok' % (Gtk.get_major_version(), Gtk.get_minor_version()))" | Out-Host
if ($LASTEXITCODE -ne 0) { Warn '  GTK/PyGObject fehlt – Skript bitte erneut starten.' }

Say "Fertig! $($App.Name) steht jetzt im Startmenü."
Write-Host 'Aktualisieren: neue Version holen (git pull bzw. ZIP neu laden) und install-windows.bat erneut starten.'
Write-Host 'Entfernen:     uninstall-windows.bat'
