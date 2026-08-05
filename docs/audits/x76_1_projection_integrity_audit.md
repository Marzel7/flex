# X76.1 Operator Identity Projection Integrity — Architecture Audit

## Objective

Multiple independent audits (X74.2, X75.3, X75.3A) all found the same
fact: `operator_entities` is populated (~70 rows), `operator_identity_assets`
has stayed at 0. This document traces every write path to determine the
exact, non-speculative root cause.

## Phase 1 — Every write path

### `operator_entities` writers (5 found)

| Site | Called from | Live in production? |
|---|---|---|
| `src/ops/operator_store.py:105` (`add_entity`) | nothing — `grep -rn "\.add_entity(" src/` returns zero callers | **No — dead code** |
| `src/ops/watchtower_alignment.py:138` (`reconcile_confirmed_treasury`) | `src/core/treasury_bank.py:103` (`_align_confirmed_treasury`, called from `promote_to_confirmed()`), `src/core/operation_dashboard_routes.py:3364,4049`, `watchtower_alignment.py:189` (`reconcile_all_confirmed_treasuries`) | **Yes — the live production path.** 69 of 70 current `operator_entities` rows trace here (`SELECT operator_id, entity_type, COUNT(*)... GROUP BY` confirms). |
| `src/ops/promotion_service.py:284` (brand-new-operator promotion loop) | `PromotionService._approve()` | Present in code, but has never fired against current live data (0 rows attributable to this path by provenance/timestamp inspection). |
| `src/ops/operator_identity_governance.py:430` (`expand()`) | `POST /api/ops/operators/<id>/identity/expand`, `treasury_review_workspace.py`'s `APPROVE_TREASURY`/`LINK_TO_OPERATOR` actions | Present, but X74.2 already confirmed 0 real invocations to date. |
| `src/ops/operator_identity_governance.py:521,578` (`merge()`/`split()`) | their own HTTP endpoints | Human-invoked only, 0 invocations to date. |

### `operator_identity_assets` writers (2 found, pre-fix)

| Site | Called from |
|---|---|
| `operator_identity_governance.py:422` (`expand()`) | as above — 0 real invocations |
| `operator_identity_governance.py:532` (`merge()`) | as above — 0 real invocations |

**Every** write to `operator_identity_assets`, before this fix, went
through `OperatorIdentityGovernanceService`. **No** write to
`operator_entities` from the one path that actually produces live rows
(`reconcile_confirmed_treasury`) ever called into that service.

### Consumers / read paths

- `src/discovery/service.py::_canonical_identity()` reads `operator_entities`
  directly (not `operator_identity_assets`).
- `src/discovery/relationship_classification.py::find_canonical_identity()`
  (X75.3A) reads `operator_entities` directly.
- `src/ops/operator_identity_governance.py::read_identity_lifecycle()`
  reads `operator_identity_assets` (the "assets" field in its response) —
  this is the ONLY consumer that was silently starved by the gap; any UI
  surfacing an operator's "assets" via this function would have shown an
  empty list despite `operator_entities` having 69 real WATCHTOWER
  treasuries.
- No code path reads `operator_identity_assets` for `entity_type`-style
  role lookups the way `operator_entities` is read — the two tables are
  not in a read-time race, only a write-time one.

### Historical migrations / feature flags

None found. `grep -rn "operator_identity_assets" src/` shows no
migration script, no `if os.environ.get(...)` feature-flag gate, no
conditional branch that skips the projection under some condition — the
absence is simply because no code ever called the projection at the one
site that matters, not because a flag or branch suppresses it.

## Phase 2 — Root cause (not a guess, confirmed above)

`operator_identity_assets` never received rows because
`watchtower_alignment.reconcile_confirmed_treasury()` — the sole live
producer of real `operator_entities` rows — writes `operator_entities`
directly via raw SQL and has never called
`OperatorIdentityGovernanceService.expand()` or any equivalent projection.
This is **not** a broken dual-write (nothing was ever attempting to write
both and failing); it is a **missing single write** at the one call site
that actually executes in production. `operator_store.add_entity()` and
`promotion_service.py`'s entity-insert loop are dead/unreached and do not
contribute to the live gap, though the fix (`project_entity_to_asset()`)
is written generically enough that either could safely call it if they
are ever wired up.

## Phase 3 — Projection contract

> There is exactly one authoritative write: `operator_entities`, encoding
> the confirmed role a wallet plays for an operator, written by whichever
> business-logic path (treasury confirmation, governance expansion, etc.)
> establishes that fact. `operator_identity_assets` is a deterministic,
> idempotent PROJECTION of that same fact — never an independently
> decided second source of truth. Every writer of `operator_entities`
> that has a known `entity_type` -> `asset_type` mapping (`TREASURY`,
> `SUB_PROVISIONER` -> `PROVISIONING_CONTROLLER`, `CREATOR` ->
> `CREATOR_FAMILY` — the same three-item map `ENTITY_ASSET_TYPES` already
> declared) MUST call `project_entity_to_asset()` in the SAME transaction,
> on the SAME connection, immediately after writing `operator_entities`,
> so the two tables can never diverge from a partial write. Entity types
> with no mapping (e.g. `CLIENT`) are intentionally not projected — this
> mirrors `expand()`'s own pre-existing behaviour, not a new gap.

## Phase 4 — Repair

