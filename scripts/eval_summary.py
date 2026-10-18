"""Run the full evaluation pipeline (v1 and v2) and summarise it.

    python scripts/eval_summary.py            # print the summary tables
    python scripts/eval_summary.py --write    # also refresh results/eval_summary.json
    python scripts/eval_summary.py --check    # fail if the committed artifact differs

The README's before/after numbers are rendered from this script, and CI runs
``--check``, so no number in the docs can drift from what the pipeline
actually produces.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluation import improve  # noqa: E402

ARTIFACT = ROOT / "results" / "eval_summary.json"


def build():
    result = improve.compare_versions()
    comp = result["comparison"]
    return {
        "cases": len(result["v2_results"]),
        "metrics": {name: {"v1_average": round(c["v1_average"], 4),
                           "v2_average": round(c["v2_average"], 4),
                           "delta": round(c["delta"], 4),
                           "v1_pass_rate": round(c["v1_pass_rate"], 4),
                           "v2_pass_rate": round(c["v2_pass_rate"], 4)}
                    for name, c in comp.items()},
        "improved_metrics": improve.improved_metrics(result),
        "regressed_metrics": improve.regressed_metrics(result),
        "case_regressions": [list(x) for x in improve.case_regressions(result)],
        "v1_patterns": [p["id"] for p in result["v1_patterns"]],
        "v2_patterns": [p["id"] for p in result["v2_patterns"]],
    }


def render(s):
    lines = ["| Metric | v1 avg | v2 avg | Delta | v2 pass rate |", "|---|---|---|---|---|"]
    for name, m in s["metrics"].items():
        lines.append("| %s | %.2f | %.2f | %+.2f | %d%% |" % (
            name, m["v1_average"], m["v2_average"], m["delta"], round(m["v2_pass_rate"] * 100)))
    lines.append("")
    lines.append("cases: %d" % s["cases"])
    lines.append("improved (delta >= 0.05): %s" % ", ".join(s["improved_metrics"]))
    lines.append("metric regressions: %d; per-case regressions: %d"
                 % (len(s["regressed_metrics"]), len(s["case_regressions"])))
    lines.append("v1 failure patterns: %s; v2: %s" % (
        ", ".join(s["v1_patterns"]) or "none", ", ".join(s["v2_patterns"]) or "none"))
    return "\n".join(lines)


def main(argv):
    s = build()
    text = json.dumps(s, indent=2, sort_keys=True) + "\n"
    if "--check" in argv:
        if not ARTIFACT.exists() or ARTIFACT.read_text() != text:
            print("results/eval_summary.json does not match a fresh run; "
                  "run: python scripts/eval_summary.py --write")
            return 1
        print("results/eval_summary.json matches a fresh run")
        return 0
    if "--write" in argv:
        ARTIFACT.write_text(text)
    print(render(s))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
