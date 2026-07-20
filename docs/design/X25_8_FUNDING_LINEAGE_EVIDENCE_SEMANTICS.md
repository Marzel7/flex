# X25.8 — Funding Lineage Evidence Semantics

Status: Audit complete. **This audit found the current wording overstates
certainty** — see Phase 4/Recommendation. No code has been changed as part
of writing this document; the one recommended wording correction is
flagged for a follow-up sprint, per the brief's "only recommend wording
changes if objectively necessary" instruction (the sprint's own scope
statement is to define/verify, not necessarily to also implement).

---

## Phase 1 — Trace of actual backend logic (not inferred)

Traced end-to-end: `src/core/walkback_worker.py` (`_process_row`, the
`PARTIAL_TREASURY`/`PARTIAL_SUBPROV`/`FULL_WALKBACK` branches) →
`wt_walkback_queue.intelligence_outcome` → `src/ops/detection_reconciliation.py`
(`classify_walkback_confirmed_launches`, `_CONFIRMED_WALKBACK_OUTCOMES`) →
`templates/discovery.html` (`detectionReconciliation()`).

**Critical finding: the walk is a fixed 1-2 hop probe, never a
full-depth trace to origin.**

- `PARTIAL_TREASURY` (subprov known): **1 hop** from subprov. If that hop
  lands on a wallet already in `wt_confirmed_treasuries` → `WATCHTOWER_CONFIRMED`.
  Otherwise → `LINEAGE_GAP`.
- `PARTIAL_SUBPROV` (creator known, subprov missing): **1 hop** from creator.
  If that hop is a known subprov (`wt_discovered_subprovs`) with a
  `treasury` field set → `WATCHTOWER_CONFIRMED`; if the subprov has no
  treasury on file → `LINEAGE_GAP`. If the hop is itself directly a known
  treasury → `WATCHTOWER_CONFIRMED`. If the hop resolves to nothing (no
  signatures found) → `NO_ATTRIBUTION_FOUND`.
- `FULL_WALKBACK` (creator only, or nothing known): **hop 1** = funder of
  creator; **hop 2** = funder of hop 1, only reached if hop 1 was not
  itself already a known subprov/treasury. Confirmation triggers the
  instant either hop lands on `wt_confirmed_treasuries` or a
  `wt_discovered_subprovs` row with a `treasury` set. **The walk never
  proceeds past hop 2, and never walks upstream of a confirmed treasury to
  discover how that treasury itself was funded.**

Verified directly against `_is_known_treasury()`/`_is_known_subprov()`
(`walkback_worker.py:349-358`): both are simple existence checks against
`wt_confirmed_treasuries`/`wt_discovered_subprovs` — **the moment either
check succeeds, the walk stops and marks `WATCHTOWER_CONFIRMED`.** No
additional hop is ever taken beyond that point, regardless of how many more
hops might exist between that treasury and its own ultimate funding origin.

**Per-state stopping condition / terminating entity / evidence table:**

| Outcome | Stopping condition | Terminating entity | Required evidence | Optional evidence | Missing evidence |
|---|---|---|---|---|---|
| `WATCHTOWER_CONFIRMED` | A hop (1 or 2) lands on a wallet already in `wt_confirmed_treasuries`, or a `wt_discovered_subprovs` row with `treasury` set | A confirmed treasury wallet (never traced further) | The 1-2 on-chain funding hop(s) actually walked | — | Everything upstream of the confirmed treasury; the treasury's own funding origin |
| `LINEAGE_GAP` | A hop resolves to a real wallet, but that wallet is not in `wt_confirmed_treasuries`/`wt_discovered_subprovs` (or a discovered subprov has no treasury on file) | An unconfirmed wallet | The hop(s) actually walked | A "treasury review lead" may be surfaced for analyst follow-up | Whether the unconfirmed wallet would itself resolve to a confirmed treasury if walked further |
| `NO_ATTRIBUTION_FOUND` | `_find_funder_via_rpc` returns no signatures at all for the wallet being probed | None (walk terminates on absence of data) | None beyond "no signatures returned" | — | Whether this is genuinely "no funder exists" vs. an RPC/window limitation (see Phase 5) |

