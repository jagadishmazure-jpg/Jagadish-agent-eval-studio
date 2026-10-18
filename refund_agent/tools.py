"""The four tools the refund agent may call.

Design notes (these matter for the evaluation):
- Tools are deliberately narrow: each does one thing and returns a small,
  structured result. The agent decides which to call and in what order.
- ``process_return_request`` issues a *ticket* for a human team to action.
  It never moves money. Payouts stay human-in-the-loop by design; an agent
  that could refund unilaterally would be a fraud and safety risk.
- ``get_today`` takes no arguments. An earlier version required a username,
  which the evaluation flagged as an unnecessary argument (see
  knowledge/04-improvement-loop.md).
"""

import hashlib

from . import data


def get_order_details(username):
    """Look up a customer's purchase history.

    Returns {"status": "ok", "orders": [...]} or
    {"status": "user_not_found"}.
    """
    user = data.get_user(username)
    if user is None:
        return {"status": "user_not_found", "username": username}
    return {"status": "ok", "username": username,
            "display_name": user["display_name"],
            "orders": user["orders"]}


def check_refund_eligibility(username, items):
    """Check each requested item against the refund policy.

    Returns {"status": "ok", "results": [{"item": name, "eligible": bool,
    "reason": str}, ...]} or {"status": "user_not_found"}.
    An item the customer never purchased is ineligible ("not_purchased").
    An item older than the refund window is ineligible ("expired").
    """
    user = data.get_user(username)
    if user is None:
        return {"status": "user_not_found", "username": username}
    results = []
    for item_name in items:
        _, item = data.find_item(user, item_name)
        if item is None:
            results.append({"item": item_name, "eligible": False,
                            "reason": "not_purchased"})
            continue
        age = data.days_between(item["purchased"], data.TODAY)
        if age > data.REFUND_WINDOW_DAYS:
            results.append({
                "item": item_name, "eligible": False, "reason": "expired",
                "detail": "purchased %d days ago; window is %d days"
                          % (age, data.REFUND_WINDOW_DAYS)})
        else:
            results.append({"item": item_name, "eligible": True,
                            "reason": "within_window"})
    return {"status": "ok", "username": username, "results": results}


def calculate_refund(username, items):
    """Total refund value for the *eligible* items in ``items``.

    Items never purchased contribute 0.00 and are listed as skipped so the
    caller can see what was ignored and why.
    """
    user = data.get_user(username)
    if user is None:
        return {"status": "user_not_found", "username": username}
    total = 0.0
    lines = []
    skipped = []
    for item_name in items:
        _, item = data.find_item(user, item_name)
        if item is None:
            skipped.append({"item": item_name, "reason": "not_purchased"})
            continue
        age = data.days_between(item["purchased"], data.TODAY)
        if age > data.REFUND_WINDOW_DAYS:
            skipped.append({"item": item_name, "reason": "expired"})
            continue
        total += item["price"]
        lines.append({"item": item_name, "price": item["price"]})
    return {"status": "ok", "username": username,
            "total": round(total, 2), "lines": lines, "skipped": skipped}


def process_return_request(username, items, amount, queue=None):
    """Issue a refund ticket for a human team to action.

    This is intentionally NOT a payout: it files a ticket in the review
    queue (refund_agent/review.py), and only a named human reviewer can
    authorise the payout there. The agent has no approve call.

    The tool does not trust its arguments: it re-checks the customer and
    every item against the policy and recomputes the amount, so a ticket
    can never carry an unknown user, an ineligible item or a made-up total
    (earlier, ``process_return_request("u-1003", ["running sneakers"],
    9999)`` returned "ticket_issued"). A refused request returns
    ``{"status": "rejected", "reason": ...}``.
    """
    from .review import DEFAULT_QUEUE
    queue = DEFAULT_QUEUE if queue is None else queue
    items = list(items or [])
    if not items:
        return {"status": "rejected", "reason": "no_items"}
    user = data.get_user(username)
    if user is None:
        return {"status": "rejected", "reason": "user_not_found",
                "username": username}
    elig = check_refund_eligibility(username, items)
    ineligible = [r["item"] for r in elig["results"] if not r["eligible"]]
    if ineligible:
        return {"status": "rejected", "reason": "ineligible_items",
                "items": ineligible}
    expected = calculate_refund(username, items)["total"]
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) \
            or round(float(amount), 2) != expected:
        return {"status": "rejected", "reason": "amount_mismatch",
                "expected": expected, "got": amount}
    # Deterministic id (same request -> same ticket), over a SHA-256 of the
    # user, the sorted items and the amount.
    digest = hashlib.sha256(("%s|%s|%.2f" % (
        username, ",".join(sorted(items)), expected)).encode()).hexdigest()
    ticket_id = "tkt-%s-%s" % (username.replace("u-", ""), digest[:10])
    ticket = queue.submit(ticket_id, username, items, expected)
    return {"status": "ticket_issued", "ticket_id": ticket["ticket_id"],
            "username": username, "items": list(ticket["items"]),
            "amount": ticket["amount"],
            "next_step": "human_review_before_payout"}


def get_today():
    """Return today's date (ISO). Takes no arguments."""
    return {"status": "ok", "today": data.TODAY}
