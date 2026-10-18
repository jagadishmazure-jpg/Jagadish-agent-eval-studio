"""The improvement loop: measure v1, fix, measure v2, compare.

This is the "data-driven decisions" half of the project. The v1 agent has
two documented flaws (see refund_agent/agent.py); v2 fixes them. This
module re-runs the full evaluation for both versions and produces a
before/after comparison proving the fixes moved the targeted metrics --
without regressing the others.
"""

from . import analyze
from .run_eval import run_all, summarize


def compare_versions(thresholds=None):
    """Run v1 and v2; return per-metric before/after comparison dicts."""
    v1_results = run_all(version="v1", thresholds=thresholds)
    v2_results = run_all(version="v2", thresholds=thresholds)
    v1_summary = summarize(v1_results)
    v2_summary = summarize(v2_results)
    comparison = {}
    for name in v1_summary:
        before = v1_summary[name]["average"]
        after = v2_summary[name]["average"]
        comparison[name] = {
            "v1_average": before,
            "v2_average": after,
            "delta": after - before,
            "v1_pass_rate": v1_summary[name]["pass_rate"],
            "v2_pass_rate": v2_summary[name]["pass_rate"],
        }
    return {
        "comparison": comparison,
        "v1_results": v1_results,
        "v2_results": v2_results,
        "v1_patterns": analyze.detect_patterns(v1_results),
        "v2_patterns": analyze.detect_patterns(v2_results),
    }


def improved_metrics(comparison_result, min_delta=0.05):
    """Metric names where v2 beats v1 by at least min_delta."""
    return [name for name, c in comparison_result["comparison"].items()
            if c["delta"] >= min_delta]


def regressed_metrics(comparison_result, min_delta=0.05):
    """Metric names where v2 is worse than v1 by at least min_delta."""
    return [name for name, c in comparison_result["comparison"].items()
            if c["delta"] <= -min_delta]
