"""The coding speed test: small real tasks in throw-away projects, each checked by hidden tests the agent never
sees. `newal-code bench` runs it on this computer with the chosen model; desktop/bench/run_old.py runs the same tasks
through the previous NewAl (project mode), so the two can be compared on the same model and computer."""


import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap

TASKS = [
    {
        "id": "bugfix",
        "prompt": "The cart total is wrong when there is a discount: total([{'price': 100, 'qty': 2}], 10) returns 190 "
                  "but should be 180 (10% off). Fix the bug so the tests pass.",
        "files": {
            "shop/__init__.py": "",
            "shop/cart.py": '''\
                """Shopping cart helpers."""


                def line_total(item):
                    return item["price"] * item["qty"]


                def total(items, discount_percent=0):
                    """Total price of the items after a percentage discount (0-100)."""
                    subtotal = sum(line_total(i) for i in items)
                    return subtotal - discount_percent
                ''',
            "tests/test_cart.py": '''\
                from shop.cart import total


                def test_no_discount():
                    assert total([{"price": 10, "qty": 3}]) == 30


                def test_discount():
                    assert total([{"price": 100, "qty": 2}], 10) == 180
                ''',
        },
        "hidden": '''\
            from shop.cart import total


            def test_hidden():
                assert total([{"price": 40, "qty": 1}], 25) == 30
                assert total([], 50) == 0
                assert total([{"price": 5, "qty": 2}, {"price": 1, "qty": 1}]) == 11
            ''',
    },
    {
        "id": "feature",
        "prompt": "Add a --chars option to wc.py: `python wc.py --chars FILE` prints the lines, words and number of "
                  "characters (three numbers). Without the option the output stays the same.",
        "files": {
            "wc.py": '''\
                import sys


                def count(text):
                    lines = text.count("\\n")
                    words = len(text.split())
                    return lines, words


                def main(argv):
                    path = argv[1]
                    with open(path, encoding="utf-8") as f:
                        text = f.read()
                    lines, words = count(text)
                    print(lines, words)


                if __name__ == "__main__":
                    main(sys.argv)
                ''',
            "tests/test_wc.py": '''\
                from wc import count


                def test_count():
                    assert count("a b\\nc\\n") == (2, 3)
                ''',
        },
        "hidden": '''\
            import os
            import subprocess
            import sys

            WD = os.environ["BENCH_WORKDIR"]


            def run(*args):
                p = os.path.join(WD, "sample.txt")
                with open(p, "w", encoding="utf-8") as f:
                    f.write("a b\\nc\\n")
                out = subprocess.run([sys.executable, "wc.py", *args, p], cwd=WD, capture_output=True, text=True)
                return out.stdout.split()


            def test_plain():
                assert run() == ["2", "3"]


            def test_chars():
                assert run("--chars") == ["2", "3", "6"]
            ''',
    },
    {
        "id": "implement",
        "prompt": "Implement slugify in textutils.py as its docstring describes.",
        "files": {
            "textutils.py": '''\
                def slugify(text):
                    """Return a URL slug: lowercase, every run of characters that are not a-z or 0-9 becomes a single
                    "-", and there is no "-" at the start or the end. Example: slugify("Hello, World!") == "hello-world"."""
                    raise NotImplementedError
                ''',
        },
        "hidden": '''\
            from textutils import slugify


            def test_hidden():
                assert slugify("Hello, World!") == "hello-world"
                assert slugify("  Many   spaces ") == "many-spaces"
                assert slugify("Python 3.12 Release") == "python-3-12-release"
                assert slugify("a--b__c") == "a-b-c"
                assert slugify("---") == ""
            ''',
    },
    {
        "id": "rename",
        "prompt": "Rename the function area_rect to rectangle_area everywhere in this project (definition, imports, "
                  "calls and tests).",
        "files": {
            "geometry/__init__.py": "from .shapes import area_rect, area_square\n",
            "geometry/shapes.py": '''\
                def area_rect(w, h):
                    return w * h


                def area_square(s):
                    return area_rect(s, s)
                ''',
            "report.py": '''\
                from geometry import area_rect


                def describe(w, h):
                    return f"{w}x{h} = {area_rect(w, h)}"
                ''',
            "tests/test_shapes.py": '''\
                from geometry import area_rect, area_square


                def test_rect():
                    assert area_rect(2, 3) == 6


                def test_square():
                    assert area_square(4) == 16
                ''',
        },
        "hidden": '''\
            import os
            import re

            from geometry import rectangle_area
            from geometry.shapes import area_square
            from report import describe

            WD = os.environ["BENCH_WORKDIR"]


            def test_hidden():
                assert rectangle_area(2, 5) == 10
                assert area_square(3) == 9
                assert describe(2, 3) == "2x3 = 6"


            def test_no_old_name_left():
                for folder, _, files in os.walk(WD):
                    for name in files:
                        if name.endswith(".py"):
                            with open(os.path.join(folder, name), encoding="utf-8") as f:
                                assert not re.search(r"\\barea_rect\\b", f.read()), name
            ''',
    },
    {
        "id": "question",
        "prompt": "Which port does server.py listen on when APP_PORT is not set? Answer with just the number.",
        "files": {
            "settings.py": '''\
                import os

                DEFAULTS = {"host": "127.0.0.1", "port": 8000}


                def get_port():
                    return int(os.environ.get("APP_PORT", DEFAULTS["port"] + 80))
                ''',
            "server.py": '''\
                from http.server import HTTPServer, SimpleHTTPRequestHandler

                from settings import get_port


                def start():
                    HTTPServer(("127.0.0.1", get_port()), SimpleHTTPRequestHandler).serve_forever()


                if __name__ == "__main__":
                    start()
                ''',
        },
        "answer": "8080",
    },
    {
        "id": "traceback",
        "prompt": "python -c \"from inventory import low_stock; print(low_stock('stock.json'))\" crashes with "
                  "TypeError: '<' not supported between instances of 'str' and 'int'. Fix the code (not the data) so "
                  "quantities stored as strings work.",
        "files": {
            "inventory.py": '''\
                from storage import load


                def low_stock(path, threshold=5):
                    items = load(path)
                    return [name for name, qty in items.items() if qty < threshold]
                ''',
            "storage.py": '''\
                import json


                def load(path):
                    with open(path, encoding="utf-8") as f:
                        data = json.load(f)
                    return {row["name"]: row["qty"] for row in data}
                ''',
            "stock.json": '[{"name": "apple", "qty": "3"}, {"name": "pear", "qty": 10}]\n',
        },
        "hidden": '''\
            import json
            import os

            from inventory import low_stock

            WD = os.environ["BENCH_WORKDIR"]


            def test_hidden(tmp_path):
                assert low_stock(os.path.join(WD, "stock.json")) == ["apple"]
                p = tmp_path / "s.json"
                p.write_text(json.dumps([{"name": "kiwi", "qty": "12"}, {"name": "fig", "qty": "1"}]))
                assert low_stock(str(p)) == ["fig"]
            ''',
        "unchanged": ["stock.json"],
    },
]


