"""Money never moves without a named human: file a ticket, then try to approve it.

Run: python demos/demo_human_review.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from refund_agent import tools  # noqa: E402
from refund_agent.review import ReviewError, ReviewQueue  # noqa: E402


def attempt(label, fn):
    try:
        print("%-40s %s" % (label, fn()))
    except ReviewError as exc:
        print("%-40s blocked: %s" % (label, exc))


def main():
    q = ReviewQueue()
    print("%-40s %s" % ("inflated amount ($9,999)",
                        tools.process_return_request("u-1001", ["trail backpack"], 9999, queue=q)["reason"]))
    print("%-40s %s" % ("item never purchased",
                        tools.process_return_request("u-1003", ["running sneakers"], 0, queue=q)["reason"]))
    t = tools.process_return_request("u-1001", ["trail backpack"], 45.0, queue=q)
    print("%-40s %s, next step: %s" % ("valid request", t["status"], t["next_step"]))
    tid = t["ticket_id"]
    attempt("agent approves its own ticket", lambda: q.approve(tid, "refund-agent"))
    attempt("customer approves own refund", lambda: q.approve(tid, "u-1001"))
    attempt("no reviewer named", lambda: q.approve(tid, ""))
    attempt("named human approves", lambda: q.approve(tid, "finance-reviewer-1")["status"])
    attempt("same approval replayed", lambda: q.approve(tid, "finance-reviewer-2"))


if __name__ == "__main__":
    main()
