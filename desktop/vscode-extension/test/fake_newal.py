"""A real NewAl server for testing the VS Code extension: the model is replaced by a scripted turn."""
import json, os, sys, tempfile, time
os.environ["NEWAL_HOME"] = tempfile.mkdtemp(prefix="newal-vsc-")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from newal import agent, config, memory, server, tasks

project = sys.argv[1]
state = {"approved": None}


def fake_run(self):
    self.emit({"type": "status", "text": "🧑‍💻 خطوة 1…"})
    self.emit({"type": "tool", "name": "read_file", "args": '{"path": "shop.py"}', "state": "start"})
    self.emit({"type": "tool", "name": "read_file", "state": "done", "result": "shop.py (2 lines)"})
    state["approved"] = self.approve("تشغيل أمر داخل المشروع: rm -rf build")
    self.emit({"type": "run", "lang": "python -m pytest -q", "ok": True, "attempt": 1, "output": "1 passed"})
    return "صلّحت الجمع (approved=%s, project=%s)" % (state["approved"], self.project), \
           {"route": "project", "checkpoint": "cp1", "files": [{"path": "shop.py"}], "project": self.project}


agent.Turn.run = fake_run
# a finished background task with its own copy of the project
tree = os.path.join(tasks.TREES, "t1")
os.makedirs(tree)
with open(os.path.join(tree, "shop.py"), "w") as f:
    f.write("def total(prices):\n    return sum(prices)\n")
tasks._save([{"id": "t1", "project": project, "prompt": "fix the total", "status": "done", "created": time.time(),
              "conv": None, "kind": "copy", "files": [{"path": "shop.py", "new": False, "plus": 1, "minus": 1}],
              "verified": True, "summary": "done"}])
httpd = server.serve(0)
config.update({"api_port": httpd.server_address[1]})
print(httpd.server_address[1], flush=True)
time.sleep(120)
