"""Tool calls a model got wrong, put right (OmniCode's JSON / tool-call repair). Small local models often know what
to do but not how to write it down; instead of an error the call is repaired and run:

- arguments that are not quite JSON - single quotes, trailing commas, Python's True/False/None, raw line breaks in
  strings, missing closing quotes or braces, a code fence around them - are made JSON;
- calls written into the text instead of made as calls become calls: <tool_call>{...}</tool_call> (Qwen, Hermes),
  <function=name>{...}</function> (Llama), [TOOL_CALLS] [...] (Mistral), a ```json {"name", "arguments"} block,
  ReAct's "Action: bash / Action Input: ...", and SEARCH/REPLACE blocks under a file name (Aider's) as edits;
- other agents' tool and argument names become NewAl Code's: apply_diff -> edit, execute_terminal -> bash,
  read_file_range -> read, search_codebase -> grep; file_path -> path, old_string -> old, start_line/end_line ->
  offset/limit...

Only calls to tools this thread has are made, and a SEARCH block must name a file that exists (or, empty, a new one
in a folder that exists): a code example in an answer stays text."""

import json
import os
import re

TOOL_ALIASES = {
    "apply_diff": "edit", "search_replace": "edit", "replace_in_file": "edit", "str_replace": "edit",
    "str_replace_editor": "edit", "edit_file": "edit", "multiedit": "edit", "multi_edit": "edit", "Edit": "edit",
    "execute_terminal": "bash", "execute_command": "bash", "run_command": "bash", "run_terminal_cmd": "bash",
    "run_shell_command": "bash", "shell": "bash", "terminal": "bash", "exec": "bash", "Bash": "bash", "cmd": "bash",
    "read_file_range": "read", "read_file": "read", "view_file": "read", "open_file": "read", "cat": "read",
    "Read": "read", "view": "read",
    "write_file": "write", "create_file": "write", "Write": "write", "save_file": "write",
    "search_codebase": "grep", "search": "grep", "ripgrep": "grep", "rg": "grep", "grep_search": "grep",
    "Grep": "grep", "search_files": "grep", "codebase_search": "grep",
    "list_files": "glob", "find_files": "glob", "fd": "glob", "file_search": "glob", "Glob": "glob", "ls": "glob",
    "list_dir": "glob",
    "manage_background_process": "job", "background_process": "job", "process": "job",
    "fetch": "web_fetch", "fetch_url": "web_fetch", "WebFetch": "web_fetch",
    "web": "web_search", "WebSearch": "web_search",
    "patch": "apply_patch", "todo_write": "todo", "TodoWrite": "todo", "update_plan": "todo",
}
ARG_ALIASES = {
    "path": ("file_path", "filepath", "file", "filename", "target_file", "absolute_path", "relative_path",
             "notebook_path", "directory", "dir", "folder"),
    "old": ("old_string", "old_str", "search", "find", "original", "old_text", "before"),
    "new": ("new_string", "new_str", "replace", "replacement", "new_text", "after"),
    "all": ("replace_all",),
    "command": ("cmd", "commands", "shell_command", "script", "input"),
    "content": ("contents", "text", "file_text", "code", "data"),
    "pattern": ("query", "regex", "search_term", "term", "glob_pattern", "name"),
    "ignore_case": ("case_insensitive", "-i"),
    "url": ("link", "href"),
    "id": ("job", "job_id", "pid"),
}
MAIN_ARG = {"bash": "command", "powershell": "command", "read": "path", "glob": "pattern", "grep": "pattern",
            "web_fetch": "url", "web_search": "query", "job": "id", "skill": "name", "task": "prompt",
            "apply_patch": "patch"}


# ------------------------------------------------------------------ JSON

