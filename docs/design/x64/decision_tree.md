# X64 — Real Decision Tree After Hop1 (FULL_WALKBACK branch)

All line numbers against `src/core/walkback_worker.py` as currently on
disk. This is the literal branch structure, not a paraphrase.

## The real flow (FULL_WALKBACK, lines 915-1039)

```
creator known (or recovered from DB)
        │
        ▼
create_sig = _recover_create_signature_from_db(mint)  (zero-RPC, DB-only)
        │
        ▼
hop1, sig1, slot1, bt1, amt1, mech1 = _find_with_evidence(
    creator, rpc, ops, before_signature=create_sig, source_mint=mint, hop_depth=1)
        │
        ├─► hop1 is falsy (no funder found at all)
        │       └─► _mark_complete(mint, "NO_ATTRIBUTION_FOUND", None, None, rpc[0])
        │           return                                        [line 937-939]
        │
        ▼  (hop1 found — evidence ALWAYS persisted from here on, regardless
        │   of what happens next)
_store_funder(ops, mint, hop1, sig1, slot1, bt1, amt1, mech1)      [line 943]
   → writes wt_walkback_queue.funder_wallet/funder_sig/funder_slot/
     funder_block_time/funder_amount_sol/funding_mechanism — UNCONDITIONAL
        │
        ▼
if mech1 == "WSOL_WRAP_CLOSE" and sig1:
    funding_tx = _get_tx(sig1)          # +1 RPC — the ONLY extra RPC call
                                         # in this whole audit's scope
    _store_close_destination_evidence(...)  [lines 946-951]
       → writes wt_wrap_close_candidates (state='WALKBACK_EVIDENCE') if the
         tx's closeAccount.destination == creator. UNCONDITIONAL on mech1,
         not on hop1's known/unknown status.
        │
        ▼
is hop1 a KNOWN subprov?  (_is_known_subprov — wt_discovered_subprovs,
                            state NOT LIKE 'REJECTED%')
        │
   ┌────┴─────┐
  YES          NO
   │            │
   ▼            ▼
treasury =                 is hop1 a KNOWN treasury?  (_is_known_treasury
  lookup                    — wt_confirmed_treasuries)
   │                              │
   ▼                        ┌─────┴─────┐
outcome =                  YES           NO
 WATCHTOWER_CONFIRMED       │             │
   if treasury else         ▼             ▼
 LINEAGE_GAP          _mark_complete   Hop 2: who funded hop1?
   │                  (WATCHTOWER_       hop2, sig2, ... =
_capture_             CONFIRMED,           _find_with_evidence(hop1, ...,
provisioning_facts    None, hop1, ...)      before_signature=sig1,
   │                  return                 prefer_oldest=True, hop_depth=2)
_mark_complete                                    │
 (outcome, hop1,                    ┌─────────────┼──────────────┐
  treasury, ...,                   hop2 found     hop2 found      hop2 NOT
  confirmed_subprov=True)          & KNOWN         & UNKNOWN       found
   return                          treasury                          │
                                       │              │               ▼
                                       ▼              ▼         ★★★ _mark_complete(
                                 WATCHTOWER_    _surface_treasury_    mint,
                                 CONFIRMED       review_lead(hop2)    "NO_ATTRIBUTION_FOUND",
                                 (hop1,hop2)          +                None,   ← hop1 DROPPED
                                                 _expand_unknown_      None,
                                                 upstream(hop2,...)    rpc[0])
                                                      │                  [line 1038]
                                       ┌──────────────┴──────┐        return
                                    resolved             still
                                    treasury            unresolved
                                       │                    │
                                       ▼                    ▼
                                 WATCHTOWER_          _mark_complete(
                                 CONFIRMED             mint, "LINEAGE_GAP",
                                 (hop1, deep           hop1, None, rpc[0],
                                  .treasury)            confirmed_subprov=False)
                                                        + set_path_state(...)
also: hop2 found & KNOWN subprov (not treasury)
   → treasury = lookup for that subprov
   → outcome = WATCHTOWER_CONFIRMED if treasury else LINEAGE_GAP
   → _mark_complete(outcome, hop1, treasury, ..., confirmed_subprov=False)
```

The `★★★` line is the one this audit is about: **it is the only terminal
branch in the entire `FULL_WALKBACK` path where a fully-resolved,
fully-evidenced hop1 wallet is discarded from the `_mark_complete` call —
passed as `subprov=None` instead of `hop1` — and the outcome string used
(`NO_ATTRIBUTION_FOUND`) is the same one used when hop1 itself was never
found at all** (line 938).

## Assumption sites

Every path that reaches `WATCHTOWER_CONFIRMED` or `LINEAGE_GAP` for hop1
does so through one of exactly three gates:

1. `_is_known_subprov(ops, hop1)` — true only if `hop1` already has a row in
   `wt_discovered_subprovs` (not REJECTED). [line 954]
2. `_is_known_treasury(ops, hop1)` — true only if `hop1` already has a row
   in `wt_confirmed_treasuries`. [line 969]
3. A resolved hop2 — i.e. `hop1`'s *own funder* exists and is itself
   traceable (known subprov/treasury, or resolvable via
   `_expand_unknown_upstream`). [lines 979-1036]

**There is no fourth gate that asks "was hop1's own funding transaction a
`WSOL_WRAP_CLOSE`/`SEEDED_ACCOUNT_CLOSE`?"** — despite that being exactly
the primitive X62 established as the defining WATCHTOWER signature, and
despite the mechanism already being computed (`mech1`) and already used
three lines earlier (line 946) to decide whether to fetch
`_store_close_destination_evidence`.

This is the precise sense in which "known infrastructure" is currently a
prerequisite for evidence to survive into the outcome/subprov fields: a
disposable sub-provisioner is, by the X62 model, expected to be
**mechanism-identifiable before it is identity-known** — often on its very
first (and only) appearance, with no upstream hop2 to walk to (a genuinely
disposable wallet may have been funded by a method or wallet the walk
cannot resolve, or may have received its entire balance in a way that
doesn't produce a clean single hop2 candidate). The current code has no
path for "mechanism known, identity unknown, upstream unresolved" that
preserves anything beyond the raw queue-row columns.

## Case A vs. Case B, traced to the exact same line

- **Case A** (no wrap-close evidence at all — hop1 never found):
  `_mark_complete(ops, mint, "NO_ATTRIBUTION_FOUND", None, None, rpc[0])`
  at **line 938**.
- **Case B** (`WSOL_WRAP_CLOSE` observed at hop1, hop1 unknown, hop2 not
  found): `_mark_complete(ops, mint, "NO_ATTRIBUTION_FOUND", None, None,
  rpc[0])` at **line 1038**.

Different line numbers, **identical call signature** — same outcome
string, same `subprov=None`, same `treasury=None`. The only difference
between these two calls anywhere in the database is whatever was already
written to `wt_walkback_queue.funder_wallet`/`funding_mechanism` by
`_store_funder` (Case A never calls it — hop1 is falsy; Case B does, at
line 943) and to `wt_wrap_close_candidates` (Case B only, if the tx
decode confirms `close_destination == creator`).
