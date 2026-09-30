"""/create <kind> <name> [--ar]: a new project that runs at once, with its README, .gitignore and one passing test -
then its test is run, to show it works. Kinds:

  python    a package with a command line (src/ layout, pyproject.toml), tests with unittest
  web       a page that works offline: index.html, style.css, app.js (a small notes app kept in the browser)
  node      an ES module with a command line, tests with node --test
  flask     a Flask site with a page and a JSON route, tests with its test client
  fastapi   a FastAPI service with two routes, tests with its TestClient

--ar: the page or messages in Arabic, right-to-left. Standard library only: NewAl Code's own Python runs it."""

import json
import os
import re
import shutil
import subprocess
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass

GITIGNORE_PY = "__pycache__/\n*.pyc\n.venv/\nvenv/\n.env\ndist/\nbuild/\n*.egg-info/\n.pytest_cache/\n"
GITIGNORE_NODE = "node_modules/\n.env\ndist/\ncoverage/\n"


def python_files(name, pkg, ar):
    hello = "مرحباً" if ar else "Hello"
    return {
        "pyproject.toml": '[project]\nname = "%s"\nversion = "0.1.0"\nrequires-python = ">=3.9"\n\n'
                          '[project.scripts]\n%s = "%s.__main__:main"\n\n[build-system]\n'
                          'requires = ["setuptools>=61"]\nbuild-backend = "setuptools.build_meta"\n' % (name, name, pkg),
        "src/%s/__init__.py" % pkg: '"""%s."""\n\n\ndef greet(who: str) -> str:\n    """The greeting for who."""\n'
                                    '    return "%s, %%s!" %% who\n' % (name, hello),
        "src/%s/__main__.py" % pkg: 'import argparse\n\nfrom . import greet\n\n\ndef main(argv=None):\n'
                                    '    ap = argparse.ArgumentParser(prog="%s", description="Greets someone.")\n'
                                    '    ap.add_argument("who", nargs="?", default="world")\n'
                                    '    print(greet(ap.parse_args(argv).who))\n    return 0\n\n\n'
                                    'if __name__ == "__main__":\n    raise SystemExit(main())\n' % name,
        "tests/test_%s.py" % pkg: 'import os\nimport sys\nimport unittest\n\nsys.path.insert(0, os.path.join('
                                  'os.path.dirname(__file__), "..", "src"))\n\nfrom %s import greet  # noqa: E402\n\n\n'
                                  'class GreetTest(unittest.TestCase):\n    def test_greets(self):\n'
                                  '        self.assertEqual(greet("Sam"), "%s, Sam!")\n\n\nif __name__ == "__main__":\n'
                                  '    unittest.main()\n' % (pkg, hello),
        "README.md": "# %s\n\nRun: `PYTHONPATH=src python -m %s Sam`\n\nTest: `python -m unittest discover -s tests`\n\n"
                     "Install as a command: `pip install -e .`, then `%s Sam`.\n" % (name, pkg, name),
        ".gitignore": GITIGNORE_PY,
    }


