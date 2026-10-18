# ADR 003: argument_correctness passes only when every argument matches

## Status
Accepted (2026-10-18)

## Context
With a 0.8 match-rate threshold, a ticket call with the amount as the
string "45.0", or with an extra `approved=True` flag, scored about 0.88 and
passed. For tools that file refunds, "mostly right" arguments are wrong.

## Decision
Keep the match rate as the score (it shows how far off a call was), but
pass only when every checked argument matches, every call of an expected
tool is checked, values are compared type-aware (int and float compare
numerically; bool never equals a number), and arguments the real tool does
not accept count as failures.

## Consequences
- Wrong-type and smuggled arguments fail the metric (pinned by tests).
- Teams adopting the harness with fuzzy arguments (free text) should add a
  per-argument comparator rather than lowering the bar for all tools.
