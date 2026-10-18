# ADR 002: The agent files tickets; only a named human authorises payouts

## Status
Accepted (hardened 2026-10-18)

## Context
A refund agent that can move money is a high-impact target for prompt
injection and for its own bugs. Originally "human review" was a docstring
and the ticket tool accepted any user, items and amount.

## Decision
- `process_return_request` re-validates the user, every item and the
  amount, and files a `pending_review` ticket.
- `ReviewQueue.approve` requires a reviewer who is not an agent identity and
  not the customer; each ticket is decided once.
- The agent module has no reference to `approve` or `ReviewQueue`
  (a test asserts this).

## Consequences
- An agent bug or injected instruction can at worst file a wrong-but-valid
  ticket, which a human sees before any money moves.
- A production port needs authenticated reviewer identities and a
  persistent, audited queue.
