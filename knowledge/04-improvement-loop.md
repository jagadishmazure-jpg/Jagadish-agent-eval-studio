# 04 — The improvement loop

Scores don't fix agents; the loop does. This repo demonstrates the full
cycle on two real, measured flaws.

## The cycle

1. **Run.** `run_eval.py` scores all 7 metrics over 10 behaviour cases.
2. **Analyse.** `analyze.py` groups failures by metric and matches
   traces against failure patterns.
3. **Fix.** Change the agent (prompt/logic/tooling), not the test.
4. **Re-run.** `improve.py` evaluates both versions.
5. **Prove.** The before/after comparison shows the delta per metric
   and confirms no regressions.

## Flaw 1: unknown-user messaging (answer_correctness)

- **v1 behaviour:** unknown username → "No purchase history found, so no
  refund will be issued." The real problem is the username itself, but
  the agent talks about history.
- **Detected by:** `answer_correctness` (expected "username not valid",
  got history talk) and `task_completion`.
- **Fix (v2):** name the problem — "The username u-9999 is not valid.
  Please check it and send a valid username."
- **Measured:** answer_correctness 0.90 → 1.00, task_completion
  0.90 → 1.00.

## Flaw 2: unnecessary date lookup (step_efficiency)

- **v1 behaviour:** calls `get_today` even when the requested item was
  never purchased, so the date cannot change the outcome.
- **Detected by:** `step_efficiency` (4/5 necessary calls = 0.80) on the
  expired-window and fraud cases.
- **Fix (v2):** drop the call — the eligibility check already encodes
  the 30-day window.
- **Measured:** step_efficiency 0.85 → 1.00.

## Full before/after (real runs, 10 cases)

| Metric | v1 avg | v2 avg | Delta |
|---|---|---|---|
| task_completion | 0.90 | 1.00 | +0.10 |
| step_efficiency | 0.85 | 1.00 | +0.15 |
| plan_adherence | 1.00 | 1.00 | +0.00 |
| plan_quality | 1.00 | 1.00 | +0.00 |
| tool_correctness | 0.92 | 1.00 | +0.08 |
| argument_correctness | 1.00 | 1.00 | +0.00 |
| answer_correctness | 0.90 | 1.00 | +0.10 |

No regressions: every delta is ≥ 0, and `case_regressions` confirms no
single case got worse on any metric (averages alone can hide a case that
regressed). `scripts/eval_summary.py --check` compares a fresh run with
the committed `results/eval_summary.json` in CI.

## The interview story

"I built an eval harness with seven metrics, found two flaws the
happy-path tests missed — wrong error messaging and a wasted tool call
— fixed them, and proved the improvement with before/after numbers and
zero regressions." That sentence is the whole point of the repo.
