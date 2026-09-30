"""/rtl [file or folder] [--apply]: makes web pages right-to-left for Arabic.

- <html> gets dir="rtl" (and lang="ar" where no language is set);
- CSS's left and right become their logical forms, which follow the page's direction: margin-left becomes
  margin-inline-start, padding-right padding-inline-end, border-left border-inline-start, left: inset-inline-start,
  the corner radii border-start-start-radius..., text-align: left start, float and clear: left inline-start;
- a margin or padding with four values whose left and right differ becomes margin-block + margin-inline;
- in JSX/TSX style objects, marginLeft becomes marginInlineStart, textAlign: "left" "start", and so on.

In .css/.scss/.sass/.less files, in <style> blocks and style="" of .html/.htm/.vue/.svelte/.php, and in .jsx/.tsx. It
lists what it would change; --apply writes it. Standard library only: NewAl Code's own Python runs it."""

import os
import re
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass

AR = os.environ.get("NEWAL_LANG", "").lower().startswith("ar")
SKIP_DIRS = {".git", "node_modules", "dist", "build", "vendor", ".venv", "venv", "__pycache__", ".next", "out",
             "coverage", ".cache"}
CSS_FILES = (".css", ".scss", ".sass", ".less")
PAGE_FILES = (".html", ".htm", ".vue", ".svelte", ".php")
JSX_FILES = (".jsx", ".tsx")

SIDES = {"left": "start", "right": "end"}
PROPS = {}
for box in ("margin", "padding", "scroll-margin", "scroll-padding"):
    for side, logical in SIDES.items():
        PROPS["%s-%s" % (box, side)] = "%s-inline-%s" % (box, logical)
for side, logical in SIDES.items():
    PROPS["border-%s" % side] = "border-inline-%s" % logical
    for part in ("width", "style", "color"):
        PROPS["border-%s-%s" % (side, part)] = "border-inline-%s-%s" % (logical, part)
    PROPS[side] = "inset-inline-%s" % logical
for v, block in (("top", "start"), ("bottom", "end")):
    for h, inline in SIDES.items():
        PROPS["border-%s-%s-radius" % (v, h)] = "border-%s-%s-radius" % (block, inline)
VALUES = {"text-align": {"left": "start", "right": "end"}, "text-align-last": {"left": "start", "right": "end"},
          "float": {"left": "inline-start", "right": "inline-end"}, "clear": {"left": "inline-start",
                                                                              "right": "inline-end"}}

PROP_RE = re.compile(r"(^|[{;\s\"'])(%s)(\s*:)" % "|".join(sorted(map(re.escape, PROPS), key=len, reverse=True)),
                     re.M | re.I)
VALUE_RE = re.compile(r"(^|[{;\s\"'])(%s)(\s*:\s*)(left|right)(?=\s*(!important)?\s*[;}\"'\n]|$)" %
                      "|".join(map(re.escape, VALUES)), re.M | re.I)
BOX_RE = re.compile(r"(^|[{;\s\"'])(margin|padding)(\s*:\s*)([^;{}\"'\n]+?)(\s*!important)?(?=\s*[;}\"'\n]|$)",
                    re.M | re.I)

CAMEL = {"marginLeft": "marginInlineStart", "marginRight": "marginInlineEnd", "paddingLeft": "paddingInlineStart",
         "paddingRight": "paddingInlineEnd", "borderLeft": "borderInlineStart", "borderRight": "borderInlineEnd",
         "borderLeftWidth": "borderInlineStartWidth", "borderRightWidth": "borderInlineEndWidth",
         "borderLeftColor": "borderInlineStartColor", "borderRightColor": "borderInlineEndColor",
         "borderLeftStyle": "borderInlineStartStyle", "borderRightStyle": "borderInlineEndStyle",
         "borderTopLeftRadius": "borderStartStartRadius", "borderTopRightRadius": "borderStartEndRadius",
         "borderBottomLeftRadius": "borderEndStartRadius", "borderBottomRightRadius": "borderEndEndRadius"}
CAMEL_RE = re.compile(r"(?<![\w$.])(%s)(\s*:)" % "|".join(sorted(CAMEL, key=len, reverse=True)))
CAMEL_VALUE_RE = re.compile(r"(?<![\w$.])(textAlign|float)(\s*:\s*)([\"'])(left|right)\3")


def split_values(text):
    """A CSS value's parts, with calc(...) and the like kept whole."""
    out, depth, cur = [], 0, ""
    for ch in text.strip():
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch.isspace() and depth == 0:
            if cur:
                out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


