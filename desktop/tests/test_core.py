"""Tests that need no model: files, tools, memory, training data, routing rules, server safety."""

import json
import os
import re
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
import zipfile

HOME = tempfile.mkdtemp(prefix="newal-test-")
os.environ["NEWAL_HOME"] = HOME
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from newal import agent, catalog, config, files, memory, router, tools, training, web  # noqa: E402


class FilesTest(unittest.TestCase):
    def test_chunks_cover_text(self):
        text = "".join("line %d with some words\n" % i for i in range(400))
        parts = files.chunks(text, size=500, overlap=50)
        self.assertTrue(all(len(p) <= 550 for p in parts))
        self.assertIn("line 399", parts[-1])
        self.assertIn("line 0 ", parts[0])

    def test_docx_and_xlsx(self):
        d = os.path.join(HOME, "a.docx")
        with zipfile.ZipFile(d, "w") as z:
            z.writestr("word/document.xml", '<w:document><w:body><w:p><w:r><w:t>مرحبا</w:t></w:r></w:p>'
                                            '<w:p><w:r><w:t>Hello</w:t></w:r></w:p></w:body></w:document>')
        self.assertEqual(files.extract(d), "مرحبا\nHello")
        x = os.path.join(HOME, "a.xlsx")
        with zipfile.ZipFile(x, "w") as z:
            z.writestr("xl/sharedStrings.xml", "<sst><si><t>name</t></si><si><t>سعر</t></si></sst>")
            z.writestr("xl/worksheets/sheet1.xml", '<sheetData><row r="1"><c r="A1" t="s"><v>0</v></c>'
                                                   '<c r="B1" t="s"><v>1</v></c></row><row r="2"><c r="A2"><v>5</v></c></row></sheetData>')
        self.assertEqual(files.extract(x), "name\tسعر\n5")

    def test_text_encodings_and_binary(self):
        p = os.path.join(HOME, "w.txt")
        with open(p, "wb") as f:
            f.write("نص عربي".encode("cp1256"))
        self.assertEqual(files.extract(p), "نص عربي")
        b = os.path.join(HOME, "b.bin")
        with open(b, "wb") as f:
            f.write(b"\x00\x01\x02" * 100)
        self.assertEqual(files.extract(b), "")


class WebTest(unittest.TestCase):
    def test_html_to_text_and_relevant(self):
        page = "<html><script>x=1</script><p>First paragraph about cats and dogs here.</p><p>Second one talks about the price of gold today in detail.</p></html>"
        text = web.html_to_text(page)
        self.assertNotIn("x=1", text)
        self.assertEqual(web.relevant(text, "gold price", 60), "Second one talks about the price of gold today in detail.")


class ToolsTest(unittest.TestCase):
    def test_groups(self):
        self.assertNotIn("github_repos", tools.select("مرحبا"))
        self.assertIn("github_repos", tools.select("ارفع مشروعي على جيتهب"))
        self.assertIn("kaggle_download", tools.select("نزل داتا سيت من كاغل"))
        self.assertIn("drive_upload", tools.select("حط الملف على درايف"))
        self.assertIn("vscode", tools.select("افتحه بـ VS Code"))

    def test_definitions_are_valid(self):
        for d in tools.definitions():
            params = d["function"]["parameters"]
            self.assertEqual(params["type"], "object")
            for name in params["required"]:
                self.assertIn(name, params["properties"])
            for p in params["properties"].values():
                self.assertNotIn("optional", p)

    def test_write_read_list(self):
        self.assertIn("تم إنشاء الملف", tools.call("write_file", {"path": "dir/x.md", "content": "# hi"}))
        self.assertEqual(tools.call("read_file", '{"path": "dir/x.md"}'), "# hi")
        self.assertIn("x.md", tools.call("list_dir", {"path": "dir"}))

    def test_find_files_is_recursive_and_counts(self):
        for rel in ("ff/a.py", "ff/sub/b.py", "ff/sub/deep/c.py", "ff/sub/n.txt"):
            full = os.path.join(config.WORKSPACE, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            open(full, "w").close()
        out = tools.call("find_files", {"pattern": "*.py", "folder": "ff"})
        self.assertIn("Count: 3", out)
        self.assertIn(os.path.join("sub", "deep", "c.py"), out)

    def test_bad_calls(self):
        self.assertIn("No tool named 'nope'", tools.call("nope", {}))
        self.assertIn("Missing required argument(s) for write_file: content", tools.call("write_file", {"path": "a"}))
        self.assertIn("غير صالحة", tools.call("read_file", "{not json"))

    def test_closed_world_resolution(self):
        self.assertEqual(tools.resolve("powershell", '{"command": "dir"}')[:2],
                         ("run_command", {"command": "dir", "shell": "powershell"}))
        self.assertEqual(tools.resolve("cmd", {"command": "dir"})[1]["shell"], "cmd")
        self.assertEqual(tools.resolve("vscode_open", {"path": "a.py"})[:2], ("vscode", {"path": "a.py"}))
        self.assertEqual(tools.resolve("run_comand", {"command": "x", "bogus": 1})[:2], ("run_command", {"command": "x"}))
        self.assertIn("shell غير معروف", tools.call("run_command", {"command": "x", "shell": "zsh"}))
        for shell in ("cmd", "powershell"):
            out = tools.call("run_command", {"command": "echo مرحبا", "shell": shell})
            self.assertIn("exit code 0", out)
            self.assertIn("مرحبا", out, shell)

    def test_false_success_is_caught(self):
        self.assertTrue(agent.unsupported_claim("تمام، أنشأت لك الملف plan.md", []))
        self.assertTrue(agent.unsupported_claim("فتحت لك VS Code على المشروع", []))
        self.assertTrue(agent.unsupported_claim("I've installed pandas", [{"name": "run_command", "result": "خطأ: x"}]))
        self.assertFalse(agent.unsupported_claim("أنشأت الملف", [{"name": "write_file", "result": "تم إنشاء الملف"}]))
        self.assertFalse(agent.unsupported_claim("الجاذبية قوة بتجذب الأجسام لبعضها", []))
        failed = [{"name": "run_command", "result": "exit code 127"}]
        self.assertTrue(agent.unsupported_claim("لا يوجد cmd هنا. لكن النتيجة ستكون: NewAl-ok", failed))
        self.assertFalse(agent.unsupported_claim("النتيجة: الملف فيه مرحبا", failed))
        worked = [{"name": "run_command", "result": "exit code 0\n5"}]
        self.assertFalse(agent.unsupported_claim("الناتج سيكون 5 كل مرة", worked))    # an explanation, not a guess
        self.assertTrue(agent.wants_action("ثبتلي بايثون"))
        self.assertFalse(agent.wants_action("شو هي الجاذبية"))
        self.assertTrue(agent.wants_action("قديش بيساوي 237 × 18؟"))              # worked out by a tool, not guessed
        self.assertTrue(agent.wants_action("calculate the compound interest"))
        self.assertEqual(tools.call("run_command", {"command": "cmd /c echo NewAl-ok", "shell": "cmd"}),
                         "exit code 0\nNewAl-ok")

    def test_read_only_calls_run_together(self):
        import time as _t
        old = tools.TOOLS["current_time"]
        tools.TOOLS["current_time"] = (lambda: (_t.sleep(0.5), "t")[1],) + old[1:]
        try:
            t = agent.Turn(None, "x")
            started = _t.time()
            out = t._tools([{"name": "current_time", "arguments": "{}"}] * 4)
            took = _t.time() - started
        finally:
            tools.TOOLS["current_time"] = old
        self.assertEqual(out, ["t"] * 4)
        self.assertLess(took, 1.2)

    def test_approval_rules(self):
        config.update({"auto_run": False})
        self.assertTrue(tools.needs_approval("run_command"))
        self.assertFalse(tools.needs_approval("web_search"))
        config.update({"auto_run": True})
        self.assertFalse(tools.needs_approval("run_command"))
        config.update({"auto_run": False})


class AgentTest(unittest.TestCase):
    def test_runnable_block(self):
        text = "شرح\n```python\nprint(1)\n```\nثم\n```bash\nls\n```\n```py\nprint(2)\n```"
        self.assertEqual(agent.runnable_block(text), ("python", "print(2)\n"))
        self.assertEqual(agent.runnable_block("```html\n<p>x</p>\n```"), ("browser", "<p>x</p>\n"))   # pages run too
        self.assertIsNone(agent.runnable_block("```sql\nselect 1\n```"))
        split = "```python\ndef is_prime(n):\n    return n > 1\n```\nTests:\n```python\nassert is_prime(2)\nprint('ok')\n```"
        lang, code = agent.runnable_block(split)
        self.assertIn("def is_prime", code)
        self.assertIn("assert is_prime(2)", code)
        ok, out, _ = agent.run_code(lang, code)
        self.assertTrue(ok, out)
        self.assertEqual(agent.runnable_block("```js\nconst a = 1\n```\n```js\nconst a = 2\n```")[1], "const a = 2\n")
        # a corrected full version after a draft runs alone
        redo = "```python\ndef f():\n    return 1/0\nf()\n```\nFixed:\n```python\ndef f():\n    return 1\nprint(f())\n```"
        self.assertEqual(agent.runnable_block(redo)[1], "def f():\n    return 1\nprint(f())\n")
        imports = "```python\nimport math as m\n```\n```python\nprint(m.pi)\n```"
        self.assertIn("import math as m", agent.runnable_block(imports)[1])

    def test_run_code(self):
        ok, out, slow = agent.run_code("python", "print('ok', 6*7)")
        self.assertTrue(ok, out)
        self.assertIn("ok 42", out)
        self.assertFalse(slow)
        ok, out, slow = agent.run_code("python", "raise ValueError('boom')")
        self.assertFalse(ok)
        self.assertIn("boom", out)
        self.assertEqual(agent._last_line(out), "ValueError: boom")
        ok, out, slow = agent.run_code("python", "import time\ntime.sleep(10)", timeout=2)
        self.assertTrue(slow)

    def test_multi_step_code_requests_are_goals(self):
        self.assertTrue(agent.MULTI_STEP.search("create a calculator app, build an exe and push it to GitHub"))
        self.assertTrue(agent.MULTI_STEP.search("اكتب آلة حاسبة وارفعها على جيتهب"))
        self.assertFalse(agent.MULTI_STEP.search("اكتب دالة بايثون تحسب المضروب"))

    def test_programs_go_to_the_coder(self):
        self.assertTrue(agent.writes_program('{"path": "calc.py", "content": "%s"}' % ("x" * 300)))
        self.assertFalse(agent.writes_program('{"path": "notes.md", "content": "%s"}' % ("x" * 300)))
        self.assertIn("code_task for ANY", agent.GOAL)

    def test_error_line_prefers_the_real_error(self):
        out = "Traceback...\nfatal: could not read Username for 'https://github.com'\nIf you see 'done', it worked\n(exit code 1)"
        self.assertIn("fatal:", agent.error_line(out))

    def test_risky_code_is_spotted(self):
        self.assertTrue(agent.RISKY.search("import shutil\nshutil.rmtree('x')"))
        self.assertTrue(agent.RISKY.search("Remove-Item -Recurse C:\\x"))
        self.assertFalse(agent.RISKY.search("def is_prime(n):\n    return n > 1\nprint(is_prime(7))"))


class MemoryTest(unittest.TestCase):
    def test_conversations(self):
        c = memory.new_conversation()
        a = memory.add_message(c, "user", "سؤال")
        memory.add_message(c, "assistant", "جواب", {"route": "chat"})
        self.assertEqual([m["content"] for m in memory.messages(c)], ["سؤال", "جواب"])
        self.assertEqual(memory.messages(c)[1]["meta"]["route"], "chat")
        memory.delete_from(c, a)
        self.assertEqual(memory.messages(c), [])
        memory.delete_conversation(c)

    def test_keyword_search_without_models(self):
        self.assertFalse(catalog.available("embed"))
        memory.remember("the restaurant project uses Flask and SQLite")
        p = os.path.join(HOME, "notes.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("deployment happens on Fridays with docker compose\n")
        memory.index_file(p)
        r = memory.search("which database does the restaurant project use", k=2)
        self.assertEqual(r[0]["kind"], "memory")
        r = memory.search("docker deployment day", k=1)
        self.assertEqual(r[0]["source"], p)


class TrainingTest(unittest.TestCase):
    def test_log_feedback_examples_export(self):
        rid = training.log("trainrole", "code", [{"role": "user", "content": "write fizzbuzz in python please"}],
                           "```python\n...\n```", verified=False)
        rid2 = training.log("trainrole", "code", [{"role": "user", "content": "reverse a string in python"}], "s[::-1]",
                            verified=True)
        training.feedback(rid, True)            # the user's 👍 beats the failed run
        good = {r["id"]: r["good"] for r in training.records("trainrole")}
        self.assertTrue(good[rid])
        self.assertTrue(good[rid2])
        ex = training.examples("trainrole", "please write fizzbuzz in python")
        self.assertEqual(ex[0][0], "write fizzbuzz in python please")
        path, n = training.export("trainrole")
        self.assertEqual(n, 2)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.loads(f.readline())["messages"][-1]["role"], "assistant")
        self.assertIn("trainrole", training.update()["prepared"])


class RouterTest(unittest.TestCase):
    def test_build_requests_go_to_the_coder(self):
        for q in ["Create a professional calculator application for me in .exe format",
                  "اكتبلي برنامج بايثون يرتب الملفات", "اعمل موقع بسيط لمطعم", "write a script that renames photos"]:
            self.assertEqual(router.route(q), "code", q)

    def test_rules(self):
        self.assertEqual(router.route("Traceback (most recent call last):\n  File x"), "code")
        self.assertEqual(router.route("```js\nlet a\n```"), "code")
        self.assertEqual(router.route("مرحبا"), "chat")        # no models: safe default


class ThinkStripTest(unittest.TestCase):
    def run_pieces(self, pieces):
        from newal.engine import ThinkStripper
        st = ThinkStripper()
        return "".join(st.feed(p) for p in pieces)

    def test_strips_leading_block(self):
        self.assertEqual(self.run_pieces(["<th", "ink></think>", "\n\nمرحبا", " كيفك"]), "مرحبا كيفك")
        self.assertEqual(self.run_pieces(["<think>plan</think>Hi"]), "Hi")

    def test_keeps_normal_text(self):
        self.assertEqual(self.run_pieces(["مر", "حبا <think> ليست بالبداية"]), "مرحبا <think> ليست بالبداية")
        self.assertEqual(self.run_pieces(["<", "b>bold"]), "<b>bold")


class WebQuestionTest(unittest.TestCase):
    def test_needs_web(self):
        self.assertTrue(agent.needs_web("شو آخر أخبار الذكاء الاصطناعي هالأسبوع؟"))
        self.assertTrue(agent.needs_web("كم سعر الدولار اليوم"))
        self.assertFalse(agent.needs_web("مرحبا كيفك"))
        self.assertFalse(agent.needs_web("اعمل ملف todo.md"))

    def test_query_fallback_without_models(self):
        self.assertEqual(agent.search_queries("شو آخر أخبار الذكاء؟"), ["آخر أخبار الذكاء"])


class ComputerRequestTest(unittest.TestCase):
    def test_computer_requests_use_the_terminal(self):
        for q in ["شغّل ipconfig", "كم مساحة الهارد الفاضية؟", "كم رام عندي؟", "افتح المفكرة"]:
            self.assertEqual(router.route(q), "tools", q)
            self.assertFalse(agent.needs_web(q), q)
            self.assertTrue(agent.is_local(q), q)
        self.assertEqual(router.route("اكتب سكربت بايثون يفتح ملف"), "code")  # writing code, not a command
        self.assertFalse(agent.is_local("شو آخر أخبار الذكاء الاصطناعي"))

    def test_store_python_stub_is_skipped(self):
        import shutil
        real = shutil.which
        try:
            shutil.which = lambda n: r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\python.exe"
            self.assertNotIn("WindowsApps", config.find_python() or "")
        finally:
            shutil.which = real

    def test_missing_runtime_stops_the_loop(self):
        self.assertTrue(agent.MISSING_RUNTIME.search("Python was not found; run without arguments to install\n(exit code 9009)"))
        self.assertFalse(agent.MISSING_RUNTIME.search("NameError: name 'x' is not defined"))


class SkillsTest(unittest.TestCase):
    def test_builtin_skills_match_requests(self):
        from newal import skills
        names = [s["name"] for s in skills.all_skills()]
        self.assertIn("خبير PowerShell", names)
        self.assertEqual(skills.relevant("حلل ملف excel وعملي رسم بياني")[0]["name"], "تحليل البيانات وExcel")
        self.assertEqual(skills.relevant("مرحبا"), [])
        # «repo» inside «report.md» once pulled in the Git skill and sent goal mode cloning a made-up repo.
        self.assertEqual(skills.relevant("اكتب النتيجة بملف اسمه report.md"), [])
        self.assertEqual(skills.relevant("ارفع المشروع على github")[0]["name"], "Git وGitHub")
        self.assertEqual(skills.relevant("حلل البيانات بالإكسل")[0]["name"], "تحليل البيانات وExcel")

    def test_user_skill_save_toggle_delete(self):
        from newal import skills
        sid = skills.save("فواتير", "حساب الفواتير", "فاتورة, invoice", "- اجمع البنود\n- أضف الضريبة")
        self.assertEqual(skills.relevant("اعملي فاتورة")[0]["id"], sid)
        skills.set_enabled(sid, False)
        self.assertEqual(skills.relevant("اعملي فاتورة"), [])
        skills.delete(sid)
        self.assertNotIn(sid, [s["id"] for s in skills.all_skills()])


class WinToolsTest(unittest.TestCase):
    def test_zip_roundtrip_and_unsafe_archive(self):
        from newal import wintools
        d = os.path.join(config.WORKSPACE, "ziptest")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "a.txt"), "w") as f:
            f.write("hi")
        self.assertIn("تم الضغط", wintools.zip_path("ziptest"))
        self.assertIn("تم فك الضغط", wintools.unzip_path("ziptest.zip", "unzipped"))
        self.assertTrue(os.path.exists(os.path.join(config.WORKSPACE, "unzipped", "ziptest", "a.txt")))
        bad = os.path.join(config.WORKSPACE, "bad.zip")
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("../evil.txt", "x")
        self.assertIn("غير آمن", wintools.unzip_path(bad))

    def test_goal_prompt_stays_small(self):
        defs = tools.definitions(tools.goal_names("create a calculator exe")) + [agent.CODE_TASK_TOOL]
        size = len(json.dumps(defs, ensure_ascii=False))
        self.assertLess(size, 12000, "tool list too large for a CPU prompt: %d chars" % size)

    def test_compact_keeps_recent_results(self):
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "goal"}]
        for i in range(12):
            msgs += [{"role": "assistant", "content": "step"}, {"role": "tool", "content": "x" * 5000}]
        agent.compact(msgs)
        self.assertLessEqual(len(msgs[3]["content"]), 420)
        self.assertLessEqual(sum(len(m["content"]) for m in msgs), 26000)

    def test_compact_leaves_the_prefix_alone_while_it_fits(self):
        # llama.cpp re-reads everything after the first changed message: no trimming while there is room,
        # and one hard cut leaves room for several more steps without touching the prefix again.
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "goal"}]
        for i in range(4):
            msgs += [{"role": "assistant", "content": "step"}, {"role": "tool", "content": "x" * 4000}]
        before = [dict(m) for m in msgs]
        agent.compact(msgs, keep=2, budget_chars=24000)
        self.assertEqual(msgs, before)
        for i in range(4):
            msgs += [{"role": "assistant", "content": "step"}, {"role": "tool", "content": "x" * 4000}]
        agent.compact(msgs, keep=2, budget_chars=24000)
        cut = [dict(m) for m in msgs]
        self.assertLessEqual(sum(len(m["content"]) for m in msgs), 12500)
        msgs += [{"role": "assistant", "content": "step"}, {"role": "tool", "content": "x" * 4000}]
        agent.compact(msgs, keep=2, budget_chars=24000)
        self.assertEqual(msgs[:len(cut)], cut)                 # the next step only appends

    def test_system_prompt_is_stable(self):
        self.assertNotRegex(agent._system("code"), r"\d\d:\d\d")

    def test_goal_tools_follow_connections(self):
        config.update({"github_token": ""})
        self.assertNotIn("github_repos", tools.goal_names())
        self.assertIn("run_command", tools.goal_names())
        self.assertIn("schedule", tools.goal_names())


