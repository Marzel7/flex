# X29.3 — Funding Boundary Intelligence: Validation Report

Renames X29.2's "Capital Origin" concept to "Funding Boundary." The core reframe: the bounded 2-hop walk (`src/core/walkback_worker.py`) never attempts to prove wallet genesis, so the primary honest claim is **"what known funding boundary does the observed lineage reach?"**, not "what is this launch's capital origin?" Origin becomes a rare, stronger *subtype* of that observation — surfaced as a computed `origin_proven` flag, true only when `boundary_status == PROVEN` (which the current bounded walk can never produce).

This is a rename-in-place, not a logic change: `derive_funding_boundary()` is byte-for-byte the same classification logic as X29.2's `derive_capital_origin()`, just with `origin_*` fields renamed to `boundary_*` and a computed (never stored) `origin_proven` field added at serialization time.

## Files changed

Renamed (git mv equivalent — old files deleted, new files created):
- `src/ops/capital_origin.py` → [src/ops/funding_boundary.py](../../src/ops/funding_boundary.py)
- `src/ops/capital_origin_backfill.py` → [src/ops/funding_boundary_backfill.py](../../src/ops/funding_boundary_backfill.py)
- `src/ops/capital_origin_analytics.py` → [src/ops/funding_boundary_analytics.py](../../src/ops/funding_boundary_analytics.py)
- `tests/test_x29_2_capital_origin.py` → [tests/test_x29_3_funding_boundary.py](../../tests/test_x29_3_funding_boundary.py) — 30 tests (26 original + 4 new `origin_proven` cases)

New:
- [scripts/migrate_capital_origin_to_funding_boundary.py](../../scripts/migrate_capital_origin_to_funding_boundary.py) — one-time table rename + row copy, row-count-verified, rolls back on mismatch

Modified (additive only):
- [src/core/operation_dashboard_routes.py](../../src/core/operation_dashboard_routes.py) — response field renamed `capital_origin` → `funding_boundary`
- [templates/discovery.html](../../templates/discovery.html) — card renamed "Funding Boundary," `Origin: ✓ Proven initial funder` / `Not proven` rendered as a distinct sub-line, never folded into the primary status label

## Schema rename

`wt_capital_origin` → `wt_funding_boundary`. Every `origin_*` column renamed to `boundary_*` (`origin_status`→`boundary_status`, `origin_type`→`boundary_type`, `origin_wallet`→`boundary_wallet`, etc.). Same `CHECK` constraints, same 4-value status enum (`PROVEN`/`BOUNDED_OBSERVATION`/`STATIC_MATCH`/`UNRESOLVED` — kept intact per direction, not reduced to 3), same indexes, same `UNIQUE(launch_mint, subject_wallet)`.

`origin_proven` is **not** a column — it's computed at `serialize_funding_boundary()` time as `boundary_status == "PROVEN"`, so it can never disagree with the stored status.

## Migration executed (2026-07-19)

```
python3 -m scripts.migrate_capital_origin_to_funding_boundary database/wt_ops_v2.db
{'status': 'ok', 'rows_migrated': 529}
```

Row count verified equal before dropping the old table (529 → 529); old `wt_capital_origin` table no longer exists in the ops DB.

## WATCHTOWER regression

Unchanged from X29.2: `derive_outcome()` in `src/ops/attribution_outcome.py` still checks `operator_ids` before any `_boundary(` call (verified via `inspect.getsource` position comparison) — this sprint touches only naming, not attribution ordering.

## Live verification (2026-07-19)

Reloaded gunicorn after the migration, hit `/api/ops-v2/investigation-pipeline` against the same two corpus mints used in X29.2's validation:

**BOUNDED_OBSERVATION** (`7cYYeZ2...`): `funding_boundary.status=BOUNDED_OBSERVATION`, `entity=Binance`, `origin_proven=false` — identical evidence to X29.2, now under the renamed field.

**STATIC_MATCH** (`231nLAv...`): `funding_boundary.status=STATIC_MATCH`, `entity=Moonpay`, `origin_proven=false`.

No data loss or reclassification across the rename — every value is byte-identical to its pre-migration `capital_origin` equivalent, just under new field names.

## Test results

`test_x29_3_funding_boundary.py`: 30/30 passed (26 renamed + 4 new: `origin_proven` computed correctly for each of the 4 status values). Combined X29 family (this file + `test_x29_1_operational_topology_intelligence.py` + `test_x29_1_1_operational_topology_ui_migration.py` + `test_x29_1_2_swr_cache.py` + `test_x29_1_3_outcome_grouped_launches.py`): 93/93 passed.

## RPC calls introduced

**Zero** — same guarantee as X29.2, re-verified: structural test confirms no RPC-related strings appear in `funding_boundary.py` or `funding_boundary_analytics.py`.

## Prior validation (X29.2, superseded by this rename)

See git history for the original [X29_2_CAPITAL_ORIGIN_INTELLIGENCE.md] report — same backfill counts apply unchanged since no data was reclassified: `rows_considered=529`, `status_counts: PROVEN=0, BOUNDED_OBSERVATION=384, STATIC_MATCH=121, UNRESOLVED=24`, `type_counts: CEX=431, BRIDGE=0, RELAY=98`.
