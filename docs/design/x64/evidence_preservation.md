# X64 — Evidence Preservation: What Exists, What Survives, What Requires No New RPC

## What is captured at hop1 (always, regardless of known/unknown)

`_find_with_evidence(creator, rpc, ops, before_signature=create_sig,
source_mint=mint, hop_depth=1)` returns a 6-tuple:
`(hop1, sig1, slot1, bt1, amt1, mech1)` — wallet, signature, slot, block
time, amount (SOL), funding mechanism. This costs RPC (the walk itself),
but that cost is **already paid** by the time the code reaches the
discard point — it is not an *additional* cost of preserving the evidence.

Immediately on a truthy `hop1` (line 943):
```python
_store_funder(ops, mint, hop1, sig1, slot1, bt1, amt1, mech1)
```
This is **unconditional** — it runs before any known/unknown branching, and
writes directly to `wt_walkback_queue`:
```sql
UPDATE wt_walkback_queue SET funder_wallet=?, funder_sig=?, funder_slot=?,
  funder_block_time=?, funder_amount_sol=?, funding_mechanism=?, updated_at=?
WHERE mint=?
```
Confirmed live on the traced case (`CvP9vV…`):
`funder_wallet='DCyQJVfAL37WtcwWAmLNeTatRG553WyfDNytQok41tko'`,
`funding_mechanism='WSOL_WRAP_CLOSE'`, `funder_amount_sol=0.112139`,
`funder_sig='NoK7KdV5UuQS9VLJ7YYf1e35Rgj6s1HR54Ht84hKgWhSkMV4DhynGtvSmHkp9pRwPR9XHdrnU7BNm37ETAjRXHq'`,
`funder_slot=434118204`, `funder_block_time=1784558726`. **All six fields
this audit's question 1 asks about are already in the row.** None of them
were dropped at the `_store_funder` step.

## What is captured next (WSOL_WRAP_CLOSE only)

