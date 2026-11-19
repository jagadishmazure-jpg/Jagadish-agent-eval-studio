"""Continuous-improvement loop: cluster, optimize, gate, repeat."""

import pathlib
from dataclasses import replace

import pytest

from evaluation.continuous import (
    COMBINED_QUALITY,
    CONFIG_SMALL_MODEL,
    CONFIG_TOKEN_CAP,
    DATE_LOOKUP,
    PROMPT_EXTRA_TOKENS,
    PROMPT_UNKNOWN_USER,
    SMALL_MODEL_LATENCY_FACTOR,
    SMALL_MODEL_TOKEN_FACTOR,
    TOKEN_CAP_FACTOR,
    TOOL_DROP_DATE,
    UNKNOWN_USER,
    Corpus,
    Feedback,
    GateResult,
    SyntheticTrace,
    build_report,
    establish_baseline,
    identify_problems,
    optimize,
    promote,
    recommend,
    render_summary,
    run_loop,
    scope_corpus,
    synthetic_corpus,
    validate,
)
from evaluation.metrics import MetricResult
from evaluation.run_eval import run_all, summarize

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE = "https://azure.microsoft.com/en-us/blog/continuous-improvement-comes-to-microsoft-foundry/"


def _drop_metric(results, metric, amount, case_id=None):
    dropped = []
    for result in results:
        metrics = dict(result.metric_results)
        if case_id is None or result.case_id == case_id:
            current = metrics[metric]
            score = max(0.0, current.score - amount)
            metrics[metric] = MetricResult(
                current.metric, score, score >= 0.8, current.reason)
        dropped.append(replace(result, metric_results=metrics))
    return tuple(dropped)


def _gate(change_id, quality_after, tokens_after, latency_after=10.0):
    return GateResult(
        change_id=change_id, passed=True, reasons=(),
        metric_regressions=(), case_regressions=(),
        quality_before=0.5, quality_after=quality_after,
        latency_before=10.0, latency_after=latency_after,
        tokens_before=100.0, tokens_after=tokens_after)


def test_synthetic_corpus_is_internally_consistent():
    corpus = synthetic_corpus()
    trace_ids = [t.trace_id for t in corpus.traces]
    feedback_ids = [f.feedback_id for f in corpus.feedback]
    assert len(trace_ids) == len(set(trace_ids))
    assert len(feedback_ids) == len(set(feedback_ids))
    assert {t.agent_version for t in corpus.traces} <= {"v1", "v2"}
    for note in corpus.feedback:
        assert note.source in {"user", "expert"}
        assert 1 <= note.rating <= 5
    assert any(note.trace_id == "t-missing" for note in corpus.feedback)


def test_baseline_matches_the_harness():
    baseline = establish_baseline("v1")
    summary = summarize(run_all(version="v1"))
    assert baseline.golden_case_ids
    assert len(baseline.golden_case_ids) == summary["task_completion"]["n"] == 10
    for name, row in summary.items():
        assert baseline.metric_averages[name] == row["average"]
        assert baseline.metric_pass_rates[name] == row["pass_rate"]
    assert baseline.trace_count == 11
    assert baseline.feedback_count == 11
    assert baseline.patterns


def test_scope_keeps_orphans_and_drops_other_versions():
    scoped = scope_corpus(synthetic_corpus(), "v1")
    assert any(note.feedback_id == "fb-orphan" for note in scoped.feedback)
    assert all(note.trace_id != "t-v2-refund" for note in scoped.feedback)


def test_clusters_rank_the_most_common_problem_first():
    baseline = establish_baseline("v1")
    findings = identify_problems(scope_corpus(synthetic_corpus(), "v1"), baseline)
    assert [c.cluster_id for c in findings.clusters] == [DATE_LOOKUP, UNKNOWN_USER]
    assert findings.clusters[0].frequency == 4
    assert findings.clusters[1].frequency == 3
    assert [c.cluster_id for c in findings.below_threshold] == ["vague-item-ask"]
    assert findings.below_threshold[0].frequency == 1
    assert findings.orphan_feedback_ids == ("fb-orphan",)


def test_clusters_carry_trace_feedback_and_eval_evidence():
    baseline = establish_baseline("v1")
    findings = identify_problems(scope_corpus(synthetic_corpus(), "v1"), baseline)
    by_id = {c.cluster_id: c for c in findings.clusters}
    date = by_id[DATE_LOOKUP]
    user = by_id[UNKNOWN_USER]
    assert "t-date-3" in date.trace_ids
    assert "fb-date-3" in date.feedback_ids
    assert "fraud-never-purchased" in date.eval_case_ids
    assert len(date.eval_case_ids) == 2
    assert "t-user-3" in user.trace_ids
    assert user.eval_case_ids == ("invalid-user",)
    assert date.suggested_fix and user.cause


