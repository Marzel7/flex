# X65.2 — Phase 7: Permanent Fix

Root cause (Phase 6) is **Mixed Causes**, with a dominant, fully-explanatory
capture/persistence defect plus a secondary instability contributor.
Per the task's guidance, this maps to a **capture correction** (the
issue is neither purely historical nor purely an indexing gap — it is
a live persistence bug in the capture pipeline's write path).

## Primary fix: smallest capture correction

**File**: `src/core/pumpfun_curve_listener.py`
**Function**: `_update_token_entry_with_creator()` (line 7933), its
`UPDATE` statement (line 7963).

**Current behavior**: unconditionally sets `create_tx_signature = ?`
using whatever value the caller passed — `None` whenever the
migration-time RPC re-validation doesn't independently reconfirm the
CREATE transaction (line 9148) — overwriting an already-correct
birth-time signature.

**Fix**: apply the same non-destructive `COALESCE` discipline already
used correctly in the birth-time write path
(`_insert_bonding_curve_token()`, line 5764):

```sql
UPDATE token_analysis SET
    earliest_tx_creator=?,
    created_at=?,
    bonding_curve_pda=?,
    create_tx_signature=COALESCE(?, create_tx_signature),
    cluster_id=?, cluster_name=?, cluster_risk_multiplier=?
WHERE mint=?
```

One-line change to a single `UPDATE` statement's column expression.
No schema change, no new table, no new pipeline stage. The existing
strict on-chain re-validation (line 9146-9148) is untouched — it
remains exactly as strict for *writing a new* signature; it simply can
no longer *erase an existing one*.

## Why this is the smallest correction and not an indexing/backfill fix

Phase 6 determined the defect sits in the **capture/persistence**
layer (a live write overwriting already-captured evidence), not in
indexing (`wt_create_event_ledger`/`wt_active_subprov_sessions` behave
correctly given what they receive) or in walkback (which completes
correctly). A bounded historical replay/backfill is therefore **not**
the primary recommendation here — recovering the already-lost 12
signatures would not prevent the next 12 from being lost the same way.
The capture-layer fix is the only change that stops recurrence at its
source.

## Secondary recommendation: address the listener instability separately (not designed here)

Phase 5/6 found chronic `watchtower_listener` restart-looping (3,224
restarts across the investigated window) as a corroborating, additive
factor for roughly 8 of the 12 launches. This is a distinct
operational-stability issue (process exiting with status 1
unexpectedly, on a ~6-minute median cadence) with its own separate
root cause not investigated in this task (the crash reason itself was
out of scope — this task investigated evidence disappearance, not
process-crash causes). Recommending: a **separate, future-scoped**
investigation into why `watchtower_listener` exits unexpectedly this
often, since fixing the capture defect above does not address this
independent stability problem, and a sufficiently unstable process
could still cause other, different evidence-loss patterns even after
the CREATE-signature clobber is fixed. Not designed further here to
avoid scope creep beyond this task's mandate.

## No bounded replay/backfill recommended for the already-affected 12

Per this task's own Phase 6 (recoverability) finding from the prior
investigation pass: recovering `create_tx_signature` for these 12
launches would not, by itself, resolve their Funding Origin/Operation
Attribution gap (a separate, independent finding — their funder
wallets have no sub-provisioner/treasury lineage regardless of CREATE
evidence). A backfill limited to CREATE-ledger population is possible
in principle (e.g., propagating `9Mn2t7yX...`'s already-recovered
`wt_walkback_queue.create_anchor_signature`, or a fresh mint-keyed RPC
lookup for the other 11) but is explicitly **not required** to prevent
recurrence, and this task's scope is the permanent fix, not recovery
of the existing 12 — consistent with "Do not change attribution logic"
and "avoid duplicate pipelines."

## Explicitly avoided approaches

- **A new/duplicate "hardened" migration-write pathway**: rejected —
  would recreate a parallel, disconnected pipeline (this investigation
  already found one dead example: `watchtower_attribution.py`'s
  `store_migration()`/`intake_migration()`, zero callers). The
  existing live path needs its one destructive statement corrected,
  not a replacement system.
- **New treasury heuristics or attribution-logic changes**: none
  proposed, per the task's explicit constraint — this fix is entirely
  scoped to the write-path defect and touches no attribution,
  classification, or treasury-confirmation logic.
- **Backfilling `wt_create_event_ledger` on a schedule**: a reasonable
  complementary idea (would retroactively catch the already-recovered
  `9Mn2t7yX...` anchor), but a distinct enhancement, not required to
  close the primary gap — noted as a candidate for separate future
  work only.
