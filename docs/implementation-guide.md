# Implementation guide

How to adopt this evaluation harness for your own agent.

## 1. Port the agent seam

Your agent needs two things the harness expects:

- `handle(user_input)` returning an object with `.answer` (str),
  `.conversation_continue` (bool), and `.trace` (list of dicts with
  `tool`, `arguments`, `reasoning`, `result`).
- Tools as plain functions. The harness never calls your tools
  directly; it reads them from the trace.

If your agent is LLM-based, wrap it: run the LLM, record each tool
call into the trace format, and return the structured response. The
metrics don't care how the trace was produced.

## 2. Write behaviour cases

For each scenario, define a `BehaviorCase`:

- `input`: the raw user message.
- `expected_conclusion`: key phrases (normalised, order-free) that must
  appear in the final answer. Prefer 2-4 phrases over exact strings.
- `expected_tools`: the tools a correct run should call.
- `expected_arguments`: per-tool argument expectations (only what
  matters; leave the rest unchecked).
- `expect_continue`: whether the conversation should continue.

Cover: happy path, each policy boundary (expired, ineligible), fraud
probes (never purchased), malformed input (unknown user, missing
username), and non-goal inputs (info-only questions must not trigger
side effects).

## 3. Run and read the report

```bash
python3 demos/demo_run.py
python3 demos/demo_run.py --html report.html
```

Start with the failure analysis: it names patterns, not just scores.
Fix the agent, not the test. Re-run and check `improve.py` deltas.

## 4. Plug in LLM judges (optional)

`task_completion` and `plan_quality` accept a `judge` callable:

```python
def my_judge(answer, expected_phrases):
    # call your LLM judge here; return (score_0_to_1, reason_str)
    ...

metrics.task_completion(response, case, judge=my_judge)
```

Calibrate the judge against the deterministic baseline first: on the
cases where both agree you have ground truth; where they disagree, read
the trace yourself before trusting either.

## 5. CI integration

The suite is stdlib-only and runs in under a second. Gate on it:
fail the build if any metric's pass rate drops below its threshold, and
fail on any regression in the v1-vs-v2 comparison (see
`test_eval_loop.py` for the assertion pattern).