def test_clean_and_unhappy_traces_do_not_form_clusters():
    baseline = establish_baseline("v1")
    findings = identify_problems(scope_corpus(synthetic_corpus(), "v1"), baseline)
    seen = {tid for cluster in (*findings.clusters, *findings.below_threshold)
            for tid in cluster.trace_ids}
    assert "t-good-1" not in seen
    assert "t-good-2" not in seen
    assert "t-unhappy-1" not in seen
    assert "t-v2-fraud" not in seen


def test_v2_traffic_has_no_problem_clusters():
    baseline = establish_baseline("v2")
    findings = identify_problems(scope_corpus(synthetic_corpus(), "v2"), baseline)
    assert findings.clusters == ()
    assert findings.below_threshold == ()
    assert findings.orphan_feedback_ids == ("fb-orphan",)


def test_eval_patterns_surface_when_there_is_no_traffic():
    empty = Corpus((), ())
    baseline = establish_baseline("v1", empty)
    findings = identify_problems(empty, baseline)
    clustered = {c.cluster_id for c in findings.clusters}
    held = {c.cluster_id for c in findings.below_threshold}
    assert DATE_LOOKUP in clustered
    assert UNKNOWN_USER in held
    assert UNKNOWN_USER not in clustered


def test_clustering_ignores_input_order():
    corpus = synthetic_corpus()
    baseline = establish_baseline("v1", corpus)
    original = identify_problems(scope_corpus(corpus, "v1"), baseline)
    flipped = Corpus(tuple(reversed(corpus.traces)), tuple(reversed(corpus.feedback)))
    again = identify_problems(scope_corpus(flipped, "v1"), baseline)
    assert original == again


def test_one_trace_can_support_two_clusters():
    trace = SyntheticTrace(
        "t-both", "v1", "refund",
        "No purchase history found. running sneakers: was never purchased.",
        ({"tool": "get_today", "arguments": {}, "reasoning": "date",
          "result": "2026-10-18"},),
        10, 10)
    note = Feedback("fb-both", "t-both", "expert", 2, "both problems",
                    (DATE_LOOKUP, UNKNOWN_USER))
    baseline = establish_baseline("v1", Corpus((), ()))
    findings = identify_problems(Corpus((trace,), (note,)), baseline, min_frequency=1)
    by_id = {c.cluster_id: c for c in findings.clusters}
    assert "t-both" in by_id[DATE_LOOKUP].trace_ids
    assert "t-both" in by_id[UNKNOWN_USER].trace_ids


def test_optimizer_emits_prompt_tool_and_config_in_frequency_order():
    result = run_loop()
    ids = [c.change_id for c in result.candidates]
    kinds = {c.kind for c in result.candidates}
    assert {"prompt", "tool", "config", "prompt+tool"} <= kinds
    assert ids.index(TOOL_DROP_DATE) < ids.index(PROMPT_UNKNOWN_USER)
    assert ids.index(PROMPT_UNKNOWN_USER) < ids.index(COMBINED_QUALITY)
    assert ids.index(COMBINED_QUALITY) < ids.index(CONFIG_SMALL_MODEL)
    assert ids.index(CONFIG_SMALL_MODEL) < ids.index(CONFIG_TOKEN_CAP)
    for candidate in result.candidates:
        assert candidate.instruction.strip()
    prompt = next(c for c in result.candidates if c.change_id == PROMPT_UNKNOWN_USER)
    tool = next(c for c in result.candidates if c.change_id == TOOL_DROP_DATE)
    combined = next(c for c in result.candidates if c.change_id == COMBINED_QUALITY)
    assert "username" in prompt.instruction
    assert "get_today" in tool.instruction
    assert "get_today" in combined.instruction and "username" in combined.instruction


def test_cost_estimates_follow_the_change_kind():
    result = run_loop()
    baseline = result.baseline
    prompt = next(c for c in result.candidates if c.change_id == PROMPT_UNKNOWN_USER)
    tool = next(c for c in result.candidates if c.change_id == TOOL_DROP_DATE)
    small = next(c for c in result.candidates if c.change_id == CONFIG_SMALL_MODEL)
    cap = next(c for c in result.candidates if c.change_id == CONFIG_TOKEN_CAP)
    assert prompt.mean_latency_ms == baseline.mean_latency_ms
    assert prompt.mean_tokens == pytest.approx(baseline.mean_tokens + PROMPT_EXTRA_TOKENS)
    assert tool.mean_latency_ms < baseline.mean_latency_ms
    assert tool.mean_tokens < baseline.mean_tokens
    assert small.model == "mock-small"
    assert small.mean_latency_ms == pytest.approx(
        baseline.mean_latency_ms * SMALL_MODEL_LATENCY_FACTOR)
    assert small.mean_tokens == pytest.approx(
        baseline.mean_tokens * SMALL_MODEL_TOKEN_FACTOR)
    assert cap.mean_tokens == pytest.approx(baseline.mean_tokens * TOKEN_CAP_FACTOR)
    assert cap.mean_latency_ms == baseline.mean_latency_ms


