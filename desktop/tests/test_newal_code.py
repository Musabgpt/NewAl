"""Tests for NewAl Code (desktop/newal_code): tools, permissions, extensions, hooks, MCP, RAM planning, providers and
the agent loop end to end against a scripted model (tests/fake_llm.py)."""

import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
if os.environ.get("NEWAL_TEST_WATCHDOG"):            # CI: where a hung test is, then stop
    import faulthandler
    faulthandler.dump_traceback_later(int(os.environ["NEWAL_TEST_WATCHDOG"]), exit=True)
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
os.environ["NEWAL_CODE_HOME"] = tempfile.mkdtemp(prefix="newal-code-test-")
os.environ["HOME"] = tempfile.mkdtemp(prefix="newal-code-home-")     # no user-level skills, agents or MCP servers
os.environ["USERPROFILE"] = os.environ["HOME"]
for _k, _v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@t"), ("GIT_COMMITTER_NAME", "t"),
               ("GIT_COMMITTER_EMAIL", "t@t")):
    os.environ.setdefault(_k, _v)

from fake_llm import FakeLLM  # noqa: E402
from newal_code import (agent as agentmod, extensions, gguf, hardware, hooks, mcp, models, patch,  # noqa: E402
                        permissions, providers, runtime, session, settings, tools)


def make_project(files):
    root = tempfile.mkdtemp(prefix="nc-proj-")
    for rel, text in files.items():
        p = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(textwrap.dedent(text))
    return root


CALC = {
    "calc.py": "def add(a, b):\n    return a - b\n",
    "test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
}


def fake_client(llm, anthropic=False, on_device=False):
    if anthropic:
        spec = {"id": "fake-anthropic", "name": "fake", "provider": "anthropic"}
        return models.Client(spec, providers.Anthropic("k", llm.base), "fake")
    spec = {"id": "fake", "name": "fake", "provider": "openai"}
    if on_device:
        spec["preset"] = "ollama"            # a model on this device (models.Client.on_device)
    return models.Client(spec, providers.OpenAICompat(llm.url), "fake")


def run_agent(script, files=None, mode="auto-edit", approve=lambda r: "once", goal="", text="do it", root=None,
              on_device=False, **kw):
    llm = FakeLLM(script)
    root = root or make_project(files or CALC)
    s = session.Session(root, mode=mode)
    s.goal = goal
    events = []
    ag = agentmod.Agent(s, emit=events.append, approve=approve, client=fake_client(llm, on_device=on_device), **kw)
    answer = ag.run(text)
    llm.close()
    return answer, events, s, llm, root


class ToolsTest(unittest.TestCase):
    def ctx(self, root):
        s = session.Session(root)
        a = agentmod.Agent(s, client=None)
        return agentmod.ToolContext(a)

    def test_notebooks_read_and_edit(self):
        nb = {"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": [
            {"cell_type": "markdown", "id": "intro", "metadata": {}, "source": ["# Sales\n"]},
            {"cell_type": "code", "id": "load", "metadata": {}, "execution_count": 1, "source": ["x = 2\n", "x * 21"],
             "outputs": [{"output_type": "execute_result", "execution_count": 1, "metadata": {},
                          "data": {"text/plain": ["42"]}}]}]}
        root = make_project({"sales.ipynb": json.dumps(nb)})
        self.assertIn("notebook_edit", tools.default_set(None, root))
        self.assertNotIn("notebook_edit", tools.default_set(None, make_project(CALC)))
        c = self.ctx(root)
        text, _ = tools.call(c, "read", {"path": "sales.ipynb"})
        self.assertIn("# cell 1 (code, id load)\nx = 2\nx * 21", text)
        self.assertIn("# output of cell 1\n42", text)
        text, meta = tools.call(c, "notebook_edit", {"path": "sales.ipynb", "cell": "1", "source": "x = 3\nx * 14"})
        self.assertIn("Replaced cell 1", text)
        self.assertIn("+x = 3", meta["diff"])
        tools.call(c, "notebook_edit", {"path": "sales.ipynb", "cell": "load", "mode": "insert",
                                        "source": "print(undefined_name)"})
        with open(os.path.join(root, "sales.ipynb")) as f:
            saved = json.load(f)
        self.assertEqual([x["cell_type"] for x in saved["cells"]], ["markdown", "code", "code"])
        self.assertEqual(saved["cells"][1]["outputs"], [])          # the old output no longer belongs to it
        self.assertEqual(saved["cells"][1]["source"], ["x = 3\n", "x * 14"])
        self.assertTrue(saved["cells"][2]["id"])
        text, _ = tools.call(c, "notebook_edit", {"path": "sales.ipynb", "cell": 2, "source": "%matplotlib inline\nprint(x)"})
        self.assertNotIn("Problems", text)                             # magics and earlier cells' names are fine
        text, _ = tools.call(c, "notebook_edit", {"path": "sales.ipynb", "cell": 0, "mode": "delete"})
        self.assertIn("(2 cells now)", text)
        d = permissions.decide("auto-edit", "notebook_edit", "edit", {"path": "sales.ipynb"}, root, {})
        self.assertEqual(d.action, "allow")

    def test_read_edit_write_and_undo(self):
        root = make_project(CALC)
        c = self.ctx(root)
        text, meta = tools.call(c, "read", {"path": "calc.py"})
        self.assertEqual(text, "def add(a, b):\n    return a - b")
        text, _ = tools.call(c, "read", {"path": "calc.py", "offset": 2, "limit": 1})
        self.assertEqual(text, "(lines 2-2 of 2)\n    return a - b")
        text, meta = tools.call(c, "edit", {"path": "calc.py", "old": "return a - b", "new": "return a + b"})
        self.assertIn("Edited calc.py", text)
        self.assertEqual(meta["plus"], 1)
        tools.call(c, "write", {"path": "notes/new.md", "content": "hi\n"})
        changes = {x["path"]: x["status"] for x in c.session.changes()}
        self.assertEqual(changes, {"calc.py": "modified", "notes/new.md": "added"})
        c.session.undo()
        with open(os.path.join(root, "calc.py")) as f:
            self.assertIn("a - b", f.read())
        self.assertFalse(os.path.exists(os.path.join(root, "notes", "new.md")))

    def test_edit_tolerates_indentation_and_line_numbers(self):
        out, n, how = tools.apply_edit("def f():\n    x = 1\n    return x\n", "def f():\n  x = 1", "def f():\n  x = 2")
        self.assertEqual(out, "def f():\n    x = 2\n    return x\n")
        self.assertEqual(how, "indentation")
        out, _, _ = tools.apply_edit("a = 1\nb = 2\n", "1\ta = 1\n2\tb = 2", "1\ta = 5\n2\tb = 2")
        self.assertIn("a = 5", out)
        with self.assertRaises(tools.ToolError) as e:
            tools.apply_edit("x = 1\nx = 1\n", "x = 1", "x = 2")
        self.assertIn("matches 2 places", str(e.exception))
        out, n, _ = tools.apply_edit("x = 1\nx = 1\n", "x = 1", "x = 2", replace_all=True)
        self.assertEqual((out, n), ("x = 2\nx = 2\n", 2))
        with self.assertRaises(tools.ToolError) as e:
            tools.apply_edit("def total(items):\n    return sum(items)\n", "def totl(items):", "x")
        self.assertIn("closest text", str(e.exception))

    def test_edit_reports_problems_it_leaves(self):
        root = make_project({"t.py": "def slug(t):\n    raise NotImplementedError\n"})
        c = self.ctx(root)
        text, _ = tools.call(c, "edit", {"path": "t.py", "old": "    raise NotImplementedError",
                                         "new": "    return re.sub('[^a-z]+', '-', t)"})
        self.assertIn("'re' is not defined", text)
        text, _ = tools.call(c, "write", {"path": "t.py", "content": "import re\n\ndef slug(t):\n    return re.sub('a', 'b', t)\n"})
        self.assertNotIn("Problems", text)
        text, _ = tools.call(c, "write", {"path": "u.py", "content": "def f(:\n"})
        self.assertIn("syntax error", text)

    def test_crlf_kept(self):
        root = make_project({})
        p = os.path.join(root, "w.txt")
        with open(p, "wb") as f:
            f.write(b"one\r\ntwo\r\n")
        c = self.ctx(root)
        tools.call(c, "edit", {"path": "w.txt", "old": "two", "new": "three"})
        with open(p, "rb") as f:
            self.assertEqual(f.read(), b"one\r\nthree\r\n")

    def test_glob_grep_bash_and_argument_aliases(self):
        root = make_project({"a/x.py": "def alpha():\n    pass\n", "b/y.js": "function beta() {}\n",
                             ".gitignore": "build/\n", "build/z.py": "def gamma(): pass\n"})
        c = self.ctx(root)
        text, _ = tools.call(c, "glob", {"pattern": "**/*.py"})
        self.assertIn("a/x.py", text)
        self.assertNotIn("build/z.py", text)
        text, meta = tools.call(c, "grep", {"pattern": r"def \w+", "glob": "*.py"})
        self.assertEqual(meta["matches"], 1)
        text, meta = tools.call(c, "bash", {"cmd": "echo hello && exit 3"})
        self.assertTrue(text.startswith("exit 3"))
        self.assertIn("hello", text)
        text, _ = tools.call(c, "read", {"file_path": "a/x.py"})
        self.assertIn("alpha", text)

    def test_bash_timeout_and_background_job(self):
        root = make_project({})
        c = self.ctx(root)
        text, meta = tools.call(c, "bash", {"command": "sleep 5", "timeout": 1})
        self.assertEqual(meta["exit"], 124, text)
        text, meta = tools.call(c, "bash", {"command": "echo started; sleep 30", "background": True})
        self.assertIn("job1", text)
        text, _ = tools.call(c, "job", {"id": "job1"})
        self.assertIn("running", text)
        tools.call(c, "job", {"id": "job1", "action": "stop"})

    def test_mistyped_absolute_paths_land_in_the_project(self):
        root = make_project({"src/app.py": "x = 1\n"})
        c = self.ctx(root)
        text, meta = tools.call(c, "write", {"path": "/nowhere/%s/hello.py" % os.path.basename(root),
                                             "content": "print('hi')\n"})
        self.assertTrue(os.path.exists(os.path.join(root, "hello.py")), text)
        tools.call(c, "write", {"path": "/tmp/nowhere/src/b.py", "content": "y = 2\n"})
        self.assertTrue(os.path.exists(os.path.join(root, "src", "b.py")))
        text, _ = tools.call(c, "read", {"path": "/elsewhere/project/src/app.py"})
        self.assertIn("x = 1", text)
        if os.name != "nt" and os.geteuid() != 0:
            # a new file where nothing may be written (a small model's /hello.py on a phone): the project's
            ro = tempfile.mkdtemp()
            os.chmod(ro, 0o555)
            self.addCleanup(os.chmod, ro, 0o755)
            tools.call(c, "write", {"path": os.path.join(ro, "hello2.py"), "content": "print(2)\n"})
            self.assertTrue(os.path.exists(os.path.join(root, "hello2.py")))
        out = os.path.join(tempfile.mkdtemp(), "out.txt")           # a real folder elsewhere: written there
        tools.call(c, "write", {"path": out, "content": "z\n"})
        self.assertTrue(os.path.exists(out))
        self.assertFalse(os.path.exists(os.path.join(root, "out.txt")))

    def test_apply_patch(self):
        root = make_project({"m.py": "def area(w, h):\n    return w + h\n\n\ndef other():\n    return 1\n"})
        c = self.ctx(root)
        p = ("*** Begin Patch\n*** Update File: m.py\n@@ def area(w, h):\n-    return w + h\n+    return w * h\n"
             "*** Add File: n.py\n+X = 1\n*** End Patch")
        text, meta = tools.call(c, "apply_patch", {"patch": p})
        self.assertIn("M m.py", text)
        with open(os.path.join(root, "m.py")) as f:
            self.assertIn("w * h", f.read())
        with open(os.path.join(root, "n.py")) as f:
            self.assertEqual(f.read(), "X = 1\n")
        with self.assertRaises(patch.PatchError):
            patch.parse("no patch here")


class CircuitTest(unittest.TestCase):
    """The circuit breaker (circuit.py): the same failing call a 3rd time, or the step budget, ends the turn."""

    def test_states(self):
        from newal_code import circuit
        b = circuit.Breaker()
        k = circuit.Breaker.key(-1, "bash", {"command": "pyhton x.py"})
        self.assertEqual(b.record(k, True, "bash", "exit 127"), "")
        self.assertEqual(b.state, circuit.CLOSED)
        self.assertIn("change it", b.record(k, True, "bash", "exit 127"))
        self.assertEqual(b.state, circuit.HALF_OPEN)
        b.record(circuit.Breaker.key(-1, "read", {"path": "x.py"}), False, "read")
        self.assertEqual(b.state, circuit.CLOSED)                         # a success closes it again
        self.assertIn("Stopping", b.record(k, True, "bash", "exit 127"))
        self.assertTrue(b.open)
        self.assertEqual(b.reason, "bash failed 3 times the same way (exit 127)")
        fresh = circuit.Breaker()
        after_edit = circuit.Breaker.key(4, "bash", {"command": "pyhton x.py"})      # after a file change: a new try
        fresh.record(k, True, "bash")
        fresh.record(k, True, "bash")
        self.assertEqual(fresh.record(after_edit, True, "bash"), "")
        self.assertFalse(fresh.open)
        self.assertTrue(circuit.Breaker().over_budget(25, 25))
        self.assertEqual(circuit.step_budget({}, local=True), 25)
        self.assertEqual(circuit.step_budget({}, local=False), 60)
        self.assertEqual(circuit.step_budget({"max_steps": 9}, local=True), 9)
        self.assertEqual(circuit.step_budget({"max_steps": 9}, {"steps": 4}), 4)

    def test_a_turn_stops_at_the_third_identical_failure(self):
        bad = {"tools": [("bash", {"command": "python3 no_such_file.py"})]}
        answer, events, _, llm, _ = run_agent([bad, bad, bad, bad, "never reached"])
        self.assertTrue(answer.startswith("Stopped: bash failed 3 times the same way (exit"), answer)
        ends = [e for e in events if e.get("type") == "tool_end" and e.get("name") == "bash"]
        self.assertEqual(len(ends), 3)                                     # the 4th never ran
        self.assertIn("change it", ends[1]["text"])

    def test_the_step_budget(self):
        script = [{"tools": [("bash", {"command": "echo %d" % i})]} for i in range(10)] + ["done"]
        llm = FakeLLM(script)
        self.addCleanup(llm.close)
        sess = session.Session(make_project(CALC))
        ag = agentmod.Agent(sess, emit=lambda e: None, approve=lambda r: "once", client=fake_client(llm))
        ag.cfg["max_steps"] = 4
        self.assertEqual(ag.run("go"), "Stopped after 4 steps without finishing.")

    def test_a_model_on_this_device_gets_25_steps_and_the_local_prompt(self):
        from newal_code import circuit, prompts
        script = [{"tools": [("bash", {"command": "echo %d" % i})]} for i in range(30)] + ["done"]
        llm = FakeLLM(script)
        self.addCleanup(llm.close)
        spec = {"id": "ollama/qwen2.5-coder:1.5b", "name": "qwen", "provider": "openai", "preset": "ollama"}
        client = models.Client(spec, providers.OpenAICompat(llm.url), "qwen2.5-coder:1.5b")
        sess = session.Session(make_project({"README.md": "# x\n"}))
        ag = agentmod.Agent(sess, emit=lambda e: None, approve=lambda r: "once", client=client)
        self.assertEqual(ag.cfg.get("max_steps"), 0)                    # the settings as they come: automatic
        self.assertEqual(ag.run("go"), "Stopped after 25 steps without finishing.")
        self.assertEqual(len(llm.requests), 25)
        system = llm.requests[0]["messages"][0]["content"]
        self.assertTrue(system.startswith(prompts.system(ag.shell, local=True, steps=25)[:400]))
        self.assertIn("The same failing call a third time ends your turn, and a turn has 25 steps.", system)
        # an API model: the full prompt, and 60 steps
        api = agentmod.Agent(session.Session(make_project({})), client=fake_client(FakeLLM([])))
        self.assertFalse(api.client.on_device)
        self.assertNotIn("a turn has", api.system_prompt())
        self.assertEqual(circuit.step_budget(api.cfg, None, local=api.client.on_device), 60)
        on = lambda **sp: models.Client(dict({"id": "x", "provider": "openai"}, **sp), None, "x").on_device  # noqa
        self.assertTrue(on(base_url="http://localhost:11434/v1"))
        self.assertTrue(on(preset="lmstudio"))
        self.assertTrue(on(small=True))
        self.assertTrue(on(provider="local"))
        self.assertFalse(on(base_url="https://api.deepseek.com/v1", preset="deepseek"))
        self.assertFalse(on(provider="anthropic"))

    def test_the_documented_local_prompt_is_the_one_sent(self):
        from newal_code import prompts
        with open(os.path.join(HERE, "..", "..", "docs", "omnicode.md"), encoding="utf-8") as f:
            doc = f.read()
        block = re.search(r"<!-- the text of prompts.LOCAL[^>]*-->\s*```text\n(.*?)\n```", doc, re.S).group(1)
        self.assertEqual(block, prompts.LOCAL)


class RepairTest(unittest.TestCase):
    """Tool calls a small model got wrong, put right and run (repair.py)."""

    def test_json_that_is_not_quite_json(self):
        from newal_code import repair
        self.assertEqual(repair.loads("{'command': 'ls -la', }"), {"command": "ls -la"})
        self.assertEqual(repair.loads('{"path": "a.js", "content": "const o = {a: 1,};\n"}'),
                         {"path": "a.js", "content": "const o = {a: 1,};\n"})      # a string's ",}" stays
        self.assertEqual(repair.loads('{"path": "x.py", "content": "print(1)\nprint(2)"'),
                         {"path": "x.py", "content": "print(1)\nprint(2)"})           # left open: closed
        self.assertEqual(repair.loads("{'all': True, 'x': None}"), {"all": True, "x": None})
        self.assertEqual(repair.loads('```json\n{"command": "pytest -q"}\n```'), {"command": "pytest -q"})
        self.assertEqual(repair.loads('"{\\"command\\": \\"ls\\"}"'), {"command": "ls"})
        self.assertIsNone(repair.loads("not json at all"))

    def test_calls_written_as_text(self):
        from newal_code import repair
        root = make_project(CALC)
        allowed = ["read", "edit", "write", "bash", "glob", "grep"]

        def calls(text, loose=True):
            return [(c["name"], json.loads(c["arguments"])) for c in repair.calls_in_text(text, allowed, root,
                                                                                            loose)[0]]
        self.assertEqual(calls('<tool_call>\n{"name": "bash", "arguments": {"command": "ls"}}\n</tool_call>'),
                         [("bash", {"command": "ls"})])
        self.assertEqual(calls("<tool_call>\n{'name': 'execute_terminal', 'arguments': {'command': 'ls'}"),
                         [("bash", {"command": "ls"})])
        self.assertEqual(calls('<function=read_file>{"file_path": "calc.py", "start_line": 1, "end_line": 2}'
                               '</function>'), [("read", {"path": "calc.py", "offset": 1, "limit": 2})])
        self.assertEqual(calls('[TOOL_CALLS] [{"name": "search_codebase", "arguments": {"query": "def add"}}]'),
                         [("grep", {"pattern": "def add"})])
        self.assertEqual(calls("Action: bash\nAction Input: python3 -m pytest -q"),
                         [("bash", {"command": "python3 -m pytest -q"})])
        self.assertEqual(calls("Action: bash\nAction Input: ls", loose=False), [])      # API models: tags only
        self.assertEqual(calls("calc.py\n```python\n<<<<<<< SEARCH\n    return a - b\n=======\n    return a + b\n"
                               ">>>>>>> REPLACE\n```"), [("edit", {"path": "calc.py", "old": "    return a - b",
                                                                   "new": "    return a + b"})])
        self.assertEqual(calls("new.py\n<<<<<<< SEARCH\n=======\nprint('hi')\n>>>>>>> REPLACE"),
                         [("write", {"path": "new.py", "content": "print('hi')\n"})])
        for text in ('Use this:\n```json\n{"name": "Sam", "age": 3}\n```',        # a code example stays text
                     "gone.py\n<<<<<<< SEARCH\nx = 1\n=======\nx = 2\n>>>>>>> REPLACE",   # no such file
                     '<tool_call>{"name": "launch_rocket", "arguments": {}}</tool_call>'):   # no such tool
            self.assertEqual(calls(text), [], text)

    def test_a_call_written_as_text_runs(self):
        answer, events, _, _, _ = run_agent(['<tool_call>\n{"name": "execute_terminal", "arguments": '
                                             '{"command": "echo repaired"}}\n</tool_call>', "done"], on_device=True)
        self.assertEqual(answer, "done")
        end = next(e for e in events if e.get("type") == "tool_end")
        self.assertEqual((end["name"], end["ok"]), ("bash", True))
        self.assertIn("repaired", end["text"])

    def test_a_search_replace_block_edits_the_file(self):
        answer, _, _, _, root = run_agent(["calc.py\n<<<<<<< SEARCH\n    return a - b\n=======\n    return a + b\n"
                                           ">>>>>>> REPLACE", "Fixed add."], on_device=True)
        self.assertEqual(answer, "Fixed add.")
        with open(os.path.join(root, "calc.py")) as f:
            self.assertIn("return a + b", f.read())

    def test_an_api_models_example_stays_text(self):
        # An API model makes its calls itself: a call it shows in its answer (an example) is not run.
        shown = ('A call looks like this:\n```json\n{"name": "bash", "arguments": {"command": "echo ran"}}\n```\n'
                 "calc.py\n<<<<<<< SEARCH\n    return a - b\n=======\n    return a + b\n>>>>>>> REPLACE")
        answer, events, _, _, root = run_agent([shown], text="how does a tool call look?")
        self.assertEqual([e for e in events if e.get("type") == "tool_start"], [])
        self.assertIn('"name": "bash"', answer)
        with open(os.path.join(root, "calc.py")) as f:
            self.assertIn("return a - b", f.read())

    def test_broken_arguments_and_other_agents_names_run(self):
        broken = "{'file_path': 'calc.py', 'old_string': 'return a - b', 'new_string': 'return a + b',"
        answer, events, _, _, root = run_agent([{"tools": [("apply_diff", broken)]}, "Fixed."])
        end = next(e for e in events if e.get("type") == "tool_end")
        self.assertEqual((end["name"], end["ok"]), ("edit", True), end)
        with open(os.path.join(root, "calc.py")) as f:
            self.assertIn("return a + b", f.read())


class RepairArgsTest(unittest.TestCase):
    def test_a_tools_own_arguments_are_never_renamed(self):
        # (a regression: "name" became "pattern" for every tool, so the phone could not open an app or press back,
        # and the skill tool lost its skill's name)
        from newal_code import repair
        allowed = ["read", "edit", "write", "bash", "grep", "glob", "phone", "skill", "job", "mcp__x__find"]
        for name, args in (("phone", {"action": "open_app", "name": "Settings"}), ("phone", {"action": "key",
                                                                                             "name": "back"}),
                           ("phone", {"action": "type", "text": "hello"}), ("skill", {"name": "pdf"}),
                           ("mcp__x__find", {"query": "a", "name": "b", "text": "c"})):
            self.assertEqual(repair.normalize(name, args, allowed), (name, args))
        # other agents' names still become ours, where the tool takes ours
        self.assertEqual(repair.normalize("glob", {"name": "*.py"}, allowed), ("glob", {"pattern": "*.py"}))
        self.assertEqual(repair.normalize("write", {"file_path": "a", "text": "x"}, allowed),
                         ("write", {"path": "a", "content": "x"}))
        self.assertEqual(repair.normalize("manage_background_process", {"pid": 7}, allowed), ("job", {"id": "7"}))
        # through the loop: the phone call reaches the tool with its name
        from unittest import mock
        from newal_code import phone
        seen = []
        on = mock.patch.object(phone, "available", lambda: True)             # a phone: the tool is offered
        on.start()
        self.addCleanup(on.stop)
        real = tools.REGISTRY["phone"].fn
        tools.REGISTRY["phone"].fn = lambda ctx, action, **a: (seen.append((action, a)), ("done", {}))[1]
        self.addCleanup(setattr, tools.REGISTRY["phone"], "fn", real)
        run_agent([{"tools": [("phone", {"action": "open_app", "name": "Settings"})]}, "Opened."],
                  files={"README.md": "# x\n"}, mode="full-auto")
        self.assertEqual(seen, [("open_app", {"name": "Settings"})])