`src/ops/operator_identity_governance.py::project_entity_to_asset(conn,
operator_id, entity_type, entity_address)` — a new, connection-scoped,
idempotent function (INSERT OR IGNORE against a deterministic
`uuid5`-derived `asset_id`, identical pattern to `expand()`'s own
idempotency). Wired into `watchtower_alignment.reconcile_confirmed_treasury()`
immediately after its existing `operator_entities` INSERT, inside a
`try/except` that never lets a projection failure block the authoritative
write. No other file touched — `operator_store.py` and
`promotion_service.py` left as-is since they are confirmed dead/unreached
paths and modifying unreached code carries no live benefit and adds
review surface for no reason.

**Explicitly NOT altered**: `operator_identity_events` semantics (the
projection appends its own event, using the existing `EVENT_TYPES`
vocabulary, not a new type), operator lifecycle transitions
(`set_activity`/`retire`/`move_to_review`/`resolve_review` — confirmed via
regression test to leave `operator_identity_assets` row counts unchanged,
as expected since they only touch `activity_status`/`identity_status`),
Treasury Review, Discovery, Walkback, Registry, attribution,
reconciliation, or the resolver (confirmed via empty `git diff` on all
authoritative files).

## Phase 5 — Backfill

`scripts/maintenance/x76_1_backfill_operator_identity_assets.py` — scans
every existing `operator_entities` row once, calls
`project_entity_to_asset()` for each, reports before/after counts. Run
against the live database:

```
before: operator_entities=70 operator_identity_assets=0
after:  operator_entities=70 operator_identity_assets=69
entity rows scanned: 70
newly projected: 69
already present (idempotent no-op): 0
skipped (no asset-type mapping): 1 {'CLIENT': 1}
```

Re-run immediately after (dry-run against a copy, confirmed identical on
a second pass): `newly projected: 0`, `already present: 69` — true
no-op, zero duplicates, `operator_entities` count unchanged both times.

## Phase 6 — Validation

- **WATCHTOWER**: 69 `operator_entities` TREASURY rows, 69
  `operator_identity_assets` TREASURY rows, set-equal wallet-for-wallet
  (`entity_wallets == asset_wallets`, verified by direct query and by
  `tests/test_x76_1_operator_identity_projection.py::TestPhase8ConsistencyAudit
  ::test_watchtower_treasury_entities_and_assets_match_exactly`).
- **3SW2**: 1 `operator_entities` CLIENT row, 0 `operator_identity_assets`
  rows — correct and expected, since `CLIENT` has no reverse asset-type
  mapping (matches `expand()`'s own pre-existing behaviour, not a new
  gap introduced here).
- **Every confirmed operator/treasury**: `test_no_missing_assets_for_mapped_
  entity_types` confirms zero `operator_entities` rows with a mapped
  `entity_type` (`TREASURY`/`SUB_PROVISIONER`/`CREATOR`) lacking a
  corresponding `operator_identity_assets` row, across the whole live
  database, not just WATCHTOWER.

## Phase 8 — Consistency audit results

- No orphan assets (every `operator_identity_assets.operator_id`
  references a real `operators` row) — confirmed.
- No duplicate assets (`GROUP BY operator_id, asset_type, asset_value
  HAVING COUNT(*) > 1` returns zero rows) — confirmed.
- No missing assets for mapped entity types — confirmed (see Phase 6).
- WATCHTOWER entity/asset sets match exactly — confirmed.

## Incidental correction to X76.0 discovered during validation

Running the full regression suite (including `test_x71_2b_investigation_
population_ui.py::test_named_controls_remain_reconciled`, which asserts
against the REAL production `EmergingOperatorService`/live database)
surfaced that X76.0's merge contract, as originally written, was too
strict: it blocked 3SW2 from absorbing its own not-yet-promoted source
population, because that population (a single wallet,
`3SW2zquY2mVTbNuw1ZCGgtoehq2evfU36PFd6TTqSXdK`) carries no funding-
mechanism/topology/structural-depth signals of its own (it's a
client-relationship population, not a treasury/subprov chain), so it
never reached the 2-identity-signal threshold X76.0 required for ANY
absorption. X76.0's contract only tested for wallet overlap, never for
wallet-set EQUALITY, so it could not distinguish "shares a wallet with
some other identity" (B48k/Dv34's case -- correctly blocked) from "IS the
identical population as its own operator" (3SW2's case -- was
incorrectly blocked).

Fixed in `src/ops/canonical_merge_contract.py::evaluate_merge()`: exact,
non-empty wallet-set equality between canonical and candidate is now
recognised as the operator's own source population (not a merge between
distinct identities) and is allowed without requiring the 2-signal
threshold -- still subject to the REJECTED-review hard stop. This is
narrower than "any overlap": a population sharing only some of a
multi-wallet operator's wallets still must satisfy the full identity-
signal threshold, so B48k/Dv34 (whose wallets are a strict subset overlap
with WATCHTOWER's 69-treasury set, not an equal set) remains correctly
blocked -- re-verified via `test_x76_0_canonical_merge_safety.py::
TestNamedControlsAgainstLiveData::test_watchtower_does_not_absorb_b48k_dv34`
and the now-passing `test_named_controls_remain_reconciled`.

## Summary

| Deliverable | Result |
|---|---|
| Architecture audit | This document, Phase 1 |
| Root-cause analysis | Phase 2 — missing single write at the live production call site, not a broken dual-write, not a flag, not dead-code-only |
| Projection repair | `project_entity_to_asset()` + one call site in `watchtower_alignment.py` |
| Backfill | 69/69 projected, idempotent, `operator_entities` untouched |
| Consistency report | Zero orphans, zero duplicates, zero missing (for mapped types) |
| Regression | 16 new tests, all passing, covering create/expand/merge/split-adjacent/reactivation/retirement/live-path/idempotency/consistency |