class SignInTest(unittest.TestCase):
    def test_git_credential_reads_the_helper(self):
        from newal import connectors
        cfg = os.path.join(HOME, "gitconfig")
        with open(cfg, "w") as f:
            f.write('[credential]\n\thelper = "!f() { echo username=me; echo password=tok123; }; f"\n')
        old = os.environ.get("GIT_CONFIG_GLOBAL")
        os.environ["GIT_CONFIG_GLOBAL"] = cfg
        try:
            self.assertEqual(connectors.git_credential("example.com"), ("tok123", None))
        finally:
            if old is None:
                del os.environ["GIT_CONFIG_GLOBAL"]
            else:
                os.environ["GIT_CONFIG_GLOBAL"] = old

    def test_gitlab_header_kind(self):
        from newal import connectors
        config.update({"gitlab_token": "glpat-abc"})
        self.assertEqual(connectors._gl(), {"PRIVATE-TOKEN": "glpat-abc"})
        config.update({"gitlab_token": "oauth-xyz"})
        self.assertEqual(connectors._gl(), {"Authorization": "Bearer oauth-xyz"})
        config.update({"gitlab_token": ""})


class ConfigTest(unittest.TestCase):
    def test_secrets_hidden(self):
        config.update({"github_token": "ghp_secret"})
        self.assertEqual(config.all_settings()["github_token"], "••••")
        config.update({"github_token": "••••"})              # the UI sends the mask back unchanged
        self.assertEqual(config.get("github_token"), "ghp_secret")
        config.update({"github_token": ""})


class CodingHelpersTest(unittest.TestCase):
    def test_libraries_found(self):
        self.assertEqual(agent.libraries("اعملي API بـ FastAPI مع pandas، و fastapi routes"), ["fastapi", "pandas"])
        self.assertEqual(agent.libraries("import cv2\nfrom sklearn import svm"), ["opencv", "scikit-learn"])
        self.assertEqual(agent.libraries("اكتب دالة تعكس نص"), [])

    def test_library_id(self):
        self.assertEqual(agent.library_id("- Title: FastAPI\n- Context7-compatible library ID: /tiangolo/fastapi\n"),
                         "/tiangolo/fastapi")
        self.assertEqual(agent.library_id("nothing here"), "")

    def test_docs_are_added_before_coding(self):
        from newal import mcp
        calls = []

        def fake_call(name, args, timeout=0):
            calls.append(name)
            return "Context7-compatible library ID: /tiangolo/fastapi" if "resolve" in name else "@app.post('/items')"
        old = (mcp.manager.enabled, mcp.manager.call)
        mcp.manager.enabled, mcp.manager.call = (lambda: {"docs": {}}), fake_call
        try:
            t = agent.Turn(None, "اعملي API بـ FastAPI")
            docs = t._docs(t.text)
        finally:
            mcp.manager.enabled, mcp.manager.call = old
        self.assertIn("@app.post", docs)
        self.assertIn("/tiangolo/fastapi", docs)
        self.assertEqual(calls, ["mcp__docs__resolve-library-id", "mcp__docs__query-docs"])

    def test_no_docs_without_the_addon(self):
        from newal import mcp
        old = mcp.manager.enabled
        mcp.manager.enabled = lambda: {}
        try:
            self.assertEqual(agent.Turn(None, "FastAPI app")._docs("FastAPI app"), "")
        finally:
            mcp.manager.enabled = old

    def test_programming_skills_match(self):
        from newal import skills
        names = lambda t: [x["id"] for x in skills.relevant(t, k=3)]
        self.assertIn("debugging", names("عندي traceback وما عم يشتغل"))
        self.assertIn("build-exe", names("حوّله لملف exe"))
        self.assertIn("api-backend", names("اعملي api بـ fastapi"))

    def test_setup_all_runs_every_step(self):
        import time
        from newal import addons
        done = []
        old = (addons.install, addons.status)
        addons.install = lambda i, extra=None: (done.append(i), {"ok": i != "node", "message": "x"})[1]
        addons.status = lambda: [{"id": "git", "title": "Git", "ready": True}]
        try:
            addons.setup_all()
            for _ in range(200):
                if not addons.setup_status()["running"]:
                    break
                time.sleep(0.02)
            st = addons.setup_status()
        finally:
            addons.install, addons.status = old
        self.assertNotIn("git", done)                    # already ready: skipped
        self.assertEqual(done[0], "node")                 # Node.js before the MCP add-ons
        self.assertIn("mcp:docs", done)
        self.assertEqual(st["failed"], 1)
        self.assertEqual(st["done"], st["total"] - 1)


