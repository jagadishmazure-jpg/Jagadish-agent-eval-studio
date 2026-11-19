"""Print one continuous-improvement pass (baseline held; apply is off).

    python scripts/continuous_summary.py

The README renders this output, and CI checks it still matches a fresh run.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluation.continuous import render_summary, run_loop  # noqa: E402


def main():
    print(render_summary(run_loop()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
