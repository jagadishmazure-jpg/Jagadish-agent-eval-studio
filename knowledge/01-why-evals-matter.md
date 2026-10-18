# 01 — Why evals matter

A demo is not evidence. An agent that handles one refund correctly on a
call has told you nothing about the next hundred calls: the edge cases,
the fraud attempts, the wasted tool calls that cost money at scale.

Evaluation turns "it worked once" into measured behaviour:

1. **A dataset pins the contract.** Each behaviour case says what the
   agent must do *and how* — which tools, which arguments, what
   conclusion. Without the "how", an agent can reach the right answer
   through the wrong path and you will never know.

2. **Metrics separate luck from skill.** Seven lenses on the same trace
   catch different failure modes: a wrong answer, a wasteful path, a
   good plan poorly followed, the right tools with wrong arguments.

3. **The loop compounds.** Scores alone change nothing. The value is in
   the cycle: run → find the pattern → fix → re-run → prove the delta.
   Every fix in this repo is demonstrated with a before/after comparison,
   not claimed.

The honest framing: these metrics are deterministic heuristics, not
human judgment. They catch structural failures cheaply and repeatably.
For nuanced grading (tone, plan creativity), an LLM judge plugs into the
documented seams — but the heuristics run first, in CI, for free.