def _normalize(text):
    """JSON from JSON-ish text: single-quoted strings, raw control characters in strings, trailing commas, Python's
    literals, and what was left open (a string, braces, brackets) closed. Only what is outside strings changes: a
    string's content (code with ",}" in it) stays as it is."""
    out, stack, i, n = [], [], 0, len(text)
    quote = None
    while i < n:
        ch = text[i]
        if quote:
            if ch == "\\" and i + 1 < n:
                out.append(ch + text[i + 1])
                i += 2
                continue
            if ch == quote:
                out.append('"')
                quote = None
            elif ch == '"':                          # a double quote inside a single-quoted string
                out.append('\\"')
            else:
                out.append({"\n": "\\n", "\t": "\\t", "\r": "\\r"}.get(ch, ch))
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            out.append('"')
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
            out.append(ch)
        elif ch in "}]":
            j = len(out) - 1
            while j >= 0 and out[j] in (" ", "\t", "\r", "\n"):
                j -= 1
            if j >= 0 and out[j] == ",":             # a trailing comma
                del out[j]
            if stack:
                stack.pop()
            out.append(ch)
        else:
            m = re.match(r"(True|False|None)\b", text[i:i + 5])
            if m and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")):
                out.append({"True": "true", "False": "false", "None": "null"}[m.group(1)])
                i += len(m.group(1))
                continue
            out.append(ch)
        i += 1
    if quote:
        out.append('"')
    while out and out[-1] in (" ", "\t", "\r", "\n", ","):
        out.pop()
    return "".join(out) + "".join(reversed(stack))


def loads(text):
    """A dict from a model's JSON arguments, repaired when they are not quite JSON; None when nothing comes out."""
    if isinstance(text, dict):
        return text
    s = str(text or "").strip()
    if not s:
        return {}
    m = re.match(r"^```[\w-]*\s*\n?(.*?)\n?```\s*$", s, re.S)
    if m:
        s = m.group(1).strip()
    for attempt in (s, _normalize(s)):
        try:
            value = json.loads(attempt)
        except ValueError:
            continue
        if isinstance(value, str):                   # arguments given as a JSON string of the object
            try:
                inner = json.loads(value)
                return inner if isinstance(inner, dict) else None
            except ValueError:
                return None
        return value if isinstance(value, dict) else None
    return None


# ------------------------------------------------------------------ names

def normalize(name, args, allowed=None):
    """(name, args) with other agents' tool and argument names made NewAl Code's (when the tool exists)."""
    known = set(allowed) if allowed else None
    base = str(name or "").strip()
    if not (known and base in known):
        mapped = TOOL_ALIASES.get(base) or TOOL_ALIASES.get(base.lower()) or base
        if known is None or mapped in known or base not in known:
            base = mapped
    args = dict(args or {})
    for good, others in ARG_ALIASES.items():
        if good in args:
            continue
        for o in others:
            if o in args and not (base == "grep" and o == "name") and not (base == "bash" and o == "input" and
                                                                           "command" in args):
                args[good] = args.pop(o)
                break
    if base == "read":
        start = args.pop("start_line", args.pop("line_start", args.pop("from_line", None)))
        end = args.pop("end_line", args.pop("line_end", args.pop("to_line", None)))
        lines = args.pop("lines", None) or args.pop("range", None)
        if isinstance(lines, str) and re.match(r"^\d+\s*[-:]\s*\d+$", lines.strip()):
            start, end = [int(x) for x in re.split(r"\s*[-:]\s*", lines.strip())]
        if start is not None and "offset" not in args:
            args["offset"] = int(start)
            if end is not None and "limit" not in args:
                args["limit"] = max(1, int(end) - int(start) + 1)
    if base == "job" and isinstance(args.get("id"), int):
        args["id"] = str(args["id"])
    return base, args


# ------------------------------------------------------------------ calls in the text

def _obj_call(obj):
    """(name, args) from the usual shapes of a call written as JSON."""
    if not isinstance(obj, dict):
        return None
    fn = obj.get("function")
    if isinstance(fn, dict):
        obj = dict(fn, **{k: v for k, v in obj.items() if k != "function"})
        fn = None
    name = obj.get("name") or obj.get("tool") or obj.get("tool_name") or obj.get("action") or fn
    if not isinstance(name, str) or not name:
        return None
    args = obj.get("arguments", obj.get("args", obj.get("parameters", obj.get("input", obj.get("tool_input")))))
    if isinstance(args, str):
        parsed = loads(args)
        args = parsed if parsed is not None else {"__main__": args}
    if args is None:
        args = {k: v for k, v in obj.items() if k not in ("name", "tool", "tool_name", "action", "function", "type",
                                                            "id")}
    return name, args if isinstance(args, dict) else {}


def _main_arg(name, args):
    if "__main__" in args:
        value = args.pop("__main__")
        if name in MAIN_ARG and MAIN_ARG[name] not in args:
            args[MAIN_ARG[name]] = value
    return args


