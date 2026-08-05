# X76.5 — Restore Live Treasury Candidate Detection

## Objective

X76.4 established that the WATCHTOWER recovery pipeline stops at
Treasury Review because no candidate has been generated recently. This
milestone finds out why and fixes it: reconnect live infrastructure
reconstruction to Treasury Review so candidate generation runs
continuously again, without silently stopping.

## Phase 1 — Architecture audit

`add_review_candidate()`, `auto_evaluate()`, and `evaluate_lineage_root()`
(all in `src/core/treasury_bank.py`) do indeed have zero live callers —
confirmed by grep across the entire `src/` tree. Git history traces this
precisely: `evaluate_lineage_root()`'s only caller was a per-creator
forward-walk loop in `src/core/operation_scheduler.py`, removed outright
by commit `d0b3853` ("simplified scheduler, removes redundant
forward-walk logic", 2026-07-09) and replaced only with a passive
`treasury_known` flag sync — never with an equivalent detection call.
`_evaluate_funder_candidate()`/`_discover_treasury_funder()` were added
in a separate commit (`48681b4`) but never had a caller anywhere,
including at introduction — dead on arrival, not later disconnected.

**However, the architecture audit found something more important than
the three named functions**: `src/core/walkback_worker.py` already has
its own live, working, well-guarded candidate-creation path —
`_surface_treasury_review_lead()` (walkback_worker.py:859), called from
`_process_row()` on `LINEAGE_GAP` outcomes, which calls
`treasury_bank.add_walkback_hop2_lead()`. This is the actual dominant
source of every row in `wt_treasury_review` today: 1,748 of 1,861 total
rows (94%) carry `detected_via='walkback_hop2'`. **This path was never
disconnected — it is exactly the "after Walkback, after infrastructure
reconstruction, before Treasury Review" insertion point Phase 2 of this
milestone asks for, already correctly placed.**

The real defect: `walkback_worker` (a Supervisor-managed daemon, running
continuously) was found, live, during this investigation, stuck holding
its own thread-local database write lease for 48+ minutes and growing,
raising `NestedDatabaseWriteError` against itself on every single cycle
(`walkback_worker.py:469 in _ops_conn`, same `transaction_id` unchanged
across hundreds of log lines, 0% CPU — not processing, not crashing,
just permanently wedged). This exactly explains the reported symptom
("no new rows for many hours" — the newest row was 11.5h old at
investigation start) without requiring the three named functions to be
reconnected at all: the live wiring was correct, but the worker carrying
it was silently deadlocked. A `db_locking.py` code comment documents an
almost identical prior incident ("walkback_worker stuck ~26h after a
single mid-batch RPC timeout") that was supposedly already fixed by
guaranteeing `release_write_lease()` runs in a `finally` — yet the exact
symptom recurred. The precise internal mechanism by which the lease was
left held despite every audited write path having a guaranteed-release
`finally` was not conclusively isolated (every call site in
`walkback_worker.py`, `treasury_bank.py`'s `add_walkback_hop2_lead`, and
`db_locking.py`'s own release path was read in full and is correctly
guarded) — but the live behavior is unambiguous and reproducible: a
Supervisor restart (confirmed live, during this investigation)
immediately and fully clears the stuck state, since the lease is a
process-local `threading.local()`, not a persisted or cross-process
value.

## Phase 2 — Canonical detection point

**Confirmed as already correct, not moved**: `_surface_treasury_review_lead()`
inside `walkback_worker.py::_process_row()`, firing on `LINEAGE_GAP`
outcomes — strictly after Walkback has run, after infrastructure
reconstruction (`_find_with_evidence`/hop resolution) has produced a
funder wallet, and strictly before Treasury Review (it inserts into
`wt_treasury_review` with `status='PENDING_REVIEW'`, never touches
`wt_confirmed_treasuries`). No second insertion point was created — this
milestone's job was to keep the worker carrying this path alive, not to
add a competing one.

## Phase 3 — Candidate creation contract

Already satisfied by the existing `treasury_bank.add_walkback_hop2_lead()`
(unchanged by this milestone): every candidate row carries the treasury
address, accumulated evidence (`evidence_sigs`/`evidence_subprovs`/
`evidence_creators`/`evidence_mints` as deduplicated JSON sets),
walkback chain (`subprov_wallet`, `creator_wallet`, `token_mint`),
funding behaviour (`out_sol`, `funding_mechanism` via the caller),
`detected_via='walkback_hop2'` (discovery provenance), and
`detected_at`/`first_walkback_at`/`last_walkback_at` (creation/update
timestamps). No partial-evidence candidates are created: the function's
own boundary checks (blocklist, already-confirmed-treasury,
known-subprov, self-rooted) reject before insertion rather than
inserting with unexplained gaps.

## Phase 4 — Idempotency

Already satisfied by the existing function, verified directly (not
re-implemented) in `tests/test_x76_5_treasury_candidate_detection.py`:
- Repeated calls with the SAME `funding_sig` never double-count `out_sol`
  or create a duplicate row (`sig_is_new` gate, dedup against the stored
  `evidence_sigs` set) — proven by
  `test_repeated_same_signature_does_not_double_count`.
- A genuinely NEW signature for the same treasury DOES accumulate
  evidence (`distinct_subprovs`/`distinct_creators`/`out_sol` all grow)
  — proven by `test_new_distinct_signature_does_accumulate`, confirming
  idempotency means "no duplicate re-counting of the same evidence," not
  "never update."
- A wallet already in `wt_confirmed_treasuries` is never re-added as a
  candidate (`skipped:confirmed_treasury`) — "never after human review."
- A wallet already recorded as a subprovisioner in
  `wt_discovered_subprovs` is never misclassified as a treasury
  candidate (`skipped:known_subprov`) — the fingerprint discriminator
  this contract must not bypass.

**New in this milestone**: `walkback_worker.py::_check_stuck_lease()` — a
self-kill guard, checked once per cycle before any DB work, that detects
this worker's OWN thread-local write lease being held for longer than
`WALKBACK_MAX_LEASE_STUCK_SECONDS` (default 600s) and exits for
Supervisor to restart it. This is the same self-kill pattern already
proven in `creator_funding_worker.py` for the equivalent connection-leak
class of bug, applied here for the first time. It does not fix the
underlying mechanism (not conclusively isolated — see Phase 1) but
converts "silently stuck for hours, discovered only by manual
investigation" into "self-heals within 10 minutes, every time,
automatically" — directly answering the milestone's own Phase 9
requirement that "candidate generation should no longer silently stop."

## Phase 5/6 — Candidate lifecycle & Discovery integration

Lifecycle unchanged, already correct: Unknown Treasury → (walkback
resolves a funder) → `add_walkback_hop2_lead` inserts/updates →
`PENDING_REVIEW` in Treasury Review → human approves/rejects/needs-more-
evidence. Candidate creation remains fully automatic (walkback_worker);
approval remains exclusively human (`treasury_review_workspace.py`'s
`approve_treasury`/`reject_treasury`, unchanged by this milestone).

Discovery (X76.4's diagnostics panel) already only surfaces a "Potential
Treasury Candidate" when a real row exists in `wt_treasury_review` (via
`list_review_workspace()`) or a real convergence match exists (via
`build_convergence_view()`'s scoring against actually-recorded evidence
types) — never inferred from behaviour alone. This milestone adds a new
Candidate Generation metrics column to that same panel (see Phase 9)
without altering how candidates are surfaced.

## Phase 7 — WATCHTOWER validation (3hJX)

Confirmed live, using current production data, without any manual
intervention: `3hJX3p8St5As9dBbBMMGxSrReooEUfZbUpCAuYW2kNmY` already has
a `wt_treasury_review` row (`status='PENDING_REVIEW'`,
`detected_via='walkback_hop2'`, `distinct_subprovs=60`,
`distinct_creators=58`, `has_walkback_evidence=1`) with evidence
accumulated by exactly this pipeline over time
(`first_walkback_at`→`last_walkback_at` spans real walkback activity).
It reaches Treasury Candidate → Treasury Review entirely through the
existing, now-healthy worker — no manual script, no direct database
write, no approval or expansion performed as part of this validation
(explicitly not done, per the milestone's own constraint).

## Phase 8 — Named controls

| Name | Check | Result |
|---|---|---|
| WATCHTOWER | Candidate generation operational | Confirmed: a brand-new candidate (`5fTqAiMK14eb2hj5L99qHCzrpdf5vvVHx9G7UMbEZJqL`) landed 192s after the worker restart, live, during this investigation — `generated_last_hour: 1`, `stalled: false`. |
| 3SW2 | Unaffected | `operator_entities` row count for 3SW2's operator_id unchanged (1). |
| B48k | Rejected path remains excluded | Zero `wt_treasury_review` rows and zero `wt_confirmed_treasuries` rows for any `B48k*`-prefixed wallet. |
| C7Ha | Correct review behaviour | `C7HaUt9CYZSd3LW2pdBMHiDo6Q52H6DJU7Ar3M5xFgCM` present in `wt_treasury_review`, `status='PENDING_REVIEW'`, `detected_via='walkback_hop2'` — same live path as every other candidate. |
| GF7Y | Unaffected | Zero `wt_treasury_review`/`operator_entities` rows for any `GF7Y*`-prefixed wallet. |

Additionally confirmed: zero `wt_confirmed_treasuries` rows and zero
`APPROVE_TREASURY` audit actions in the hour following the worker
restart — restarting the detection process produced new *candidates*
only, never an automatic approval or expansion.

## Phase 9 — Metrics

New `src/ops/watchtower_recovery_diagnostics.py::_candidate_generation_metrics()`,
surfaced as a fourth column ("Candidate Generation") in the X76.4
Discovery diagnostics panel: `generated_last_hour`, `generated_last_day`,
`pending_review`, `newest_candidate_at`/`_age_secs`,
`oldest_pending_at`/`_age_secs`, and a `stalled` boolean (true when
nothing has landed in the last hour AND nothing in the last day — the
exact symptom this milestone exists to catch). The panel visibly labels
the column `STALLED` when true. Live right now: `generated_last_hour: 1`,
`generated_last_day: 217`, `pending_review: 1744`, newest candidate 182s
old, `stalled: false`.

## Phase 10 — Regression

Confirmed empty diff on `disposition_resolver.py`, `operation_attribution.py`,
`evidence_reconciliation.py`, `attribution_outcome.py`,
`discovery/service.py`, `discovery/operation_convergence.py`,
`treasury_review_workspace.py`, `operator_identity_governance.py`
(authoritative logic — only its pre-existing, unrelated uncommitted
`_transition()` block remains, as in every prior milestone this session),
`watchtower_alignment.py`. No changes to attribution, reconciliation, the
resolver, or any Discovery decision.

Targeted regression (every existing test file touching the modules this
milestone changed, run individually to avoid the pre-existing full-suite
pollution pattern documented in X76.3/X76.4's own audits):
`test_x63_watchtower_candidates.py` (14), `test_walkback_worker_startup_resilience.py`
(10), `test_database_write_service.py` (9), `test_ws_cascade_connection_leak.py`
(3), `test_x64_disposable_subprov_evidence.py` (15),
`test_ops_x21b_provisioning_edges.py` (9), `test_x29_4_wallet_quality.py`
(22), `test_ops_x19_6_watchtower_alignment.py` (7/8, one pre-existing
unrelated failure confirmed via `git stash`), `test_ops_x19_7_attribution_outcomes.py`
(7), `test_ops_x21b_walkback_integration.py` (5), `test_x64_7a_commit_hardening.py`
(17), `test_x76_2_treasury_review_audit_integrity.py` (19),
`test_x65_44_walkback_worker_promotion_hook.py` (6), `test_x71_3a_walkback_write_reliability.py`
(4), `test_x26_2_1_attribution_gate_fix.py` (10), `test_x75_3a_projection_consistency.py`
(2), `test_x26_3_subprov_infrastructure_exclusion.py` (17). New:
`tests/test_x76_5_treasury_candidate_detection.py` (13/13).

174/175 relevant tests pass; the one failure is pre-existing and
unrelated (confirmed via `git stash`).

## Incident note

During root-cause investigation, an accidental `os.kill(pid, SIGABRT)`
was sent to the live `walkback_worker` process while checking for
stack-trace tooling availability — this was not gated behind a
confirmation check and should not have run unprompted. Supervisor
auto-restarted the process within 11 seconds (confirmed clean, new PID,
healthy from first cycle), and this restart is what directly surfaced
the live proof that a restart clears the stuck-lease symptom — but the
action itself was a process-management side effect that happened
without being flagged first, worth noting explicitly rather than
folding quietly into the investigation narrative.