class ProjectLoopTest(unittest.TestCase):
    ANSWER = ("Here is the project.\n\n```python calc/core.py\ndef add(a, b):\n    return a - b\n```\n\n"
              "```python main.py\nfrom calc.core import add\nassert add(2, 3) == 5, add(2, 3)\nprint('ok', add(2, 3))\n```\n"
              "\n```python calc/__init__.py\n```\n\nRUN: `python main.py`")
    FIX = "Fixed.\n\n```python calc/core.py\ndef add(a, b):\n    return a + b\n```"

    def test_project_files(self):
        files, run = agent.project_files(self.ANSWER)
        self.assertEqual(sorted(files), ["calc/__init__.py", "calc/core.py", "main.py"])
        self.assertEqual(run, "python main.py")
        self.assertEqual(agent.project_files("```python ../../evil.py\nx\n```")[0], {})
        self.assertEqual(agent.project_files("```python C:/x.py\nx\n```")[0], {})
        loose = ("**app/main.py**\n```python\nprint(1)\n```\n### `tests/test_x.py`\n```python\ndef test(): pass\n```\n"
                 "```js\n// web/index.js\nx\n```\n```python\n# 1.5 times\nprint(2)\n```")
        self.assertEqual(sorted(agent.project_files(loose)[0]), ["app/main.py", "tests/test_x.py", "web/index.js"])

    def test_fix_merges_changed_files(self):
        first = agent.program(self.ANSWER)
        self.assertTrue(first["project"])
        second = agent.program(self.FIX, first)
        self.assertIn("a + b", second["files"]["calc/core.py"])
        self.assertIn("main.py", second["files"])
        self.assertEqual(second["run"], "python main.py")
        # a single runnable block stays a plain program
        self.assertFalse(agent.program("```python\nprint(1)\n```")["project"])

    def test_run_project(self):
        ok, out, slow, folder = agent.run_program(agent.program(self.ANSWER))
        self.assertFalse(ok)
        self.assertIn("AssertionError", out)
        ok, out, slow, folder = agent.run_program(agent.program(self.FIX, agent.program(self.ANSWER)))
        self.assertTrue(ok, out)
        self.assertIn("ok 5", out)

    def test_loop_fixes_project_and_learns(self):
        from newal import engine, lessons
        replies = [self.ANSWER, self.FIX]
        old = (engine.pool.chat, catalog.pick, agent.pool.chat)

        def fake_chat(role, messages, **kw):
            return {"content": replies.pop(0), "tps": 1, "tool_calls": []}
        agent.pool.chat = fake_chat
        catalog.pick = lambda role: None          # no judge model: a clean run counts as success
        try:
            t = agent.Turn(None, "write a calculator package")
            answer, info = t._code([{"role": "user", "content": "write a calculator package"}], "coder")
        finally:
            agent.pool.chat, catalog.pick = old[2], old[1]
        self.assertTrue(info["verified"], info)
        self.assertEqual(info["attempts"], 2)
        self.assertIn("a + b", answer)
        self.assertIn("main.py", answer)                 # the answer shows the whole project
        self.assertTrue(os.path.isfile(os.path.join(info["project"], "calc", "core.py")))

    def test_lessons(self):
        from newal import lessons
        lessons.add("fix", "On Windows open text files with encoding='utf-8' to read Arabic text.",
                    "read an arabic csv file", "UnicodeDecodeError: 'charmap' codec can't decode")
        lessons.add("fix", "On Windows open text files with encoding='utf-8' when reading Arabic text.",
                    "read arabic text", "UnicodeDecodeError")
        found = lessons.relevant("اقرأ ملف csv", error="UnicodeDecodeError: 'charmap'")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["seen"], 2)            # the same lesson again is counted, not duplicated
        self.assertIn("utf-8", lessons.as_prompt(found))
        self.assertEqual(lessons.relevant("draw a chart of sales"), [])
        lessons.forget(found[0]["id"])
        self.assertEqual(lessons.all_lessons(), [])


class PhoneTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from newal import phone
        config.update({"phone_port": 0})
        phone.configure(True)
        cls.port = phone._server.server_address[1]
        cls.ip = next((a["ip"] for a in phone.addresses()), "127.0.0.1")
        cls.base = "http://%s:%d" % (cls.ip, cls.port)

    @classmethod
    def tearDownClass(cls):
        from newal import phone
        phone.configure(False)

    def req(self, path, method="GET", body=None, headers=None):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        opener = urllib.request.build_opener(NoRedirect)
        r = urllib.request.Request(self.base + path, json.dumps(body).encode() if body is not None else None,
                                   headers or {}, method=method)
        try:
            with opener.open(r) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_key_needed(self):
        from newal import phone
        self.assertEqual(self.req("/")[0], 403)
        self.assertEqual(self.req("/api/state")[0], 403)
        self.assertEqual(self.req("/?k=wrong")[0], 403)
        code, headers, _ = self.req("/?k=" + phone.key())
        self.assertEqual(code, 303)
        cookie = headers["Set-Cookie"].split(";")[0]
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertEqual(self.req("/", headers={"Cookie": cookie})[0], 200)
        self.assertEqual(self.req("/api/state", headers={"Cookie": cookie})[0], 200)
        # POSTs: the page's own header and origin only
        ok = {"Cookie": cookie, "X-NewAl": "1", "Content-Type": "application/json"}
        self.assertEqual(self.req("/api/lessons", "POST", {}, ok)[0], 200)
        self.assertEqual(self.req("/api/lessons", "POST", {}, {"Cookie": cookie})[0], 403)
        self.assertEqual(self.req("/api/lessons", "POST", {}, dict(ok, Origin="http://evil.example"))[0], 403)
        # the phone app sends the key as a bearer token (no cookie, no page header)
        bearer = {"Authorization": "Bearer " + phone.key(), "Content-Type": "application/json"}
        self.assertEqual(self.req("/api/state", headers=bearer)[0], 200)
        self.assertEqual(self.req("/api/lessons", "POST", {}, bearer)[0], 200)
        self.assertEqual(self.req("/api/state", headers={"Authorization": "Bearer wrong"})[0], 403)
        # a new key logs the old phone out
        phone.configure(True, new_key=True)
        self.assertEqual(self.req("/api/state", headers={"Cookie": cookie})[0], 403)

    def test_status_has_qr(self):
        from newal import phone
        st = phone.status()
        self.assertTrue(st["running"])
        if st["urls"]:
            self.assertIn("k=" + phone.key(), st["urls"][0]["url"])
            self.assertTrue(st["urls"][0]["svg"].startswith("<svg"))


class ProjectModeTest(unittest.TestCase):
    def make(self):
        root = tempfile.mkdtemp(prefix="proj-")
        with open(os.path.join(root, "shop.py"), "w") as f:
            f.write("def total(prices):\n    return sum(prices) - 1\n")
        os.makedirs(os.path.join(root, "tests"))
        with open(os.path.join(root, "tests", "test_shop.py"), "w") as f:
            f.write("import unittest, sys, os\nsys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))\n"
                    "from shop import total\n\nclass T(unittest.TestCase):\n    def test_total(self):\n"
                    "        self.assertEqual(total([2, 3]), 5)\n")
        return root

    def test_review_sends_it_back(self):
        from newal import workspace
        root = self.make()
        config.update({"project_path": root, "review_changes": True})
        call = lambda n, a: {"id": "", "name": n, "arguments": json.dumps(a)}
        replies = [
            {"content": "", "tool_calls": [call("edit_file", {"path": "shop.py", "old": "sum(prices) - 1",
                                                                "new": "sum(prices)  # print(prices)"})]},
            {"content": "Done.", "tool_calls": []},                                  # review: a debug leftover
            {"content": "", "tool_calls": [call("edit_file", {"path": "shop.py", "old": "  # print(prices)", "new": ""})]},
            {"content": "Removed the leftover.", "tool_calls": []},
        ]
        verdicts = [{"ok": False, "problems": ["remove the commented-out print"]}, {"ok": True, "problems": []}]
        old = (agent.pool.chat, agent.pool.complete_json, catalog.pick, workspace.test_command)
        agent.pool.chat = lambda role, messages, **kw: dict(replies.pop(0), tps=1)
        agent.pool.complete_json = lambda role, messages, schema, **kw: verdicts.pop(0)
        catalog.pick = lambda role: "coder"
        workspace.test_command = lambda r: "python -m unittest discover -s tests -q"
        events = []
        try:
            # 💭: the review also runs when the tests passed (without it, passing tests are the check)
            t = agent.Turn(None, "fix the total", emit=events.append, mode="project", think=True)
            answer, info = t._project([{"role": "user", "content": "fix the total"}], "coder")
        finally:
            agent.pool.chat, agent.pool.complete_json, catalog.pick, workspace.test_command = old
        self.assertEqual(replies, [])
        self.assertEqual(verdicts, [])
        self.assertTrue(info["verified"])
        with open(os.path.join(root, "shop.py")) as f:
            self.assertEqual(f.read(), "def total(prices):\n    return sum(prices)\n")
        reviews = [e for e in events if e["type"] == "verdict"]
        self.assertEqual([e["ok"] for e in reviews], [False, True])

    def test_repo_map(self):
        from newal import workspace
        root = self.make()
        with open(os.path.join(root, "app.js"), "w") as f:
            f.write("export function start(port) {}\nconst stop = async () => {}\nclass Server {}\n")
        m = workspace.repo_map(root)
        self.assertIn("shop.py: def total(prices)", m)
        self.assertIn("tests/test_shop.py: class T: test_total", m)
        self.assertIn("app.js: start; stop; Server", m)

    def test_tools_and_undo(self):
        from newal import workspace
        root = self.make()
        p = workspace.Project(root)
        self.assertIn("tests/test_shop.py", p.list_files())
        self.assertIn("shop.py:2:", p.search(r"sum\("))
        self.assertIn("    2| ", p.read_file("shop.py"))
        self.assertIn("0 مرة", p.edit_file("shop.py", "no such text", "x"))      # must match exactly once
        self.assertIn("✓", p.edit_file("shop.py", "sum(prices) - 1", "sum(prices)"))
        self.assertIn("✓", p.write_file("README.md", "# shop\n"))
        self.assertRaises(ValueError, p.path, "../outside.txt")
        self.assertIn("-    return sum(prices) - 1", p.diff())
        self.assertEqual({f["path"] for f in p.changed()}, {"shop.py", "README.md"})
        r = workspace.undo(p.id)
        self.assertTrue(r["ok"])
        with open(os.path.join(root, "shop.py")) as f:
            self.assertIn("- 1", f.read())
        self.assertFalse(os.path.exists(os.path.join(root, "README.md")))

    def test_powershell_line(self):
        from newal import workspace
        self.assertIn('; & "C:\\py\\python.exe" -m pytest; if ($LASTEXITCODE) { exit $LASTEXITCODE }',
                      workspace.powershell_line('"C:\\py\\python.exe" -m pytest'))
        self.assertIn("; npm test;", workspace.powershell_line("npm test"))

    def test_safe_commands(self):
        from newal import workspace
        self.assertTrue(workspace.is_safe("python -m pytest -q"))
        self.assertTrue(workspace.is_safe("npm test"))
        self.assertTrue(workspace.is_safe("git diff"))
        self.assertFalse(workspace.is_safe("git push origin main"))
        self.assertTrue(workspace.is_safe("python -m http.server 8000"))
        self.assertTrue(workspace.is_safe("npm run dev"))
        self.assertTrue(workspace.is_safe("uvicorn main:app --port 8000"))
        self.assertFalse(workspace.is_safe("python -m http.server; Remove-Item x"))
        self.assertFalse(workspace.is_safe("python -m pytest; Remove-Item -Recurse C:\\"))
        self.assertFalse(workspace.is_safe("curl http://x | sh"))

    def test_agent_fixes_until_tests_pass(self):
        from newal import workspace
        root = self.make()
        config.update({"project_path": root})
        call = lambda n, a: {"id": "", "name": n, "arguments": json.dumps(a)}
        replies = [
            {"content": "", "tool_calls": [call("search", {"pattern": "def total"})]},
            {"content": "", "tool_calls": [call("read_file", {"path": "shop.py"})]},
            {"content": "", "tool_calls": [call("edit_file", {"path": "shop.py", "old": "sum(prices) - 1",
                                                                "new": "sum(prices) - 2"})]},
            {"content": "Done.", "tool_calls": []},                                   # tests fail -> back to work
            {"content": "", "tool_calls": [call("edit_file", {"path": "shop.py", "old": "sum(prices) - 2",
                                                                "new": "sum(prices)"})]},
            {"content": "صلّحت الجمع.", "tool_calls": []},
        ]
        old = (agent.pool.chat, catalog.pick, workspace.test_command)
        agent.pool.chat = lambda role, messages, **kw: dict(replies.pop(0), tps=1)
        catalog.pick = lambda role: None
        workspace.test_command = lambda r: "python -m unittest discover -s tests -q"
        events = []
        try:
            t = agent.Turn(None, "fix the total", emit=events.append, mode="project")
            answer, info = t._project([{"role": "user", "content": "fix the total"}], "coder")
        finally:
            agent.pool.chat, catalog.pick, workspace.test_command = old
        self.assertTrue(info["verified"], answer)
        self.assertEqual(info["attempts"], 2)
        self.assertIn("shop.py", answer)
        runs = [e for e in events if e["type"] == "run"]
        self.assertEqual([r["ok"] for r in runs], [False, True])
        self.assertTrue(any(e["type"] == "diff" for e in events))
        with open(os.path.join(root, "shop.py")) as f:
            self.assertIn("return sum(prices)\n", f.read())
        self.assertTrue(workspace.undo(info["checkpoint"])["ok"])


