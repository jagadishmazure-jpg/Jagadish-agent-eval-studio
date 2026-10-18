# 02 — The seven metrics

Each metric answers one question about an agent trace. Together they
cover outcome, path, and conclusion.

## 1. Task completion — "Did it achieve the expected outcome?"
Checks that every expected conclusion phrase appears in the final
answer. Catches agents that do work but never deliver the result, or
deliver something adjacent to what was asked.

## 2. Step efficiency — "Any wasted tool calls?"
Scores the fraction of calls that could change the outcome. The classic
catch: fetching today's date *after* eligibility already ruled every
item out. At scale, wasted calls are wasted money and latency.

## 3. Plan adherence — "Did it follow a sensible order?"
The refund pipeline has a natural order: identify the user, check
history, check eligibility, calculate, ticket. Calculating before
checking eligibility, or ticketing before calculating, fails here.

## 4. Plan quality — "Was the plan effective for this scenario?"
Heuristics for structural nonsense: a ticket with no amount calculated,
an amount with no eligibility check, silence when tools were needed.
Catches plans that are internally consistent but wrong for the case.

## 5. Tool correctness — "The right tools?"
F1 over expected vs actual tool sets. Catches both missing tools
(skipped the eligibility check) and extra tools (called things
unprompted).

## 6. Argument correctness — "The right arguments?"
Per-tool, per-argument comparison against expectations. The right tool
with the wrong customer ID is a failure, not a near-miss.

## 7. Answer correctness — "The same logical conclusion?"
Phrase-level conclusion matching, not string equality. The agent may
word the answer its own way; the conclusion must match. This is the
deterministic stand-in for a G-Eval style LLM judge.

## What each one caught in this repo (real runs)

| Metric | What it caught |
|---|---|
| answer_correctness | v1 told an unknown user "no purchase history found" instead of asking for a valid username |
| step_efficiency | v1 called get_today even when the item was never purchased |
| tool_correctness | Would catch a skipped eligibility check (F1 drops) |
| plan_adherence | Would catch calculating before checking eligibility |
| task_completion | Would catch a ticket issued with no amount mentioned |

## LLM-judge seams

`task_completion` and `plan_quality` accept an optional `judge`
callable. Use the deterministic heuristics in CI (free, stable); plug
an LLM judge behind the seam for nuanced cases (tone, plan creativity)
and calibrate it against the heuristic baseline.
