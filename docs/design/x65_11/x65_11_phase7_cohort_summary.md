# X65.11 — Phase 7: Cohort Summary

## Total WATCHTOWER launches

**19** (last 24 hours, `campaign=WATCHTOWER`, live query at time of audit).

## Topology distribution

| Topology | Count | % |
|---|---|---|
| MULTI_LEVEL_FAN_OUT | 13 | 68.4% |
| LINEAR | 6 | 31.6% |
| FAN_OUT | 0 | 0% |
| MESH | 0 | 0% |
| UNKNOWN | 0 | 0% |
| **Total** | **19** | **100%** |

Conserved (sums to cohort size exactly, per the classifier's own
exhaustive if/elif/else structure, X65.8/X65.10).

## Treasury distribution

| Treasury status | Count | % |
|---|---|---|
| Known (confirmed) | 17 | 89.5% |
| Unknown (unconfirmed) | 2 | 10.5% |

The 2 unconfirmed-treasury launches (`5KtNnnPt7x…`, `EnEgmM4Eb6…`)
share the **same** unconfirmed treasury wallet (`FkccGTEh6tJe…`) — one
distinct unconfirmed candidate, not two independent ones.

## Creator freshness

| Creator identity | Count | % |
|---|---|---|
| Fresh creator | 19 | 100% |
| Repeat creator | 0 | 0% |

## Provisioning reuse

| Status | Count | % |
|---|---|---|
| Single-use (verified) | 0 | 0% |
| Reused (verified) | 0 | 0% |
| Unknown (no wrap-wallet-level evidence available) | 19 | 100% |

## Sub-provider reuse

| Status (sub-provider → creator layer) | Count of launches | Count of distinct sub-providers |
|---|---|---|
| Sub-provider with multiple creators funded (fan-out evidenced) | 12 launches | 5 sub-providers |
| Sub-provider with exactly 1 creator funded | 4 launches | 4 sub-providers |
| Sub-provider with 0 recorded creator edges | 3 launches | 3 sub-providers |

Within this 24-hour window specifically, 3 sub-providers each produced
more than one launch: `5tzFkiKscXHK…` (5 launches),
`BmFdpraQhkiD…` (3 launches), `Dv34prGm2BT7…` (2 launches) — direct,
observed sub-provider reuse within the window itself, not only
inferred from all-time history.

## Funding mechanism

| Mechanism | Count | % |
|---|---|---|
| WSOL_WRAP_CLOSE (account-close funding) | 13 | 68.4% |
| PLAIN_TRANSFER | 6 | 31.6% |

## Operation attribution

| Field | Count | % |
|---|---|---|
| `campaign=WATCHTOWER` (this cohort's defining criterion) | 19 | 100% |
| `operation_id=WATCHTOWER` (confirmed operation, via treasury→`wt_ops_v2_wallets` link) | 4 | 21.1% |
| `operation_id=None` / `__UNASSIGNED__` | 15 | 78.9% |

These are two **independent** fields (X65.7's Campaign membership vs.
the older, treasury-gated Operation Attribution) — a launch can be, and
frequently is in this cohort, correctly `campaign=WATCHTOWER` while
`operation_id` remains unassigned, because Operation Attribution
requires the resolved treasury to already be linked to a confirmed
operation UUID in `wt_ops_v2_wallets` (X65.1), a stricter and
independent condition from Campaign's own mandatory criteria.
