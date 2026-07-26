# X65.2 — Phase 8: Regression Validation

Verifies the proposed fix (Phase 7: `COALESCE`-guard the
`create_tx_signature` column in `_update_token_entry_with_creator()`'s
`UPDATE` statement) against every required safety property. The fix
was **not implemented** in this task — this is a design-time
verification of what it would and would not touch.

## Preserve Behaviour Cohorts

**Preserved.** The fix touches exactly one column expression in one
`UPDATE` statement in `pumpfun_curve_listener.py`. It has no
relationship to `src/ops/operational_behaviour_tags.py` or
`canonical_behaviour_for()` — neither reads nor is affected by
`create_tx_signature`. Confirmed by inspection: no behaviour-cohort
code path references `create_tx_signature` anywhere in the codebase
(grepped in the prior investigation pass while tracing this column's
writers/readers — only `token_analysis` write/read sites and
`wt_create_event_ledger`-adjacent code touch it).

## Preserve Creator Identity

**Preserved.** `src/ops/creator_identity.py`'s `enrich_creator_identity()`
and the `HISTORY_ROW_CAP` guard operate on `token_analysis` creator/
history fields (`pf_ws_creator`, launch counts, etc.) — never on
`create_tx_signature`. The fix changes only how one column's value is
written, not the value of any creator-identity-relevant column, and
not the row's existence or creator fields at all.

## Preserve Treasury Resolution

**Preserved.** `src/ops/treasury_resolution.py`'s
`resolve_treasury_for_cohort()` reads exclusively from
`wt_attribution_outcomes`, `wt_active_subprov_sessions`,
`wt_confirmed_treasuries`, and `wt_ops_v2_wallets` — confirmed (prior
investigation pass) to contain zero references to
`token_analysis.create_tx_signature` anywhere in the module. The fix
cannot alter any Treasury Resolution outcome for any launch, resolved
or unresolved, because that module never consults this column.

## Preserve confirmed operations

**Preserved.** The fix contains no `INSERT`/`UPDATE`/`DELETE` against
`wt_confirmed_treasuries` or `wt_ops_v2_wallets` — it modifies a single
`UPDATE` statement scoped entirely to `token_analysis`. No confirmed
operation's treasury, wallet, or UUID mapping is touched.

## Introduce no automatic treasury confirmation

**Preserved.** The fix does not create, infer, or promote any treasury
candidate — it only prevents an existing, correctly-captured
`create_tx_signature` value from being overwritten with `NULL`. It
adds no new confirmation logic of any kind.

## Introduce no duplicate lineage

**Preserved.** No new table, no new pipeline stage, and no parallel
write path is introduced. The fix corrects the existing single write
site in place; it does not add a second mechanism that could produce
divergent or duplicate lineage records for the same mint. This was an
explicit design constraint honored in Phase 7 (the "avoid duplicate
pipelines" instruction ruled out an alternative "hardened" write path).

## Avoid unnecessary RPC load

**Preserved — reduces RPC dependency if anything.** The fix is a pure
SQL expression change (`create_tx_signature=?` →
`create_tx_signature=COALESCE(?, create_tx_signature)`) inside a
statement that already runs exactly once per migration-time
creator-extraction call. It adds zero new RPC calls, zero new queries,
and zero additional round-trips. If anything, by preserving an
already-good signature instead of leaving the field `NULL`, it
**reduces** the likelihood that some downstream consumer later
triggers its own RPC-based recovery attempt for a signature that
should never have been lost in the first place.

## Additional check: does the fix change any currently-passing test's expected behavior?

Not verified directly in this task (the fix was not implemented), but
by inspection: no existing test file in `tests/` was found (in the
prior investigation pass's review of `_update_token_entry_with_creator`
and its call site) to assert on the specific "overwrite with NULL"
behavior being corrected — the strict `is_pumpfun_create` validation
gate (line 9146-9148) and its resulting log lines
(`[CREATOR] ✅ Extracted from earliest tx: ... CREATE tx validated`
vs. `... FAILED`/`NOT_SET`) are unaffected by this fix; only the
subsequent write's destructiveness changes. A full test-suite run
would still be warranted before shipping, but no direct evidence of a
test asserting the destructive behavior was found.

## Summary

| Property | Status |
|---|---|
| Behaviour Cohorts preserved | ✅ |
| Creator Identity preserved | ✅ |
| Treasury Resolution preserved | ✅ |
| Confirmed operations preserved | ✅ |
| No automatic treasury confirmation introduced | ✅ |
| No duplicate lineage introduced | ✅ |
| No unnecessary RPC load | ✅ (neutral to positive) |

All required regression properties are satisfied by the fix as
designed in Phase 7. No implementation was performed in this task.
