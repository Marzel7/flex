# X65.1 — Phase 7: Resolution Model

Implemented as `src/ops/treasury_resolution.py` — a new, read-only
module producing exactly one `treasury_resolution` object per launch,
per the task's specified schema. Zero writes, zero RPC. 23 tests in
`tests/test_x65_1_treasury_resolution.py`, all passing.

## Schema (as implemented)

```json
{
  "treasury_resolution": {
    "status": "KNOWN_TREASURY | UNKNOWN_TREASURY_CANDIDATE | NO_SUBPROV | UNRESOLVED",
    "creator_wallet": "...",
    "subprov_wallet": "...",
    "treasury_wallet": "...",
    "operation_id": "...",
    "operation_name": null,
    "hop_depth": 2,
    "confidence": 0.0,
    "evidence": [...],
    "reason": "..."
  }
}
```

Matches the task's recommended structure exactly. `operation_name` is
always `null` in the current implementation — per Phase 5's finding,
`wt_operation_lifecycle` (the only per-operation metadata table found)
has no display-name column, so there is genuinely nothing to populate
here without inventing one; this is documented, not silently omitted.

## Design decisions and how they satisfy the task's requirements

### One resolution object per launch

`resolve_treasury_for_cohort(ops_db_path, mints)` returns a dict keyed
by mint, one `treasury_resolution` per key — verified directly:
`test_resolve_cohort_returns_one_object_per_mint`.

### Explicit nulls where unresolved

Every early-exit path (`UNRESOLVED`, `NO_SUBPROV`) explicitly sets
`subprov_wallet`/`treasury_wallet`/`operation_id` to `None` rather than
omitting the keys or leaving a prior value — verified:
`test_no_fabricated_wallet_when_unresolved`,
`test_full_resolution_unresolved_when_no_evidence`.

### No fabricated wallet, no silent fallback

`classify_creator_funder()` only ever returns a wallet it read directly
from `wt_active_subprov_sessions`/`wt_confirmed_treasuries` — there is
no code path that constructs, guesses, or falls back to a default
wallet value anywhere in the module (confirmed by direct code
inspection: the only string literals assigned to a wallet field are
`None` or values read from a SQL row).

### Evidence path retained

Every non-trivial resolution accumulates an `evidence` list describing
each hop, its source table, and the specific facts used (signature,
amount, timestamp, confirmation method) — never just a bare
conclusion. Verified: `test_evidence_path_never_empty_for_any_resolved_status`.

### Confidence derived from documented evidence, not invented

| Scenario | Confidence | Basis |
|---|---|---|
| `KNOWN_TREASURY`, subprov was `CONFIRMED_SUBPROV` | 0.95 | Full transaction-level evidence at both hops, plus authoritative treasury confirmation |
| `KNOWN_TREASURY`, subprov was `PROBABLE_SUBPROV` | 0.6 | Partial hop-1 evidence, but hop-2 treasury is still fully confirmed |
| `KNOWN_TREASURY` via direct treasury (no subprov hop) | 0.9 | One fewer hop of uncertainty, but still a full confirmation match |
| `UNKNOWN_TREASURY_CANDIDATE`, subprov was `CONFIRMED_SUBPROV` | 0.4 | Real transaction evidence exists, but the treasury itself is unconfirmed — capped well below any `KNOWN_TREASURY` value |
| `UNKNOWN_TREASURY_CANDIDATE`, subprov was `PROBABLE_SUBPROV` | 0.2 | Weakest positive case — partial hop-1 evidence AND an unconfirmed treasury |
| `UNRESOLVED` (any reason) | 0.0 | No evidence to derive confidence from |

These are not tuned/calibrated probabilities in the statistical sense —
they are ordinal confidence bands reflecting how much persisted
evidence supports each conclusion, consistent with this project's
existing convention (e.g. `wt_confirmed_treasuries.confidence` itself
uses labels like `CONFIRMED`/`MANUAL`, not calibrated probabilities).

### Existing confirmed operation assignment remains authoritative

This module never writes to `wt_ops_v2_wallets`, `wt_confirmed_treasuries`,
or any operation-attribution table — `match_known_treasury()` is a pure
read (verified: `test_match_known_treasury_never_writes_to_confirmed_table`,
`test_resolve_cohort_is_read_only`). Any `operation_id` this module
surfaces is exactly the one already present in `wt_ops_v2_wallets` —
never a new or modified value.

### Bounded traversal (max 2 hops, extended only with proof)

`MAX_WALKBACK_DEPTH = 2`. `is_bridged_further_upstream()` explicitly
checks (never assumes) whether a treasury candidate is itself a
`subprov_wallet` of some further wallet — this check ran against all 3
real treasury candidates found in the live cohort and returned `False`
for all three (Phase 4), so depth was never extended in practice, but
the check itself is real and tested
(`test_bridging_detected_when_treasury_is_itself_a_subprov`,
`test_no_bridging_when_treasury_is_terminal`).

## Live verification against the real 19-mint cohort

Running `resolve_treasury_for_cohort()` directly against the production
`wt_ops_v2.db` for the 19 cohort mints reproduces Phases 3-5's manual
analysis **exactly**:

```
status counts: {'UNRESOLVED': 12, 'KNOWN_TREASURY': 7}
```

Zero `UNKNOWN_TREASURY_CANDIDATE`, zero `NO_SUBPROV` — consistent with
Phase 3's finding that every direct funder checked was either a
complete `CONFIRMED_SUBPROV` (Group A, resolving cleanly to
`KNOWN_TREASURY`) or had zero persisted evidence at all (Group B,
`UNRESOLVED`). A spot-checked `KNOWN_TREASURY` result
(`3LZL5cXac86U1ti81V8GEA1qoj3HenLfnJMcQo7opump`) matches Phase 5's
manually-traced resolution path field-for-field: `subprov_wallet:
82Yzf1hMDyLa1Z8uADcxzMHxmmGedwKj6viUReKfTeKJ`, `treasury_wallet:
9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4`, `operation_id:
4135d67d-2b70-407a-be3c-ab47526203ac`, `hop_depth: 2`.
