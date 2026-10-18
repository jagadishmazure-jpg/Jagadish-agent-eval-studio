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

import inspect
import re
import string
from collections import Counter
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


_NEGATORS = {"no", "not", "never", "without", "cannot", "cant", "isnt",
             "wasnt", "wont", "didnt", "doesnt", "nor"}


def _phrase_hit(phrase, answer):
    """True when ``phrase`` occurs in ``answer`` as whole words (a trailing
    plural "s" is allowed) and is not negated by one of the two words just
    before it. Both strings must already be normalised.

    Substring matching was gameable: "45.00" matched "$145.00", "valid
    username" matched "invalid username", and "no refund ticket" matched
    "refund ticket".
    """
    if not phrase:
        return False
    pattern = r"(?<!\w)" + re.escape(phrase) + r"s?(?!\w)"
    phrase_is_negative = phrase.split()[0] in _NEGATORS
    for m in re.finditer(pattern, answer):
        before = answer[:m.start()].split()[-2:]
        if phrase_is_negative or not any(w in _NEGATORS for w in before):
            return True
    return False


def _conclusion_score(response, case):
    """Shared phrase scoring for task_completion and answer_correctness.

    Returns (score, reason). An empty answer scores 0. Any forbidden phrase
    (the opposite conclusion, e.g. "no refund will be issued" on a refund
    case) scores 0, so an answer cannot pass by stuffing every possible
    conclusion into one reply.
    """
    answer = _normalise(response.answer)
    if not answer:
        return 0.0, "empty answer"
    forbidden = [p for p in getattr(case, "forbidden_conclusion", ())
                 if _phrase_hit(_normalise(p), answer)]
    if forbidden:
        return 0.0, "contradicting conclusion present: %s" % ", ".join(forbidden)
    expected = case.expected_conclusion
    if not expected:
        return 1.0, "no expected phrases; answer is non-empty"
    missing = [p for p in expected if not _phrase_hit(_normalise(p), answer)]
    score = (len(expected) - len(missing)) / len(expected)
    reason = ("all %d expected phrases present" % len(expected)) if not missing \
        else "missing expected phrases: %s" % ", ".join(missing)
    return score, reason


# The sensible tool order for a refund turn. Steps may be skipped when the
# scenario makes them pointless (e.g. no eligibility check needed when the
# user is unknown), but they must never run out of order.
_SENSIBLE_ORDER = ("get_order_details", "check_refund_eligibility",
                   "calculate_refund", "process_return_request")


def _check_threshold(score, threshold):
    return bool(score >= threshold)


def task_completion(response, case, threshold=0.8, judge=None):
    """Did the agent reach the expected conclusion?

    Deterministic check: every expected phrase must appear (normalised,
    whole words, not negated) in the final answer, no contradicting phrase
    may appear, and ``conversation_continue`` must match the case. ``judge`` is an optional LLM-seam callable
    ``judge(answer, expected) -> (score, reason)``; when given it is used
    instead of the phrase check.
    """
    if judge is not None:
        score, reason = judge(response.answer, case.expected_conclusion)
        return MetricResult("task_completion", float(score),
                            _check_threshold(score, threshold), reason)
    score, reason = _conclusion_score(response, case)
    # Completing the task also means leaving the conversation in the right
    # state: ending it when the customer still has to reply (or the reverse)
    # is a failed turn even when the wording is right.
    want = getattr(case, "expect_continue", None)
    if want is not None and bool(response.conversation_continue) != bool(want):
        score = 0.0
        reason = "conversation_continue=%s but the case expects %s" % (
            response.conversation_continue, want)
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
    # Each expected tool is needed as many times as it is listed; repeats
    # beyond that cannot change the outcome (calling the same lookup six
    # times used to score 1.0).
    budget = Counter(case.expected_tools)
    necessary, unnecessary = [], []
    for t in actual:
        if budget[t] > 0:
            budget[t] -= 1
            necessary.append(t)
        else:
            unnecessary.append(t)
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
    if not actual and case is not None and case.expected_tools:
        # Doing nothing cannot "adhere" to a plan that needed tools.
        return MetricResult("plan_adherence", 0.0,
                            _check_threshold(0.0, threshold),
                            "no tools executed although the case needs %d"
                            % len(case.expected_tools))
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
    if "process_return_request" in actual and \
            "process_return_request" not in case.expected_tools:
        # The docstring promised this check; it was never implemented, so a
        # ticket for a never-purchased item scored 1.0.
        problems.append("ticket issued although nothing in this case is "
                        "eligible for a refund")
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
    expected = Counter(case.expected_tools)
    actual = Counter(response.tool_names())
    if not expected and not actual:
        return MetricResult("tool_correctness", 1.0, True,
                            "no tools expected and none called")
    if not expected or not actual:
        return MetricResult("tool_correctness", 0.0,
                            _check_threshold(0.0, threshold),
                            "expected %s, got %s"
                            % (sorted(expected), sorted(actual)))
    # Multiset F1: repeated calls count against precision.
    tp = sum((expected & actual).values())
    precision = tp / sum(actual.values())
    recall = tp / sum(expected.values())
    f1 = 2 * precision * recall / (precision + recall) \
        if (precision + recall) else 0.0
    missing = sorted((expected - actual).elements())
    extra = sorted((actual - expected).elements())
    bits = []
    if missing:
        bits.append("missing: %s" % ", ".join(missing))
    if extra:
        bits.append("extra: %s" % ", ".join(extra))
    reason = "; ".join(bits) if bits else "tool set matches exactly"
    return MetricResult("tool_correctness", f1,
                        _check_threshold(f1, threshold), reason)


