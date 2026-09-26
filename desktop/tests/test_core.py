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
    def test_rules(self):
        self.assertEqual(router.route("Traceback (most recent call last):\n  File x"), "code")
        self.assertEqual(router.route("```js\nlet a\n```"), "code")
        self.assertEqual(router.route("مرحبا"), "chat")        # no models: safe default


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
