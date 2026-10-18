"""Run the agent over every behaviour case and score all metrics.

Produces a list of CaseResult: the agent response plus one MetricResult
per metric. Deterministic: the same code always yields the same report.
"""

from dataclasses import dataclass, field

from . import metrics
from .dataset import get_cases
from refund_agent.agent import RefundAgent


@dataclass
class CaseResult:
    case_id: str
    description: str
    input: str
    answer: str
    conversation_continue: bool
    trace: list
    metric_results: dict = field(default_factory=dict)

    @property
    def passed_all(self):
        return all(r.passed for r in self.metric_results.values())


def run_case(case, agent, thresholds=None):
    """Run one case through the agent and score every metric."""
    thresholds = thresholds or {}
    response = agent.handle(case.input)
    results = {}
    for name, fn in metrics.ALL_METRICS.items():
        threshold = thresholds.get(name, 0.8)
        results[name] = fn(response, case, threshold)
    return CaseResult(case_id=case.case_id, description=case.description,
                      input=case.input, answer=response.answer,
                      conversation_continue=response.conversation_continue,
                      trace=response.trace, metric_results=results)


def run_all(version="v2", thresholds=None):
    """Run the full dataset against one agent version."""
    agent = RefundAgent(version=version)
    return [run_case(case, agent, thresholds) for case in get_cases()]


def summarize(results):
    """Per-metric averages and pass rates over a result list."""
    summary = {}
    names = list(metrics.ALL_METRICS)
    for name in names:
        scores = [r.metric_results[name].score for r in results]
        passes = [r.metric_results[name].passed for r in results]
        summary[name] = {
            "average": sum(scores) / len(scores) if scores else 0.0,
            "pass_rate": sum(passes) / len(passes) if passes else 0.0,
            "n": len(scores),
        }
    return summary
