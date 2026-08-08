# OIP v2.1 — Eligible Migrated Coverage Census

Contract: `OIP_V2_COVERAGE_V1`

Measured against 32,042 eligible migrated launches:

| State | Launches | Deterministic meaning |
|---|---:|---|
| Complete | 0 | Creation transaction, migration transaction and LaunchFact present |
| Pending | 13,202 | Known signatures permit bounded transaction acquisition or replay |
| Unavailable | 18,840 | No recorded creation signature; signature discovery is out of scope |
| Failed | 0 | No currently indexed normalization failure owns the gap |
| Stale | 0 | No LaunchFact/production creation-signature conflict |

Pending root causes:

- 13,081 launches lack both creation and migration TransactionFacts.
- 121 launches have creation evidence but lack migration TransactionFact.
- 26,283 unique transaction signatures require acquisition.
- No required signature is present in the production `rpc_response_cache`.

The earlier 67.31% value measures Evidence-complete EP4 discovery occurrences;
it is not eligible-migrated per-launch coverage and must not be presented as
such. Both metrics remain valid within their named denominators.

## Execution gate

Acquisition must use the shared transaction layer, mirror into the isolated
Evidence store, and declare a hard call limit before execution. The complete
recoverable plan is 26,283 calls. Provider credit cost is intentionally not
assumed. No call has been made under OIP v2.1 without an approved budget.
