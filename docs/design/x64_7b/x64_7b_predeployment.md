# X64.7B — Phase 1: Pre-Deployment Baseline

Captured 2026-07-21, immediately before restarting `watchtower_listener`.

## Process state

| Field | Value |
|---|---|
| Process name (supervisord) | `watchtower_listener` |
| PID (pre-restart) | 46902 |
| Started | 2026-07-21 13:29:53 |
| Uptime at baseline capture | 0:26:25 (restarted recently by supervisord/operator, not by this task) |
| Command | `python -u -m src.core.pumpfun_curve_listener` |

Also relevant, since this task's changes touch `walkback_worker.py`
(the `retry_pending_writes()` wiring and the ledger-priority
reconciliation path):

| Process | PID | Uptime |
|---|---|---|
| `walkback_worker` | 11183 | 4 days, 22:06:23 |

Both processes need a restart for this task's code to take effect —
`watchtower_listener` for the CREATE-ledger write path,
`walkback_worker` for the retry/reconciliation consumption path.

## Git state

| Field | Value |
|---|---|
| Current commit | `6fbc80f404bb66945fa9f527b28bfeb2b7d16c5b` |
| Current branch | `classification-attribution-axis` |
| Working tree | Uncommitted modifications present (this session's X64.5-X64.7A work: `pumpfun_curve_listener.py`, `watchtower_attribution.py`, `walkback_worker.py`, `walkback_queue.py`, `anchor_reconciliation.py`, `create_event_ledger.py`, plus unrelated pre-existing modifications from earlier sessions to `operation_dashboard_routes.py`, `operation_store_v2.py`, `treasury_bank.py`, `creator_activity.py`, `operational_behaviour_tags.py`, `operational_intelligence.py`, `templates/discovery.html`) |

No commit was made before this deployment — Python processes read
`.py` files directly from disk at each restart, so a restart picks up
the working-tree state without requiring a commit. This is noted for
rollback clarity (see below), not treated as a blocker.

## Database population baseline

| Metric | Value |
|---|---|
| `WAITING_FOR_CREATE_ANCHOR` count | **39** |
| `MINT_NOT_FOUND` (via `anchor_reconciliation.dry_run_report()`) | **34** |
| `RECOVERABLE_VALID_ANCHOR` (already recoverable, zero-RPC, X64.5 path) | 5 |
| `wt_create_event_ledger` row count | **0** (table exists — schema-only, created incidentally by this session's own testing — no data) |
| `wt_create_ledger_pending` row count | **table does not exist yet** — created automatically on first `ensure_schema()` call after deployment |
| `wt_migration_ledger_coverage` row count | **table does not exist yet** — same |

## Rollback procedure

Since no commit was made, rollback is: `git checkout -- src/core/pumpfun_curve_listener.py src/core/watchtower_attribution.py src/core/walkback_worker.py` (reverting to the pre-X64.7/X64.7A working-tree state — note this would also revert this session's earlier, already-tested X64.5/X64.6 changes to these same files, since they were never separately committed; a more surgical rollback would require `git diff`-based partial reverts, not prepared in advance) plus deleting the two new files
(`src/ops/create_event_ledger.py`, and `anchor_reconciliation.py`'s
X64.7 additions would need a partial revert since that file has
pre-existing X64.5/X64.6 content), then a supervisord restart of both
`watchtower_listener` and `walkback_worker`. **Confirmed available and
sufficient for a code-level rollback.** No schema rollback is needed —
all three new tables are additive-only (new tables, no altered/dropped
columns on any existing table), so leaving them in place after a code
rollback causes no harm; they would simply stop being written to.

## Confirmed rollback command

```bash
supervisorctl -c config/supervisor/supervisord.conf restart watchtower_listener walkback_worker
```
(same mechanism as the forward deployment — supervisord already manages
both processes, confirmed via `supervisorctl status`)
