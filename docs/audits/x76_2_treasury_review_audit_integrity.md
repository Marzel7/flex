# X76.2 Treasury Review Audit Integrity — Architecture Audit

## Objective

Determine why `wt_treasury_review` contains 117 real decisions while
`wt_treasury_review_actions` (X74.1's immutable audit table, intended to
record every analyst action) had 0 rows, and restore deterministic
governance recording.

## Phase 1 — Every write path

### `wt_treasury_review` (mutable state) — 3 live/historical writers found

| Site | Reachability |
|---|---|
| `src/core/treasury_bank.py::promote_to_confirmed()` / `reject_candidate()` | **The actual production choke point** — every UPDATE to `status` that has ever executed against live data traces here, directly or via one of the two wrappers below. |
| `src/ops/treasury_review_workspace.py` (X74.1 analyst workspace) | Calls `treasury_bank.promote_to_confirmed()`/`reject_candidate()` for `APPROVE_TREASURY`/`REJECT_TREASURY`; has its own direct UPDATEs for `NEEDS_MORE_EVIDENCE` (annotation only, no status change), `LINK_TO_OPERATOR`, `CREATE_INVESTIGATION`, `CREATE_OPERATOR_CANDIDATE`. **Zero real invocations before this fix** (confirmed: 0 rows in `wt_treasury_review_actions`, its own audit table). |
| `src/core/operation_dashboard_routes.py` — TWO separate older HTTP surfaces | (a) `api_intel_treasury_promote` (`/api/ops-v2/intel/treasury-promote`) — calls `treasury_bank.promote_to_confirmed()`/`reject_candidate()` directly, with **zero audit trail of any kind**. (b) `api_intel_treasury_approve` (`/api/ops-v2/intel/treasury-approve`, the "recovery-safe" route) — writes `wt_treasury_review`/`wt_confirmed_treasuries` via its **own inline SQL**, never calling `treasury_bank.py` at all, and writes its **own separate audit table**, `wt_treasury_approval_audit`. |

### `wt_treasury_review_actions` (immutable audit) — 1 writer found, pre-fix

| Site | Reachability |
|---|---|
| `src/ops/treasury_review_workspace.py::_record_action()` | The only code that has ever written this table. Called from `approve_treasury()`, `reject_treasury()`, `needs_more_evidence()`, `link_to_existing_operator()`, `create_investigation()`, `create_operator_candidate()`. **Zero real invocations** — confirmed via `SELECT COUNT(*) FROM wt_treasury_review_actions` = 0 before this fix, despite 117 decided rows existing in `wt_treasury_review`. |

### Reconciling the 117 decided rows against the OTHER audit table

`wt_treasury_approval_audit` (route (b) above) had **76 rows**. Cross-
referencing:

- 69 of the 117 decided `wt_treasury_review` rows have a matching
  `wt_treasury_approval_audit` row (some treasuries have more than one
  audit-table row, e.g. a later `WEBHOOK_ENROLLED` action, which is why
  76 audit rows map to 69 distinct decided treasuries).
- **48 of the 117 have NEITHER audit table populated at all** — these
  trace to route (a), `api_intel_treasury_promote`, which has always
  called `treasury_bank.promote_to_confirmed()`/`reject_candidate()`
  directly with no audit write whatsoever.

## Phase 2 — Root cause (confirmed, not inferred)

