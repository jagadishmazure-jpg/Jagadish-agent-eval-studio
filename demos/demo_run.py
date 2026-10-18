#!/usr/bin/env python3
"""End-to-end demo: run the v1 vs v2 evaluation and print the dashboard.

Usage: python3 demos/demo_run.py [--html out.html]
Everything is offline and deterministic.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from evaluation import improve
from evaluation.report import build_html, build_markdown

cmp = improve.compare_versions()
results = cmp["v2_results"]

print(build_markdown(results, title="Refund agent evaluation (v2)",
                     comparison=cmp))

if "--html" in sys.argv:
    idx = sys.argv.index("--html")
    out = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "report.html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(build_html(results, title="Refund agent evaluation (v2)",
                           comparison=cmp))
    print("\nHTML dashboard written to %s" % out)
