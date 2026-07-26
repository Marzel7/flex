# X64 — Recommended Changes

**Status: IMPLEMENTED.** This document originally evaluated and
recommended (audit-only) a fix; a follow-up task
("X64 — Implement Disposable Sub-Provisioner Evidence Preservation")
explicitly authorized implementation. Option 1 below was implemented
exactly as described, plus an evidence-level refinement (STRICT vs.
MECHANISM_ONLY) discovered necessary during implementation — see
"Evidence-strength nuance discovered" at the end of this document. The
original recommendation text is preserved below for audit-trail fidelity;
implementation details and the historical-impact dry-run report follow.

## What should change, conceptually

The single terminal branch identified in `decision_tree.md` (walkback_worker.py:1037-1038):

```python
else:
    # hop1 unknown, hop2 not found — no confirmation possible
    _mark_complete(ops, mint, "NO_ATTRIBUTION_FOUND", None, None, rpc[0])
```

should instead preserve hop1 and its observed mechanism, using a
classification that is honest about two separate facts that are currently
conflated:
1. Lineage reconstruction did not complete (no confirmed treasury/subprov
   chain) — this fact should still gate `WATCHTOWER_CONFIRMED`, unchanged.
2. A concrete, on-chain-observed WATCHTOWER primitive (the
   `WSOL_WRAP_CLOSE`/`SEEDED_ACCOUNT_CLOSE` handoff) WAS directly witnessed
   at hop1 — this fact should not be silently dropped just because fact #1
   is also true.

The audit's own example shape is apt and requires no invention beyond what
the code already computes:
```
LINEAGE_GAP
Evidence: EPHEMERAL_WSOL_CREATOR_HANDOFF
Variant: WSOL_WRAP_CLOSE
Disposable Subprov: <hop1>
Termination: UPSTREAM_UNRESOLVED
```

## Smallest viable code change

Two options, not mutually exclusive, both scoped entirely to
`src/core/walkback_worker.py`'s `FULL_WALKBACK` branch — no schema change,
no new table, no new RPC:

### Option 1 — reclassify the terminal branch as LINEAGE_GAP with hop1 preserved
Change line 1038 from:
```python
_mark_complete(ops, mint, "NO_ATTRIBUTION_FOUND", None, None, rpc[0])
```
to:
```python
_mark_complete(ops, mint, "LINEAGE_GAP", hop1, None, rpc[0], confirmed_subprov=False)
```
This alone is sufficient to activate the **already-existing**
`_ensure_subprov_lead`/`_surface_treasury_review_lead` machinery inside
`_mark_complete` (lines 605-625) — no new function is needed, because that
machinery already does the right thing for a resolved-but-unconfirmed
`subprov`, it just was never being reached for this specific case. This is
a one-line change (the string `"NO_ATTRIBUTION_FOUND"` → `"LINEAGE_GAP"`
and `None` → `hop1` in the same call).

Consequence check: `_mark_complete`'s attribution-table write
(`watchtower_token_attribution`) is gated on `confirmed_subprov or
treasury` (line 590) — with `confirmed_subprov=False` and `treasury=None`
here, that INSERT is correctly skipped, so this change does **not** cause
any new row to be written as confirmed attribution. `WATCHTOWER_CONFIRMED`
remains reachable only through the existing known-subprov/known-treasury/
resolved-hop2 gates — this recommendation does not touch those.

### Option 2 — add an explicit termination-reason / evidence marker
For closer alignment with the audit's example (distinguishing this case
from an ordinary `LINEAGE_GAP` where hop2 was found-but-unresolved), reuse
the existing `deep_walkback.set_path_state` mechanism (already used at
line 1028 for the deep-expansion LINEAGE_GAP case) to record something like
`termination_reason_json={"reason": "UPSTREAM_UNRESOLVED",
"evidence": "EPHEMERAL_WSOL_CREATOR_HANDOFF", "variant": mech1,
"disposable_subprov": hop1}` alongside the Option-1 change. This uses a
column (`termination_reason_json`) that already exists in the live schema
(confirmed present in the X63 schema audit) and is already written by the
same module for a structurally identical purpose, so no new schema work is
required — only a new call using data already in scope.

Both options together are still a small, localized change: no new tables,
no new RPC, no change to the known-subprov/treasury gates that already
correctly govern `WATCHTOWER_CONFIRMED`.

## What this recommendation deliberately does NOT do