Lines 946-951:
```python
if mech1 == "WSOL_WRAP_CLOSE" and sig1:
    funding_tx = _get_tx(sig1)      # +1 RPC
    rpc[0] += 1
    _store_close_destination_evidence(
        ops, creator=creator, subprov=hop1, tx=funding_tx,
        signature=sig1, block_time=bt1, amount_sol=amt1)
```
This is the **one and only RPC call** anywhere in this evidence-capture
sequence, and it already runs today, unconditionally on `mech1`, before the
known/unknown branch. `_store_close_destination_evidence` verifies
`_close_account_destination(tx) == creator` and, if true, does:
```sql
INSERT OR IGNORE INTO wt_wrap_close_candidates
    (creator, funding_mechanism, creator_extraction_method,
     subprov_wallet, close_destination, base_amount_sol,
     tx_signature, funded_at, confidence, state, detected_at)
VALUES (?, 'WSOL_WRAP_CLOSE', 'CLOSE_ACCOUNT_DESTINATION',
        ?, ?, ?, ?, ?, 'STRICT', 'WALKBACK_EVIDENCE', ?)
```
This row **already carries** the exact primitive name
(`WSOL_WRAP_CLOSE`), the disposable-subprov wallet (`subprov_wallet=hop1`),
the creator, the signature, the amount, and a timestamp — labeled
`state='WALKBACK_EVIDENCE'` specifically to mark it as review-only,
non-attributing evidence (per its own docstring: "The WALKBACK_EVIDENCE
state is intentionally review-only... does not touch any treasury,
Operator, registry, or attribution table").

**Confirmed for the traced case**: querying
`wt_wrap_close_candidates WHERE creator='71ftvekAkhanTdJJXdZRLtz7Shk...'`
was checked directly as part of this audit's predecessor investigation and
returned zero rows — meaning `_close_account_destination(tx) != creator`
for this specific transaction, i.e. the tx decode did not confirm a clean
wrap-close-to-creator match for this exact case (a validation nuance —
this row-insertion path itself is conditional on the decode succeeding,
independent of the outcome-classification bug this audit is about).

## Answer to Question 6 (evidence available at terminal branch)

At the exact point the code reaches the discard line
(`decision_tree.md`'s `★★★`, walkback_worker.py:1038), the following are
**all already available as local Python variables in the same function
call**, with zero additional RPC required to access them:

| Field | Variable | Already computed by |
|---|---|---|
| Mechanism (`WSOL_WRAP_CLOSE`/`SEEDED_ACCOUNT_CLOSE`) | `mech1` | `_find_with_evidence` at hop1, line 934 |
| Hop1 (disposable subprov) wallet | `hop1` | same |
| Creator | `creator` | function parameter / `_recover_creator_from_db` |
| Mint | `mint` | function parameter |
| Funding signature | `sig1` | `_find_with_evidence` |
| Funding amount | `amt1` | `_find_with_evidence` |
| Funding block time / slot | `bt1` / no slot var here (see note) | `_find_with_evidence` |
| Close-destination evidence | already written to `wt_wrap_close_candidates` (if decode matched) at line 951, before this point | `_store_close_destination_evidence` |

Note: `_find_with_evidence`'s 6-tuple does not include a slot value at
hop1 in this particular call signature (`hop1, sig1, slot1, bt1, amt1,
mech1` — `slot1` IS present in the tuple per the assignment on line
935-936, contrary to the note above; confirmed it is captured and passed
to `_store_funder` as the 3rd positional argument). All fields the audit's
question 6 lists are therefore present with no gap.

**Confirmed: no additional RPC call would be required to preserve this
information in the outcome/subprov fields or in a discovery-lead table.**
The only RPC-consuming step in this whole sequence (`_get_tx(sig1)` for the
WSOL_WRAP_CLOSE tx decode) already runs today, before the discard point —
preserving the evidence downstream of that point is pure control flow: it
means passing `hop1` instead of `None` into `_mark_complete`, and/or adding
a lead-promotion call using data that already exists in local variables.

## What is currently lost, precisely

Not the raw `wt_walkback_queue.funder_wallet`/`funding_mechanism` columns
(those persist regardless, per `_store_funder`'s "always called, even on
NO_ATTRIBUTION_FOUND" docstring comment on line 532). What is lost:

1. **`wt_walkback_queue.subprov`** — stays `NULL` (COALESCE against the
   `None` passed to `_mark_complete`, so even a later completed re-run
   could not backfill it without a code change, since `_mark_complete`'s
   own UPDATE only COALESCEs, it can't overwrite a prior non-NULL value —
   moot here since it's never set to non-NULL in the first place).
2. **`intelligence_outcome`** — recorded as `NO_ATTRIBUTION_FOUND`,
   indistinguishable from Case A (no evidence at all) for every downstream
   reader that filters/displays by this column (dashboards,
   `detection_reconciliation.py`, `funding_boundary_backfill.py`, Discovery
   UI behaviour/topology panels — none of which currently cross-reference
   `funder_wallet`/`funding_mechanism` to recover this distinction).
3. **`wt_discovered_subprovs`** — hop1 never gets a `PROVISION_CANDIDATE`
   row via `_ensure_subprov_lead`, because that call only happens inside
   the `outcome == "LINEAGE_GAP"` branch of `_mark_complete`
   (lines 608-620), which this path never reaches.
4. **`wt_treasury_review`** — no lead is ever surfaced for hop1's own
   funder (there is no hop1 funder to surface — hop2 was not found — but
   also no lead is surfaced for hop1 itself as a *disposable-subprov*
   candidate, since `wt_treasury_review`'s schema and
   `_surface_treasury_review_lead`'s call sites are entirely about
   upstream-of-subprov leads, never about the subprov identity itself; see
   `discovery_gap.md`).

So the loss is entirely in the **classification/discovery layer**, not the
raw evidence layer — but the classification/discovery layer is what every
downstream feature (dashboards, review queues, WATCHTOWER confirmation
funnel) actually reads.
