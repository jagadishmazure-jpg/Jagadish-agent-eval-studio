"""Shared helpers for metric unit tests: build fake agent responses."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from refund_agent.agent import AgentResponse


def make_response(answer, tool_calls=(), continue_flag=False):
    """tool_calls: list of (tool_name, arguments_dict)."""
    trace = [{"tool": name, "arguments": args,
              "reasoning": "test step", "result": "ok"}
             for name, args in tool_calls]
    return AgentResponse(answer, continue_flag, trace)


def make_case(expected_conclusion=("ok",), expected_tools=(),
             expected_arguments=None, case_id="test-case"):
    from evaluation.dataset import BehaviorCase
    return BehaviorCase(case_id=case_id, description="test",
                        input="test input",
                        expected_conclusion=expected_conclusion,
                        expected_tools=expected_tools,
                        expected_arguments=expected_arguments or {})
