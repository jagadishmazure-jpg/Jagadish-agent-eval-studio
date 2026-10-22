"""Print the guarded-vs-unguarded comparison (README renders this).

    python scripts/guard_summary.py

Runs the same agent over the full 30-case dataset with the guard layer
off and on and prints the delta report from evaluation/compare.py. The
README block is regenerated from this script and CI's docs check fails
if it drifts, same as the v1/v2 summary.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluation import compare  # noqa: E402


def main():
    print(compare.render_markdown(compare.run_comparison()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