- It does not make `WSOL_WRAP_CLOSE` mechanism detection alone sufficient
  for `WATCHTOWER_CONFIRMED`. Confirmation still requires a resolved
  treasury/subprov identity, exactly as today.
- It does not change `_is_known_subprov`/`_is_known_treasury` semantics.
- It does not change Case A (hop1 never found at all) — that remains
  `NO_ATTRIBUTION_FOUND` with no evidence to preserve, correctly.
- It does not touch the other `NO_ATTRIBUTION_FOUND` call site at line 938
  (hop1 not found) or line 1039's sibling paths for `PARTIAL_TREASURY`/
  `PARTIAL_SUBPROV`, whose existing behavior already correctly distinguishes
  found-vs-not-found at their own hop1.
- It does not affect the X63-documented `wt_watchtower_candidates`
  pre-detection gap — that is a separate, earlier-stage issue (the
  `classify_quick_birth_migration` timing gate) unrelated to this
  worker-side classification branch.

## Direct answers to the audit's success-criteria questions

1. **Is a previously known sub-provider currently required before
   WATCHTOWER evidence is preserved?** Raw evidence (queue-row funder
   columns, `wt_wrap_close_candidates`) — no, always written. Discovery-lead
   / outcome-classification evidence — yes, in the one specific combination
   where hop1 is unknown and hop2 is never found; every other combination
   already promotes hop1 correctly.
2. **Are disposable sub-provisioners incorrectly treated as unknown
   noise?** Yes, in that one combination — they terminate with the exact
   same outcome string and `subprov=NULL` as a launch with zero WATCHTOWER
   evidence at all.
3. **Where is the WSOL handoff evidence lost?** `src/core/walkback_worker.py:1038`
   — the `_mark_complete` call in the final `else` of the `FULL_WALKBACK`
   branch, which passes `subprov=None` instead of the already-resolved
   `hop1`, and uses `outcome="NO_ATTRIBUTION_FOUND"` instead of
   `"LINEAGE_GAP"`.
4. **Can that evidence be preserved without additional RPC?** Yes,
   confirmed in `evidence_preservation.md` — every field needed is already
   a local variable at that point in the function; the one RPC call this
   sequence uses (`_get_tx(sig1)` for the wrap-close decode) already runs
   earlier in the same invocation, unconditionally.
5. **Should unknown disposable sub-provisioners become discovery leads in
   the same way unknown hop2 wallets already do?** Yes — and the mechanism
   to do so (`_ensure_subprov_lead`, called from `_mark_complete`'s
   `LINEAGE_GAP` branch) already exists and already does exactly this for
   hop1 in every other reachable case; it only needs to be reached from
   this one remaining branch.
6. **What is the smallest code change required?** A one-line change at
   `walkback_worker.py:1038` — swap `"NO_ATTRIBUTION_FOUND"` for
   `"LINEAGE_GAP"` and `None` for `hop1` in the existing `_mark_complete`
   call (Option 1 above) — which activates already-existing discovery-lead
   logic with zero new functions, zero new RPC, and zero change to the
   `WATCHTOWER_CONFIRMED` gating logic. Option 2 (explicit termination-
   reason tagging) is an optional, additive refinement on top, using an
   already-existing schema column and an already-existing helper
   (`deep_walkback.set_path_state`) called elsewhere in the same file for
   the same purpose.

---

## Implementation record

### What was actually changed
`src/core/walkback_worker.py`, two edits, both confined to the
`FULL_WALKBACK` branch:

1. A new module-level predicate:
   ```python
   _DISPOSABLE_HANDOFF_MECHANISMS: frozenset[str] = frozenset({
       "WSOL_WRAP_CLOSE", "SEEDED_ACCOUNT_CLOSE",
   })

   def _is_disposable_subprov_handoff(mechanism: Optional[str]) -> bool:
       return mechanism in _DISPOSABLE_HANDOFF_MECHANISMS
   ```
2. The final `else` of the hop2-search block (previously
   `_mark_complete(ops, mint, "NO_ATTRIBUTION_FOUND", None, None, rpc[0])`)
   now branches on `_is_disposable_subprov_handoff(mech1)`: if true,
   `_mark_complete(ops, mint, "LINEAGE_GAP", hop1, None, rpc[0],
   confirmed_subprov=False)` plus a structured
   `[WALKBACK_DISPOSABLE_SUBPROV_LEAD]` log line; if false, the original
   `NO_ATTRIBUTION_FOUND` call is unchanged, verbatim.

