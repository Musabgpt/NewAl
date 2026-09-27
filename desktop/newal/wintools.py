"""Everyday Windows actions: open apps, clipboard, screenshot, notifications, downloads, archives,
scheduled tasks and a system summary."""

import os
import re
import shutil
import time
import urllib.parse
import urllib.request
import zipfile

from . import config, connectors


def _ps(script, timeout=60):
    return connectors.shell(script, "powershell", timeout=timeout)


def _q(text):
    """A PowerShell single-quoted string."""
    return "'" + str(text).replace("'", "''") + "'"


def open_target(target):
    """An app (notepad, calc, excel, chrome...), a website or a file/folder."""
    if not config.IS_WINDOWS:
        return connectors.shell("xdg-open %s" % _q(target))
    return _ps("Start-Process %s; 'opened'" % _q(target))


def clipboard_get():
    return _ps("Get-Clipboard -Raw") if config.IS_WINDOWS else "الحافظة متاحة على ويندوز فقط"


def clipboard_set(text):
    return _ps("Set-Clipboard -Value %s; 'copied'" % _q(text)) if config.IS_WINDOWS else "الحافظة متاحة على ويندوز فقط"


def screenshot():
    """Saves the whole screen as a PNG in the workspace and returns its path."""
    folder = os.path.join(config.WORKSPACE, "screenshots")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, time.strftime("screen-%Y%m%d-%H%M%S.png"))
    if not config.IS_WINDOWS:
        return "لقطة الشاشة متاحة على ويندوز فقط"
    out = _ps("Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
              "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen; "
              "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height; "
              "$g=[System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($b.Left,$b.Top,0,0,$bmp.Size); "
              "$bmp.Save(%s); 'saved'" % _q(path))
    return ("تم حفظ لقطة الشاشة: " + path) if os.path.exists(path) else out


def notify(title, message):
    if not config.IS_WINDOWS:
        return "الإشعارات متاحة على ويندوز فقط"
    return _ps("Add-Type -AssemblyName System.Windows.Forms; $n=New-Object System.Windows.Forms.NotifyIcon; "
               "$n.Icon=[System.Drawing.SystemIcons]::Information; $n.Visible=$true; "
               "$n.ShowBalloonTip(8000, %s, %s, 'Info'); Start-Sleep 9; $n.Dispose(); 'shown'" % (_q(title), _q(message)),
               timeout=30)


def download_file(url, filename=""):
    """Downloads a URL into Downloads\\NewAl (or the workspace)."""
    if not re.match(r"https?://", url):
        return "رابط غير صالح"
    folder = os.path.join(os.path.expanduser("~"), "Downloads", "NewAl")
    if not os.path.isdir(os.path.dirname(folder)):
        folder = os.path.join(config.WORKSPACE, "downloads")
    os.makedirs(folder, exist_ok=True)
    name = filename or os.path.basename(urllib.parse.urlparse(url).path) or "download"
    path = os.path.join(folder, re.sub(r'[\\/:*?"<>|]', "_", name))
    req = urllib.request.Request(url, headers={"User-Agent": "NewAl"})
    with urllib.request.urlopen(req, timeout=60) as r, open(path, "wb") as f:
        shutil.copyfileobj(r, f)
    return "تم التنزيل: %s (%.1f MB)" % (path, os.path.getsize(path) / 1e6)


def zip_path(source, archive=""):
    src = _abs(source)
    dest = _abs(archive) if archive else src.rstrip("\\/") + ".zip"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        if os.path.isdir(src):
            for root, _, files in os.walk(src):
                for n in files:
                    p = os.path.join(root, n)
                    z.write(p, os.path.relpath(p, os.path.dirname(src)))
        else:
            z.write(src, os.path.basename(src))
    return "تم الضغط: " + dest


def unzip_path(archive, folder=""):
    src = _abs(archive)
    dest = _abs(folder) if folder else os.path.splitext(src)[0]
    with zipfile.ZipFile(src) as z:
        for m in z.namelist():                      # no paths escaping the target folder
            if os.path.isabs(m) or ".." in m.replace("\\", "/").split("/"):
                return "أرشيف غير آمن: " + m
        z.extractall(dest)
    return "تم فك الضغط في: " + dest


def schedule_task(name, command, time_hhmm, daily=True):
    """A Windows scheduled task that runs a command (PowerShell) daily or once at HH:MM."""
    if not config.IS_WINDOWS:
        return "جدولة المهام متاحة على ويندوز فقط"
    if not re.fullmatch(r"\d{1,2}:\d{2}", time_hhmm):
        return "الوقت بصيغة HH:MM"
    tn = "NewAl\\" + re.sub(r"[^\w -]", "_", name)[:60]
    tr = 'powershell -NoProfile -WindowStyle Hidden -Command "%s"' % command.replace('"', '\\"')
    code, out = connectors.run(["schtasks", "/Create", "/F", "/SC", "DAILY" if daily else "ONCE", "/TN", tn,
                                "/TR", tr, "/ST", time_hhmm.zfill(5)], timeout=60)
    return ("تمت جدولة المهمة %s الساعة %s" % (tn, time_hhmm)) if code == 0 else out


def system_info():
    if not config.IS_WINDOWS:
        return connectors.shell("uname -a; free -h; df -h / ; hostname -I")
    return _ps("$o=Get-CimInstance Win32_OperatingSystem; $c=Get-CimInstance Win32_Processor | Select -First 1; "
               "'Computer: ' + $env:COMPUTERNAME; 'Windows: ' + $o.Caption + ' ' + $o.Version; 'CPU: ' + $c.Name; "
               "'RAM: {0:N1} GB total, {1:N1} GB free' -f ($o.TotalVisibleMemorySize/1MB), ($o.FreePhysicalMemory/1MB); "
               "Get-PSDrive -PSProvider FileSystem | ForEach-Object { 'Disk {0}: {1:N1} GB free of {2:N1} GB' -f "
               "$_.Name, ($_.Free/1GB), (($_.Used+$_.Free)/1GB) }; "
               "'IP: ' + ((Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -notlike '127.*'}).IPAddress -join ', ')")


def _abs(p):
    p = os.path.expandvars(os.path.expanduser(p or ""))
    return p if os.path.isabs(p) else os.path.join(config.WORKSPACE, p)
