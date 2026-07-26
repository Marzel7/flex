# X65.2 — Phase 7: Permanent Fix Design

Design only — no code in this task was changed. This proposes the
smallest change that prevents future launches from entering the same
unresolved state, targeting the single root cause identified in
Phase 5 (the migration-time creator re-extraction unconditionally
overwriting a valid birth-time `create_tx_signature`).

## The smallest fix: make the migration-time write non-destructive

**File**: `src/core/pumpfun_curve_listener.py`
**Function**: `_update_token_entry_with_creator()` (line 7933),
specifically its `_update_creator_write` closure's `UPDATE` statement
(line 7963).

**Current behavior**: unconditionally sets
`create_tx_signature = ?` using whatever value the caller passed
(`None` whenever the migration-time RPC re-validation doesn't
independently reconfirm the transaction — line 9148), regardless of
what was already stored.

**Proposed change**: apply the same `COALESCE` discipline already used
correctly in `_insert_bonding_curve_token()` (line 5764):

```sql
UPDATE token_analysis SET
    earliest_tx_creator=?,
    created_at=?,
    bonding_curve_pda=?,
    create_tx_signature=COALESCE(?, create_tx_signature),
    cluster_id=?, cluster_name=?, cluster_risk_multiplier=?
WHERE mint=?
```

i.e., only overwrite `create_tx_signature` when the new value is
non-null; never let a `None` from a failed re-validation erase an
existing, already-persisted value. This is a one-line change to a
single `UPDATE` statement's column expression — no schema change, no
new table, no new pipeline stage, no change to the validation logic
itself (the strict `is_pumpfun_create` check at line 9146-9148 stays
exactly as strict as it is today for *setting a new* value; it simply
stops being able to *erase an existing one*).

## Why this is sufficient (and why nothing larger is needed)

- Phase 5 found a single, precisely-located write site responsible for
  100% of the confirmed clobber cases (10/12) and the most likely
  explanation for the remaining 2. Fixing that one site addresses the
  root cause directly rather than adding a parallel/duplicate pipeline.
- The birth-time write path (`_insert_bonding_curve_token`) is already
  correct and needs no change — it already uses `COALESCE` throughout.
- The strict on-chain re-validation at migration time (line 9146-9148)
  is itself a *correct* defensive measure (it prevents ever writing a
  new, unvalidated signature that didn't actually pass the strict
  Pump.Fun CREATE check) — the bug is not in the validation logic
  itself, only in how its `None` result is subsequently written. The
  fix preserves 100% of the existing validation strictness for *new*
  writes; it only prevents that strictness from destroying an
  independently-good prior value.
- No change to `_create_minimal_token_entry()` is needed — it was
  confirmed in Phase 2 to already correctly leave `create_tx_signature`
  untouched via its own column-scoped `INSERT`/`DO UPDATE`.

## Secondary, lower-priority fix (not required to close the primary gap)

Phase 2 also flagged an unrelated type-confusion in the in-process
dedup guard: `pumpfun_curve_listener.py:10816-10817` checks
`mint not in self.completed_launches` but inserts `sig` into that same
set, making the mint-side membership check permanently vacuous. This
does not contribute to the 12-launch pattern investigated here (it
would only matter for genuine duplicate-mint reprocessing, which was
not observed in this cohort) but is a correctness issue worth fixing
alongside the primary change, at effectively zero additional risk:
change line 10817 to `self.completed_launches.add(mint)` (or track
mints and signatures in two separate sets, if both checks are
independently needed elsewhere in the class — not verified in this
task, would need a quick grep of all `completed_launches` reads before
changing).

## Explicitly avoided approaches

- **A new/duplicate "hardened" migration-write pathway**: rejected —
  would create exactly the kind of parallel, disconnected pipeline this
  investigation already found once (`watchtower_attribution.py`'s dead
  `store_migration()`/`intake_migration()` pathway, confirmed to have
  zero callers). The existing live path just needs its one destructive
  statement corrected, not a replacement system.
- **Re-running migration-time creator extraction with retries until
  validation succeeds**: rejected as unnecessary — the birth-time value
  is already correct in the 10 confirmed cases; there is nothing to
  retry toward, the value already existed and was destroyed.
- **Backfilling `wt_create_event_ledger` from `wt_walkback_queue`'s
  `create_anchor_signature` on a schedule**: a reasonable *complementary*
  idea (would have caught `9Mn2t7yX...`'s already-recovered signature
  automatically) but is a distinct enhancement, not required to prevent
  the root-cause failure mode itself — noted here as a candidate for
  separate future work, not designed further in this task.