**Every decision that has ever actually executed in production reached
`treasury_bank.promote_to_confirmed()`/`reject_candidate()`** through one
of three call sites — but the ONE call site that writes the intended
immutable audit table (`treasury_review_workspace.py`'s dispatch) has
never actually been used by a real analyst. The two paths that HAVE been
used either write to a *different*, older audit table
(`wt_treasury_approval_audit`) or to no audit trail at all. This is the
same shape of gap X76.1 found for `operator_identity_assets`: a newer,
more complete mechanism exists in code with a correct implementation, but
the code path that actually executes in production was built earlier and
never wired to it.

Not a feature flag, not dead code in the sense of "never runs" (all three
paths DO run — see the 117 real decisions) — the gap is that the wrong
choke point was chosen for the audit write. `treasury_bank.py`'s two
functions are the one place EVERY path converges; `treasury_review_
workspace.py`'s dispatch is not.

## Phase 3 — Canonical contract

> Every analyst governance decision must produce exactly one mutable
> `wt_treasury_review` status update AND exactly one immutable
> `wt_treasury_review_actions` event, in the SAME transaction — neither
> may succeed independently. The audit write belongs at
> `treasury_bank.promote_to_confirmed()`/`reject_candidate()` themselves,
> since that is the single choke point every caller (present or future)
> actually passes through, not at whichever HTTP route happens to be
> newest.

## Phase 4/5 — Repair (same-transaction guarantee)

Added `src/core/treasury_bank.py::_record_review_action()` — a
self-contained writer (inlined, not imported, to avoid a circular import
with `treasury_review_workspace.py`, which already imports
`treasury_bank`) targeting the exact same `wt_treasury_review_actions`
schema (idempotent `CREATE TABLE IF NOT EXISTS`, same immutability
triggers). Called from inside `promote_to_confirmed()`/`reject_candidate()`
**before** their own `conn.commit()` — same transaction as the mutable
`wt_treasury_review` UPDATE, so neither can succeed without the other.

To avoid double-recording, `treasury_review_workspace.py`'s
`approve_treasury()`/`reject_treasury()` no longer call `_record_action()`
themselves for those two actions (verified: exactly 1 audit row per
action through the full workspace dispatch, not 2). The real analyst
`reason` text is now threaded through to `treasury_bank.py` via a new
optional `reason=` parameter (previously only `reviewed_by` was passed).

The two older `operation_dashboard_routes.py` surfaces:
- Route (a) (`api_intel_treasury_promote`) automatically gets the new
  audit row for free — it calls `treasury_bank.py`'s functions directly
  with no changes needed.
- Route (b) (`api_intel_treasury_approve`, the recovery-safe surface) now
  ALSO calls `treasury_bank._record_review_action()` explicitly,
  additively alongside its own pre-existing `wt_treasury_approval_audit`
  write — not a replacement of that older table, per the task's "do not
  alter review semantics" instruction.

**Not altered**: `wt_treasury_approval_audit` itself, `wt_confirmed_
treasuries` writes, `OperatorIdentityGovernanceService.expand()`, Discovery,
Walkback, reconciliation, attribution, or the resolver (confirmed via
empty diffs on all authoritative files).

## Phase 6 — Historical backfill

`scripts/maintenance/x76_2_backfill_treasury_review_actions.py` —
reconstructs one `wt_treasury_review_actions` row for every existing
decided `wt_treasury_review` row, using only already-persisted evidence:
`treasury`/`status`/`reviewed_by`/`reviewed_at` (present for all 117) and,
when available, the older `wt_treasury_approval_audit` row's
`reviewer`/`notes`/`created_at` (available for 69). Every row is marked
`"reconstructed": true` with an explicit `"source"` field. For the 48
rows with no corresponding `wt_treasury_approval_audit` evidence, the
`reason` field honestly states
`"Reason not recorded at the time of this decision..."` rather than
inventing one. Original timestamps are always preserved — the backfilled
row's `created_at` is the historical `reviewed_at`/old-audit
`created_at`, never "now."

Run against the live database:
```
before: 0, after: 117
decided rows scanned: 117
reconstructed with old-audit-table reason: 69
reconstructed WITHOUT old-audit-table reason (honest placeholder used): 48
already present (idempotent no-op): 0
```
Confirmed idempotent on a second run (0 newly written, 117 already
present).

## Phase 7/8 — Action coverage and named validation

All 6 analyst actions verified (via
`tests/test_x76_2_treasury_review_audit_integrity.py`) to produce both
the mutable state and exactly one immutable audit row:
`APPROVE_TREASURY`, `REJECT_TREASURY`, `NEEDS_MORE_EVIDENCE` (status
stays `PENDING_REVIEW`, correctly — it's an annotation), `LINK_TO_OPERATOR`,
`CREATE_INVESTIGATION`, `CREATE_OPERATOR_CANDIDATE`.

WATCHTOWER and 3SW2: `LINK_TO_OPERATOR`/`CREATE_OPERATOR_CANDIDATE` tests
exercise real `OperatorIdentityGovernanceService.expand()` calls against
WATCHTOWER/3SW2 (both CONFIRMED operators), confirmed working correctly
end-to-end with the audit row present. Every currently-decided review
(pending, previously rejected, previously approved) now has a matching
`wt_treasury_review_actions` row — confirmed via a direct query with zero
exceptions across the full live database.

## Incident: test-fixture live-database pollution during validation

While iterating on the regression test file, two mistakes independently
leaked real rows into the live database (both fully investigated,
contained, and cleaned up to the extent the immutable-audit design
permits):

1. `OperatorIdentityGovernanceService`'s default constructor resolves
   `OPS_DB_PATH` (the LIVE database) independently of whatever connection
   object a test is otherwise using. Two test functions
   (`link_to_existing_operator`/`create_operator_candidate`, and later
   `approve_treasury` via its default `operator_id=WATCHTOWER`) called
   into this default instead of an isolated copy, writing real
   `operator_identity_events` rows (4 total across the session) plus
   corresponding `operator_entities`/`operator_identity_assets` rows.
   The mutable rows (`operator_entities`, `operator_identity_assets`)
   were deleted; the immutable `operator_identity_events` rows (protected
   by the same triggers this milestone relies on) remain permanently,
   clearly labelled with test analyst names (`ws-analyst`, `a`) and
   timestamps from this session — harmless, but present. All three call
   sites are now fixed to require an explicit `governance_service` bound
   to the test's own copy database, with a docstring documenting the
   incident so it cannot recur silently in this file.
2. The test file's original per-function `tmp_path` fixture copied the
   full ~2.9GB live database once per test (19 functions), and combined
   with pytest's default retention of the last 3 sessions' temp
   directories, this filled the disk completely (down to under 1% free)
   partway through this milestone's validation. Fixed by making the
   database copy module-scoped (one copy shared read-write across all 19
   tests in the file, since each test's own writes are a handful of rows
   and additive) and by manually clearing ~57GB of accumulated
   `pytest-of-*` temp directories from prior sessions.

## Phase 9 — Regression

Confirmed via empty `git diff` on `disposition_resolver.py`,
`operation_attribution.py`, `evidence_reconciliation.py`,
`walkback_worker.py`, `attribution_outcome.py`, and every file under
`src/discovery/` — no attribution, reconciliation, resolver, or Discovery
changes. 180 regression tests pass: the new 19-test suite plus Treasury
Review, Operator Governance, Discovery, Investigation Populations,
Identity Expansion, Operator Registry, Merge Safety (X76.0), and Identity
Projection (X76.1).
