"""Human review queue: the only place a refund can be authorised.

The refund agent can file tickets (tools.process_return_request) but has no
way to approve one. A payout is authorised only by ``approve`` with a named
human reviewer who is neither the agent nor the customer, and each ticket
can be decided exactly once, so an approval cannot be replayed.

Nothing here moves real money: ``approve`` returns a payout authorisation
record for a finance system to act on.
"""

AGENT_IDENTITIES = frozenset({"refund-agent", "agent", "system", "auto"})


class ReviewError(Exception):
    """A review action that must not happen (self-approval, replay, ...)."""


class ReviewQueue:
    def __init__(self):
        self._tickets = {}

    def submit(self, ticket_id, username, items, amount):
        """File a ticket. Re-submitting the same request returns the
        existing ticket instead of creating a second one."""
        existing = self._tickets.get(ticket_id)
        if existing is not None:
            return dict(existing)
        ticket = {"ticket_id": ticket_id, "username": username,
                  "items": tuple(items), "amount": round(float(amount), 2),
                  "status": "pending_review", "reviewer": None}
        self._tickets[ticket_id] = ticket
        return dict(ticket)

    def get(self, ticket_id):
        ticket = self._tickets.get(ticket_id)
        return dict(ticket) if ticket else None

    def _decide(self, ticket_id, reviewer, status):
        ticket = self._tickets.get(ticket_id)
        if ticket is None:
            raise ReviewError("unknown ticket %r" % (ticket_id,))
        if ticket["status"] != "pending_review":
            raise ReviewError("ticket %s was already %s"
                              % (ticket_id, ticket["status"]))
        name = (reviewer or "").strip()
        if not name:
            raise ReviewError("a named human reviewer is required")
        if name.lower() in AGENT_IDENTITIES:
            raise ReviewError("the agent cannot review its own tickets")
        if name.lower() == ticket["username"].lower():
            raise ReviewError("a customer cannot approve their own refund")
        ticket["status"] = status
        ticket["reviewer"] = name
        return dict(ticket)

    def approve(self, ticket_id, reviewer):
        """Authorise the payout for a pending ticket (single use)."""
        ticket = self._decide(ticket_id, reviewer, "approved")
        return {"status": "payout_authorised", "ticket_id": ticket_id,
                "amount": ticket["amount"], "reviewer": ticket["reviewer"]}

    def reject(self, ticket_id, reviewer):
        self._decide(ticket_id, reviewer, "rejected")
        return {"status": "rejected", "ticket_id": ticket_id}


DEFAULT_QUEUE = ReviewQueue()
