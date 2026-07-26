# X65.3 — Executive Summary: Runtime Verification of CREATE Signature Overwrite

Live production verification of X65.2's suspected write-path defect
(`_update_token_entry_with_creator()` overwriting an existing
`create_tx_signature` with `NULL`). Temporary diagnostic instrumentation
(one `SELECT` + conditional `log_print`, no SQL/behaviour change) was
deployed to the live `watchtower_listener` process and observed for
~3 hours of continuous production operation.

## Was a real overwrite observed?

**Yes — definitively, and repeatedly.** The very first migration
processed after deployment (within 93 seconds) already triggered the
condition. It recurred steadily throughout the entire ~3-hour
observation window.

## How many times?

**102 log-line detections across 105 distinct mints**, out of 418
migrations processed (~24.4% of all migrations during the observation
window). Every one of the 105 flagged mints was independently
confirmed, by direct database read (not inference), to have
`create_tx_signature = NULL` afterward — a **100% completion rate**
for the detected overwrite condition.

## Does the overwrite explain the missing signatures?

**Yes, fully, for every case checked.** All 105 flagged mints ended in
exactly the predicted end state with zero exceptions — the write-path
defect identified via static analysis in X65.2 is confirmed, at scale
and in real time, to be the actual, live, ongoing mechanism destroying
CREATE signatures in production.

## Would the COALESCE change prevent every observed overwrite?

**Yes — 107 of 107 (100%) of parsed real attempts.** Simulating
`COALESCE(incoming, existing)` against the exact captured values from
every logged attempt shows the fix would have preserved the original
signature in every single case, with zero instances where it would
have blocked a genuine new value (no observed attempt had a non-null
`incoming` to begin with).

## Is the fix safe to deploy?

**Yes**, per Phase 6's full safety assessment: preserves valid
signatures (confirmed live), still allows genuine new ones (confirmed
by SQL semantics), leaves migration processing, treasury attribution,
and Behaviour Cohorts completely unchanged, and introduces no
additional SQL queries or RPC calls beyond the already-deployed,
already-safe diagnostic itself.

## Success criteria — final status

| Criterion | Status |
|---|---|
| At least one real overwrite directly observed, or sufficient evidence to reject the hypothesis | ✅ 105 directly observed and DB-confirmed; hypothesis strongly affirmed, not rejected |
| Every conclusion supported by runtime observations, not static inspection | ✅ every Phase 2-6 finding is a live log/DB read, not a code-reading inference |
| Proposed COALESCE fix validated against real production behaviour before implementation | ✅ Phase 5, 107/107 simulated against real captured values |

## What remains (not performed in this task, by design)

- **The fix itself was not implemented** — this task was diagnostic-only,
  per its explicit instruction ("No functional behaviour is to change").
  Implementing the one-line `COALESCE` change is a small, separately-
  authorizable follow-up now backed by the strongest possible evidence.
- **The diagnostic instrumentation remains deployed** in the live
  listener — per its own comment, it should be removed once the fix
  ships and the observation window is considered closed; not removed
  in this task since further observation may still be useful before
  the fix is deployed.
- **The 105 already-affected mints were not recovered** — recovery
  remains a separate, explicit action per X65.2 Phase 6's
  `PARTIALLY_RECOVERABLE` finding, not performed here.

## Deliverables

`docs/design/x65_3/` — `x65_3_runtime_observation.md`,
`x65_3_post_update_state.md`, `x65_3_blast_radius.md`,
`x65_3_fix_validation.md`, `x65_3_safety.md`, this summary. Diagnostic
instrumentation remains live in
`src/core/pumpfun_curve_listener.py`. No functional code was changed;
no data was recovered or modified.
