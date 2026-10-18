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


def process_return_request(username, items, amount):
    """Issue a refund ticket for a human team to action.

    This is intentionally NOT a payout: it creates a ticket that a human
    reviewer approves before any money moves. Returns the ticket record.
    """
    import hashlib
    digest = hashlib.md5(
        ("%s|%s" % (username, ",".join(items))).encode()).hexdigest()
    ticket_id = "tkt-%s-%s" % (username.replace("u-", ""), digest[:5])
    return {"status": "ticket_issued", "ticket_id": ticket_id,
            "username": username, "items": list(items),
            "amount": round(float(amount), 2),
            "next_step": "human_review_before_payout"}


def get_today():
    """Return today's date (ISO). Takes no arguments."""
    return {"status": "ok", "today": data.TODAY}