class SlipsTest(unittest.TestCase):
    """A small model's slips that no longer end its turn (seen with a 2B model in the speed test)."""

    def test_a_call_cut_off_at_the_output_limit_is_not_run_and_the_history_stays_json(self):
        # A 2B model ran away inside an edit's arguments until the output limit: the half call was "repaired" and
        # half an edit written, and its arguments, sent back as they were, made llama.cpp answer every later request
        # of the thread with HTTP 500 "Failed to parse tool call arguments as JSON".
        cut = {"tools": [("edit", '{"path": "calc.py", "old": "    return a - b", "new": "    return a +')],
               "finish": "length"}
        fix = {"tools": [("edit", {"path": "calc.py", "old": "return a - b", "new": "return a + b"})]}
        answer, events, s, llm, root = run_agent([cut, fix, "Fixed add."], on_device=True)
        self.assertEqual(answer, "Fixed add.")
        first = next(e for e in events if e.get("type") == "tool_end")
        self.assertFalse(first["ok"])
        self.assertIn("cut off at the output limit", first["text"])
        with open(os.path.join(root, "calc.py")) as f:
            self.assertIn("return a + b", f.read())                        # the whole edit, not half of the cut one
        for req in llm.requests[1:]:
            for m in req["messages"]:
                for tc in m.get("tool_calls") or []:
                    json.loads(tc["function"]["arguments"])                # every call sent back is JSON
        # a thread saved before (a broken call in its transcript) is sent with JSON arguments too
        s.messages.insert(1, {"role": "assistant", "content": "", "tool_calls": [{"id": "old", "type": "function",
                              "function": {"name": "edit", "arguments": '{"path": "a.py", "new": "x'}}]})
        s.messages.insert(2, {"role": "tool", "tool_call_id": "old", "content": "error"})
        ag = agentmod.Agent(s, client=fake_client(FakeLLM([]), on_device=True))
        sent = ag.request_messages()[2]["tool_calls"][0]["function"]["arguments"]
        self.assertEqual(json.loads(sent), {"path": "a.py", "new": "x"})
        self.assertEqual(providers.valid_arguments("not json at all"), "{}")

    def test_an_empty_reply_is_asked_for_its_answer_once(self):
        answer, _, _, llm, _ = run_agent(["<think>it is 8000 + 80</think>", "8080"], files={"README.md": "# x\n"},
                                         text="Which port does it use? Answer with the number.")
        self.assertEqual(answer, "8080")
        self.assertEqual(llm.requests[1]["messages"][-1]["content"], "Reply to the user now, in one short sentence.")
        answer, _, _, llm, _ = run_agent(["", ""], files={"README.md": "# x\n"}, text="Which port?")
        self.assertEqual((answer, len(llm.requests)), ("", 2))                     # no loop


class PruneTest(unittest.TestCase):
    def test_old_outputs_pruned_at_80_percent_then_a_summary(self):
        llm = FakeLLM(["The user fixed add; tests pass."])
        self.addCleanup(llm.close)
        sess = session.Session(make_project(CALC))
        sess.save_meta()                                               # (a turn does this at its start)
        ag = agentmod.Agent(sess, emit=lambda e: None, client=fake_client(llm))
        sess.add({"role": "user", "content": "fix it"})
        for i in range(10):
            name, out = ("read", "1\tdef add(a, b):\n" + "x" * 900) if i == 3 else ("bash", "exit %d\n" % (i % 2) + "y" * 900)
            sess.add({"role": "assistant", "content": "", "tool_calls": [
                {"id": "c%d" % i, "type": "function", "function": {"name": name, "arguments": "{}"}}]})
            sess.add({"role": "tool", "tool_call_id": "c%d" % i, "content": out})
        self.assertEqual(settings.user()["auto_compact"], 0.8)
        sess.last_prompt_tokens = int(0.81 * ag.client.context())
        self.assertFalse(ag._maybe_compact())                          # pruned: no summary yet
        results = [m["content"] for m in sess.messages if m.get("role") == "tool"]
        self.assertEqual(results[0], "[Terminal output pruned: exit 0]")
        self.assertEqual(results[1], "[Terminal output pruned: exit 1]")
        self.assertEqual(results[3], "[File content pruned: read it again if you need it]")
        self.assertTrue(all(len(r) > 900 for r in results[-6:]))      # the latest six stay whole
        reloaded = session.Session.load(sess.id)                       # recorded: a resume sees the same
        self.assertEqual([m["content"] for m in reloaded.messages if m.get("role") == "tool"][0],
                         "[Terminal output pruned: exit 0]")
        sess.last_prompt_tokens = int(0.9 * ag.client.context())      # still full: now the summary
        self.assertTrue(ag._maybe_compact())
        self.assertIn("The user fixed add; tests pass.", sess.messages[0]["content"])


class OmniCodeTest(unittest.TestCase):
    """The rest of OmniCode's list: search by ripgrep/fd with the same results as without, the goal's percentage,
    background processes by their PIDs, and /status showing both."""

    def test_ripgrep_and_the_python_search_agree(self):
        from unittest import mock
        root = make_project({"a/x.py": "def alpha():\n    return 1\n", "b/y.js": "function beta() {}\n",
                             ".gitignore": "build/\n", "build/z.py": "def gamma(): pass\n",
                             "node_modules/m/i.js": "def nope\n", ".hidden/h.py": "def hidden(): pass\n",
                             ".github/workflows/ci.yml": "name: def ci\n"})
        s = session.Session(root)
        ag = agentmod.Agent(s, client=fake_client(FakeLLM([])))
        c = tools.ToolContext(ag) if hasattr(tools, "ToolContext") else agentmod.ToolContext(ag)

        def both(name, args):
            fast = tools.call(c, name, args)
            real_which = shutil.which
            with mock.patch("shutil.which", lambda n, *a, **k: None if n in ("rg", "fd", "fdfind") else real_which(n)):
                slow = tools.call(c, name, args)
            return fast, slow
        if not shutil.which("rg"):
            self.skipTest("ripgrep is not installed here")
        (ft, fm), (st, sm) = both("grep", {"pattern": r"def \w+"})
        self.assertTrue(fm.get("rg"))
        self.assertEqual(sorted(ft.splitlines()), sorted(st.splitlines()))
        self.assertEqual((fm["matches"], fm["files"]), (sm["matches"], sm["files"]))
        self.assertIn(".github/workflows/ci.yml:1: name: def ci", ft)
        for gone in ("build/z.py", "node_modules", ".hidden"):
            self.assertNotIn(gone, ft)
        (ft, _), (st, _) = both("grep", {"pattern": "return", "context": 1, "ignore_case": True})
        self.assertEqual(ft, st)
        (ft, _), (st, _) = both("glob", {"pattern": "**/*.py"})
        self.assertEqual(sorted(ft.splitlines()), sorted(st.splitlines()))
        (ft, _), _ = both("grep", {"pattern": r"(?<=def )alpha"})         # look-behind: Python's search answers
        self.assertIn("a/x.py:1:", ft)

    def test_the_goal_has_a_percentage(self):
        script = [{"tools": [("write", {"path": "a.py", "content": "x = 1\n"})]}, "Wrote a.py.",
                  "CONTINUE 40%: b.py is missing", {"tools": [("write", {"path": "b.py", "content": "y = 2\n"})]},
                  "Wrote b.py.", "DONE"]
        answer, events, s, _, _ = run_agent(script, files={"README.md": "# x\n"}, goal="a.py and b.py exist",
                                            text="make a.py and b.py")
        checks = [e for e in events if e.get("type") == "goal_check"]
        self.assertEqual([(e["done"], e["progress"]) for e in checks], [(False, 40), (True, 100)])
        self.assertIn("b.py is missing", next(m["content"] for m in s.messages if "not met yet" in
                                              str(m.get("content"))))
        self.assertEqual(s.goal, "")

    def test_background_processes_by_pid_and_status(self):
        from newal_code import service
        root = make_project({})
        svc = service.Service()
        s = svc.create(root)
        ag = svc.agent(s.id)
        c = agentmod.ToolContext(ag)
        text, _ = tools.call(c, "bash", {"command": "echo up; sleep 30", "background": True})
        pid = int(re.search(r"pid (\d+)", text).group(1))
        try:
            listed, _ = tools.call(c, "job", {"action": "list"})
            self.assertIn("job1 pid %d running" % pid, listed)
            out, _ = tools.call(c, "job", {"id": str(pid)})              # by its process id too
            self.assertIn("job1 (pid %d): running" % pid, out)
            s.goal, s.goal_progress = "ship it", 45
            status = svc.command(s, "/status")["reply"]
            self.assertIn("Goal: ship it (45% done)", status)
            self.assertIn("job1 (pid %d) running: echo up; sleep 30" % pid, status)
        finally:
            stopped, _ = tools.call(c, "job", {"id": "job1", "action": "stop"})
        self.assertIn("stopped job1 (pid %d)" % pid, stopped)


class AnnounceTest(unittest.TestCase):
    def test_a_reply_that_only_announces_is_told_to_act(self):
        # the 0.8B on a phone once answered "I will create hello.py and run it." and stopped: told once, it acts
        answer, events, s, llm, root = run_agent(
            ["I will create hello.py and run it with python3.",
             {"tools": [("write", {"path": "hello.py", "content": "print('hi')\n"})]}, "Created hello.py."],
            files={"README.md": "# x\n"}, text="Create hello.py that prints hi")
        self.assertTrue(os.path.exists(os.path.join(root, "hello.py")))
        self.assertEqual(answer, "Created hello.py.")
        self.assertIn("Do it now", json.dumps(s.messages))
        # a question, a plan, or an answer that asks something: left as they are
        for text, reply in (("What does calc.py do?", "I will explain: it adds."),
                            ("Plan how to add a CLI", "I will add argparse, then a main()."),
                            ("Fix it", "I will need the file name. Which one?")):
            answer, _, s, _, _ = run_agent([reply], files={"README.md": "# x\n"}, text=text)
            self.assertNotIn("Do it now", json.dumps(s.messages), text)
        from newal_code.agent import announces
        self.assertTrue(announces("سأنشئ الملف الآن."))
        self.assertFalse(announces("Created hello.py and ran it."))


class LeftRunningTest(unittest.TestCase):
    def test_a_command_that_leaves_a_program_running_returns(self):
        # (a regression: `python server.py &` kept the command's output open, and the turn waited for it forever)
        root = make_project({})
        ag = agentmod.Agent(session.Session(root), client=fake_client(FakeLLM([])))
        c = agentmod.ToolContext(ag)
        started = time.time()
        text, meta = tools.call(c, "bash", {"command": "sleep 37 & echo started"})
        self.assertLess(time.time() - started, 15)
        self.assertIn("started", text)
        if os.name != "nt":          # (Git Bash on Windows gives a program started with & its own output: no wait)
            self.assertIn("start servers and other long-running programs with background=true", text)
        if os.name != "nt" and shutil.which("pgrep"):
            gone = time.time() + 5                  # (a killed process can take a moment to go on a busy machine)
            left = ["pgrep", "-f", "^sleep 37$"]          # (anchored: not a shell whose command line names it)
            while time.time() < gone and subprocess.run(left, capture_output=True).stdout:
                time.sleep(0.2)
            self.assertEqual(subprocess.run(left, capture_output=True).stdout, b"")
        text, _ = tools.call(c, "bash", {"command": "sleep 38 > /dev/null 2>&1 & echo fine"})     # output elsewhere:
        self.assertNotIn("background=true", text)                                              # nothing to stop
        subprocess.run(["pkill", "-f", "^sleep 38$"], capture_output=True) if os.name != "nt" else None


class ClipTest(unittest.TestCase):
    def test_windows_line_ends_and_progress_bars(self):
        self.assertEqual(tools.clip("a.txt\r\nb.txt\r\nlast\r\n").strip(), "a.txt\nb.txt\nlast")
        self.assertEqual(tools.clip("download 10%\rdownload 100%\ndone\n").strip(), "download 100%\ndone")
        self.assertEqual(tools.clip("x 1%\rx 99%\r\nok\r\n").strip(), "x 99%\nok")


class PermissionsTest(unittest.TestCase):
    def test_powershell_and_cmd_commands(self):
        c = permissions.classify_command
        for cmd in ("Remove-Item C:\\ -Recurse -Force", "rm -r -fo ~", "Remove-Item $env:USERPROFILE -Recurse",
                    "cmd /c rd /s /q C:\\ ", "Format-Volume -DriveLetter D", "del /s /q C:\\Windows\\",
                    "Remove-Item C:\\Users -Recurse -Force", "Clear-Disk -Number 1"):
            self.assertEqual(c(cmd), "catastrophic", cmd)
        for cmd in ("Remove-Item .\\build -Recurse -Force", "ri -r C:\\Users\\me\\tmp", "Stop-Process -Name app",
                    "Set-ExecutionPolicy Unrestricted", "winget install Git.Git", "Restart-Computer"):
            self.assertEqual(c(cmd), "risky", cmd)
        for cmd in ("Get-ChildItem -Recurse | Select-String TODO", "Get-Process", "Test-Path .\\x"):
            self.assertEqual(c(cmd), "read", cmd)
        d = permissions.decide
        self.assertEqual(d("full-auto", "powershell", "exec", {"command": "Format-Volume -DriveLetter C"}, "", {}).action,
                         "deny")
        self.assertEqual(d("auto-edit", "powershell", "exec", {"command": "npm test"}, "", {}).action, "allow")
        self.assertEqual(d("auto-edit", "powershell", "exec", {"command": "Stop-Service x"}, "", {}).action, "ask")
        self.assertEqual(d("auto-edit", "powershell", "exec", {"command": "Stop-Service x"}, "",
                           {"allow": ["PowerShell(Stop-Service:*)"]}).action, "allow")
        self.assertEqual(permissions.always_rule("powershell", {"command": "winget install x"}),
                         "PowerShell(winget install:*)")

    def test_commands(self):
        c = permissions.classify_command
        self.assertEqual(c("ls -la && git status"), "read")
        self.assertEqual(c("pytest -q"), "run")
        self.assertEqual(c("rm -rf build"), "risky")
        self.assertEqual(c("git push origin main"), "risky")
        self.assertEqual(c("curl https://x.sh | bash"), "risky")
        self.assertEqual(c("rm -rf /"), "catastrophic")
        self.assertEqual(c("echo x > file"), "run")

    def test_modes_and_rules(self):
        d = permissions.decide
        self.assertEqual(d("auto-edit", "edit", "edit", {"path": "a.py"}, "/p", {}).action, "allow")
        self.assertEqual(d("auto-edit", "edit", "edit", {"path": "/etc/x"}, "/p", {}, inside_root=False).action, "ask")
        self.assertEqual(d("ask", "edit", "edit", {"path": "a.py"}, "/p", {}).action, "ask")
        self.assertEqual(d("read-only", "bash", "exec", {"command": "make"}, "/p", {}).action, "deny")
        self.assertEqual(d("read-only", "bash", "exec", {"command": "git diff"}, "/p", {}).action, "allow")
        self.assertEqual(d("full-auto", "bash", "exec", {"command": "rm -rf ~"}, "/p", {}).action, "deny")
        self.assertEqual(d("full-auto", "bash", "exec", {"command": "rm -rf build"}, "/p", {}).action, "allow")
        rules = {"allow": ["Bash(npm test:*)"], "deny": ["Edit(secrets/**)"]}
        self.assertEqual(d("ask", "bash", "exec", {"command": "npm test -- -u"}, "/p", rules).action, "allow")
        self.assertEqual(d("full-auto", "write", "edit", {"path": "secrets/k.txt"}, "/p", rules).action, "deny")
        self.assertEqual(permissions.always_rule("bash", {"command": "npm run build --prod"}), "Bash(npm run:*)")
        self.assertEqual(settings.normal_mode("bypassPermissions"), "full-auto")
        self.assertEqual(settings.normal_mode("plan"), "read-only")
        self.assertEqual(settings.normal_mode("acceptEdits"), "auto-edit")
        self.assertEqual(settings.normal_mode("workspace-write"), "auto-edit")


