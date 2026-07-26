# X64 — Disposable Sub-Provisioner Misclassification: Master Audit

Read-only, code-grounded audit of `src/core/walkback_worker.py`'s post-hop1
decision logic. No code was modified. Companion documents: `decision_tree.md`,
`evidence_preservation.md`, `discovery_gap.md`, `recommended_changes.md`.

This audit was triggered by a concrete, directly-traced real-world case:
mint `CvP9vVUCpoDuMd2jg5qvakFsk8Ht4qQwmKtZTMeUpump`, creator
`71ftvekAkhanTdJJXdZRLtz7ShkXxdAxhmVmyv2YVSFS`. Its `wt_walkback_queue` row
shows `walkback_class='FULL_WALKBACK'`, `status='complete'`,
`intelligence_outcome='NO_ATTRIBUTION_FOUND'`, `subprov=NULL`,
`treasury=NULL`, `rpc_used=16` — yet the same row's `funder_wallet` /
`funding_mechanism` columns show
`funder_wallet='DCyQJVfAL37WtcwWAmLNeTatRG553WyfDNytQok41tko'`,
`funding_mechanism='WSOL_WRAP_CLOSE'`. The walk genuinely observed the
WATCHTOWER wrap-close primitive at hop1 and then discarded it from the
outcome/subprov fields entirely. This audit traces exactly how and confirms
it is a systemic branch, not a one-off.

## Answer to the audit's core question

**Yes — the confirmed finding is that `_mark_complete` only promotes hop-1
evidence into a discovery lead when `outcome == "LINEAGE_GAP"`, and the
current `FULL_WALKBACK` branch only reaches `LINEAGE_GAP` for hop1 if hop2
is found but hop2's own funder is unknown/unresolved through
`_expand_unknown_upstream`. If hop2 is not found at all (`hop2` is falsy —
i.e. no upstream funding transaction exists for the disposable
sub-provisioner, which is expected and common for a wallet that is
intentionally short-lived and near-empty before/after the handoff), the
code takes the `else` branch at
[walkback_worker.py:1037-1038](../../../src/core/walkback_worker.py#L1037-L1038):
`_mark_complete(ops, mint, "NO_ATTRIBUTION_FOUND", None, None, rpc[0])` —
passing `subprov=None`, discarding the hop1 wallet from the call entirely,
even though it is sitting in a local variable (`hop1`) with full evidence
already persisted via `_store_funder`/`_store_close_destination_evidence`
a few lines earlier in the same function invocation.**

This confirms the audit's stated concern precisely: **a previously unknown
sub-provisioner is currently required, in effect, for WATCHTOWER evidence to
survive into the outcome classification** — not because the code explicitly
checks "is this wallet known" before storing evidence (it does store it
unconditionally via `_store_funder`), but because the *outcome*/`subprov`
fields written by `_mark_complete`, and the entire discovery-lead promotion
gate inside it, are only reachable through paths that already presuppose a
resolved hop2. A disposable, single-use sub-provisioner with no discoverable
upstream funder (by design, the common case per the X62 primitive) never
reaches any of those paths.

## Summary of all five sub-questions (detail in companion docs)

1. **Hop1 behaviour** (`evidence_preservation.md`): wallet, mechanism,
   amount, signature, slot, and timestamp are all captured and written to
   `wt_walkback_queue` (`funder_wallet`, `funding_mechanism`,
   `funder_amount_sol`, `funder_sig`, `funder_slot`, `funder_block_time`) via
   `_store_funder`, unconditionally, before any known/unknown branching.
   Additionally, if `mech1 == "WSOL_WRAP_CLOSE"`, a second write happens via
   `_store_close_destination_evidence` into `wt_wrap_close_candidates` with
   `state='WALKBACK_EVIDENCE'`. **Both of these succeed regardless of what
   happens next.** Nothing about the queue-row/candidate-table writes is
   discarded. What *is* discarded is the `intelligence_outcome` and
   `wt_walkback_queue.subprov` fields, and — critically — the discovery-lead
   promotion into `wt_discovered_subprovs` that would otherwise make this
   wallet visible in the Unknown-Treasury Sub-Provisioners panel.

2. **Decision logic** (`decision_tree.md`): the real flow, traced exactly
   against the current `FULL_WALKBACK` branch
   ([walkback_worker.py:915-1039](../../../src/core/walkback_worker.py#L915-L1039)).

3. **Evidence loss** (`evidence_preservation.md`): Case A (no WSOL evidence
   at all — hop1 itself never found) and Case B (WSOL_WRAP_CLOSE observed at
   hop1, hop1 unknown, hop2 not found) **do produce an identical terminal
   outcome string, `NO_ATTRIBUTION_FOUND`, and an identical `subprov=NULL`
   in the `wt_walkback_queue.subprov` column and in the `_mark_complete`
   call itself.** They are NOT identical in the raw table data — Case B
   still has `funder_wallet`/`funding_mechanism` populated and a
   `wt_wrap_close_candidates` row — but every downstream consumer that reads
   `intelligence_outcome` or `subprov` (dashboards, `detection_reconciliation.py`,
   `funding_boundary_backfill.py`, the Discovery UI's behaviour/topology
   panels) cannot distinguish the two cases without a raw join against
   `funder_wallet`/`funding_mechanism`, which none of those consumers
   currently perform for this purpose.

4. **Discovery lead audit** (`discovery_gap.md`): unknown hop2 wallets
   (when hop1 IS resolved as a known subprov, or when a deep-expansion path
   is walked) get `_surface_treasury_review_lead` → `wt_treasury_review`.
   Unknown hop1 wallets with no resolvable hop2 get **no equivalent lead of
   any kind** — not `wt_discovered_subprovs`, not `wt_treasury_review`. The
   `_ensure_subprov_lead` call inside `_mark_complete` is gated on
   `outcome == "LINEAGE_GAP"`, which this path never reaches.

5. **Architectural assumption** (`decision_tree.md` §"Assumption sites"):
   every `WATCHTOWER_CONFIRMED`/`LINEAGE_GAP` outcome in the `FULL_WALKBACK`
   branch is reached only via `_is_known_subprov(hop1)` or
   `_is_known_treasury(hop1)` being true, OR via a resolved (even if
   unknown) hop2. There is no branch that treats "hop1's own *funding
   mechanism* was `WSOL_WRAP_CLOSE`" as evidence in its own right,
   independent of whether hop1's *identity* was previously known. This is
   the exact assumption the X62 primitive breaks: disposable
   sub-provisioners are, by design, mechanism-identifiable (via the
   wrap-close pattern) before they are ever identity-known.

6. **Evidence preservation without RPC** (`evidence_preservation.md`):
   confirmed — every field listed in the audit's question 6
   (`WSOL_WRAP_CLOSE`/`SEEDED_ACCOUNT_CLOSE` mechanism, hop1 wallet,
   creator, mint, funding signature, funding amount, close-destination
   evidence) is already present in local variables (`hop1`, `sig1`, `bt1`,
   `amt1`, `mech1`, `creator`, `mint`) at the exact point the code currently
   discards them (line 1038). **No additional RPC call is required** — this
   is a pure control-flow/classification change, not a data-availability
   gap.

7. **Recommended behaviour** (`recommended_changes.md`): preserve the
   observed primitive as a distinct, non-attributing outcome/lead rather
   than collapsing it into `NO_ATTRIBUTION_FOUND`, gated so that
   WATCHTOWER_CONFIRMED still requires a resolved lineage — this is
   explicitly NOT a request to auto-confirm on mechanism alone.
