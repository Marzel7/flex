# X77.1 — Walkback Transaction Boundary Optimisation

## Objective

X77.0's Phase 10 recommendation C identified `walkback_worker.py::_process_row()`'s
`FULL_WALKBACK` branch as the dominant contributor to `wt_ops_v2.db` write-lease
hold time: hop1's funder record and mechanism evidence were written to the DB,
then the write lease sat open across hop2's own RPC round-trip before hop2's
provisioning facts were finally written and the transaction committed. This
milestone restructures that branch into collect-then-persist — gather all RPC
evidence first (pure in-memory), then open one write transaction, persist
everything, commit — with the explicit constraint that **only transaction
boundaries may change**: walkback behaviour, hop logic, funding attribution,
evidence, topology, discovery, treasury review, operator matching, identity
governance, resolver, reconciliation, and candidate generation must all be
unchanged.

## Design

### Scope of the defect

`PARTIAL_TREASURY` and `PARTIAL_SUBPROV` (the two 1-hop branches) were audited
first and found to already match the target shape — a single `_find_with_evidence`
call followed immediately by `_store_funder`, with no RPC after any write. No
changes were needed there.

Only `FULL_WALKBACK` (the 2-hop walk) had the defect: hop1 resolves via
`_find_with_evidence`, then (pre-X77.1) `_store_funder` and the mech1-evidence
capture (`_store_close_destination_evidence` / `_capture_provisioning_wallet`,
gated on `WSOL_WRAP_CLOSE`/`SEEDED_ACCOUNT_CLOSE`, each requiring its own
`_get_tx` RPC call) wrote immediately — *before* hop2's own `_find_with_evidence`
RPC call was even attempted.

### The fix: collect-then-persist split

Two new pure functions in `src/core/walkback_worker.py`:

- **`_collect_hop1_evidence(mech1, sig1) -> (fetched, tx)`** — RPC-only. Fetches
  hop1's funding transaction if `mech1` warrants it (`WSOL_WRAP_CLOSE` or
  `SEEDED_ACCOUNT_CLOSE`, both requiring `sig1`). Returns `fetched=True`
  whenever a `_get_tx` call was actually attempted — regardless of whether the
  fetch succeeded — so the caller's RPC counter increments on *attempt*, not on
  a non-`None` result. Never writes.
- **`_persist_hop1_evidence(ops, ...)`** — pure writes. Persists whatever
  `_collect_hop1_evidence` already fetched (`_store_close_destination_evidence`,
  `_capture_provisioning_wallet`). No RPC of its own. No-op when `tx is None`.

`_process_row`'s `FULL_WALKBACK` branch now:

