"""Failure analysis: from metric scores to actionable patterns.

Takes scored case results and answers: which metrics fail most, which
cases fail most, and what recurring patterns explain the failures. The
patterns are heuristic rules over traces (e.g. "get_today called although
no item was ever eligible"), each documented with the evidence behind it.
"""

from collections import Counter


def failures_by_metric(results):
    """metric name -> list of (case_id, reason) for failing results."""
    out = {}
    for r in results:
        for name, m in r.metric_results.items():
            if not m.passed:
                out.setdefault(name, []).append((r.case_id, m.reason))
    return out


def failures_by_case(results):
    """case_id -> list of failed metric names."""
    return {r.case_id: [name for name, m in r.metric_results.items()
                       if not m.passed]
            for r in results if not r.passed_all}


def detect_patterns(results):
    """Heuristic pattern detection over failing traces.

    Returns a list of dicts: pattern id, description, evidence (case ids),
    and a suggested fix. These are the "data-driven decisions" the
    improvement loop acts on.
    """
    patterns = []

    unnecessary_today = [
        r.case_id for r in results
        if any(s["tool"] == "get_today" for s in r.trace)
        and not r.metric_results["step_efficiency"].passed]
    if unnecessary_today:
        patterns.append({
            "id": "unnecessary-date-lookup",
            "description": "Agent calls get_today even when the date cannot "
                           "change the outcome (e.g. item never purchased).",
            "evidence": unnecessary_today,
            "suggested_fix": "Drop the get_today call: eligibility already "
                             "encodes the refund window.",
        })

    wrong_unknown_user = [
        r.case_id for r in results
        if r.case_id == "invalid-user"
        and not r.metric_results["answer_correctness"].passed]
    if wrong_unknown_user:
        patterns.append({
            "id": "unknown-user-messaging",
            "description": "For an unknown username the agent talks about "
                           "missing purchase history instead of asking for a "
                           "valid username.",
            "evidence": wrong_unknown_user,
            "suggested_fix": "Name the real problem: the username is not "
                             "valid; ask the customer to check it.",
        })

    ticket_without_calc = [
        r.case_id for r in results
        if not r.metric_results["plan_quality"].passed
        and "ticket issued without calculating" in
            r.metric_results["plan_quality"].reason]
    if ticket_without_calc:
        patterns.append({
            "id": "ticket-without-calculation",
            "description": "A refund ticket step ran without a prior amount "
                           "calculation.",
            "evidence": ticket_without_calc,
            "suggested_fix": "Enforce calculate_refund before "
                             "process_return_request in the agent logic.",
        })

    return patterns


def top_failing_metrics(results, limit=3):
    """(metric, failure_count) sorted worst-first."""
    counts = Counter()
    for r in results:
        for name, m in r.metric_results.items():
            if not m.passed:
                counts[name] += 1
    return counts.most_common(limit)
