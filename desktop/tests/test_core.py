"""Tests that need no model: files, tools, memory, training data, routing rules, server safety."""

import json
import os
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
        self.assertIn("vscode_open", tools.select("افتحه بـ VS Code"))

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
        self.assertIn("غير معروفة", tools.call("nope", {}))
        self.assertIn("وسائط خاطئة", tools.call("write_file", {"path": "a"}))
        self.assertIn("غير صالحة", tools.call("read_file", "{not json"))

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
        self.assertIsNone(agent.runnable_block("```html\n<p>x</p>\n```"))

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
        rid = training.log("coder", "code", [{"role": "user", "content": "write fizzbuzz in python please"}],
                           "```python\n...\n```", verified=False)
        rid2 = training.log("coder", "code", [{"role": "user", "content": "reverse a string in python"}], "s[::-1]",
                            verified=True)
        training.feedback(rid, True)            # the user's 👍 beats the failed run
        good = {r["id"]: r["good"] for r in training.records("coder")}
        self.assertTrue(good[rid])
        self.assertTrue(good[rid2])
        ex = training.examples("coder", "please write fizzbuzz in python")
        self.assertEqual(ex[0][0], "write fizzbuzz in python please")
        path, n = training.export("coder")
        self.assertEqual(n, 2)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.loads(f.readline())["messages"][-1]["role"], "assistant")
        self.assertIn("coder", training.update()["prepared"])


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

    def test_goal_tools_follow_connections(self):
        config.update({"github_token": ""})
        self.assertNotIn("github_repos", tools.goal_names())
        self.assertIn("run_command", tools.goal_names())
        self.assertIn("schedule_task", tools.goal_names())


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

    def test_static_path_traversal(self):
        try:
            urllib.request.urlopen(self.base + "/ui/../config.py")
            self.fail("served a file outside ui/")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)


if __name__ == "__main__":
    unittest.main()