1. Resolves hop1 via `_find_with_evidence` (unchanged).
2. Checks `_is_known_subprov(hop1)` / `_is_known_treasury(hop1)` — pure DB
   reads against `wt_discovered_subprovs`/`wt_confirmed_treasuries`, which
   never depend on anything `_store_funder`/mech1-evidence-capture would have
   written. These gates stayed in their exact original position; verified they
   read nothing that moved, so the reorder changes no decision, only when the
   bookkeeping writes land. (The two early-return branches under these gates
   never reach hop2, so their own hop1-evidence persistence is inlined
   immediately after the gate — there's no hop2 RPC left to defer past.)
3. Main path: resolves hop2 via `_find_with_evidence` (unchanged) — this is
   now the last RPC call in the branch.
4. **Then**, and only then: `_collect_hop1_evidence` → `_store_funder` →
   `_persist_hop1_evidence` → `_capture_provisioning_facts` (hop2's edge) →
   `_mark_complete`/`_mark_failed` outcome branches. All pure writes, zero RPC,
   fired together in one uninterrupted stretch.

### Explicitly out of scope, confirmed untouched

- **`_find_with_evidence`/`_find_funder_via_rpc`** — this RPC-search primitive
  already commits internally per hop scan (`deep_walkback.persist_edge_candidate`
  + `ops.commit()`), a pre-existing, legitimate "collect during scan, commit at
  end of scan" pattern that is `deep_walkback`'s own crash-safety/topology
  contract. Not modified.
- **`_expand_unknown_upstream`** — the deep multi-hop upstream-expansion
  function, deliberately incremental (RPC + write per hop) for crash-safety
  observability of a potentially-failing deep walk. Not modified.
- **`_mark_complete`/`_mark_failed`/`_mark_exhausted`** — confirmed 100%
  RPC-free already. Not modified.

### A strict improvement found as a side effect

The pre-X77.1 code had no explicit `ops.rollback()` in its exception handler,
so a hop2 RPC failure would still leave hop1's already-written funder record
and mechanism evidence committed — a genuine partial-write bug. Post-X77.1,
hop1's evidence collection is deferred until after hop2 resolves, so a hop2
failure now leaves **no** hop1 write behind at all (proven in
`test_hop2_rpc_failure_leaves_no_partial_hop1_evidence`, below). This is a
strict improvement matching the milestone's own Phase 4 requirement, not a
behaviour change requiring separate justification.

### Idempotency

`_store_funder` is a plain unconditional `UPDATE` — safe to fire at any point,
any number of times, with the same final result regardless of retry timing.

## Validation

### Phase 4/5 — regression tests

New file `tests/test_x77_1_walkback_transaction_boundary.py` (4 tests, all
passing):

1. **`test_all_rpc_calls_precede_first_write`** — instruments every RPC call
   and every write call with an event log; asserts the last RPC event index is
   strictly less than the first write event index. Direct proof the lease is
   never acquired before an RPC call in this branch.
2. **`test_reordered_full_walkback_matches_pre_x77_1_final_state`** — same
   fixture/mocks as the pre-existing `test_full_walkback_anchors_creator_and_upstream_searches`
   in `test_ops_x21b_walkback_integration.py`; asserts identical final
   `intelligence_outcome`/`subprov`/`treasury`/`funder_wallet`/`funding_mechanism`
   and identical `wt_wrap_close_candidates` state.
3. **`test_hop2_rpc_failure_leaves_no_partial_hop1_evidence`** — simulates a
   hop2 RPC exception; asserts the row resets to `pending` for retry (< max
   attempts) with `funder_wallet`/`funding_mechanism` both `NULL` and zero rows
   in `wt_wrap_close_candidates`/provisioning edges — no partial hop1 write
   survives, confirming the rollback-safety improvement above.
4. **`test_idempotent_retry_produces_no_duplicate_rows`** — runs the same row
   through `_process_row` twice (simulating a crash-then-retry); asserts
   exactly one `wt_wrap_close_candidates` row and one provisioning edge, not
   two.

The full pre-existing X21B suite (`tests/test_ops_x21b_walkback_integration.py`,
5 tests) also passes unmodified against the new code.

**A real bug was caught and fixed during this phase**: the first draft of
`_collect_hop1_evidence` inferred whether an RPC call happened from
`tx is not None`, which under-counted `rpc_used` whenever `_get_tx` was
attempted but returned `None` (a failed fetch) — two tests in the pre-existing
`tests/test_x64_disposable_subprov_evidence.py` suite caught this
(`rpc_used == 0` where `1` was expected). Fixed by having
`_collect_hop1_evidence` return an explicit `fetched: bool` alongside `tx`,
so the caller counts RPC credit on *attempt*, matching the pre-X77.1 code's
unconditional increment, not on a successful non-`None` result.

### Phase 6 — before/after lease timing and SQLITE_BUSY

Measured with a controlled microbenchmark
(`x77_1_lease_bench.py`, scratch) using the real
`acquire_write_lease`/`release_write_lease` cross-process `fcntl.flock`
primitives from `database_write_service.py` against a throwaway lock file,
with simulated RPC latency (200ms/250ms per hop, matching observed real Helius
round-trip times) and a concurrently-contending second thread standing in for
`ws_cascade` writing to the same ops DB.

| Metric | OLD (pre-X77.1) | NEW (X77.1) |
|---|---|---|
| Avg lease-hold duration | 472–477ms | 12–14ms |
| Max lease-hold duration | 487–489ms | 15–30ms |
| Contender SQLITE_BUSY-equivalent blocks (30 cycles) | 30 | 71–189 |
| Contender avg wait | 458–462ms | 2–8ms |
| Contender max wait | 477–487ms | 44–46ms |

The lease-hold duration drops by ~97% (from spanning both hop RPC round-trips
to spanning only the local writes). The contender's block *count* rises
(more, but far shorter, contention windows) while its wait *duration* per
block drops by ~98% — the aggregate time other writers spend blocked on this
worker collapses from ~14s/30-cycles to under 0.5s/30-cycles. This is the
expected and intended shape of the fix: shorter, more frequent lease holds
instead of long monopolizing ones.

### Phase 7 — named validation

Replayed real, already-completed `FULL_WALKBACK` rows from the live ops DB
(`database/wt_ops_v2.db`) through the new `_process_row`, with
`_find_funder_via_rpc`/`_get_tx` mocked to return exactly the evidence values
already recorded for that row, against an isolated in-memory schema — proving
the new code reproduces the same stored outcome given the same inputs.

| Entity | Result | Outcome (live == replayed) |
|---|---|---|
| WATCHTOWER (`8ncU5YW1…` subprov, `ATX1poAM…pump`) | MATCH | `WATCHTOWER_CONFIRMED`, subprov, funder_wallet, `WSOL_WRAP_CLOSE` |
| 3SW2 (`3SW2zquY2…`) | MATCH | `LINEAGE_GAP`, subprov, funder_wallet, `PLAIN_XFER` |
| B48k (`B48kNVXs4…`) | MATCH | `LINEAGE_GAP`, subprov, funder_wallet, `PLAIN_XFER` |
| C7Ha (`C7HaUt9CY…`) | MATCH | `LINEAGE_GAP`, subprov, funder_wallet, `PLAIN_XFER` |
| 3hJX | **NOT PRESENT** | No matching entity in `wt_walkback_queue`, `wt_discovered_subprovs`, or `wt_confirmed_treasuries` in the current ops DB — checked directly by prefix, none found. Reported honestly rather than fabricated. |
| Creator Funding | N/A to this file | `realtime_creator_funding_extractor.py` is untouched by X77.1 (X76.3's own subsystem); its regression coverage (`tests/test_x76_3_extractor_concurrency.py`, 19/19) confirms no impact. |

All present entities produced identical evidence between the live-recorded
run and the replay through the reordered code — only write timing changed.

### Phase 8 — regression

Targeted regression (the standard used throughout this session, given
documented pre-existing full-suite collection pollution from unrelated
modules — `test_helius_analysis.py`, `test_pumpswap_detection.py`,
`test_pumpswap_phase2.py` all fail to import for reasons unrelated to
walkback):

`test_x77_1_walkback_transaction_boundary.py` (4/4, new),
`test_ops_x21b_walkback_integration.py` (5/5),
`test_x65_44_walkback_worker_promotion_hook.py` (6/6),
`test_x64_disposable_subprov_evidence.py` (15/15),
`test_x29_3_funding_boundary.py` (30/30),
`test_x64_5_anchor_reconciliation.py` (14/14),
`test_x64_6_missing_create_audit.py` (17/17),
`test_x64_7_create_event_ledger.py` (23/23),
`test_x64_7a_commit_hardening.py` (17/17),
`test_walkback_worker_startup_resilience.py` (10/10),
`test_x76_3_extractor_concurrency.py` (19/19, Creator Funding),
`test_x76_2_treasury_review_audit_integrity.py` (19/19),
`test_ops_x19_7_attribution_outcomes.py` (7/7),
`test_x26_2_1_attribution_gate_fix.py` (10/10),
`test_x75_3a_structural_graph_integrity.py` (18/18),
`test_x75_3a_projection_consistency.py` (2/2),
`test_database_write_service.py` (9/9),
`test_x76_5a_walkback_candidate_health.py` (16/16).
**248/249 relevant tests pass.**

The one failure, `test_ops_x19_6_watchtower_alignment.py::test_production_surfaces_name_the_x19_6_control_concepts`,
was confirmed **pre-existing on `main` before this milestone's changes**
(reproduced by stashing the X77.1 diff and re-running — identical failure, a
template-content assertion unrelated to `walkback_worker.py`). Not a
regression introduced by this milestone.

Confirmed empty diff (git status) on `disposition_resolver.py`,
`operation_attribution.py`, `evidence_reconciliation.py`,
`attribution_outcome.py`, `discovery/service.py`,
`discovery/operation_convergence.py`, `treasury_review_workspace.py`,
`operator_identity_governance.py` (only its pre-existing, unrelated
uncommitted `_transition()` block remains, as in every prior X76.x/X77.x
milestone this session), `watchtower_alignment.py`, `deep_walkback.py`. The
only file changed by this milestone is `src/core/walkback_worker.py`, plus
the new test file.

## Acceptance criteria

- ✅ Write lease no longer held across RPC (proven both structurally —
  `test_all_rpc_calls_precede_first_write` — and empirically — Phase 6
  microbenchmark).
- ✅ Behaviour unchanged (Phase 7 named validation, Phase 8 regression).
- ✅ Attribution unchanged (identical `intelligence_outcome`/`subprov`/
  `treasury` across all replayed entities).
- ✅ Candidate generation unchanged (`wt_discovered_subprovs` state identical
  in replay; `test_x76_5a_walkback_candidate_health.py` 16/16).
- ✅ Discovery unchanged (empty diff on `discovery/service.py`,
  `discovery/operation_convergence.py`).
- ✅ Treasury review unchanged (empty diff on `treasury_review_workspace.py`;
  `test_x76_2_treasury_review_audit_integrity.py` 19/19).
- ✅ Reduced `SQLITE_BUSY`-equivalent contention duration (~98% reduction in
  contender wait time per cycle).
- ✅ Reduced average lease duration (~97% reduction, 472–477ms → 12–14ms).
- ✅ All regressions pass (248/249; the 1 failure is pre-existing and
  unrelated, confirmed via stash-and-compare against `main`).

## Files changed

- `src/core/walkback_worker.py` — `FULL_WALKBACK` branch reorder; two new
  pure helper functions (`_collect_hop1_evidence`, `_persist_hop1_evidence`).
- `tests/test_x77_1_walkback_transaction_boundary.py` — new, 4 tests.

[x77_1_walkback_transaction_boundary_optimisation.md](docs/audits/x77_1_walkback_transaction_boundary_optimisation.md)