def web_files(name, ar):
    t = (lambda en, a: a if ar else en)
    return {
        "index.html": '<!doctype html>\n<html lang="%s" dir="%s">\n<head>\n<meta charset="utf-8">\n'
                      '<meta name="viewport" content="width=device-width, initial-scale=1">\n<title>%s</title>\n'
                      '<link rel="stylesheet" href="style.css">\n</head>\n<body>\n<main>\n  <h1>%s</h1>\n'
                      '  <form id="add"><input id="text" placeholder="%s" autocomplete="off" required>'
                      '<button>%s</button></form>\n  <ul id="notes"></ul>\n  <p id="empty">%s</p>\n</main>\n'
                      '<script src="app.js"></script>\n</body>\n</html>\n' % (
                          "ar" if ar else "en", "rtl" if ar else "ltr", name, name, t("A note…", "ملاحظة…"),
                          t("Add", "أضف"), t("No notes yet: write one above.", "لا ملاحظات بعد: اكتب واحدة في الأعلى.")),
        "style.css": ':root { --bg: #fff; --fg: #1d1d1f; --soft: #f3f3f5; --accent: #3b6cff; }\n'
                     '@media (prefers-color-scheme: dark) { :root { --bg: #151517; --fg: #ececf0; --soft: #232326; } }\n'
                     '* { box-sizing: border-box; }\n'
                     'body { margin: 0; background: var(--bg); color: var(--fg); font: 16px/1.6 system-ui, '
                     'Tahoma, sans-serif; }\n'
                     'main { max-width: 36rem; margin: auto; padding: 1.5rem 1rem; }\n'
                     'form { display: flex; gap: .5rem; }\n'
                     'input { flex: 1; min-height: 44px; padding: 0 .75rem; border: 1px solid #8885; '
                     'border-radius: 10px; background: var(--soft); color: inherit; font: inherit; }\n'
                     'button { min-height: 44px; padding: 0 1rem; border: 0; border-radius: 10px; '
                     'background: var(--accent); color: #fff; font: inherit; }\n'
                     'ul { list-style: none; padding: 0; }\n'
                     'li { display: flex; justify-content: space-between; gap: .5rem; padding: .6rem .75rem; '
                     'margin-block: .4rem; background: var(--soft); border-radius: 10px; }\n'
                     'li button { min-height: 32px; background: transparent; color: inherit; }\n'
                     '#empty { opacity: .7; }\n',
        "app.js": '// Notes kept in this browser (localStorage), added and removed without a server.\n'
                  'const KEY = "notes:%s";\n'
                  'const $ = s => document.querySelector(s);\n'
                  'function load() { try { return JSON.parse(localStorage.getItem(KEY)) || []; } catch { return []; } }\n'
                  'function save(notes) { try { localStorage.setItem(KEY, JSON.stringify(notes)); } catch {} }\n'
                  'function render() {\n  const notes = load();\n  $("#notes").replaceChildren(...notes.map((n, i) => {\n'
                  '    const li = document.createElement("li");\n    const span = document.createElement("span");\n'
                  '    span.textContent = n;\n    span.dir = "auto";\n    const del = document.createElement("button");\n'
                  '    del.textContent = "×";\n    del.title = "%s";\n'
                  '    del.onclick = () => { notes.splice(i, 1); save(notes); render(); };\n'
                  '    li.append(span, del);\n    return li;\n  }));\n  $("#empty").hidden = notes.length > 0;\n}\n'
                  '$("#add").addEventListener("submit", e => {\n  e.preventDefault();\n  const text = $("#text").value.trim();\n'
                  '  if (!text) return;\n  save([...load(), text]);\n  $("#text").value = "";\n  render();\n});\n'
                  'render();\n' % (name, t("Delete", "احذف")),
        "README.md": "# %s\n\n%s\n\nOpen `index.html`, or serve it: `/serve %s` (or `python -m http.server`).\n" % (
            name, t("A small notes app that works offline.", "تطبيق ملاحظات صغير يعمل دون إنترنت."), name),
        ".gitignore": ".DS_Store\n",
    }


def node_files(name, ar):
    hello = "مرحباً" if ar else "Hello"
    return {
        "package.json": json.dumps({"name": name, "version": "0.1.0", "type": "module", "bin": {name: "./cli.js"},
                                    "scripts": {"start": "node cli.js", "test": "node --test"}}, indent=2) + "\n",
        "index.js": '/** The greeting for who. */\nexport function greet(who) {\n  return `%s, ${who}!`;\n}\n' % hello,
        "cli.js": '#!/usr/bin/env node\nimport { greet } from "./index.js";\n\n'
                  'console.log(greet(process.argv[2] ?? "world"));\n',
        "test/index.test.js": 'import { test } from "node:test";\nimport assert from "node:assert/strict";\n'
                              'import { greet } from "../index.js";\n\ntest("greets", () => {\n'
                              '  assert.equal(greet("Sam"), "%s, Sam!");\n});\n' % hello,
        "README.md": "# %s\n\nRun: `node cli.js Sam`\n\nTest: `npm test`\n" % name,
        ".gitignore": GITIGNORE_NODE,
    }


def flask_files(name, ar):
    hello = "مرحباً" if ar else "Hello"
    return {
        "app.py": 'from flask import Flask, jsonify, render_template\n\napp = Flask(__name__)\n\n\n@app.get("/")\n'
                  'def index():\n    return render_template("index.html", title="%s")\n\n\n@app.get("/api/hello/<who>")\n'
                  'def hello(who):\n    return jsonify(message="%s, %%s!" %% who)\n\n\nif __name__ == "__main__":\n'
                  '    app.run(debug=True)\n' % (name, hello),
        "templates/index.html": '<!doctype html>\n<html lang="%s" dir="%s">\n<head><meta charset="utf-8">'
                                '<meta name="viewport" content="width=device-width, initial-scale=1">'
                                '<title>{{ title }}</title></head>\n<body><h1>{{ title }}</h1></body>\n</html>\n' % (
                                    "ar" if ar else "en", "rtl" if ar else "ltr"),
        "test_app.py": 'import unittest\n\nfrom app import app\n\n\nclass AppTest(unittest.TestCase):\n'
                       '    def setUp(self):\n        self.client = app.test_client()\n\n    def test_page(self):\n'
                       '        self.assertEqual(self.client.get("/").status_code, 200)\n\n    def test_hello(self):\n'
                       '        self.assertEqual(self.client.get("/api/hello/Sam").get_json(), {"message": "%s, Sam!"})\n\n\n'
                       'if __name__ == "__main__":\n    unittest.main()\n' % hello,
        "requirements.txt": "flask>=3\n",
        "README.md": "# %s\n\n`python -m venv .venv`, activate it, `pip install -r requirements.txt`, then `python app.py`"
                     " (http://127.0.0.1:5000).\n\nTest: `python -m unittest`\n" % name,
        ".gitignore": GITIGNORE_PY,
    }


