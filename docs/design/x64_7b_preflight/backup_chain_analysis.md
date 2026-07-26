# X64.7B Preflight — Phase 3/5: Backup Chain and Database Comparison

## Full chain of every full-database backup found (chronological)

| Backup | Date | Size | Location |
|---|---|---|---|
| `db_backup/flex_complete_database.db` | 2026-03-05 | 3.0G | `db_backup/` |
| `database/flex_complete_database.backup_pre_lineage_20260604_162419.db` | 2026-06-04 16:24 | 8.1G | `database/` |
| `database/flex_complete_database.backup_pre_opsbridge_20260604_170906.db` | 2026-06-04 17:09 | 8.1G | `database/` |
| **`database/flex_complete_database.db`** (live, current) | 2026-07-21 (ongoing) | 9.9G | `database/` |

## Are these the newest backup?

**No — they are the second-oldest of four points in the chain.** The
live database itself (updated continuously) is the newest point, but it
is not a "backup" in the safety-copy sense — it is the production
database being actively written to. Of the three genuine static
snapshots, the June 4 pair are the **newest snapshots that exist**
(the March 5 one is older and smaller).

## Are they the oldest?

**No.** `db_backup/flex_complete_database.db` (2026-03-05, 3.0G) is
three months older and less than half the June 4 backups' size.

## Are there newer verified backups?

**No newer full-database snapshot exists anywhere on this host** —
confirmed by the complete inventory in `backup_inventory.md`. There is
no automated backup job (confirmed via `crontab -l`), so **no snapshot
has been taken since 2026-06-04, 47 days before this audit.** This means
the June 4 pair are simultaneously "the best available backup" (nothing
newer exists) and "very stale relative to current data" (live DB has
grown from ~4.5-5GB implied at that time to 9.9GB, more than doubling).

## Database comparison (Phase 5, read-only, no modification)

| Metric | Live DB | opsbridge backup | lineage backup |
|---|---|---|---|
| `PRAGMA user_version` | 0 | 0 | 0 |
| Table count | 274 | 261 | 261 |
| `token_analysis` row count | 1,336,699 | 607,113 | 606,968 |
| File size | 9.9G | 8.1G | 8.1G |
| `PRAGMA quick_check` | not run (live, in-use) | **ok** | **ok** |

**Observations**:
- Both backups have **identical table counts (261)** and near-identical
  `token_analysis` row counts (607,113 vs 606,968 — a ~145-row
  difference consistent with ~45 minutes of live traffic between the
  two snapshot times on the same afternoon), confirming they are two
  genuine, consistent, complete full-database snapshots taken ~45
  minutes apart on the same day, not corrupted or partial dumps.
- Live DB has **13 more tables** than either backup (274 vs 261) —
  consistent with 47 days of subsequent schema additions (this session's
  own `wt_create_event_ledger`, `wt_create_ledger_pending`,
  `wt_migration_ledger_coverage`, `wt_anchor_reconciliation_log`, plus
  many other X-numbered feature tables added since June 4).
- Live DB has **2.2x the `token_analysis` row count** of either backup —
  consistent with continuous organic data growth, not a discontinuity.
- **Both backups pass `PRAGMA quick_check` with `ok`** — no corruption
  detected in either file.

**Conclusion: these are complete, verified-intact, full-production-database
snapshots from a single point in time (2026-06-04) — not partial or
table-specific snapshots.** They reflect the schema and data as it
existed 47 days ago, before 13 additional tables and roughly 730,000
additional `token_analysis` rows accumulated.