_MISSING = object()


def _same_value(got, want):
    """Type-aware equality: True is not 1, 0 is not False, "45" is not 45.
    Ints and floats compare numerically; lists and tuples compare
    element by element."""
    if isinstance(want, bool) or isinstance(got, bool):
        return type(got) is type(want) and got == want
    if isinstance(want, (int, float)) and isinstance(got, (int, float)):
        return float(got) == float(want)
    if isinstance(want, (list, tuple)) and isinstance(got, (list, tuple)):
        return len(got) == len(want) and all(
            _same_value(g, w) for g, w in zip(got, want))
    if isinstance(want, dict) and isinstance(got, dict):
        return got.keys() == want.keys() and all(
            _same_value(got[k], want[k]) for k in want)
    return type(got) is type(want) and got == want


_INTERNAL_PARAMS = frozenset({"queue"})


def _tool_parameters(tool):
    """Parameter names of a real refund tool, or None for unknown tools."""
    from refund_agent import tools as _tools
    fn = getattr(_tools, tool, None)
    if not callable(fn):
        return None
    # ``queue`` is a test/wiring hook, never something the agent may choose:
    # an agent that routes its ticket to a queue it controls is a bypass.
    return set(inspect.signature(fn).parameters) - _INTERNAL_PARAMS


def argument_correctness(response, case, threshold=0.8):
    """Right arguments? Match rate over expected arguments.

    Every call of an expected tool is checked (not only the first), values
    are compared type-aware, and an argument the real tool does not accept
    (for example ``override_policy=True``) counts as a failed check.
    Tools with no expectations are skipped. The metric passes only when every
    check matches; the score is the match rate.
    """
    checks, matched, notes = 0, 0, []
    calls = {}
    for step in response.trace:
        calls.setdefault(step["tool"], []).append(step["arguments"] or {})
    for tool, expected_args in case.expected_arguments.items():
        if tool not in calls:
            notes.append("%s: not called" % tool)
            continue
        allowed = _tool_parameters(tool)
        for n, actual_args in enumerate(calls[tool], 1):
            label = tool if len(calls[tool]) == 1 else "%s#%d" % (tool, n)
            for arg, want in expected_args.items():
                checks += 1
                got = actual_args.get(arg, _MISSING)
                if got is not _MISSING and _same_value(got, want):
                    matched += 1
                else:
                    notes.append("%s.%s: expected %r, got %s"
                                 % (label, arg, want, "<missing>"
                                    if got is _MISSING else repr(got)))
            if allowed is not None:
                for arg in sorted(set(actual_args) - allowed):
                    checks += 1
                    notes.append("%s.%s: unexpected argument" % (label, arg))
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
    # Strict pass: one wrong, missing or smuggled argument fails the metric
    # (a string amount or an extra flag on the ticket tool is not "80% right").
    # The score stays partial so the reason shows how far off the call was.
    passed = matched == checks and _check_threshold(score, threshold)
    return MetricResult("argument_correctness", score, passed, reason)


def answer_correctness(response, case, threshold=0.8):
    """Does the final answer reach the same logical conclusion?

    Phrase-level, not string equality: the agent may word things its own
    way as long as every expected conclusion phrase is present. This is
    the deterministic stand-in for a G-Eval style LLM judge.
    """
    score, reason = _conclusion_score(response, case)
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