class SchoolTest(unittest.TestCase):
    def tearDown(self):
        # the school tests log their own examples: leave the training folder as they found it
        import shutil
        shutil.rmtree(config.TRAINING)
        shutil.copytree(self.saved, config.TRAINING)
        shutil.rmtree(self.saved)
        from newal import lessons
        if os.path.exists(lessons.PATH):
            os.remove(lessons.PATH)

    def setUp(self):
        import shutil
        self.saved = tempfile.mkdtemp()
        shutil.rmtree(self.saved)
        shutil.copytree(config.TRAINING, self.saved)
        from newal import school
        if os.path.exists(school.STATE):
            os.remove(school.STATE)
        config.update({"kaggle_username": "me", "kaggle_key": "k", "school_hours": 30})

    def log(self, text, **info):
        return training.log("coder", "code", [{"role": "user", "content": "notes\n\nMy message:\n" + text}],
                            "```python\nprint(1)\n```", **info)

    def test_collect_hard_tasks_only(self):
        from newal import school
        easy = self.log("write a function that adds two numbers together", verified=True, attempts=1)
        hard = self.log("parse this csv of sales and total by month", verified=True, attempts=3)
        failed = self.log("scrape the titles from a news page with requests", verified=False, attempts=5)
        win = self.log("اكتب سكربت باورشل يحذف الملفات القديمة", verified=False, attempts=5)
        ids = [t["id"] for t in school.collect()]
        self.assertIn(hard, ids)
        self.assertIn(failed, ids)
        self.assertNotIn(easy, ids)
        self.assertNotIn(win, ids)                     # Windows-only: cannot run on Kaggle's Linux
        req = next(t for t in school.collect() if t["id"] == hard)["request"]
        self.assertEqual(req, "parse this csv of sales and total by month")

    def test_kernel_source_embeds_tasks(self):
        import base64
        from newal import school
        src = school.kernel_source([{"id": "a", "request": "اكتب دالة", "error": ""}], 2.5)
        self.assertNotIn("__CONFIG__", src)
        self.assertNotIn("__TASKS__", src)
        compile(src, "school.py", "exec")
        cfg = json.loads(base64.b64decode(re.search(r'CONFIG = json.loads\(base64.b64decode\("([^"]+)"', src).group(1)))
        self.assertEqual(cfg["hours"], 2.5)
        self.assertIn("Qwen3.6-35B-A3B", cfg["model_url"])
        self.assertIn("self-test", cfg["coder_prompt"])

    def test_push_and_import(self):
        from newal import lessons, school
        for i in range(3):
            self.log("task number %d that was hard to get right" % i, verified=False, attempts=4)
        calls = []

        def fake_cli(args, timeout=0):
            calls.append(args)
            if args[0] == "quota":
                return 0, '[{"resource": "GPU", "used": "4.00h", "remaining": "26.00h", "total": "30.00h"}]'
            if args[:2] == ["kernels", "push"]:
                return 0, "Kernel version 1 successfully pushed."
            if args[:2] == ["kernels", "status"]:
                return 0, 'me/newal-school has status "KernelWorkerStatus.COMPLETE"'
            if args[:2] == ["kernels", "output"]:
                folder = args[args.index("-p") + 1]
                tasks = school.state()["tasks"]
                with open(os.path.join(folder, "results.jsonl"), "w") as f:
                    f.write(json.dumps({"id": tasks[0], "request": "task 0", "solved": True, "answer": "```python\nok\n```",
                                        "lesson": "Close files with a with-block so the data is flushed before reading.",
                                        "errors": ["try 1: ValueError"], "output": "ok"}) + "\n")
                    f.write(json.dumps({"id": tasks[1], "request": "task 1", "solved": False, "errors": ["try 1: x"]}) + "\n")
                with open(os.path.join(folder, "summary.json"), "w") as f:
                    json.dump({"session_seconds": 7200}, f)
                return 0, "ok"
            return 1, "?"
        old = school.cli
        school.cli = fake_cli
        try:
            r = school.push()
            self.assertTrue(r["ok"], r)
            push = next(a for a in calls if a[:2] == ["kernels", "push"])
            self.assertIn("NvidiaTeslaT4", push)
            self.assertLessEqual(float(push[push.index("--timeout") + 1]), 8.5 * 3600 + 900)
            self.assertFalse(school.push()["ok"])          # one session at a time
            done = school.check()
        finally:
            school.cli = old
        self.assertEqual((done["solved"], done["done"], done["lessons"]), (1, 2, 1))
        st = school.state()
        self.assertFalse(st["running"])
        self.assertEqual(st["sessions"][-1]["hours"], 2.0)
        self.assertTrue(any("with-block" in x["text"] for x in lessons.all_lessons()))
        self.assertEqual(training.examples("coder", "task 0 please")[0][1], "```python\nok\n```")
        ids = [t["id"] for t in school.collect()]
        self.assertNotIn(st["tasks"][0], ids)             # learned: not sent again
        self.assertIn(st["tasks"][1], ids)                # failed once: tried again next session

    def kernel(self):
        from newal import school
        os.environ["SCHOOL_OUT"] = tempfile.mkdtemp()
        os.environ["SCHOOL_WORK"] = tempfile.mkdtemp()
        ns = {"__name__": "school_kernel"}
        exec(compile(school.kernel_source([], 1), "school_kernel.py", "exec"), ns)
        return ns

    def test_kernel_programs(self):
        k = self.kernel()
        split = "```python\ndef is_prime(n):\n    return n > 1 and all(n % d for d in range(2, n))\n```\n" \
                "```python\nassert is_prime(7)\nprint('ok')\n```"
        ok, out = k["run_program"](k["program"](split))
        self.assertTrue(ok, out)
        bad = k["run_program"](k["program"]("```python\nraise ValueError('x')\n```"))
        self.assertFalse(bad[0])
        self.assertIn("ValueError", k["error_line"](bad[1]))

    def test_kernel_no_test_functions(self):
        import importlib.util
        if not importlib.util.find_spec("pytest"):
            self.skipTest("pytest not installed")
        k = self.kernel()
        ans = ("```python fib.py\ndef fib(n):\n    return n if n < 2 else fib(n-1) + fib(n-2)\n```\n"
               "```python test_fib.py\nfrom fib import fib\nassert fib(10) == 55\nprint('fine')\n```")
        ok, out = k["run_program"](k["program"](ans))
        self.assertTrue(ok, out)
        self.assertIn("fine", out)
        prog = agent.program(ans + "\n```python main.py\nfrom fib import fib\nprint(fib(6))\n```")
        ok, out, _, _ = agent.run_program(prog)
        self.assertTrue(ok, out)

    def test_week_starts_saturday(self):
        import datetime
        from newal import school
        sat = datetime.datetime(2026, 9, 26, 10, tzinfo=datetime.timezone.utc)
        fri = datetime.datetime(2026, 10, 2, 23, tzinfo=datetime.timezone.utc)
        self.assertEqual(school.week_key(sat), "2026-09-26")
        self.assertEqual(school.week_key(fri), "2026-09-26")
        self.assertEqual(school.week_key(fri + datetime.timedelta(hours=2)), "2026-10-03")


class BackgroundTaskTest(unittest.TestCase):
    def project(self, git):
        root = ProjectModeTest.make(self)
        if git:
            import subprocess
            run = lambda *a: subprocess.run(["git", "-C", root] + list(a), capture_output=True, check=True)
            run("init", "-q")
            run("-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
            run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "start")
        return root

    def run_task(self, git):
        from newal import tasks, workspace
        root = self.project(git)
        call = lambda n, a: {"id": "", "name": n, "arguments": json.dumps(a)}
        replies = [{"content": "", "tool_calls": [call("edit_file", {"path": "shop.py", "old": "sum(prices) - 1",
                                                                      "new": "sum(prices)"})]},
                   {"content": "صلّحت.", "tool_calls": []}]
        old = (agent.pool.chat, catalog.pick, workspace.test_command)
        agent.pool.chat = lambda role, messages, **kw: dict(replies.pop(0), tps=1)
        catalog.pick = lambda role: "coder"
        workspace.test_command = lambda r: "python -m unittest discover -s tests -q"
        try:
            t = tasks.add("fix the total", root)["task"]
            tasks.run_one(t)
        finally:
            agent.pool.chat, catalog.pick, workspace.test_command = old
        return root, tasks.get(t["id"])

    def check(self, git):
        from newal import tasks, workspace
        root, t = self.run_task(git)
        self.assertEqual(t["status"], "done", t)
        self.assertEqual(t["kind"], "git" if git else "copy")
        self.assertTrue(t["verified"])
        with open(os.path.join(root, "shop.py")) as f:
            self.assertIn("- 1", f.read())                    # the real project is untouched until applied
        self.assertIn("+    return sum(prices)", tasks.diff_of(t["id"])["diff"])
        r = tasks.apply(t["id"])
        self.assertTrue(r["ok"], r)
        with open(os.path.join(root, "shop.py")) as f:
            self.assertNotIn("- 1", f.read())
        self.assertFalse(os.path.exists(os.path.join(tasks.TREES, t["id"])))     # the copy is cleaned up
        workspace.undo(r["checkpoint"])
        with open(os.path.join(root, "shop.py")) as f:
            self.assertIn("- 1", f.read())

    def test_task_in_git_worktree(self):
        import shutil
        if not shutil.which("git"):
            self.skipTest("git not installed")
        self.check(git=True)

    def test_task_in_copy(self):
        self.check(git=False)

    def test_publish_pull_request(self):
        import shutil
        import subprocess
        if not shutil.which("git"):
            self.skipTest("git not installed")
        from newal import connectors, tasks
        root, t = self.run_task(git=True)
        remote = tempfile.mkdtemp(prefix="remote-")
        subprocess.run(["git", "init", "-q", "--bare", remote], check=True)
        subprocess.run(["git", "-C", root, "remote", "add", "origin", remote], check=True)
        calls = []

        def fake_api(url, headers=None, method="GET", body=None, **kw):
            calls.append((method, url, body))
            return {"default_branch": "master"} if method == "GET" else {"html_url": "https://github.com/me/shop/pull/1"}
        old = (tasks.github_repo_of, connectors._api)
        tasks.github_repo_of = lambda folder: "me/shop"
        connectors._api = fake_api
        config.update({"github_token": "t"})
        try:
            r = tasks.publish(t["id"])
        finally:
            tasks.github_repo_of, connectors._api = old
            config.update({"github_token": ""})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["url"], "https://github.com/me/shop/pull/1")
        post = next(c for c in calls if c[0] == "POST")
        self.assertEqual(post[2]["head"], "newal/" + t["id"])
        self.assertEqual(post[2]["base"], "master")
        self.assertIn("اختبارات المشروع نجحت", post[2]["body"])
        shown = subprocess.run(["git", "--git-dir", remote, "show", "newal/%s:shop.py" % t["id"]],
                               capture_output=True, text=True).stdout
        self.assertIn("return sum(prices)\n", shown)                 # the fix is on the pushed branch
        self.assertEqual(tasks.get(t["id"])["status"], "pr")
        with open(os.path.join(root, "shop.py")) as f:
            self.assertIn("- 1", f.read())                               # the user's copy is untouched


