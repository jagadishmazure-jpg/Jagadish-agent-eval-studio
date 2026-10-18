"""Deterministic rule-based refund agent (offline).

This is deliberately NOT an LLM: every input maps to a fixed trace, which
makes the evaluation metrics testable without API keys or nondeterminism.
The agent mirrors the structure of a tool-calling LLM agent:

- it parses the request, picks tools, and records a full trace
  (tool name, arguments, reasoning for each step);
- it returns a structured response: ``answer`` (str) plus
  ``conversation_continue`` (bool) so a caller knows whether to keep
  talking to the customer;
- money never moves here: eligible refunds become tickets for human
  review (see tools.process_return_request).

Two versions exist so the improvement loop can be measured:
- ``v1`` (baseline) has two known flaws, each caught by a metric:
  1. unknown username -> says "no purchase history found" instead of
     asking for a valid username (caught by answer_correctness);
  2. calls get_today even when the requested item was never purchased,
     making the date check pointless (caught by step_efficiency).
- ``v2`` fixes both. evaluation/improve.py measures the delta.

Where an LLM would plug in (for a production port), the seams are marked
with "LLM SEAM".
"""

import re

from . import data, tools

# LLM SEAM: an LLM agent would extract these with function calling; here a
# small parser keeps behaviour deterministic.
_USERNAME_RE = re.compile(r"\bu-(\d{1,6})\b", re.IGNORECASE)
# Ordered most-specific-first: "socks" is a substring of both sock items.
_ITEM_HINTS = [
    "trail backpack", "backpack",
    "insulated bottle", "bottle",
    "camp lantern", "lantern",
    "hiking socks",
    "trail socks",
    "socks",
    "running sneakers", "sneakers",  # never sold: fraud probe
]

_CANONICAL = {
    "backpack": "trail backpack",
    "bottle": "insulated bottle",
    "lantern": "camp lantern",
    "socks": "hiking socks",
    "sneakers": "running sneakers",
    "running sneakers": "running sneakers",
    "trail backpack": "trail backpack",
    "insulated bottle": "insulated bottle",
    "camp lantern": "camp lantern",
    "hiking socks": "hiking socks",
    "trail socks": "trail socks",
}


class AgentResponse:
    """Structured agent output."""

    def __init__(self, answer, conversation_continue, trace):
        self.answer = answer
        self.conversation_continue = conversation_continue
        self.trace = trace  # list of step dicts

    def tool_names(self):
        return [s["tool"] for s in self.trace]


class RefundAgent:
    """Rule-based refund agent. version is "v1" or "v2"."""

    POLICY_HINT = (
        "Refunds are only issued within %d days of purchase, only for items "
        "the customer actually bought, and only for the items requested."
        % data.REFUND_WINDOW_DAYS
    )

    def __init__(self, version="v2"):
        if version not in ("v1", "v2"):
            raise ValueError("version must be 'v1' or 'v2'")
        self.version = version

    # -- parsing (LLM SEAM: entity extraction) ---------------------------
    def _parse_username(self, text):
        m = _USERNAME_RE.search(text or "")
        return "u-" + m.group(1) if m else None

    def _parse_items(self, text):
        lowered = (text or "").lower()
        found = []
        for hint in _ITEM_HINTS:
            if hint not in lowered:
                continue
            canon = _CANONICAL[hint]
            # The bare word "socks" is a substring of both sock items; only
            # count it when no specific sock was already recognised.
            if hint == "socks" and any(
                    s in found for s in ("hiking socks", "trail socks")):
                continue
            if canon not in found:
                found.append(canon)
        return found

    def _wants_refund(self, text):
        lowered = (text or "").lower()
        return any(w in lowered for w in
                   ("refund", "return", "money back"))

    # -- main entry point -------------------------------------------------
    def handle(self, user_input):
        """Run the agent; return an AgentResponse with a full trace."""
        trace = []

        def step(tool, arguments, reasoning, result_summary):
            trace.append({"tool": tool, "arguments": arguments,
                          "reasoning": reasoning, "result": result_summary})

        username = self._parse_username(user_input)
        items = self._parse_items(user_input)

        # 1. Identify the customer first. No username -> ask, keep talking.
        if username is None:
            return AgentResponse(
                "I can help with a refund. Could you share your username "
                "(for example u-1001) so I can look up your order?",
                True, trace)

        # 2. Pull the purchase history.
        details = tools.get_order_details(username)
        step("get_order_details", {"username": username},
             "Need the customer's purchase history before any refund decision.",
             details["status"])
        if details["status"] == "user_not_found":
            if self.version == "v1":
                # KNOWN FLAW (v1): talks about missing history instead of the
                # real problem -- the username itself is not valid.
                return AgentResponse(
                    "No purchase history found for %s, so no refund will be "
                    "issued." % username, False, trace)
            # v2: name the real problem and ask for a valid username.
            return AgentResponse(
                "The username %s is not valid. Please check it and send a "
                "valid username so I can look up your order." % username,
                True, trace)

        # 3. Non-refund questions: answer from history, issue nothing.
        if not self._wants_refund(user_input):
            n_orders = len(details["orders"])
            return AgentResponse(
                "I found %d order(s) on your account. Let me know if you "
                "would like a refund for any item." % n_orders, True, trace)

        if not items:
            return AgentResponse(
                "Which item would you like to refund? I can see your order "
                "history once you tell me.", True, trace)

        # 4. Eligibility per item.
        elig = tools.check_refund_eligibility(username, items)
        step("check_refund_eligibility",
             {"username": username, "items": items},
             "Policy check per item: purchased? within %d days?"
             % data.REFUND_WINDOW_DAYS,
             "; ".join("%s=%s" % (r["item"], r["reason"])
                       for r in elig["results"]))
        eligible = [r["item"] for r in elig["results"] if r["eligible"]]
        denied = [(r["item"], r["reason"]) for r in elig["results"]
                  if not r["eligible"]]

        # v1 KNOWN FLAW: fetches today's date even when nothing was ever
        # purchased, so the date lookup cannot change the outcome.
        # v2 skips it: the eligibility result already encodes the window.
        if self.version == "v1":
            today = tools.get_today()
            step("get_today", {},
                 "Confirm today's date for the refund window.",
                 today["today"])
        # (v2 deliberately makes no get_today call here.)

        # 5. Nothing eligible -> deny with reasons, conversation over.
        if not eligible:
            reasons = "; ".join(
                "%s: %s" % (name,
                            "was never purchased" if why == "not_purchased"
                            else "outside the %d-day refund window"
                            % data.REFUND_WINDOW_DAYS)
                for name, why in denied)
            return AgentResponse(
                "No refund will be issued. %s." % reasons, False, trace)

        # 6. Calculate and ticket the eligible items only.
        calc = tools.calculate_refund(username, eligible)
        step("calculate_refund", {"username": username, "items": eligible},
             "Total only the eligible items; never refund unpurchased goods.",
             "total=%.2f" % calc["total"])
        ticket = tools.process_return_request(username, eligible,
                                              calc["total"])
        step("process_return_request",
             {"username": username, "items": eligible,
              "amount": calc["total"]},
             "Issue a ticket for human review; the agent never moves money.",
             ticket["ticket_id"])

        note = ""
        if denied:
            note = " Not eligible: %s." % "; ".join(
                "%s (%s)" % (name,
                             "never purchased" if why == "not_purchased"
                             else "outside refund window")
                for name, why in denied)
        return AgentResponse(
            "A refund ticket %s for $%.2f has been issued and will be "
            "reviewed by our team before payout.%s"
            % (ticket["ticket_id"], calc["total"], note),
            False, trace)
