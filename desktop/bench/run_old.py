"""Runs the coding speed test (bench/tasks.py) through the current NewAl's project mode (🧑‍💻), the way the app runs a
task: the model is loaded and has read project mode's instructions in advance, then each task starts a new chat.

    python bench/run_old.py --code <folder with the old newal package> --model <file.gguf> --llama-server <exe>

What is timed: from sending the task to the final answer (what the user waits for). What is not: loading the model and
reading the fixed instructions in advance (the app does that before the user types)."""

import argparse
import json
import os
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tasks  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True, help="folder that contains the old newal package (desktop/)")
    ap.add_argument("--model", required=True)
    ap.add_argument("--llama-server", required=True)
    ap.add_argument("--home", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--tasks", nargs="*", default=[])
    ap.add_argument("--limit", type=int, default=1200, help="seconds per task")
    ap.add_argument("--as", dest="as_name", default="Qwen3.5-4B-Q4_K_M.gguf",
                    help="the file name the old catalog knows the model by (Qwen3.6-35B-A3B-MTP-UD-IQ3_S.gguf makes it "
                         "the brain, with MTP)")
    a = ap.parse_args()

    home = a.home or tempfile.mkdtemp(prefix="newal-old-home-")
    os.makedirs(os.path.join(home, "models"), exist_ok=True)
    link = os.path.join(home, "models", a.as_name)                    # the name the old catalog knows
    if not os.path.exists(link):
        os.symlink(os.path.abspath(a.model), link)
    os.environ["NEWAL_HOME"] = home
    os.environ["NEWAL_LLAMA_SERVER"] = os.path.abspath(a.llama_server)
    sys.path.insert(0, os.path.abspath(a.code))

    from newal import agent, catalog, memory, speed  # noqa: E402
    from newal.engine import Cancelled, pool  # noqa: E402

    role = speed.brain()
    print("model role:", role, catalog.MODELS[role]["title"], flush=True)
    t0 = time.time()
    pool.get(role)
    print("loaded in %.1f s" % (time.time() - t0), flush=True)

    results = []
    for task in tasks.by_id(a.tasks):
        folder = tasks.make(task)
        # Project mode's start read in advance, as when 🧑‍💻 is chosen in the app (untimed).
        for prompt in agent.warm_prompts(role, project=True):
            pool.chat(role, prompt["messages"], tools=prompt.get("tools"), max_tokens=1, temperature=0)
        first = len(pool.calls)
        warm = list(pool.calls)[-1] if pool.calls else {}
        conv = memory.new_conversation("bench " + task["id"], temp=True)
        cancel = threading.Event()
        timer = threading.Timer(a.limit, cancel.set)
        events = []
        turn = agent.Turn(conv, task["prompt"], mode="project", project=folder, cancel=cancel,
                          emit=lambda e: events.append(e.get("type")), approve=lambda _: True)
        turn.learn = False
        answer, info, error = "", {}, ""
        started = time.time()
        timer.start()
        try:
            answer, info = turn.run()
        except Cancelled:
            error = "timeout"
        except Exception as e:  # noqa: BLE001
            error = "%s: %s" % (type(e).__name__, e)
        finally:
            timer.cancel()
        seconds = time.time() - started
        ok, why = tasks.check(task, folder, answer) if not error else (False, error)
        calls = list(pool.calls)[first:]
        rec = {"id": task["id"], "ok": ok, "why": why, "seconds": round(seconds, 1),
               "steps": info.get("steps"), "model_calls": len(calls),
               "prompt_tokens": sum(c["prompt"] for c in calls), "cached_tokens": sum(c["cached"] for c in calls),
               "generated_tokens": sum(c["generated"] for c in calls),
               "prompt_s": round(sum(c["prompt_ms"] for c in calls) / 1000, 1),
               "gen_s": round(sum(c["gen_ms"] for c in calls) / 1000, 1),
               "warm_cached": warm.get("cached", 0) + warm.get("prompt", 0),
               "answer": (answer or "")[:600]}
        results.append(rec)
        print(json.dumps(rec, ensure_ascii=False), flush=True)
        try:
            memory.delete_conversation(conv)
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(folder, ignore_errors=True)
    pool.stop_all()
    total = sum(r["seconds"] for r in results)
    summary = {"program": "current NewAl (project mode)", "tasks": len(results),
               "passed": sum(r["ok"] for r in results), "seconds": round(total, 1), "results": results}
    print("TOTAL %.1f s, %d/%d passed" % (total, summary["passed"], len(results)), flush=True)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()
