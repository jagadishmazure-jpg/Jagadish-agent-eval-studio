"""Guarded vs unguarded: the same agent, the same 30 cases, guards on/off.

Runs the full dataset (core 10 + guard 20, five buckets) through
``GuardedRefundAgent`` in both modes, scores every run with the existing
7 metrics, and reports:

- success rate (all 7 metrics pass) overall and per bucket, per mode;
- per-metric average scores per mode;
- guard catch counts by type, from the guarded mode's structured logs;
- whether every case that pins ``expect_guard="blocked_input"`` was in
  fact blocked in guarded mode.

Deterministic: the same code always yields the same report, so the
numbers rendered into the README (scripts/guard_summary.py) cannot
drift. This is a regression-scale self-written set (30 cases), not a
quality claim about a production agent.
"""

from collections import Counter

from .dataset import BUCKETS, get_all_cases
from .guards import GuardedRefundAgent
from .run_eval import run_case, summarize


def run_comparison(version="v2"):
    """Run both modes over the full dataset; return the raw bundle."""
    cases = get_all_cases()
    bundle = {"cases": cases}
    for mode, enabled in (("unguarded", False), ("guarded", True)):
        agent = GuardedRefundAgent(version=version, guards_enabled=enabled)
        results, logs = [], {}
        for case in cases:
            results.append(run_case(case, agent))
            logs[case.case_id] = list(agent.last_guard_log)
        bundle[mode] = {"results": results, "guard_logs": logs}
    return bundle


def _rate(results):
    if not results:
        return 0.0
    return sum(1 for r in results if r.passed_all) / len(results)


def success_rates(bundle):
    """Overall success rate (all metrics pass) per mode."""
    return {mode: _rate(bundle[mode]["results"])
            for mode in ("unguarded", "guarded")}


def bucket_rates(bundle):
    """Per-bucket success rates: {bucket: {"n", "unguarded", "guarded"}}."""
    out = {}
    for bucket in BUCKETS:
        ids = [c.case_id for c in bundle["cases"] if c.bucket == bucket]
        row = {"n": len(ids)}
        for mode in ("unguarded", "guarded"):
            results = [r for r in bundle[mode]["results"]
                       if r.case_id in ids]
            row[mode] = _rate(results)
        out[bucket] = row
    return out


def catch_counts(bundle):
    """What the guards caught in guarded mode, by category.

    Returns {"input_blocks": Counter, "tool_suppressions": Counter,
    "output_corrections": Counter, "claims_verified": int}.
    """
    counts = {"input_blocks": Counter(), "tool_suppressions": Counter(),
              "output_corrections": Counter(), "claims_verified": 0}
    for log in bundle["guarded"]["guard_logs"].values():
        for entry in log:
            if entry["guard"] == "input" and entry["action"] == "block":
                counts["input_blocks"][entry["category"]] += 1
            elif entry["guard"] == "tool" and entry["action"] == "suppress":
                counts["tool_suppressions"][entry["category"]] += 1
            elif entry["guard"] == "output" and \
                    entry["action"] == "correct":
                counts["output_corrections"][entry["category"]] += 1
            elif entry["guard"] == "output" and entry["action"] == "pass":
                counts["claims_verified"] += 1
    return counts


def guard_expectations(bundle):
    """Cases that pin expect_guard: (case_id, expected, met) triples."""
    out = []
    for case in bundle["cases"]:
        if case.expect_guard == "none":
            continue
        log = bundle["guarded"]["guard_logs"].get(case.case_id, [])
        blocked = any(e["guard"] == "input" and e["action"] == "block"
                      for e in log)
        out.append((case.case_id, case.expect_guard,
                    blocked if case.expect_guard == "blocked_input"
                    else not blocked))
    return out


def _pct(x):
    return "%d%%" % round(x * 100)


def _fmt_counter(counter):
    if not counter:
        return "0"
    parts = ", ".join("%s %d" % (k, v) for k, v in sorted(counter.items()))
    return "%d (%s)" % (sum(counter.values()), parts)


def render_markdown(bundle):
    """The delta report as Markdown tables (used by scripts/guard_summary.py)."""
    rates = success_rates(bundle)
    buckets = bucket_rates(bundle)
    catches = catch_counts(bundle)
    expectations = guard_expectations(bundle)
    met = sum(1 for _, _, ok in expectations if ok)
    lines = []
    lines.append("Cases: %d (core 10 + guard 20), agent v2, success = "
                 "all 7 metrics pass." % len(bundle["cases"]))
    lines.append("")
    lines.append("| Bucket | n | Unguarded | Guarded |")
    lines.append("| --- | --- | --- | --- |")
    for bucket in BUCKETS:
        row = buckets[bucket]
        lines.append("| %s | %d | %s | %s |"
                     % (bucket, row["n"], _pct(row["unguarded"]),
                        _pct(row["guarded"])))
    lines.append("| **overall** | %d | %s | %s |"
                 % (len(bundle["cases"]), _pct(rates["unguarded"]),
                    _pct(rates["guarded"])))
    lines.append("")
    lines.append("Guard catches in guarded mode:")
    lines.append("- input blocks: %s" % _fmt_counter(catches["input_blocks"]))
    lines.append("- repeat tool calls suppressed (served from cache): %s"
                 % _fmt_counter(catches["tool_suppressions"]))
    lines.append("- output claims corrected: %s"
                 % _fmt_counter(catches["output_corrections"]))
    lines.append("- output claims verified against the ledger: %d"
                 % catches["claims_verified"])
    lines.append("- guard expectations met: %d/%d"
                 % (met, len(expectations)))
    lines.append("")
    lines.append("Per-metric averages:")
    lines.append("")
    lines.append("| Metric | Unguarded | Guarded |")
    lines.append("| --- | --- | --- |")
    summaries = {mode: summarize(bundle[mode]["results"])
                 for mode in ("unguarded", "guarded")}
    for name, s in summaries["unguarded"].items():
        lines.append("| %s | %.2f | %.2f |"
                     % (name, s["average"],
                        summaries["guarded"][name]["average"]))
    return "\n".join(lines)


if __name__ == "__main__":
    print(render_markdown(run_comparison()))