## Phase 2 — Every terminal boundary currently in the system

| Boundary | Where checked | Counts as `WATCHTOWER_CONFIRMED`? |
|---|---|---|
| Confirmed treasury (`wt_confirmed_treasuries`) | `_is_known_treasury()`, both `PARTIAL_TREASURY` and `FULL_WALKBACK` paths | **Yes** |
| Known subprov with a treasury on file (`wt_discovered_subprovs.treasury`) | `_is_known_subprov()` + treasury lookup | **Yes** (measured: 825/1234 discovered subprovs have this set, and 100% of those treasuries are themselves confirmed — verified live) |
| Known subprov with **no** treasury on file | Same lookup, `treasury` is `NULL` | **No** — produces `LINEAGE_GAP` |
| Relay / bridge / exchange (CEX) wallet | **Not checked by the walkback worker at all.** These are a separate, later classification performed by `src/ops/attribution_outcome.py`'s `_boundary()` function, against `INFRASTRUCTURE_ACCOUNTS`/`CEX_ACCOUNTS`/`address_labels`, applied to whatever terminal wallet the walk already stopped at (`creator`/`terminal` from the queue row) | **No** — `attribution_outcome.py` assigns `KNOWN_RELAY_REACHED`/`KNOWN_BRIDGE_REACHED`/`KNOWN_CEX_REACHED`, entirely independent of, and downstream from, the `WATCHTOWER_CONFIRMED`/`LINEAGE_GAP` classification `detection_reconciliation.py` reads |
| Treasury mesh (multi-treasury Operation Identity, X25.4) | Not evaluated by the walkback worker at all — Operation Identity is a separate, post-hoc read-only resolver over confirmed treasuries | **No direct relationship** — a confirmed treasury reached by walkback may or may not turn out to belong to a multi-treasury operation; that's determined afterward by `operation_identity.py`, never by the walk itself |
| Distribution hub / automation wallet | Same as relay/bridge/exchange — a later, separate `attribution_outcome.py` classification, not a walkback-time boundary | **No** |
| Known operator wallet | Not checked directly by the walk; only reachable via the fully independent `canonical_identity()` gate (X24.8/X25.6) | **No** |
| Unknown wallet (genuinely never seen before) | Any hop landing on a wallet absent from every lookup table | **No** — `LINEAGE_GAP` |
| RPC exhaustion / no signatures returned | `_find_funder_via_rpc()` returns empty | **No** — `NO_ATTRIBUTION_FOUND` (see Phase 5: this is indistinguishable from genuinely-no-funder) |
| Missing/truncated history (`TX_FETCH_LIMIT`-bounded signature window) | Same function, same empty-result path | **No** — collapses into the same `NO_ATTRIBUTION_FOUND` bucket as RPC exhaustion and genuine absence |

## Phase 3 — Precise definitions (matching implementation exactly)

**Complete** (current backend value: `WATCHTOWER_CONFIRMED`, surfaced in
the UI as `WALKBACK_RECOVERED`/`PIPELINE_INCONSISTENCY`):

> A confirmed treasury boundary was reached within the platform's 1-2 hop
> walk from the creator or sub-provisioner. This does **not** mean the
> entire funding chain back to ultimate origin was reconstructed — the walk
> stops at the first confirmed treasury or subprov-with-treasury it
> encounters and never traces further upstream.

**Partial** (current backend value: any non-`WATCHTOWER_CONFIRMED` outcome
where at least one funding fragment was captured — i.e. `LINEAGE_GAP`,
surfaced in the UI as `WALKBACK_OBSERVED`):

> At least one funding hop was successfully walked on-chain, but it
> terminated at a wallet the platform does not recognise as a confirmed
> treasury or sub-provisioner. The chain may or may not extend further; the
> platform did not determine this.

**Inconclusive** (current backend value: no `wt_walkback_queue` row exists
for this mint at all — a data-availability gap, not a data quality
finding — surfaced in the UI as `WALKBACK_INCONCLUSIVE`):

> No walkback record exists for this launch, so neither a confirmed nor a
> partial lineage determination can be reported.