def css(text, notes):
    """The CSS text in its logical form; what changed is counted in notes."""
    def prop(m):
        name = m.group(2).lower()
        notes[name + " → " + PROPS[name]] = notes.get(name + " → " + PROPS[name], 0) + 1
        return m.group(1) + PROPS[name] + m.group(3)

    def value(m):
        name, side = m.group(2).lower(), m.group(4).lower()
        new = VALUES[name][side]
        notes["%s: %s → %s" % (name, side, new)] = notes.get("%s: %s → %s" % (name, side, new), 0) + 1
        return m.group(1) + m.group(2) + m.group(3) + new

    def box(m):
        parts = split_values(m.group(4))
        if len(parts) != 4 or parts[1] == parts[3] or any(p.lower() in ("inherit", "initial") for p in parts):
            return m.group(0)
        name, imp = m.group(2), m.group(5) or ""
        key = "%s: 4 values → %s-block + %s-inline" % (name.lower(), name.lower(), name.lower())
        notes[key] = notes.get(key, 0) + 1
        return "%s%s-block%s%s %s%s; %s-inline%s%s %s%s" % (
            m.group(1), name, m.group(3), parts[0], parts[2], imp, name, m.group(3), parts[3], parts[1], imp)
    text = VALUE_RE.sub(value, text)
    text = BOX_RE.sub(box, text)
    return PROP_RE.sub(prop, text)


def page(text, notes):
    """<html dir="rtl">, and the CSS of <style> blocks and style="" attributes."""
    def html_tag(m):
        tag = m.group(0)
        if re.search(r"\sdir\s*=", tag, re.I):
            if re.search(r"\sdir\s*=\s*[\"']?rtl", tag, re.I):
                return tag
            notes['<html dir="rtl">'] = 1
            return re.sub(r"(\sdir\s*=\s*)([\"']?)ltr\2", r'\1\2rtl\2', tag, flags=re.I)
        notes['<html dir="rtl">'] = 1
        tag = tag[:-1].rstrip() + ' dir="rtl">'
        lang = re.search(r"\slang\s*=\s*[\"']?([\w-]+)", tag, re.I)
        if not lang:
            notes['<html lang="ar">'] = 1
            tag = tag[:-1] + ' lang="ar">'
        elif not lang.group(1).lower().startswith("ar"):
            notes['lang="%s" kept: make it lang="ar" if the page is in Arabic' % lang.group(1)] = 1
        return tag
    text = re.sub(r"<html\b[^>]*>", html_tag, text, count=1, flags=re.I)
    text = re.sub(r"(<style\b[^>]*>)(.*?)(</style>)", lambda m: m.group(1) + css(m.group(2), notes) + m.group(3),
                  text, flags=re.I | re.S)
    text = re.sub(r"(\sstyle\s*=\s*)([\"'])(.*?)\2", lambda m: m.group(1) + m.group(2) + css(m.group(3), notes)
                  + m.group(2), text, flags=re.I | re.S)
    return text


def jsx(text, notes):
    def camel(m):
        k = m.group(1) + " → " + CAMEL[m.group(1)]
        notes[k] = notes.get(k, 0) + 1
        return CAMEL[m.group(1)] + m.group(2)

    def camel_value(m):
        new = VALUES["text-align" if m.group(1) == "textAlign" else "float"][m.group(4)]
        k = "%s: %s → %s" % (m.group(1), m.group(4), new)
        notes[k] = notes.get(k, 0) + 1
        return m.group(1) + m.group(2) + m.group(3) + new + m.group(3)
    return CAMEL_RE.sub(camel, CAMEL_VALUE_RE.sub(camel_value, text))


def files(top):
    if os.path.isfile(top):
        yield top
        return
    for folder, dirs, names in os.walk(top):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for n in sorted(names):
            if n.lower().endswith(CSS_FILES + PAGE_FILES + JSX_FILES) and not n.endswith(".min.css"):
                yield os.path.join(folder, n)


def main(argv):
    apply = "--apply" in argv
    args = [a for a in argv if a != "--apply"]
    top = os.path.abspath(os.path.expanduser(" ".join(args))) if args else os.getcwd()
    if not os.path.exists(top):
        print("%s: not found" % top)
        return 2
    changed = 0
    for path in files(top):
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError):
            continue
        notes = {}
        low = path.lower()
        new = css(text, notes) if low.endswith(CSS_FILES) else jsx(text, notes) if low.endswith(JSX_FILES) \
            else page(text, notes)
        if new == text:
            continue
        changed += 1
        rel = os.path.relpath(path, top) if os.path.isdir(top) else os.path.basename(path)
        print("%s:" % rel)
        for k, n in notes.items():
            print("  %s%s" % (k, " (%d)" % n if n > 1 else ""))
        if apply:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(new)
    if not changed:
        print("لا شيء لتغييره: الصفحات تتبع اتجاه اللغة أصلاً." if AR else
              "Nothing to change: the pages already follow the text's direction.")
    elif apply:
        print("\n%s %d." % ("عُدّلت الملفات:" if AR else "Files changed:", changed))
    else:
        print("\n%s" % ("لكتابة هذه التغييرات: /rtl --apply" if AR else "To write these changes: /rtl --apply"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
