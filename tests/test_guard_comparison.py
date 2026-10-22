"""Tests for the expanded dataset and the guarded/unguarded comparison.

The headline assertion is the one the guard layer has to earn: on the
policy bucket, guarded mode must do at least as well as unguarded. The
exact catch counts are pinned too -- if a guard stops catching, or the
dataset changes, this file should fail loudly rather than let the
README numbers quietly rot (they are rendered from the same run).
"""

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from evaluation import compare
from evaluation.dataset import BUCKETS, get_all_cases, get_guard_cases

BUNDLE = compare.run_comparison()


def test_full_dataset_has_30_cases():
    assert len(get_all_cases()) == 30
    assert len(get_guard_cases()) == 20


def test_every_bucket_has_at_least_four_cases():
    counts = Counter(c.bucket for c in get_all_cases())
    assert set(counts) == set(BUCKETS)
    for bucket, n in counts.items():
        assert n >= 4, (bucket, n)


def test_blocked_cases_are_all_in_the_policy_bucket():
    blocked = [c for c in get_all_cases()
               if c.expect_guard == "blocked_input"]
    assert len(blocked) == 6
    assert all(c.bucket == "policy" for c in blocked)
    assert all(c.expected_tools == () for c in blocked)


def test_case_ids_unique_across_the_full_set():
    ids = [c.case_id for c in get_all_cases()]
    assert len(ids) == len(set(ids))


def test_guarded_policy_bucket_at_least_as_good_as_unguarded():
    rates = compare.bucket_rates(BUNDLE)
    assert rates["policy"]["guarded"] >= rates["policy"]["unguarded"]


def test_guarded_overall_at_least_as_good_and_perfect_here():
    rates = compare.success_rates(BUNDLE)
    assert rates["guarded"] >= rates["unguarded"]
    # On this dataset the guards close the whole gap with no false
    # positives on the other buckets; pin it so a future guard change
    # that starts blocking legitimate requests fails here first.
    assert rates["guarded"] == 1.0
    assert compare.bucket_rates(BUNDLE)["policy"]["guarded"] == 1.0


def test_unguarded_fails_the_six_guard_cases():
    by_id = {r.case_id: r for r in BUNDLE["unguarded"]["results"]}
    for case in get_all_cases():
        if case.expect_guard == "blocked_input":
            assert not by_id[case.case_id].passed_all, case.case_id


def test_guard_expectations_all_met():
    expectations = compare.guard_expectations(BUNDLE)
    assert len(expectations) == 6
    assert all(met for _, _, met in expectations)


def test_catch_counts_by_type():
    catches = compare.catch_counts(BUNDLE)
    assert dict(catches["input_blocks"]) == {
        "prompt_injection": 3, "cross_customer": 1, "out_of_scope": 2}
    # The deterministic agent never files a false claim, so the output
    # guard verifies (14 claims) and corrects none on this dataset; its
    # blocking paths are covered in tests/test_guards.py.
    assert catches["claims_verified"] == 14
    assert sum(catches["output_corrections"].values()) == 0


def test_render_markdown_reports_buckets_and_catches():
    text = compare.render_markdown(BUNDLE)
    for bucket in BUCKETS:
        assert bucket in text
    assert "overall" in text and "prompt_injection" in text
