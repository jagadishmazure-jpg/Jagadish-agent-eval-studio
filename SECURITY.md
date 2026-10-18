# Security policy

## Scope

This is a portfolio learning project. It runs fully offline on fictional
data: there are no live services, no credentials, and no real customer
data anywhere in the repo.

## Safety properties (tested, not just documented)

- The refund agent cannot move money. `process_return_request` issues a
  ticket flagged for human review; no payout code path exists.
- Fraud probes (items never purchased) are denied with reasons.
- Unknown users are asked for a valid username; no data is disclosed.

## Reporting a vulnerability

If you find a security issue in this demo code, please open a GitHub
issue. Do not include any real personal data in the report.
