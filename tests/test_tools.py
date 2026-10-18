"""Tests for the four refund tools."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from refund_agent import tools, data


def test_get_order_details_known_user():
    r = tools.get_order_details("u-1001")
    assert r["status"] == "ok"
    assert r["username"] == "u-1001"
    assert len(r["orders"]) == 1


def test_get_order_details_unknown_user():
    r = tools.get_order_details("u-9999")
    assert r["status"] == "user_not_found"


def test_eligibility_within_window():
    r = tools.check_refund_eligibility("u-1001", ["trail backpack"])
    assert r["status"] == "ok"
    assert r["results"][0]["eligible"] is True
    assert r["results"][0]["reason"] == "within_window"


def test_eligibility_expired():
    r = tools.check_refund_eligibility("u-1002", ["camp lantern"])
    assert r["results"][0]["eligible"] is False
    assert r["results"][0]["reason"] == "expired"


def test_eligibility_never_purchased():
    r = tools.check_refund_eligibility("u-1003", ["running sneakers"])
    assert r["results"][0]["eligible"] is False
    assert r["results"][0]["reason"] == "not_purchased"


def test_eligibility_unknown_user():
    r = tools.check_refund_eligibility("u-9999", ["trail backpack"])
    assert r["status"] == "user_not_found"


def test_calculate_refund_sums_eligible_only():
    r = tools.calculate_refund("u-1002", ["camp lantern", "trail socks"])
    assert r["status"] == "ok"
    assert r["total"] == 15.00  # lantern expired -> skipped
    assert r["skipped"] == [{"item": "camp lantern", "reason": "expired"}]


def test_calculate_refund_skips_unpurchased():
    r = tools.calculate_refund("u-1003", ["running sneakers"])
    assert r["total"] == 0.00
    assert r["skipped"][0]["reason"] == "not_purchased"


def test_calculate_refund_unknown_user():
    assert tools.calculate_refund("u-9999", ["x"])["status"] == "user_not_found"


def test_process_return_request_is_ticket_not_payout():
    r = tools.process_return_request("u-1001", ["trail backpack"], 45.00)
    assert r["status"] == "ticket_issued"
    assert r["ticket_id"].startswith("tkt-1001-")
    assert r["amount"] == 45.00
    # The critical safety property: a human reviews before money moves.
    assert r["next_step"] == "human_review_before_payout"


def test_process_return_request_deterministic_ticket():
    a = tools.process_return_request("u-1001", ["trail backpack"], 45.00)
    b = tools.process_return_request("u-1001", ["trail backpack"], 45.00)
    assert a["ticket_id"] == b["ticket_id"]


def test_get_today_takes_no_arguments():
    r = tools.get_today()
    assert r == {"status": "ok", "today": data.TODAY}


def test_refund_window_is_30_days():
    assert data.REFUND_WINDOW_DAYS == 30
