"""Runs the coding speed test through NewAl Code on a given model, the same way run_old.py runs the current NewAl:
the model is loaded and has read the fixed start of every request in advance, then each task is a new session.

    python bench/run_new.py --model <file.gguf | model id> [--llama-server <exe>] [--out results.json]

Timed: from sending the task to the final answer, including the automatic test run after a change."""

import argparse
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--llama-server", default="")
    ap.add_argument("--home", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--tasks", nargs="*", default=[])
    ap.add_argument("--mode", default="auto-edit")
    ap.add_argument("--limit", type=int, default=1200)
    ap.add_argument("--speculative", default="")
    ap.add_argument("--reasoning", default="")
    ap.add_argument("--threads", type=int, default=0)
    a = ap.parse_args()

    os.environ["NEWAL_CODE_HOME"] = a.home or tempfile.mkdtemp(prefix="newal-code-home-")
    # A clean user folder: no skills, agents or MCP servers of whoever runs the test (the old NewAl reads none either).
    os.environ["HOME"] = os.environ["USERPROFILE"] = tempfile.mkdtemp(prefix="newal-code-user-")
    if a.llama_server:
        os.environ["NEWAL_LLAMA_SERVER"] = os.path.abspath(a.llama_server)
    from newal_code import benchmark, runtime, settings  # noqa: E402
    settings.ensure_dirs()
    extra = {}
    if a.speculative:
        extra["speculative"] = a.speculative
    if a.reasoning:
        extra["reasoning"] = a.reasoning
    if a.threads:
        extra["threads"] = a.threads
    if extra:
        settings.save(extra)
    model = os.path.abspath(a.model) if a.model.lower().endswith(".gguf") else a.model
    try:
        summary = benchmark.run(model, a.tasks, mode=a.mode, limit=a.limit, log=lambda s: print(s, flush=True))
        srv = runtime.pool.running()
        if srv:
            summary["server_args"] = " ".join(srv[0].args("llama-server")[1:])
            summary["server_rss_gb"] = round(srv[0].rss() / 2 ** 30, 2)
            summary["server_peak_rss_gb"] = round(srv[0].rss(peak=True) / 2 ** 30, 2)
            summary["plan"] = {k: v for k, v in srv[0].plan.items() if k != "info"}
            import resource
            summary["agent_peak_rss_gb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2 ** 20, 3)
    finally:
        runtime.pool.stop_all()
    print("TOTAL %.1f s, %d/%d passed" % (summary["seconds"], summary["passed"], summary["tasks"]), flush=True)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()
