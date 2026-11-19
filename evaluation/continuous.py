"""Continuous improvement: baseline, cluster, optimize, validate, repeat.

Offline stand-in for a four-step improvement loop:

1. Establish a baseline (what "good" looks like) from the existing harness.
2. Identify recurring problems in synthetic traces and feedback.
3. Optimize by proposing prompt, tool, and config changes.
4. Validate each candidate against the baseline and promote it only when
   it clears a regression gate. The promoted candidate becomes the next
   baseline.

Partial prompt and tool candidates keep the baseline case results and
copy scores for the metrics they target from a real ``run_all("v2")``.
The combined candidate is that v2 run. Config candidates adjust the
synthetic latency and token totals; one of them also applies a fixed
quality penalty so a cheaper change that gets worse is rejected.

Product-specific behavior of the hosted loop this is modeled on is
documented in ``docs/continuous-improvement.md`` and marked [verify]
there. This module does not call a model or a hosted service.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace

from . import analyze, improve
from .metrics import MetricResult
from .run_eval import run_all, summarize

DATE_LOOKUP = "unnecessary-date-lookup"
UNKNOWN_USER = "unknown-user-messaging"
VAGUE_ITEM = "vague-item-ask"

PROMPT_UNKNOWN_USER = "prompt-unknown-user"
TOOL_DROP_DATE = "tool-drop-date-lookup"
COMBINED_QUALITY = "combined-quality"
CONFIG_SMALL_MODEL = "config-small-model"
CONFIG_TOKEN_CAP = "config-token-cap"

# Synthetic traffic adjustments. These are mock costs on the fixture,
# not measurements from a model host.
DATE_TOOL = "get_today"
DATE_LATENCY_MS = 40
DATE_TOKENS = 30
PROMPT_EXTRA_TOKENS = 8
SMALL_MODEL = "mock-small"
SMALL_MODEL_LATENCY_FACTOR = 0.6
SMALL_MODEL_TOKEN_FACTOR = 0.45
SMALL_MODEL_PENALTY = 0.15
SMALL_MODEL_METRICS = ("answer_correctness", "task_completion")
TOKEN_CAP_FACTOR = 0.9
DEFAULT_MODEL = "mock-large"

_CLUSTERS = {
    DATE_LOOKUP: {
        "cause": (
            "Traces call get_today after eligibility already decided the "
            "request, so the date lookup cannot change the outcome."
        ),
        "kind": "tool",
        "fix": (
            "Remove the get_today call. The eligibility check already "
            "encodes the refund window."
        ),
        "metrics": ("step_efficiency", "tool_correctness"),
    },
    UNKNOWN_USER: {
        "cause": (
            "For an unknown username the answer talks about missing "
            "purchase history instead of asking for a valid username."
        ),
        "kind": "prompt",
        "fix": (
            "If the user record does not exist, say the username is not "
            "valid and ask for a correction. Do not say purchase history "
            "is missing."
        ),
        "metrics": ("task_completion", "answer_correctness"),
    },
    VAGUE_ITEM: {
        "cause": (
            "The customer named no item and the answer did not ask which "
            "item to refund."
        ),
        "kind": "prompt",
        "fix": (
            "When no item is named, ask which item to refund before any "
            "ticket step."
        ),
        "metrics": (),
    },
}


def _cluster_meta(cluster_id):
    if cluster_id in _CLUSTERS:
        return _CLUSTERS[cluster_id]
    return {
        "cause": "Recurring feedback label %s." % cluster_id,
        "kind": "prompt",
        "fix": (
            "Review traces labeled %s and update the agent instructions."
            % cluster_id
        ),
        "metrics": (),
    }


@dataclass(frozen=True)
class SyntheticTrace:
    """One mocked production turn. Steps use the agent trace shape."""

    trace_id: str
    agent_version: str
    user_input: str
    answer: str
    steps: tuple
    latency_ms: int
    tokens: int
    model: str = DEFAULT_MODEL


@dataclass(frozen=True)
class Feedback:
    """A user or expert note attached to one trace."""

    feedback_id: str
    trace_id: str
    source: str
    rating: int
    note: str
    labels: tuple = ()


@dataclass(frozen=True)
class Corpus:
    traces: tuple
    feedback: tuple

    def for_version(self, version):
        traces = tuple(t for t in self.traces if t.agent_version == version)
        ids = {t.trace_id for t in traces}
        feedback = tuple(f for f in self.feedback if f.trace_id in ids)
        return Corpus(traces, feedback)


def scope_corpus(corpus, version):
    """Traces for ``version``, plus feedback whose trace id is unknown.

    Feedback for another version stays out. A note that points at a
    missing trace is kept so the report can list it as an orphan.
    """
    scoped = corpus.for_version(version)
    known = {t.trace_id for t in corpus.traces}
    orphans = tuple(f for f in corpus.feedback if f.trace_id not in known)
    return Corpus(scoped.traces, scoped.feedback + orphans)


def _step(tool, arguments, reasoning, result):
    return {
        "tool": tool,
        "arguments": arguments,
        "reasoning": reasoning,
        "result": result,
    }


def synthetic_corpus():
    """Fixed traces and feedback. Fictional customers, no live traffic."""
    order = _step(
        "get_order_details", {"username": "u-1003"},
        "Need the purchase history before a refund decision.", "ok")
    elig_never = _step(
        "check_refund_eligibility",
        {"username": "u-1003", "items": ["running sneakers"]},
        "Policy check for the requested item.", "running sneakers=not_purchased")
    today = _step(
        "get_today", {}, "Confirm today's date for the refund window.",
        "2026-10-18")
    elig_expired = _step(
        "check_refund_eligibility",
        {"username": "u-1002", "items": ["camp lantern"]},
        "Policy check for the requested item.", "camp lantern=outside_window")
    traces = (
        SyntheticTrace(
            "t-date-1", "v1",
            "I am u-1003, I would like a refund for the running sneakers.",
            "No refund will be issued. running sneakers: was never purchased.",
            (order, elig_never, today), 200, 500),
        SyntheticTrace(
            "t-date-2", "v1",
            "I am u-1002, refund the camp lantern.",
            "No refund will be issued. camp lantern: outside the 30-day refund window.",
            (
                _step("get_order_details", {"username": "u-1002"},
                      "Need the purchase history before a refund decision.", "ok"),
                elig_expired, today,
            ), 200, 500),
        SyntheticTrace(
            "t-date-3", "v1",
            "I am u-1002, refund the camp lantern please.",
            "No refund will be issued. camp lantern: outside the 30-day refund window.",
            (
                _step("get_order_details", {"username": "u-1002"},
                      "Need the purchase history before a refund decision.", "ok"),
                elig_expired, today,
            ), 200, 500),
        SyntheticTrace(
            "t-date-4", "v1",
            "I am u-1003, refund the running sneakers.",
            "No refund will be issued. running sneakers: was never purchased.",
            (order, elig_never, today), 200, 500),
        SyntheticTrace(
            "t-user-1", "v1",
            "I am u-9999, I would like a refund for the trail backpack.",
            "No purchase history found for u-9999, so no refund will be issued.",
            (_step("get_order_details", {"username": "u-9999"},
                   "Look up the customer before deciding.", "user_not_found"),),
            140, 300),
        SyntheticTrace(
            "t-user-2", "v1",
            "I am u-4242 and I want a refund.",
            "No purchase history found for u-4242, so no refund will be issued.",
            (_step("get_order_details", {"username": "u-4242"},
                   "Look up the customer before deciding.", "user_not_found"),),
            140, 300),
        SyntheticTrace(
            "t-user-3", "v1",
            "I am u-0007, refund the bottle.",
            "No purchase history found for u-0007, so no refund will be issued.",
            (_step("get_order_details", {"username": "u-0007"},
                   "Look up the customer before deciding.", "user_not_found"),),
            140, 300),
        SyntheticTrace(
            "t-vague-1", "v1",
            "I am u-1001 and I want a refund.",
            "I cannot help with that.",
            (_step("get_order_details", {"username": "u-1001"},
                   "Look up the customer.", "ok"),),
            120, 250),
        SyntheticTrace(
            "t-good-1", "v1",
            "I am u-1001, I would like a refund for the trail backpack.",
            "A refund ticket tkt-demo for $45.00 has been issued and will be "
            "reviewed by our team before payout.",
            (
                _step("get_order_details", {"username": "u-1001"},
                      "Need the purchase history.", "ok"),
                _step("check_refund_eligibility",
                      {"username": "u-1001", "items": ["trail backpack"]},
                      "Policy check.", "trail backpack=eligible"),
                _step("calculate_refund",
                      {"username": "u-1001", "items": ["trail backpack"]},
                      "Total the eligible items.", "total=45.00"),
                _step("process_return_request",
                      {"username": "u-1001", "items": ["trail backpack"],
                       "amount": 45.0},
                      "File a ticket for human review.", "tkt-demo"),
            ), 160, 400),
        SyntheticTrace(
            "t-good-2", "v1",
            "I am u-1001, what is my order status?",
            "I found 1 order(s) on your account. Let me know if you would "
            "like a refund for any item.",
            (_step("get_order_details", {"username": "u-1001"},
                   "Answer from history; issue nothing.", "ok"),),
            160, 400),
        SyntheticTrace(
            "t-unhappy-1", "v1",
            "I am u-1001, I would like a refund for the insulated bottle.",
            "A refund ticket tkt-demo-2 for $28.00 has been issued and will "
            "be reviewed by our team before payout.",
            (
                _step("get_order_details", {"username": "u-1001"},
                      "Need the purchase history.", "ok"),
                _step("check_refund_eligibility",
                      {"username": "u-1001", "items": ["insulated bottle"]},
                      "Policy check.", "insulated bottle=eligible"),
                _step("calculate_refund",
                      {"username": "u-1001", "items": ["insulated bottle"]},
                      "Total the eligible items.", "total=28.00"),
                _step("process_return_request",
                      {"username": "u-1001", "items": ["insulated bottle"],
                       "amount": 28.0},
                      "File a ticket for human review.", "tkt-demo-2"),
            ), 160, 400),
        SyntheticTrace(
            "t-v2-fraud", "v2",
            "I am u-1003, I would like a refund for the running sneakers.",
            "No refund will be issued. running sneakers: was never purchased.",
            (order, elig_never), 160, 420),
        SyntheticTrace(
            "t-v2-user", "v2",
            "I am u-9999, I would like a refund for the trail backpack.",
            "The username u-9999 is not valid. Please check it and send a "
            "valid username so I can look up your order.",
            (_step("get_order_details", {"username": "u-9999"},
                   "Look up the customer before deciding.", "user_not_found"),),
            130, 280),
        SyntheticTrace(
            "t-v2-refund", "v2",
            "I am u-1001, I would like a refund for the trail backpack.",
            "A refund ticket tkt-demo for $45.00 has been issued and will be "
            "reviewed by our team before payout.",
            (
                _step("get_order_details", {"username": "u-1001"},
                      "Need the purchase history.", "ok"),
                _step("check_refund_eligibility",
                      {"username": "u-1001", "items": ["trail backpack"]},
                      "Policy check.", "trail backpack=eligible"),
                _step("calculate_refund",
                      {"username": "u-1001", "items": ["trail backpack"]},
                      "Total the eligible items.", "total=45.00"),
                _step("process_return_request",
                      {"username": "u-1001", "items": ["trail backpack"],
                       "amount": 45.0},
                      "File a ticket for human review.", "tkt-demo"),
            ), 150, 380),
        SyntheticTrace(
            "t-v2-info", "v2",
            "I am u-1001, what is my order status?",
            "I found 1 order(s) on your account. Let me know if you would "
            "like a refund for any item.",
            (_step("get_order_details", {"username": "u-1001"},
                   "Answer from history; issue nothing.", "ok"),),
            120, 260),
    )
    feedback = (
        Feedback("fb-date-1", "t-date-1", "expert", 2,
                 "The date lookup cannot change a never-purchased decision.",
                 (DATE_LOOKUP,)),
        Feedback("fb-date-2", "t-date-2", "user", 2,
                 "There was an extra step before the denial.",
                 (DATE_LOOKUP,)),
        Feedback("fb-date-3", "t-date-3", "expert", 3,
                 "Same extra date call as the other denials.", ()),
        Feedback("fb-date-4", "t-date-4", "user", 2,
                 "This denial did more work than it needed to.",
                 (DATE_LOOKUP,)),
        Feedback("fb-user-1", "t-user-1", "expert", 2,
                 "The username is the problem. Ask for a valid one.",
                 (UNKNOWN_USER,)),
        Feedback("fb-user-2", "t-user-2", "user", 1,
                 "I was told there is no purchase history.",
                 (UNKNOWN_USER,)),
        Feedback("fb-user-3", "t-user-3", "user", 2,
                 "It talked about history instead of the username.", ()),
        Feedback("fb-vague-1", "t-vague-1", "expert", 2,
                 "The customer named no item and the answer gave up.",
                 (VAGUE_ITEM,)),
        Feedback("fb-good-1", "t-good-1", "user", 5,
                 "Ticket filed and the amount matches.", ()),
        Feedback("fb-good-2", "t-good-2", "user", 5,
                 "Status answer was enough.", ()),
        Feedback("fb-unhappy-1", "t-unhappy-1", "user", 1,
                 "Unhappy, but the ticket and the amount were right.", ()),
        Feedback("fb-v2-fraud", "t-v2-fraud", "user", 5,
                 "Denial names the item and skips the extra step.", ()),
        Feedback("fb-v2-user", "t-v2-user", "expert", 5,
                 "Asks for a valid username.", ()),
        Feedback("fb-v2-refund", "t-v2-refund", "user", 5,
                 "Ticket filed for review.", ()),
        Feedback("fb-v2-info", "t-v2-info", "user", 4,
                 "Status answer was enough.", ()),
        Feedback("fb-orphan", "t-missing", "expert", 1,
                 "This note points at a trace that was not retained.", ()),
    )
    corpus = Corpus(traces, feedback)
    trace_ids = [t.trace_id for t in corpus.traces]
    feedback_ids = [f.feedback_id for f in corpus.feedback]
    if len(trace_ids) != len(set(trace_ids)):
        raise ValueError("duplicate synthetic trace id")
    if len(feedback_ids) != len(set(feedback_ids)):
        raise ValueError("duplicate synthetic feedback id")
    for note in corpus.feedback:
        if note.source not in ("user", "expert"):
            raise ValueError("feedback source must be user or expert")
        if not 1 <= note.rating <= 5:
            raise ValueError("feedback rating must be from 1 to 5")
    return corpus


@dataclass(frozen=True)
class Baseline:
    version: str
    metric_averages: dict
    metric_pass_rates: dict
    results: tuple
    patterns: tuple
    golden_case_ids: tuple
    mean_latency_ms: float
    mean_tokens: float
    trace_count: int
    feedback_count: int
    applied_change_ids: tuple
    model: str


@dataclass(frozen=True)
class ProblemCluster:
    cluster_id: str
    cause: str
    suggested_kind: str
    suggested_fix: str
    trace_ids: tuple
    feedback_ids: tuple
    eval_case_ids: tuple
    frequency: int


@dataclass(frozen=True)
class Findings:
    clusters: tuple
    below_threshold: tuple
    orphan_feedback_ids: tuple


@dataclass(frozen=True)
class Candidate:
    change_id: str
    kind: str
    description: str
    instruction: str
    targets: tuple
    results: tuple | None
    metric_averages: dict
    mean_latency_ms: float
    mean_tokens: float
    adopt_version: str | None
    model: str
    measurable: bool = True


@dataclass(frozen=True)
class GateResult:
    change_id: str
    passed: bool
    reasons: tuple
    metric_regressions: tuple
    case_regressions: tuple
    quality_before: float
    quality_after: float
    latency_before: float
    latency_after: float
    tokens_before: float
    tokens_after: float


@dataclass(frozen=True)
class LoopResult:
    baseline: Baseline
    findings: Findings
    candidates: tuple
    gates: tuple
    recommendation_id: str | None
    promoted: bool
    next_baseline: Baseline
    regression_threshold: float
    min_improvement: float
    min_frequency: int


def _means(traces):
    if not traces:
        return 0.0, 0.0
    return (sum(t.latency_ms for t in traces) / len(traces),
            sum(t.tokens for t in traces) / len(traces))


def _majority_model(traces):
    if not traces:
        return DEFAULT_MODEL
    counts = Counter(t.model for t in traces)
    return sorted(counts, key=lambda name: (-counts[name], name))[0]


def _scorecard(results):
    summary = summarize(results)
    averages = {name: row["average"] for name, row in summary.items()}
    pass_rates = {name: row["pass_rate"] for name, row in summary.items()}
    return averages, pass_rates


def _quality(averages):
    if not averages:
        return 0.0
    return sum(averages.values()) / len(averages)


def _check_args(regression_threshold, min_improvement, min_frequency):
    if regression_threshold < 0:
        raise ValueError("regression_threshold must be >= 0")
    if min_improvement < 0:
        raise ValueError("min_improvement must be >= 0")
    if min_frequency < 1:
        raise ValueError("min_frequency must be >= 1")


def establish_baseline(version="v1", corpus=None, applied_change_ids=()):
    """Freeze harness scores and the synthetic traffic for one version.

    The golden set is the behaviour-case run from ``run_all``. Latency
    and tokens come from the synthetic traces for that version.
    """
    if version not in ("v1", "v2"):
        raise ValueError("version must be 'v1' or 'v2'")
    corpus = synthetic_corpus() if corpus is None else corpus
    scoped = corpus.for_version(version)
    results = tuple(run_all(version=version))
    averages, pass_rates = _scorecard(results)
    latency, tokens = _means(scoped.traces)
    return Baseline(
        version=version,
        metric_averages=averages,
        metric_pass_rates=pass_rates,
        results=results,
        patterns=tuple(analyze.detect_patterns(results)),
        golden_case_ids=tuple(r.case_id for r in results),
        mean_latency_ms=latency,
        mean_tokens=tokens,
        trace_count=len(scoped.traces),
        feedback_count=len(scoped.feedback),
        applied_change_ids=tuple(applied_change_ids),
        model=_majority_model(scoped.traces),
    )


def _problem_keys(trace, feedbacks):
    keys = set()
    for note in feedbacks:
        keys.update(label for label in note.labels if label)
    tools = [step["tool"] for step in trace.steps]
    answer = (trace.answer or "").lower()
    wasted_date = DATE_TOOL in tools and (
        "never purchased" in answer or "not purchased" in answer
        or "outside the" in answer)
    if wasted_date:
        keys.add(DATE_LOOKUP)
    if "no purchase history" in answer or "purchase history found" in answer:
        keys.add(UNKNOWN_USER)
    return keys


def identify_problems(corpus, baseline, min_frequency=2):
    """Cluster traces and feedback. Rank clusters that affect the most traces.

    A signature seen fewer than ``min_frequency`` times is returned in
    ``below_threshold`` rather than dropped, so a one-off is visible and
    still not treated as the next fix. Eval patterns from the baseline
    are attached as extra evidence. A pattern with no traces uses its
    eval-case count as the frequency.
    """
    if min_frequency < 1:
        raise ValueError("min_frequency must be >= 1")
    by_trace = defaultdict(list)
    orphans = []
    known = {t.trace_id for t in corpus.traces}
    for note in corpus.feedback:
        if note.trace_id not in known:
            orphans.append(note.feedback_id)
        else:
            by_trace[note.trace_id].append(note)

    grouped = defaultdict(list)
    feedback_ids = defaultdict(set)
    for trace in corpus.traces:
        notes = by_trace.get(trace.trace_id, [])
        for key in _problem_keys(trace, notes):
            grouped[key].append(trace)
            for note in notes:
                labeled = key in note.labels
                unlabeled_unhappy = not note.labels and note.rating <= 3
                if labeled or unlabeled_unhappy:
                    feedback_ids[key].add(note.feedback_id)

    eval_cases = {}
    for pattern in baseline.patterns:
        eval_cases[pattern["id"]] = tuple(pattern["evidence"])

    clusters = []
    held = []
    for cluster_id in set(grouped) | set(eval_cases):
        meta = _cluster_meta(cluster_id)
        trace_ids = tuple(sorted({t.trace_id for t in grouped.get(cluster_id, [])}))
        case_ids = eval_cases.get(cluster_id, ())
        frequency = len(trace_ids) if trace_ids else len(case_ids)
        cluster = ProblemCluster(
            cluster_id=cluster_id,
            cause=meta["cause"],
            suggested_kind=meta["kind"],
            suggested_fix=meta["fix"],
            trace_ids=trace_ids,
            feedback_ids=tuple(sorted(feedback_ids.get(cluster_id, ()))),
            eval_case_ids=case_ids,
            frequency=frequency,
        )
        if frequency >= min_frequency:
            clusters.append(cluster)
        elif frequency > 0:
            held.append(cluster)

    def _rank(cluster):
        return (-cluster.frequency, cluster.cluster_id)

    return Findings(
        clusters=tuple(sorted(clusters, key=_rank)),
        below_threshold=tuple(sorted(held, key=_rank)),
        orphan_feedback_ids=tuple(sorted(set(orphans))),
    )


def _blend(baseline_results, improved_by_id, take):
    blended = []
    for current in baseline_results:
        improved = improved_by_id.get(current.case_id)
        if improved is None:
            raise ValueError("v2 results are missing case %s" % current.case_id)
        metrics = dict(current.metric_results)
        for name in take:
            metrics[name] = improved.metric_results[name]
        blended.append(replace(current, metric_results=metrics))
    return tuple(blended)


def _penalize(results, names, penalty):
    penalized = []
    for current in results:
        metrics = dict(current.metric_results)
        for name in names:
            original = metrics[name]
            score = max(0.0, original.score - penalty)
            metrics[name] = MetricResult(
                original.metric, score, score >= 0.8,
                original.reason + "; synthetic smaller-model penalty")
        penalized.append(replace(current, metric_results=metrics))
    return tuple(penalized)


def _traffic(traces, drop_date_tool=False, extra_tokens=0,
             latency_factor=1.0, token_factor=1.0):
    if not traces:
        return 0.0, 0.0
    latencies = []
    tokens = []
    for trace in traces:
        latency = trace.latency_ms
        token_count = trace.tokens
        if drop_date_tool and any(step["tool"] == DATE_TOOL for step in trace.steps):
            latency = max(0, latency - DATE_LATENCY_MS)
            token_count = max(0, token_count - DATE_TOKENS)
        token_count += extra_tokens
        latencies.append(latency * latency_factor)
        tokens.append(token_count * token_factor)
    return sum(latencies) / len(latencies), sum(tokens) / len(tokens)


def _candidate(change_id, kind, description, instruction, targets, results,
               latency, tokens, adopt_version, model, measurable=True):
    averages = _scorecard(results)[0] if results else {}
    return Candidate(
        change_id=change_id,
        kind=kind,
        description=description,
        instruction=instruction,
        targets=tuple(targets),
        results=results,
        metric_averages=averages,
        mean_latency_ms=latency,
        mean_tokens=tokens,
        adopt_version=adopt_version,
        model=model,
        measurable=measurable,
    )


def optimize(findings, baseline, corpus=None, include_cost=True):
    """Propose prompt, tool, and config candidates for the open clusters.

    Candidates whose ids are already on the baseline are not proposed
    again. Quality candidates are measured with the existing harness.
    A cluster with no metric mapping is returned as an unmeasured
    candidate so it cannot sneak through the gate.
    """
    corpus = synthetic_corpus() if corpus is None else corpus
    traces = corpus.for_version(baseline.version).traces
    applied = set(baseline.applied_change_ids)
    present = {c.cluster_id: c for c in findings.clusters}
    measured = []
    unmeasured = []
    for cluster in findings.clusters:
        meta = _cluster_meta(cluster.cluster_id)
        if meta["metrics"]:
            measured.append(cluster)
        else:
            unmeasured.append(cluster)

    improved = None
    if measured:
        improved = {r.case_id: r for r in run_all(version="v2")}

    candidates = []
    for cluster in measured:
        meta = _cluster_meta(cluster.cluster_id)
        if cluster.cluster_id == UNKNOWN_USER:
            change_id = PROMPT_UNKNOWN_USER
            kind = "prompt"
            drop_date = False
            extra = PROMPT_EXTRA_TOKENS
        elif cluster.cluster_id == DATE_LOOKUP:
            change_id = TOOL_DROP_DATE
            kind = "tool"
            drop_date = True
            extra = 0
        else:
            continue
        if change_id in applied:
            continue
        latency, tokens = _traffic(traces, drop_date_tool=drop_date, extra_tokens=extra)
        results = _blend(baseline.results, improved, meta["metrics"])
        candidates.append(_candidate(
            change_id, kind, meta["fix"], meta["fix"], (cluster.cluster_id,),
            results, latency, tokens, None, baseline.model))

    if (UNKNOWN_USER in present and DATE_LOOKUP in present
            and COMBINED_QUALITY not in applied):
        latency, tokens = _traffic(
            traces, drop_date_tool=True, extra_tokens=PROMPT_EXTRA_TOKENS)
        instruction = "%s %s" % (
            _cluster_meta(UNKNOWN_USER)["fix"], _cluster_meta(DATE_LOOKUP)["fix"])
        candidates.append(_candidate(
            COMBINED_QUALITY, "prompt+tool",
            "Apply the prompt fix and the tool fix together.",
            instruction, (UNKNOWN_USER, DATE_LOOKUP),
            tuple(improved.values()) if improved else tuple(run_all(version="v2")),
            latency, tokens, "v2", baseline.model))

    for cluster in unmeasured:
        change_id = "unmeasured-%s" % cluster.cluster_id
        if change_id in applied:
            continue
        meta = _cluster_meta(cluster.cluster_id)
        latency, tokens = _means(traces)
        candidates.append(_candidate(
            change_id, meta["kind"], meta["fix"], meta["fix"],
            (cluster.cluster_id,), None, latency, tokens, None,
            baseline.model, measurable=False))

    if include_cost and CONFIG_SMALL_MODEL not in applied:
        latency, tokens = _traffic(
            traces, latency_factor=SMALL_MODEL_LATENCY_FACTOR,
            token_factor=SMALL_MODEL_TOKEN_FACTOR)
        results = _penalize(baseline.results, SMALL_MODEL_METRICS, SMALL_MODEL_PENALTY)
        candidates.append(_candidate(
            CONFIG_SMALL_MODEL, "config",
            "Route the agent to a smaller mock model.",
            "Route this agent to the smaller mock model (%s). This "
            "candidate is a synthetic cost cut with a fixed quality "
            "penalty, so a cheaper run that gets worse fails the gate."
            % SMALL_MODEL,
            (), results, latency, tokens, None, SMALL_MODEL))

    if include_cost and CONFIG_TOKEN_CAP not in applied:
        latency, tokens = _traffic(traces, token_factor=TOKEN_CAP_FACTOR)
        candidates.append(_candidate(
            CONFIG_TOKEN_CAP, "config",
            "Cap completion length without changing metric scores.",
            "Cap completion length. In this mock the cap trims tokens "
            "and leaves every metric score unchanged.",
            (), baseline.results, latency, tokens, None, baseline.model))
    return tuple(candidates)


def validate(baseline, candidate, regression_threshold=0.0, min_improvement=0.05):
    """Score one candidate against the baseline.

    The decision uses the candidate's case results, not the averages it
    advertises. A drop greater than ``regression_threshold`` fails the
    gate, both on metric averages and on any single case (via
    ``improve.case_regressions``). Prompt and tool changes must also
    clear ``min_improvement`` on a targeted metric. Config changes must
    hold quality and reduce tokens or latency.
    """
    _check_args(regression_threshold, min_improvement, 1)
    quality_before = _quality(baseline.metric_averages)
    empty = GateResult(
        change_id=candidate.change_id, passed=False,
        reasons=("no measured eval scores for this candidate",),
        metric_regressions=(), case_regressions=(),
        quality_before=quality_before, quality_after=quality_before,
        latency_before=baseline.mean_latency_ms,
        latency_after=candidate.mean_latency_ms,
        tokens_before=baseline.mean_tokens,
        tokens_after=candidate.mean_tokens)
    if not candidate.measurable or not candidate.results:
        return empty

    averages = _scorecard(candidate.results)[0]
    quality_after = _quality(averages)
    metric_regressions = []
    reasons = []
    for name, before in baseline.metric_averages.items():
        after = averages.get(name, 0.0)
        drop = before - after
        if drop > regression_threshold:
            metric_regressions.append((name, before, after))
            reasons.append(
                "metric regression: %s dropped %.3f (limit %.3f)"
                % (name, drop, regression_threshold))

    base_ids = {r.case_id for r in baseline.results}
    cand_ids = {r.case_id for r in candidate.results}
    case_regressions = ()
    if base_ids != cand_ids:
        reasons.append("candidate cases do not match the golden dataset")
    else:
        case_regressions = tuple(improve.case_regressions(
            {"v1_results": baseline.results, "v2_results": candidate.results},
            tolerance=regression_threshold))
        if case_regressions:
            reasons.append(
                "case regression: %d case/metric score(s) dropped past the limit"
                % len(case_regressions))

    if candidate.kind == "config":
        cheaper = (candidate.mean_tokens < baseline.mean_tokens - 1e-9
                   or candidate.mean_latency_ms < baseline.mean_latency_ms - 1e-9)
        if not cheaper:
            reasons.append("cost and latency did not improve")
    else:
        targeted = []
        for cluster_id in candidate.targets:
            targeted.extend(_cluster_meta(cluster_id)["metrics"])
        if targeted:
            gain = max(averages.get(name, 0.0) - baseline.metric_averages.get(name, 0.0)
                       for name in targeted)
        else:
            gain = 0.0
        if gain < min_improvement:
            reasons.append(
                "targeted metrics did not improve by at least %.3f" % min_improvement)

    return GateResult(
        change_id=candidate.change_id,
        passed=not reasons,
        reasons=tuple(reasons),
        metric_regressions=tuple(metric_regressions),
        case_regressions=case_regressions,
        quality_before=quality_before,
        quality_after=quality_after,
        latency_before=baseline.mean_latency_ms,
        latency_after=candidate.mean_latency_ms,
        tokens_before=baseline.mean_tokens,
        tokens_after=candidate.mean_tokens,
    )


def _rank_gate(gate):
    quality_delta = gate.quality_after - gate.quality_before
    token_savings = gate.tokens_before - gate.tokens_after
    latency_savings = gate.latency_before - gate.latency_after
    return (-quality_delta, -token_savings, -latency_savings, gate.change_id)


def recommend(candidates, gates):
    """Pick the passing candidate with the best quality, then cost, then speed.

    Returns None when every candidate fails the gate. The caller still
    decides whether to promote; this only names the strongest candidate.
    """
    by_id = {c.change_id: c for c in candidates}
    passing = [g for g in gates if g.passed and g.change_id in by_id]
    if not passing:
        return None
    best = sorted(passing, key=_rank_gate)[0]
    return by_id[best.change_id]


def promote(baseline, candidate, gate, corpus=None):
    """Adopt a candidate that cleared the gate. Refuses a failed gate.

    A candidate with ``adopt_version`` (the combined fix, measured as
    agent v2) re-establishes the baseline from that version, including
    its synthetic traffic. Other candidates keep the version and take
    the candidate's scores and cost estimate.
    """
    if gate.change_id != candidate.change_id:
        raise ValueError("gate does not match candidate %s" % candidate.change_id)
    if not gate.passed:
        raise ValueError(
            "refusing to promote %s: the gate failed" % candidate.change_id)
    applied = tuple(dict.fromkeys((*baseline.applied_change_ids, candidate.change_id)))
    if candidate.adopt_version and candidate.adopt_version != baseline.version:
        return establish_baseline(
            candidate.adopt_version,
            synthetic_corpus() if corpus is None else corpus,
            applied_change_ids=applied)
    patterns = (tuple(analyze.detect_patterns(candidate.results))
                if candidate.results else baseline.patterns)
    averages, pass_rates = (_scorecard(candidate.results)
                            if candidate.results else
                            (baseline.metric_averages, baseline.metric_pass_rates))
    return replace(
        baseline,
        metric_averages=averages,
        metric_pass_rates=pass_rates,
        results=candidate.results if candidate.results is not None else baseline.results,
        patterns=patterns,
        mean_latency_ms=candidate.mean_latency_ms,
        mean_tokens=candidate.mean_tokens,
        applied_change_ids=applied,
        model=candidate.model,
    )


def run_loop(version="v1", corpus=None, baseline=None, apply=False,
             include_cost=True, min_frequency=2, regression_threshold=0.0,
             min_improvement=0.05):
    """Run one pass. Promotion happens only when ``apply`` is true and the gate passed."""
    _check_args(regression_threshold, min_improvement, min_frequency)
    corpus = synthetic_corpus() if corpus is None else corpus
    if baseline is None:
        baseline = establish_baseline(version, corpus)
    findings = identify_problems(
        scope_corpus(corpus, baseline.version), baseline, min_frequency=min_frequency)
    candidates = optimize(findings, baseline, corpus, include_cost=include_cost)
    gates = tuple(
        validate(baseline, candidate, regression_threshold, min_improvement)
        for candidate in candidates)
    chosen = recommend(candidates, gates)
    promoted = False
    next_baseline = baseline
    if apply and chosen is not None:
        gate = next(g for g in gates if g.change_id == chosen.change_id)
        next_baseline = promote(baseline, chosen, gate, corpus)
        promoted = True
    return LoopResult(
        baseline=baseline,
        findings=findings,
        candidates=candidates,
        gates=gates,
        recommendation_id=None if chosen is None else chosen.change_id,
        promoted=promoted,
        next_baseline=next_baseline,
        regression_threshold=regression_threshold,
        min_improvement=min_improvement,
        min_frequency=min_frequency,
    )


def _fmt_delta(value):
    return "%+.2f" % value


def build_report(result):
    """Markdown report for one pass: baseline, clusters, candidates, gate."""
    baseline = result.baseline
    intro = (
        "Offline mock. One pass of establish, identify, optimize, and "
        "validate. Product-specific claims about the hosted loop are "
        "marked [verify] in docs/continuous-improvement.md."
    )
    lines = [
        "# Continuous improvement report",
        "",
        intro,
        "",
        "## Establish",
        "",
        "- Version: %s" % baseline.version,
        "- Model: %s" % baseline.model,
        "- Golden cases: %d" % len(baseline.golden_case_ids),
        "- Synthetic traces: %d" % baseline.trace_count,
        "- Feedback notes on those traces: %d" % baseline.feedback_count,
        "- Mean latency: %.2f ms" % baseline.mean_latency_ms,
        "- Mean tokens: %.2f" % baseline.mean_tokens,
        "- Applied changes: %s" % (", ".join(baseline.applied_change_ids) or "none"),
        "",
        "| Metric | Average | Pass rate |",
        "| --- | --- | --- |",
    ]
    for name, average in baseline.metric_averages.items():
        lines.append("| %s | %.2f | %.0f%% |" % (
            name, average, baseline.metric_pass_rates[name] * 100))
    lines.extend(["", "## Identify", ""])
    if not result.findings.clusters:
        lines.append("No cluster met the frequency cutoff (%d)." % result.min_frequency)
        lines.append("")
    for cluster in result.findings.clusters:
        lines.extend(_cluster_lines(cluster))
    if result.findings.below_threshold:
        lines.append("Below the frequency cutoff:")
        lines.append("")
        for cluster in result.findings.below_threshold:
            lines.append("- %s (%d)" % (cluster.cluster_id, cluster.frequency))
        lines.append("")
    else:
        lines.append("Below the frequency cutoff: none.")
        lines.append("")
    orphan = ", ".join(result.findings.orphan_feedback_ids) or "none"
    lines.extend(["Orphan feedback (no matching trace): %s" % orphan, "",
                  "## Optimize", ""])
    if not result.candidates:
        lines.append("No candidates.")
        lines.append("")
    for candidate in result.candidates:
        lines.extend([
            "### %s" % candidate.change_id,
            "",
            "- Kind: %s" % candidate.kind,
            "- Measured: %s" % ("yes" if candidate.measurable else "no"),
            "- Instruction: %s" % candidate.instruction,
            "",
        ])
    gate_note = (
        "Regression limit: %.3f. Minimum targeted gain for prompt and tool "
        "changes: %.3f. Config changes must hold quality and reduce tokens "
        "or latency." % (result.regression_threshold, result.min_improvement)
    )
    lines.extend([
        "## Validate",
        "",
        gate_note,
        "",
        "| Candidate | Kind | Gate | Quality | Latency ms | Tokens |",
        "| --- | --- | --- | --- | --- | --- |",
    ])
    by_id = {c.change_id: c for c in result.candidates}
    gate_by_id = {g.change_id: g for g in result.gates}
    for candidate in result.candidates:
        gate = gate_by_id[candidate.change_id]
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            candidate.change_id, candidate.kind,
            "pass" if gate.passed else "fail",
            _fmt_delta(gate.quality_after - gate.quality_before),
            _fmt_delta(gate.latency_after - gate.latency_before),
            _fmt_delta(gate.tokens_after - gate.tokens_before)))
    lines.append("")
    for gate in result.gates:
        if gate.passed:
            continue
        lines.append("- %s: %s" % (gate.change_id, "; ".join(gate.reasons)))
    if any(not g.passed for g in result.gates):
        lines.append("")
    chosen = result.recommendation_id or "none"
    lines.extend(["Recommendation: %s" % chosen, ""])
    if result.promoted:
        lines.append(
            "Promoted %s. The next baseline is %s."
            % (result.recommendation_id, result.next_baseline.version))
    elif result.recommendation_id:
        lines.append(
            "Held. %s cleared the gate. The baseline stays %s until "
            "apply=True." % (result.recommendation_id, baseline.version))
    else:
        lines.append(
            "Held. No candidate cleared the gate. The baseline stays %s."
            % baseline.version)
    lines.append("")
    if result.recommendation_id and result.recommendation_id in by_id:
        gate = gate_by_id[result.recommendation_id]
        lines.append(
            "Recommendation metric regressions: %d. Case regressions: %d."
            % (len(gate.metric_regressions), len(gate.case_regressions)))
        lines.append("")
    return "\n".join(lines)


def _cluster_lines(cluster):
    return [
        "### %s" % cluster.cluster_id,
        "",
        "- Frequency: %d" % cluster.frequency,
        "- Cause: %s" % cluster.cause,
        "- Suggested change: %s" % cluster.suggested_kind,
        "- Suggested fix: %s" % cluster.suggested_fix,
        "- Traces: %s" % (", ".join(cluster.trace_ids) or "none"),
        "- Feedback: %s" % (", ".join(cluster.feedback_ids) or "none"),
        "- Eval cases: %s" % (", ".join(cluster.eval_case_ids) or "none"),
        "",
    ]


def render_summary(result):
    """Short text summary. The README renders this and CI checks it."""
    baseline = result.baseline
    baseline_line = (
        "baseline: %s | cases: %d | traces: %d | feedback: %d | "
        "latency_ms: %.2f | tokens: %.2f" % (
            baseline.version, len(baseline.golden_case_ids), baseline.trace_count,
            baseline.feedback_count, baseline.mean_latency_ms, baseline.mean_tokens)
    )
    lines = [
        "continuous improvement (offline mock)",
        baseline_line,
    ]
    if result.findings.clusters:
        lines.append("clusters: " + ", ".join(
            "%s (%d)" % (c.cluster_id, c.frequency) for c in result.findings.clusters))
    else:
        lines.append("clusters: none")
    if result.findings.below_threshold:
        lines.append("below threshold: " + ", ".join(
            "%s (%d)" % (c.cluster_id, c.frequency)
            for c in result.findings.below_threshold))
    else:
        lines.append("below threshold: none")
    lines.append("orphan feedback: %s" % (
        ", ".join(result.findings.orphan_feedback_ids) or "none"))
    lines.append("candidates:")
    gates = {g.change_id: g for g in result.gates}
    if not result.candidates:
        lines.append("  none")
    for candidate in result.candidates:
        gate = gates[candidate.change_id]
        lines.append(
            "  %s | %s | %s | quality %s | latency %s | tokens %s" % (
                candidate.change_id, candidate.kind,
                "pass" if gate.passed else "fail",
                _fmt_delta(gate.quality_after - gate.quality_before),
                _fmt_delta(gate.latency_after - gate.latency_before),
                _fmt_delta(gate.tokens_after - gate.tokens_before)))
    lines.append("recommendation: %s" % (result.recommendation_id or "none"))
    if result.promoted:
        lines.append("promoted: yes | next baseline: %s" % result.next_baseline.version)
    else:
        lines.append("promoted: no")
    if result.recommendation_id:
        gate = gates[result.recommendation_id]
        lines.append("recommendation metric regressions: %d" % len(gate.metric_regressions))
        lines.append("recommendation case regressions: %d" % len(gate.case_regressions))
    return "\n".join(lines)
