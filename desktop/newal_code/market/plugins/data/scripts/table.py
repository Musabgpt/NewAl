"""/csv <file> [column] [value column]: a data file at a glance - its rows and columns, each column's type, empty
cells, distinct values, and for numbers the minimum, maximum, mean, median and total, for text the most common
values - then, with a column, a chart of it as an SVG file beside the data (a text column's most common values, a
number column's spread; with a value column, its total for each value of the first: "sales by region").

Reads CSV and TSV (the separator and the encoding found by themselves, Arabic Windows files included), Excel .xlsx
(its first sheet) and JSON (a list of records). Standard library only: NewAl Code's own Python runs it."""

import csv
import html
import io
import json
import math
import os
import re
import statistics
import sys
import zipfile
import xml.etree.ElementTree as ET

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass

AR = os.environ.get("NEWAL_LANG", "").lower().startswith("ar")
WORDS = {"rows": "صفوف", "columns": "أعمدة", "Column": "العمود", "Type": "النوع", "Filled": "معبأ",
         "Empty": "فارغ", "Distinct": "مختلف", "Summary": "الخلاصة", "number": "رقم", "text": "نص", "date": "تاريخ",
         "empty": "فارغ", "min": "أدنى", "max": "أعلى", "mean": "متوسط", "median": "وسيط", "total": "مجموع",
         "most common": "الأكثر تكراراً", "Chart": "الرسم", "no such column": "لا يوجد عمود بهذا الاسم",
         "columns are": "الأعمدة", "count": "العدد", "by": "حسب"}


def t(s):
    return WORDS.get(s, s) if AR else s


# ------------------------------------------------------------------ reading

