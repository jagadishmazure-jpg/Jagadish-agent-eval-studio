"""Guardrail layer: three deterministic guards around the refund agent.

The guards wrap the *existing* agent without forking it -- the same
RefundAgent code runs with guards on or off (``GuardedRefundAgent``),
which is what makes the guarded-vs-unguarded comparison in compare.py a
measurement rather than a rewrite.

Layers (defence in depth; no single layer is a security boundary):

1. ``InputGuard`` -- screens the request before the agent sees it:
   prompt-injection patterns, cross-customer probes (one request naming
   two accounts, or asking for another customer's data) and clearly
   off-topic requests. A request is only scope-blocked when it matches
   an off-topic pattern *and* contains no in-scope marker, so vague
   in-domain messages ("fix it") still reach the agent, which asks for
   clarification.
2. ``ToolCallGuard`` -- middleware around every tool: identical repeat
   calls (same tool, same arguments) return the cached first result
   instead of executing again, and each tool has a per-request call cap.
3. ``OutputGuard`` -- verifies the final answer against ground truth
   before the user sees it: a claimed refund ticket must exist in the
   review queue, for items the customer actually bought, with the exact
   amount the answer states. A false claim is replaced with a corrected,
   escalated response.

Every guard decision is recorded as a structured dict so the comparison
can report what was caught, by type.

Honest limits: the injection screen is pattern-based and will miss
novel phrasings; the scope check is a deny-list of clear off-topic
intents, not a topic classifier. In a production port these layers sit
in front of (not instead of) authentication, authorisation and a real
policy engine. See knowledge/05-guardrails.md.
"""

import json
import re
from collections import Counter

from refund_agent import data, tools
from refund_agent.agent import AgentResponse, RefundAgent
from refund_agent.review import ReviewQueue

# ---------------------------------------------------------------------------
# Input guard
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS = (
    ("instruction_override", re.compile(
        r"\b(ignore|disregard|forget|override|bypass|skip)\b[^.]{0,60}"
        r"\b(instructions?|rules?|policy|policies|guidelines?|"
        r"restrictions?|checks?|eligibility)\b", re.IGNORECASE)),
    ("role_escalation", re.compile(
        r"\b(you are|act as|pretend to be|roleplay as)\b[^.]{0,30}"
        r"\b(admin|administrator|developer|manager|owner|system)\b",
        re.IGNORECASE)),
    ("prompt_extraction", re.compile(
        r"\b(reveal|show|print|repeat|display|tell me|give me)\b[^.]{0,40}"
        r"\b(system prompt|your prompt|your instructions|"
        r"internal instructions)\b", re.IGNORECASE)),
    ("prompt_extraction", re.compile(r"\bsystem prompt\b", re.IGNORECASE)),
)

_CROSS_CUSTOMER_PATTERNS = (
    re.compile(r"\banother (customer|user|person)\b", re.IGNORECASE),
    re.compile(r"\bsomeone else\b", re.IGNORECASE),
    re.compile(r"\bother (customers?|users?)\b", re.IGNORECASE),
    re.compile(r"\b(their|his|her) (order|orders|account|refund|tickets?)\b",
               re.IGNORECASE),
)

_OFF_TOPIC_PATTERNS = (
    re.compile(r"\bpython\b", re.IGNORECASE),
    re.compile(r"\b(write|compose|generate)\b[^.]{0,30}"
               r"\b(poem|essay|story|song|joke|code|function|script)\b",
               re.IGNORECASE),
    re.compile(r"\bcapital of\b", re.IGNORECASE),
    re.compile(r"\bweather\b", re.IGNORECASE),
    re.compile(r"\bbook\b[^.]{0,20}\b(flight|hotel|tickets?)\b",
               re.IGNORECASE),
    re.compile(r"\bstock (price|market)\b", re.IGNORECASE),
    re.compile(r"\brecipe\b", re.IGNORECASE),
    re.compile(r"\bjoke\b", re.IGNORECASE),
)

_USERNAME_RE = re.compile(r"\bu-(\d{1,6})\b", re.IGNORECASE)

_REFUSAL_PREFIX = "I can't help with that request. "
_REFUSALS = {
    "prompt_injection": _REFUSAL_PREFIX +
        "It asks me to set aside the refund policy or reveal internal "
        "instructions. I can help with your own orders and refunds: share "
        "your username (for example u-1001) and the item you'd like to "
        "return.",
    "cross_customer": _REFUSAL_PREFIX +
        "I can only access the account of the customer I'm speaking with. "
        "Share your own username and I'll look up your orders.",
    "out_of_scope": _REFUSAL_PREFIX +
        "I only handle orders and refunds for this store. Ask me about an "
        "order or a refund and I'll get started.",
}


