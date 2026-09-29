"""Codex's apply_patch format, so models trained on it (gpt-oss, GPT-5-Codex...) can edit files the way they know:

*** Begin Patch
*** Update File: path/to/file.py
@@ def area(w, h):
-    return w + h
+    return w * h
*** Add File: new.py
+print("hi")
*** Delete File: old.py
*** End Patch

Context lines are matched exactly first, then ignoring trailing spaces, then ignoring indentation."""

import os


class PatchError(ValueError):
    pass


def parse(text):
    """-> [{"op": "add"|"delete"|"update", "path", "move_to", "lines" | "hunks"}]"""
    lines = text.replace("\r\n", "\n").split("\n")
    # Tolerate a heredoc wrapper or missing markers around the patch.
    start = next((i for i, l in enumerate(lines) if l.strip() == "*** Begin Patch"), None)
    if start is None:
        raise PatchError("the patch must start with '*** Begin Patch'")
    end = next((i for i in range(len(lines) - 1, start, -1) if lines[i].strip() == "*** End Patch"), len(lines))
    body = lines[start + 1:end]
    ops, cur, hunk = [], None, None
    for raw in body:
        line = raw
        if line.startswith("*** Add File: "):
            cur = {"op": "add", "path": line[len("*** Add File: "):].strip(), "lines": []}
            ops.append(cur)
            hunk = None
        elif line.startswith("*** Delete File: "):
            cur = {"op": "delete", "path": line[len("*** Delete File: "):].strip()}
            ops.append(cur)
            hunk = None
        elif line.startswith("*** Update File: "):
            cur = {"op": "update", "path": line[len("*** Update File: "):].strip(), "move_to": None, "hunks": []}
            ops.append(cur)
            hunk = None
        elif line.startswith("*** Move to: ") and cur and cur["op"] == "update":
            cur["move_to"] = line[len("*** Move to: "):].strip()
        elif line.strip() == "*** End of File":
            if hunk is not None:
                hunk["eof"] = True
        elif cur is None:
            if line.strip():
                raise PatchError("text before the first file header: %r" % line[:80])
        elif cur["op"] == "add":
            if not line.startswith("+") and line.strip():
                raise PatchError("added file lines must start with '+': %r" % line[:80])
            cur["lines"].append(line[1:] if line.startswith("+") else "")
        elif cur["op"] == "update":
            if line.startswith("@@"):
                hunk = {"anchor": line[2:].strip(), "old": [], "new": [], "eof": False}
                cur["hunks"].append(hunk)
                continue
            if hunk is None:
                hunk = {"anchor": "", "old": [], "new": [], "eof": False}
                cur["hunks"].append(hunk)
            if line.startswith("+"):
                hunk["new"].append(line[1:])
            elif line.startswith("-"):
                hunk["old"].append(line[1:])
            else:
                text = line[1:] if line.startswith(" ") else line
                hunk["old"].append(text)
                hunk["new"].append(text)
    if not ops:
        raise PatchError("the patch changes no file")
    return ops


def _find(lines, block, start):
    """Index where `block` (a list of lines) starts in `lines` at or after `start`, trying looser matches."""
    if not block:
        return start
    for norm in (lambda s: s, lambda s: s.rstrip(), lambda s: s.strip()):
        want = [norm(b) for b in block]
        for i in range(start, len(lines) - len(block) + 1):
            if all(norm(lines[i + j]) == want[j] for j in range(len(block))):
                return i
    return -1


def apply_update(text, hunks, path=""):
    lines = text.split("\n")
    pos = 0
    for h in hunks:
        if h["anchor"]:
            a = _find(lines, [h["anchor"]], pos)
            if a >= 0:
                pos = a
        at = _find(lines, h["old"], pos)
        if at < 0:
            at = _find(lines, h["old"], 0)
        if at < 0:
            snippet = "\n".join(h["old"][:6])
            raise PatchError("in %s these lines were not found:\n%s" % (path, snippet))
        if h.get("eof") and at + len(h["old"]) != len(lines) and not (lines and lines[-1] == ""):
            pass
        lines[at:at + len(h["old"])] = h["new"]
        pos = at + len(h["new"])
    return "\n".join(lines)


def apply(text, root, write, read, delete):
    """Applies a patch through the given file operations (so checkpoints and permissions apply);
    returns a short report."""
    done = []
    for op in parse(text):
        path = os.path.join(root, op["path"]) if not os.path.isabs(op["path"]) else op["path"]
        if op["op"] == "add":
            write(path, "\n".join(op["lines"]) + ("\n" if op["lines"] and op["lines"][-1] != "" else ""))
            done.append("A " + op["path"])
        elif op["op"] == "delete":
            delete(path)
            done.append("D " + op["path"])
        else:
            new = apply_update(read(path), op["hunks"], op["path"])
            target = path
            if op.get("move_to"):
                target = os.path.join(root, op["move_to"]) if not os.path.isabs(op["move_to"]) else op["move_to"]
                delete(path)
            write(target, new)
            done.append("M " + (op["move_to"] or op["path"]))
    return "Done:\n" + "\n".join(done)