Separately, **`NO_ATTRIBUTION_FOUND`** (a real backend outcome value not
currently mapped to any of the three UI evidence levels — see Phase 4) means:

> No funding hop could be walked at all — the wallet being probed returned
> no transaction signatures. This is indistinguishable, using persisted
> data, from a wallet that genuinely has no funder versus one where the
> RPC call failed or the signature window was too narrow to find one.

## Phase 4 — Wording vs. implementation comparison

| UI sentence | Backend fact it maps to | Matches exactly? |
|---|---|---|
| "A complete funding lineage was established for this launch after the fact." | `WATCHTOWER_CONFIRMED`: a confirmed treasury was reached in 1-2 hops | **No — overstates certainty.** "Complete" reads as "the whole chain was reconstructed to origin." The actual guarantee is narrower: "a recognised treasury boundary was reached." An analyst has no way to tell, from this sentence, that the walk could have stopped one hop before a genuinely more distant/different funding source, or that the reached treasury's own upstream funding was never examined. |
| "Partial funding lineage was established for this launch, but the available evidence is insufficient to confirm the complete lineage." | `WALKBACK_OBSERVED` → `LINEAGE_GAP`: a hop was walked but landed on an unconfirmed wallet | **Yes, accurate as written** — correctly frames this as partial and explicitly declines to claim completeness. |
| "Available evidence is insufficient to establish funding lineage for this launch, and no record exists to judge how complete that evidence is." | `WALKBACK_INCONCLUSIVE`: no walkback queue row at all | **Yes, accurate as written.** |
| (No current UI sentence exists for `NO_ATTRIBUTION_FOUND`.) | `NO_ATTRIBUTION_FOUND`: no signatures returned for the probed wallet | **N/A — currently unmapped.** This backend value is not one of the six classification states `detection_reconciliation.py` emits today (it operates over `wt_provisioning_sessions`, which is never populated when `NO_ATTRIBUTION_FOUND` is the walk's terminal state, since no hop was ever captured to write a session row for). This is not a UI wording defect — it's a population-scope fact, confirmed by re-reading `capture_provisioning_relationship`'s call sites: `NO_ATTRIBUTION_FOUND` walks never call `_capture_provisioning_facts` at all, so they never generate a `wt_provisioning_sessions` row and therefore never appear in `detection_reconciliation.py`'s population in the first place. |

## Phase 5 — Edge-case audit (today's actual implementation, not proposed changes)

| Edge case | Backend outcome today | Evidence level (Complete/Partial/Inconclusive) |
|---|---|---|
| Relay termination | `attribution_outcome.py` → `KNOWN_RELAY_REACHED`, but the *walkback* classification underneath is whatever `WATCHTOWER_CONFIRMED`/`LINEAGE_GAP` value the walk already produced — relay-ness is layered on top, not a walkback-time evidence judgment | Depends entirely on whether the walk separately reached a confirmed treasury; relay/bridge/CEX classification and evidence-completeness are two independent axes (confirmed correct, per X24.8/X25.6) |
| Bridge termination | Same as relay — independent classification layer | Same as above |
| Exchange (CEX) termination | Same as relay — independent classification layer | Same as above |
| Treasury termination | `_is_known_treasury()` succeeds | **Complete** (per today's definition — see Phase 4's caveat about what "complete" actually guarantees) |
| Treasury mesh termination | Not evaluated at walkback time at all — Operation Identity is computed afterward, independently, over already-confirmed treasuries | Same as plain treasury termination; mesh membership is a separate downstream fact, never part of the evidence-completeness judgment |
| Operator termination | Not evaluated at walkback time — Canonical Operator is a wholly separate, independently-gated fact (`canonical_identity()`) | No direct relationship to evidence completeness |
| Unknown-wallet termination | Neither `_is_known_treasury()` nor `_is_known_subprov()` succeeds | **Partial** (`LINEAGE_GAP`) |
| Truncated history (signature window limit) | `_find_funder_via_rpc()` returns empty exactly like a genuine no-funder case | **Neither Partial nor Inconclusive as currently modeled — collapses into `NO_ATTRIBUTION_FOUND`, a fourth, currently UI-unmapped bucket** (see Phase 4) |
| RPC failure | Same code path as truncated history — no distinct error signal is captured | Same as truncated history — indistinguishable from genuine absence |
| `LINEAGE_GAP` | Directly the backend value itself | **Partial**, exactly as currently worded |

## Phase 6 — Analyst interpretation (read as if the reader knows nothing about the implementation)

- **"Complete funding lineage was established"** — as currently worded, an
  analyst would reasonably conclude the entire chain back to the ultimate
  funding origin was traced. **That is not what the platform guarantees.**
  What it actually guarantees: *a recognised treasury boundary was reached
  within a short (1-2 hop) walk from the creator*. It does **not**
  guarantee: that the treasury itself was traced to its own funding source;
  that no shorter or different path existed; that every intermediate hop
  was examined exhaustively.
- **"Partial funding lineage"** is correctly distinguished today — it
  honestly says a fragment exists without claiming it reached a
  recognised boundary.
- **"Inconclusive"** is correctly distinguished today — it honestly says no
  record exists to judge anything.
- The fourth real backend state, **`NO_ATTRIBUTION_FOUND`**, is invisible
  in the current three-level framing entirely — worth flagging even though
  fixing it is out of this audit's explicit scope (see Recommendation).

## Phase 7 — Deliverables

**Formal definitions**: given in Phase 3, above.

**Decision tree** (as implemented, not idealized):

```
Walkback worker probes creator/subprov (wclass-dependent hop count)
  │
  ├─ Hop returns no signatures at all
  │    → NO_ATTRIBUTION_FOUND (currently unmapped to any UI evidence level)
  │
  ├─ Hop lands on a wallet in wt_confirmed_treasuries
  │    → WATCHTOWER_CONFIRMED → UI: "Complete" (see Phase 4 caveat)
  │
  ├─ Hop lands on a wt_discovered_subprovs row WITH treasury set
  │    → WATCHTOWER_CONFIRMED → UI: "Complete"
  │
  ├─ Hop lands on a wt_discovered_subprovs row WITHOUT treasury set
  │    → LINEAGE_GAP → UI: "Partial"
  │
  ├─ Hop lands on any other real, unrecognised wallet
  │    → LINEAGE_GAP → UI: "Partial"
  │
  └─ No wt_walkback_queue row exists for this mint at all
       → (detection_reconciliation.py's WALKBACK_INCONCLUSIVE) → UI: "Inconclusive"
```

**Terminal attribution boundary table**: Phase 2, above.

**Backend-to-UI wording mapping**: Phase 4, above.

**Wording corrections required**: **Yes — one.** The word "complete" in
both the `WALKBACK_RECOVERED`/`PIPELINE_INCONSISTENCY` explain sentences
and the Analyst Summary sentence overstates certainty relative to what the
backend actually guarantees (a confirmed-treasury boundary reached in 1-2
hops, not a full trace to origin). **Per this sprint's explicit scope
("do not change... UI wording unless the audit proves the current wording
is technically incorrect" / "only recommend wording changes if objectively
necessary"), this audit recommends but does not implement the following
correction, to be applied in a dedicated follow-up sprint:**

> Replace "A complete funding lineage was established" with wording such
> as "A confirmed treasury boundary was reached" or "Funding lineage was
> established to a confirmed treasury" — avoiding the word "complete"
> entirely, since it implies a guarantee (full trace to origin) the
> platform does not make.

No other wording correction is required — "Partial" and "Inconclusive" are
both already accurate as written.

## Explicit confirmation

- **No detection logic changed** — `src/ops/detection_reconciliation.py` was read, not modified.
- **No walkback logic changed** — `src/core/walkback_worker.py` was read, not modified.
- **No attribution logic changed** — `src/ops/attribution_outcome.py` was read, not modified.
- **No operation identity logic changed** — `src/ops/operation_identity.py` was not touched.
- **No UI changed** — `templates/discovery.html` was read only, for the Phase 4 comparison; the one wording defect found is recommended, not applied, in this sprint, per its audit-only scope.

`git diff --stat` for this sprint shows only this new document under
`docs/design/` — no source files modified.
