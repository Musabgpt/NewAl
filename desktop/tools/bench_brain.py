"""Measures the brain (Qwen3.6-35B-A3B) on this computer as NewAl runs it: the same llama-server, the same model file
and the same server options as engine.Server (without the eyes and the slot folder, which do not change text speed),
for a few thread counts, with and without MTP.

Reading: prompt tokens per second on a fresh ~1700-token prompt (code and Arabic). Writing: generated tokens per
second on a code answer and an Arabic answer, with NewAl's sampling. Each setting starts a new engine, so nothing is
cached between them. The last setting repeats the first: a laptop that heats up writes slower at the end.

    python tools/bench_brain.py                      (NewAl must be closed: the brain needs ~17 GB of RAM)
    python tools/bench_brain.py --configs 3/8/mtp,4/8/mtp --out C:\\temp
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from newal import catalog, config, diagnose, engine  # noqa: E402

# threads for writing / threads for reading / MTP on or off; "read" also times reading a fresh prompt.
DEFAULT = "3/8/mtp/read,4/8/mtp,4/4/mtp/read,3/8/off,4/8/off,6/8/mtp,3/8/mtp"

CODE_ASK = ("Write a Python function is_palindrome(s) that ignores spaces, punctuation and letter case, and a function "
            "longest_palindrome(words) that returns the longest palindrome in a list (None when there is none). Check "
            "both with 8 asserts and print 'ok'. One code block, then one short sentence.")
ARABIC_ASK = "اكتب فقرة واحدة بالعربية الفصحى (حوالي 150 كلمة) عن فوائد القراءة اليومية للطلاب، بدون عناوين."


def read_prompt():
    code = "\n".join('def helper_%d(values):\n    """Weighted total of batch %d."""\n    return sum(v * %d for v in values) + %d\n'
                     % (i, i, i % 7 + 1, i) for i in range(40))
    arabic = " ".join("الفقرة %d: يقرأ النموذج هذا النص ليقيس سرعة القراءة على المعالج، ثم يجيب عن سؤال قصير عنه." % i
                      for i in range(20))
    return code + "\n\n" + arabic + "\n\nWhat does helper_7 return for [1, 2]? Answer with the number only."


def post(url, body, timeout=1800):
    req = urllib.request.Request(url, json.dumps(body).encode("utf-8"), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def ask(url, text, max_tokens):
    """One chat request with NewAl's sampling (engine.Pool.chat); llama.cpp's own timings."""
    body = {"messages": [{"role": "user", "content": text}], "max_tokens": max_tokens, "temperature": 0.3,
            "top_p": 0.95, "min_p": 0.05, "repeat_penalty": 1.05, "seed": 1,
            "chat_template_kwargs": {"enable_thinking": False, "preserve_thinking": True}}
    started = time.time()
    out = post(url + "/v1/chat/completions", body)
    t = out.get("timings") or {}
    return {"read_tps": round(t.get("prompt_per_second", 0), 1), "read_n": t.get("prompt_n", 0),
            "write_tps": round(t.get("predicted_per_second", 0), 2), "write_n": t.get("predicted_n", 0),
            "drafted": t.get("draft_n", 0), "accepted": t.get("draft_n_accepted", 0),
            "seconds": round(time.time() - started, 1)}


def run(spec, exe, model, log):
    parts = spec.split("/")
    threads, batch, mtp, read = int(parts[0]), int(parts[1]), parts[2] == "mtp", "read" in parts[3:]
    port = engine._free_port()
    # engine.Server._start for the brain, with the thread counts under test
    args = [exe, "-m", model, "--host", "127.0.0.1", "--port", str(port), "-t", str(threads), "--no-webui",
            "-c", str(max(engine.MIN_CONTEXT, int(config.get("brain_context") or 0))), "-np", "1", "--jinja",
            "-tb", str(batch), "--cache-ram", "2048"] + (engine.MTP_ARGS if mtp else [])
    log.write("\n\n===== %s: %s\n" % (spec, " ".join(args)))
    log.flush()
    started = time.time()
    proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            cwd=os.path.dirname(exe), creationflags=0x08000000 if config.IS_WINDOWS else 0)
    url = "http://127.0.0.1:%d" % port
    try:
        while True:
            if proc.poll() is not None:
                raise RuntimeError("llama-server stopped (exit code %s)" % proc.returncode)
            try:
                with urllib.request.urlopen(url + "/health", timeout=2) as r:
                    if r.status == 200:
                        break
            except OSError:
                pass
            if time.time() - started > 900:
                raise RuntimeError("the model did not load in 15 minutes")
            time.sleep(0.5)
        row = {"config": spec, "threads": threads, "batch_threads": batch, "mtp": mtp,
               "load_s": round(time.time() - started, 1)}
        if read:
            r = ask(url, read_prompt(), 1)
            row.update(read_tps=r["read_tps"], read_n=r["read_n"])
        for name, text in (("code", CODE_ASK), ("arabic", ARABIC_ASK)):
            r = ask(url, text, 300)
            row[name] = r
        return row
    finally:
        proc.terminate()
        try:
            proc.wait(20)
        except subprocess.TimeoutExpired:
            proc.kill()


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--configs", default=DEFAULT, help="writing threads/reading threads/mtp|off[/read], comma-separated")
    p.add_argument("--out", default=config.LOGS, help="folder for the results (JSON) and the engine log")
    p.add_argument("--min-free-gb", type=float, default=16)
    a = p.parse_args()
    exe = config.find_tool("llama-server") or os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "NewAl",
                                                           "bin", "llama-server.exe")
    model = catalog.path("coder")
    if not os.path.exists(exe) or not catalog.available("coder"):
        sys.exit("llama-server (%s) or the brain's file (%s) is missing" % (exe, model))
    total, free = diagnose.ram()
    if free and free < a.min_free_gb:
        sys.exit("only %.1f GB of RAM free: close NewAl (and other big programs) first" % free)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    os.makedirs(a.out, exist_ok=True)
    result = {"time": time.strftime("%Y-%m-%d %H:%M"), "cpu": diagnose.cpu_name(), "cores": os.cpu_count(),
              "ram_gb": round(total, 1), "model": os.path.basename(model), "power": _power(), "rows": []}
    with open(os.path.join(a.out, "bench-brain-%s-llama.log" % stamp), "w", encoding="utf-8", errors="replace") as log:
        for spec in a.configs.split(","):
            try:
                row = run(spec.strip(), exe, model, log)
            except Exception as e:  # noqa: BLE001 - one broken setting does not stop the others
                row = {"config": spec, "error": str(e)}
            result["rows"].append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    path = os.path.join(a.out, "bench-brain-%s.json" % stamp)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(table(result))
    print("saved:", path)


def _power():
    try:
        from newal import speed
        return speed.power()
    except Exception:  # noqa: BLE001
        return {}


def table(result):
    lines = ["%s · %s · %s" % (result["cpu"], result["model"], result.get("power", {}).get("plan", "")),
             "setting        load  read t/s   code t/s (MTP ok)   arabic t/s (MTP ok)"]
    for r in result["rows"]:
        if "error" in r:
            lines.append("%-14s %s" % (r["config"], r["error"]))
            continue
        cell = lambda x: "%5.2f (%s)" % (x["write_tps"], "%d%%" % round(100 * x["accepted"] / x["drafted"])
                                         if x["drafted"] else "-")
        lines.append("%-14s %4.0fs  %8s   %-18s  %s" % (r["config"], r["load_s"], r.get("read_tps", "-"),
                                                     cell(r["code"]), cell(r["arabic"])))
    return "\n".join(lines)


if __name__ == "__main__":
    main()