def make(task, root=None):
    """Creates the task's project in a new folder; returns its path."""
    folder = tempfile.mkdtemp(prefix="bench-%s-" % task["id"], dir=root)
    for rel, text in task["files"].items():
        path = os.path.join(folder, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(textwrap.dedent(text))
    return folder


def check(task, folder, answer):
    """(ok, why): the hidden tests pass (or the answer is right), and files that must not change did not."""
    for rel in task.get("unchanged", []):
        with open(os.path.join(folder, *rel.split("/")), encoding="utf-8") as f:
            if f.read() != textwrap.dedent(task["files"][rel]):
                return False, "%s was changed" % rel
    if "answer" in task:
        ok = task["answer"] in (answer or "")
        return ok, "answer %s" % ("has " if ok else "lacks ") + task["answer"]
    hidden = tempfile.mkdtemp(prefix="bench-hidden-")
    try:
        path = os.path.join(hidden, "test_hidden_%s.py" % task["id"])
        with open(path, "w", encoding="utf-8") as f:
            f.write(textwrap.dedent(task["hidden"]))
        env = dict(os.environ, PYTHONPATH=folder, BENCH_WORKDIR=folder, PYTHONDONTWRITEBYTECODE="1")
        p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", hidden, path],
                           cwd=hidden, env=env, capture_output=True, text=True, timeout=120)
        tail = (p.stdout + p.stderr).strip().splitlines()[-1:] or [""]
        return p.returncode == 0, tail[0][:200]
    finally:
        shutil.rmtree(hidden, ignore_errors=True)


def by_id(ids):
    if not ids:
        return list(TASKS)
    want = set(ids)
    return [t for t in TASKS if t["id"] in want]


def run(model="auto", ids=None, mode="auto-edit", limit=1200, log=print, client=None):
    """Runs the tasks through NewAl Code; returns the summary (seconds per task, pass/fail, tokens)."""
    import threading
    import time
    from . import agent as agentmod, models, session
    spec = models.resolve(model)
    t0 = time.time()
    client = client or models.connect(spec)
    log("model: %s, loaded in %.1f s, context %d" % (spec.get("name", spec["id"]), time.time() - t0, client.context()))
    results = []
    for task in by_id(ids):
        folder = make(task)
        s = session.Session(folder, model=spec["id"], mode=mode)
        events = []
        ag = agentmod.Agent(s, emit=events.append, approve=lambda req: "once", client=client, persist=False)
        warm = ag.warm()
        started = time.time()
        timer = threading.Timer(limit, ag.cancel.set)
        timer.start()
        answer, error = "", ""
        try:
            answer = ag.run(task["prompt"])
        except Exception as e:  # noqa: BLE001
            error = "%s: %s" % (type(e).__name__, e)
        finally:
            timer.cancel()
        seconds = time.time() - started
        ok, why = check(task, folder, answer) if not error else (False, error)
        u = s.usage
        rec = {"id": task["id"], "ok": ok, "why": why, "seconds": round(seconds, 1), "steps": ag.step,
               "model_calls": u.get("calls", 0), "prompt_tokens": u.get("new", 0), "cached_tokens": u.get("cached", 0),
               "generated_tokens": u.get("output", 0), "prompt_s": u.get("prompt_s", 0), "gen_s": u.get("gen_s", 0),
               "warm_s": round(warm or 0, 1),
               "verified": [e.get("ok") for e in events if e.get("type") == "verify"],
               "tools": [e.get("name") for e in events if e.get("type") == "tool_start"],
               "answer": (answer or "")[:600]}
        results.append(rec)
        log(json.dumps(rec, ensure_ascii=False))
        shutil.rmtree(folder, ignore_errors=True)
    total = sum(r["seconds"] for r in results)
    return {"program": "NewAl Code", "model": spec["id"], "tasks": len(results),
            "passed": sum(r["ok"] for r in results), "seconds": round(total, 1), "results": results}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="newal-code bench")
    ap.add_argument("--model", default="auto")
    ap.add_argument("--tasks", nargs="*", default=[])
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv or [])
    summary = run(a.model, a.tasks)
    print("TOTAL %.1f s, %d/%d passed" % (summary["seconds"], summary["passed"], summary["tasks"]))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=1, ensure_ascii=False)
    return 0