class SandboxTest(unittest.TestCase):
    """Commands write only inside the project (auto-edit) or nowhere (read-only): Landlock on Linux, Seatbelt on
    macOS, low integrity on Windows. Each test runs on whichever of them this computer has."""

    def setUp(self):
        from newal_code import sandbox
        if not sandbox.available():
            self.skipTest("no sandbox on this system")
        self.sb = sandbox
        # Not under the temp folder: temp folders are always writable in the sandbox.
        self.root = tempfile.mkdtemp(prefix=".nc-sandbox-", dir=HERE)
        self.addCleanup(shutil.rmtree, self.root, True)
        self.outside = tempfile.mkdtemp(prefix=".nc-outside-", dir=HERE)
        self.addCleanup(shutil.rmtree, self.outside, True)
        self.out = self.outside.replace("\\", "/")          # a path bash takes on Windows too

    def run_in(self, mode, command, dirs=()):
        s = session.Session(self.root, mode=mode)
        s.dirs = list(dirs)
        ctx = agentmod.ToolContext(agentmod.Agent(s))
        return tools.call(ctx, "bash", {"command": command})[1]

    def run_python(self, code, mode="auto-edit", network=True, *args):
        argv, on = self.sb.wrap([sys.executable, "-c", code] + list(args), self.root, mode, network=network)
        self.assertTrue(on)
        return subprocess.run(argv, cwd=self.root, capture_output=True, text=True, timeout=300)

    def test_added_folder_is_writable_and_inside(self):
        m = self.run_in("auto-edit", "echo b > %s/out.txt" % self.out, dirs=[self.outside])
        self.assertTrue(m["sandboxed"])
        self.assertEqual(m["exit"], 0, m)
        self.assertTrue(os.path.exists(os.path.join(self.outside, "out.txt")))
        s = session.Session(self.root)
        s.dirs = [self.outside]
        ctx = agentmod.ToolContext(agentmod.Agent(s))
        self.assertTrue(tools.inside(ctx, os.path.join(self.outside, "x.py")))       # edits there need no approval
        self.assertFalse(tools.inside(ctx, os.path.join(HERE, "x.py")))

    def test_auto_edit_writes_only_in_the_project(self):
        m = self.run_in("auto-edit", "echo a > in.txt && echo b > %s/out.txt" % self.out)
        self.assertTrue(m["sandboxed"])
        self.assertNotEqual(m["exit"], 0, m)
        self.assertTrue(tools.SANDBOX_DENIED.search(m["output"]), m["output"])     # so the user is asked to rerun
        self.assertTrue(os.path.exists(os.path.join(self.root, "in.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.outside, "out.txt")))

    def test_read_only_writes_nothing_and_full_auto_is_free(self):
        m = self.run_in("read-only", "echo a > in.txt")
        self.assertNotEqual(m["exit"], 0, m)
        self.assertFalse(os.path.exists(os.path.join(self.root, "in.txt")))
        m = self.run_in("full-auto", "echo b > %s/out.txt" % self.out)
        self.assertFalse(m["sandboxed"])
        self.assertTrue(os.path.exists(os.path.join(self.outside, "out.txt")))
        d = permissions.decide("read-only", "bash", "exec", {"command": "make"}, self.root, {}, sandboxed=True)
        self.assertEqual(d.action, "allow")

    def test_programs_without_a_shell(self):
        """Exit codes and output come back; the error is the one the escalation looks for."""
        code = ("import sys\n"
                "open('in.txt', 'w').write('a')\n"
                "try:\n"
                "    open(sys.argv[1] + '/out.txt', 'w').write('b')\n"
                "except OSError as e:\n"
                "    print('denied:', e)\n"
                "    sys.exit(3)\n")
        r = self.run_python(code, "auto-edit", True, self.outside)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertTrue(tools.SANDBOX_DENIED.search(r.stdout), r.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.root, "in.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.outside, "out.txt")))

    def test_temp_folder_and_files_already_there(self):
        os.makedirs(os.path.join(self.root, "a", "b"))
        with open(os.path.join(self.root, "a", "b", "old.txt"), "w") as f:
            f.write("old")
        code = ("import os, tempfile\n"
                "with tempfile.NamedTemporaryFile('w', delete=False) as f:\n"
                "    f.write('t')\n"
                "os.remove(f.name)\n"
                "open(os.path.join('a', 'b', 'old.txt'), 'w').write('new')\n"
                "os.rename(os.path.join('a', 'b', 'old.txt'), os.path.join('a', 'moved.txt'))\n"
                "os.makedirs(os.path.join('a', 'c', 'd'))\n"
                "open(os.path.join('a', 'c', 'd', 'n.txt'), 'w').write('n')\n")
        r = self.run_python(code)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(os.path.join(self.root, "a", "moved.txt")) as f:
            self.assertEqual(f.read(), "new")
        self.assertTrue(os.path.exists(os.path.join(self.root, "a", "c", "d", "n.txt")))

    def test_no_network(self):
        k = self.sb.kind()
        if k == "low-integrity" or (k == "landlock" and self.sb.abi() < 4):
            self.skipTest("this sandbox does not limit the network here")
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)
        self.addCleanup(srv.close)
        code = ("import socket\nsocket.create_connection(('127.0.0.1', %d), timeout=10).close()\nprint('connected')"
                % srv.getsockname()[1])
        on = self.run_python(code, "auto-edit", True)
        off = self.run_python(code, "auto-edit", False)
        self.assertEqual(on.returncode, 0, on.stdout + on.stderr)
        self.assertNotEqual(off.returncode, 0, off.stdout + off.stderr)
        self.assertNotIn("connected", off.stdout)

    def test_cancel_stops_everything_the_command_started(self):
        s = session.Session(self.root, mode="auto-edit")
        ctx = agentmod.ToolContext(agentmod.Agent(s))
        py = sys.executable.replace("\\", "/")
        late = "import time; time.sleep(4); open('late.txt', 'w').write('x')"
        threading.Timer(1.5, ctx.cancel.set).start()
        t0 = time.time()
        code, out = tools.run_command(ctx, '"%s" -c "%s"' % (py, late), timeout=60)
        self.assertEqual(code, 130, out)
        self.assertLess(time.time() - t0, 15)
        time.sleep(5)
        self.assertFalse(os.path.exists(os.path.join(self.root, "late.txt")))

    def other_git_bash(self, seconds):
        """A Git Bash at normal integrity, like a terminal the user left open: MSYS2's own bash.exe from Git's folder
        (Git's bin\\bash.exe is a launcher that starts that one, and would leave it running when killed)."""
        root = self.sb.git_root(tools.shell_command()[0][0])
        exe = os.path.join(root, "usr", "bin", "bash.exe") if root else tools.shell_command()[0][0]
        p = subprocess.Popen([exe, "-c", "sleep %d" % seconds])
        self.addCleanup(lambda: subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True))
        time.sleep(3)
        return p

    def test_git_bash_open_at_the_same_time(self):
        """Windows: a Git Bash at normal integrity (a terminal left open) while a sandboxed one runs."""
        if self.sb.kind() != "low-integrity" or tools.shell_command()[1] != "bash":
            self.skipTest("Windows with Git Bash only")
        self.other_git_bash(25)
        m = self.run_in("auto-edit", "echo a > in.txt && echo ok")
        self.assertEqual(m["exit"], 0, m)
        self.assertTrue(os.path.exists(os.path.join(self.root, "in.txt")))

    def test_external_commands_in_the_sandbox(self):
        """Programs found on PATH (not only shell builtins) run in the sandbox; on Windows with a Git Bash also
        running at normal integrity (the case that needs MSYS2 objects of the sandbox's own)."""
        if self.sb.kind() == "low-integrity" and tools.shell_command()[1] == "bash":
            self.other_git_bash(20)
        m = self.run_in("auto-edit", "sleep 0 && ls > listing.txt && cat listing.txt && type sleep")
        print("\n[external commands] exit %s:\n%s" % (m["exit"], m["output"][-1500:]))
        self.assertEqual(m["exit"], 0, m["output"])
        self.assertTrue(os.path.exists(os.path.join(self.root, "listing.txt")))

    def test_windows_git_bash_runs_from_its_own_copy(self):
        """Windows: the sandboxed Git Bash runs from NewAl Code's copy of the MSYS2 install (hard links to Git's
        files), which MSYS2 takes as its root: objects of its own, apart from any Git Bash the user has open."""
        if self.sb.kind() != "low-integrity" or tools.shell_command()[1] != "bash":
            self.skipTest("Windows with Git Bash only")
        t = time.time()
        argv, env = self.sb.msys_view(tools.shell_command()[0] + ["cygpath -w /"])
        print("\n[msys copy] %s %s, %.2f s, made now: %s, error=%r" % (
            argv[0], env.get("MSYSTEM"), time.time() - t, self.sb.msys_copy.made, self.sb.msys_copy.error))
        self.assertIn(os.path.join("NewAlCode", "msys"), argv[0], self.sb.msys_copy.error)
        t = time.time()
        self.sb.msys_view(tools.shell_command()[0] + ["true"])
        self.assertLess(time.time() - t, 1.0)                     # made once, then only checked
        m = self.run_in("auto-edit", "cygpath -w / && echo $MSYSTEM")
        print("[msys root in the sandbox] %s" % m["output"].strip())
        self.assertEqual(m["exit"], 0, m["output"])
        self.assertIn(os.path.join("NewAlCode", "msys").lower(), m["output"].lower())

    def test_windows_msys_copy_without_hard_links(self):
        """Windows: where Windows makes no hard link (Git in Program Files for a user who cannot write there, another
        drive) Git's usr\\bin is copied, MSYS2 runs from the copy the same way, and removing a copy leaves Git's
        folder alone (the copy is mostly junctions to it)."""
        if self.sb.kind() != "low-integrity" or tools.shell_command()[1] != "bash":
            self.skipTest("Windows with Git Bash only")
        root = self.sb.git_root(tools.shell_command()[0][0])
        home = tempfile.mkdtemp(prefix="nc-msys-")
        self.addCleanup(self.sb._remove, home)
        t = time.time()
        view = self.sb.msys_copy(root, hardlinks=False, home=home)
        print("\n[msys copy without links] %s, %.1f s: %s error=%r" % (
            view, time.time() - t, self.sb.msys_copy.made, self.sb.msys_copy.error))
        self.assertTrue(view, self.sb.msys_copy.error)
        self.assertEqual(self.sb.msys_copy.made["linked"], 0)
        out = subprocess.run([os.path.join(view, "usr", "bin", "bash.exe"), "-c", "cygpath -w / && ls /etc/fstab"],
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertTrue(os.path.samefile(out.stdout.splitlines()[0].strip(), view), out.stdout)
        self.sb._remove(view)
        self.assertFalse(os.path.exists(view))
        for part in (("usr", "bin", "msys-2.0.dll"), ("etc", "fstab"), ("usr", "bin", "bash.exe")):
            self.assertTrue(os.path.isfile(os.path.join(root, *part)), part)

    def test_msys_copy_is_made_once_per_set_of_files(self):
        """The copy of an MSYS2 install for sandboxed commands (on a made-up install here; junctions become
        symbolic links off Windows): linked or copied, reused while Git's files are the same, made again and the
        old one removed when they change, and Git's folder never touched."""
        git = tempfile.mkdtemp(prefix="nc-git-")
        home = tempfile.mkdtemp(prefix="nc-msys-")
        self.addCleanup(shutil.rmtree, git, True)
        self.addCleanup(self.sb._remove, home)
        for part, text in ((("usr", "bin", "msys-2.0.dll"), "dll"), (("usr", "bin", "bash.exe"), "bash"),
                           (("usr", "bin", "ls.exe"), "ls"), (("usr", "share", "terminfo", "x"), "t"),
                           (("etc", "fstab"), "none /tmp usertemp"), (("mingw64", "bin", "git.exe"), "git"),
                           (("git-bash.exe",), "launcher")):
            os.makedirs(os.path.join(git, *part[:-1]), exist_ok=True)
            with open(os.path.join(git, *part), "w") as f:
                f.write(text)
        self.assertEqual(self.sb.git_root(os.path.join(git, "usr", "bin", "bash.exe")), git)
        self.assertEqual(self.sb.git_root(os.path.join(git, "bin", "bash.exe")), git)
        self.assertIsNone(self.sb.git_root(os.path.join(git, "usr", "bin", "ls.exe")))
        orig = self.sb._junction
        if os.name != "nt":
            self.sb._junction = lambda target, link: os.symlink(target, link)
            self.addCleanup(setattr, self.sb, "_junction", orig)
        first = self.sb.msys_copy(git, home=home)
        self.assertTrue(first, self.sb.msys_copy.error)
        self.assertEqual(self.sb.msys_copy.made["linked"], 3)
        with open(os.path.join(first, "usr", "bin", "bash.exe")) as f:
            self.assertEqual(f.read(), "bash")
        with open(os.path.join(first, "etc", "fstab")) as f:
            self.assertIn("usertemp", f.read())
        self.assertTrue(os.path.isfile(os.path.join(first, "usr", "share", "terminfo", "x")))
        self.assertTrue(os.path.isfile(os.path.join(first, "mingw64", "bin", "git.exe")))
        self.assertFalse(os.path.exists(os.path.join(first, "git-bash.exe")))
        self.sb.msys_copy.made = None
        self.assertEqual(self.sb.msys_copy(git, home=home), first)            # the same files: reused as it is
        self.assertIsNone(self.sb.msys_copy.made)
        with open(os.path.join(git, "usr", "bin", "ls.exe"), "w") as f:     # Git updated
            f.write("ls, newer")
        second = self.sb.msys_copy(git, home=home, hardlinks=False)
        self.assertTrue(second and second != first, self.sb.msys_copy.error)
        self.assertEqual(self.sb.msys_copy.made["copied"], 3)
        self.assertFalse(os.path.exists(first))                              # the old copy is gone...
        self.assertEqual(sorted(os.listdir(home)), [os.path.basename(second)])
        for part in (("usr", "bin", "msys-2.0.dll"), ("etc", "fstab"), ("usr", "share", "terminfo", "x"),
                     ("mingw64", "bin", "git.exe")):
            self.assertTrue(os.path.isfile(os.path.join(git, *part)), part)  # ...and Git's files are all there

    def test_removing_a_copy_never_follows_its_links(self):
        outside = tempfile.mkdtemp(prefix="nc-outside-")
        self.addCleanup(shutil.rmtree, outside, True)
        with open(os.path.join(outside, "keep.txt"), "w") as f:
            f.write("x")
        made = tempfile.mkdtemp(prefix="nc-copy-")
        os.makedirs(os.path.join(made, "usr", "bin"))
        with open(os.path.join(made, "usr", "bin", "a.exe"), "w") as f:
            f.write("x")
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(outside, os.path.join(made, "etc"))
            _winapi.CreateJunction(outside, os.path.join(made, "usr", "share"))
        else:
            os.symlink(outside, os.path.join(made, "etc"))
            os.symlink(outside, os.path.join(made, "usr", "share"))
        self.sb._remove(made)
        self.assertFalse(os.path.exists(made))
        self.assertTrue(os.path.isfile(os.path.join(outside, "keep.txt")))

    def test_windows_labels_the_project_once(self):
        if self.sb.kind() != "low-integrity":
            self.skipTest("Windows only")
        self.assertFalse(self.sb.is_low(self.root))
        self.assertTrue(self.sb.active(self.root, "read-only"))
        r = self.run_python("print('hi')")
        self.assertEqual(r.stdout.strip(), "hi", r.stderr)
        self.assertTrue(self.sb.is_low(self.root))
        self.assertEqual(self.sb.label_low(self.root), 0)          # done: nothing to do again
        # Labelled low, the project is writable to any low command: read-only mode cannot rely on the sandbox,
        # and the permissions know it (no command that may write runs without asking).
        self.assertFalse(self.sb.wrap(["x"], self.root, "read-only")[1])
        s = session.Session(self.root, mode="read-only")
        ag = agentmod.Agent(s)
        self.assertFalse(ag._sandbox_on())


def git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        raise AssertionError("git %s: %s" % (" ".join(args), r.stderr))
    return r.stdout


class ConnectTest(unittest.TestCase):
    """An API in one tap: the key is recognised in the clipboard, checked against the provider (a fake one here), and
    the provider's newest models land in the picker, with the key used for them."""

    @classmethod
    def setUpClass(cls):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                ok = self.headers.get("Authorization") == "Bearer AIza" + "k" * 35
                body = json.dumps({"data": [{"id": "models/" + n} for n in (
                    "gemini-2.0-flash", "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro",
                    "gemini-3-flash-preview", "text-embedding-004")]} if ok else
                    {"error": {"message": "API key not valid"}}).encode()
                self.send_response(200 if ok else 400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d/v1beta/openai" % cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        from unittest import mock
        from newal_code import connect
        self.connect = connect
        p = mock.patch.dict(connect.PROVIDERS["gemini"], {"base_url": self.base})
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(settings.save, {"keys": {}, "connected": {}, "model": "auto"})

    def test_keys_are_recognised(self):
        c = self.connect
        self.assertEqual(c.detect("my key: AIza" + "x" * 35 + " thanks"), ("gemini", "AIza" + "x" * 35))
        self.assertEqual(c.detect("sk-" + "a1" * 16)[0], "deepseek")
        self.assertEqual(c.detect("sk-or-v1-" + "ab" * 32)[0], "openrouter")
        self.assertEqual(c.detect("sk-ant-api03-" + "Z" * 60)[0], "anthropic")
        self.assertEqual(c.detect("sk-proj-" + "Q" * 60)[0], "openai")
        self.assertEqual(c.detect("gsk_" + "g" * 52)[0], "groq")
        self.assertEqual(c.detect("hello world"), (None, ""))
        self.assertEqual(c.pick("deepseek", ["deepseek-chat", "deepseek-reasoner"]), ["deepseek-chat", "deepseek-reasoner"])
        self.assertEqual(c.pick("openai", ["gpt-4.1", "gpt-5", "gpt-5.1", "gpt-5-mini", "o3"]), ["gpt-5.1", "gpt-5-mini"])

    def test_connect_checks_the_key_and_adds_the_newest_models(self):
        c = self.connect
        with self.assertRaises(c.ConnectError) as e:
            c.connect("gemini", "AIza" + "w" * 35)
        self.assertIn("refused", str(e.exception))
        with self.assertRaises(c.ConnectError):
            c.connect("gemini", "")
        r = c.connect("gemini", "copied: AIza" + "k" * 35)            # the key found inside what was copied
        self.assertEqual(r["default"], "gemini/gemini-3-flash-preview")
        self.assertEqual(r["models"], ["gemini/gemini-3-flash-preview", "gemini/gemini-2.5-pro"])
        self.assertEqual(settings.user()["model"], "gemini/gemini-3-flash-preview")
        reg = models.registry()
        spec = models.resolve("gemini/gemini-2.5-pro")
        self.assertIn("gemini/gemini-3-flash-preview", reg)
        self.assertEqual(spec["base_url"], self.base)
        self.assertEqual(spec["model"], "gemini-2.5-pro")
        self.assertEqual(models._key(spec), "AIza" + "k" * 35)
        self.assertEqual(models._key(models.resolve("gemini/any-other-model")), "AIza" + "k" * 35)
        ui = {p["id"]: p for p in c.listing_for_ui()}
        self.assertTrue(ui["gemini"]["connected"])
        self.assertFalse(ui["deepseek"]["connected"])
        if os.name != "nt":
            self.assertEqual(os.stat(settings.CONFIG).st_mode & 0o777, 0o600)
        c.disconnect("gemini")
        self.assertNotIn("gemini/gemini-2.5-pro", models.registry())
        self.assertEqual(settings.user()["model"], "auto")


class PhoneTest(unittest.TestCase):
    """The phone tool against a stand-in for NewAl Code Lite's phone server (the app's Java side is tested in the
    Android emulator: android-lite/tests/phone_test.py): the key goes with every call, looking never asks, acting asks
    unless full-auto, and read-only mode only looks."""

    def setUp(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from unittest import mock
        calls = self.calls = []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if self.headers.get("X-NewAl-Key") != "phone-key":
                    out, code = {"error": "wrong key"}, 401
                elif body.get("action") == "type":                   # no text field on the screen
                    calls.append(body)
                    out, code = {"error": "no text field to type into: tap one first"}, 400
                else:
                    calls.append(body)
                    out, code = {"ok": True, "text": {
                        "screen": "Settings\n[1] Network & internet (tap)\n[2] Battery (tap)",
                        "tap": "tapped [1] Network & internet"}.get(body["action"], "done")}, 200
                data = json.dumps(out).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        env = mock.patch.dict(os.environ, {"NEWAL_PHONE_URL": "http://127.0.0.1:%d" % self.srv.server_address[1],
                                           "NEWAL_PHONE_KEY": "phone-key"})
        env.start()
        self.addCleanup(env.stop)

    def test_permissions(self):
        d = permissions.decide
        self.assertEqual(d("auto-edit", "phone", "phone", {"action": "screen"}, "", {}).action, "allow")
        self.assertEqual(d("read-only", "phone", "phone", {"action": "apps"}, "", {}).action, "allow")
        self.assertEqual(d("auto-edit", "phone", "phone", {"action": "clipboard"}, "", {}).action, "allow")
        self.assertEqual(d("auto-edit", "phone", "phone", {"action": "clipboard", "text": "x"}, "", {}).action, "ask")
        self.assertEqual(d("auto-edit", "phone", "phone", {"action": "tap", "item": 3}, "", {}).action, "ask")
        self.assertEqual(d("ask", "phone", "phone", {"action": "open_app", "name": "Camera"}, "", {}).action, "ask")
        self.assertEqual(d("full-auto", "phone", "phone", {"action": "tap"}, "", {}).action, "allow")
        self.assertEqual(d("read-only", "phone", "phone", {"action": "tap"}, "", {}).action, "deny")
        self.assertEqual(d("auto-edit", "phone", "phone", {"action": "open_app"}, "",
                           {"allow": ["Phone(open_app)"]}).action, "allow")
        self.assertEqual(d("auto-edit", "phone", "phone", {"action": "sms"}, "",
                           {"allow": ["Phone(open_app)"]}).action, "ask")
        self.assertEqual(permissions.always_rule("phone", {"action": "torch", "on": True}), "Phone(torch)")

    def test_the_agent_looks_then_taps_after_asking(self):
        self.assertIn("phone", tools.default_set())
        asked = []
        _, ev, _, _, _ = run_agent([{"tools": [("phone", {"action": "screen"})]},
                            {"tools": [("phone", {"action": "tap", "item": 1})]}, "Opened Network & internet."],
                           approve=lambda r: asked.append(r) or "once")
        self.assertEqual([c["action"] for c in self.calls], ["screen", "tap"])
        self.assertEqual(self.calls[1]["item"], 1)
        self.assertEqual(len(asked), 1)                       # the tap asked; the look did not
        self.assertEqual(asked[0]["tool"], "phone")
        ends = [e for e in ev if e.get("type") == "tool_end" and e.get("name") == "phone"]
        self.assertIn("Network & internet", ends[0]["text"])
        self.assertTrue(ends[1]["ok"])

    def test_the_prompt_names_the_phone_and_small_models_get_few_tools(self):
        llm = FakeLLM([])
        self.addCleanup(llm.close)
        ag = agentmod.Agent(session.Session(make_project(CALC)), client=fake_client(llm))
        self.assertIn("Android phone", ag.system_prompt())
        self.assertIn("use the phone tool", ag.system_prompt())
        self.assertTrue({"phone", "grep", "web_fetch", "todo"} <= set(ag.tool_names()))
        # a phone's small local model (under 1.5 GB): the tools it uses well, and a shorter start to read
        tiny = models.Client({"id": "tiny", "name": "tiny", "provider": "local", "size": 500 * 1024 ** 2},
                             providers.OpenAICompat(llm.url), "tiny")
        names = agentmod.Agent(session.Session(make_project(CALC)), client=tiny).tool_names()
        self.assertEqual(set(names) - {"skill"}, {"read", "edit", "write", "bash", "phone"})
        # the phone's model as NewAl Code in Termux reaches it (through the app): small too
        served = models.Client({"id": "phone", "name": "phone", "provider": "openai", "small": True},
                               providers.OpenAICompat(llm.url), "phone")
        ag = agentmod.Agent(session.Session(make_project(CALC)), client=served)
        self.assertEqual(set(ag.tool_names()) - {"skill"}, {"read", "edit", "write", "bash", "phone"})
        self.assertEqual(served.default_reasoning(), "off")
        schema = json.dumps(tools.REGISTRY["phone"].schema())
        self.assertLess(len(schema), 2000)                   # read at the start of every thread on a phone
        self.assertIn("open_app", schema)

    def test_a_coding_task_typed_into_the_screen_is_pointed_to_the_file_tools(self):
        # The 3 GB phone's model, asked for "hello.py that prints 'hello from the phone'", typed the code into the
        # screen until it gave up: the failure now says which tools do files and programs.
        _, ev, _, _, root = run_agent([{"tools": [("phone", {"action": "type", "text": "print('hello')"})]},
                                       {"tools": [("write", {"path": "hello.py", "content": "print('hello')\n"})]},
                                       "Wrote hello.py."], mode="full-auto")
        typed = next(e for e in ev if e.get("type") == "tool_end" and e.get("name") == "phone")
        self.assertFalse(typed["ok"])
        self.assertIn("no text field", typed["text"])
        self.assertIn("write the file with the write tool and run it with bash", typed["text"])
        self.assertTrue(os.path.isfile(os.path.join(root, "hello.py")))

    def test_no_phone_no_tool_and_a_wrong_key_is_an_error(self):
        from unittest import mock
        from newal_code import phone
        with mock.patch.dict(os.environ, {"NEWAL_PHONE_KEY": "wrong"}):
            with self.assertRaises(phone.PhoneError) as e:
                phone.call("battery")
            self.assertIn("wrong key", str(e.exception))
        with mock.patch.dict(os.environ, {"NEWAL_PHONE_URL": "", "NEWAL_PHONE_KEY": ""}):
            with mock.patch.object(settings, "HOME", tempfile.mkdtemp()):
                self.assertFalse(phone.available())
                self.assertNotIn("phone", tools.default_set())
                s = session.Session(make_project(CALC))
                self.assertNotIn("phone", agentmod.Agent(s, client=fake_client(FakeLLM([]))).system_prompt())


class PhoneStorageTest(unittest.TestCase):
    """GGUF files the user already has on a phone: found in its shared storage (Download, Documents, a Telegram
    download; not among the photos), and listed as models with their files."""

    def test_ggufs_in_the_phones_storage_are_models(self):
        from unittest import mock
        from newal_code import service
        top = tempfile.mkdtemp()

        def put(rel, real=True):
            p = os.path.join(top, *rel.split("/"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            if real:
                RamTest().fake_gguf(p, size_mb=1)
            else:
                with open(p, "w") as f:
                    f.write("not a model")
            return p

        want = [put("Download/Qwen-Tiny-Q4.gguf"), put("Documents/models/old/Llama-Small.gguf"),
                put("Android/media/org.telegram.messenger/Telegram/Telegram Documents/From-A-Friend.gguf")]
        put("DCIM/Camera/odd.gguf")
        put("Download/mmproj-vision.gguf")
        put("Download/broken.gguf", real=False)
        with mock.patch.dict(os.environ, {"NEWAL_SHARED_STORAGE": top}):
            models._shared.update(at=0.0)
            found = models.shared_ggufs()
            self.assertEqual(sorted(os.path.basename(p) for p in found),
                             ["From-A-Friend.gguf", "Llama-Small.gguf", "Qwen-Tiny-Q4.gguf", "broken.gguf"])
            reg = models.registry()
            files = {os.path.abspath(s.get("file") or "") for s in reg.values()}
            for p in want:
                self.assertIn(os.path.abspath(p), files)
            self.assertNotIn(os.path.abspath(os.path.join(top, "Download", "broken.gguf")), files)
            spec = reg["qwen-tiny-q4"]
            self.assertEqual((spec["provider"], spec["downloaded"]), ("local", True))
            listing = service.Service().model_listing()
            self.assertEqual(listing["storage"], top)
            row = next(m for m in listing["models"] if m["id"] == "qwen-tiny-q4")
            self.assertEqual(row["file"], os.path.abspath(want[0]))
            # picked in Models (added by name): listed once, under that name
            before = settings.user().get("models") or {}
            settings.save({"models": dict(before, **{"qwen-tiny": {"provider": "local", "file": want[0]}})})
            try:
                reg = models.registry()
                self.assertIn("qwen-tiny", reg)
                self.assertNotIn("qwen-tiny-q4", reg)
                self.assertEqual(reg["qwen-tiny"]["file"], want[0])
            finally:
                settings.save({"models": before})
        models._shared.update(at=0.0)
        with mock.patch.dict(os.environ, {"NEWAL_SHARED_STORAGE": ""}):
            self.assertEqual(models.shared_ggufs(), [])


class SystemTest(unittest.TestCase):
    """The one permission (full access) and NewAl Code's place in the system: launchers on PATH, and on Windows the
    user's Path, Explorer's menu and a Windows Terminal profile (the Windows parts run in the Windows check)."""

    def test_full_access_is_one_grant(self):
        from newal_code import system
        self.addCleanup(settings.save, {"full_access": False, "mode": "auto-edit"})
        system.grant(True)
        self.assertTrue(system.full_access())
        self.assertEqual(settings.user()["mode"], "full-auto")
        root = make_project(CALC)
        from newal_code import service
        s = service.Service().create(root)
        self.assertEqual(s.mode, "full-auto")                       # a new thread works with full access
        from newal_code import sandbox
        self.assertFalse(sandbox.active(root, s.mode))
        system.grant(False)
        self.assertEqual((system.full_access(), settings.user()["mode"]), (False, "auto-edit"))
        r = subprocess.run([sys.executable, "-m", "newal_code", "access", "full"], capture_output=True, text=True,
                           cwd=os.path.dirname(HERE), timeout=60, env=dict(os.environ, PYTHONPATH=os.path.dirname(HERE)))
        self.assertIn("Full access: on", r.stdout, r.stderr)
        self.assertTrue(system.full_access())

    def test_launchers_and_windows_integration(self):
        from unittest import mock
        from newal_code import system
        top = tempfile.mkdtemp(prefix="nc-sys-")
        with mock.patch.object(system, "bin_dir", lambda: os.path.join(top, "bin")), \
                mock.patch.dict(os.environ, {"LOCALAPPDATA": top}):
            made = system.write_launchers()
            names = sorted(os.path.basename(p) for p in made)
            self.assertIn("newal", names)
            if os.name == "nt":
                self.assertIn("newal.cmd", names)
                r = subprocess.run([os.path.join(top, "bin", "newal.cmd"), "--version"], capture_output=True, text=True,
                                   timeout=120)
            else:
                r = subprocess.run([os.path.join(top, "bin", "newal"), "--version"], capture_output=True, text=True,
                                   timeout=120)
            self.assertIn("NewAl Code", r.stdout, r.stderr)                 # the launcher starts NewAl Code
            if os.name != "nt":
                return
            import winreg
            self.addCleanup(system.set_explorer, False)
            self.assertTrue(system.set_explorer(True))
            self.assertTrue(system.explorer_on())
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, system.MENU_KEYS[0] + r"\command") as k:
                self.assertIn("%V", winreg.QueryValueEx(k, "")[0])
            system.set_explorer(False)
            self.assertFalse(system.explorer_on())
            self.assertTrue(system.set_terminal(True))
            with open(system.terminal_fragment(), encoding="utf-8") as f:
                self.assertEqual(json.load(f)["profiles"][0]["name"], "NewAl Code")
            system.set_terminal(False)
            self.assertFalse(system.terminal_on())
            before = system._user_path()[0]
            self.addCleanup(lambda: system.set_path(False))
            system.set_path(True)
            self.assertTrue(system.on_path())
            system.set_path(False)
            self.assertFalse(system.on_path())
            self.assertEqual(system._user_path()[0].rstrip(";"), before.rstrip(";"))

    def test_powershell_tool_on_windows(self):
        if not tools.has_powershell_tool():
            self.assertNotIn("powershell", tools.default_set())
            self.skipTest("a powershell tool is offered on Windows beside Git Bash")
        self.assertIn("powershell", tools.default_set())
        root = make_project({"a.txt": "x"})
        s = session.Session(root, mode="full-auto")
        ctx = agentmod.ToolContext(agentmod.Agent(s))
        text, meta = tools.REGISTRY["powershell"].fn(ctx, "Get-ChildItem -Name; Write-Output 'مرحبا من PowerShell'")
        self.assertEqual(meta["exit"], 0, text)
        self.assertIn("a.txt", text)
        self.assertIn("مرحبا من PowerShell", text)                         # UTF-8 through the pipe

    def test_the_app_opens_a_folder_from_the_command_line(self):
        from newal_code import app
        d = tempfile.mkdtemp()
        self.assertEqual(app.folder_arg(["--browser", d]), os.path.abspath(d))
        self.assertEqual(app.folder_arg(["--browser", os.path.join(d, "nope")]), "")

    def test_github_login_from_this_computer(self):
        from unittest import mock
        from newal_code import github
        top = tempfile.mkdtemp(prefix="nc-gh-")
        tok = "gho_" + "k" * 36
        if os.name == "nt":
            with open(os.path.join(top, "gh.cmd"), "w") as f:
                f.write("@echo %s\n" % tok)
        else:
            path = os.path.join(top, "gh")
            with open(path, "w") as f:
                f.write("#!/bin/sh\necho %s\n" % tok)
            os.chmod(path, 0o755)
        env = {"PATH": top + os.pathsep + os.environ.get("PATH", ""), "GH_TOKEN": "", "GITHUB_TOKEN": "",
               "NEWAL_NO_GIT_CREDENTIAL": "1"}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(github.detect(interactive=False), (tok, "gh"))
        # git's credential helper (Git Credential Manager on Windows) when there is no GitHub CLI
        if os.name != "nt":
            home = tempfile.mkdtemp(prefix="nc-gh-home-")
            with open(os.path.join(home, ".gitconfig"), "w") as f:
                f.write("[credential]\n\thelper = \"!f() { echo username=x; echo password=%s; }; f\"\n" % tok)
            with mock.patch.dict(os.environ, {"PATH": "/usr/bin:/bin", "GH_TOKEN": "", "GITHUB_TOKEN": "",
                                              "HOME": home, "XDG_CONFIG_HOME": home,
                                              "NEWAL_NO_GIT_CREDENTIAL": ""}):
                if shutil.which("git"):
                    self.assertEqual(github.detect(interactive=False), (tok, "git credential manager"))


class GitHubTest(unittest.TestCase):
    """GitHub from NewAl Code against a fake API (and a local repository standing in for github.com): connect with a
    token, list and clone repositories, open a pull request without gh."""

    def setUp(self):
        from unittest import mock
        from fake_github import FakeGitHub
        self.gh = FakeGitHub()
        self.addCleanup(self.gh.close)
        good = "ghp_" + "t" * 36

        def user(m, b, h):
            if h.get("Authorization") != "Bearer " + good:
                return 401, {"message": "Bad credentials"}, {}
            return 200, {"login": "musab", "name": "Musab", "id": 7, "email": None}, {}
        self.gh.route("GET", r"/user", user)
        self.gh.reply("GET", r"/user/emails", [{"email": "m@example.com", "primary": True, "verified": True}])
        self.gh.reply("GET", r"/user/repos\?.*", [
            {"full_name": "musab/newal", "private": False, "description": "the assistant", "default_branch": "main",
             "clone_url": "x", "pushed_at": "2026-09-29T00:00:00Z"},
            {"full_name": "musab/notes", "private": True, "description": "", "default_branch": "main",
             "clone_url": "y", "pushed_at": "2026-09-28T00:00:00Z"}])
        self.gh.reply("GET", r"/repos/musab/newal", {"default_branch": "main"})
        self.gh.reply("GET", r"/repos/musab/newal/pulls\?.*", [])
        self.gh.reply("POST", r"/repos/musab/newal/pulls", {"html_url": "https://github.com/musab/newal/pull/5"})
        self.web = tempfile.mkdtemp(prefix="nc-ghweb-")
        self.addCleanup(shutil.rmtree, self.web, True)
        src = make_project({"README.md": "# newal\n"})
        git(src, "init", "-q", "-b", "main")
        git(src, "add", "-A")
        git(src, "-c", "user.name=T", "-c", "user.email=t@x", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "one")
        os.makedirs(os.path.join(self.web, "musab"))
        subprocess.run(["git", "clone", "-q", "--bare", src, os.path.join(self.web, "musab", "newal.git")], check=True)
        self.projects = tempfile.mkdtemp(prefix="nc-projects-")
        self.addCleanup(shutil.rmtree, self.projects, True)
        env = mock.patch.dict(os.environ, {"NEWAL_GITHUB_API": self.gh.url, "NEWAL_GITHUB_WEB": self.web,
                                           "GH_TOKEN": "", "GITHUB_TOKEN": ""})
        env.start()
        self.addCleanup(env.stop)
        settings.save({"projects_dir": self.projects})
        self.addCleanup(settings.save, {"keys": {}, "github": {}, "projects_dir": ""})
        self.good = good

    def test_issues_listed_then_one_becomes_the_task(self):
        from newal_code import github
        self.gh.reply("GET", r"/repos/musab/newal/issues\?.*", [
            {"number": 7, "title": "Crash on empty cart", "labels": [{"name": "bug"}], "comments": 1,
             "updated_at": "2026-09-01T10:00:00Z"},
            {"number": 8, "title": "A pull request", "pull_request": {}, "comments": 0, "updated_at": ""}])
        self.gh.reply("GET", r"/repos/musab/newal/issues/7", {"number": 7, "title": "Crash on empty cart",
                                                               "body": "total() divides by zero.", "comments": 1})
        self.gh.reply("GET", r"/repos/musab/newal/issues/7/comments.*", [{"user": {"login": "sara"},
                                                                          "body": "Seen on the phone too."}])
        root = make_project({"cart.py": "x = 1\n"})
        git(root, "init", "-q", "-b", "main")
        git(root, "remote", "add", "origin", "https://github.com/musab/newal.git")
        r = github.issues_command(root, "")
        self.assertIn("#7     Crash on empty cart  [bug]", r["reply"])
        self.assertNotIn("A pull request", r["reply"])                     # pull requests are not issues
        task = github.issues_command(root, "#7")["prompt"]
        for part in ("issue #7 of musab/newal: Crash on empty cart", "total() divides by zero.",
                     "- @sara: Seen on the phone too.", "Fixes #7"):
            self.assertIn(part, task)
        self.assertIn("not on GitHub", github.issues_command(make_project({"a": "b"}), "")["reply"])

    def test_connect_list_clone_and_pull_request(self):
        from newal_code import github
        with self.assertRaises(github.GitHubError):
            github.connect("ghp_" + "w" * 36)
        profile = github.connect("my token: " + self.good)
        self.assertEqual(profile["login"], "musab")
        self.assertEqual(profile["email"], "m@example.com")
        self.assertTrue(github.account()["connected"])
        self.assertEqual(github.token(), self.good)
        from newal_code import cloud
        self.assertEqual(cloud.token(), self.good)                  # cloud tasks use it too
        self.assertEqual([r["full_name"] for r in github.repos("note")], ["musab/notes"])
        root = github.clone("musab/newal")
        self.assertEqual(root, os.path.join(self.projects, "newal"))
        self.assertTrue(os.path.isfile(os.path.join(root, "README.md")))
        self.assertEqual(github.clone("musab/newal"), root)          # already there: used as it is
        git(root, "remote", "set-url", "origin", "https://github.com/musab/newal.git")
        git(root, "checkout", "-q", "-b", "newal/fix")
        self.assertEqual(github.pull_request(root, "Fix it"), "https://github.com/musab/newal/pull/5")
        post = self.gh.calls("POST", "/repos/musab/newal/pulls")[0]
        self.assertEqual(post["body"]["head"], "newal/fix")
        self.assertEqual(post["body"]["base"], "main")
        git(root, "checkout", "-q", "main")
        with self.assertRaises(github.GitHubError):
            github.pull_request(root, "on main")
        github.disconnect()
        self.assertFalse(github.account()["connected"])


CI_LOG = "\n".join("2026-09-30T10:00:%02d.1234567Z %s" % (i % 60, line) for i, line in enumerate([
    "##[group]Run actions/checkout@v4", "with:", "  repository: musab/newal", "##[endgroup]",
    "Syncing repository: musab/newal",
    "##[group]Run python -m pytest -q", "\x1b[36;1mpython -m pytest -q\x1b[0m", "shell: /usr/bin/bash -e {0}",
    "##[endgroup]",
    "F                                                                        [100%]",
    "=================================== FAILURES ===================================",
    "___________________________________ test_add ___________________________________", "",
    "    def test_add():", ">       assert add(2, 3) == 5", "E       assert -1 == 5", "E        +  where -1 = add(2, 3)",
    "", "test_calc.py:5: AssertionError",
    "=========================== short test summary info ============================",
    "FAILED test_calc.py::test_add - assert -1 == 5", "\x1b[31m1 failed\x1b[0m in 0.03s",
    "##[error]Process completed with exit code 1.", "Post job cleanup."]))


class CITest(unittest.TestCase):
    """/ci end to end: a real repository and a real push (to a bare repository standing in for github.com), GitHub
    Actions answered by a fake API (a run in progress, then failed, its job's log behind a redirect), and the agent
    fixing the code; the fix is committed and pushed, and the new commit's run is watched until it is green."""

    def setUp(self):
        from unittest import mock
        from fake_github import FakeGitHub
        self.top = tempfile.mkdtemp(prefix="nc-ci-")
        self.addCleanup(shutil.rmtree, self.top, True)
        self.gh = FakeGitHub()
        self.addCleanup(self.gh.close)
        env = mock.patch.dict(os.environ, {"NEWAL_GITHUB_API": self.gh.url, "GH_TOKEN": "ghp_" + "c" * 36,
                                           "GIT_CONFIG_NOSYSTEM": "1"})
        env.start()
        self.addCleanup(env.stop)
        # a path that ends in github.com/musab/newal.git: this project's origin "is on GitHub", and pushes work
        self.bare = os.path.join(self.top, "github.com", "musab", "newal.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", self.bare], check=True)
        self.root = os.path.join(self.top, "work")
        subprocess.run(["git", "init", "-q", "-b", "main", self.root], check=True)
        for name, text in CALC.items():
            with open(os.path.join(self.root, name), "w", newline="\n") as f:
                f.write(text)
        with open(os.path.join(self.root, "notes.txt"), "w") as f:
            f.write("mine\n")
        # (with / on Windows too: git takes it, and it reads as github.com/musab/newal.git as a real remote would)
        for cmd in (["add", "-A"], ["commit", "-q", "-m", "add()"],
                    ["remote", "add", "origin", self.bare.replace(os.sep, "/")], ["push", "-q", "-u", "origin", "main"]):
            subprocess.run(["git"] + cmd, cwd=self.root, check=True)
        self.broken = self.head()
        self.looks = {}

        def runs(m, b, h):
            sha = re.search(r"head_sha=(\w+)", m.group(0)).group(1)
            n = self.looks[sha] = self.looks.get(sha, 0) + 1
            if sha == self.broken or self.always_red:
                done = n > 1                                       # the first look: still running
                return 200, {"total_count": 1, "workflow_runs": [{
                    "id": 100 + len(self.looks), "name": "Tests", "head_sha": sha, "event": "push",
                    "status": "completed" if done else "in_progress", "conclusion": "failure" if done else None,
                    "html_url": "https://github.com/musab/newal/actions/runs/1"}]}, {}
            return 200, {"total_count": 1, "workflow_runs": [{"id": 200, "name": "Tests", "head_sha": sha,
                                                              "status": "completed", "conclusion": "success"}]}, {}
        self.always_red = False
        self.gh.route("GET", r"/repos/musab/newal/actions/runs\?head_sha=\w+&per_page=100", runs)
        self.gh.route("GET", r"/repos/musab/newal/actions/runs/\d+/jobs\?filter=latest&per_page=100",
                      lambda m, b, h: (200, {"jobs": [{"id": 555, "name": "pytest (ubuntu)", "status": "completed", "conclusion": "failure",
                                                       "html_url": "https://github.com/musab/newal/job/555",
                                                       "steps": [{"name": "Set up job", "conclusion": "success"},
                                                                 {"name": "Run tests", "conclusion": "failure"}]}]}, {}))
        self.gh.route("GET", r"/repos/musab/newal/actions/jobs/555/logs",
                      lambda m, b, h: (302, b"", {"Location": self.gh.url + "/blob/555.txt?sig=x"}))
        self.gh.route("GET", r"/blob/555\.txt\?sig=x", lambda m, b, h: (200, CI_LOG.encode(), {}))

    def head(self):
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.root, capture_output=True,
                              text=True).stdout.strip()

    def remote_log(self):
        return subprocess.run(["git", "--git-dir", self.bare, "log", "--format=%s", "main"], capture_output=True,
                              text=True).stdout.split("\n")

    def agent(self, script, mode="auto-edit"):
        llm = FakeLLM(script)
        self.addCleanup(llm.close)
        s = session.Session(self.root, mode=mode)
        events = []
        return agentmod.Agent(s, emit=events.append, approve=lambda r: "once", client=fake_client(llm)), events, llm

    def test_the_log_excerpt_is_the_failed_step(self):
        from newal_code import ci
        text = ci.excerpt(CI_LOG)
        self.assertTrue(text.startswith("$ python -m pytest -q\n"), text)
        for part in ("E       assert -1 == 5", "FAILED test_calc.py::test_add", "1 failed in 0.03s",
                     "Error: Process completed with exit code 1."):
            self.assertIn(part, text)
        for gone in ("Syncing repository", "Post job cleanup", "2026-09-30T", "\x1b[", "shell: /usr/bin/bash"):
            self.assertNotIn(gone, text)
        short = ci.excerpt(CI_LOG, limit=300)                   # a long log: its end, where the errors are
        self.assertIn("lines above", short)
        self.assertIn("FAILED test_calc.py::test_add", short)
        self.assertIn("warning: disk", ci.excerpt("2026-01-01T00:00:00Z a\n2026-01-01T00:00:01Z warning: disk\n"))

    def test_a_red_run_is_fixed_committed_pushed_and_watched_until_green(self):
        from newal_code import ci
        with open(os.path.join(self.root, "notes.txt"), "a") as f:
            f.write("not committed\n")                          # the user's own work: stays out of the fix
        with open(os.path.join(self.root, "draft.txt"), "w") as f:
            f.write("draft\n")
        ag, events, llm = self.agent([{"tools": [("edit", {"path": "calc.py", "old": "return a - b",
                                                            "new": "return a + b"})]},
                                      "Fixed add(): it subtracted instead of adding."])
        said = []
        ok, text = ci.heal(ag, say=lambda st, t: said.append((st, t)), poll=0.05)
        self.assertTrue(ok, text)
        fixed = self.head()
        self.assertEqual(text, "CI is green for %s: Tests ✓" % fixed[:7])
        prompt = next(m["content"] for m in llm.requests[0]["messages"] if m["role"] == "user")
        for part in ("GitHub Actions failed on musab/newal (branch main, commit %s)" % self.broken[:7],
                     "## Tests / pytest (ubuntu) (failed step: Run tests)", "$ python -m pytest -q",
                     "E       assert -1 == 5", "never skip, disable or delete a test"):
            self.assertIn(part, prompt)
        self.assertEqual(self.remote_log()[:2], ["Fix CI: Tests / pytest (ubuntu)", "add()"])       # pushed
        body = subprocess.run(["git", "log", "-1", "--format=%b"], cwd=self.root, capture_output=True,
                              text=True).stdout
        self.assertIn("Fixed add(): it subtracted instead of adding.", body)
        files = subprocess.run(["git", "show", "--name-only", "--format=", "HEAD"], cwd=self.root,
                               capture_output=True, text=True).stdout.split()
        self.assertEqual(files, ["calc.py"])
        status = subprocess.run(["git", "status", "--porcelain"], cwd=self.root, capture_output=True,
                                text=True).stdout
        self.assertIn(" M notes.txt", status)
        self.assertIn("?? draft.txt", status)
        states = [st for st, _ in said]
        self.assertEqual(states[:2], ["watching", "fixing"])       # the end of a watch is said once, with what follows
        self.assertIn("Tests … in progress", said[0][1])
        self.assertIn("Tests ✗", said[1][1])
        self.assertIn("pushed", states)
        self.assertEqual(self.gh.calls("GET", "/blob/555.txt")[0]["auth"], None)     # the log's storage: no token
        self.assertTrue(self.gh.calls("GET", "/repos/musab/newal/actions/jobs/555/logs")[0]["auth"])

    def test_it_gives_up_after_its_tries_and_pushes_nothing_half_done(self):
        from newal_code import ci
        self.always_red = True
        ag, _, _ = self.agent([{"tools": [("edit", {"path": "calc.py", "old": "return a - b",
                                                     "new": "return a - b  # checked"})]}, "Added a note."])
        ag.cfg["verify"] = False
        ok, text = ci.heal(ag, tries=1, say=lambda st, t: None, poll=0.01)
        self.assertFalse(ok)
        self.assertIn("Still failing after 1 fix: Tests / pytest (ubuntu)", text)
        self.assertEqual(self.remote_log()[0], "Fix CI: Tests / pytest (ubuntu)")
        # a fix the circuit breaker stopped is not committed
        ag, _, _ = self.agent([{"tools": [("bash", {"command": "false"})]}] * 3)
        before = self.head()
        ok, text = ci.heal(ag, tries=1, say=lambda st, t: None, poll=0.01)
        self.assertFalse(ok)
        self.assertIn("The fix did not finish: Stopped: bash failed 3 times the same way", text)
        self.assertEqual(self.head(), before)

    def test_no_runs_and_read_only(self):
        from newal_code import ci
        self.gh.reply("GET", r"/repos/musab/newal/actions/runs\?head_sha=\w+&per_page=100",
                      {"total_count": 0, "workflow_runs": []})
        ag, _, _ = self.agent([])
        ok, text = ci.heal(ag, say=lambda st, t: None, poll=0.01, appear=0.05)
        self.assertFalse(ok)
        self.assertIn("No GitHub Actions run started for %s" % self.broken[:7], text)
        self.gh.routes = self.gh.routes[1:]                       # the red run again
        ag, _, llm = self.agent([], mode="read-only")
        ok, text = ci.heal(ag, say=lambda st, t: None, poll=0.01)
        self.assertFalse(ok)
        self.assertIn("E       assert -1 == 5", text)
        self.assertIn("(read-only mode: nothing is fixed.)", text)
        self.assertEqual(llm.requests, [])
        # read-only pushes nothing: a commit not on GitHub yet is not pushed, the branch as GitHub has it is watched
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "local only"], cwd=self.root, check=True)
        ok, text = ci.heal(ag, say=lambda st, t: None, poll=0.01)
        self.assertEqual(self.remote_log()[0], "add()")
        self.assertIn("CI for %s" % self.broken[:7], text)

    def test_the_command_runs_in_the_background_and_says_how_it_ended(self):
        from unittest import mock
        from newal_code import ci, service
        svc = service.Service()
        s = svc.create(self.root, mode="auto-edit")
        llm = FakeLLM([{"tools": [("edit", {"path": "calc.py", "old": "return a - b", "new": "return a + b"})]},
                       "Fixed add()."])
        self.addCleanup(llm.close)
        svc.agent(s.id).client = fake_client(llm)
        q = svc.subscribe()
        with mock.patch.object(ci, "POLL", 0.05):
            r = svc.send(s.id, "/ci")
            self.assertTrue(r["started"])
            self.assertEqual(svc.send(s.id, "/ci")["reply"], "busy: this thread is still working (interrupt it first)")
            svc.threads[s.id].join(60)
        events = []
        while not q.empty():
            events.append(q.get())
        ci_events = [e for e in events if e.get("type") == "ci"]
        self.assertEqual(ci_events[0]["state"], "start")
        self.assertEqual(ci_events[-1]["state"], "end")
        self.assertTrue(ci_events[-1]["ok"], ci_events[-1]["text"])
        self.assertIn("CI is green for", ci_events[-1]["text"])
        self.assertTrue(any(e.get("type") == "turn_end" for e in events))           # the fix was an agent turn
        self.assertEqual(svc.command(s, "/help")["reply"].count("/ci [tries]"), 1)
        not_git = service.Service().create(make_project({}))
        self.assertIn("not a git repository", svc.command(not_git, "/ci")["reply"])

    def test_the_github_tool(self):
        self.gh.reply("GET", r"/repos/musab/newal/actions/runs\?branch=main&per_page=15", {"workflow_runs": [
            {"id": 101, "name": "Tests", "status": "completed", "conclusion": "failure", "head_sha": self.broken,
             "created_at": "2026-09-30T10:00:00Z"}]})
        self.gh.reply("GET", r"/repos/musab/newal/actions/runs/101", {"name": "Tests", "status": "completed",
                                                                      "conclusion": "failure", "head_sha": self.broken})
        self.gh.reply("POST", r"/repos/musab/newal/actions/runs/101/rerun-failed-jobs", None, status=201)
        self.gh.reply("GET", r"/repos/musab/newal/pulls\?state=open&per_page=20", [
            {"number": 9, "title": "Faster sums", "head": {"ref": "sums"}, "base": {"ref": "main"},
             "user": {"login": "sara"}, "updated_at": "2026-09-29T00:00:00Z"}])
        self.gh.reply("POST", r"/repos/musab/newal/issues/9/comments", {"html_url": "https://github.com/c/1"})
        ag, _, _ = self.agent([])
        c = agentmod.ToolContext(ag)
        text, _ = tools.call(c, "github", {"action": "runs"})
        self.assertIn("101  Tests ✗  %s (this commit)" % self.broken[:7], text)
        text, _ = tools.call(c, "github", {"action": "run", "number": "101"})
        self.assertIn("pytest (ubuntu): ✗", text)
        self.assertIn("failed step: Run tests", text)
        text, meta = tools.call(c, "github", {"action": "logs", "number": "101"})
        self.assertIn("E       assert -1 == 5", text)
        self.assertEqual(meta, {"failed_jobs": 1})
        text, _ = tools.call(c, "github", {"action": "rerun", "number": "#101"})
        self.assertIn("Run 101: its failed jobs run again", text)
        text, _ = tools.call(c, "github", {"action": "prs"})
        self.assertIn("#9 Faster sums (sums -> main, @sara", text)
        text, _ = tools.call(c, "github", {"action": "comment", "number": "9", "body": "Looks good."})
        self.assertEqual(self.gh.calls("POST", "/repos/musab/newal/issues/9/comments")[0]["body"],
                         {"body": "Looks good."})
        with self.assertRaises(tools.ToolError):
            tools.call(c, "github", {"action": "logs"})               # a run id is needed
        self.assertIn("github", tools.default_set(root=self.root))    # offered in a GitHub project...
        self.assertNotIn("github", tools.default_set(root=make_project({})))     # ...and only there
        from newal_code import repair
        self.assertEqual(repair.normalize("github_cli", {"action": "runs"}, ["github", "bash"])[0], "github")
        d = permissions.decide
        self.assertEqual(d("ask", "github", "github", {"action": "logs"}, self.root, {}).action, permissions.ALLOW)
        self.assertEqual(d("auto-edit", "github", "github", {"action": "rerun"}, self.root, {}).action,
                         permissions.ASK)
        self.assertEqual(d("read-only", "github", "github", {"action": "comment"}, self.root, {}).action,
                         permissions.DENY)
        self.assertEqual(d("full-auto", "github", "github", {"action": "create_pr"}, self.root, {}).action,
                         permissions.ALLOW)
        self.assertEqual(d("ask", "github", "github", {"action": "rerun"}, self.root,
                           {"allow": ["GitHub(rerun)"]}).action, permissions.ALLOW)
        self.assertEqual(permissions.always_rule("github", {"action": "comment"}), "GitHub(comment)")


class MiniGitTest(unittest.TestCase):
    """The phone's git (dulwich behind git's commands) against the computer's git: the same steps, the same output,
    the same commits."""

    STEPS = [["init", "-q", "-b", "main", "."], "W a.py print(1)", "W sub/b.txt x", ["status", "--short"], ["status"],
             ["add", "-A"], ["status", "--short"], ["commit", "-q", "-m", "first commit"],
             ["rev-parse", "--abbrev-ref", "HEAD"], ["log", "--oneline"], "W a.py print(2)", "R sub/b.txt",
             "W c.txt new", ["status", "--short"], ["diff"], ["add", "."], ["diff", "--cached", "--stat"],
             ["commit", "-q", "-m", "second"], ["checkout", "-q", "-b", "feature"], ["branch"], "W c.txt more",
             ["commit", "-q", "-am", "on feature"], ["log", "--oneline", "-2"], ["remote", "add", "origin", "BARE"],
             ["push", "-q", "-u", "origin", "HEAD"], ["status"], ["diff", "main..feature", "--name-only"],
             ["show", "--stat", "HEAD"], ["log", "--format=%h %an %s", "-1"], ["rev-parse", "--short", "HEAD~1"]]

    def run_steps(self, git_cmd):
        top = tempfile.mkdtemp(prefix="nc-git-")
        self.addCleanup(shutil.rmtree, top, True)
        work = os.path.join(top, "w")
        os.makedirs(work)
        bare = os.path.join(top, "bare.git")
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
        env = dict(os.environ, HOME=top, GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@x", GIT_COMMITTER_NAME="T",
                   GIT_COMMITTER_EMAIL="t@x", GIT_AUTHOR_DATE="2026-09-29T10:00:00Z",
                   GIT_COMMITTER_DATE="2026-09-29T10:00:00Z", GIT_CONFIG_NOSYSTEM="1", LANG="C",
                   PYTHONPATH=os.pathsep.join([os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]
                                              + [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]))
        out = []
        for step in self.STEPS:
            if isinstance(step, str):
                op, path, *text = step.split(" ")
                full = os.path.join(work, path)
                if op == "W":
                    os.makedirs(os.path.dirname(full), exist_ok=True)
                    with open(full, "a" if os.path.exists(full) and path == "c.txt" and text == ["more"] else "w",
                              newline="\n") as f:
                        f.write(" ".join(text) + "\n")
                else:
                    os.remove(full)
                continue
            args = [a.replace("BARE", bare) for a in step]
            r = subprocess.run(git_cmd + args, cwd=work, env=env, capture_output=True, text=True, timeout=120)
            out.append("== %s\n%s%s[exit %d]" % (" ".join(step), r.stdout, r.stderr.replace(bare, "BARE"),
                                                   r.returncode))
        return re.sub(r"\b[0-9a-f]{7,40}\b", "H", "\n".join(out))

    def test_the_same_as_git(self):
        try:
            import dulwich  # noqa: F401
        except ImportError:
            self.skipTest("dulwich is not installed")
        if not shutil.which("git"):
            self.skipTest("git is not installed")
        real = self.run_steps(["git"])
        mine = self.run_steps([sys.executable, "-m", "newal_code.minigit"])
        self.assertEqual(mine, real)

    def test_github_token_goes_only_to_github(self):
        try:
            import dulwich.client as dc
        except ImportError:
            self.skipTest("dulwich is not installed")
        from unittest import mock
        from newal_code import minigit
        seen = []
        with mock.patch.object(dc, "get_transport_and_path", lambda loc, *a, **kw: seen.append((loc, kw)) or kw):
            import dulwich.porcelain as dp
            with mock.patch.object(dp, "get_transport_and_path", dc.get_transport_and_path):
                minigit._auth("ghp_secret")
                dc.get_transport_and_path("https://github.com/musab/newal.git")
                dc.get_transport_and_path("https://gitlab.com/musab/newal.git")
                dp.get_transport_and_path("https://github.com/musab/other")
        self.assertEqual(seen[0][1], {"username": "x-access-token", "password": "ghp_secret"})
        self.assertEqual(seen[1][1], {})
        self.assertEqual(seen[2][1]["password"], "ghp_secret")


class CloudTest(unittest.TestCase):
    """Cloud tasks end to end without GitHub: a bare repository stands for GitHub's git, a fake API for its REST
    API, and the runner's part runs here with a scripted model."""

    def setUp(self):
        from fake_github import FakeGitHub
        from unittest import mock
        self.base = tempfile.mkdtemp(prefix="nc-cloud-")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.origin = os.path.join(self.base, "origin.git")
        git(self.base, "init", "-q", "--bare", self.origin)
        self.work = os.path.join(self.base, "work")
        os.makedirs(self.work)
        for rel, text in CALC.items():
            with open(os.path.join(self.work, rel), "w", encoding="utf-8") as f:
                f.write(text)
        git(self.work, "init", "-q", "-b", "main")
        git(self.work, "add", "-A")
        git(self.work, "commit", "-qm", "init")
        git(self.work, "remote", "add", "origin", self.origin)
        git(self.work, "push", "-q", "origin", "main")
        self.gh = FakeGitHub()
        self.addCleanup(self.gh.close)
        patcher = mock.patch.dict(os.environ, {"NEWAL_GITHUB_API": self.gh.url, "GH_TOKEN": "test-token"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_task_round_trip(self):
        from newal_code import cloud
        with open(os.path.join(self.work, "notes.txt"), "w") as f:
            f.write("not committed\n")                      # stays out of the task (no --with-changes)
        rec = cloud.submit(self.work, "Fix add() so the tests pass", model="scripted", repo="me/proj")
        self.assertTrue(rec["branch"].startswith("newal-cloud/"))
        self.assertEqual(git(self.work, "status", "--porcelain"), "?? notes.txt\n")     # the checkout untouched
        files = git(self.origin, "ls-tree", "-r", "--name-only", rec["branch"]).split()
        self.assertEqual(sorted(files), sorted(["calc.py", "test_calc.py", cloud.TASK_FILE, cloud.WORKFLOW_FILE]))
        task = json.loads(git(self.origin, "show", "%s:%s" % (rec["branch"], cloud.TASK_FILE)))
        self.assertEqual((task["task"], task["model"], task["base_branch"]), ("Fix add() so the tests pass",
                                                                             "scripted", "main"))
        self.assertIn("newal-cloud/**", git(self.origin, "show", "%s:%s" % (rec["branch"], cloud.WORKFLOW_FILE)))

        # The runner: a checkout of the task branch, the agent with a scripted model, the result pushed back.
        runner = os.path.join(self.base, "runner")
        git(self.base, "clone", "-q", "-b", rec["branch"], self.origin, runner)
        llm = FakeLLM([{"tools": [("edit", {"path": "calc.py", "old": "return a - b", "new": "return a + b"})]},
                       "Fixed add()."])
        settings.save({"models": {"scripted": {"base_url": llm.url, "model": "fake"}}})
        self.addCleanup(settings.save, {"models": {}})
        out = os.path.join(self.base, "result")
        result = cloud.run_here(runner, out)
        llm.close()
        self.assertEqual((result["answer"], result["files"], result["pushed"], result["error"]),
                         ("Fixed add().", ["calc.py"], True, ""))
        with open(os.path.join(out, "changes.patch"), encoding="utf-8") as f:
            patch = f.read()
        self.assertIn("+    return a + b", patch)
        self.assertNotIn("task.json", patch)
        self.assertNotIn("newal-code-cloud.yml", patch)
        after = git(self.origin, "ls-tree", "-r", "--name-only", rec["branch"]).split()
        self.assertNotIn(cloud.TASK_FILE, after)
        self.assertIn("return a + b", git(self.origin, "show", rec["branch"] + ":calc.py"))

        # GitHub's API: the run, its artifact (behind a link that refuses the token), a pull request, the cleanup.
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for name in os.listdir(out):
                z.write(os.path.join(out, name), name)
        run = {"id": 77, "head_sha": rec["commit"], "path": cloud.WORKFLOW_FILE, "status": "completed",
               "conclusion": "success", "html_url": "https://github.com/me/proj/actions/runs/77"}
        self.gh.reply("GET", r"/repos/me/proj/actions/runs\?.*", {"workflow_runs": [dict(run, head_sha="other"), run]})
        self.gh.reply("GET", r"/repos/me/proj/actions/runs/77/artifacts", {"artifacts": [{"id": 5, "name": cloud.ARTIFACT}]})
        self.gh.route("GET", r"/repos/me/proj/actions/artifacts/5/zip",
                      lambda m, b, h: (302, None, {"Location": self.gh.url + "/storage/5"}))
        self.gh.route("GET", r"/storage/5", lambda m, b, h: (400, {"message": "token not accepted"}, {})
                      if h.get("Authorization") else (200, buf.getvalue(), {"Content-Type": "application/zip"}))
        self.gh.reply("POST", r"/repos/me/proj/pulls", {"html_url": "https://github.com/me/proj/pull/9"}, 201)
        self.gh.reply("DELETE", r"/repos/me/proj/git/refs/heads/newal-cloud/.+", None, 204)

        rec = cloud.status(rec["id"])
        self.assertEqual((rec["state"], rec["fetched"], rec["answer"]), ("done", True, "Fixed add()."))
        ch = cloud.changes(rec["id"])
        self.assertEqual([(c["path"], c["status"], c["plus"], c["minus"]) for c in ch], [("calc.py", "modified", 1, 1)])
        cloud.apply(rec["id"], self.work)
        with open(os.path.join(self.work, "calc.py")) as f:
            self.assertIn("return a + b", f.read())
        rec = cloud.pull_request(rec["id"])
        self.assertEqual(rec["pr"], "https://github.com/me/proj/pull/9")
        body = self.gh.calls("POST")[0]["body"]
        self.assertEqual((body["head"], body["base"]), (rec["branch"], "main"))
        cloud.delete(rec["id"])
        self.assertEqual(len(self.gh.calls("DELETE", "/repos/me/proj/git/refs/heads/newal-cloud/")), 1)
        self.assertNotIn(rec["id"], [r["id"] for r in cloud.listing()])
        api_calls = [r for r in self.gh.requests if not r["path"].startswith("/storage/")]
        self.assertTrue(all(r["auth"] == "Bearer test-token" for r in api_calls))

    def test_with_changes_and_a_workflow_already_there(self):
        from newal_code import cloud
        path = cloud.setup(self.work)
        self.assertTrue(path.endswith(os.path.join(".github", "workflows", "newal-code-cloud.yml")))
        git(self.work, "add", "-A")
        git(self.work, "commit", "-qm", "cloud workflow")
        with open(os.path.join(self.work, "calc.py"), "a") as f:
            f.write("# local edit\n")
        rec = cloud.submit(self.work, "Tidy up", with_changes=True, push_result=False, repo="me/proj")
        self.assertIn("# local edit", git(self.origin, "show", rec["branch"] + ":calc.py"))
        changed = git(self.origin, "diff", "--name-only", rec["base"], rec["branch"]).split()
        self.assertEqual(sorted(changed), sorted(["calc.py", cloud.TASK_FILE]))       # the workflow was there
        self.assertFalse(json.loads(git(self.origin, "show", rec["branch"] + ":" + cloud.TASK_FILE))["push"])
        self.gh.reply("GET", r"/repos/me/proj/actions/runs\?.*", {"workflow_runs": []})
        self.assertEqual(cloud.status(rec["id"])["state"], "queued")                  # no run yet
        self.assertNotIn("__", cloud.workflow(["dev"]))
        self.assertIn('"newal-cloud/**", "dev"', cloud.workflow(["dev"]))


class GitHubAppTest(unittest.TestCase):
    """@newal in issues and pull requests, with simulated GitHub events: a bare repository for git, a fake API, a
    scripted model; what the workflow runs on GitHub's runner runs here."""

    def setUp(self):
        from fake_github import FakeGitHub
        from unittest import mock
        self.base = tempfile.mkdtemp(prefix="nc-gh-")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.origin = os.path.join(self.base, "origin.git")
        git(self.base, "init", "-q", "--bare", self.origin)
        seed = os.path.join(self.base, "seed")
        os.makedirs(seed)
        for rel, text in CALC.items():
            with open(os.path.join(seed, rel), "w", encoding="utf-8") as f:
                f.write(text)
        git(seed, "init", "-q", "-b", "main")
        git(seed, "add", "-A")
        git(seed, "commit", "-qm", "init")
        git(seed, "checkout", "-q", "-b", "feature")
        with open(os.path.join(seed, "calc.py"), "a", encoding="utf-8") as f:
            f.write("\n\ndef mul(a, b):\n    return a + b\n")
        git(seed, "commit", "-qam", "mul")
        git(seed, "push", "-q", self.origin, "main", "feature")
        self.runner = os.path.join(self.base, "runner")                 # actions/checkout: the default branch
        git(self.base, "clone", "-q", self.origin, self.runner)
        self.gh = FakeGitHub()
        self.addCleanup(self.gh.close)
        patcher = mock.patch.dict(os.environ, {"NEWAL_GITHUB_API": self.gh.url, "GH_TOKEN": "test-token"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.gh.reply("POST", r"/repos/me/proj/issues/\d+/comments", {"id": 11})
        self.gh.route("PATCH", r"/repos/me/proj/issues/comments/11", lambda m, b, h: (200, {"id": 11}, {}))
        self.gh.reply("GET", r"/repos/me/proj/pulls/2", {
            "number": 2, "title": "Add mul()", "body": "Multiplication.", "head": {"ref": "feature", "repo": {
                "full_name": "me/proj"}}, "base": {"ref": "main"}})

    def model(self, script):
        llm = FakeLLM(script)
        self.addCleanup(llm.close)
        settings.save({"models": {"scripted": {"base_url": llm.url, "model": "fake"}}})
        self.addCleanup(settings.save, {"models": {}})
        return llm

    def event(self, body, assoc="OWNER", pr=False):
        issue = {"number": 2 if pr else 1, "title": "add() subtracts", "body": "add(2, 3) gives -1.",
                 "user": {"login": "someone"}}
        if pr:
            issue["pull_request"] = {"url": "..."}
        return {"issue": issue, "comment": {"body": body, "author_association": assoc, "user": {"login": "musab"}},
                "repository": {"full_name": "me/proj", "default_branch": "main"}}

    def final_comment(self):
        return self.gh.calls("PATCH", "/repos/me/proj/issues/comments/11")[-1]["body"]["body"]

    def test_issue_mention_makes_a_pull_request(self):
        from newal_code import github_app
        self.gh.reply("POST", r"/repos/me/proj/pulls", {"html_url": "https://github.com/me/proj/pull/5"}, 201)
        self.model([{"tools": [("edit", {"path": "calc.py", "old": "return a - b", "new": "return a + b"})]},
                    "Fixed add()."])
        out = github_app.handle("issue_comment", self.event("@newal please fix this"), "me/proj", self.runner,
                                model="scripted")
        self.assertEqual((out["changed"], out["pr"]), (True, "https://github.com/me/proj/pull/5"))
        self.assertIn("return a + b", git(self.origin, "show", out["branch"] + ":calc.py"))
        self.assertTrue(out["branch"].startswith("newal/issue-1-"))
        pr = self.gh.calls("POST", "/repos/me/proj/pulls")[0]["body"]
        self.assertEqual((pr["head"], pr["base"]), (out["branch"], "main"))
        self.assertIn("Closes #1", pr["body"])
        self.assertIn("working on it", self.gh.calls("POST", "/repos/me/proj/issues/1/comments")[0]["body"]["body"])
        self.assertIn("Fixed add().", self.final_comment())
        self.assertIn("pull/5", self.final_comment())

    def test_review_comments_on_the_changed_lines(self):
        from newal_code import github_app
        self.gh.reply("POST", r"/repos/me/proj/pulls/2/reviews", {"id": 1})
        self.model(["[P1] calc.py:6 - mul() adds instead of multiplying - return a * b\n"
                    "[P3] calc.py:40 - there is no line 40 in the change - x"])
        out = github_app.handle("issue_comment", self.event("@newal review", assoc="MEMBER", pr=True), "me/proj",
                                self.runner, model="scripted")
        self.assertEqual(len(out["findings"]), 2)
        rv = self.gh.calls("POST", "/repos/me/proj/pulls/2/reviews")[0]["body"]
        self.assertEqual(rv["event"], "COMMENT")
        self.assertEqual([(c["path"], c["line"], c["side"]) for c in rv["comments"]], [("calc.py", 6, "RIGHT")])
        self.assertIn("calc.py:40", rv["body"])             # not a line of the change: in the review's text
        self.assertIn("2 findings", self.final_comment())

    def test_pull_request_change_is_pushed_to_its_branch(self):
        from newal_code import github_app
        self.model([{"tools": [("edit", {"path": "calc.py", "old": "    return a + b\n", "new": "    return a * b\n"})]},
                    "mul() multiplies now."])
        out = github_app.handle("issue_comment", self.event("@newal fix mul()", pr=True), "me/proj", self.runner,
                                model="scripted")
        self.assertEqual((out["changed"], out["branch"]), (True, "feature"))
        self.assertIn("return a * b", git(self.origin, "show", "feature:calc.py"))
        self.assertIn("Pushed to `feature`", self.final_comment())

    def test_only_trusted_people_and_only_mentions(self):
        from newal_code import github_app
        self.assertIn("only", github_app.handle("issue_comment", self.event("@newal do it", assoc="NONE"),
                                                "me/proj", self.runner)["skipped"])
        self.assertIn("no @newal", github_app.handle("issue_comment", self.event("looks good"), "me/proj",
                                                     self.runner)["skipped"])
        fork = {"pull_request": {"number": 3, "head": {"repo": {"full_name": "stranger/proj"}}},
                "repository": {"full_name": "me/proj"}}
        self.assertIn("only", github_app.handle("pull_request", fork, "me/proj", self.runner)["skipped"])
        self.assertEqual(self.gh.requests, [])
        self.assertIn("contains(github.event.comment.body, '@newal')", github_app.workflow())
        self.assertNotIn("__", github_app.workflow())


class ExtensionsTest(unittest.TestCase):
    def test_instructions_skills_commands_agents(self):
        root = make_project({
            "AGENTS.md": "Use tabs.\n@docs/style.md\n",
            "docs/style.md": "Style: short functions.\n",
            "sub/CLAUDE.md": "Sub rules.\n",
            ".claude/skills/pdf/SKILL.md": "---\nname: pdf-tools\ndescription: Work with PDF files\n---\nUse pypdf.\n",
            ".claude/commands/fix-issue.md": "---\ndescription: Fix an issue\n---\nFix issue $ARGUMENTS in @docs/style.md\n",
            ".claude/commands/git/sync.md": "Sync with !`echo main`\n",
            ".claude/agents/tester.md": "---\nname: tester\ndescription: Runs tests\ntools: Read, Bash, Grep\n"
                                        "model: fast\n---\nRun the tests.\n",
        })
        os.makedirs(os.path.join(root, ".git"))
        text = extensions.instructions(os.path.join(root, "sub"))
        self.assertIn("Use tabs.", text)
        self.assertIn("Style: short functions.", text)
        self.assertIn("Sub rules.", text)
        sk = extensions.skills(root)
        self.assertIn("pdf-tools", sk)
        self.assertIn("Work with PDF files", extensions.skills_index(sk))
        cmds = extensions.custom_commands(root)
        self.assertIn("fix-issue", cmds)
        self.assertIn("git:sync", cmds)
        out = extensions.expand_command(cmds["fix-issue"], "123", root)
        self.assertIn("Fix issue 123", out)
        self.assertIn("Style: short functions.", out)
        self.assertIn("Sync with main", extensions.expand_command(cmds["git:sync"], "", root))
        ag = extensions.agents(root)
        self.assertEqual(ag["tester"]["tools"], ["bash", "grep", "read"])
        self.assertEqual(ag["tester"]["model"], "fast")
        self.assertIn("explore", ag)


class PluginsTest(unittest.TestCase):
    def test_plugin_brings_commands_agents_skills_hooks_and_mcp(self):
        from newal_code import plugins
        src = make_project({
            ".claude-plugin/plugin.json": json.dumps({"name": "lint-kit", "description": "Lint helpers",
                                                      "version": "1.0.0"}),
            "commands/lint.md": "---\ndescription: Lint the project\n---\nRun the linter on $ARGUMENTS\n",
            "agents/linter.md": "---\nname: linter\ndescription: Fixes lint\ntools: Read, Edit\n---\nFix lint.\n",
            "skills/style/SKILL.md": "---\nname: house-style\ndescription: The house style\n---\nTabs.\n",
            "hooks/hooks.json": json.dumps({"hooks": {"PostToolUse": [{"matcher": "Edit", "hooks": [
                {"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/fmt.sh"}]}]}}),
            ".mcp.json": json.dumps({"mcpServers": {"lintd": {"command": "${CLAUDE_PLUGIN_ROOT}/server"}}}),
        })
        root = make_project(CALC)
        p = plugins.install(src, root=root)
        self.assertEqual((p["name"], p["version"]), ("lint-kit", "1.0.0"))
        self.assertEqual(sorted(p["has"]), ["agents", "commands", "hooks", "mcp", "skills"])
        self.assertIn("lint", extensions.custom_commands(root))
        self.assertIn("linter", extensions.agents(root))
        self.assertIn("house-style", extensions.skills(root))
        hook = settings.project(root)["hooks"]["PostToolUse"][-1]["hooks"][0]["command"]
        self.assertEqual(hook, p["dir"] + "/fmt.sh")            # ${CLAUDE_PLUGIN_ROOT} is the plugin's folder
        self.assertEqual(mcp.configs(root)["lintd"]["command"], p["dir"] + "/server")
        with self.assertRaises(ValueError):
            plugins.install(src, root=root)                 # already there
        plugins.remove("lint-kit", root)
        self.assertNotIn("lint", extensions.custom_commands(root))


    def test_marketplace_add_and_install(self):
        from newal_code import plugins
        market = make_project({
            ".claude-plugin/marketplace.json": json.dumps({
                "name": "team-tools", "owner": {"name": "Team"}, "metadata": {"pluginRoot": "./plugins"},
                "plugins": [{"name": "fmt", "source": "fmt", "description": "Formatting"},
                            {"name": "docs", "source": "./extra/docs"}]}),
            "plugins/fmt/.claude-plugin/plugin.json": json.dumps({"name": "fmt"}),
            "plugins/fmt/commands/fmt.md": "Format $ARGUMENTS\n",
            "extra/docs/skills/write/SKILL.md": "---\nname: doc-writing\ndescription: Docs style\n---\nShort.\n",
        })
        m = plugins.marketplace_add(market)
        self.assertEqual((m["name"], m["plugins"]), ("team-tools", ["fmt", "docs"]))
        self.assertIn("team-tools", [x["name"] for x in plugins.marketplaces()])
        root = make_project(CALC)
        self.assertEqual(plugins.install("fmt@team-tools", root=root)["name"], "fmt")
        plugins.install("docs@team-tools", root=root)
        self.assertIn("fmt", extensions.custom_commands(root))
        self.assertIn("doc-writing", extensions.skills(root))
        with self.assertRaises(ValueError):
            plugins.install("nope@team-tools", root=root)
        plugins.marketplace_remove("team-tools")
        self.assertEqual([x["name"] for x in plugins.marketplaces()], ["newal"])    # NewAl's own stays


MARKET = os.path.join(os.path.dirname(HERE), "newal_code", "market", "plugins")


class StoreTest(unittest.TestCase):
    """NewAl's own plugins (newal_code/market): one tap installs them with nothing to download, and each does its
    work with a program, run here for real: no model is involved."""

    def setUp(self):
        from newal_code import service
        self.root = make_project({"app.py": "print('hi')\n", "pyproject.toml": "[project]\nname = 'x'\n"})
        self.svc = service.Service()

    def install(self, name):
        from newal_code import plugins
        return plugins.install(name + "@newal", root=self.root)        # this project's: no hooks leak to others

    def run_cmd(self, text):
        return self.svc.command(self.svc.create(self.root), text)

    def test_listed_first_installed_offline_and_kept_up_to_date(self):
        from newal_code import plugins
        m = plugins.marketplaces()[0]
        self.assertEqual((m["name"], m["builtin"]), ("newal", True))
        listed = {p["name"]: p for p in m["plugins"]}
        self.assertIn("commands", listed["system"]["has"])
        self.assertIn("hooks", listed["guard"]["has"])
        p = self.install("system")
        cmds = extensions.custom_commands(self.root)
        for c in ("sysinfo", "disk", "clean", "ports", "programs"):
            self.assertTrue(cmds[c]["script"], c)
        with self.assertRaises(ValueError):
            plugins.marketplace_remove("newal")
        # an older copy (as after an update of NewAl Code) is replaced by this version's
        with open(os.path.join(p["dir"], ".claude-plugin", "plugin.json"), "w") as f:
            json.dump({"name": "system", "version": "0.1"}, f)
        os.remove(os.path.join(p["dir"], "scripts", "disk.py"))
        plugins._fresh.clear()
        plugins.dirs(self.root)
        self.assertTrue(os.path.isfile(os.path.join(p["dir"], "scripts", "disk.py")))
        self.assertNotEqual(plugins.info(p["dir"])["version"], "0.1")

    def test_sysinfo_disk_and_ports_answer_at_once(self):
        self.install("system")
        r = self.run_cmd("/sysinfo")
        self.assertEqual((r["output"], r["code"]), (True, 0), r["reply"])
        for word in ("System", "Processor", "Memory", "GB", "python"):
            self.assertIn(word, r["reply"])
        with open(os.path.join(self.root, "big.bin"), "wb") as f:
            f.write(os.urandom(3 * 1024 * 1024))
        r = self.run_cmd("/disk")
        self.assertEqual(r["code"], 0, r["reply"])
        self.assertRegex(r["reply"], r"big\.bin\s+3\.0 MB")
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        port = srv.getsockname()[1]
        try:
            r = self.run_cmd("/ports %d" % port)
        finally:
            srv.close()
        self.assertEqual(r["code"], 0, r["reply"])
        self.assertRegex(r["reply"], r"%d\s+this device only" % port)
        self.assertIn("free, nothing listens on it", self.run_cmd("/ports 1")["reply"])

    def test_clean_lists_then_deletes_only_caches(self):
        from unittest import mock
        self.install("system")
        home = tempfile.mkdtemp()
        tmp = os.path.join(home, "tmp")
        local = os.path.join(home, "AppData", "Local")
        env = {"HOME": home, "USERPROFILE": home, "LOCALAPPDATA": local, "XDG_CACHE_HOME": os.path.join(home, ".cache"),
               "TMPDIR": tmp, "TEMP": tmp, "TMP": tmp}
        pip = (os.path.join(local, "pip", "Cache") if os.name == "nt" else
               os.path.join(home, "Library", "Caches", "pip") if sys.platform == "darwin" else
               os.path.join(home, ".cache", "pip"))

        def put(path, n=20000):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(os.urandom(n))
        put(os.path.join(pip, "http", "blob"))
        old = os.path.join(tmp, "old-build")
        put(os.path.join(old, "x.log"))
        then = time.time() - 2 * 86400
        os.utime(os.path.join(old, "x.log"), (then, then))
        os.utime(old, (then, then))
        fresh = os.path.join(tmp, "fresh.txt")
        put(fresh)
        pyc = os.path.join(self.root, "__pycache__")
        put(os.path.join(pyc, "app.cpython-3.pyc"))
        with mock.patch.dict(os.environ, env):
            r = self.run_cmd("/clean")
            self.assertEqual(r["code"], 0, r["reply"])
            for part in ("pip cache", "old-build", "__pycache__", "/clean --yes"):
                self.assertIn(part, r["reply"])
            self.assertNotIn("fresh.txt", r["reply"])                 # touched less than a day ago
            self.assertTrue(os.path.exists(pip))                        # listing deletes nothing
            r = self.run_cmd("/clean --yes")
        self.assertEqual(r["code"], 0, r["reply"])
        self.assertIn("Deleted", r["reply"])
        for gone in (pip, old, pyc):
            self.assertFalse(os.path.exists(gone), gone)
        self.assertTrue(os.path.exists(fresh))
        self.assertTrue(os.path.exists(os.path.join(self.root, "app.py")))

    def test_programs_says_how_and_searches(self):
        self.install("system")
        r = self.run_cmd("/programs")
        self.assertEqual(r["code"], 2)
        self.assertIn("/programs search", r["reply"])
        if sys.platform.startswith("linux") and shutil.which("apt-cache"):
            r = self.run_cmd("/programs search coreutils")
            self.assertEqual(r["code"], 0, r["reply"])
            self.assertIn("coreutils", r["reply"])

    def test_csv_reads_csv_excel_json_and_arabic_files_and_draws_a_chart(self):
        import zipfile
        self.install("data")
        with open(os.path.join(self.root, "sales.csv"), "w", encoding="utf-8", newline="") as f:
            f.write('region,sales,date,note\nNorth,"1,200",2026-01-05,ok\nSouth,800,2026-01-06,\n'
                    'North,300.5,2026-02-01,late\nEast,50,2026-02-03,ok\n')
        with open(os.path.join(self.root, "ar.csv"), "wb") as f:              # Excel's Arabic "CSV": cp1256, ;
            f.write("المدينة;المبلغ\nعمان;100\nإربد;250\nعمان;50\n".encode("cp1256"))
        with open(os.path.join(self.root, "s.json"), "w") as f:
            json.dump([{"name": "a", "score": 3}, {"name": "b", "score": 5}, {"name": "c", "score": None}], f)
        m = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        with zipfile.ZipFile(os.path.join(self.root, "book.xlsx"), "w") as z:
            z.writestr("xl/workbook.xml", '<workbook xmlns="%s" xmlns:r="http://schemas.openxmlformats.org/'
                       'officeDocument/2006/relationships"><sheets><sheet name="D" sheetId="1" r:id="rId1"/>'
                       '</sheets></workbook>' % m)
            z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/'
                       'package/2006/relationships"><Relationship Id="rId1" Type="x" Target="worksheets/d.xml"/>'
                       '</Relationships>')
            z.writestr("xl/sharedStrings.xml", '<sst xmlns="%s"><si><t>item</t></si><si><t>price</t></si>'
                       '<si><t>pen</t></si><si><t>book</t></si></sst>' % m)
            z.writestr("xl/worksheets/d.xml", '<worksheet xmlns="%s"><sheetData>'
                       '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
                       '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>1.5</v></c></row>'
                       '<row r="3"><c r="A3" t="s"><v>3</v></c><c r="B3"><v>12</v></c></row>'
                       '<row r="4"><c r="A4" t="inlineStr"><is><t>bag</t></is></c></row></sheetData></worksheet>' % m)
        r = self.run_cmd("/csv sales.csv region sales")
        self.assertEqual(r["code"], 0, r["reply"])
        self.assertIn("sales.csv: 4 rows, 4 columns", r["reply"])
        self.assertIn("min 50 · max 1,200 · mean 587.62 · median 550.25 · total 2,350.50", r["reply"])
        self.assertIn("2026-01-05 → 2026-02-03", r["reply"])
        svg = os.path.join(self.root, "sales-sales-by-region.svg")
        with open(svg, encoding="utf-8") as f:
            chart = f.read()
        self.assertIn("North", chart)
        self.assertIn("1,500.50", chart)                                  # 1,200 + 300.5
        r = self.run_cmd("/csv ar.csv")
        self.assertIn("(cp1256)", r["reply"])
        self.assertIn("عمان (2)", r["reply"])
        self.assertIn("total 400", r["reply"])
        r = self.run_cmd("/csv book.xlsx price")
        self.assertIn("book.xlsx: 3 rows, 2 columns", r["reply"])
        self.assertIn("total 13.50", r["reply"])
        self.assertTrue(os.path.isfile(os.path.join(self.root, "book-price.svg")))
        r = self.run_cmd("/csv s.json")
        self.assertRegex(r["reply"], r"score\s+number\s+2\s+1\s+2\s+min 3 · max 5")
        r = self.run_cmd("/csv sales.csv nope")
        self.assertEqual(r["code"], 2)
        self.assertIn("no such column", r["reply"])

    def test_rtl_lists_then_rewrites_css_and_pages(self):
        self.install("arabic")
        files = {"index.html": '<!doctype html>\n<html>\n<head><style>\n'
                               '.nav-left:hover { margin-left: 8px; text-align: left; }\n'
                               '.card { padding: 4px 12px 4px calc(1rem + 2px) !important; float: right; left: 0; }\n'
                               '</style></head>\n<body><div class="left" style="padding-right: 3px">x</div></body>\n'
                               '</html>\n',
                 "css/app.css": ".side { margin-right: 2rem; }\n.same { padding: 1px 2px 1px 2px; }\n",
                 "src/App.jsx": 'const S = () => <div style={{ marginLeft: 4, textAlign: "left" }} />;\n'
                                'const obj = { left: 1 };\n'}
        for rel, text in files.items():
            os.makedirs(os.path.dirname(os.path.join(self.root, rel)), exist_ok=True)
            with open(os.path.join(self.root, rel), "w", encoding="utf-8") as f:
                f.write(text)
        r = self.run_cmd("/rtl")
        self.assertEqual(r["code"], 0, r["reply"])
        self.assertIn("margin-left → margin-inline-start", r["reply"])
        self.assertIn("/rtl --apply", r["reply"])
        with open(os.path.join(self.root, "css", "app.css")) as f:
            self.assertIn("margin-right", f.read())                        # listing writes nothing
        self.run_cmd("/rtl --apply")

        def read(rel):
            with open(os.path.join(self.root, rel), encoding="utf-8") as f:
                return f.read()
        page = read("index.html")
        self.assertIn('<html dir="rtl" lang="ar">', page)
        self.assertIn(".nav-left:hover { margin-inline-start: 8px; text-align: start; }", page)
        self.assertIn("padding-block: 4px 4px !important; padding-inline: calc(1rem + 2px) 12px !important;", page)
        self.assertIn("float: inline-end; inset-inline-start: 0;", page)
        self.assertIn('class="left" style="padding-inline-end: 3px"', page)
        self.assertEqual(read("css/app.css"), ".side { margin-inline-end: 2rem; }\n.same { padding: 1px 2px 1px 2px; }\n")
        self.assertEqual(read("src/App.jsx"), 'const S = () => <div style={{ marginInlineStart: 4, textAlign: '
                                              '"start" }} />;\nconst obj = { left: 1 };\n')
        self.assertIn("Nothing to change", self.run_cmd("/rtl")["reply"])

    def test_secrets_are_found_and_the_guard_asks_before_committing_one(self):
        self.install("guard")
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "-c", "user.name=T", "-c", "user.email=t@x", "add", "-A")
        git(self.root, "-c", "user.name=T", "-c", "user.email=t@x", "commit", "-q", "-m", "first")
        self.assertIn("No secrets found", self.run_cmd("/secrets")["reply"])
        with open(os.path.join(self.root, "config.py"), "w") as f:
            f.write('TOKEN = "ghp_%s"\npassword = "changeme-please"\n' % ("A" * 36))
        r = self.run_cmd("/secrets")
        self.assertEqual(r["code"], 1, r["reply"])
        self.assertIn("config.py:1  GitHub token  ghp_…AA", r["reply"])
        self.assertNotIn("config.py:2", r["reply"])                       # a placeholder is no secret
        asked = []
        run_agent([{"tools": [("bash", {"command": "git add -A && git commit -q -m wip"})]}, "done"],
                  mode="full-auto", approve=lambda req: asked.append(req) or "deny:no", root=self.root)
        self.assertEqual(len(asked), 1)
        self.assertIn("would publish what looks like a secret: config.py:1 GitHub token", asked[0]["reason"])
        os.remove(os.path.join(self.root, "config.py"))
        asked.clear()
        with open(os.path.join(self.root, "notes.txt"), "w") as f:
            f.write("nothing secret\n")
        run_agent([{"tools": [("bash", {"command": "git add -A && git -c user.name=T -c user.email=t@x commit -q "
                                                   "-m notes"})]}, "done"],
                  mode="full-auto", approve=lambda req: asked.append(req) or "once", root=self.root)
        self.assertEqual(asked, [])                                          # nothing secret: no asking
        self.assertIn("notes", git(self.root, "log", "--oneline", "-1"))

    def test_serve_shows_a_folder_at_an_address_until_stopped(self):
        self.install("system")
        site = os.path.join(self.root, "site")
        os.makedirs(site)
        with open(os.path.join(site, "index.html"), "w", encoding="utf-8") as f:
            f.write("<h1>مرحبا</h1>")
        r = self.run_cmd("/serve site")
        try:
            self.assertEqual(r["code"], 0, r["reply"])
            url = re.search(r"http://127\.0\.0\.1:\d+/", r["reply"]).group(0)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(url, timeout=10) as page:
                self.assertEqual(page.read().decode("utf-8"), "<h1>مرحبا</h1>")
                self.assertIn("charset=utf-8", page.headers.get("Content-Type"))
            self.assertIn("already running", self.run_cmd("/serve site")["reply"])
            self.assertIn(url, self.run_cmd("/serve list")["reply"])
        finally:
            r = self.run_cmd("/serve stop")
        self.assertIn("Stopped: :", r["reply"])
        port = int(url.rstrip("/").rsplit(":", 1)[1])
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=2).close()

    def test_create_makes_projects_that_pass_their_own_tests(self):
        self.install("starters")
        r = self.run_cmd("/create python hello-cli")
        self.assertEqual(r["code"], 0, r["reply"])
        self.assertIn("Its test: ✓ passes", r["reply"])
        self.assertTrue(os.path.isfile(os.path.join(self.root, "hello-cli", "src", "hello_cli", "__main__.py")))
        r = self.run_cmd("/create web notes --ar")
        self.assertIn("✓", r["reply"])
        with open(os.path.join(self.root, "notes", "index.html"), encoding="utf-8") as f:
            self.assertIn('<html lang="ar" dir="rtl">', f.read())
        if shutil.which("node"):
            self.assertIn("Its test: ✓ passes", self.run_cmd("/create node greeter")["reply"])
        self.assertIn("already exists", self.run_cmd("/create python hello-cli")["reply"])
        self.assertEqual(self.run_cmd("/create rust x")["code"], 2)

    def test_guard_asks_even_with_full_access(self):
        self.install("guard")
        asked = []

        def approve(req):
            asked.append(req)
            return "deny:not now"
        run_agent([{"tools": [("write", {"path": ".env", "content": "TOKEN=1\n"})]},
                   {"tools": [("bash", {"command": "git push --force origin main"})]},
                   {"tools": [("write", {"path": "notes.txt", "content": "ok\n"})]}, "done"],
                  mode="full-auto", approve=approve, root=self.root)
        self.assertEqual(len(asked), 2, asked)
        self.assertIn(".env may hold secrets", asked[0]["reason"])
        self.assertIn("force push", asked[1]["reason"])
        self.assertFalse(os.path.exists(os.path.join(self.root, ".env")))
        self.assertTrue(os.path.exists(os.path.join(self.root, "notes.txt")))     # the rest: no asking

    def test_guard_rules(self):
        import runpy
        decide = runpy.run_path(os.path.join(MARKET, "guard", "hooks", "guard.py"))["decide"]

        def ask(tool, **inp):
            return decide({"tool_name": tool, "tool_input": inp})[0]
        for cmd in ("git push --force origin main", "git push -f", "git push origin +main", "git reset --hard HEAD~2",
                    "git clean -fdx", "git branch -D old", "git checkout -- .", "rm -rf ~", "rm -rf .git",
                    "npm publish", "sqlite3 db.sqlite 'DROP TABLE users'", "git push origin --delete feature"):
            self.assertTrue(ask("Bash", command=cmd), cmd)
        for cmd in ("git push", "git push -u origin feature", "rm -rf build/", "git reset --soft HEAD~1", "ls -la",
                    "git checkout -b new", "rm -rf ./node_modules", "npm test"):
            self.assertFalse(ask("Bash", command=cmd), cmd)
        self.assertTrue(ask("Write", file_path="/p/.env"))
        self.assertTrue(ask("edit", path="config/prod.key"))
        self.assertFalse(ask("Write", file_path=".env.example"))
        self.assertFalse(ask("Write", file_path="id_rsa.pub"))
        self.assertFalse(ask("Edit", file_path="src/app.py"))
        self.assertTrue(ask("apply_patch", patch="*** Begin Patch\n*** Add File: .env.local\n+X=1\n*** End Patch"))

    @unittest.skipIf(os.name == "nt", "a shell script stands in for gofmt")
    def test_format_on_edit_runs_the_formatter_and_says_so(self):
        from unittest import mock
        self.install("format-on-edit")
        bin_dir = tempfile.mkdtemp()
        fake = os.path.join(bin_dir, "gofmt")
        with open(fake, "w") as f:
            f.write("#!/bin/sh\n# stands in for gofmt -w FILE\nsed 's/  */ /g' \"$2\" > \"$2.tmp\" && mv \"$2.tmp\" \"$2\"\n")
        os.chmod(fake, 0o755)
        with mock.patch.dict(os.environ, {"PATH": bin_dir + os.pathsep + os.environ.get("PATH", "")}):
            _, events, _, _, _ = run_agent([{"tools": [("write", {"path": "main.go", "content": "package  main\n"})]},
                                            "done"], root=self.root)
        with open(os.path.join(self.root, "main.go")) as f:
            self.assertEqual(f.read(), "package main\n")
        end = next(e for e in events if e.get("type") == "tool_end" and e["name"] == "write")
        self.assertIn("format-on-edit reformatted main.go", end["text"])

    def test_format_on_edit_leaves_projects_without_a_formatter_set_up(self):
        import runpy
        fmt = runpy.run_path(os.path.join(MARKET, "format-on-edit", "hooks", "format.py"))
        plain = make_project({"a.py": "x=1\n", "pyproject.toml": "[project]\nname='x'\n"})
        self.assertIsNone(fmt["formatter"](os.path.join(plain, "a.py"), plain))
        ruffed = make_project({"a.py": "x=1\n", "pyproject.toml": "[tool.ruff]\nline-length = 100\n"})
        cmd = fmt["formatter"](os.path.join(ruffed, "a.py"), ruffed)
        self.assertTrue(cmd is None or cmd[1:3] == ["format", "--quiet"], cmd)      # ruff, where it is installed
        patch_in = {"cwd": plain, "tool_input": {"patch": "*** Begin Patch\n*** Update File: a.py\n@@\n*** End Patch"}}
        self.assertEqual(fmt["edited_files"](patch_in), [os.path.join(plain, "a.py")])

    def test_packaged_app_runs_plugin_programs(self):
        # --newal-python: how the packaged app, which has no python of its own, runs a plugin's program
        from unittest import mock
        from newal_code import plugins
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "helper.py"), "w", encoding="utf-8") as f:
            f.write("WORD = 'مرحبا'\n")
        with open(os.path.join(d, "main.py"), "w", encoding="utf-8") as f:
            f.write("import sys\nfrom helper import WORD\nprint(WORD, sys.argv[1:])\nsys.exit(3)\n")
        p = subprocess.run([sys.executable, os.path.join(os.path.dirname(HERE), "newal-code.py"), "--newal-python",
                            os.path.join(d, "main.py"), "a b"], capture_output=True, timeout=60)
        self.assertEqual(p.returncode, 3, p.stderr)
        self.assertEqual(p.stdout.decode("utf-8").strip(), "مرحبا ['a b']")
        cli = os.path.join(d, "newal-code" + (".exe" if os.name == "nt" else ""))
        open(cli, "w").close()
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", os.path.join(d, "NewAlCode")):
            self.assertEqual(plugins.python_argv(), [cli, "--newal-python"])
        self.assertEqual(plugins.python_argv(), [sys.executable])
        self.assertEqual(plugins._expand('${NEWAL_PYTHON:-python3} "${CLAUDE_PLUGIN_ROOT}/x.py"', "/p"),
                         '"%s" "/p/x.py"' % sys.executable.replace("\\", "/"))

    def test_command_line(self):
        env = dict(os.environ, NEWAL_CODE_HOME=tempfile.mkdtemp())

        def run(*a):
            return subprocess.run([sys.executable, "-m", "newal_code", "plugin", *a], cwd=os.path.dirname(HERE),
                                  capture_output=True, text=True, env=env, timeout=60)
        self.assertIn("system@newal", run("list").stdout)
        p = run("install", "system@newal")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("Installed system", p.stdout)
        self.assertEqual(run("marketplace", "remove", "newal").returncode, 1)
        root = make_project({"a.txt": "x"})
        p = subprocess.run([sys.executable, "-m", "newal_code", "exec", "/ports 1", "--cd", root],
                           cwd=os.path.dirname(HERE), capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("free, nothing listens on it", p.stdout)


class SyncTest(unittest.TestCase):
    """/sync against a real remote (a bare repository), with the computer's git and with the phone's (minigit)."""

    def setUp(self):
        from unittest import mock
        self.top = tempfile.mkdtemp(prefix="nc-sync-")
        self.addCleanup(shutil.rmtree, self.top, True)
        env = mock.patch.dict(os.environ, {"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "T",
                                           "GIT_COMMITTER_EMAIL": "t@x", "GIT_CONFIG_NOSYSTEM": "1",
                                           "HOME": self.top, "USERPROFILE": self.top})
        env.start()
        self.addCleanup(env.stop)
        self.bare = os.path.join(self.top, "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", self.bare], check=True)
        seed = os.path.join(self.top, "seed")
        subprocess.run(["git", "init", "-q", "-b", "main", seed], check=True)
        self.write(seed, "a.txt", "one\ntwo\n")
        for cmd in (["add", "-A"], ["commit", "-q", "-m", "first"], ["remote", "add", "origin", self.bare],
                    ["push", "-q", "-u", "origin", "main"]):
            subprocess.run(["git"] + cmd, cwd=seed, check=True)

    def write(self, root, name, text):
        with open(os.path.join(root, name), "w", newline="\n") as f:
            f.write(text)

    def clone(self, name):
        d = os.path.join(self.top, name)
        subprocess.run(["git", "clone", "-q", self.bare, d], check=True)
        return d

    def commit(self, root, name, text, msg):
        self.write(root, name, text)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", msg], cwd=root, check=True)

    def remote_log(self):
        return subprocess.run(["git", "--git-dir", self.bare, "log", "--format=%s", "main"], capture_output=True,
                              text=True).stdout.split("\n")

    def test_pulls_then_pushes_and_says_so(self):
        from newal_code import sync
        a, b = self.clone("a"), self.clone("b")
        self.commit(b, "b.txt", "from b\n", "from b")
        subprocess.run(["git", "push", "-q"], cwd=b, check=True)
        self.commit(a, "c.txt", "from a\n", "from a")
        ok, text = sync.sync(a)
        self.assertTrue(ok, text)
        self.assertIn("Pulled 1 new commit from origin/main.", text)
        self.assertIn("Pushed 1 commit to origin/main.", text)
        self.assertEqual(self.remote_log()[:3], ["from a", "from b", "first"])     # rebased: no merge commit
        ok, text = sync.sync(a)
        self.assertTrue(ok, text)
        self.assertIn("Nothing new on origin/main.", text)
        self.assertIn("Nothing to push", text)

    def test_a_conflict_says_which_files_and_how_to_finish_or_undo(self):
        from newal_code import sync
        a, b = self.clone("a"), self.clone("b")
        self.commit(b, "a.txt", "one\nTWO from b\n", "b edits")
        subprocess.run(["git", "push", "-q"], cwd=b, check=True)
        self.commit(a, "a.txt", "one\nTWO from a\n", "a edits")
        ok, text = sync.sync(a)
        self.assertFalse(ok)
        self.assertIn("a.txt", text)
        self.assertIn("git rebase --continue", text)
        self.assertIn("git rebase --abort", text)
        self.assertEqual(self.remote_log()[0], "b edits")                              # nothing pushed

    def test_a_new_branch_is_pushed_and_followed(self):
        from newal_code import sync
        a = self.clone("a")
        subprocess.run(["git", "switch", "-q", "-c", "feature"], cwd=a, check=True)
        self.commit(a, "f.txt", "f\n", "feature work")
        ok, text = sync.sync(a)
        self.assertTrue(ok, text)
        self.assertIn("Pushed the branch to origin/feature.", text)
        up = subprocess.run(["git", "rev-parse", "--abbrev-ref", "@{u}"], cwd=a, capture_output=True, text=True)
        self.assertEqual(up.stdout.strip(), "origin/feature")

    def test_outside_a_repository_or_without_a_remote(self):
        from newal_code import sync
        ok, text = sync.sync(tempfile.mkdtemp())
        self.assertFalse(ok)
        self.assertIn("not a git repository", text)
        solo = os.path.join(self.top, "solo")
        subprocess.run(["git", "init", "-q", "-b", "main", solo], check=True)
        ok, text = sync.sync(solo)
        self.assertFalse(ok)
        self.assertIn("git remote add origin", text)

    def phones_git(self):
        """git on PATH is the phone's (minigit, over dulwich) inside the with block."""
        from unittest import mock
        if os.name == "nt":
            self.skipTest("a shell script stands in for the phone's git")
        try:
            import dulwich  # noqa: F401
            import merge3  # noqa: F401
        except ImportError:
            self.skipTest("dulwich and merge3 (the phone's git) are not installed")
        bin_dir = os.path.join(self.top, "bin")
        if not os.path.isdir(bin_dir):
            os.makedirs(bin_dir)
            with open(os.path.join(bin_dir, "git"), "w") as f:
                f.write('#!/bin/sh\nPYTHONPATH="%s" exec "%s" -m newal_code.minigit "$@"\n' % (
                    os.path.dirname(HERE), sys.executable))
            os.chmod(os.path.join(bin_dir, "git"), 0o755)
        return mock.patch.dict(os.environ, {"PATH": bin_dir + os.pathsep + os.environ["PATH"]})

    def test_the_phones_git_stops_on_a_conflict_then_finishes_or_undoes_the_merge(self):
        from newal_code import sync
        a, b = self.clone("a"), self.clone("b")
        self.commit(b, "a.txt", "one\nTWO from b\n", "b edits")
        subprocess.run(["git", "push", "-q"], cwd=b, check=True)
        self.commit(a, "a.txt", "one\nTWO from a\n", "a edits")
        with self.phones_git():
            ok, text = sync.sync(a)
            self.assertFalse(ok)
            self.assertIn("a.txt", text)
            self.assertIn("git merge --continue", text)
            with open(os.path.join(a, "a.txt")) as f:
                self.assertIn("<<<<<<<", f.read())
            r = subprocess.run(["git", "commit", "-am", "x"], cwd=a, capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)                   # never a commit with the markers in it
            self.assertIn("unmerged files", r.stderr + r.stdout)
            r = subprocess.run(["git", "merge", "--abort"], cwd=a, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(os.path.join(a, "a.txt")) as f:
                self.assertEqual(f.read(), "one\nTWO from a\n")     # as before the pull
            self.assertFalse(sync.sync(a)[0])                         # the same conflict again
            self.write(a, "a.txt", "one\nTWO from a and b\n")
            for cmd in (["add", "a.txt"], ["merge", "--continue"]):
                r = subprocess.run(["git"] + cmd, cwd=a, capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            ok, text = sync.sync(a)
            self.assertTrue(ok, text)
        parents = subprocess.run(["git", "--git-dir", self.bare, "log", "-1", "--format=%P", "main"],
                                 capture_output=True, text=True).stdout.split()
        self.assertEqual(len(parents), 2)                                 # a merge of both sides, pushed
        self.assertIn("b edits", self.remote_log())

    def test_with_the_phones_git(self):
        from newal_code import sync
        a, b = self.clone("a"), self.clone("b")
        self.commit(b, "b.txt", "from b\n", "from b")
        subprocess.run(["git", "push", "-q"], cwd=b, check=True)
        self.commit(a, "c.txt", "from a\n", "from a")
        with self.phones_git():
            ok, text = sync.sync(a)
        self.assertTrue(ok, text)
        self.assertIn("Pulled the new commits from origin/main.", text)                # a merge: no rev-list
        log = self.remote_log()
        self.assertIn("from a", log)
        self.assertIn("from b", log)


class HooksTest(unittest.TestCase):
    def test_pre_tool_use_blocks_and_prompt_context(self):
        py = sys.executable.replace("\\", "/")          # quoted, with / : bash (Git Bash on Windows) runs it as is
        cfg = {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command":
               "\"%s\" -c \"import sys,json; d=json.load(sys.stdin); sys.stderr.write('no rm'); "
               "sys.exit(2 if 'rm' in d['tool_input']['command'] else 0)\"" % py}]}],
               "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "echo remember-this"}]}]}
        cwd = tempfile.gettempdir()
        r = hooks.run(cfg, "PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "rm x"}}, cwd, tool="bash")
        self.assertTrue(r.block)
        self.assertEqual(r.permission, "deny")
        self.assertIn("no rm", r.reason)
        r = hooks.run(cfg, "PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "ls"}}, cwd, tool="bash")
        self.assertFalse(r.block)
        r = hooks.run(cfg, "UserPromptSubmit", {"prompt": "x"}, cwd)
        self.assertEqual(r.context, ["remember-this"])


class RamTest(unittest.TestCase):
    def fake_gguf(self, path, arch="qwen35", layers=32, kv_heads=4, head_dim=256, interval=4, size_mb=100, nextn=0):
        def s(x):
            b = x.encode()
            return struct.pack("<Q", len(b)) + b
        kvs = [("general.architecture", 8, s(arch)), ("general.name", 8, s("Fake")),
               ("%s.block_count" % arch, 4, struct.pack("<I", layers)),
               ("%s.context_length" % arch, 4, struct.pack("<I", 262144)),
               ("%s.embedding_length" % arch, 4, struct.pack("<I", 2560)),
               ("%s.attention.head_count" % arch, 4, struct.pack("<I", 16)),
               ("%s.attention.head_count_kv" % arch, 4, struct.pack("<I", kv_heads)),
               ("%s.attention.key_length" % arch, 4, struct.pack("<I", head_dim)),
               ("%s.attention.value_length" % arch, 4, struct.pack("<I", head_dim))]
        if interval:        # a hybrid model: Gated DeltaNet layers between the attention ones (Qwen3.5's sizes)
            kvs.append(("%s.full_attention_interval" % arch, 4, struct.pack("<I", interval)))
            for k, v in (("conv_kernel", 4), ("state_size", 128), ("group_count", 16), ("inner_size", 4096)):
                kvs.append(("%s.ssm.%s" % (arch, k), 4, struct.pack("<I", v)))
        if nextn:
            kvs.append(("%s.nextn_predict_layers" % arch, 4, struct.pack("<I", nextn)))
        with open(path, "wb") as f:
            f.write(b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", len(kvs)))
            for k, t, v in kvs:
                f.write(s(k) + struct.pack("<I", t) + v)
            f.truncate(size_mb * 1024 * 1024)

    def test_gguf_info_and_hybrid_kv(self):
        p = os.path.join(tempfile.mkdtemp(), "m.gguf")
        self.fake_gguf(p)
        info = gguf.info(p)
        self.assertEqual(info["attention_layers"], 8)
        self.assertEqual(info["kv_bytes_per_token"], 8 * 4 * 512 * 2)
        # 24 recurrent layers, each with a 128 x 4096 state and a 3 x 8192 convolution state, in f32: ~50 MB
        self.assertEqual(info["state_bytes"], 24 * (128 * 4096 + 3 * 8192) * 4)
        self.assertEqual(info["draft_kv_bytes_per_token"], 0)
        # A file with an MTP head: one more block, which is not one of the model's own layers
        mtp = os.path.join(tempfile.mkdtemp(), "mtp.gguf")
        self.fake_gguf(mtp, layers=33, nextn=1)
        info = gguf.info(mtp)
        self.assertEqual((info["layers"], info["attention_layers"], info["nextn"]), (32, 8, 1))
        self.assertEqual(info["state_bytes"], 24 * (128 * 4096 + 3 * 8192) * 4)
        self.assertEqual(info["draft_kv_bytes_per_token"], 4 * 512 * 2)

    def test_plan_counts_states_and_checkpoints(self):
        p = os.path.join(tempfile.mkdtemp(), "m.gguf")
        self.fake_gguf(p, size_mb=2800)
        plan = runtime.plan(p, budget=64 * 1024 ** 3)
        info = plan["info"]
        states = 2 * info["state_bytes"] * (1 + runtime.CHECKPOINTS)          # 2 slots, each with its checkpoints
        self.assertGreaterEqual(plan["need"], info["size"] + plan["kv"] + states)
        s = runtime.Server(p, budget=64 * 1024 ** 3)
        args = s.args("llama-server")
        self.assertEqual(args[args.index("--ctx-checkpoints") + 1], str(runtime.CHECKPOINTS))
        # the RAM cache for other conversations only takes what the plan leaves
        self.assertLessEqual(s.cache_ram_mb() * 1024 * 1024, s.plan["budget"] - s.plan["need"])

    def test_tiers_and_plans_fit(self):
        GB = 1024 ** 3
        self.assertEqual(hardware.tier(8 * GB), "8gb")
        self.assertEqual(hardware.tier(16 * GB), "16gb")
        self.assertGreaterEqual(hardware.budget(8 * GB), 5 * GB)
        d = tempfile.mkdtemp()
        small = os.path.join(d, "small.gguf")
        self.fake_gguf(small, size_mb=2800)          # a 4B Q4 model
        dense = os.path.join(d, "dense.gguf")
        self.fake_gguf(dense, arch="llama", layers=32, kv_heads=8, head_dim=128, interval=0, size_mb=4700)
        for total in (8, 12, 16):
            budget = hardware.budget(total * GB)
            for f in ((small,) if total == 8 else (small, dense)):
                plan = runtime.plan(f, budget=budget)
                self.assertTrue(plan["fits"], (total, f, plan))
                self.assertLessEqual(plan["need"], budget)
                self.assertGreaterEqual(plan["ctx"], 8192)
        # A dense 8B at Q4 (4.7 GB) leaves too little on 8 GB; the hybrid 4B keeps its full 32k context there.
        self.assertFalse(runtime.plan(dense, budget=hardware.budget(8 * GB))["fits"])
        self.assertEqual(runtime.plan(small, budget=hardware.budget(8 * GB))["ctx"], 32768)

    def test_catalog_recommendations_fit(self):
        GB = 1024 ** 3
        for total in (8, 12, 16, 24):
            m = models.catalog.recommended(total * GB)
            self.assertTrue(models.catalog.fits(m, total * GB), (total, m["id"]))
        self.assertEqual(models.catalog.recommended(8 * GB)["id"], "qwen3.5-4b")


class ProviderTest(unittest.TestCase):
    def test_openai_stream_with_tool_calls(self):
        llm = FakeLLM([{"text": "ok ", "tools": [("read", {"path": "a.py"}), ("glob", {"pattern": "*"})]}])
        events = []
        c = providers.OpenAICompat(llm.url).chat("m", [{"role": "user", "content": "x"}],
                                                 on_event=lambda k, p: events.append(k))
        llm.close()
        self.assertEqual(c.content, "ok ")
        self.assertEqual([t["name"] for t in c.tool_calls], ["read", "glob"])
        self.assertEqual(json.loads(c.tool_calls[0]["arguments"]), {"path": "a.py"})
        self.assertEqual(c.usage["cached"], 60)
        self.assertIn("tool_start", events)

    def test_anthropic_stream_and_conversion(self):
        llm = FakeLLM([{"text": "looking", "tools": [("read", {"path": "a.py"})]}])
        c = providers.Anthropic("key", llm.base).chat("claude", [
            {"role": "system", "content": "sys"}, {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "type": "function",
                                                                 "function": {"name": "glob", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "t1", "content": "a.py"}], tools=tools.schemas(["read", "glob"]))
        body = llm.requests[0]
        llm.close()
        self.assertEqual(c.tool_calls[0]["name"], "read")
        self.assertEqual(c.usage["cached"], 40)
        self.assertEqual(body["system"][0]["text"], "sys")
        self.assertEqual(body["messages"][-1]["content"][0]["type"], "tool_result")
        self.assertIn("cache_control", body["tools"][-1])

    def test_cancel_stops_a_slow_request(self):
        def slow(body):
            time.sleep(3)
            return "late"
        llm = FakeLLM([slow])
        cancel = threading.Event()
        threading.Timer(0.3, cancel.set).start()
        t0 = time.time()
        with self.assertRaises(providers.Cancelled):
            providers.OpenAICompat(llm.url).chat("m", [{"role": "user", "content": "x"}], cancel=cancel)
        self.assertLess(time.time() - t0, 2.5)
        llm.close()


class AgentLoopTest(unittest.TestCase):
    def test_fix_verified_by_tests(self):
        script = [{"tools": [("read", {"path": "calc.py"})]},
                  {"tools": [("edit", {"path": "calc.py", "old": "return a - b", "new": "return a + b"})]},
                  "Fixed add() in calc.py."]
        answer, events, s, llm, root = run_agent(script, text="add() in calc.py subtracts; fix it")
        self.assertEqual(answer, "Fixed add() in calc.py.")
        with open(os.path.join(root, "calc.py")) as f:
            self.assertIn("a + b", f.read())
        v = [e for e in events if e["type"] == "verify"]
        self.assertEqual(len(v), 1)
        self.assertTrue(v[0]["ok"])
        end = [e for e in events if e["type"] == "turn_end"][0]
        self.assertEqual(end["changes"][0]["path"], "calc.py")
        # The fixed start never changes and the conversation only grows: llama.cpp re-reads nothing.
        systems = {json.dumps(r["messages"][0]) for r in llm.requests}
        self.assertEqual(len(systems), 1)
        for a, b in zip(llm.requests, llm.requests[1:]):
            self.assertEqual(b["messages"][:len(a["messages"])], a["messages"])
            self.assertEqual(a["tools"], b["tools"])
        # The first request carried the project, and the file the task names as a read already made.
        first = llm.requests[0]["messages"]
        self.assertIn("Project files (2)", first[1]["content"])
        self.assertEqual(first[2]["tool_calls"][0]["function"]["name"], "read")
        self.assertIn("return a - b", first[3]["content"])

    def test_failing_tests_send_the_agent_back(self):
        script = [{"tools": [("edit", {"path": "calc.py", "old": "return a - b", "new": "return a * b"})]},
                  "Done.",
                  {"tools": [("edit", {"path": "calc.py", "old": "return a * b", "new": "return a + b"})]},
                  "Fixed for real."]
        root = make_project(dict(CALC, **{".newal/settings.json": '{"test_after_edit": false}'}))
        answer, events, s, llm, root = run_agent(script, root=root)
        self.assertEqual(answer, "Fixed for real.")
        self.assertEqual([e["ok"] for e in events if e["type"] == "verify"], [False, True])
        self.assertIn("tests fail", llm.requests[2]["messages"][-1]["content"])

    def test_tests_run_with_the_step_that_changed_files(self):
        # The result of the tests comes with the edit: the model fixes it in its next step, and answers right after
        # the passing run (no test call of its own, no second run at the end).
        script = [{"tools": [("edit", {"path": "calc.py", "old": "return a - b", "new": "return a * b"})]},
                  {"tools": [("edit", {"path": "calc.py", "old": "return a * b", "new": "return a + b"})]},
                  "Fixed add(); the tests pass."]
        answer, events, s, llm, root = run_agent(script)
        self.assertEqual(answer, "Fixed add(); the tests pass.")
        self.assertEqual([e["ok"] for e in events if e["type"] == "verify"], [False, True])
        self.assertTrue(all(e.get("auto") for e in events if e["type"] == "verify"))
        tool_results = [m["content"] for m in s.messages if m["role"] == "tool"]
        self.assertIn("The tests ran after this change", tool_results[0])
        self.assertIn("FAILED", tool_results[0])
        self.assertIn("passed", tool_results[1])
        self.assertEqual(len(llm.requests), 3)
        # read-only work runs nothing; neither does a project without tests
        answer, events, *_ = run_agent([{"tools": [("edit", {"path": "notes.txt", "old": "a", "new": "b"})]}, "ok"],
                                       files={"notes.txt": "a\n"})
        self.assertEqual([e for e in events if e["type"] == "verify"], [])

    def test_ask_mode_denied_edit_is_not_applied(self):
        script = [{"tools": [("edit", {"path": "calc.py", "old": "return a - b", "new": "return a + b"})]}, "ok"]
        answer, events, s, llm, root = run_agent(script, mode="ask", approve=lambda r: "deny")
        with open(os.path.join(root, "calc.py")) as f:
            self.assertIn("a - b", f.read())
        tool_msg = [m for m in s.messages if m["role"] == "tool"][0]
        self.assertIn("not allowed", tool_msg["content"])
        self.assertEqual(len([e for e in events if e["type"] == "approval"]), 1)

    def test_always_allow_is_remembered(self):
        asked = []
        script = [{"tools": [("bash", {"command": "echo hi > one.txt"})]},
                  {"tools": [("bash", {"command": "echo hi > two.txt"})]},
                  {"tools": [("bash", {"command": "touch three.txt"})]}, "done"]
        answer, events, s, llm, root = run_agent(script, mode="ask", files={"x.txt": "x"},
                                                 approve=lambda r: asked.append(r["rule"]) or "always")
        self.assertEqual(asked, ["Bash(echo hi:*)", "Bash(touch three.txt:*)"])
        for f in ("one.txt", "two.txt", "three.txt"):
            self.assertTrue(os.path.exists(os.path.join(root, f)))

    def test_read_only_mode_refuses_edits(self):
        script = [{"tools": [("write", {"path": "new.py", "content": "x"})]}, "I cannot change files here."]
        answer, events, s, llm, root = run_agent(script, mode="read-only", approve=lambda r: "once")
        self.assertFalse(os.path.exists(os.path.join(root, "new.py")))
        self.assertEqual([e for e in events if e["type"] == "approval"], [])

    def test_parallel_reads_keep_order(self):
        script = [{"tools": [("read", {"path": "calc.py"}), ("read", {"path": "test_calc.py"}),
                             ("grep", {"pattern": "add"})]}, "They add numbers."]
        answer, events, s, llm, root = run_agent(script, text="what does calc do?")
        results = [m["content"] for m in s.messages if m["role"] == "tool"]
        self.assertIn("return a - b", results[0])
        self.assertIn("test_add", results[1])
        self.assertIn("calc.py:1", results[2])

    def test_subagent_reports_back(self):
        script = [{"tools": [("task", {"prompt": "Where is add defined?", "agent": "explore"})]},
                  {"tools": [("grep", {"pattern": "def add"})]},
                  "add is defined in calc.py:1",
                  "It is in calc.py."]
        answer, events, s, llm, root = run_agent(script, text="where is add?")
        self.assertEqual(answer, "It is in calc.py.")
        report = [m for m in s.messages if m["role"] == "tool"][0]["content"]
        self.assertIn("add is defined in calc.py:1", report)
        self.assertTrue(any(e["type"] == "subagent_end" for e in events))
        sub_req = llm.requests[1]
        self.assertIn("explore a code base", sub_req["messages"][0]["content"])
        self.assertEqual(sorted(t["function"]["name"] for t in sub_req["tools"]), ["bash", "glob", "grep", "read"])

    def test_hooks_in_the_loop(self):
        root = make_project(CALC)
        os.makedirs(os.path.join(root, ".newal"))
        with open(os.path.join(root, ".newal", "settings.json"), "w") as f:
            json.dump({"hooks": {
                "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command",
                                                            "command": "echo 'no shell today' >&2; exit 2"}]}],
                "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "echo 'context from hook'"}]}]}}, f)
        script = [{"tools": [("bash", {"command": "ls"})]}, "ok"]
        answer, events, s, llm, root = run_agent(script, root=root)
        self.assertIn("context from hook", llm.requests[0]["messages"][1]["content"])
        tool_msg = [m for m in s.messages if m["role"] == "tool"][0]
        self.assertIn("no shell today", tool_msg["content"])

    def test_goal_keeps_working_until_done(self):
        script = ["first part done", "CONTINUE: the tests are not run", {"tools": [("bash", {"command": "echo ran"})]},
                  "ran them", "DONE"]
        answer, events, s, llm, root = run_agent(script, goal="tests were run", files={"a.txt": "a"})
        self.assertEqual(answer, "ran them")
        self.assertEqual(s.goal, "")
        self.assertEqual([e["done"] for e in events if e["type"] == "goal_check"], [False, True])

    def test_compaction_and_resume(self):
        answer, events, s, llm, root = run_agent(["answer one"], files={"a.txt": "a"})
        llm2 = FakeLLM(["SUMMARY: user asked one thing."])
        ag = agentmod.Agent(s, client=fake_client(llm2))
        s.messages.append({"role": "assistant", "content": "extra"})
        s.messages.append({"role": "user", "content": "more"})
        self.assertTrue(ag._maybe_compact(force=True))
        llm2.close()
        self.assertIn("SUMMARY", s.messages[0]["content"])
        self.assertIn("<context>", s.messages[0]["content"])
        s.save_meta()
        again = session.Session.load(s.id)
        self.assertEqual(again.messages, s.messages)
        self.assertEqual(again.system, s.system)
        self.assertIn(s.id, [x["id"] for x in session.listing()])

    def test_interrupt(self):
        def slow(body):
            time.sleep(4)
            return "late"
        llm = FakeLLM([slow])
        root = make_project(CALC)
        s = session.Session(root)
        ag = agentmod.Agent(s, client=fake_client(llm))
        threading.Timer(0.4, ag.cancel.set).start()
        t0 = time.time()
        answer = ag.run("do something slow")
        self.assertEqual(answer, "(interrupted)")
        self.assertLess(time.time() - t0, 3)
        llm.close()

    def test_local_model_thinks_only_when_stuck(self):
        llm = FakeLLM([{"tools": [("edit", {"path": "calc.py", "old": "return a - b", "new": "return a * b"})]},
                       {"tools": [("bash", {"command": "exit 1"})]},
                       {"tools": [("edit", {"path": "calc.py", "old": "return a * b", "new": "return a + b"})]},
                       {"tools": [("bash", {"command": "echo ok"})]},
                       "fixed"])
        root = make_project(dict(CALC, **{".newal/settings.json": '{"test_after_edit": false}'}))
        spec = {"id": "fake-local", "name": "fake", "provider": "local"}
        client = models.Client(spec, providers.LlamaCpp(llm.url), "fake")
        ag = agentmod.Agent(session.Session(root), client=client)
        self.assertEqual(ag.run("fix add"), "fixed")
        llm.close()
        thinking = [r["chat_template_kwargs"]["enable_thinking"] for r in llm.requests]
        self.assertEqual(thinking, [False, False, True, False, False])
        self.assertEqual(llm.requests[2]["thinking_budget_tokens"], 384)
        self.assertTrue(all(r["cache_prompt"] and r["parallel_tool_calls"] for r in llm.requests))

    def test_local_model_thinks_before_answering_a_question(self):
        llm = FakeLLM(["It adds (well, it subtracts: a bug)."])
        spec = {"id": "fake-local", "name": "fake", "provider": "local"}
        client = models.Client(spec, providers.LlamaCpp(llm.url), "fake")
        ag = agentmod.Agent(session.Session(make_project(CALC)), client=client)
        ag.run("What does add() in calc.py do?")
        llm.close()
        self.assertTrue(llm.requests[0]["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual(llm.requests[0]["thinking_budget_tokens"], 256)

    def test_local_model_stopped_by_the_pool_is_restarted(self):
        # A sub-agent's model took the RAM, so the pool stopped this one: its next request starts it again.
        llm = FakeLLM(["done"])

        class FakeServer:
            path, ctx, mtp, speculative, threads, used = "/m.gguf", 32768, False, "", 4, 0

            def __init__(self, url, alive):
                self.url, self._alive = url, alive

            def alive(self):
                return self._alive

            def take_slot(self, owner):
                return 0
        dead, live = FakeServer("http://127.0.0.1:9", False), FakeServer(llm.url[:-len("/v1")], True)
        client = models.Client({"id": "fake-local", "name": "fake", "provider": "local"},
                               providers.LlamaCpp(dead.url + "/v1"), "fake", dead)
        started = []
        orig = runtime.pool.get
        runtime.pool.get = lambda path, **kw: started.append(path) or live
        try:
            ag = agentmod.Agent(session.Session(make_project(CALC)), client=client)
            ag.warm = lambda: 0
            self.assertEqual(ag.run("say done"), "done")
        finally:
            runtime.pool.get = orig
            llm.close()
        self.assertEqual(started, ["/m.gguf"])
        self.assertIs(client.server, live)

    def test_anthropic_model_runs_the_loop(self):
        llm = FakeLLM([{"tools": [("edit", {"path": "calc.py", "old": "return a - b", "new": "return a + b"})]},
                       "fixed"])
        root = make_project(CALC)
        s = session.Session(root)
        ag = agentmod.Agent(s, client=fake_client(llm, anthropic=True))
        self.assertEqual(ag.run("fix add"), "fixed")
        llm.close()
        with open(os.path.join(root, "calc.py")) as f:
            self.assertIn("a + b", f.read())


class ContextTest(unittest.TestCase):
    def test_first_message_brings_the_named_code(self):
        root = make_project({
            "app/main.py": "from app.store import load\n\n\ndef low_stock(path):\n    return [n for n, q in load(path).items() if q < 5]\n",
            "app/store.py": "import json\n\n\ndef load(path):\n    return json.load(open(path))\n",
            "app/__init__.py": "",
            "docs/notes.md": "low_stock is used by the report\n",
            "tests/test_main.py": "from app.main import low_stock\n",
        })
        from newal_code import context
        s = session.Session(root)
        ctx = agentmod.ToolContext(agentmod.Agent(s))
        msg = context.first_message(ctx, "low_stock() crashes when a quantity is a string")
        self.assertIn('<file path="app/main.py"', msg)          # where it is defined
        self.assertIn('<file path="app/store.py"', msg)         # what that file imports
        self.assertIn("Where \\b(low_stock)\\b appear:", msg)
        self.assertIn("tests/test_main.py:1:", msg)
        self.assertIn("Tests: ", msg)
        self.assertNotIn("<file path=\"docs/notes.md\"", msg)

    def test_prefix_ends_at_a_special_token(self):
        class FakeServer:
            url = "http://x"
        a = agentmod.Agent(session.Session(make_project({})))
        orig = providers.post_json
        try:
            providers.post_json = lambda url, body, timeout=0: {
                "prompt": "<|im_start|>system\nS<|im_end|>\n<|im_start|>user\n" + body["messages"][1]["content"] +
                          "<|im_end|>\n<|im_start|>assistant\n"}
            client = type("C", (), {"server": FakeServer})()
            self.assertEqual(a._prefix_text(client, "S", []), "<|im_start|>system\nS<|im_end|>\n<|im_start|>")
        finally:
            providers.post_json = orig

    def test_project_context_is_read_while_the_user_types(self):
        class FakeServer:
            url = "http://x"
        root = make_project(dict(CALC, **{"AGENTS.md": "Use tabs.\n"}))
        a = agentmod.Agent(session.Session(root))
        sent = []

        def post(url, body, timeout=0):
            sent.append((url, body))
            if url.endswith("/apply-template"):
                return {"prompt": "<|im_start|>system\nS<|im_end|>\n<|im_start|>user\n" + body["messages"][1]["content"]
                                  + "<|im_end|>\n<|im_start|>assistant\n"}
            return {}
        orig = providers.post_json
        try:
            providers.post_json = post
            a._warm_context(type("C", (), {"server": FakeServer})(), "S", [], 1)
        finally:
            providers.post_json = orig
        url, body = sent[-1]
        self.assertTrue(url.endswith("/completion"))
        self.assertEqual((body["n_predict"], body["id_slot"]), (0, 1))
        self.assertTrue(body["prompt"].endswith("</context>"))
        self.assertIn("Use tabs.", body["prompt"])
        # The first request's message starts with exactly the text that was read in advance.
        content, _ = a._user_content("fix add in calc.py", [])
        self.assertTrue(("<|im_start|>system\nS<|im_end|>\n<|im_start|>user\n" + content).startswith(body["prompt"]))


class ServerTest(unittest.TestCase):
    """The web app's API end to end: a thread, a turn with an approval, the event stream, review, undo, commands."""

    @classmethod
    def setUpClass(cls):
        from newal_code import server
        cls.llm = FakeLLM([])
        settings.save({"models": {"fake": {"provider": "openai", "base_url": cls.llm.url, "model": "fake"}},
                       "model": "fake"})
        cls.httpd, cls.login = server.serve(0)
        cls.url, cls.key = cls.httpd.base_url, cls.httpd.key
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.events = []
        threading.Thread(target=cls._listen, daemon=True).start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.llm.close()

    @classmethod
    def _listen(cls):
        import urllib.request
        req = urllib.request.Request(cls.url + "api/events", headers={"X-NewAl-Key": cls.key})
        with urllib.request.urlopen(req, timeout=120) as r:
            for raw in r:
                line = raw.decode().strip()
                if line.startswith("data:"):
                    cls.events.append(json.loads(line[5:]))

    def call(self, path, body=None):
        import urllib.request
        req = urllib.request.Request(self.url + path.lstrip("/"), json.dumps(body).encode() if body is not None else None,
                                     {"Content-Type": "application/json", "Authorization": "Bearer " + self.key})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    def raw(self, path, headers=None, host=None):
        """(status, headers, body) of a GET with exactly these headers (no key unless given)."""
        import http.client
        port = int(self.url.rsplit(":", 1)[1].strip("/"))
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        h = dict(headers or {})
        if host:
            h["Host"] = host
        c.request("GET", path, headers=h)
        r = c.getresponse()
        out = (r.status, dict(r.getheaders()), r.read())
        c.close()
        return out

    def test_check_everything(self):
        d = self.call("/api/doctor?quick=1")
        keys = [c["key"] for c in d["checks"]]
        for k in ("model", "llama", "memory", "git", "shell", "access", "github"):
            self.assertIn(k, keys)
        model = next(c for c in d["checks"] if c["key"] == "model")
        self.assertIs(model["ok"], True, model)              # the fake API model needs no key
        self.assertTrue(all(c["ok"] in (True, False, None) for c in d["checks"]))
        self.assertIn("NewAl Code", d["report"])

    def test_speed_test_measures_the_model_in_use(self):
        r = self.call("/api/speedtest", {})
        self.assertEqual(r["model"], "fake")
        self.assertFalse(r["local"])
        for k in ("load_s", "first_token_s", "read_tps", "write_tps", "prompt_tokens", "output_tokens"):
            self.assertGreaterEqual(r[k], 0, k)
        self.assertIn("Speed of fake (an API)", r["report"])

    def test_a_projects_branch_before_any_thread(self):
        root = make_project(CALC)
        self.assertIsNone(self.call("/api/git?root=" + urllib.parse.quote(root))["git"])
        git(root, "init", "-q", "-b", "trunk")
        self.assertEqual(self.call("/api/git?root=" + urllib.parse.quote(root))["git"], {"branch": "trunk"})

    def test_builtin_plugin_one_tap_then_its_command_runs_at_once(self):
        root = make_project({"a.txt": "x"})
        q = urllib.parse.quote(root)
        m = self.call("/api/extensions?root=" + q)["marketplaces"][0]
        self.assertEqual((m["name"], m["builtin"]), ("newal", True))
        self.assertEqual(self.call("/api/plugins", {"action": "install", "source": "system@newal", "root": root})["name"],
                         "system")
        try:
            cmds = {c["name"]: c for c in self.call("/api/commands?root=" + q)}
            self.assertTrue(cmds["sysinfo"]["instant"])
            sid = self.call("/api/sessions", {"root": root, "warm": False})["id"]
            r = self.call("/api/sessions/%s/send" % sid, {"text": "/ports 1"})
            self.assertEqual((r.get("started"), r["output"], r["code"]), (None, True, 0), r)     # no turn started
            self.assertIn("free, nothing listens on it", r["reply"])
        finally:
            self.call("/api/plugins", {"action": "remove", "source": "system", "root": root})

    def test_new_folder_gguf_files_and_threads_listed_from_their_first_message(self):
        top = tempfile.mkdtemp()
        made = self.call("/api/mkdir", {"parent": top, "name": "my app"})["path"]
        self.assertTrue(os.path.isdir(made))
        self.assertEqual(made, os.path.join(top, "my app"))
        for bad in ("../x", "a/b", "", ".."):
            with self.assertRaises(urllib.error.HTTPError) as e:
                self.call("/api/mkdir", {"parent": top, "name": bad})
            self.assertEqual(e.exception.code, 400)
        RamTest().fake_gguf(os.path.join(top, "Phone-Model.gguf"), size_mb=1)
        b = self.call("/api/browse?files=gguf&path=" + urllib.request.quote(top))
        self.assertEqual(b["dirs"], ["my app"])
        self.assertEqual([(f["name"], f["size"]) for f in b["files"]], [("Phone-Model.gguf", 1024 * 1024)])
        self.assertNotIn("files", self.call("/api/browse?path=" + urllib.request.quote(top)))
        # a thread opened while its first message is typed (the app warms the model meanwhile) is not listed yet
        sid = self.call("/api/sessions", {"root": made, "warm": False})["id"]
        self.assertNotIn(sid, [x["id"] for x in self.call("/api/sessions")])
        self.assertEqual(self.call("/api/sessions/" + sid)["meta"]["id"], sid)

    def test_plugins_and_marketplaces_from_the_extensions_page(self):
        from newal_code import plugins
        kit = make_project({".claude-plugin/plugin.json": json.dumps({"name": "hello-kit", "description": "Greets"}),
                            "commands/hello.md": "Say hello to $ARGUMENTS\n"})
        market = make_project({
            ".claude-plugin/marketplace.json": json.dumps({"name": "phone-tools", "plugins": [
                {"name": "fmt", "source": "./plugins/fmt", "description": "Formatting"}]}),
            "plugins/fmt/.claude-plugin/plugin.json": json.dumps({"name": "fmt"}),
            "plugins/fmt/commands/fmt.md": "Format $ARGUMENTS\n"})
        root = make_project(CALC)
        try:
            self.assertEqual(self.call("/api/plugins", {"action": "install", "source": kit})["name"], "hello-kit")
            m = self.call("/api/plugins", {"action": "marketplace_add", "source": market})
            self.assertEqual((m["name"], m["plugins"]), ("phone-tools", ["fmt"]))
            self.call("/api/plugins", {"action": "install", "source": "fmt@phone-tools"})
            ext = self.call("/api/extensions?root=" + urllib.request.quote(root))
            self.assertEqual(sorted(p["name"] for p in ext["plugins"]), ["fmt", "hello-kit"])
            self.assertEqual([x["name"] for x in ext["marketplaces"]], ["newal", "phone-tools"])
            self.assertIn("hello", [c["name"] for c in ext["commands"]])
            with self.assertRaises(urllib.error.HTTPError) as e:             # already there
                self.call("/api/plugins", {"action": "install", "source": kit})
            self.assertEqual(e.exception.code, 400)
            self.call("/api/plugins", {"action": "remove", "source": "hello-kit", "root": root})
            self.call("/api/plugins", {"action": "marketplace_remove", "source": "phone-tools"})
            ext = self.call("/api/extensions?root=" + urllib.request.quote(root))
            self.assertEqual([p["name"] for p in ext["plugins"]], ["fmt"])
            self.assertEqual([x["name"] for x in ext["marketplaces"]], ["newal"])      # NewAl's own stays
        finally:
            for name in ("fmt", "hello-kit"):
                try:
                    plugins.remove(name)
                except ValueError:
                    pass

    def test_termux_link_command_works_once(self):
        cmd = self.call("/api/termux/link", {})["command"]
        token = cmd.split("once=")[1].split("'")[0]
        status, _, body = self.raw("/termux/setup?once=" + token)
        self.assertEqual(status, 200)
        self.assertIn(b"newal-termux", body)
        self.assertIn(self.key.encode(), body)                  # the key reaches Termux's own files only
        self.assertEqual(self.raw("/termux/setup?once=" + token)[0], 401)       # once
        self.assertEqual(self.raw("/termux/setup?once=made-up")[0], 401)
        self.assertEqual(self.raw("/termux/app.zip")[0], 401)
        status, _, data = self.raw("/termux/app.zip", {"X-NewAl-Key": self.key})
        import io
        import zipfile
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        self.assertIn("newal_code/__main__.py", names)
        self.assertIn("newal_code/ui/app.js", names)
        self.assertFalse([n for n in names if n.endswith(".pyc")])

    def test_termux_setup_script_end_to_end(self):
        """The script Termux runs, run here: a made-up Termux (its paths, pkg, a HOME) with this machine's Python.
        It fetches NewAl Code from the app's server, keeps the key, adds the phone's model, and starts NewAl Code on
        its own port, answering with the app's key."""
        if os.name == "nt" or not shutil.which("bash") or not shutil.which("curl"):
            self.skipTest("bash and curl")
        from unittest import mock
        from newal_code import termux
        fake = tempfile.mkdtemp(prefix="nc-termux-")
        self.addCleanup(shutil.rmtree, fake, True)
        home, prefix = os.path.join(fake, "home"), os.path.join(fake, "usr")
        os.makedirs(os.path.join(prefix, "bin"))
        os.makedirs(home)
        os.symlink(sys.executable, os.path.join(prefix, "bin", "python"))
        with open(os.path.join(prefix, "bin", "pkg"), "w") as f:
            f.write("#!/bin/sh\necho pkg \"$@\" >> \"$HOME/pkg.log\"\n")
        os.chmod(os.path.join(prefix, "bin", "pkg"), 0o755)
        port = socket.socket()
        port.bind(("127.0.0.1", 0))
        free = port.getsockname()[1]
        port.close()
        with mock.patch.object(termux, "PORT", free):
            script = termux.setup_script(int(self.url.rsplit(":", 1)[1].strip("/")), self.key)
        script = script.replace("/data/data/com.termux/files/usr/bin/bash", shutil.which("bash"))
        script = script.replace('curl -fsSL -H "X-NewAl-Key: $KEY" "$APP/termux/app.zip"',
                                'curl -fsSL --noproxy "*" -H "X-NewAl-Key: $KEY" "$APP/termux/app.zip"')
        script = script.replace('curl -s -o /dev/null', 'curl -s --noproxy "*" -o /dev/null')
        env = {"HOME": home, "PREFIX": prefix, "PATH": os.path.join(prefix, "bin") + os.pathsep + os.environ["PATH"],
               "LANG": "C.UTF-8"}
        self.addCleanup(lambda: subprocess.run(["pkill", "-f", "newal_code app --port %d" % free]))
        r = subprocess.run(["bash"], input=script, env=env, capture_output=True, text=True, timeout=120)   # curl | bash
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("runs in Termux", r.stdout)
        self.assertTrue(os.path.isfile(os.path.join(home, ".newal-code", "termux", "app", "newal_code", "server.py")))
        with open(os.path.join(home, ".newal-code", "phone.json")) as f:
            phone = json.load(f)
        self.assertEqual(phone["key"], self.key)
        self.assertEqual(os.stat(os.path.join(home, ".newal-code", "phone.json")).st_mode & 0o777, 0o600)
        with open(os.path.join(home, ".newal-code", "config.json")) as f:
            cfg = json.load(f)
        self.assertEqual(cfg["model"], "phone")
        self.assertTrue(cfg["models"]["phone"]["base_url"].endswith("/v1"))
        with open(os.path.join(home, ".termux", "termux.properties")) as f:
            self.assertIn("allow-external-apps = true", f.read())
        import urllib.request
        req = urllib.request.Request("http://127.0.0.1:%d/api/state" % free, headers={"X-NewAl-Key": self.key})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=20) as resp:
            self.assertEqual(json.loads(resp.read())["name"], "NewAl Code")
        r2 = subprocess.run(["bash", "-c", "newal-termux status; newal-termux stop"], env=env, capture_output=True,
                            text=True, timeout=30)
        self.assertIn("running", r2.stdout)

    def test_model_proxy_streams_the_local_model(self):
        """/v1/chat/completions (what NewAl Code in Termux uses as the phone's model) answers from the local model."""
        from unittest import mock
        from newal_code import server as srv
        llm = FakeLLM(["hello from the phone model"])
        self.addCleanup(llm.close)

        class Srv:
            url = llm.base
            used = 0

        client = models.Client({"id": "p", "provider": "local"}, None, "model.gguf", Srv())
        with mock.patch.object(srv, "local_model", lambda: {"id": "p", "provider": "local", "file": "x"}), \
                mock.patch.object(srv.models, "connect", lambda spec: client):
            import urllib.request
            body = json.dumps({"model": "phone", "stream": True,
                               "messages": [{"role": "user", "content": "hi"}]}).encode()
            req = urllib.request.Request(self.url + "v1/chat/completions", body,
                                         {"Content-Type": "application/json", "Authorization": "Bearer " + self.key})
            with urllib.request.urlopen(req, timeout=30) as r:
                text = r.read().decode()
        self.assertIn("hello from the phone model", "".join(
            json.loads(l[5:])["choices"][0]["delta"].get("content") or "" for l in text.splitlines()
            if l.startswith("data:") and l.strip() != "data: [DONE]"))
        self.assertEqual(llm.requests[-1]["model"], "model.gguf")
        req = urllib.request.Request(self.url + "v1/chat/completions", body, {"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(e.exception.code, 401)

    def test_the_key_and_the_host(self):
        """Only whoever has the key gets the API; the interface's files are open; the key in the address becomes a
        cookie; a request naming another host (a web page rebinding its name to 127.0.0.1) is refused."""
        status, _, body = self.raw("/api/state")
        self.assertEqual(status, 401)
        self.assertTrue(json.loads(body)["key_needed"])
        self.assertEqual(self.raw("/api/state", {"X-NewAl-Key": "wrong"})[0], 401)
        self.assertEqual(self.raw("/api/state", {"X-NewAl-Key": self.key})[0], 200)
        self.assertEqual(self.raw("/api/state", {"Authorization": "Bearer " + self.key})[0], 200)
        self.assertEqual(self.raw("/api/state?key=" + self.key)[0], 200)
        self.assertEqual(self.raw("/app.js")[0], 200)
        status, headers, _ = self.raw("/?key=" + self.key)
        self.assertEqual(status, 302)
        cookie = headers.get("Set-Cookie", "")
        self.assertIn("newal_key=" + self.key, cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertEqual(headers.get("Location"), "/")
        # a folder to open (Explorer's "Open with NewAl Code") stays in the address
        status, headers, _ = self.raw("/?key=" + self.key + "&root=" + urllib.request.quote("/tmp/my app"))
        self.assertEqual((status, headers.get("Location")), (302, "/?root=%2Ftmp%2Fmy+app"))
        self.assertEqual(self.raw("/api/state", {"Cookie": "a=b; newal_key=" + self.key})[0], 200)
        self.assertEqual(self.raw("/api/state", {"X-NewAl-Key": self.key}, host="evil.example:%s"
                                  % self.url.rsplit(":", 1)[1].strip("/"))[0], 403)
        self.assertTrue(self.login.endswith("?key=" + self.key))

    def wait_for(self, pred, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            hit = [e for e in list(self.events) if pred(e)]
            if hit:
                return hit[0]
            time.sleep(0.05)
        self.fail("event not seen")

    def test_thread_turn_approval_review_undo(self):
        root = make_project(CALC)
        util_git = lambda *a: __import__("subprocess").run(["git", *a], cwd=root, capture_output=True)  # noqa: E731
        util_git("init", "-q")
        util_git("add", "-A")
        util_git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
        sid = self.call("/api/sessions", {"root": root, "mode": "ask", "warm": False})["id"]
        self.llm.script[:] = [{"tools": [("edit", {"path": "calc.py", "old": "return a - b", "new": "return a + b"})]},
                              "Fixed add."]
        self.assertTrue(self.call("/api/sessions/%s/send" % sid, {"text": "fix add in calc.py"})["started"])
        ap = self.wait_for(lambda e: e.get("type") == "approval" and e.get("session") == sid)
        self.assertEqual(ap["tool"], "edit")
        self.assertTrue(self.call("/api/approvals/%s" % ap["id"], {"answer": "once"})["ok"])
        end = self.wait_for(lambda e: e.get("type") == "turn_end" and e.get("session") == sid)
        self.assertEqual(end["answer"], "Fixed add.")
        self.assertEqual(end["changes"][0]["path"], "calc.py")
        ch = self.call("/api/sessions/%s/changes" % sid)
        self.assertIn("+    return a + b", ch["changes"][0]["diff"])
        self.assertIsNotNone(ch["git"])
        meta = self.call("/api/sessions/%s" % sid)
        self.assertEqual(meta["meta"]["title"], "fix add in calc.py")
        self.assertTrue(any(e["type"] == "tool_end" for e in meta["events"]))
        origin = tempfile.mkdtemp()
        util_git("init", "-q", "--bare", origin)
        util_git("remote", "add", "origin", origin)
        # "Commit and create PR" from master: a branch of its own, committed and pushed (no gh here: no PR itself)
        r = self.call("/api/sessions/%s/commit" % sid, {"message": "Fix add", "then": "pr"})
        self.assertTrue(r["ok"] and r["pushed"], r)
        pushed = __import__("subprocess").run(["git", "branch", "--list"], cwd=origin, capture_output=True, text=True)
        self.assertIn("newal/fix-add", pushed.stdout)
        self.call("/api/sessions/%s/undo" % sid, {})
        with open(os.path.join(root, "calc.py")) as f:
            self.assertIn("a - b", f.read())

    def test_worktree_thread_apply_and_discard(self):
        import subprocess
        root = make_project(CALC)
        for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "init"]):
            subprocess.run(["git", *args], cwd=root, capture_output=True)
        d = self.call("/api/sessions", {"root": root, "worktree": True, "warm": False})
        sid, meta = d["id"], d["meta"]
        self.assertTrue(meta["worktree"])
        self.assertNotEqual(os.path.realpath(meta["root"]), os.path.realpath(root))
        self.assertEqual(meta["origin"], root)
        self.llm.script[:] = [{"tools": [("edit", {"path": "calc.py", "old": "return a - b", "new": "return a + b"})]},
                              "Fixed in the worktree."]
        self.call("/api/sessions/%s/send" % sid, {"text": "fix add"})
        self.wait_for(lambda e: e.get("type") == "turn_end" and e.get("session") == sid)
        with open(os.path.join(root, "calc.py")) as f:
            self.assertIn("a - b", f.read())              # the project itself is untouched
        with open(os.path.join(root, "calc.py"), "rb") as f:
            original = f.read()
        with open(os.path.join(root, "calc.py"), "wb") as f:
            f.write(b"# changed meanwhile\n" + original)
        r = self.call("/api/sessions/%s/apply" % sid, {})
        self.assertFalse(r.get("ok"), r)                  # nothing the user changed meanwhile is overwritten
        self.assertIn("calc.py", r["error"])
        with open(os.path.join(root, "calc.py"), "wb") as f:    # a Windows checkout: CRLF, the same content
            f.write(original.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        r = self.call("/api/sessions/%s/apply" % sid, {})
        self.assertTrue(r.get("ok"), r)
        with open(os.path.join(root, "calc.py")) as f:
            self.assertIn("a + b", f.read())
        self.assertIn(sid, [x["id"] for x in self.call("/api/sessions?root=" + root)])
        self.assertTrue(self.call("/api/sessions/%s/discard" % sid, {})["ok"])
        self.assertFalse(os.path.exists(meta["root"]))

    def test_slash_commands_and_notes(self):
        root = make_project(CALC)
        sid = self.call("/api/sessions", {"root": root, "warm": False})["id"]
        self.assertIn("/review", self.call("/api/sessions/%s/send" % sid, {"text": "/help"})["reply"])
        self.assertEqual(self.call("/api/sessions/%s/send" % sid, {"text": "/mode plan"})["reply"], "Mode: read-only")
        self.assertIn("No changes", self.call("/api/sessions/%s/send" % sid, {"text": "/diff"})["reply"])
        self.assertIn("AGENTS.md", self.call("/api/sessions/%s/send" % sid, {"text": "# always use tabs"})["reply"])
        with open(os.path.join(root, "AGENTS.md")) as f:
            self.assertIn("- always use tabs", f.read())
        perms = self.call("/api/sessions/%s/send" % sid, {"text": "/permissions"})["reply"]
        self.assertIn("Mode: read-only", perms)
        self.assertIn("deny:", perms)
        other = tempfile.mkdtemp()
        self.assertIn("Added", self.call("/api/sessions/%s/send" % sid, {"text": "/add-dir " + other})["reply"])
        self.assertIn(other, self.call("/api/sessions/%s" % sid)["meta"]["dirs"])
        r = self.call("/api/sessions/%s/send" % sid, {"text": "/new"})
        self.assertTrue(r.get("new"))
        self.assertNotEqual(r["session"], sid)
        names = [c["name"] for c in self.call("/api/commands?root=" + root)]
        self.assertIn("goal", names)
        state = self.call("/api/state")
        self.assertIn(root, state["projects"])
        models_ = self.call("/api/models")
        self.assertIn("qwen3.5-4b", [m["id"] for m in models_["models"]])

    def test_notification_and_session_end_hooks(self):
        root = make_project(CALC)
        marks = tempfile.mkdtemp().replace("\\", "/")
        os.makedirs(os.path.join(root, ".newal"))
        with open(os.path.join(root, ".newal", "settings.json"), "w") as f:
            json.dump({"hooks": {e: [{"hooks": [{"type": "command", "command": 'echo x >> "%s/%s"' % (marks, e)}]}]
                                 for e in ("Notification", "SessionEnd")}}, f)
        sid = self.call("/api/sessions", {"root": root, "mode": "ask", "warm": False})["id"]
        self.llm.script[:] = [{"tools": [("edit", {"path": "calc.py", "old": "return a - b", "new": "return a + b"})]},
                              "Fixed add."]
        self.call("/api/sessions/%s/send" % sid, {"text": "fix add in calc.py"})
        ap = self.wait_for(lambda e: e.get("type") == "approval" and e.get("session") == sid)
        end = time.time() + 20
        while not os.path.exists(os.path.join(marks, "Notification")) and time.time() < end:
            time.sleep(0.05)
        self.assertTrue(os.path.exists(os.path.join(marks, "Notification")))      # the user is needed: told
        self.call("/api/approvals/%s" % ap["id"], {"answer": "deny"})
        self.wait_for(lambda e: e.get("type") == "turn_end" and e.get("session") == sid)
        self.call("/api/sessions/delete", {"id": sid})
        self.assertTrue(os.path.exists(os.path.join(marks, "SessionEnd")))


class CliTest(unittest.TestCase):
    def test_exec_json_and_terminal_printer(self):
        import io
        import subprocess
        llm = FakeLLM([{"tools": [("edit", {"path": "calc.py", "old": "return a - b", "new": "return a + b"})]},
                       "fixed it"])
        root = make_project(CALC)
        env = dict(os.environ)
        known = dict(settings.user().get("models") or {})
        known["fake2"] = {"provider": "openai", "base_url": llm.url, "model": "fake"}
        settings.save({"models": known})
        last = os.path.join(tempfile.mkdtemp(), "answer.txt")
        p = subprocess.run([sys.executable, "-m", "newal_code", "exec", "fix add", "--model", "fake2", "--json",
                            "--cd", root, "-o", last, "--max-steps", "5"], cwd=os.path.dirname(HERE),
                           capture_output=True, text=True, timeout=120, env=env)
        llm.close()
        self.assertEqual(p.returncode, 0, p.stderr[-2000:])
        with open(last) as f:
            self.assertEqual(f.read().strip(), "fixed it")
        events = [json.loads(l) for l in p.stdout.splitlines() if l.startswith("{")]
        kinds = [e["type"] for e in events]
        self.assertIn("tool_end", kinds)
        self.assertEqual(events[-1]["type"], "turn_end")
        self.assertEqual(events[-1]["answer"], "fixed it")
        with open(os.path.join(root, "calc.py")) as f:
            self.assertIn("a + b", f.read())
        from newal_code import tui
        out = io.StringIO()
        pr = tui.Printer(out)
        for e in events:
            pr.event(e)
        self.assertIn("• Edited", out.getvalue())
        self.assertIn("Worked for", out.getvalue())


class McpTest(unittest.TestCase):
    def test_stdio_server(self):
        root = make_project({})
        server = os.path.join(root, "srv.py")
        with open(server, "w") as f:
            f.write(textwrap.dedent('''\
                import json, sys
                for line in sys.stdin:
                    m = json.loads(line)
                    if "id" not in m:
                        continue
                    if m["method"] == "initialize":
                        r = {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "t"}}
                    elif m["method"] == "tools/list":
                        r = {"tools": [{"name": "shout", "description": "upper-case",
                                        "inputSchema": {"type": "object", "properties": {"s": {"type": "string"}}}}]}
                    elif m["method"] == "tools/call":
                        r = {"content": [{"type": "text", "text": m["params"]["arguments"]["s"].upper()}]}
                    print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}), flush=True)
                '''))
        with open(os.path.join(root, ".mcp.json"), "w") as f:
            json.dump({"mcpServers": {"t": {"command": sys.executable, "args": [server]}}}, f)
        m = mcp.Manager(root)
        names = [d["function"]["name"] for d in m.schemas()]
        self.assertEqual(names, ["mcp__t__shout"])
        self.assertEqual(m.call("mcp__t__shout", {"s": "hi"}), "HI")
        m.stop_all()


if __name__ == "__main__":
    unittest.main()
