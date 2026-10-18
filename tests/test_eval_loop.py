"""Tests for the eval runner, failure analysis and improvement loop."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from evaluation import analyze, improve
from evaluation.dataset import get_cases
from evaluation.run_eval import run_all, run_case, summarize
from refund_agent.agent import RefundAgent


def test_run_all_covers_dataset():
    results = run_all(version="v2")
    assert len(results) == len(get_cases()) == 10


def test_run_case_scores_all_metrics():
    case = get_cases()[0]
    result = run_case(case, RefundAgent(version="v2"))
    assert set(result.metric_results) == {
        "task_completion", "step_efficiency", "plan_adherence",
        "plan_quality", "tool_correctness", "argument_correctness",
        "answer_correctness"}
    assert result.case_id == case.case_id


def test_v2_passes_everything():
    for r in run_all(version="v2"):
        assert r.passed_all, (r.case_id,
                              [n for n, m in r.metric_results.items()
                               if not m.passed])


def test_v1_has_known_failures():
    by_id = {r.case_id: r for r in run_all(version="v1")}
    assert not by_id["invalid-user"].passed_all
    assert not by_id["fraud-never-purchased"].passed_all


def test_summarize_averages():
    summary = summarize(run_all(version="v2"))
    assert summary["task_completion"]["average"] == 1.0
    assert summary["task_completion"]["n"] == 10


def test_failures_by_metric_finds_step_efficiency():
    failed = analyze.failures_by_metric(run_all(version="v1"))
    assert "step_efficiency" in failed
    case_ids = [c for c, _ in failed["step_efficiency"]]
    assert "fraud-never-purchased" in case_ids


def test_failures_by_case_lists_metrics():
    by_case = analyze.failures_by_case(run_all(version="v1"))
    assert "answer_correctness" in by_case["invalid-user"]


def test_detect_patterns_finds_both_v1_flaws():
    patterns = analyze.detect_patterns(run_all(version="v1"))
    ids = {p["id"] for p in patterns}
    assert ids == {"unnecessary-date-lookup", "unknown-user-messaging"}


def test_detect_patterns_clean_for_v2():
    assert analyze.detect_patterns(run_all(version="v2")) == []


def test_patterns_carry_evidence_and_fix():
    patterns = analyze.detect_patterns(run_all(version="v1"))
    for p in patterns:
        assert p["evidence"] and p["suggested_fix"]


def test_top_failing_metrics_ordered():
    worst = analyze.top_failing_metrics(run_all(version="v1"))
    assert worst[0][0] == "step_efficiency"  # 2 failures, most of any metric
    assert worst[0][1] == 2


def test_v2_beats_v1_on_targeted_metrics():
    cmp = improve.compare_versions()
    improved = improve.improved_metrics(cmp)
    assert "step_efficiency" in improved
    assert "answer_correctness" in improved


def test_no_regressions_v1_to_v2():
    cmp = improve.compare_versions()
    assert improve.regressed_metrics(cmp) == []


def test_comparison_reports_deltas():
    cmp = improve.compare_versions()
    c = cmp["comparison"]["step_efficiency"]
    assert c["v2_average"] > c["v1_average"]
    assert c["delta"] == c["v2_average"] - c["v1_average"]


def test_improve_returns_both_result_sets():
    cmp = improve.compare_versions()
    assert len(cmp["v1_results"]) == 10
    assert len(cmp["v2_results"]) == 10