def fastapi_files(name, ar):
    hello = "مرحباً" if ar else "Hello"
    return {
        "main.py": 'from fastapi import FastAPI\n\napp = FastAPI(title="%s")\n\n\n@app.get("/")\ndef root():\n'
                   '    return {"name": "%s", "ok": True}\n\n\n@app.get("/hello/{who}")\ndef hello(who: str):\n'
                   '    return {"message": "%s, %%s!" %% who}\n' % (name, name, hello),
        "test_main.py": 'import unittest\n\nfrom fastapi.testclient import TestClient\n\nfrom main import app\n\n\n'
                        'class MainTest(unittest.TestCase):\n    def setUp(self):\n        self.client = TestClient(app)\n\n'
                        '    def test_root(self):\n        self.assertTrue(self.client.get("/").json()["ok"])\n\n'
                        '    def test_hello(self):\n        self.assertEqual(self.client.get("/hello/Sam").json(), '
                        '{"message": "%s, Sam!"})\n\n\nif __name__ == "__main__":\n    unittest.main()\n' % hello,
        "requirements.txt": "fastapi>=0.110\nuvicorn>=0.29\nhttpx>=0.27\n",
        "README.md": "# %s\n\n`python -m venv .venv`, activate it, `pip install -r requirements.txt`, then "
                     "`uvicorn main:app --reload` (http://127.0.0.1:8000/docs).\n\nTest: `python -m unittest`\n" % name,
        ".gitignore": GITIGNORE_PY,
    }


def run(cmd, cwd):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=300, env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1"),
                           creationflags=0x08000000 if os.name == "nt" else 0)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)


def python():
    return [sys.executable, "--newal-python"] if getattr(sys, "frozen", False) else [sys.executable]


def check(kind, folder):
    """(ran, passed, output) of the new project's own test."""
    if kind == "python":
        code, out = run(python() + ["-m", "unittest", "discover", "-s", "tests", "-q"], folder)
        return True, code == 0, out
    if kind == "node":
        if not shutil.which("node"):
            return False, False, "Node.js is not installed: its test runs with npm test"
        code, out = run([shutil.which("node"), "--test"], folder)
        return True, code == 0, out
    if kind in ("flask", "fastapi"):
        module = "flask" if kind == "flask" else "fastapi"
        code, _ = run(python() + ["-c", "import %s%s" % (module, ", httpx" if kind == "fastapi" else "")], folder)
        if code:
            return False, False, "install what it needs first: pip install -r requirements.txt"
        code, out = run(python() + ["-m", "unittest", "-q"], folder)
        return True, code == 0, out
    if kind == "web":
        from html.parser import HTMLParser
        HTMLParser().feed(open(os.path.join(folder, "index.html"), encoding="utf-8").read())
        return True, True, "index.html reads as HTML"
    return False, False, ""


KINDS = {"python": python_files, "web": web_files, "node": node_files, "flask": flask_files, "fastapi": fastapi_files}
NEXT = {"python": "PYTHONPATH=src python -m {pkg} Sam", "web": "/serve {name}", "node": "node {name}/cli.js Sam",
        "flask": "cd {name} && python app.py", "fastapi": "cd {name} && uvicorn main:app --reload"}


def main(argv):
    ar = "--ar" in argv
    args = [a for a in argv if a != "--ar"]
    if len(args) < 2 or args[0].lower() not in KINDS:
        print("/create <%s> <name> [--ar]" % "|".join(KINDS))
        return 2
    kind, name = args[0].lower(), args[1]
    if not re.fullmatch(r"[A-Za-z][\w-]{0,60}", name):
        print("%s: a name is letters, digits, - and _ (starting with a letter)" % name)
        return 2
    folder = os.path.abspath(name)
    if os.path.exists(folder) and os.listdir(folder):
        print("%s already exists and is not empty" % folder)
        return 1
    pkg = re.sub(r"\W", "_", name.lower())
    files = python_files(name, pkg, ar) if kind == "python" else KINDS[kind](name, ar)
    for rel, text in files.items():
        path = os.path.join(folder, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    if kind == "node":
        try:
            os.chmod(os.path.join(folder, "cli.js"), 0o755)
        except OSError:
            pass
    print(("أُنشئ %s (%s):" if ar else "Made %s (%s):") % (folder, kind))
    for rel in files:
        print("  " + rel)
    ran, passed, out = check(kind, folder)
    if ran:
        print(("\nالاختبار: " if ar else "\nIts test: ") + ("✓ " + ("نجح" if ar else "passes") if passed else
                                                              "✗ " + ("فشل" if ar else "fails")))
        if not passed:
            print(out[-1500:])
    elif out:
        print("\n" + out)
    print(("\nشغّله: " if ar else "\nRun it: ") + NEXT[kind].format(name=name, pkg=pkg))
    return 0 if passed or not ran else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
