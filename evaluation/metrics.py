"""The seven agent evaluation metrics, all deterministic and offline.

Each metric takes an agent trace (and the behaviour case it ran against)
and returns a MetricResult: score in [0, 1], pass/fail against a
threshold, and a human-readable reason.

These are *heuristic* scorers, not LLM judges: they measure structure
(tool sets, argument values, phrase presence, call order), which is
exactly what makes them runnable in CI with zero API cost. Where a
production setup would use an LLM-as-judge (nuanced plan quality, open
answer grading), the seam is documented in knowledge/02-seven-metrics.md
and the function signatures accept an optional ``judge`` callable.

Metrics:
 1. task_completion   -- did the final answer reach the expected conclusion?
 2. step_efficiency    -- any tool calls that could not change the outcome?
 3. plan_adherence     -- did execution follow the declared sensible order?
 4. plan_quality       -- was the tool sequence effective for this scenario?
 5. tool_correctness    -- right tools vs expected (F1 over tool sets)?
 6. argument_correctness-- right arguments vs expected (per-tool match rate)?
 7. answer_correctness  -- does the conclusion match (phrase-level, not exact)?
"""

import re
import string
from dataclasses import dataclass


@dataclass
class MetricResult:
    metric: str
    score: float
    passed: bool
    reason: str


def _normalise(text):
    """Lowercase, strip punctuation, collapse whitespace."""
    text = (text or "").lower()
    text = text.translate(str.maketrans("", "", string.punctuation))
    return re.sub(r"\s+", " ", text).strip()


# The sensible tool order for a refund turn. Steps may be skipped when the
# scenario makes them pointless (e.g. no eligibility check needed when the
# user is unknown), but they must never run out of order.
_SENSIBLE_ORDER = ("get_order_details", "check_refund_eligibility",
                   "calculate_refund", "process_return_request")


def _check_threshold(score, threshold):
    return bool(score >= threshold)


def task_completion(response, case, threshold=0.8, judge=None):
    """Did the agent reach the expected conclusion?

    Deterministic check: every expected phrase must appear (normalised) in
    the final answer. ``judge`` is an optional LLM-seam callable
    ``judge(answer, expected) -> (score, reason)``; when given it is used
    instead of the phrase check.
    """
    if judge is not None:
        score, reason = judge(response.answer, case.expected_conclusion)
        return MetricResult("task_completion", float(score),
                            _check_threshold(score, threshold), reason)
    answer = _normalise(response.answer)
    hits = [p for p in case.expected_conclusion if _normalise(p) in answer]
    score = len(hits) / len(case.expected_conclusion) \
        if case.expected_conclusion else 1.0
    missing = [p for p in case.expected_conclusion if _normalise(p) not in answer]
    reason = ("all %d expected phrases present" % len(hits)) if not missing \
        else "missing expected phrases: %s" % ", ".join(missing)
    return MetricResult("task_completion", score,
                        _check_threshold(score, threshold), reason)


def step_efficiency(response, case, threshold=0.8):
    """Were all tool calls necessary?

    A call is unnecessary when it cannot change the outcome: e.g. fetching
    today's date after eligibility already ruled every item out, or calling
    any tool when no username was ever identified. Score is the fraction
    of necessary calls; a trace with no calls scores 1.0 when none were
    expected, else 0.0.
    """
    actual = response.tool_names()
    if not actual:
        if not case.expected_tools:
            return MetricResult("step_efficiency", 1.0, True,
                                "no tools called and none expected")
        return MetricResult("step_efficiency", 0.0,
                            _check_threshold(0.0, threshold),
                            "no tools called but %d expected"
                            % len(case.expected_tools))
    expected_set = set(case.expected_tools)
    necessary = [t for t in actual if t in expected_set]
    unnecessary = [t for t in actual if t not in expected_set]
    score = len(necessary) / len(actual)
    reason = ("%d/%d calls necessary" % (len(necessary), len(actual))) \
        if not unnecessary else \
        "unnecessary calls: %s" % ", ".join(sorted(set(unnecessary)))
    return MetricResult("step_efficiency", score,
                        _check_threshold(score, threshold), reason)


def plan_adherence(response, case=None, threshold=0.8):
    """Did the executed tools follow the sensible order?

    Compares the actual call sequence against _SENSIBLE_ORDER, allowing
    skips. Out-of-order calls (e.g. calculating before checking
    eligibility) fail. A single-tool or empty trace trivially adheres.
    """
    actual = response.tool_names()
    order_index = {name: i for i, name in enumerate(_SENSIBLE_ORDER)}
    # Unknown tools are ignored for ordering (flagged by step_efficiency).
    seq = [order_index[t] for t in actual if t in order_index]
    if len(seq) <= 1:
        return MetricResult("plan_adherence", 1.0, True,
                            "0-1 ordered tools; nothing to violate")
    violations = sum(1 for a, b in zip(seq, seq[1:]) if b < a)
    pairs = len(seq) - 1
    score = 1.0 - violations / pairs
    reason = "tool order respected" if violations == 0 else \
        "%d order violation(s) in %d transitions" % (violations, pairs)
    return MetricResult("plan_adherence", score,
                        _check_threshold(score, threshold), reason)