def _item_names():
    names = set()
    for user in data.USERS.values():
        for order in user["orders"]:
            for item in order["items"]:
                names.add(item["name"].lower())
    return sorted(names)


def _in_scope(text):
    """True when the request mentions refunds, orders or a store item."""
    lowered = (text or "").lower()
    if any(w in lowered for w in ("refund", "return", "money back",
                                   "order")):
        return True
    return any(name in lowered for name in _item_names())


class InputGuard:
    """Deterministic screen applied to a request before the agent runs."""

    def check(self, user_input):
        """Return a decision dict: action allow/block, category, and --
        when blocked -- the refusal text to send instead of running."""
        text = user_input or ""
        for name, pattern in _INJECTION_PATTERNS:
            if pattern.search(text):
                return self._block("prompt_injection", name)
        usernames = {"u-" + n for n in _USERNAME_RE.findall(text)}
        if len(usernames) > 1 or any(p.search(text)
                                     for p in _CROSS_CUSTOMER_PATTERNS):
            return self._block("cross_customer", "cross_customer_reference")
        off_topic = [p.pattern for p in _OFF_TOPIC_PATTERNS
                     if p.search(text)]
        if off_topic and not _in_scope(text):
            return self._block("out_of_scope", "off_topic_pattern")
        return {"guard": "input", "action": "allow", "category": "ok",
                "pattern": None, "refusal": None}

    @staticmethod
    def _block(category, pattern):
        return {"guard": "input", "action": "block", "category": category,
                "pattern": pattern, "refusal": _REFUSALS[category]}


# ---------------------------------------------------------------------------
# Tool-call guard (middleware)
# ---------------------------------------------------------------------------

class ToolCallGuard:
    """Per-request middleware around the agent's tools.

    - Identical repeat calls (same tool, same arguments) are suppressed:
      the cached first result is returned instead of executing again.
    - Each tool may execute at most ``max_calls_per_tool`` times per
      request; excess calls return a guard error dict instead of running.
    Every suppression is appended to ``log`` as a structured record.
    """

    def __init__(self, max_calls_per_tool=3, log=None):
        if max_calls_per_tool < 1:
            raise ValueError("max_calls_per_tool must be >= 1")
        self.max_calls_per_tool = max_calls_per_tool
        self.log = log if log is not None else []
        self._executions = Counter()
        self._cache = {}

    def wrap(self, name, fn):
        """Return a guarded version of tool ``fn`` registered as ``name``."""

        def guarded(*args, **kwargs):
            key = (name, json.dumps([args, kwargs], sort_keys=True,
                                    default=str))
            if key in self._cache:
                self.log.append({"guard": "tool", "action": "suppress",
                                 "category": "duplicate_call", "tool": name})
                return self._cache[key]
            if self._executions[name] >= self.max_calls_per_tool:
                self.log.append({"guard": "tool", "action": "suppress",
                                 "category": "call_cap_exceeded",
                                 "tool": name})
                return {"status": "guard_blocked",
                        "reason": "call_cap_exceeded", "tool": name}
            result = fn(*args, **kwargs)
            self._executions[name] += 1
            self._cache[key] = result
            return result

        guarded.__name__ = name
        return guarded


# ---------------------------------------------------------------------------
# Output guard
# ---------------------------------------------------------------------------

_TICKET_CLAIM_RE = re.compile(
    r"refund ticket\s+(?P<ticket>\S+)\s+for\s+\$(?P<amount>\d+\.\d{2})")
_AMOUNT_RE = re.compile(r"(?<![\d.])\$(?P<amount>\d+\.\d{2})(?!\d)")

_CORRECTED_ANSWER = (
    "I need to correct that: no refund ticket was issued for this "
    "request, so please don't rely on the claim. I've flagged the "
    "request for human review instead, and our team will follow up.")