SEARCH_BLOCK = re.compile(
    r"(?:^|\n)(?P<path>[^\n`<>=]{1,300}?)[ \t]*\n(?:```[\w+-]*[ \t]*\n)?<{5,9}[ \t]*SEARCH[ \t]*\n(?P<old>.*?)\n?={5,9}"
    r"[ \t]*\n(?P<new>.*?)\n?>{5,9}[ \t]*REPLACE[ \t]*(?:\n```)?", re.S)


def _search_replace(text, root):
    """Edits from Aider-style blocks: a file's path on the line before <<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE."""
    found, spans = [], []
    for m in SEARCH_BLOCK.finditer(text):
        path = m.group("path").strip().strip("`*:").strip()
        path = re.sub(r"^(?:#+\s*|file:\s*|path:\s*)", "", path, flags=re.I).strip()
        if not path or " " in path and not os.path.exists(os.path.join(root or ".", path)):
            continue
        full = path if os.path.isabs(path) else os.path.join(root or ".", path)
        old, new = m.group("old"), m.group("new")
        if old.strip() and os.path.isfile(full):
            found.append(("edit", {"path": path, "old": old, "new": new}))
        elif not old.strip() and os.path.isdir(os.path.dirname(os.path.abspath(full))):
            found.append(("write", {"path": path, "content": new + ("\n" if new and not new.endswith("\n") else "")}))
        else:
            continue
        spans.append(m.span())
    return found, spans


def calls_in_text(text, allowed, root=None, loose=False):
    """([{"id", "name", "arguments"}], the text without them): calls a model wrote into its reply. loose (a small
    local model): bare JSON objects naming a tool and ReAct's Action lines count too."""
    allowed = list(allowed or [])
    text = text or ""
    found, spans = [], []

    def take(obj, span):
        call = _obj_call(obj)
        if not call:
            return
        name, args = normalize(call[0], call[1], allowed)
        args = _main_arg(name, args)
        if name in allowed:
            found.append((name, args))
            spans.append(span)
    for m in re.finditer(r"<tool_call>\s*(.*?)\s*(?:</tool_call>|$)", text, re.S):
        obj = loads(m.group(1))
        if obj is not None:
            take(obj, m.span())
    for m in re.finditer(r"<function=([\w.-]+)>\s*(.*?)\s*(?:</function>|$)", text, re.S):
        args = loads(m.group(2))
        take({"name": m.group(1), "arguments": args if args is not None else {"__main__": m.group(2)}}, m.span())
    m = re.search(r"\[TOOL_CALLS\]\s*(\[.*\])", text, re.S)
    if m:
        try:
            items = json.loads(m.group(1))
        except ValueError:
            items = []
        for obj in items if isinstance(items, list) else []:
            take(obj, m.span())
    if not found:
        for m in re.finditer(r"```(?:json|tool_call|tool)?[ \t]*\n(\{.*?\})\s*\n```", text, re.S):
            obj = loads(m.group(1))
            if obj is not None and _obj_call(obj):
                take(obj, m.span())
    if not found and loose:
        for m in re.finditer(r"(?:^|\n)\s*Action:\s*([\w.-]+)\s*\n\s*Action Input:\s*(.+?)(?=\n\s*(?:Observation|Thought|"
                             r"Action|Final Answer):|\Z)", text, re.S):
            raw = m.group(2).strip()
            args = loads(raw) if raw.startswith("{") else None
            take({"name": m.group(1), "arguments": args if args is not None else {"__main__": raw.strip("`")}},
                 m.span())
        if not found:
            for m in re.finditer(r"(?:^|\n)(\{[^\n]*\"(?:name|tool)\"[^\n]*\})\s*(?=\n|$)", text):
                obj = loads(m.group(1))
                if obj is not None:
                    take(obj, m.span())
    if "edit" in allowed or "write" in allowed:
        edits, edit_spans = _search_replace(text, root)
        for (name, args), span in zip(edits, edit_spans):
            if name in allowed and not any(a <= span[0] < b for a, b in spans):
                found.append((name, args))
                spans.append(span)
    calls = [{"id": "fixed_%d_%s" % (i, os.urandom(3).hex()), "name": n, "arguments": json.dumps(a, ensure_ascii=False)}
             for i, (n, a) in enumerate(found)]
    rest = text
    for a, b in sorted(spans, reverse=True):
        rest = rest[:a] + rest[b:]
    return calls, rest.strip()
