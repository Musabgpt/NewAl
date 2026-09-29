"""Tests for NewAl Code (desktop/newal_code): tools, permissions, extensions, hooks, MCP, RAM planning, providers and
the agent loop end to end against a scripted model (tests/fake_llm.py)."""

import json
import os
import shutil
import struct
import sys
import tempfile
import textwrap
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
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


def fake_client(llm, anthropic=False):
    if anthropic:
        spec = {"id": "fake-anthropic", "name": "fake", "provider": "anthropic"}
        return models.Client(spec, providers.Anthropic("k", llm.base), "fake")
    spec = {"id": "fake", "name": "fake", "provider": "openai"}
    return models.Client(spec, providers.OpenAICompat(llm.url), "fake")


def run_agent(script, files=None, mode="auto-edit", approve=lambda r: "once", goal="", text="do it", root=None,
              **kw):
    llm = FakeLLM(script)
    root = root or make_project(files or CALC)
    s = session.Session(root, mode=mode)
    s.goal = goal
    events = []
    ag = agentmod.Agent(s, emit=events.append, approve=approve, client=fake_client(llm), **kw)
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
        self.assertEqual(meta["exit"], 124)
        text, meta = tools.call(c, "bash", {"command": "echo started; sleep 30", "background": True})
        self.assertIn("job1", text)
        text, _ = tools.call(c, "job", {"id": "job1"})
        self.assertIn("running", text)
        tools.call(c, "job", {"id": "job1", "action": "stop"})

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


class PermissionsTest(unittest.TestCase):
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
    """Linux only: commands write only inside the project (auto-edit) or nowhere (read-only), via Landlock."""

    def setUp(self):
        from newal_code import sandbox
        if not sandbox.abi():
            self.skipTest("no Landlock here")
        # Not under /tmp: temp folders are always writable in the sandbox.
        self.root = tempfile.mkdtemp(prefix=".nc-sandbox-", dir=HERE)
        self.addCleanup(shutil.rmtree, self.root, True)
        self.outside = tempfile.mkdtemp(prefix=".nc-outside-", dir=HERE)
        self.addCleanup(shutil.rmtree, self.outside, True)

    def run_in(self, mode, command, dirs=()):
        s = session.Session(self.root, mode=mode)
        s.dirs = list(dirs)
        ctx = agentmod.ToolContext(agentmod.Agent(s))
        return tools.call(ctx, "bash", {"command": command})[1]

    def test_added_folder_is_writable_and_inside(self):
        m = self.run_in("auto-edit", "echo b > %s/out.txt" % self.outside, dirs=[self.outside])
        self.assertTrue(m["sandboxed"])
        self.assertEqual(m["exit"], 0, m)
        self.assertTrue(os.path.exists(os.path.join(self.outside, "out.txt")))
        s = session.Session(self.root)
        s.dirs = [self.outside]
        ctx = agentmod.ToolContext(agentmod.Agent(s))
        self.assertTrue(tools.inside(ctx, os.path.join(self.outside, "x.py")))       # edits there need no approval
        self.assertFalse(tools.inside(ctx, os.path.join(HERE, "x.py")))

    def test_auto_edit_writes_only_in_the_project(self):
        m = self.run_in("auto-edit", "echo a > in.txt && echo b > %s/out.txt" % self.outside)
        self.assertTrue(m["sandboxed"])
        self.assertNotEqual(m["exit"], 0)
        self.assertTrue(os.path.exists(os.path.join(self.root, "in.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.outside, "out.txt")))

    def test_read_only_writes_nothing_and_full_auto_is_free(self):
        m = self.run_in("read-only", "echo a > in.txt")
        self.assertNotEqual(m["exit"], 0)
        self.assertFalse(os.path.exists(os.path.join(self.root, "in.txt")))
        m = self.run_in("full-auto", "echo b > %s/out.txt" % self.outside)
        self.assertFalse(m["sandboxed"])
        self.assertTrue(os.path.exists(os.path.join(self.outside, "out.txt")))
        d = permissions.decide("read-only", "bash", "exec", {"command": "make"}, self.root, {}, sandboxed=True)
        self.assertEqual(d.action, "allow")


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
        self.assertEqual(hook, os.path.join(p["dir"], "fmt.sh"))
        self.assertEqual(mcp.configs(root)["lintd"]["command"], os.path.join(p["dir"], "server"))
        with self.assertRaises(ValueError):
            plugins.install(src, root=root)                 # already there
        plugins.remove("lint-kit", root)
        self.assertNotIn("lint", extensions.custom_commands(root))


class HooksTest(unittest.TestCase):
    def test_pre_tool_use_blocks_and_prompt_context(self):
        py = sys.executable
        cfg = {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command":
               "%s -c \"import sys,json; d=json.load(sys.stdin); sys.stderr.write('no rm'); "
               "sys.exit(2 if 'rm' in d['tool_input']['command'] else 0)\"" % py}]}],
               "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "echo remember-this"}]}]}
        r = hooks.run(cfg, "PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "rm x"}}, "/tmp", tool="bash")
        self.assertTrue(r.block)
        self.assertEqual(r.permission, "deny")
        self.assertIn("no rm", r.reason)
        r = hooks.run(cfg, "PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "ls"}}, "/tmp", tool="bash")
        self.assertFalse(r.block)
        r = hooks.run(cfg, "UserPromptSubmit", {"prompt": "x"}, "/tmp")
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
        answer, events, s, llm, root = run_agent(script)
        self.assertEqual(answer, "Fixed for real.")
        self.assertEqual([e["ok"] for e in events if e["type"] == "verify"], [False, True])
        self.assertIn("tests fail", llm.requests[2]["messages"][-1]["content"])

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
        root = make_project(CALC)
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
        cls.httpd, cls.url = server.serve(0)
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
        with urllib.request.urlopen(cls.url + "api/events", timeout=120) as r:
            for raw in r:
                line = raw.decode().strip()
                if line.startswith("data:"):
                    cls.events.append(json.loads(line[5:]))

    def call(self, path, body=None):
        import urllib.request
        req = urllib.request.Request(self.url + path.lstrip("/"), json.dumps(body).encode() if body is not None else None,
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

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
