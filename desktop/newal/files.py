"""Text out of the files people attach or index: code, text, PDF, Word, Excel, PowerPoint."""

import os
import re
import zipfile

TEXT_EXT = {".txt", ".md", ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".kt", ".c", ".h", ".cpp", ".hpp", ".cs",
            ".go", ".rs", ".rb", ".php", ".html", ".htm", ".css", ".scss", ".json", ".yaml", ".yml", ".toml", ".ini",
            ".cfg", ".xml", ".csv", ".tsv", ".sql", ".sh", ".ps1", ".bat", ".cmd", ".gradle", ".kts", ".swift",
            ".dart", ".vue", ".svelte", ".r", ".m", ".lua", ".pl", ".log", ".env.example", ".dockerfile", ".ipynb",
            ".tex", ".rst", ".srt"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "env", "build", "dist", "bin", "obj", ".gradle",
             ".idea", ".vs", ".vscode", "target", ".next", ".cache", "site-packages"}


def is_text_name(name):
    base = os.path.basename(name).lower()
    return os.path.splitext(base)[1] in TEXT_EXT or base in {"dockerfile", "makefile", "readme", "license"}


def extract(path, limit=400_000):
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".pdf":
            return _pdf(path)[:limit]
        if ext == ".docx":
            return _xml_text(path, r"word/document\.xml", "w:p")[:limit]
        if ext == ".pptx":
            return _xml_text(path, r"ppt/slides/slide\d+\.xml", "a:p")[:limit]
        if ext == ".xlsx":
            return _xlsx(path)[:limit]
        with open(path, "rb") as f:
            raw = f.read(limit * 2)
        if b"\x00" in raw[:4000] and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            return ""
        for enc in ("utf-8-sig", "utf-16", "cp1256", "latin-1"):
            try:
                return raw.decode(enc)[:limit]
            except UnicodeDecodeError:
                continue
    except Exception as e:  # noqa: BLE001
        return "[تعذرت قراءة الملف: %s]" % e
    return ""


def _pdf(path):
    try:
        from pypdf import PdfReader
    except ImportError:
        return "[قراءة PDF تحتاج مكتبة pypdf]"
    return "\n\n".join((p.extract_text() or "") for p in PdfReader(path).pages)


def _xml_text(path, member_re, para_tag):
    out = []
    with zipfile.ZipFile(path) as z:
        names = sorted((n for n in z.namelist() if re.fullmatch(member_re, n)),
                       key=lambda n: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", n)])
        for n in names:
            xml = z.read(n).decode("utf-8", "replace")
            for p in re.findall(r"<%s[ >].*?</%s>" % (para_tag, para_tag), xml, re.S):
                t = "".join(re.findall(r"<[aw]:t[^>]*>([^<]*)</[aw]:t>", p))
                if t.strip():
                    out.append(t)
    return "\n".join(out)


def _xlsx(path):
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            xml = z.read("xl/sharedStrings.xml").decode("utf-8", "replace")
            shared = ["".join(re.findall(r"<t[^>]*>([^<]*)</t>", si)) for si in re.findall(r"<si>(.*?)</si>", xml, re.S)]
        rows = []
        for n in sorted(x for x in z.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", x)):
            xml = z.read(n).decode("utf-8", "replace")
            for row in re.findall(r"<row[^>]*>(.*?)</row>", xml, re.S):
                cells = []
                for attrs, val in re.findall(r"<c([^>]*)>(?:.*?<v>([^<]*)</v>)?.*?</c>", row, re.S):
                    if 't="s"' in attrs and val.isdigit() and int(val) < len(shared):
                        val = shared[int(val)]
                    cells.append(val)
                rows.append("\t".join(cells))
        return "\n".join(rows)


def walk(root, max_size=1_000_000):
    """Indexable files under root."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            p = os.path.join(dirpath, name)
            ext = os.path.splitext(name)[1].lower()
            if (is_text_name(name) or ext in (".pdf", ".docx", ".pptx", ".xlsx")) and os.path.getsize(p) <= max_size:
                yield p


def chunks(text, size=1200, overlap=150):
    """Pieces of about `size` characters cut at line ends."""
    lines = text.splitlines(keepends=True)
    out, cur = [], ""
    for line in lines:
        while len(line) > size:
            out.append(line[:size])
            line = line[size - overlap:]
        if len(cur) + len(line) > size and cur:
            out.append(cur)
            cur = cur[-overlap:] if overlap else ""
        cur += line
    if cur.strip():
        out.append(cur)
    return out
