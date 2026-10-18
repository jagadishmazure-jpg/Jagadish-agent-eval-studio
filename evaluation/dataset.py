"""Evaluation dataset: behaviour cases with expected outcomes.

Each case pins down not just the expected *answer* but the expected
*behaviour*: which tools the agent should call and with what arguments.
That is what lets the trajectory metrics (tool/argument correctness,
step efficiency) say anything at all -- an answer-only dataset cannot
catch an agent that reaches the right answer through the wrong path.

All people, orders and prices are fictional (see refund_agent/data.py).
"""

from dataclasses import dataclass, field


@dataclass
class BehaviorCase:
    case_id: str
    description: str
    input: str
    # Key phrases (lowercased, punctuation-insensitive) that must appear in
    # the agent's final answer for the conclusion to count as correct.
    expected_conclusion: tuple
    # Tools the agent should call, in any order unless order matters.
    expected_tools: tuple
    # tool name -> {argument: expected value}
    expected_arguments: dict = field(default_factory=dict)
    # Whether the conversation should continue after this turn.
    expect_continue: bool = False


CASES = (
    BehaviorCase(
        case_id="valid-full-refund",
        description="Valid order, item within the 30-day window.",
        input="I am u-1001, I would like a refund for the trail backpack.",
        expected_conclusion=("refund ticket", "45.00"),
        expected_tools=("get_order_details", "check_refund_eligibility",
                        "calculate_refund", "process_return_request"),
        expected_arguments={
            "get_order_details": {"username": "u-1001"},
            "check_refund_eligibility": {"username": "u-1001",
                                         "items": ["trail backpack"]},
            "calculate_refund": {"username": "u-1001",
                                 "items": ["trail backpack"]},
        },
    ),
    BehaviorCase(
        case_id="partial-refund-one-of-two",
        description="Two items owned; customer refunds only one.",
        input="I am u-1001, please refund just the insulated bottle.",
        expected_conclusion=("refund ticket", "28.00"),
        expected_tools=("get_order_details", "check_refund_eligibility",
                        "calculate_refund", "process_return_request"),
        expected_arguments={
            "calculate_refund": {"username": "u-1001",
                                 "items": ["insulated bottle"]},
        },
    ),
    BehaviorCase(
        case_id="expired-window",
        description="Item purchased 69 days ago: outside the window.",
        input="I am u-1002, I want a refund for the camp lantern.",
        expected_conclusion=("no refund will be issued", "outside"),
        expected_tools=("get_order_details", "check_refund_eligibility"),
        expected_arguments={
            "check_refund_eligibility": {"username": "u-1002",
                                         "items": ["camp lantern"]},
        },
    ),
    BehaviorCase(
        case_id="mixed-eligibility",
        description="Lantern expired, trail socks eligible: ticket for one.",
        input="I am u-1002, I would like a refund for the camp lantern "
              "and the trail socks.",
        expected_conclusion=("refund ticket", "15.00", "not eligible"),
        expected_tools=("get_order_details", "check_refund_eligibility",
                        "calculate_refund", "process_return_request"),
        expected_arguments={
            "calculate_refund": {"username": "u-1002",
                                 "items": ["trail socks"]},
        },
    ),
    BehaviorCase(
        case_id="fraud-never-purchased",
        description="Customer claims an item they never bought.",
        input="I am u-1003, I would like a refund for the running sneakers.",
        expected_conclusion=("no refund will be issued", "never purchased"),
        expected_tools=("get_order_details", "check_refund_eligibility"),
        expected_arguments={
            "check_refund_eligibility": {"username": "u-1003",
                                         "items": ["running sneakers"]},
        },
    ),
    BehaviorCase(
        case_id="invalid-user",
        description="Unknown username: agent must ask for a valid one.",
        input="I am u-9999, I would like a refund for the trail backpack.",
        expected_conclusion=("username", "not valid", "valid username"),
        expected_tools=("get_order_details",),
        expect_continue=True,
    ),
    BehaviorCase(
        case_id="missing-username",
        description="No username given: agent must ask before doing anything.",
        input="Hi, I would like a refund for my backpack.",
        expected_conclusion=("username",),
        expected_tools=(),
        expect_continue=True,
    ),
    BehaviorCase(
        case_id="info-only-no-ticket",
        description="Customer asks about history, not a refund: no ticket.",
        input="I am u-1001, what orders do I have?",
        expected_conclusion=("order",),
        expected_tools=("get_order_details",),
        expect_continue=True,
    ),
    BehaviorCase(
        case_id="no-item-named",
        description="Refund requested but no item named: ask which item.",
        input="I am u-1001, I want a refund please.",
        expected_conclusion=("which item",),
        expected_tools=("get_order_details",),
        expect_continue=True,
    ),
    BehaviorCase(
        case_id="recent-purchase-refund",
        description="Recent small purchase, straightforward refund.",
        input="I am u-1003, please refund the hiking socks.",
        expected_conclusion=("refund ticket", "12.00"),
        expected_tools=("get_order_details", "check_refund_eligibility",
                        "calculate_refund", "process_return_request"),
        expected_arguments={
            "calculate_refund": {"username": "u-1003",
                                 "items": ["hiking socks"]},
        },
    ),
)


def get_cases():
    """Return the behaviour cases as a list."""
    return list(CASES)