class VisionTest(unittest.TestCase):
    def test_with_images(self):
        from newal.engine import with_images
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "look", "_images": ["data:image/png;base64,AA"]}]
        seen = with_images(msgs, True)
        self.assertEqual(seen[1]["content"][0], {"type": "text", "text": "look"})
        self.assertEqual(seen[1]["content"][1]["image_url"]["url"], "data:image/png;base64,AA")
        self.assertNotIn("_images", seen[1])
        blind = with_images(msgs, False)
        self.assertEqual(blind[1], {"role": "user", "content": "look"})
        self.assertIn("_images", msgs[1])                      # the caller's messages are not changed

    def test_look_first(self):
        png = os.path.join(HOME, "shot.png")
        with open(png, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\nfake")
        old = (catalog.sees, agent.pool.chat)
        sent = []
        agent.pool.chat = lambda role, messages, **kw: (sent.append((role, messages)), {"content": "ZeroDivisionError: division by zero", "tps": 1})[1]
        try:
            catalog.sees = lambda role: False
            t = agent.Turn(None, "fix it", attachments=[png])
            t._look()
            self.assertIn("العيون", t.text)                    # tells the user what to download
            self.assertEqual(sent, [])
            catalog.sees = lambda role: True
            events = []
            t = agent.Turn(None, "fix it", attachments=[png], emit=events.append)
            t._look()
        finally:
            catalog.sees, agent.pool.chat = old
        self.assertIn("ZeroDivisionError: division by zero", t.text)
        self.assertEqual(t.attachments, [])                    # the picture is not also read as a text file
        role, msgs = sent[0]
        self.assertEqual(role, "coder")
        self.assertTrue(msgs[0]["_images"][0].startswith("data:image/png;base64,"))
        self.assertTrue(any(e.get("name") == "look" and e.get("state") == "done" for e in events))
        self.assertEqual(router.route(t.text), "code")        # a traceback in the picture goes to the programmer


class OneBrainTest(unittest.TestCase):
    def test_context_and_ram(self):
        from newal.engine import Server
        config.update({"brain_context": 32768, "context": 16384})
        self.assertEqual(Server("coder").context(), 32768)
        self.assertEqual(Server("agent").context(), 16384)
        # Qwen3.6 keeps ~20 KB per token of context: 32k tokens cost ~0.7 GB, not 2 GB
        ram = Server("coder").ram_gb() - catalog.MODELS["coder"]["size"] / 1e9 * 1.1 - 0.3
        self.assertLess(ram, 0.7 + (catalog.MODELS["vision"]["size"] / 1e9 if Server("coder").vision() else 0) + 0.01)
        self.assertGreater(agent.context_chars("coder"), agent.context_chars("agent"))

    def test_needed_models(self):
        config.update({"one_brain": True})
        st = {m["role"]: m for m in catalog.status()}
        self.assertTrue(st["coder"]["required"] and st["embed"]["required"])
        self.assertFalse(st["agent"]["required"])
        self.assertTrue(st["agent"]["optional"])
        config.update({"one_brain": False})
        st = {m["role"]: m for m in catalog.status()}
        self.assertTrue(st["agent"]["required"])
        config.update({"one_brain": True})

    def test_every_route_uses_the_brain(self):
        seen = []
        old = (catalog.available, catalog.pick, agent.Turn._agent, agent.Turn._plain)
        catalog.available = lambda role: role == "coder"
        catalog.pick = lambda role: "coder" if role in ("coder", "judge") else None
        agent.Turn._agent = lambda self, messages, role, route="tools": (seen.append((route, role)), ("ok", {}))[1]
        agent.Turn._plain = lambda self, messages, role: (seen.append(("analyze", role)), ("ok", {}))[1]
        try:
            for mode in ("chat", "tools", "analyze"):
                conv = memory.new_conversation()
                memory.add_message(conv, "user", "hello")
                agent.Turn(conv, "hello", mode=mode).run()
        finally:
            catalog.available, catalog.pick, agent.Turn._agent, agent.Turn._plain = old
        self.assertEqual([r for _, r in seen], ["coder", "coder", "coder"])


class SmarterLoopTest(unittest.TestCase):
    def test_lessons_by_meaning(self):
        from newal import lessons, memory
        if os.path.exists(lessons.PATH):
            os.remove(lessons.PATH)
        lessons.add("fix", "On Windows open text files with encoding='utf-8' so Arabic text reads correctly.",
                    "read a csv with arabic names", "UnicodeDecodeError: 'charmap'")
        lessons.add("fix", "Convert input() strings to int before doing arithmetic with them.", "calculator", "")
        # a stand-in for the embedding model: the Arabic request "means" the same as the first lesson
        topic = lambda t: [1.0, 0.0, 0.0] if re.search(r"utf|عربي|arabic", t, re.I) else \
            [0.0, 1.0, 0.0] if re.search(r"int|حاسبة", t) else [0.0, 0.0, 1.0]
        old = (memory.can_embed, memory._embed)
        memory.can_embed = lambda: True
        memory._embed = lambda texts, query=False: [topic(t) for t in texts]
        try:
            found = lessons.relevant("اقرأ ملف فيه أسماء بالعربي")
            again = lessons.relevant("اعملي آلة حاسبة")
            none = lessons.relevant("قصيدة عن البحر")
        finally:
            memory.can_embed, memory._embed = old
            os.remove(lessons.PATH)
        self.assertEqual([x["text"][:10] for x in found], ["On Windows"])
        self.assertEqual([x["text"][:7] for x in again], ["Convert"])
        self.assertEqual(none, [])
        self.assertNotIn("vec", found[0])

    def test_search_when_the_same_error_returns(self):
        answers = ["```python\nprint(undefined_name)\n```", "```python\nprint(undefined_name )\n```",
                   "```python\nundefined_name = 1\nprint(undefined_name)\nassert undefined_name == 1\n```"]
        prompts = []
        old = (agent.pool.chat, catalog.pick, web.search, agent._safe_read)
        agent.pool.chat = lambda role, messages, **kw: (prompts.append(messages[-1]["content"]), {"content": answers.pop(0), "tps": 1})[1]
        catalog.pick = lambda role: None
        web.search = lambda q, n=6: [{"title": "NameError fix", "url": "https://stackoverflow.com/q/1", "snippet": "define it first"}]
        agent._safe_read = lambda url, q: "Define the variable before using it."
        config.update({"web": True})
        events = []
        try:
            t = agent.Turn(None, "print a value", emit=events.append)
            answer, info = t._code([{"role": "user", "content": "print a value"}], "coder")
        finally:
            agent.pool.chat, catalog.pick, web.search, agent._safe_read = old
        self.assertTrue(info["verified"])
        self.assertNotIn("What others found", prompts[1])        # first failure: no search yet
        self.assertIn("What others found for this error", prompts[2])
        self.assertIn("stackoverflow.com", prompts[2])
        self.assertTrue(any(e.get("name") == "web_search" and e.get("state") == "start" and "NameError" in e.get("args", "")
                            for e in events))

    def test_plan_first_changes_nothing(self):
        from newal import workspace
        root = ProjectModeTest.make(self)
        call = lambda n, a: {"id": "", "name": n, "arguments": json.dumps(a)}
        replies = [{"content": "", "tool_calls": [call("read_file", {"path": "shop.py"})]},
                   {"content": "", "tool_calls": [call("edit_file", {"path": "shop.py", "old": "- 1", "new": ""})]},
                   {"content": "1. غيّر shop.py\n2. شغّل الاختبارات", "tool_calls": []}]
        offered = []
        old = (agent.pool.chat, catalog.pick)
        agent.pool.chat = lambda role, messages, tools=None, **kw: (offered.append([d["function"]["name"] for d in tools or []]),
                                                                    dict(replies.pop(0), tps=1))[1]
        catalog.pick = lambda role: None
        try:
            t = agent.Turn(None, "fix the total", mode="project", plan=True, project=root)
            answer, info = t._project([{"role": "user", "content": "fix the total"}], "coder")
        finally:
            agent.pool.chat, catalog.pick = old
        self.assertTrue(info["plan"])
        self.assertIn("غيّر shop.py", answer)
        self.assertNotIn("edit_file", offered[0])
        self.assertNotIn("run", offered[0])
        self.assertIn("read_file", offered[0])
        with open(os.path.join(root, "shop.py")) as f:
            self.assertIn("- 1", f.read())                        # nothing changed

    def test_tests_first_hint(self):
        from newal import workspace
        root = tempfile.mkdtemp()
        with open(os.path.join(root, "app.py"), "w") as f:
            f.write("def add(a, b):\n    return a + b\n")
        seen = []
        old = (agent.pool.chat, catalog.pick)
        agent.pool.chat = lambda role, messages, **kw: (seen.append(messages[-1]["content"]), {"content": "ok", "tool_calls": [], "tps": 1})[1]
        catalog.pick = lambda role: None
        config.update({"tests_first": True})
        try:
            agent.Turn(None, "add subtraction", mode="project", project=root)._project(
                [{"role": "user", "content": "add subtraction"}], "coder")
        finally:
            agent.pool.chat, catalog.pick = old
        self.assertIn("no tests yet", seen[0])


class WebAppTest(unittest.TestCase):
    PAGE = ("<!doctype html><html><body><h1>Cart</h1><div id=t>?</div><script>"
            "document.getElementById('t').textContent = 'Total: ' + (5 + 7);\nmissingFunction();</script></body></html>")

    def setUp(self):
        from newal import browser
        if not browser.find():
            self.skipTest("no headless browser here")

    def test_page_errors_and_screenshot(self):
        ok, out, slow = agent.run_code("browser", self.PAGE)
        self.assertFalse(ok)
        self.assertIn("missingFunction is not defined", out)
        shot = re.search(r"^screenshot: (.+)$", out, re.M)
        self.assertTrue(shot and os.path.getsize(shot.group(1)) > 1000, out)
        ok, out, slow = agent.run_code("browser", self.PAGE.replace("missingFunction();", "console.log('ready');"))
        self.assertTrue(ok, out)
        self.assertIn("ready", out)

    def test_server_request_and_look(self):
        from newal import workspace
        root = tempfile.mkdtemp()
        with open(os.path.join(root, "index.html"), "w") as f:
            f.write(self.PAGE.replace("missingFunction();", ""))
        port = __import__("socket").socket()
        port.bind(("127.0.0.1", 0))
        free = port.getsockname()[1]
        port.close()
        p = workspace.Project(root)
        started = p.start_server("python -m http.server %d --bind 127.0.0.1" % free, "http://127.0.0.1:%d/" % free)
        try:
            self.assertIn("Running at", started)
            self.assertIn("<h1>Cart</h1>", p.http_request("http://127.0.0.1:%d/index.html" % free))
            self.assertIn("Only local", p.http_request("https://example.com/"))
            asked = []
            old = (catalog.sees, agent.pool.chat)
            catalog.sees = lambda role: True
            agent.pool.chat = lambda role, messages, **kw: (asked.append(messages), {"content": "A heading Cart and Total: 12", "tps": 1})[1]
            try:
                seen = agent.Turn(None, "a cart page")._look_page(p, json.dumps({"target": "http://127.0.0.1:%d/" % free}))
            finally:
                catalog.sees, agent.pool.chat = old
            self.assertIn("Total: 12", seen)
            self.assertTrue(asked[0][0]["_images"][0].startswith("data:image/png;base64,"))
        finally:
            p.stop_servers()
        self.assertEqual(p.servers, [])
        self.assertIn("No answer", p.http_request("http://127.0.0.1:%d/" % free))


class LanguagesTest(unittest.TestCase):
    PROGRAMS = {
        "c": ("```c\n#include <stdio.h>\nint add(int a, int b) { return a + b; }\nint main(void) {\n"
              "  printf(\"sum %d\\n\", add(2, 3));\n  return add(2, 3) == 5 ? 0 : 1;\n}\n```", "sum 5", "gcc"),
        "cpp": ("```cpp\n#include <iostream>\n#include <vector>\nint main() {\n  std::vector<int> v{1, 2, 3};\n"
                "  int s = 0; for (int x : v) s += x;\n  std::cout << \"sum \" << s << std::endl;\n  return s == 6 ? 0 : 1;\n}\n```",
                "sum 6", "g++"),
        "csharp": ("```csharp\nusing System;\nConsole.WriteLine(\"sum \" + (2 + 3));\n```", "sum 5", "dotnet"),
        "java": ("```java\npublic class Main {\n  public static void main(String[] a) {\n"
                 "    System.out.println(\"sum \" + (2 + 3));\n  }\n}\n```", "sum 5", "java"),
        "go": ("```go\npackage main\n\nimport \"fmt\"\n\nfunc main() {\n\tfmt.Println(\"sum\", 2+3)\n}\n```", "sum 5", "go"),
        "rust": ("```rust\nfn add(a: i32, b: i32) -> i32 { a + b }\nfn main() {\n    assert_eq!(add(2, 3), 5);\n"
                 "    println!(\"sum {}\", add(2, 3));\n}\n```", "sum 5", "rustc"),
        "ts": ("```typescript\nfunction add(a: number, b: number): number { return a + b; }\nconsole.log(`sum ${add(2, 3)}`);\n```",
               "sum 5", "node"),
    }

    def check(self, runner):
        from newal import langs
        text, want, tool = self.PROGRAMS[runner]
        if not langs.find(tool) or (runner == "ts" and langs.node_major() < 22):
            self.skipTest("%s not installed" % tool)
        lang, code = agent.runnable_block(text)
        self.assertEqual(lang, runner)
        ok, out, slow = agent.run_code(lang, code, timeout=240)
        self.assertTrue(ok, out)
        self.assertIn(want, out)
        # a program that does not compile (or crashes) fails, so the loop fixes it
        broken = {"ts": "const x: number = ;", "go": "package main\nfunc main() { undefinedCall() }",
                  "java": "public class Main { public static void main(String[] a) { int x = ; } }"}.get(runner, "this is not code (")
        ok, out, slow = agent.run_code(lang, broken, timeout=240)
        self.assertFalse(ok, out)

    def test_c(self):
        self.check("c")

    def test_cpp(self):
        self.check("cpp")

    def test_csharp(self):
        self.check("csharp")

    def test_java(self):
        self.check("java")

    def test_go(self):
        self.check("go")

    def test_rust(self):
        self.check("rust")

    def test_typescript(self):
        self.check("ts")

    def test_missing_toolchain_says_what_to_install(self):
        from newal import langs
        old = langs.find
        langs.find = lambda name: None
        try:
            ok, out, slow = agent.run_code("rust", "fn main() {}")
        finally:
            langs.find = old
        self.assertFalse(ok)
        self.assertIn("غير مثبت على الجهاز", out)
        self.assertIn("الإضافات", out)
        self.assertTrue(agent.MISSING_RUNTIME.search(out))

    def test_interactive_in_every_language(self):
        for code in ("int x; scanf(\"%d\", &x);", "std::cin >> x;", "new Scanner(System.in)", "Console.ReadLine()",
                     "fmt.Scan(&x)", "io::stdin().read_line(&mut s)"):
            self.assertTrue(agent.INTERACTIVE.search(code), code)
        self.assertEqual(agent.runnable_block("```c#\nConsole.WriteLine(1);\n```")[0], "csharp")


class SandboxTest(unittest.TestCase):
    def test_plan(self):
        from newal import sandbox
        wsb, script = sandbox.plan("python", r"C:\runs\a & b", r"C:\NewAl\bin\python")
        self.assertIn("<Networking>Disable</Networking>", wsb)
        self.assertIn("<HostFolder>C:\\runs\\a &amp; b</HostFolder>", wsb)          # escaped for XML
        self.assertIn("<ReadOnly>true</ReadOnly>", wsb)                                # Python is read-only
        self.assertIn(r"C:\py\python.exe -X utf8 main.py > out.txt 2>&1", script)
        self.assertIn("shutdown /s /t 0", script)
        wsb, script = sandbox.plan("powershell", r"C:\runs\x")
        self.assertIn("main.ps1", script)
        self.assertNotIn("C:\\py", wsb)

    def test_risky_code_goes_to_the_box_without_asking(self):
        from newal import sandbox
        asked, boxed = [], []
        old = (sandbox.available, sandbox.run, agent.pool.chat, catalog.pick)
        sandbox.available = lambda: True
        sandbox.run = lambda runner, code, folder, timeout=120: (boxed.append(code), (True, "🛡 ok\n(exit code 0)", False))[1]
        agent.pool.chat = lambda role, messages, **kw: {"content": "```python\nimport os\nos.remove('x.txt')\n```", "tps": 1}
        catalog.pick = lambda role: None
        config.update({"sandbox_risky": True})
        try:
            t = agent.Turn(None, "delete x.txt", approve=lambda text: asked.append(text) or False)
            answer, info = t._code([{"role": "user", "content": "delete x.txt"}], "coder")
        finally:
            sandbox.available, sandbox.run, agent.pool.chat, catalog.pick = old
        self.assertEqual(asked, [])
        self.assertEqual(len(boxed), 1)
        # it ran cleanly but checks nothing itself: accepted, not claimed as verified
        self.assertIsNone(info["verified"])
        self.assertEqual(info["note"], "ran_ok")


class UpdateAndDiagnoseTest(unittest.TestCase):
    def test_newest_release(self):
        from newal import updater
        rel = lambda tag, asset=True, draft=False: {"tag_name": tag, "draft": draft, "html_url": "u/" + tag, "body": "notes " + tag,
                                                    "assets": [{"name": "NewAl-Setup.exe", "browser_download_url": "d/" + tag, "size": 5}] if asset else []}
        best = updater.newest([rel("desktop-b31"), rel("desktop-b35", asset=False), rel("desktop-b33"),
                               rel("desktop-b40", draft=True), rel("android-b99"), rel("v1.0")])
        self.assertEqual(best["build"], 33)
        self.assertEqual(best["asset"], "d/desktop-b33")
        self.assertIsNone(updater.newest([rel("v1")]))
        old = updater.BUILD
        updater._state["latest"] = best
        try:
            updater.BUILD = 31
            self.assertTrue(updater.status()["available"])
            updater.BUILD = 33
            self.assertFalse(updater.status()["available"])
            updater.BUILD = 0                                   # a development copy never offers updates
            self.assertFalse(updater.status()["available"])
        finally:
            updater.BUILD = old
            updater._state["latest"] = None

    def test_self_test_report(self):
        import time
        from newal import diagnose

        def boom():
            raise RuntimeError("engine stopped (exit code 0xC0000135)")
        old = diagnose.QUICK
        diagnose.QUICK = [("system", "💻 الجهاز", diagnose.step_system), ("app", "📦 البرنامج", diagnose.step_app),
                          ("models", "🧠 النماذج", diagnose.step_models), ("x", "⚡ عطل مقصود", boom),
                          ("skip", "👁 العيون", lambda: (None, "مش منزّلة"))]
        try:
            diagnose.start(full=False)
            for _ in range(300):
                if not diagnose.status()["running"]:
                    break
                time.sleep(0.05)
        finally:
            diagnose.QUICK = old
        st = diagnose.status()
        self.assertEqual([x["state"] for x in st["steps"]][3:], ["fail", "skip"])
        self.assertIn("0xC0000135", st["report"])
        self.assertIn("⏭ 👁 العيون", st["report"])
        self.assertIn("الإعدادات:", st["report"])
        self.assertTrue(os.path.exists(st["file"]))
        self.assertIn("NewAl build", st["steps"][1]["detail"])

    def test_browser_step(self):
        from newal import browser, diagnose
        if not browser.find():
            self.skipTest("no browser")
        ok, detail = diagnose.step_browser()
        self.assertTrue(ok, detail)

    def test_selftest_image_is_shipped(self):
        from newal import diagnose
        self.assertTrue(diagnose.asset("selftest-error.png"))


class SpeedTest(unittest.TestCase):
    """What keeps the brain fast on a CPU: nothing it already read is sent differently, and nothing is asked twice."""

    def test_history_window_moves_in_jumps(self):
        # under the budget: everything, from the first message
        self.assertEqual(agent.window_start([100] * 10, 5000), 0)
        # over it: the start jumps by half the budget and then stays put while the conversation grows
        starts = [agent.window_start([1000] * n, 8000) for n in range(1, 30)]
        self.assertEqual(starts[:8], [0] * 8)
        changes = sum(1 for a, b in zip(starts, starts[1:]) if a != b)
        self.assertLessEqual(changes, 6)                  # not one move per message (29 messages)
        for n, st in zip(range(1, 30), starts):
            self.assertLessEqual((n - st) * 1000, 8000)   # always within the budget
        self.assertEqual(agent.window_start([20000], 8000), 1)   # one message bigger than the window

    def test_earlier_turns_are_sent_as_they_were(self):
        conv = memory.new_conversation()
        memory.add_message(conv, "user", "first", {"sent": "Notes...\n\nMy message:\nfirst"})
        memory.add_message(conv, "assistant", "answer one")
        memory.add_message(conv, "user", "second")
        old = catalog.available
        catalog.available = lambda role: False
        try:
            t = agent.Turn(conv, "second")
            msgs = t._context("chat", "coder")
        finally:
            catalog.available = old
        self.assertEqual(msgs[1]["content"], "Notes...\n\nMy message:\nfirst")   # not the shorter shown text
        self.assertEqual(msgs[2]["content"], "answer one")
        self.assertEqual(msgs[-1]["content"], "second")
        self.assertEqual(t.sent, "second")

    def test_one_prompt_and_tool_list_for_every_kind_of_request(self):
        config.update({"one_brain": True, "about_me": "", "answer_style": ""})
        prompts = {r: agent._system(r, "coder") for r in ("chat", "tools", "code", "analyze")}
        self.assertEqual(len(set(prompts.values())), 1)
        self.assertIn("Code:", prompts["chat"])
        self.assertNotEqual(agent._system("project", "coder"), prompts["chat"])       # long tasks keep their own
        self.assertEqual(agent._system("chat", "agent"), agent._system("chat", "agent"))
        self.assertNotIn("Code:", agent._system("chat", "agent"))                     # a small model: as before
        self.assertEqual(tools.brain_names(), tools.brain_names())
        self.assertTrue({"web_search", "run_command", "clipboard_get"} <= set(tools.brain_names()))
        config.update({"about_me": "اسمي مصعب", "answer_style": "مختصر"})
        try:
            self.assertIn("اسمي مصعب", agent._system("chat", "coder"))
            self.assertIn("مختصر", agent._system("code", "coder"))
        finally:
            config.update({"about_me": "", "answer_style": ""})

    def test_chat_sends_the_same_tools_and_bans_them_unless_asked(self):
        seen = []
        replies = []
        old = (agent.pool.chat, agent.pool.no_tool_calls)
        agent.pool.no_tool_calls = lambda role: {"logit_bias": [[7, False]]}
        agent.pool.chat = lambda role, messages, tools=None, **kw: (seen.append((tools, kw.get("extra"))),
                                                                   {"content": replies.pop(0) if replies else "أهلاً",
                                                                    "tps": 1, "tool_calls": []})[1]
        config.update({"one_brain": True})

        def ask(text):
            seen.clear()
            return agent.Turn(None, text)._agent([{"role": "system", "content": "s"},
                                                  {"role": "user", "content": text}], "coder", "chat")[0]
        try:
            self.assertEqual(ask("مرحبا"), "أهلاً")
            tools_sent, extra = seen[0]
            self.assertEqual(tools_sent, agent.brain_tools())                 # the shared list, in its fixed order
            self.assertEqual({d["function"]["name"] for d in tools_sent}, set(tools.brain_names()))
            self.assertIn("logit_bias", extra)            # plain chat: one answer, no needless tool round
            self.assertFalse(extra["chat_template_kwargs"]["enable_thinking"])
            self.assertEqual(len(seen), 1)
            ask("افتح إعدادات الشبكة")
            self.assertNotIn("logit_bias", seen[0][1])    # an action: the brain keeps its tools
            replies[:] = ["أنشأت لك الملف notes.txt", "ما عملت شي"]
            self.assertEqual(ask("شو رأيك"), "ما عملت شي")
            self.assertNotIn("logit_bias", seen[1][1])    # it claimed an action: the tools path checks it
        finally:
            agent.pool.chat, agent.pool.no_tool_calls = old

    def test_passing_asserts_replace_the_judge(self):
        judged = []
        old = (agent.pool.chat, agent.Turn._verdict)
        agent.pool.chat = lambda role, messages, **kw: {"content": "```python\ndef sq(x):\n    return x * x\nassert sq(3) == 9\n"
                                                                   "print('ok')\n```", "tps": 1}
        agent.Turn._verdict = lambda self, *a: (judged.append(a), {"ok": True, "reason": "judge"})[1]
        try:
            answer, info = agent.Turn(None, "square")._code([{"role": "user", "content": "square"}], "coder")
            self.assertTrue(info["verified"])
            self.assertEqual(judged, [])                     # its own asserts were the check
            # 💭: the judge looks as well
            answer, info = agent.Turn(None, "square", think=True)._code([{"role": "user", "content": "square"}], "coder")
            self.assertEqual(len(judged), 1)
        finally:
            agent.pool.chat, agent.Turn._verdict = old

    def test_self_tested(self):
        py = {"project": False, "lang": "python", "files": {}}
        self.assertTrue(agent.self_tested(py, "assert f(2) == 4\nprint('ok')", "ok"))
        self.assertTrue(agent.self_tested(py, "def test():\n    assert f(1)\ntest()", ""))
        self.assertFalse(agent.self_tested(py, "print(f(2))", "4"))
        js = {"project": False, "lang": "node", "files": {}}
        self.assertTrue(agent.self_tested(js, "const assert = require('assert');\nassert.strictEqual(f(), 1)", ""))
        self.assertFalse(agent.self_tested(js, "console.assert(f() === 1)", "Assertion failed"))   # it only printed
        self.assertTrue(agent.self_tested({"project": True, "tests": True, "lang": "python", "files": {}}, "", ""))

    def test_search_words_without_the_brain(self):
        self.assertEqual(agent.plain_query("شو آخر أخبار الذكاء الاصطناعي هالأسبوع؟"), "آخر أخبار الذكاء الاصطناعي هذا الأسبوع")
        self.assertEqual(agent.plain_query("مين ربح مباراة برشلونة امبارح"), "ربح مباراة برشلونة أمس")
        asked = []
        old = (agent.pool.complete_json, catalog.available)
        agent.pool.complete_json = lambda *a, **k: asked.append(a) or ["x"]
        catalog.available = lambda role: role == "coder"
        try:
            self.assertEqual(agent.search_queries("شو سعر الذهب اليوم؟"), ["سعر الذهب اليوم"])
        finally:
            agent.pool.complete_json, catalog.available = old
        self.assertEqual(asked, [])                          # no model call for the search words

    def test_background_work_waits_for_a_quiet_moment(self):
        import threading
        import time
        done = threading.Event()
        old = agent.IDLE_SECONDS
        agent.IDLE_SECONDS = 0.5
        try:
            with agent._later_lock:
                agent._active[0] += 1                        # a question is being answered
            agent.later(done.set)
            self.assertFalse(done.wait(1.0))
            with agent._later_lock:
                agent._active[0] -= 1
                agent._active[1] = time.time()
            self.assertTrue(done.wait(8))
        finally:
            agent.IDLE_SECONDS = old

    def test_mtp_file_and_older_file(self):
        from newal import engine
        models = config.MODELS
        main = os.path.join(models, catalog.MODELS["coder"]["file"])
        old_file = os.path.join(models, catalog.MODELS["coder"]["legacy"][0]["file"])
        for p in (main, old_file):
            if os.path.exists(p):
                os.remove(p)
        try:
            with open(old_file, "wb") as f:
                f.write(b"GGUF" + b"\0" * 16)
            self.assertTrue(catalog.available("coder"))
            self.assertTrue(catalog.legacy("coder"))
            self.assertFalse(catalog.mtp("coder"))
            self.assertEqual(catalog.path("coder"), old_file)
            st = {m["role"]: m for m in catalog.status()}["coder"]
            self.assertTrue(st["ready"] and st["upgrade"])
            with open(main, "wb") as f:
                f.write(b"GGUF" + b"\0" * 16)
            self.assertEqual(catalog.path("coder"), main)
            self.assertTrue(catalog.mtp("coder"))
            self.assertFalse(catalog.legacy("coder"))
            config.update({"mtp": True})
            srv = engine.Server("coder")
            self.assertTrue(srv.use_mtp())
            # an engine that cannot draft with MTP starts again without it
            tried = []

            def fake_start(self_, timeout):
                tried.append(self_.mtp)
                if self_.mtp:
                    raise RuntimeError("failed to create MTP context")
            real = engine.Server._start
            engine.Server._start = fake_start
            try:
                srv.start()
            finally:
                engine.Server._start = real
                engine._mtp_broken.discard("coder")
            self.assertEqual(tried, [True, False])
            config.update({"mtp": False})
            self.assertFalse(engine.Server("coder").use_mtp())
            config.update({"mtp": True})
        finally:
            for p in (main, old_file):
                if os.path.exists(p):
                    os.remove(p)

    def test_thinking_kwargs_keep_earlier_answers_exact(self):
        from newal import engine
        sent = []

        class Resp:
            def __init__(self):
                self.lines = [b'data: {"choices":[{"delta":{"content":"hi"}}]}\n', b"data: [DONE]\n"]

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def __iter__(self):
                return iter(self.lines)

        class Srv:
            role = "coder"
            url = "http://x"
            model = catalog.MODELS["coder"]
            used = 0

            def vision(self):
                return False
        old = (engine.pool.get, engine.urllib.request.urlopen)
        engine.pool.get = lambda role: Srv()
        engine.urllib.request.urlopen = lambda req, timeout=0: (sent.append(json.loads(req.data)), Resp())[1]
        try:
            engine.pool.chat("coder", [{"role": "user", "content": "x"}],
                             extra={"chat_template_kwargs": {"enable_thinking": True}, "thinking_budget_tokens": 5})
        finally:
            engine.pool.get, engine.urllib.request.urlopen = old
        kw = sent[0]["chat_template_kwargs"]
        self.assertEqual(kw, {"enable_thinking": True, "preserve_thinking": True})
        self.assertEqual(sent[0]["thinking_budget_tokens"], 5)
        self.assertEqual(engine.pool.calls[-1]["role"], "coder")

    def test_warm_up_reads_the_shared_start(self):
        from newal import speed
        calls = []
        old = (speed.pool.get, speed.pool.chat, catalog.available, config.get("preload"))
        speed.pool.get = lambda role: calls.append(("get", role))
        speed.pool.chat = lambda role, messages, tools=None, **kw: calls.append(("chat", role, messages[0]["content"],
                                                                                 len(tools or [])))
        catalog.available = lambda role: role == "coder"
        config.update({"preload": True, "one_brain": True})
        try:
            speed.warm_up(wait=True)
        finally:
            speed.pool.get, speed.pool.chat, catalog.available = old[:3]
            config.update({"preload": old[3]})
        self.assertEqual(calls[0], ("get", "coder"))
        chat = [c for c in calls if c[0] == "chat"]
        self.assertEqual(chat[0][2], agent._system("chat", "coder"))
        self.assertEqual(chat[0][3], len(tools.brain_names()))
        self.assertEqual(speed.state()["state"], "ready")


class FeaturesTest(unittest.TestCase):
    def test_deep_research(self):
        searched, read, prompts = [], [], []
        old = (agent.pool.complete_json, agent.pool.chat, agent.web.search, agent._safe_read)
        agent.pool.complete_json = lambda role, msgs, schema, **kw: ["سعر الذهب اليوم", "gold price today"]
        agent.web.search = lambda q, n=6: (searched.append(q), [{"title": "T " + q, "url": "https://x/" + q, "snippet": "s"}])[1]
        agent._safe_read = lambda url, q, n=1500: (read.append(url), "page " + url)[1]
        agent.pool.chat = lambda role, messages, **kw: (prompts.append(messages[-1]["content"]), {"content": "تقرير [1][2]", "tps": 1})[1]
        try:
            t = agent.Turn(None, "كم سعر الذهب؟", mode="research")
            answer, info = t._research([{"role": "user", "content": "كم سعر الذهب؟"}], "coder")
        finally:
            agent.pool.complete_json, agent.pool.chat, agent.web.search, agent._safe_read = old
        self.assertEqual(searched, ["سعر الذهب اليوم", "gold price today"])
        self.assertEqual(len(read), 2)
        self.assertEqual(info["sources"], 2)
        self.assertIn("[2] T gold price today", prompts[0])
        self.assertIn("cite them as [n]", prompts[0])
        self.assertEqual(t.sent, prompts[0])

    def test_charts_a_program_saves_are_shown(self):
        code = ("open('chart.png', 'wb').write(b'\\x89PNG fake')\nprint('total', 42)\nassert 42 == 42\n")
        ok, out, slow = agent.run_code("python", code)
        self.assertTrue(ok, out)
        imgs = agent.images_in(out)
        self.assertEqual([os.path.basename(p) for p in imgs], ["chart.png"])
        events = []
        old = agent.pool.chat
        agent.pool.chat = lambda role, messages, **kw: {"content": "```python\n" + code + "```", "tps": 1}
        try:
            answer, info = agent.Turn(None, "chart", emit=events.append)._code([{"role": "user", "content": "chart"}], "coder")
        finally:
            agent.pool.chat = old
        self.assertTrue(info["verified"])
        self.assertEqual(len(info["images"]), 1)
        self.assertTrue(any(e["type"] == "image" for e in events))

    def test_data_file_goes_as_path_and_preview(self):
        path = os.path.join(HOME, "sales.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write("month,amount\n" + "".join("m%d,%d\n" % (i, i * 10) for i in range(500)))
        t = agent.Turn(None, "حلل المبيعات", attachments=[path])
        old = catalog.available
        catalog.available = lambda role: False
        try:
            msgs = t._context("code", "coder")
        finally:
            catalog.available = old
        user = msgs[-1]["content"]
        self.assertIn("[Data file: %s]" % path, user)
        self.assertIn("m3,30", user)
        self.assertNotIn("m400,4000", user)          # not the whole table


class OutputsTest(unittest.TestCase):
    def test_program_files_are_listed_and_zipped(self):
        prog = agent.program("```python\nimport os\nos.makedirs('out', exist_ok=True)\n"
                             "open('out/report.txt', 'w').write('hi')\nprint('done')\n```")
        ok, out, _, folder = agent.run_program(prog)
        self.assertTrue(ok, out)
        self.assertTrue(folder and os.path.isdir(folder))
        o = agent.outputs(folder)
        rels = [f["rel"] for f in o["files"]]
        self.assertIn("out/report.txt", rels)
        self.assertIn("main.py", rels)
        z = agent.zip_folder(folder)
        with zipfile.ZipFile(z) as zf:
            names = zf.namelist()
        self.assertTrue(any(n.endswith("out/report.txt") for n in names), names)

    def test_outputs_skip_caches(self):
        d = os.path.join(config.WORKSPACE, "runs", "skip-test")
        os.makedirs(os.path.join(d, "__pycache__"), exist_ok=True)
        open(os.path.join(d, "__pycache__", "x.pyc"), "w").close()
        open(os.path.join(d, "a.py"), "w").close()
        self.assertEqual([f["rel"] for f in agent.outputs(d)["files"]], ["a.py"])


class ChatMemoryAndProjectsTest(unittest.TestCase):
    def test_earlier_chats_are_found_but_not_the_current_one(self):
        a = memory.new_conversation("سفر")
        memory.add_message(a, "user", "شو أحسن وقت لزيارة مدينة بترا بالأردن؟")
        memory.add_message(a, "assistant", "أحسن وقت لزيارة البترا بالربيع (آذار لأيار) لأن الجو معتدل.")
        self.assertEqual(memory.index_chat(a), 1)
        self.assertEqual(memory.index_chat(a), 0)                       # indexed once
        self.assertNotIn(a, memory.unindexed_chats())
        b = memory.new_conversation("جديد")
        found = memory.search("زيارة البترا بالربيع", k=3, skip_conv=b)
        self.assertTrue(any(n["kind"] == "chat" and "البترا" in n["text"] for n in found), found)
        self.assertFalse(any(n["kind"] == "chat" for n in memory.search("زيارة البترا بالربيع", k=3, skip_conv=a)))
        config.update({"chat_memory": False})
        try:
            self.assertFalse(any(n["kind"] == "chat" for n in memory.search("زيارة البترا بالربيع", k=3)))
        finally:
            config.update({"chat_memory": True})
        self.assertIn("سفر", agent.note_label([n for n in found if n["kind"] == "chat"][0]))
        memory.delete_conversation(a)
        self.assertFalse(any(n["kind"] == "chat" for n in memory.search("زيارة البترا بالربيع", k=3)))

    def test_temporary_chats_are_not_remembered(self):
        t = memory.new_conversation(temp=True)
        memory.add_message(t, "user", "سر: كلمة السر تبعي هي بندورة123 لا تحكيها لحدا")
        memory.add_message(t, "assistant", "تمام، ما رح احكيها لحدا أبداً أبداً.")
        self.assertEqual(memory.index_chat(t), 0)

    def test_project_instructions_files_and_chats(self):
        pid = memory.save_project(0, "رسالة التخرج", "جاوب بالفصحى وبأمثلة عن الري الذكي.")
        folder = os.path.join(memory.PROJECT_FILES, str(pid))
        with open(os.path.join(folder, "notes.txt"), "w", encoding="utf-8") as f:
            f.write("حساس الرطوبة موصول على المنفذ GPIO34 ويقرأ كل عشر دقائق.")
        memory.index_file(os.path.join(folder, "notes.txt"), "project_file")
        c = memory.new_conversation(project=pid)
        memory.add_message(c, "user", "مرحبا")
        self.assertEqual([x["id"] for x in memory.project_chats(pid)], [c])
        self.assertEqual(memory.projects()[0]["files"], 1)
        prompt = agent.project_prompt(pid)
        self.assertTrue(prompt.startswith("\n\n# Project: رسالة التخرج"))
        self.assertIn("notes.txt", prompt)
        found = memory.search("على أي منفذ حساس الرطوبة", k=2, project=pid)
        self.assertTrue(found and "GPIO34" in found[0]["text"])
        memory.delete_project(pid)
        self.assertFalse(os.path.exists(folder))
        self.assertEqual(memory.conversation(c)["project"], 0)          # the chat stays, outside the project


class SchedulesTest(unittest.TestCase):
    def test_next_run(self):
        import datetime
        from newal import schedules
        base = datetime.datetime(2026, 9, 28, 9, 30).timestamp()            # a Monday, 09:30
        nxt = lambda item: datetime.datetime.fromtimestamp(schedules.next_run(item, base))
        self.assertEqual(nxt({"kind": "daily", "time": "08:00"}), datetime.datetime(2026, 9, 29, 8, 0))
        self.assertEqual(nxt({"kind": "daily", "time": "10:15"}), datetime.datetime(2026, 9, 28, 10, 15))
        self.assertEqual(nxt({"kind": "weekly", "time": "18:00", "days": [3]}), datetime.datetime(2026, 10, 1, 18, 0))
        self.assertEqual(nxt({"kind": "hourly", "time": "00:05"}), datetime.datetime(2026, 9, 28, 10, 5))
        self.assertIsNone(schedules.next_run({"kind": "once", "at": base - 60}, base))
        with self.assertRaises(ValueError):
            schedules.save({"kind": "daily", "time": "25:00", "prompt": "x"})

    def test_due_task_runs_into_its_own_chat(self):
        import time as _time
        from newal import schedules

        class FakeTurn:
            def __init__(self, conv, text, **kw):
                self.text = text

            def run(self):
                return "نتيجة: " + self.text.splitlines()[-1], {"route": "chat"}
        item = schedules.save({"name": "تذكير", "prompt": "ذكرني بالدوا", "kind": "once", "at": _time.time() + 3600})
        self.assertIn("تمت جدولة", schedules.add_from_tool("اخبار", "لخص الأخبار", "daily", "08:00"))
        old = agent.Turn
        agent.Turn = FakeTurn
        try:
            schedules.tick(now=_time.time() + 7200)
        finally:
            agent.Turn = old
        done = next(x for x in schedules.listing() if x["id"] == item["id"])
        self.assertEqual(done["last_status"], "ok")
        self.assertFalse(done["enabled"])                                  # once: done
        msgs = memory.messages(done["conv"])
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant"])
        self.assertEqual(msgs[1]["content"], "نتيجة: ذكرني بالدوا")
        self.assertEqual(memory.conversation(done["conv"])["title"], "⏰ تذكير")
        self.assertTrue(any(n["id"] == item["id"] for n in schedules.notices(clear=True)))
        self.assertIn("schedule", tools.brain_names())


class KvCacheTest(unittest.TestCase):
    def test_history_window_is_the_same_for_read_ahead_and_the_next_turn(self):
        c = memory.new_conversation()
        for i in range(30):
            memory.add_message(c, "user", "سؤال %d " % i + "x" * 900)
            memory.add_message(c, "assistant", "جواب %d " % i + "y" * 900)
        ahead = agent.history(c, "coder", "chat")
        memory.add_message(c, "user", "السؤال الجديد")
        nxt = agent.history(c, "coder", "chat")
        self.assertEqual(nxt[:-1], ahead)
        self.assertEqual(nxt[-1]["content"], "السؤال الجديد")

    def test_nothing_happens_without_a_loaded_brain(self):
        from newal import kvcache
        self.assertEqual(kvcache.after_answer(1, "coder", [{"role": "system", "content": "x"}]), 0)
        self.assertFalse(kvcache.resume(1, "coder"))
        self.assertEqual(agent.read_ahead(1, "agent"), 0)


class ImagesTest(unittest.TestCase):
    def test_models_are_checked_by_their_own_header(self):
        for role, magic in (("image", b"GGUF"),):
            path = os.path.join(config.MODELS, catalog.MODELS[role]["file"])
            self.assertFalse(catalog.available(role))
            with open(path, "wb") as f:
                f.write(b"junk")
            self.assertFalse(catalog.available(role))
            with open(path, "wb") as f:
                f.write(magic + b"\0" * 16)
            self.assertTrue(catalog.available(role))
            os.remove(path)
        self.assertNotIn("image", catalog.needed())
        self.assertTrue(next(m for m in catalog.status() if m["role"] == "image")["optional"])

    def test_draw_requests(self):
        for t in ("ارسملي قطة بتقرأ كتاب", "ارسم غروب شمس على البحر", "اعملي صورة لسيارة حمرا", "draw me a dragon"):
            self.assertTrue(agent.DRAW.search(t), t)
        for t in ("ارسم رسم بياني للمبيعات من ملف excel", "شو يعني ارسم؟", "draw a chart of my data"):
            self.assertFalse(agent.DRAW.search(t), t)

    def test_drawing_without_the_model_explains_what_to_download(self):
        answer, meta = agent.Turn(None, "ارسملي قطة", mode="auto").run()
        self.assertEqual(meta["route"], "image")
        self.assertIn("⚠", answer)

    def test_tool_pictures_show_in_the_chat(self):
        from newal import images
        png = os.path.join(config.WORKSPACE, "images", "t.png")
        os.makedirs(os.path.dirname(png), exist_ok=True)
        open(png, "wb").close()
        old = images.generate
        images.generate = lambda p, w=512, h=512: {"path": png, "seconds": 1, "width": 512, "height": 512, "prompt": p}
        events = []
        try:
            t = agent.Turn(None, "x", emit=events.append)
            t._tool("generate_image", {"prompt": "a cat"})
        finally:
            images.generate = old
        self.assertEqual(t.images, [png])
        self.assertTrue(any(e["type"] == "image" and e["caption"].startswith("🎨") for e in events))


class KaggleConnectTest(unittest.TestCase):
    def test_one_click_uses_credentials_already_here(self):
        from newal import connectors
        home = tempfile.mkdtemp()
        os.makedirs(os.path.join(home, ".kaggle"))
        with open(os.path.join(home, ".kaggle", "kaggle.json"), "w") as f:
            json.dump({"username": "musab", "key": "k" * 32}, f)
        sent = []
        old_api, old_env = connectors._api, {k: os.environ.get(k) for k in ("HOME", "USERPROFILE")}
        connectors._api = lambda url, h=None, *a, **k: (sent.append(h), [{"ref": "musab/nb"}])[1]
        os.environ["HOME"] = os.environ["USERPROFILE"] = home          # Windows reads USERPROFILE
        config.update({"kaggle_username": "", "kaggle_key": "", "kaggle_token": ""})
        try:
            r = connectors.kaggle_connect(open_page=False)
        finally:
            connectors._api = old_api
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertEqual(r["state"], "ok", r)
        self.assertTrue(connectors.kaggle_connected())
        self.assertTrue(sent[0]["Authorization"].startswith("Basic "))
        config.update({"kaggle_token": "KGAT_" + "a" * 30})
        self.assertEqual(connectors._kg(), {"Authorization": "Bearer KGAT_" + "a" * 30})
        config.update({"kaggle_username": "", "kaggle_key": "", "kaggle_token": ""})


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from newal import server
        cls.httpd = server.serve(0)
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def post(self, path, body, headers):
        req = urllib.request.Request(self.base + path, json.dumps(body).encode(), headers, method="POST")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_ui_and_state(self):
        with urllib.request.urlopen(self.base + "/") as r:
            self.assertIn(b"NewAl", r.read())
        with urllib.request.urlopen(self.base + "/api/state") as r:
            self.assertIn("models", json.loads(r.read()))

    def test_cross_site_requests_refused(self):
        self.assertEqual(self.post("/api/settings", {"auto_run": True}, {"Content-Type": "text/plain"}), 403)
        self.assertEqual(self.post("/api/settings", {}, {"X-NewAl": "1", "Origin": "https://evil.example"}), 403)
        self.assertEqual(self.post("/v1/chat/completions", {}, {"Content-Type": "text/plain"}), 403)
        self.assertEqual(self.post("/api/settings", {}, {"X-NewAl": "1", "Origin": "vscode-webview://abc"}), 403)
        self.assertEqual(self.post("/api/settings", {}, {"X-NewAl": "1", "Content-Type": "application/json"}), 200)
        self.assertFalse(config.get("auto_run"))

    def test_workspace_files_served_for_preview(self):
        d = os.path.join(config.WORKSPACE, "runs", "site")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as f:
            f.write("<h1>مرحبا</h1>")
        with urllib.request.urlopen(self.base + "/out/runs/site/") as r:
            self.assertIn("text/html", r.headers["Content-Type"])
            self.assertIn("مرحبا", r.read().decode())
        for bad in ("/out/../config.json", "/out/%2e%2e/%2e%2e/etc/passwd"):
            try:
                urllib.request.urlopen(self.base + bad)
                self.fail("served " + bad)
            except urllib.error.HTTPError as e:
                self.assertEqual(e.code, 404)
        self.assertEqual(self.post("/api/zip", {"folder": HOME}, {"X-NewAl": "1"}), 404)
        self.assertEqual(self.post("/api/zip", {"folder": d}, {"X-NewAl": "1"}), 200)

    def test_static_path_traversal(self):
        try:
            urllib.request.urlopen(self.base + "/ui/../config.py")
            self.fail("served a file outside ui/")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)


if __name__ == "__main__":
    unittest.main()
