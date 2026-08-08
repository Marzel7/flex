# EP2.1 — Temporal Primitive Reference Semantics

## Outcome

`WALLET_FRESH_AT_EVENT` now evaluates address history relative to its immutable
reference transaction. Solana address-history observations are retained newest
first, so only signatures after the reference signature in the retained list
are strictly earlier events. Later activity is excluded.

No Evidence, runtime, Operation Contract, acquisition, mirror, corpus, or RPC
behaviour changed.

## Temporal Primitive audit

| Primitive | Reference/boundary | Result |
|---|---|---|
| `WALLET_FRESH_AT_EVENT` | Activation transaction; `(-∞, activation)` | Defect fixed. The previous implementation counted every non-reference signature, including later activity. |
| `LAUNCH_ACTIVATION` | Funding event and launch event | No semantic change. It reports the observed latency and consumes event-relative freshness. |
| `ECONOMIC_FUNDING` | Funding event relative to launch | No semantic change. It records signed timing; it does not decide an Operation threshold. |
| `BEHAVIOURAL_TIMING` | Ordered observation window | No semantic change. Inputs are sorted by immutable chain timestamp. Its identities change only where corrected freshness/activation inputs change. |
| `REPEATED_COUNTERPARTY` | First/last observed events in the retained set | No defect. It is an aggregate, not an at-event historical assertion. |

The freshness implementation also indexes histories and participants before
evaluation. This preserves results while avoiding repeated full-corpus scans.

## Reference-event coverage rule

- Reference present: entries before it are later activity; entries after it are
  strictly preceding history.
- Reference absent: freshness is `UNKNOWN / UNVERIFIABLE` with
  `MISSING_REFERENCE_EVENT`.
- A non-zero pre-balance or an observed preceding signature remains
  `NOT_FRESH`.
- No inference is made across a truncated history page.

## Shadow replay

The replay report is
`docs/evidence_platform/ep2_1_temporal_primitive_replay.json`.

- RPC calls: **0**
- Evidence changes: **0**
- Source corpus mutations: **0**
- 3SW2 replay: deterministic across two independent two-checkpoint replays
- 3SW2 target creators: **9 / 13 verified fresh**
- 3SW2 unresolved: **4 / 13 unverifiable** because the retained 1,000-signature
  observation does not contain the activation reference
- WATCHTOWER activation inputs changed: **0 / 24**
- Unrelated primitive types changed: **0**

`LAUNCH_ACTIVATION` and `BEHAVIOURAL_TIMING` receive new deterministic identities
only where their corrected freshness dependency changed. Primitive counts remain
unchanged.

## EP3 parity result

EP3.2 parity improved from zero verified-fresh creators to nine. It is not
truthful to declare all thirteen fresh from the retained Evidence: four bounded
history observations omit the reference transaction. EP3.3 runtime isolation
remains valid, but full 3SW2 historical parity remains evidence-blocked rather
than engine-blocked.

This is a data-coverage limitation, not a remaining temporal evaluation defect.

