"""The committed v1/v2 numbers must be what the pipeline produces now."""
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_committed_artifact_matches_a_fresh_run():
    r = subprocess.run([sys.executable, "scripts/eval_summary.py", "--check"], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_headline_numbers_come_from_the_artifact():
    s = json.loads((ROOT / "results" / "eval_summary.json").read_text())
    m = s["metrics"]
    # The README rounds to 2 decimals (step efficiency v1 is 0.8533 exactly).
    r2 = lambda k: (round(m[k]["v1_average"], 2), round(m[k]["v2_average"], 2))  # noqa: E731
    assert r2("step_efficiency") == (0.85, 1.0)
    assert r2("answer_correctness") == (0.9, 1.0)
    assert s["regressed_metrics"] == [] and s["case_regressions"] == []
