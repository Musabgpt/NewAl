"""Skills: short expert playbooks (Markdown) added to a request when they match it.

Built-in skills ship in newal/skills/; the user's own go in %USERPROFILE%\\NewAl\\skills. Each file starts with
    ---
    name: ...
    description: ...
    triggers: word, word, ...
    ---
followed by the instructions."""

import os
import re

from . import config

BUILTIN = os.path.join(config.BUNDLE, "skills")
USER = os.path.join(config.HOME, "skills")
os.makedirs(USER, exist_ok=True)
DISABLED = os.path.join(config.DATA, "skills_disabled.txt")


def _disabled():
    try:
        with open(DISABLED, encoding="utf-8") as f:
            return {l.strip() for l in f if l.strip()}
    except OSError:
        return set()


def _parse(path, builtin):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = re.match(r"\s*---\s*\n(.*?)\n---\s*\n(.*)", text, re.S)
    meta, body = ({}, text) if not m else ({k.strip(): v.strip() for k, v in
                                            (l.split(":", 1) for l in m.group(1).splitlines() if ":" in l)}, m.group(2))
    sid = os.path.splitext(os.path.basename(path))[0]
    return {"id": sid, "name": meta.get("name", sid), "description": meta.get("description", ""),
            "triggers": [t.strip().lower() for t in meta.get("triggers", "").split(",") if t.strip()],
            "body": body.strip(), "builtin": builtin, "path": path}


def all_skills():
    out, off = {}, _disabled()
    for folder, builtin in ((BUILTIN, True), (USER, False)):
        if os.path.isdir(folder):
            for n in sorted(os.listdir(folder)):
                if n.endswith(".md"):
                    try:
                        s = _parse(os.path.join(folder, n), builtin)
                    except (OSError, ValueError):
                        continue
                    s["enabled"] = s["id"] not in off
                    out[s["id"]] = s                        # a user skill can replace a built-in one
    return list(out.values())


def _hit(trigger, text):
    """A whole-word match («repo» must not match «report.md»); Arabic words may carry و/ب/ل/ف/ك/ال in front
    and endings (ات، ي، ه...) behind."""
    t = re.escape(trigger)
    if re.search(r"[\u0600-\u06FF]", trigger):
        return re.search(r"(?<![\w])(?:[وبلفك]?(?:ال)?)%s" % t, text) is not None
    return re.search(r"(?<![\w.])%s(?![\w]|\.\w)" % t, text) is not None        # not inside file names


def relevant(text, k=2):
    """Enabled skills whose trigger words appear in the request, best first."""
    low = text.lower()
    scored = []
    for s in all_skills():
        if not s["enabled"]:
            continue
        hits = sum(1 for t in s["triggers"] if t and _hit(t, low))
        if hits:
            scored.append((hits, s))
    scored.sort(key=lambda x: -x[0])
    return [s for _, s in scored[:k]]


def as_prompt(skills):
    if not skills:
        return ""
    return "".join("Skill «%s»:\n%s\n\n" % (s["name"], s["body"][:2500]) for s in skills)


def save(name, description, triggers, body):
    sid = re.sub(r"[^\w-]", "_", name.strip().lower())[:40] or "skill"
    with open(os.path.join(USER, sid + ".md"), "w", encoding="utf-8") as f:
        f.write("---\nname: %s\ndescription: %s\ntriggers: %s\n---\n%s\n" % (name.strip(), description.strip(),
                                                                          triggers.strip(), body.strip()))
    return sid


def set_enabled(sid, enabled):
    off = _disabled()
    off.discard(sid) if enabled else off.add(sid)
    with open(DISABLED, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(off)))


def delete(sid):
    p = os.path.join(USER, sid + ".md")
    if os.path.exists(p):
        os.remove(p)