def test_gate_passes_fixes_and_rejects_the_smaller_model():
    result = run_loop()
    gates = {g.change_id: g for g in result.gates}
    for change_id in (PROMPT_UNKNOWN_USER, TOOL_DROP_DATE, COMBINED_QUALITY, CONFIG_TOKEN_CAP):
        assert gates[change_id].passed, gates[change_id].reasons
    small = gates[CONFIG_SMALL_MODEL]
    assert not small.passed
    assert any("answer_correctness" in reason for reason in small.reasons)
    assert small.tokens_after < small.tokens_before
    assert result.recommendation_id == COMBINED_QUALITY
    assert gates[COMBINED_QUALITY].metric_regressions == ()
    assert gates[COMBINED_QUALITY].case_regressions == ()


def test_default_loop_holds_a_passing_candidate():
    result = run_loop()
    assert result.promoted is False
    assert result.next_baseline is result.baseline
    assert result.baseline.version == "v1"
    text = render_summary(result)
    assert "promoted: no" in text
    assert "unnecessary-date-lookup (4)" in text
    assert COMBINED_QUALITY in text


def test_only_the_open_cluster_is_optimized():
    baseline = establish_baseline("v1")
    findings = identify_problems(scope_corpus(synthetic_corpus(), "v1"), baseline)
    only_date = replace(
        findings,
        clusters=tuple(c for c in findings.clusters if c.cluster_id == DATE_LOOKUP),
        below_threshold=(),
        orphan_feedback_ids=(),
    )
    candidates = optimize(only_date, baseline, synthetic_corpus(), include_cost=False)
    assert [c.change_id for c in candidates] == [TOOL_DROP_DATE]


def test_singleton_cluster_is_not_measurable():
    result = run_loop(min_frequency=1, include_cost=False)
    assert any(c.cluster_id == "vague-item-ask" for c in result.findings.clusters)
    gate = next(g for g in result.gates if g.change_id == "unmeasured-vague-item-ask")
    assert not gate.passed
    assert any("no measured eval" in reason for reason in gate.reasons)
    assert result.recommendation_id == COMBINED_QUALITY


def test_regression_threshold_allows_noise_and_blocks_real_drops():
    result = run_loop(include_cost=False)
    prompt = next(c for c in result.candidates if c.change_id == PROMPT_UNKNOWN_USER)
    dropped = replace(prompt, results=_drop_metric(prompt.results, "plan_adherence", 0.02))
    tight = validate(result.baseline, dropped, regression_threshold=0.0)
    loose = validate(result.baseline, dropped, regression_threshold=0.05)
    assert not tight.passed
    assert any("plan_adherence" in reason for reason in tight.reasons)
    assert loose.passed


def test_case_regression_blocks_a_gain_the_average_hides():
    result = run_loop(include_cost=False)
    prompt = next(c for c in result.candidates if c.change_id == PROMPT_UNKNOWN_USER)
    case_id = prompt.results[0].case_id
    dropped = replace(
        prompt, results=_drop_metric(prompt.results, "plan_adherence", 0.4, case_id=case_id))
    gate = validate(result.baseline, dropped, regression_threshold=0.05)
    assert not gate.passed
    assert not any(name == "plan_adherence" for name, _before, _after in gate.metric_regressions)
    assert any(item[0] == case_id and item[1] == "plan_adherence"
               for item in gate.case_regressions)


def test_gate_scores_results_not_the_advertised_averages():
    result = run_loop(include_cost=False)
    prompt = next(c for c in result.candidates if c.change_id == PROMPT_UNKNOWN_USER)
    lied = replace(
        prompt,
        metric_averages={name: 1.0 for name in result.baseline.metric_averages},
        results=_drop_metric(prompt.results, "answer_correctness", 0.5),
    )
    gate = validate(result.baseline, lied)
    assert not gate.passed
    assert any("answer_correctness" in reason for reason in gate.reasons)


def test_config_without_savings_fails():
    result = run_loop(version="v2")
    cap = next(c for c in result.candidates if c.change_id == CONFIG_TOKEN_CAP)
    same = replace(
        cap, change_id="config-noop",
        mean_tokens=result.baseline.mean_tokens,
        mean_latency_ms=result.baseline.mean_latency_ms)
    gate = validate(result.baseline, same)
    assert not gate.passed
    assert any("cost and latency" in reason for reason in gate.reasons)


