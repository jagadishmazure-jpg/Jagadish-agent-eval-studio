"""Regression tests for the 2026-10-18 standardization review.

Most tests reproduce a way to game a metric, or to file a bad refund ticket,
that worked on the original code (25 fail on it). A few are guards that the
fixes did not over-tighten (plurals still match, 45 equals 45.0, the agent
still has no approve path).
"""
import pytest

from evaluation import improve, metrics
from evaluation.dataset import get_cases
from refund_agent import tools
from refund_agent.agent import RefundAgent
from refund_agent.review import ReviewError, ReviewQueue
from tests.helpers import make_case, make_response

CASES = {c.case_id: c for c in get_cases()}
FULL = [("get_order_details", {"username": "u-1001"}),
        ("check_refund_eligibility", {"username": "u-1001", "items": ["trail backpack"]}),
        ("calculate_refund", {"username": "u-1001", "items": ["trail backpack"]}),
        ("process_return_request", {"username": "u-1001", "items": ["trail backpack"], "amount": 45.0})]


# ---- answer metrics ------------------------------------------------------------

@pytest.mark.parametrize("answer", [
    "A refund ticket for $145.00 was issued.",            # wrong amount
    "There is no refund ticket for 45.00.",                # negated
    "No refund will be issued. refund ticket 45.00",       # contradiction stuffing
    "",                                                    # empty
])
def test_answer_metrics_cannot_be_gamed_on_a_refund_case(answer):
    case = CASES["valid-full-refund"]
    r = make_response(answer, FULL)
    assert not metrics.answer_correctness(r, case).passed
    assert not metrics.task_completion(r, case).passed


def test_invalid_does_not_match_valid():
    case = CASES["invalid-user"]
    r = make_response("Your username is invalid; username not valid",
                      [("get_order_details", {"username": "u-9999"})], continue_flag=True)
    assert not metrics.answer_correctness(r, case).passed


def test_plural_and_real_answers_still_match():
    case = CASES["info-only-no-ticket"]
    r = make_response("I found 1 order(s) on your account.",
                      [("get_order_details", {"username": "u-1001"})], continue_flag=True)
    assert metrics.answer_correctness(r, case).passed


def test_empty_answer_scores_zero_even_without_expected_phrases():
    assert metrics.answer_correctness(make_response(""), make_case(expected_conclusion=())).score == 0.0


def test_task_completion_checks_conversation_state():
    case = CASES["invalid-user"]
    r = make_response("The username u-9999 is not valid. Send a valid username.",
                      [("get_order_details", {"username": "u-9999"})], continue_flag=False)
    assert metrics.answer_correctness(r, case).passed
    assert not metrics.task_completion(r, case).passed


# ---- trajectory metrics --------------------------------------------------------

def test_repeated_calls_are_not_efficient_or_correct():
    case = CASES["info-only-no-ticket"]
    r = make_response("x", [("get_order_details", {"username": "u-1001"})] * 6)
    assert metrics.step_efficiency(r, case).score == pytest.approx(1 / 6)
    assert not metrics.tool_correctness(r, case).passed


def test_empty_trace_does_not_adhere_to_a_plan_that_needs_tools():
    r = make_response("refund ticket 45.00")
    assert not metrics.plan_adherence(r, CASES["valid-full-refund"]).passed
    assert metrics.plan_adherence(r, CASES["missing-username"]).passed


def test_ticket_when_nothing_is_eligible_fails_plan_quality():
    case = CASES["fraud-never-purchased"]
    trace = [("get_order_details", {"username": "u-1003"}),
             ("check_refund_eligibility", {"username": "u-1003", "items": ["running sneakers"]}),
             ("calculate_refund", {"username": "u-1003", "items": ["running sneakers"]}),
             ("process_return_request", {"username": "u-1003", "items": ["running sneakers"], "amount": 0.0})]
    r = make_response("A refund ticket was raised.", trace)
    assert not metrics.plan_quality(r, case).passed


def test_every_call_is_checked_not_only_the_first():
    case = CASES["valid-full-refund"]
    trace = FULL[:3] + [("calculate_refund", {"username": "u-1001",
                                              "items": ["trail backpack", "camp lantern"]})] + FULL[3:]
    m = metrics.argument_correctness(make_response("x", trace), case)
    assert not m.passed and m.score < 1.0 and "calculate_refund#2.items" in m.reason


def test_unexpected_arguments_fail():
    case = CASES["valid-full-refund"]
    trace = [(n, dict(a, override_policy=True)) for n, a in FULL]
    m = metrics.argument_correctness(make_response("x", trace), case)
    assert not m.passed and "unexpected argument" in m.reason


@pytest.mark.parametrize("got,want", [(True, 1), (0, False), ("45.0", 45.0), (("a",), "a")])
def test_wrong_types_do_not_match(got, want):
    case = make_case(expected_tools=("t",), expected_arguments={"t": {"x": want}})
    assert not metrics.argument_correctness(make_response("x", [("t", {"x": got})]), case).passed


