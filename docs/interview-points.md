# Interview talking points

## The 30-second version

"I built an end-to-end agent evaluation studio: a refund agent with
five tools, a 10-case behaviour dataset, seven deterministic metrics,
and a measured improvement loop. The harness caught two flaws the
happy-path tests missed — wrong error messaging for unknown users and
a wasted tool call — and the before/after numbers prove both fixes with
zero regressions, case by case. A review then tried to game my own
metrics and found that phrase matching and loose argument checks could
be fooled; I hardened them and pinned each trick as a regression test.
126 tests, all offline, no API keys."

## Questions this repo answers well

**"How do you evaluate agents?"**
Seven metrics across three layers: outcome (task/answer correctness),
path (step efficiency, plan adherence, plan quality), and behaviour
(tool/argument correctness). Behaviour cases pin expected tools and
arguments, not just answers.

**"How do you know the metrics aren't just testing themselves?"**
The v1 agent genuinely fails: the flaws were designed into v1's logic,
the metrics catch them on real traces, and v2's fixes are separate code
changes. The tests assert the delta, not the score.

**"Why deterministic instead of LLM judges?"**
Cost, speed, CI. Deterministic heuristics catch structural failures
(wrong tools, wrong order, wasted calls, wrong conclusion) for free.
LLM judges plug into documented seams for nuance — and get calibrated
against the heuristic baseline first.

**"What would you do with a real LLM agent?"**
Wrap it to emit the same trace format, keep all seven metrics, add an
LLM judge behind the two seams, and grow the dataset. The harness
doesn't care how traces are produced.

**"Tell me about a failure you found through evals."**
Two: (1) unknown users got "no purchase history found" instead of being
asked for a valid username — answer_correctness caught it; (2) the
agent fetched today's date even when the item was never purchased —
step_efficiency caught it. Both fixed, both measured.

## Numbers to quote (real runs)

- 126 tests, 10 behaviour cases, 7 metrics
- v1 → v2: step_efficiency 0.85 (0.853) → 1.00, answer_correctness 0.90 → 1.00
- Zero regressions across all seven metrics and all 10 cases, checked in CI
  against `results/eval_summary.json`
- Suite runs in about a second, stdlib only

**"How do you stop an agent eval from being gamed?"**
Attack it. On the original code an answer saying "No refund will be
issued" scored 1.00 on a refund case, a string amount passed argument
checks, and three identical calls looked efficient. Now: whole-word,
negation-aware phrases plus forbidden phrases, multiplicity-aware
counting, type-aware strict arguments. `demos/demo_gaming_blocked.py`
shows each probe.

**"Can the agent move money?"**
No. It files a ticket; the ticket tool recomputes the amount and
re-checks eligibility; only a named human who is neither the agent nor
the customer can approve, once.