def plan_quality(response, case, threshold=0.8, judge=None):
    """Was the tool sequence effective for this scenario?

    Deterministic heuristics for the classic failure modes:
    - issuing a ticket when nothing was eligible;
    - calculating a refund without checking eligibility first;
    - stopping before any tool call while the case expects tools.
    ``judge`` is the LLM-seam for nuanced plan grading.
    """
    if judge is not None:
        score, reason = judge(response.trace, case.description)
        return MetricResult("plan_quality", float(score),
                            _check_threshold(score, threshold), reason)
    actual = response.tool_names()
    problems = []
    if "process_return_request" in actual and \
            "calculate_refund" not in actual:
        problems.append("ticket issued without calculating the amount")
    if "calculate_refund" in actual and \
            "check_refund_eligibility" not in actual:
        problems.append("amount calculated without an eligibility check")
    if case.expected_tools and not actual:
        problems.append("no tools called although the case needs %d"
                        % len(case.expected_tools))
    if "process_return_request" in actual and not any(
            k in _normalise(response.answer)
            for k in ("refund ticket", "ticket")):
        problems.append("ticket step ran but the answer never mentions it")
    score = 1.0 if not problems else max(0.0, 1.0 - 0.5 * len(problems))
    reason = "plan effective for the scenario" if not problems else \
        "; ".join(problems)
    return MetricResult("plan_quality", score,
                        _check_threshold(score, threshold), reason)


def tool_correctness(response, case, threshold=0.8):
    """Right tools? F1 over the expected vs actual tool sets."""
    expected = set(case.expected_tools)
    actual = set(response.tool_names())
    if not expected and not actual:
        return MetricResult("tool_correctness", 1.0, True,
                            "no tools expected and none called")
    if not expected or not actual:
        return MetricResult("tool_correctness", 0.0,
                            _check_threshold(0.0, threshold),
                            "expected %s, got %s"
                            % (sorted(expected), sorted(actual)))
    tp = len(expected & actual)
    precision = tp / len(actual)
    recall = tp / len(expected)
    f1 = 2 * precision * recall / (precision + recall) \
        if (precision + recall) else 0.0
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    bits = []
    if missing:
        bits.append("missing: %s" % ", ".join(missing))
    if extra:
        bits.append("extra: %s" % ", ".join(extra))
    reason = "; ".join(bits) if bits else "tool set matches exactly"
    return MetricResult("tool_correctness", f1,
                        _check_threshold(f1, threshold), reason)


def argument_correctness(response, case, threshold=0.8):
    """Right arguments? Per-tool match rate over expected arguments.

    Only tools listed in case.expected_arguments are checked; a tool with
    no expectations is skipped (it cannot be wrong here).
    """
    checks, matched, notes = 0, 0, []
    by_tool = {}
    for step in response.trace:
        by_tool.setdefault(step["tool"], step["arguments"])
    for tool, expected_args in case.expected_arguments.items():
        if tool not in by_tool:
            notes.append("%s: not called" % tool)
            continue
        actual_args = by_tool[tool] or {}
        for arg, want in expected_args.items():
            checks += 1
            got = actual_args.get(arg, "<missing>")
            if got == want:
                matched += 1
            else:
                notes.append("%s.%s: expected %r, got %r"
                             % (tool, arg, want, got))
    if checks == 0:
        if notes:
            return MetricResult("argument_correctness", 0.0,
                                _check_threshold(0.0, threshold),
                                "; ".join(notes))
        return MetricResult("argument_correctness", 1.0, True,
                            "no argument expectations to check")
    score = matched / checks
    reason = "%d/%d arguments match" % (matched, checks)
    if notes:
        reason += "; " + "; ".join(notes)
    return MetricResult("argument_correctness", score,
                        _check_threshold(score, threshold), reason)


def answer_correctness(response, case, threshold=0.8):
    """Does the final answer reach the same logical conclusion?

    Phrase-level, not string equality: the agent may word things its own
    way as long as every expected conclusion phrase is present. This is
    the deterministic stand-in for a G-Eval style LLM judge.
    """
    answer = _normalise(response.answer)
    hits = [p for p in case.expected_conclusion if _normalise(p) in answer]
    total = len(case.expected_conclusion)
    score = len(hits) / total if total else 1.0
    missing = [_normalise(p) for p in case.expected_conclusion
               if _normalise(p) not in answer]
    reason = "conclusion matches (%d/%d phrases)" % (len(hits), total) \
        if not missing else \
        "conclusion differs; missing: %s" % ", ".join(missing)
    return MetricResult("answer_correctness", score,
                        _check_threshold(score, threshold), reason)


# Registry used by run_eval.py: name -> function(response, case, threshold).
ALL_METRICS = {
    "task_completion": task_completion,
    "step_efficiency": step_efficiency,
    "plan_adherence": plan_adherence,
    "plan_quality": plan_quality,
    "tool_correctness": tool_correctness,
    "argument_correctness": argument_correctness,
    "answer_correctness": answer_correctness,
}