def read_text(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp1256", "latin-1"):          # cp1256: Arabic Windows (Excel's "CSV" in Arabic)
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        if enc == "cp1256" and not re.search(r"[؀-ۿ]", text):
            continue                                        # cp1256 decodes anything: only when Arabic comes out
        return text, enc
    return raw.decode("utf-8", "replace"), "utf-8"


def read_csv(path):
    text, enc = read_text(path)
    sample = text[:20000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel_tab if path.lower().endswith(".tsv") else csv.excel
    rows = [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
    if not rows:
        return [], [], enc
    head = [h.strip() or "column %d" % (i + 1) for i, h in enumerate(rows[0])]
    return head, rows[1:], enc


def _col(ref):
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_xlsx(path):
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        shared = []
        if "xl/sharedStrings.xml" in names:
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
                shared.append("".join(x.text or "" for x in si.iter("{%s}t" % ns["m"])))
        sheet = "xl/worksheets/sheet1.xml"
        try:                                                  # the workbook's first sheet, wherever it is
            wb = ET.fromstring(z.read("xl/workbook.xml"))
            rid = wb.find("m:sheets/m:sheet", ns).get("{%s}id" % ns["r"])
            rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
            for rel in rels:
                if rel.get("Id") == rid:
                    target = rel.get("Target").lstrip("/")
                    sheet = target if target.startswith("xl/") else "xl/" + target
        except (KeyError, AttributeError, ET.ParseError):
            pass
        grid = []
        for row in ET.fromstring(z.read(sheet)).iter("{%s}row" % ns["m"]):
            cells = {}
            for c in row.findall("m:c", ns):
                kind, v = c.get("t"), c.find("m:v", ns)
                if kind == "s" and v is not None:
                    value = shared[int(v.text)]
                elif kind == "inlineStr":
                    value = "".join(x.text or "" for x in c.iter("{%s}t" % ns["m"]))
                elif kind == "b" and v is not None:
                    value = "TRUE" if v.text == "1" else "FALSE"
                else:
                    value = v.text if v is not None and v.text is not None else ""
                cells[_col(c.get("r"))] = value
            if cells:
                grid.append([cells.get(i, "") for i in range(max(cells) + 1)])
    if not grid:
        return [], [], "xlsx"
    head = [str(h).strip() or "column %d" % (i + 1) for i, h in enumerate(grid[0])]
    return head, grid[1:], "xlsx"


def read_json(path):
    data = json.loads(read_text(path)[0])
    if isinstance(data, dict):                                # {"data": [...]} and the like
        data = next((v for v in data.values() if isinstance(v, list)), [data])
    records = [r for r in data if isinstance(r, dict)]
    head = []
    for r in records:
        for k in r:
            if k not in head:
                head.append(k)
    return head, [[("" if r.get(k) is None else r.get(k) if not isinstance(r.get(k), (dict, list))
                    else json.dumps(r.get(k), ensure_ascii=False)) for k in head] for r in records], "json"


def load(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        return read_xlsx(path)
    if ext == ".json":
        return read_json(path)
    return read_csv(path)


# ------------------------------------------------------------------ columns

def number(v):
    """A cell as a number (1,234.5 and 1.234,5 and 12% and $5 and Arabic-Indic digits), or None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().translate(str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,"))
    s = re.sub(r"^[\$€£¥₪]|\s*(%|[\$€£¥₪]|ر\.س|د\.أ|ج\.م)$", "", s).replace(" ", "")
    if not re.fullmatch(r"[-+]?[\d.,]*\d[\d.,]*", s):
        return None
    if "," in s and "." in s:
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", "") if re.fullmatch(r"[-+]?\d{1,3}(,\d{3})+", s) else s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


DATE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}([ T]\d{1,2}:\d\d(:\d\d)?)?$|^\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}$")


def describe(values):
    filled = [v for v in values if str(v).strip() != ""]
    nums = [number(v) for v in filled]
    info = {"filled": len(filled), "empty": len(values) - len(filled), "distinct": len({str(v) for v in filled})}
    if filled and all(n is not None for n in nums):
        info.update(type="number", min=min(nums), max=max(nums), mean=statistics.fmean(nums),
                    median=statistics.median(nums), total=math.fsum(nums))
    elif filled and all(DATE.match(str(v).strip()) for v in filled):
        info.update(type="date", min=min(str(v) for v in filled), max=max(str(v) for v in filled))
    else:
        counts = {}
        for v in filled:
            counts[str(v).strip()] = counts.get(str(v).strip(), 0) + 1
        info.update(type="text" if filled else "empty",
                    top=sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5])
    return info


def fmt(x):
    if isinstance(x, float):
        if x.is_integer() and abs(x) < 1e15:
            return "{:,.0f}".format(x)
        return "{:,.2f}".format(x)
    return str(x)


# ------------------------------------------------------------------ chart

def svg_bars(title, pairs, path):
    """A horizontal bar chart (labels, values) as an SVG file; Arabic labels show right in any browser."""
    pairs = pairs[:15]
    top = max((v for _, v in pairs), default=1) or 1
    w, bar, gap, left = 760, 22, 8, 230
    h = 50 + len(pairs) * (bar + gap)
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" font-family="system-ui, Tahoma, '
           'sans-serif" font-size="13">' % (w, h), '<rect width="100%" height="100%" fill="#fff"/>',
           '<text x="16" y="26" font-size="15" font-weight="600">%s</text>' % html.escape(title)]
    for i, (label, v) in enumerate(pairs):
        y = 44 + i * (bar + gap)
        width = max(1, int((w - left - 90) * v / top))
        out.append('<text x="%d" y="%d" text-anchor="end" direction="auto">%s</text>' % (
            left - 8, y + 16, html.escape(str(label)[:32])))
        out.append('<rect x="%d" y="%d" width="%d" height="%d" rx="3" fill="#4f7cff"/>' % (left, y, width, bar))
        out.append('<text x="%d" y="%d" fill="#333">%s</text>' % (left + width + 6, y + 16, fmt(float(v))))
    out.append("</svg>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def chart(head, rows, col, value_col, src):
    i = head.index(col)
    base = os.path.splitext(src)[0]
    if value_col:
        j = head.index(value_col)
        sums = {}
        for r in rows:
            k = str(r[i]).strip() if i < len(r) else ""
            n = number(r[j]) if j < len(r) else None
            if k and n is not None:
                sums[k] = sums.get(k, 0.0) + n
        pairs = sorted(sums.items(), key=lambda kv: -kv[1])
        title = "%s %s %s" % (value_col, t("by"), col)
        path = "%s-%s-by-%s.svg" % (base, _safe(value_col), _safe(col))
    else:
        values = [r[i] for r in rows if i < len(r) and str(r[i]).strip() != ""]
        nums = [number(v) for v in values]
        if values and all(n is not None for n in nums):
            lo, hi = min(nums), max(nums)
            bins = min(10, len(set(nums))) if hi > lo else 1
            step = (hi - lo) / bins if bins > 1 else 1
            counts = [0] * bins
            for n in nums:
                counts[min(bins - 1, int((n - lo) / step)) if bins > 1 else 0] += 1
            pairs = [("%s – %s" % (fmt(lo + k * step), fmt(lo + (k + 1) * step)) if bins > 1 else fmt(lo), c)
                     for k, c in enumerate(counts)]
            title = "%s: %s" % (col, t("count"))
        else:
            counts = {}
            for v in values:
                counts[str(v).strip()] = counts.get(str(v).strip(), 0) + 1
            pairs = sorted(counts.items(), key=lambda kv: -kv[1])
            title = "%s: %s" % (col, t("most common"))
        path = "%s-%s.svg" % (base, _safe(col))
    svg_bars(title, pairs, path)
    return path, pairs


def _safe(name):
    return re.sub(r"[^\w؀-ۿ-]+", "_", str(name)).strip("_")[:40] or "column"


def pick(head, name):
    """A column by its name (any case) or its number (1 = the first)."""
    if name.isdigit() and 1 <= int(name) <= len(head):
        return head[int(name) - 1]
    for h in head:
        if h.lower() == name.lower():
            return h
    return None


def main(argv):
    if not argv:
        print("/csv <file.csv|.tsv|.xlsx|.json> [column] [value column]")
        return 2
    path = os.path.abspath(os.path.expanduser(argv[0]))
    if not os.path.isfile(path):
        print("%s: no such file" % argv[0])
        return 2
    try:
        head, rows, enc = load(path)
    except (OSError, ValueError, zipfile.BadZipFile, ET.ParseError, KeyError) as e:
        print("%s: cannot read it (%s)" % (argv[0], e))
        return 1
    print("%s: %d %s, %d %s%s" % (os.path.basename(path), len(rows), t("rows"), len(head), t("columns"),
                                  "" if enc in ("utf-8-sig", "xlsx", "json") else " (%s)" % enc))
    table = [(t("Column"), t("Type"), t("Filled"), t("Empty"), t("Distinct"), t("Summary"))]
    for i, h in enumerate(head):
        d = describe([r[i] if i < len(r) else "" for r in rows])
        if d["type"] == "number":
            summary = "%s %s · %s %s · %s %s · %s %s · %s %s" % (
                t("min"), fmt(d["min"]), t("max"), fmt(d["max"]), t("mean"), fmt(d["mean"]), t("median"),
                fmt(d["median"]), t("total"), fmt(d["total"]))
        elif d["type"] == "date":
            summary = "%s → %s" % (d["min"], d["max"])
        else:
            summary = ", ".join("%s (%d)" % (k[:24], n) for k, n in d.get("top", []))
        table.append((str(h)[:28], t(d["type"]), str(d["filled"]), str(d["empty"]), str(d["distinct"]), summary))
    widths = [max(len(r[k]) for r in table) for k in range(5)]
    for r in table:
        print("  ".join(r[k].ljust(widths[k]) for k in range(5)) + "  " + r[5])
    if len(argv) > 1:
        col = pick(head, argv[1])
        value_col = pick(head, argv[2]) if len(argv) > 2 else None
        if not col or (len(argv) > 2 and not value_col):
            print("\n%s: %s (%s: %s)" % (t("no such column"), argv[1] if not col else argv[2], t("columns are"),
                                          ", ".join(head)))
            return 2
        out, pairs = chart(head, rows, col, value_col, path)
        print("\n%s: %s" % (t("Chart"), out))
        for label, v in pairs[:10]:
            print("  %s  %s" % (str(label)[:40].ljust(40), fmt(float(v))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