No new function signature changed, no schema/table/index was added, no
new RPC call was introduced — `_is_disposable_subprov_handoff` reads only
`mech1`, a value `_find_with_evidence` already returned earlier in the
same `_process_row` invocation.

### Evidence-strength nuance discovered around WSOL_WRAP_CLOSE
The audit's own concern was correct and had to be resolved before
implementing: `_detect_mechanism` (the function that assigns
`mech1 == "WSOL_WRAP_CLOSE"`) is **looser** than the strict
`closeAccount.destination == creator` check that
`_store_close_destination_evidence` separately performs. `_detect_mechanism`
returns `WSOL_WRAP_CLOSE` on any of three signals: (1) a parsed
`closeAccount` instruction anywhere in the transaction (the strict case —
matches `_close_account_destination`'s own logic), (2) a log-message
substring match for `"token"` (case-insensitive — a much weaker heuristic
that does not confirm any close instruction at all), or (3) any
instruction whose `programId` contains `"Token"`. None of the three checks
`receiver == creator` — that verification only happens later, inside
`_store_close_destination_evidence`, and only when `mech1 ==
"WSOL_WRAP_CLOSE"` already triggered the tx re-fetch.

**Resolution implemented: Design B.** The tx re-fetch and
`_store_close_destination_evidence` call already existing at this point
in the function (lines ~968-974 post-edit) were extended to capture their
own boolean result (`stored_strict`) into a new local,
`hop1_evidence_level`, set to `"STRICT"` when
`_store_close_destination_evidence` actually confirmed the close
destination, `"MECHANISM_ONLY"` otherwise (including the
`SEEDED_ACCOUNT_CLOSE` mechanism, which has no equivalent strict-decode
step in the current code at all). This required **zero additional RPC** —
`_get_tx(sig1)` was already being called unconditionally at this point
before the fix; only its already-computed return value is now retained
and labeled. `hop1_evidence_level` is included in the
`[WALKBACK_DISPOSABLE_SUBPROV_LEAD]` log line so a reviewer can
distinguish the two confidence tiers without a new query. It is **not**
written into `wt_discovered_subprovs`/`wt_walkback_queue` as a new column
— logging only, to honor "no schema change."

Live-data check on the traced case (`CvP9vV…`): its own
`wt_wrap_close_candidates` row was confirmed absent in the earlier audit
(the strict decode did not produce a matching row for this specific tx),
so this mint is itself an example of a `MECHANISM_ONLY` classification —
correctly still promoted to a `LINEAGE_GAP` discovery lead under this
fix (mechanism evidence alone is sufficient for the *lead*; it remains
insufficient, by design, for `WATCHTOWER_CONFIRMED`).

### Tests
Added `tests/test_x64_disposable_subprov_evidence.py` — 15 tests, all
passing:
- 5 unit tests for `_is_disposable_subprov_handoff` (both qualifying
  mechanisms, `PLAIN_XFER`, `UNKNOWN`, `None`).
- Test 1 — no hop1 → `NO_ATTRIBUTION_FOUND`, unchanged.
- Test 2 — qualifying `WSOL_WRAP_CLOSE`, no hop2 → `LINEAGE_GAP`,
  `subprov=hop1`, lead created, zero confirmed attribution, exactly 1 RPC
  call (the pre-existing close-destination re-fetch).
- Test 3 — ordinary `PLAIN_XFER`, no hop2 → `NO_ATTRIBUTION_FOUND`,
  unchanged, no lead created.
- Test 4 — known subprov hop1 → unchanged existing routing.
- Test 5 — hop2 found but unresolved → unchanged existing deep-expansion
  routing (`_surface_treasury_review_lead`/`_expand_unknown_upstream`
  still invoked, confirmed via mock assertion).
- Test 6 — attribution safety: zero rows in `watchtower_token_attribution`
  from the mechanism-only path.
- Test 7 — RPC-count assertion: `_find_with_evidence` called exactly
  twice (hop1 + the pre-existing hop2 attempt), `_get_tx` called exactly
  once (the pre-existing close-destination re-fetch) — the classification
  branch itself adds zero calls.
- Test 8 — idempotency: calling `_mark_complete` twice with identical
  `LINEAGE_GAP`/hop1 inputs produces exactly one lead row, no downgrade,
  zero confirmed attribution. A companion test also documents (not
  asserts as a bug) the pre-existing, unchanged routing behavior if the
  same mint's row were somehow reprocessed after its hop1 wallet was
  already promoted to a lead: `_is_known_subprov(hop1)` then correctly
  returns `True` and the pre-existing known-subprov branch is taken
  instead — this is not new behavior introduced by this fix.
- Final regression test — zero-RPC replay of the exact traced mint
  (`CvP9vVUCpoDuMd2jg5qvakFsk8Ht4qQwmKtZTMeUpump`, hop1
  `DCyQJVfAL37WtcwWAmLNeTatRG553WyfDNytQok41tko`,
  `mech1='WSOL_WRAP_CLOSE'`) using its already-known stored field values as
  a fixture, no new RPC issued: confirms `LINEAGE_GAP`, `subprov=hop1`,
  a `wt_discovered_subprovs` lead row, and zero
  `watchtower_token_attribution` rows.

Full regression run: all 71 pre-existing walkback-related tests
(`pytest -k walkback`, excluding 3 unrelated pre-existing collection
errors in unrelated modules) pass unchanged, plus a combined
walkback/x64/subprov/discovery run of 304 tests shows exactly 3 failures —
all 3 confirmed pre-existing and unrelated (stale HTML-content string
assertions against `templates/discovery.html`, a file this change never
touches; reproduced identically on the unmodified tree via a
`git stash`/`git stash pop` A-B comparison).

### Confirmations against the task's required deliverables
1. **RPC behaviour unchanged**: confirmed by Test 7 and by direct code
   inspection — `_is_disposable_subprov_handoff` performs a single
   in-memory set-membership check, no I/O.
2. **`WATCHTOWER_CONFIRMED` gating unchanged**: `_mark_complete`'s
   attribution-table write remains gated on `confirmed_subprov or
   treasury` (unmodified); the new call always passes
   `confirmed_subprov=False, treasury=None`, so it is structurally
   incapable of writing confirmed attribution. Confirmed by Test 6 and
   the final regression test.
3. **Existing discovery-lead mechanism reused, not duplicated**: no new
   function was added for lead promotion — the existing
   `_ensure_subprov_lead` call inside `_mark_complete`'s `LINEAGE_GAP`
   branch is simply reached from one more call site.
4. **Dry-run historical impact report**: see below.

### Historical impact — dry-run only, no rows modified
Query run against live `database/wt_ops_v2.db` (read-only,
`sqlite3`), matching the pattern specified in the implementation task:
```sql
SELECT COUNT(*) FROM wt_walkback_queue
WHERE status='complete' AND intelligence_outcome='NO_ATTRIBUTION_FOUND'
  AND funder_wallet IS NOT NULL
  AND funding_mechanism IN ('WSOL_WRAP_CLOSE','SEEDED_ACCOUNT_CLOSE')
  AND subprov IS NULL AND treasury IS NULL;
```
**Result: 634 historical rows** match the misclassification pattern this
fix corrects going forward. All 634 have `funding_mechanism='WSOL_WRAP_CLOSE'`
— zero rows in the current dataset have `funding_mechanism=
'SEEDED_ACCOUNT_CLOSE'` combined with this exact NO_ATTRIBUTION_FOUND/
NULL-subprov/NULL-treasury shape (a distinct, narrower finding from the
earlier X63 audit's aggregate mechanism counts, which covered a different
table). **542 distinct `funder_wallet` values** are represented (multiple
mints frequently funded by the same disposable wallet). Of those 542, **39
already have an existing `wt_discovered_subprovs` row** (from some other
code path reaching `LINEAGE_GAP` for the same wallet via a different
mint/hop2 combination) — meaning roughly 503 distinct wallets would be
newly surfaced as discovery-lead candidates if this reconciliation were
run. The confirmed traced mint (`CvP9vVUCpoDuMd2jg5qvakFsk8Ht4qQwmKtZTMeUpump`)
is present in this list, consistent with the earlier audit.

**Per the task's explicit instruction, no historical row was modified.**
This section reports the dry-run count/list only. A separate,
explicitly-authorized reconciliation task would be required to backfill
these 634 rows — likely as a zero-RPC pass re-running the same
`_is_disposable_subprov_handoff(funding_mechanism)` check directly
against the stored `wt_walkback_queue` columns and calling the same
`_mark_complete`/`_ensure_subprov_lead` path, entirely from already-stored
data.
