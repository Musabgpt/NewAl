"""Shared by the system plugin's programs: the platform, running a command quietly, sizes, and the Arabic labels
(NEWAL_LANG=ar, which NewAl Code sets from the app's language). Standard library only."""

import os
import subprocess
import sys

WINDOWS = os.name == "nt"
MAC = sys.platform == "darwin"
ANDROID = os.path.exists("/system/build.prop") or "ANDROID_ROOT" in os.environ
TERMUX = "com.termux" in os.environ.get("PREFIX", "")
AR = os.environ.get("NEWAL_LANG", "").lower().startswith("ar")

for _s in (sys.stdout, sys.stderr):                     # UTF-8 on a Windows pipe too
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass

AR_WORDS = {
    "System": "النظام", "Device": "الجهاز", "Processor": "المعالج", "Memory": "الذاكرة", "Storage": "التخزين",
    "Graphics": "الرسوميات", "Battery": "البطارية", "Network": "الشبكة", "Uptime": "يعمل منذ", "Tools": "الأدوات",
    "free": "متاح", "of": "من", "threads": "خيوط", "charging": "يشحن", "on battery": "على البطارية",
    "plugged in": "على الشاحن", "full": "ممتلئة", "offline": "غير متصل", "not found": "غير موجود",
    "Local models": "النماذج المحلية", "App": "التطبيق", "Phone storage": "ذاكرة الجوال",
    "GGUF (Q4) models that fit in the free memory now": "نماذج GGUF (Q4) تتسع لها الذاكرة المتاحة الآن",
    "Folder": "المجلد", "Biggest in the folder": "الأكبر في المجلد", "partial": "نتيجة جزئية", "Biggest files": "أكبر الملفات", "Total": "المجموع",
    "files": "ملفات", "Drives": "الأقراص", "stopped after": "توقف بعد", "seconds": "ثانية",
    "Found": "وُجد", "Deleted": "حُذف", "nothing to clean": "لا شيء للتنظيف",
    "To delete these, run": "لحذفها شغّل", "Could not delete": "تعذّر حذف",
    "Listening": "يستمع", "Port": "المنفذ", "Program": "البرنامج", "Address": "العنوان", "none": "لا شيء",
    "free, nothing listens on it": "متاح، لا يستخدمه أي برنامج", "all networks": "كل الشبكات",
    "this device only": "هذا الجهاز فقط",
    "This needs administrator rights. In a terminal, run:": "هذا يحتاج صلاحيات المدير. شغّل في الطرفية:",
    "No package manager found.": "لم يُعثر على مدير حزم.", "Stopped after 30 minutes.": "توقف بعد 30 دقيقة.",
    "The app has no package manager: NewAl Code in Termux installs programs with pkg (Settings > Termux).":
        "التطبيق لا يملك مدير حزم: NewAl Code داخل Termux يثبّت البرامج بـ pkg (الإعدادات > Termux).",
    "Android does not let an app see the ports other programs use.": "أندرويد لا يسمح لتطبيق برؤية منافذ البرامج الأخرى.",
}


def t(text):
    return AR_WORDS.get(text, text) if AR else text


def run(cmd, timeout=12, cwd=None):
    """A command's output ("" when it is missing or fails), without a console window on Windows."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           stdin=subprocess.DEVNULL, cwd=cwd, creationflags=0x08000000 if WINDOWS else 0)
        return (r.stdout or "").strip()
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""


def used(st):
    """The space a file takes on the disk (as du counts it: a sparse file only its written blocks)."""
    blocks = getattr(st, "st_blocks", None)
    return blocks * 512 if blocks is not None else st.st_size


def size(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024
    return "%.1f TB" % n


def table(rows, gap=2):
    """Rows of cells as aligned text."""
    rows = [[str(c) for c in r] for r in rows]
    if not rows:
        return ""
    widths = [max(len(r[i]) for r in rows if i < len(r)) for i in range(max(len(r) for r in rows))]
    return "\n".join((" " * gap).join(c.ljust(widths[i]) if i < len(r) - 1 else c for i, c in enumerate(r))
                     for r in rows)
