"""Tests for the deterministic refund agent (both versions)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from refund_agent.agent import RefundAgent


def v1():
    return RefundAgent(version="v1")


def v2():
    return RefundAgent(version="v2")


def test_valid_refund_issues_ticket_v2():
    r = v2().handle("I am u-1001, I would like a refund for the trail backpack.")
    assert "refund ticket" in r.answer.lower()
    assert "45.00" in r.answer
    assert r.conversation_continue is False
    assert r.tool_names() == ["get_order_details", "check_refund_eligibility",
                              "calculate_refund", "process_return_request"]


def test_partial_refund_only_requested_item():
    r = v2().handle("I am u-1001, please refund just the insulated bottle.")
    assert "28.00" in r.answer
    assert "45.00" not in r.answer  # backpack must NOT be refunded


def test_expired_window_denied():
    r = v2().handle("I am u-1002, I want a refund for the camp lantern.")
    lowered = r.answer.lower()
    assert "no refund will be issued" in lowered
    assert "outside" in lowered
    assert "process_return_request" not in r.tool_names()


def test_fraud_never_purchased_denied():
    r = v2().handle("I am u-1003, refund the running sneakers.")
    assert "no refund will be issued" in r.answer.lower()
    assert "never purchased" in r.answer.lower()
    assert "process_return_request" not in r.tool_names()


def test_mixed_eligibility_tickets_one_item():
    r = v2().handle("I am u-1002, refund the camp lantern and the trail socks.")
    assert "15.00" in r.answer
    assert "32.50" not in r.answer
    assert "not eligible" in r.answer.lower()


def test_invalid_user_v2_asks_for_valid_username():
    r = v2().handle("I am u-9999, I want a refund for the backpack.")
    lowered = r.answer.lower()
    assert "not valid" in lowered
    assert "valid username" in lowered
    assert r.conversation_continue is True


def test_invalid_user_v1_known_flaw():
    # v1 talks about missing history instead of the invalid username.
    r = v1().handle("I am u-9999, I want a refund for the backpack.")
    assert "no purchase history found" in r.answer.lower()
    assert "not valid" not in r.answer.lower()


def test_missing_username_asks_first():
    r = v2().handle("Hi, I would like a refund for my backpack.")
    assert "username" in r.answer.lower()
    assert r.tool_names() == []
    assert r.conversation_continue is True


def test_info_only_issues_no_ticket():
    r = v2().handle("I am u-1001, what orders do I have?")
    assert "process_return_request" not in r.tool_names()
    assert r.conversation_continue is True


def test_no_item_named_asks_which():
    r = v2().handle("I am u-1001, I want a refund please.")
    assert "which item" in r.answer.lower()
    assert r.conversation_continue is True


def test_trace_captures_tool_arguments_reasoning():
    r = v2().handle("I am u-1001, refund the trail backpack.")
    assert len(r.trace) == 4
    for step in r.trace:
        assert set(step) == {"tool", "arguments", "reasoning", "result"}
    assert r.trace[0]["arguments"] == {"username": "u-1001"}


def test_v1_wastes_get_today_call():
    # v1 calls get_today even when the item was never purchased.
    r = v1().handle("I am u-1003, refund the running sneakers.")
    assert "get_today" in r.tool_names()


def test_v2_skips_unnecessary_get_today():
    r = v2().handle("I am u-1003, refund the running sneakers.")
    assert "get_today" not in r.tool_names()


def test_response_is_structured():
    r = v2().handle("I am u-1001, refund the trail backpack.")
    assert isinstance(r.answer, str) and r.answer
    assert isinstance(r.conversation_continue, bool)
    assert isinstance(r.trace, list)


def test_money_never_moves_without_human():
    r = v2().handle("I am u-1001, refund the trail backpack.")
    ticket_step = [s for s in r.trace
                   if s["tool"] == "process_return_request"][0]
    assert "human" in ticket_step["reasoning"].lower()


def test_item_name_normalisation():
    agent = v2()
    assert agent._parse_items("refund the Backpack please") == ["trail backpack"]
    assert agent._parse_items("refund the trail socks") == ["trail socks"]
    assert agent._parse_items("refund the socks") == ["hiking socks"]


def test_username_parsing_case_insensitive():
    agent = v2()
    assert agent._parse_username("I am U-1001") == "u-1001"
    assert agent._parse_username("no username here") is None
