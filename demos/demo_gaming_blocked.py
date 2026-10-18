"""Ways to game the metrics that scored 1.00 before the 2026-10-18 review.

Run: python demos/demo_gaming_blocked.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from evaluation import metrics  # noqa: E402
from evaluation.dataset import CASES  # noqa: E402
from refund_agent.agent import AgentResponse  # noqa: E402

CASE = {c.case_id: c for c in CASES}["valid-full-refund"]
GOOD = [("get_order_details", {"username": "u-1001"}),
        ("check_refund_eligibility", {"username": "u-1001", "items": ["trail backpack"]}),
        ("calculate_refund", {"username": "u-1001", "items": ["trail backpack"]}),
        ("process_return_request", {"username": "u-1001", "items": ["trail backpack"], "amount": 45.0})]


def resp(answer, calls, cont=False):
    return AgentResponse(answer, cont, [{"tool": n, "arguments": a, "reasoning": "", "result": "ok"}
                                        for n, a in calls])


def row(label, metric, r):
    m = getattr(metrics, metric)(r, CASE)
    print("%-44s %-21s %.2f %s" % (label, metric, m.score, "pass" if m.passed else "FAIL"))


def main():
    row("empty answer, no tools", "answer_correctness", resp("", []))
    row("answer says 'no refund will be issued'", "answer_correctness",
        resp("No refund will be issued. A refund ticket was not created for $45.00.", GOOD))
    row("keyword stuffing with a denial", "task_completion",
        resp("refund ticket $45.00 no refund will be issued", GOOD))
    row("amount passed as the string '45.0'", "argument_correctness",
        resp("x", GOOD[:3] + [("process_return_request",
                               {"username": "u-1001", "items": ["trail backpack"], "amount": "45.0"})]))
    row("extra argument smuggled into a tool call", "argument_correctness",
        resp("x", GOOD[:3] + [("process_return_request",
                               {"username": "u-1001", "items": ["trail backpack"], "amount": 45.0,
                                "approved": True})]))
    row("same tool called 3 times", "tool_correctness", resp("x", GOOD + GOOD[2:3] * 2))
    row("same tool called 3 times", "step_efficiency", resp("x", GOOD + GOOD[2:3] * 2))
    row("honest v2-style trace", "argument_correctness", resp("x", GOOD))


if __name__ == "__main__":
    main()
