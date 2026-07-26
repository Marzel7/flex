# X64 — Discovery Lead Audit: Unknown Hop2 vs. Unknown Hop1 Disposable Subprov

## Unknown hop2 — confirmed lead treatment (two separate mechanisms)

**Mechanism 1 — deep-expansion path** (hop1 known-or-unresolved, hop2 found
but unknown, walkback_worker.py:1013-1030):
```python
disp = _surface_treasury_review_lead(
    ops, hop2, hop1, creator, mint, sig2, amt2, mech2)
deep = _expand_unknown_upstream(
    ops, mint=mint, start_wallet=hop2, anchor_signature=sig2,
    rpc_counter=rpc, start_depth=2)
```
`_surface_treasury_review_lead` (line 707) calls
`treasury_bank.add_walkback_hop2_lead(ops, upstream=hop2,
subprov_wallet=hop1, creator_wallet=creator, token_mint=mint,
funding_sig=sig2, funding_amount_sol=amt2, funding_mechanism=mech2)` —
writing hop2 into `wt_treasury_review` as a **treasury candidate**,
explicitly carrying hop1 as its `subprov_wallet` context field. This fires
even if `_expand_unknown_upstream` subsequently resolves the wallet, so the
review lead is created eagerly and unconditionally once hop2 exists and
isn't already a known treasury/subprov.

**Mechanism 2 — inside `_mark_complete`, LINEAGE_GAP branch** (lines
605-625): when the *final* outcome is `LINEAGE_GAP` with a resolved
`subprov` and no `treasury`, two leads are surfaced:
- `_ensure_subprov_lead(ops, subprov, creator, first_seen)` — inserts
  `subprov` (which by this point in the call chain is always hop1, per
  every call site that passes `outcome="LINEAGE_GAP"`) into
  `wt_discovered_subprovs` as `state='PROVISION_CANDIDATE'`.
- If `funder_wallet` (hop1's own funder, read back from the queue row) is
  present and itself unknown, `_surface_treasury_review_lead` is called
  again for that wallet.

**So "unknown hop1" DOES get discovery-lead treatment — but only when the
outcome is LINEAGE_GAP**, which (per `decision_tree.md`) requires hop2 to
have been found (known or unknown) or a resolved-but-not-treasury upstream.
**The one case where hop1 alone is known (mechanism-identified, unknown
identity) and hop2 is never found at all gets neither mechanism.**

## Unknown hop1 with no resolvable hop2 — confirmed NO lead of any kind

Traced exhaustively against every write site in `walkback_worker.py`:

- `_ensure_subprov_lead` is called from exactly one place:
  `_mark_complete`'s `LINEAGE_GAP` branch. Not reachable from the
  `NO_ATTRIBUTION_FOUND` path.
- `_surface_treasury_review_lead` is called from exactly two places: inside
  `_mark_complete`'s `LINEAGE_GAP` branch (for hop1's *funder*, not hop1
  itself), and inside the deep-expansion hop2-found-but-unknown branch (for
  hop2, not hop1). **There is no call site anywhere in the file that
  surfaces hop1 itself as a lead when hop2 was never found.**
- `wt_wrap_close_candidates` (via `_store_close_destination_evidence`) is
  the only place hop1's disposable-subprov role is written when this case
  occurs — and that table is explicitly `state='WALKBACK_EVIDENCE'`,
  separate from the live/production candidate-detection states
  (`DETECTED`/`ARMED`/`FIRED`/`EXPIRED` used by
  `src/ops/watchtower_candidates.py`) and is **not** read by any Discovery
  UI panel or dashboard route found in this session's earlier X63 audit of
  the walkback pipeline (confirmed: `wt_wrap_close_candidates` is read only
  by `walkback_queue.py`'s `classify_creator()` zero-RPC classification
  step and by `watchtower_candidates.py`'s `_handoff_evidence()` — neither
  of which runs again for an already-`complete` queue row).

## Why not: the direct cause

The gate is structural, not a missing feature that was scoped out — it's a
side effect of `_mark_complete`'s single `outcome` parameter driving both
the terminal status string AND the lead-promotion decision. Every call site
that reaches `_mark_complete` with `outcome="NO_ATTRIBUTION_FOUND"` also
passes `subprov=None`, by construction of the two lines that do so (938 and
1038) — there was never a code path written to say "outcome is effectively
unresolved, but a mechanism was still observed at hop1; keep hop1 for lead
purposes even though we won't call this LINEAGE_GAP." The
`NO_ATTRIBUTION_FOUND` outcome was evidently designed for the true
"nothing was found" case (Case A) and reused, by branch fallthrough, for a
materially different case (Case B) that happens to share the same "we
couldn't complete the lineage" shape but not the same evidence state.

## Direct answer to the audit's question 4

**No, unknown hop1 wallets do NOT receive equivalent treatment to unknown
hop2 wallets, in the one specific sub-case where hop1 is unknown AND hop2
is never found.** In every other sub-case (hop1 unknown, hop2 found —
known or unknown), hop1 already does get promoted via the existing
`LINEAGE_GAP` → `_ensure_subprov_lead` path. The gap is narrower than "hop1
is always second-class" — it is specifically "hop1 with no hop2 at all is
the one combination that falls through every lead-promotion mechanism in
the file, despite being the exact shape the X62 disposable-provisioner
model predicts will be common."
