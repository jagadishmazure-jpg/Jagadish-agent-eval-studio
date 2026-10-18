"""Tests for the behaviour-case dataset."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from evaluation.dataset import get_cases

KNOWN_TOOLS = {"get_order_details", "check_refund_eligibility",
               "calculate_refund", "process_return_request", "get_today"}


def test_ten_cases():
    assert len(get_cases()) == 10


def test_case_ids_unique():
    ids = [c.case_id for c in get_cases()]
    assert len(ids) == len(set(ids))


def test_expected_tools_are_real():
    for c in get_cases():
        assert set(c.expected_tools) <= KNOWN_TOOLS, c.case_id


def test_expected_conclusions_nonempty():
    for c in get_cases():
        assert len(c.expected_conclusion) >= 1, c.case_id


def test_expected_arguments_reference_expected_tools():
    for c in get_cases():
        for tool in c.expected_arguments:
            assert tool in c.expected_tools, (c.case_id, tool)


def test_continue_flag_set_where_expected():
    by_id = {c.case_id: c for c in get_cases()}
    assert by_id["invalid-user"].expect_continue is True
    assert by_id["missing-username"].expect_continue is True
    assert by_id["valid-full-refund"].expect_continue is False


def test_all_scenarios_covered():
    ids = {c.case_id for c in get_cases()}
    for needed in ("valid-full-refund", "expired-window", "invalid-user",
                   "fraud-never-purchased", "partial-refund-one-of-two",
                   "mixed-eligibility"):
        assert needed in ids
