"""Unit tests for the seven metrics: pass, fail and edge cases."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from helpers import make_response, make_case
from evaluation import metrics


# -- task_completion ----------------------------------------------------
def test_task_completion_pass():
    r = make_response("A refund ticket for $45.00 has been issued.")
    c = make_case(expected_conclusion=("refund ticket", "45.00"))
    m = metrics.task_completion(r, c)
    assert m.passed and m.score == 1.0


def test_task_completion_fail_missing_phrase():
    r = make_response("A refund ticket has been issued.")
    c = make_case(expected_conclusion=("refund ticket", "45.00"))
    m = metrics.task_completion(r, c)
    assert not m.passed and m.score == 0.5
    assert "45.00" in m.reason


def test_task_completion_case_insensitive():
    r = make_response("NO REFUND WILL BE ISSUED.")
    c = make_case(expected_conclusion=("no refund will be issued",))
    assert metrics.task_completion(r, c).passed


# -- step_efficiency ------------------------------------------------------
def _refund_tools():
    return [("get_order_details", {"username": "u-1001"}),
            ("check_refund_eligibility", {"username": "u-1001"}),
            ("calculate_refund", {"username": "u-1001"}),
            ("process_return_request", {"username": "u-1001"})]


def test_step_efficiency_all_necessary():
    r = make_response("done", _refund_tools())
    c = make_case(expected_tools=("get_order_details",
                                  "check_refund_eligibility",
                                  "calculate_refund",
                                  "process_return_request"))
    m = metrics.step_efficiency(r, c)
    assert m.passed and m.score == 1.0


def test_step_efficiency_flags_extra_call():
    tools = _refund_tools() + [("get_today", {})]
    r = make_response("done", tools)
    c = make_case(expected_tools=("get_order_details",
                                  "check_refund_eligibility",
                                  "calculate_refund",
                                  "process_return_request"))
    m = metrics.step_efficiency(r, c, threshold=0.9)
    assert not m.passed
    assert m.score == 0.8
    assert "get_today" in m.reason


def test_step_efficiency_no_calls_none_expected():
    r = make_response("please share your username")
    c = make_case(expected_tools=())
    m = metrics.step_efficiency(r, c)
    assert m.passed and m.score == 1.0


def test_step_efficiency_no_calls_but_expected():
    r = make_response("please share your username")
    c = make_case(expected_tools=("get_order_details",))
    m = metrics.step_efficiency(r, c)
    assert not m.passed and m.score == 0.0


# -- plan_adherence -------------------------------------------------------
def test_plan_adherence_in_order():
    r = make_response("done", _refund_tools())
    assert metrics.plan_adherence(r).passed


def test_plan_adherence_allows_skips():
    r = make_response("done", [("get_order_details", {}),
                               ("process_return_request", {})])
    assert metrics.plan_adherence(r).passed


def test_plan_adherence_fails_out_of_order():
    r = make_response("done", [("calculate_refund", {}),
                               ("check_refund_eligibility", {})])
    m = metrics.plan_adherence(r)
    assert not m.passed
    assert "violation" in m.reason


def test_plan_adherence_single_tool_trivial():
    r = make_response("done", [("get_order_details", {})])
    assert metrics.plan_adherence(r).score == 1.0


# -- plan_quality ---------------------------------------------------------
def test_plan_quality_effective_plan():
    r = make_response("ticket tkt-1 for $45.00", _refund_tools())
    c = make_case(expected_tools=("get_order_details",))
    assert metrics.plan_quality(r, c).passed


def test_plan_quality_ticket_without_calculation():
    r = make_response("ticket issued",
                      [("get_order_details", {}),
                       ("process_return_request", {})])
    c = make_case(expected_tools=("get_order_details",))
    m = metrics.plan_quality(r, c)
    assert not m.passed
    assert "without calculating" in m.reason


def test_plan_quality_calc_without_eligibility():
    r = make_response("total is $45",
                      [("get_order_details", {}),
                       ("calculate_refund", {})])
    c = make_case(expected_tools=("get_order_details",))
    m = metrics.plan_quality(r, c)
    assert not m.passed
    assert "eligibility" in m.reason


def test_plan_quality_no_tools_when_needed():
    r = make_response("hmm")
    c = make_case(expected_tools=("get_order_details",))
    assert not metrics.plan_quality(r, c).passed


# -- tool_correctness -----------------------------------------------------
def test_tool_correctness_exact_match():
    r = make_response("done", _refund_tools())
    c = make_case(expected_tools=("get_order_details",
                                  "check_refund_eligibility",
                                  "calculate_refund",
                                  "process_return_request"))
    m = metrics.tool_correctness(r, c)
    assert m.passed and m.score == 1.0


def test_tool_correctness_partial_f1():
    r = make_response("done", [("get_order_details", {}),
                               ("get_today", {})])
    c = make_case(expected_tools=("get_order_details",
                                  "check_refund_eligibility"))
    m = metrics.tool_correctness(r, c)
    # precision 1/2, recall 1/2 -> F1 0.5
    assert m.score == 0.5 and not m.passed
    assert "missing" in m.reason and "extra" in m.reason


def test_tool_correctness_empty_both():
    r = make_response("ask username")
    c = make_case(expected_tools=())
    assert metrics.tool_correctness(r, c).score == 1.0


def test_tool_correctness_nothing_called():
    r = make_response("ask username")
    c = make_case(expected_tools=("get_order_details",))
    assert metrics.tool_correctness(r, c).score == 0.0


# -- argument_correctness -------------------------------------------------
def test_argument_correctness_match():
    r = make_response("done", [("calculate_refund",
                                {"username": "u-1001",
                                 "items": ["trail backpack"]})])
    c = make_case(expected_tools=("calculate_refund",),
                  expected_arguments={"calculate_refund":
                                      {"username": "u-1001",
                                       "items": ["trail backpack"]}})
    m = metrics.argument_correctness(r, c)
    assert m.passed and m.score == 1.0


def test_argument_correctness_mismatch():
    r = make_response("done", [("calculate_refund",
                                {"username": "u-1001",
                                 "items": ["trail backpack"]})])
    c = make_case(expected_tools=("calculate_refund",),
                  expected_arguments={"calculate_refund":
                                      {"username": "u-1001",
                                       "items": ["insulated bottle"]}})
    m = metrics.argument_correctness(r, c)
    assert not m.passed and m.score == 0.5
    assert "insulated bottle" in m.reason


def test_argument_correctness_tool_not_called():
    r = make_response("done")
    c = make_case(expected_tools=(),
                  expected_arguments={"calculate_refund": {"username": "u-1"}})
    m = metrics.argument_correctness(r, c)
    assert not m.passed and m.score == 0.0


def test_argument_correctness_no_expectations():
    r = make_response("done", [("get_today", {})])
    c = make_case(expected_tools=("get_today",))
    assert metrics.argument_correctness(r, c).score == 1.0


# -- answer_correctness ---------------------------------------------------
def test_answer_correctness_paraphrase_ok():
    r = make_response("Your refund ticket for $45.00 is on its way.")
    c = make_case(expected_conclusion=("refund ticket", "45.00"))
    assert metrics.answer_correctness(r, c).passed


def test_answer_correctness_wrong_conclusion():
    r = make_response("No refund will be issued.")
    c = make_case(expected_conclusion=("refund ticket", "45.00"))
    m = metrics.answer_correctness(r, c)
    assert not m.passed and m.score == 0.0


# -- registry -------------------------------------------------------------
def test_all_seven_metrics_registered():
    assert set(metrics.ALL_METRICS) == {
        "task_completion", "step_efficiency", "plan_adherence",
        "plan_quality", "tool_correctness", "argument_correctness",
        "answer_correctness"}


def test_metric_result_shape():
    r = make_response("A refund ticket for $45.00 has been issued.")
    c = make_case(expected_conclusion=("refund ticket",))
    m = metrics.task_completion(r, c, threshold=0.9)
    assert m.metric == "task_completion"
    assert isinstance(m.score, float) and isinstance(m.passed, bool)
    assert isinstance(m.reason, str) and m.reason


def test_custom_threshold_respected():
    r = make_response("A refund ticket has been issued.")
    c = make_case(expected_conclusion=("refund ticket", "45.00"))
    assert not metrics.task_completion(r, c, threshold=0.8).passed
    assert metrics.task_completion(r, c, threshold=0.5).passed