def test_int_and_float_amounts_still_match():
    case = make_case(expected_tools=("t",), expected_arguments={"t": {"x": 45.0}})
    assert metrics.argument_correctness(make_response("x", [("t", {"x": 45})]), case).passed


def test_ticket_amount_is_an_expected_argument_in_refund_cases():
    for cid in ("valid-full-refund", "partial-refund-one-of-two", "mixed-eligibility", "recent-purchase-refund"):
        assert "amount" in CASES[cid].expected_arguments["process_return_request"]


# ---- improvement loop ----------------------------------------------------------

def test_per_case_regressions_are_detected_even_when_averages_improve():
    result = improve.compare_versions()
    assert improve.case_regressions(result) == []          # the real run
    # Make one case worse in v2 while the averages still go up.
    victim = result["v2_results"][0]
    m = victim.metric_results["plan_quality"]
    victim.metric_results["plan_quality"] = metrics.MetricResult(m.metric, 0.97, True, "forced")
    assert improve.regressed_metrics(result) == []          # average check misses it
    assert improve.case_regressions(result) == [(victim.case_id, "plan_quality", 1.0, 0.97)]


# ---- refund ticket tool and human review --------------------------------------------

@pytest.mark.parametrize("args,reason", [
    (("u-1003", ["running sneakers"], 9999), "ineligible_items"),
    (("u-9999", ["trail backpack"], 45.0), "user_not_found"),
    (("u-1001", ["trail backpack"], 1.0), "amount_mismatch"),
    (("u-1001", ["trail backpack"], 4500), "amount_mismatch"),
    (("u-1001", ["trail backpack"], "45.00"), "amount_mismatch"),
    (("u-1001", ["trail backpack"], True), "amount_mismatch"),
    (("u-1002", ["camp lantern"], 32.5), "ineligible_items"),
    (("u-1001", [], 0), "no_items"),
])
def test_ticket_tool_does_not_trust_its_arguments(args, reason):
    r = tools.process_return_request(*args, queue=ReviewQueue())
    assert r["status"] == "rejected" and r["reason"] == reason


def test_ticket_ids_differ_for_different_amounts_and_are_not_md5_prefixes():
    q = ReviewQueue()
    a = tools.process_return_request("u-1001", ["trail backpack"], 45.0, queue=q)
    b = tools.process_return_request("u-1001", ["insulated bottle"], 28.0, queue=q)
    assert a["ticket_id"] != b["ticket_id"]
    assert len(a["ticket_id"].split("-")[-1]) == 10


def test_payout_needs_a_named_human_and_cannot_be_replayed():
    q = ReviewQueue()
    t = tools.process_return_request("u-1001", ["trail backpack"], 45.0, queue=q)
    assert q.get(t["ticket_id"])["status"] == "pending_review"
    for bad in ("", "  ", "refund-agent", "Agent", "u-1001"):
        with pytest.raises(ReviewError):
            q.approve(t["ticket_id"], bad)
    payout = q.approve(t["ticket_id"], "m.alvarez")
    assert payout == {"status": "payout_authorised", "ticket_id": t["ticket_id"],
                      "amount": 45.0, "reviewer": "m.alvarez"}
    with pytest.raises(ReviewError):
        q.approve(t["ticket_id"], "m.alvarez")              # single use
    with pytest.raises(ReviewError):
        q.reject(t["ticket_id"], "someone-else")
    # Re-filing the same request does not reopen or duplicate the ticket.
    again = tools.process_return_request("u-1001", ["trail backpack"], 45.0, queue=q)
    assert again["ticket_id"] == t["ticket_id"] and q.get(t["ticket_id"])["status"] == "approved"


def test_agent_only_files_tickets_and_never_approves():
    import inspect
    import sys

    src = inspect.getsource(sys.modules[RefundAgent.__module__])
    assert ".approve(" not in src and "ReviewQueue" not in src
    r = RefundAgent("v2").handle("I am u-1001, I would like a refund for the trail backpack.")
    assert r.tool_names()[-1] == "process_return_request"
    assert "reviewed by our team before payout" in r.answer


def test_agent_cannot_route_its_ticket_to_its_own_queue():
    case = CASES["valid-full-refund"]
    trace = [(n, dict(a)) for n, a in FULL]
    trace[-1][1]["queue"] = "agent-controlled-queue"
    m = metrics.argument_correctness(make_response("x", trace), case)
    assert not m.passed and "queue: unexpected argument" in m.reason


@pytest.mark.parametrize("amount, extra", [("45.0", {}), (45.0, {"approved": True})])
def test_one_bad_argument_on_the_ticket_tool_fails_the_metric(amount, extra):
    case = CASES["valid-full-refund"]
    trace = FULL[:3] + [("process_return_request",
                         {"username": "u-1001", "items": ["trail backpack"], "amount": amount, **extra})]
    m = metrics.argument_correctness(make_response("x", trace), case)
    assert not m.passed and m.score > 0.8