class OutputGuard:
    """Verify the agent's final answer against the ticket ledger.

    A refund claim is only allowed through when the ticket it cites
    actually exists in the review queue, was filed in this turn's trace,
    covers items the customer really bought, and the amount printed in
    the answer is *exactly* the ticket amount (token-exact: the string
    "145.00" never matches a $45.00 ticket).
    """

    def __init__(self, queue):
        self.queue = queue

    def check(self, response):
        """Return (response, decision). The decision is None when the
        answer makes no refund claim and needs no verification; a blocked
        claim returns a corrected response and a "correct" decision."""
        answer = response.answer or ""
        lowered = answer.lower()
        filed = [s for s in response.trace
                 if s.get("tool") == "process_return_request"]
        claim = _TICKET_CLAIM_RE.search(answer)
        if claim is None:
            if "refund ticket" in lowered:
                return self._correct(response, "unverifiable_ticket_claim",
                                     "answer claims a ticket it does not "
                                     "identify")
            if not filed and "refund" in lowered and \
                    _AMOUNT_RE.search(answer):
                return self._correct(response, "claim_without_ticket",
                                     "answer states a refund amount but no "
                                     "ticket was filed")
            return response, None
        ticket_id = claim.group("ticket")
        ticket = self.queue.get(ticket_id)
        if ticket is None:
            return self._correct(response, "ticket_not_in_ledger",
                                 "claimed ticket %s is not in the review "
                                 "queue" % ticket_id)
        if not any(s.get("result") == ticket_id for s in filed):
            return self._correct(response, "ticket_not_in_trace",
                                 "claimed ticket %s was not filed in this "
                                 "turn" % ticket_id)
        if claim.group("amount") != "%.2f" % ticket["amount"]:
            return self._correct(response, "amount_mismatch",
                                 "answer claims $%s but the ticket is for "
                                 "$%.2f" % (claim.group("amount"),
                                            ticket["amount"]))
        user = data.get_user(ticket["username"])
        for item in ticket["items"]:
            if user is None or data.find_item(user, item)[1] is None:
                return self._correct(response, "item_not_purchased",
                                     "ticket covers %r, which %s never "
                                     "bought" % (item, ticket["username"]))
        return response, {"guard": "output", "action": "pass",
                          "category": "claim_verified",
                          "ticket_id": ticket_id}

    @staticmethod
    def _correct(response, category, detail):
        corrected = AgentResponse(_CORRECTED_ANSWER, False, response.trace)
        return corrected, {"guard": "output", "action": "correct",
                           "category": category, "detail": detail}


# ---------------------------------------------------------------------------
# Guarded agent: the existing agent, wrapped -- never forked
# ---------------------------------------------------------------------------

_TOOL_NAMES = ("get_order_details", "check_refund_eligibility",
               "calculate_refund", "process_return_request", "get_today")


class GuardedRefundAgent:
    """RefundAgent with the three guards switched on or off by one flag.

    With ``guards_enabled=False`` this is exactly the plain agent (the
    comparison's control). With it on, the input is screened first, the
    agent's tools are swapped for ToolCallGuard wrappers for the duration
    of the call (tickets are routed to this instance's own review queue),
    and the answer is verified against that queue before it is returned.
    ``last_guard_log`` holds the structured decisions of the latest call.
    """

    def __init__(self, version="v2", guards_enabled=True,
                 max_calls_per_tool=3, queue=None):
        self._agent = RefundAgent(version=version)
        self.version = version
        self.guards_enabled = guards_enabled
        self.max_calls_per_tool = max_calls_per_tool
        self.queue = queue if queue is not None else ReviewQueue()
        self.input_guard = InputGuard()
        self.last_guard_log = []

    def handle(self, user_input):
        log = []
        self.last_guard_log = log
        if not self.guards_enabled:
            return self._agent.handle(user_input)

        decision = self.input_guard.check(user_input)
        log.append(decision)
        if decision["action"] == "block":
            return AgentResponse(decision["refusal"], True, [])

        guard = ToolCallGuard(self.max_calls_per_tool, log)
        originals = {}
        try:
            for name in _TOOL_NAMES:
                originals[name] = getattr(tools, name)
            queue = self.queue

            def _ticket_tool(username, items, amount, _queue=None):
                # Route tickets to this agent's queue so the OutputGuard
                # verifies against the same ledger the agent filed into.
                return originals["process_return_request"](
                    username, items, amount, queue=queue)

            for name in _TOOL_NAMES:
                fn = _ticket_tool if name == "process_return_request" \
                    else originals[name]
                setattr(tools, name, guard.wrap(name, fn))
            response = self._agent.handle(user_input)
        finally:
            for name, fn in originals.items():
                setattr(tools, name, fn)

        response, out_decision = OutputGuard(self.queue).check(response)
        if out_decision is not None:
            log.append(out_decision)
        return response
