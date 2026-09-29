"""A Markdown table from speed-test results: python bench/compare.py old.json new.json [new2.json ...]

Per task: seconds and pass/fail for each run; totals; and how much faster the new runs are than the first file
(speed ratio = old time / new time, and time saved)."""

import json
import sys


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(paths):
    runs = [load(p) for p in paths]
    ids = [r["id"] for r in runs[0]["results"]]
    head = ["Task"] + ["%s%s" % (r.get("program", "?"), (" · " + r["model"]) if r.get("model") else "")
                       for r in runs]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for tid in ids:
        row = [tid]
        for r in runs:
            rec = next((x for x in r["results"] if x["id"] == tid), None)
            row.append("—" if not rec else "%.0f s %s" % (rec["seconds"], "✓" if rec["ok"] else "✗"))
        lines.append("| " + " | ".join(row) + " |")
    row = ["**Total**"]
    for r in runs:
        row.append("**%.0f s, %d/%d passed**" % (r["seconds"], r["passed"], r["tasks"]))
    lines.append("| " + " | ".join(row) + " |")
    base = runs[0]["seconds"]
    out = ["\n".join(lines), ""]
    for r in runs[1:]:
        out.append("- %s: %.1fx the speed of %s (%.0f%% less time); generated %d tokens vs %d." % (
            r.get("program"), base / r["seconds"], runs[0].get("program"), 100 * (1 - r["seconds"] / base),
            sum(x["generated_tokens"] for x in r["results"]), sum(x["generated_tokens"] for x in runs[0]["results"])))
    print("\n".join(out))


if __name__ == "__main__":
    main(sys.argv[1:])
