# ADR 001: A rule-based agent instead of a live LLM

## Status
Accepted

## Context
The repo exists to demonstrate the evaluation harness: metrics, cases and
the improvement loop. A live LLM makes runs non-deterministic, needs keys
and costs money, and makes the metric tests flaky.

## Decision
The agent is deterministic Python that emits the same trace format an LLM
agent would (tool, arguments, reasoning, result). The places where an LLM
would plug in are marked `LLM SEAM`.

## Consequences
- Every metric is unit-testable and CI runs in about a second, offline.
- The v1 → v2 numbers are reproducible and checked in CI.
- The results say nothing about how a real LLM agent performs; the README
  calls the 10 cases a regression set, not a quality claim.
