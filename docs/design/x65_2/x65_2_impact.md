# X65.2 — Phase 8: Impact Analysis

## Scope correction: the underlying bug is far broader than the 12-launch cohort

A live check of `token_analysis` for the last 7 days shows
**14,410 of 16,589 migrated tokens (87%) have `create_tx_signature IS
NULL`**, and of those, **14,304 (99.3% of the null subset) have
`pf_ws_creator` populated** — the exact same signature as the 12
launches investigated here (creator captured, CREATE signature lost).
Critically, **all 7 of the 19-cohort's already-*resolved* launches
(X65.1) also show `create_tx_signature IS NULL`** — proving the clobber
bug (Phase 5) is not specific to this 12-launch population at all; it
affects the large majority of migrated tokens system-wide. What makes
this specific 12-launch cohort visibly "stuck" is not that they are
uniquely missing a CREATE signature — nearly all migrated tokens are —
but that they are the subset whose funder wallet *also* has no
independent sub-provisioner/treasury lineage (Phase 3), so they have no
alternate path to attribution the way the 7 resolved launches did via
`wt_attribution_outcomes`/`wt_active_subprov_sessions`.

## Percentage of the original problem this explains

- **100% of the 12 unresolved launches' missing CREATE-signature
  evidence** is explained by a single, precisely-located root cause
  (Phase 5) with high confidence for 10/12 and medium confidence for
  the remaining 2 (log-retention-limited).
- **100% of the 12 unresolved launches' missing funding-lineage
  evidence** is explained as a downstream, fully-expected consequence
  of the funder wallet itself having no indexed lineage — this is not
  a pipeline failure, it is the walkback system correctly reporting
  `INSUFFICIENT_EVIDENCE` for a genuinely un-indexed wallet (Phase 3).
- **0% of the 12 launches' Operation/Funding-Origin attribution gap**
  would be closed by fixing the CREATE-signature clobber alone (Phase
  6) — the fix restores CREATE evidence but does not, by itself,
  create sub-provisioner/treasury lineage for wallets the system has
  never otherwise observed.

## Expected effect of the Phase 7 fix on future launches

- Prevents **future** launches from losing an already-captured
  `create_tx_signature` at migration time — directly closes the root
  cause for the class of launch this investigation targeted.
- Given the bug's much broader footprint (87% of migrated tokens
  system-wide), the fix's downstream benefit extends well past this
  12-launch cohort: `wt_create_event_ledger` and any other consumer
  of `token_analysis.create_tx_signature` would begin seeing a
  correctly-populated signature for the large majority of future
  migrations, not just this specific behaviour/topology combination.
- Does **not** directly increase the count of `KNOWN_TREASURY`
  resolutions — that would require the *separate* work of expanding
  sub-provisioner/treasury coverage (webhook subscription breadth, per
  this project's own prior "cascade-miss-is-subscription-gap" finding)
  to reach these funder wallets, which is out of this task's scope
  (X65.2 was constrained to investigate *why* evidence is missing, not
  to add new detection coverage).

## Performance impact of the proposed fix

Negligible — the fix is a single SQL expression change
(`create_tx_signature=?` → `create_tx_signature=COALESCE(?,
create_tx_signature)`) inside an already-existing `UPDATE` statement
that runs once per migration-time creator-extraction call. No new
query, no new table, no additional round-trip, no change to call
frequency.

## What remains unresolved after this fix (explicitly, not silently)

- The 12 launches investigated here would **not** automatically become
  attributed once the fix ships — the fix prevents the *pattern* from
  recurring for **future** launches; it does not retroactively recover
  these 12 (per Phase 6, recovery requires a separate, explicit,
  RPC-backed action, not performed in this task).
- The 87%-system-wide missing-signature rate itself suggests this bug
  has been present and firing across the *entire* migrated-token
  population for some time — the true historical blast radius (how
  many other Discovery cohorts/attribution gaps trace back to this
  same clobber) was not measured in this task and would need its own
  follow-up scoped explicitly to that question.