def test_min_improvement_blocks_a_weak_quality_change():
    blocked = run_loop(min_improvement=1.0, include_cost=False)
    assert blocked.gates
    assert all(not gate.passed for gate in blocked.gates)
    assert blocked.recommendation_id is None
    held_quality = run_loop(version="v2", min_improvement=1.0)
    assert held_quality.recommendation_id == CONFIG_TOKEN_CAP


def test_recommend_prefers_quality_then_tokens_then_id():
    result = run_loop(include_cost=False)
    first, second = result.candidates[0], result.candidates[1]
    richer = _gate(second.change_id, quality_after=0.9, tokens_after=95)
    cheaper = _gate(first.change_id, quality_after=0.6, tokens_after=10)
    assert recommend((first, second), (cheaper, richer)).change_id == second.change_id
    tied_a = _gate(first.change_id, quality_after=0.8, tokens_after=50)
    tied_b = _gate(second.change_id, quality_after=0.8, tokens_after=20)
    assert recommend((first, second), (tied_a, tied_b)).change_id == second.change_id
    even_a = _gate(first.change_id, quality_after=0.8, tokens_after=40, latency_after=10)
    even_b = _gate(second.change_id, quality_after=0.8, tokens_after=40, latency_after=10)
    expected = min(first.change_id, second.change_id)
    assert recommend((first, second), (even_a, even_b)).change_id == expected


def test_promote_refuses_a_failed_gate():
    result = run_loop()
    bad = next(c for c in result.candidates if c.change_id == CONFIG_SMALL_MODEL)
    bad_gate = next(g for g in result.gates if g.change_id == CONFIG_SMALL_MODEL)
    good = next(c for c in result.candidates if c.change_id == COMBINED_QUALITY)
    with pytest.raises(ValueError, match="refusing to promote"):
        promote(result.baseline, bad, bad_gate)
    with pytest.raises(ValueError, match="does not match"):
        promote(result.baseline, good, bad_gate)


def test_repeat_promotes_quality_then_cost_then_stops():
    first = run_loop(apply=True)
    assert first.promoted
    assert first.recommendation_id == COMBINED_QUALITY
    assert first.next_baseline.version == "v2"
    assert first.next_baseline.metric_averages["answer_correctness"] == 1.0
    assert COMBINED_QUALITY in first.next_baseline.applied_change_ids
    fresh_v2 = establish_baseline("v2")
    assert first.next_baseline.mean_tokens == fresh_v2.mean_tokens
    assert first.next_baseline.metric_averages == fresh_v2.metric_averages

    second = run_loop(baseline=first.next_baseline, apply=True)
    assert second.findings.clusters == ()
    assert all(c.change_id != COMBINED_QUALITY for c in second.candidates)
    assert second.recommendation_id == CONFIG_TOKEN_CAP
    assert second.promoted
    assert second.next_baseline.version == "v2"
    assert second.next_baseline.mean_tokens < first.next_baseline.mean_tokens
    assert second.next_baseline.metric_averages == first.next_baseline.metric_averages

    third = run_loop(baseline=second.next_baseline, apply=True)
    assert third.recommendation_id is None
    assert third.promoted is False
    assert third.next_baseline is third.baseline
    assert all(c.change_id != CONFIG_TOKEN_CAP for c in third.candidates)
    assert any(g.change_id == CONFIG_SMALL_MODEL and not g.passed for g in third.gates)


def test_report_covers_the_four_steps_and_the_hold():
    result = run_loop()
    report = build_report(result)
    for heading in ("## Establish", "## Identify", "## Optimize", "## Validate"):
        assert heading in report
    assert COMBINED_QUALITY in report
    assert "get_today" in report
    assert "Held." in report
    assert CONFIG_SMALL_MODEL in report
    assert render_summary(run_loop()) == render_summary(result)


def test_bad_arguments_are_rejected():
    with pytest.raises(ValueError):
        run_loop(regression_threshold=-0.01)
    with pytest.raises(ValueError):
        run_loop(min_improvement=-0.1)
    with pytest.raises(ValueError):
        run_loop(min_frequency=0)
    with pytest.raises(ValueError):
        establish_baseline(version="v3")


def test_docs_and_readme_cite_the_source():
    readme = (ROOT / "README.md").read_text()
    page = (ROOT / "docs" / "continuous-improvement.md").read_text()
    assert SOURCE in readme
    assert SOURCE in page
    assert "[verify]" in page
    assert "```mermaid" in page
    assert "docs/continuous-improvement.md" in readme
    assert "## Continuous improvement" in readme

