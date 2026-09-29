"""Starts NewAl Code's web app on a scripted model (tests/fake_llm.py) and a small demo project, for looking at the
interface without a real model (and for screenshots: tools/ui_shots.js).

    python tools/ui_demo.py [--port 8799] [--real]   (--real: use the configured model instead of the script)"""

import argparse
import json
import os
import sys
import tempfile
import textwrap
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DESKTOP = os.path.dirname(HERE)
sys.path.insert(0, DESKTOP)
sys.path.insert(0, os.path.join(DESKTOP, "tests"))

SCRIPT = [
    {"text": "", "tools": [("read", {"path": "shop/cart.py"}), ("grep", {"pattern": "discount", "path": "tests"})]},
    {"tools": [("edit", {"path": "shop/cart.py", "old": "    return subtotal - discount_percent",
                         "new": "    return subtotal * (100 - discount_percent) / 100"})]},
    {"tools": [("bash", {"command": "python3 -m pytest -q"})]},
    "Fixed `total()` in `shop/cart.py`: the discount is now a percentage of the subtotal. The tests pass (2 passed).",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--real", action="store_true")
    a = ap.parse_args()
    home = tempfile.mkdtemp(prefix="nc-demo-home-")
    os.environ["NEWAL_CODE_HOME"] = home
    project = os.path.join(tempfile.mkdtemp(prefix="nc-demo-"), "shop-app")
    files = {
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
        "README.md": "# Shop app\n\nA tiny shop used to show NewAl Code.\n",
    }
    for rel, text in files.items():
        p = os.path.join(project, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(textwrap.dedent(text))
    os.system("cd %s && git init -q && git add -A && git -c user.email=demo@x -c user.name=demo commit -qm init" % project)
    from newal_code import server, settings
    settings.ensure_dirs()
    if not a.real:
        from fake_llm import FakeLLM

        def slow(reply):
            def f(body):
                time.sleep(0.6)
                return reply
            return f
        llm = FakeLLM([slow(x) for x in SCRIPT] * 3)
        settings.save({"models": {"demo-model": {"provider": "openai", "base_url": llm.url, "model": "demo",
                                                 "name": "Qwen3.5 4B (demo script)", "context": 32768}},
                       "model": "demo-model", "recent_projects": [project]})
    httpd, url = server.serve(a.port)
    print(json.dumps({"url": url, "project": project, "home": home}), flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
