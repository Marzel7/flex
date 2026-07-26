# X65.2 — Executive Summary: Historical Coverage vs Lineage Indexing Investigation

Read-only investigation into whether the 12 unresolved launches from
X65.1 predate the current CREATE/Birth capture pipeline, bypassed part
of it, or lost lineage after successful capture — given that current
pipeline health reports 100% Birth capture (2358 seen / 2358 persisted
/ 0 missing). No attribution logic, treasury heuristics, or
classification logic was changed.

## Do the unresolved launches predate the current capture pipeline?

**No.** All 12 launches' CREATE timestamps (2026-07-15 through
2026-07-21) fall strictly after the current code generation's
deployment (`394dbd9`, 2026-07-14T14:57:14Z). None predate it. All 12
are classified `CAPTURE_PIPELINE_ACTIVE` — every one occurred while the
current, live pipeline was nominally running.

## Earliest stage where evidence disappears

**CREATE ledger — uniformly, for all 12 of 12 launches.** Every launch
passes "Program CREATE observed" (creator wallet correctly captured)
and "Birth persisted" (row created, attributed to a genuine birth
event), then fails at the very next stage: zero rows in
`wt_create_event_ledger` and a `NULL` `create_tx_signature`, despite
the creator having been correctly captured moments earlier. Every
downstream stage (Funding captured, Walkback, SubProv, Treasury Link,
Topology, Funding Origin, Operation Attribution) is a direct,
fully-explained consequence of this single earliest gap — no launch in
this cohort fails at a different stage first.

## Does current listener health contradict the unresolved cohort?

**No — the two measure different things and are fully reconciled.**
The "100% Birth Health" metric (`src/core/main.py:24681-24750`) is a
log-line-counting proxy scoped to the *current* listener process's own
run, answering only "did a birth get an initial row write." It does
not check whether any individual column (like `create_tx_signature`)
survives a **later** write to the same row, and it cannot see across
the ~3,224 separate process restarts that occurred in the window these
12 launches span. The unresolved cohort's defect operates entirely
inside that blind spot: the birth-time write succeeds (correctly
counted as "persisted" by the health metric) and a **separate,
later** migration-time write then silently discards the signature — a
data-integrity/overwrite defect, not a coverage/completeness defect.
100% Birth Health and 12 unresolved launches are not in tension once
the metric's actual measurement boundary is understood.

## Historical or active?

**Active — Mixed Causes.** Primary, fully-explanatory cause: a
still-live code defect in `_update_token_entry_with_creator()`
(`src/core/pumpfun_curve_listener.py:7963`) — an unconditional
`UPDATE ... SET create_tx_signature=?` with no `COALESCE` guard,
overwriting an already-correct birth-time signature with `NULL`
whenever migration-time RPC re-validation doesn't independently
reconfirm the CREATE transaction. This single mechanism accounts for
the uniform CREATE-ledger failure across all 12 launches. Secondary,
corroborating contributor: chronic `watchtower_listener` process
instability across the entire investigated window (3,224 restarts,
median gap ~6.3 minutes) — 8 of the 12 launches occurred within ±30
minutes of a restart, a real but not independently sufficient
additional factor. Neither cause is historical; both are properties of
the currently-deployed system.

## Recommended permanent fix

A one-line capture-layer correction: change
`create_tx_signature=?` to `create_tx_signature=COALESCE(?,
create_tx_signature)` in the single `UPDATE` statement inside
`_update_token_entry_with_creator()`. No schema change, no new table,
no new pipeline, no attribution-logic or treasury-heuristic change —
the existing strict on-chain re-validation remains exactly as strict
for writing *new* values; it simply can no longer erase a
correctly-captured existing one. The chronic listener-restart
instability is flagged as a separate, out-of-scope stability issue
warranting its own future investigation, not addressed by this fix.

## Expected increase in attribution after remediation

**None, retroactively, for the existing 12 launches** — the fix
prevents recurrence for future launches; it does not recover
already-lost signatures for this cohort (a separate, explicit recovery
action, not performed or required here). For **future** launches: the
fix directly closes the CREATE-ledger gap for the class of launch
investigated, and — given the same defect was found to affect the
large majority of migrated tokens system-wide in the prior
investigation pass (87% null `create_tx_signature` rate) — its benefit
extends well beyond this specific cohort. It does **not** by itself
increase `KNOWN_TREASURY` resolution rates, since the unresolved
launches' funder wallets independently lack any sub-provisioner/
treasury lineage regardless of CREATE-signature availability — closing
that gap would require separate, future work expanding
sub-provisioner/treasury detection coverage.

## Success criteria — final status

| Criterion | Status |
|---|---|
| Every unresolved launch traced to its earliest missing pipeline stage | ✅ all 12 → CREATE ledger |
| Current health metrics and historical unresolved launches reconciled into one explanation | ✅ Phase 5 |
| Historical coverage gaps distinguished from active pipeline failures | ✅ Phase 1/6 — active, not historical |
| Proposed fix targets the earliest missing stage, not new attribution logic | ✅ Phase 7 — one-line capture-layer fix, zero attribution changes |

## Deliverables

`docs/design/x65_2/` — `x65_2_historical_placement.md`,
`x65_2_pipeline_matrix.md`, `x65_2_first_failure.md`,
`x65_2_population_groups.md`, `x65_2_health_correlation.md`,
`x65_2_root_cause.md`, `x65_2_fix.md`, `x65_2_regression.md`, this
summary. No code was changed; no data was recovered or attributed;
all 12 launches remain exactly as `UNRESOLVED`/`__UNASSIGNED__` as
before this investigation began.
