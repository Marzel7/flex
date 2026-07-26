# X64.7B Preflight — Phase 1: Complete Backup Inventory

Read-only audit, 2026-07-21. Every `.db`-suffixed and backup-named file
found under the project root (excluding `.git/` and `node_modules/`).

## Full inventory

| File | Size | Created | Modified | Filesystem |
|---|---|---|---|---|
| `flex_complete_database.db` (repo root, distinct from `database/`) | 906M | 2026-04-13 | 2026-07-20 | `/System/Volumes/Data` |
| `wt_ops_v2.db` (repo root) | 40K | 2026-06-21 | 2026-06-30 | `/System/Volumes/Data` |
| `pumpswap_tokens.db` (repo root) | 21M | 2026-04-21 | 2026-06-25 | `/System/Volumes/Data` |
| `database/flex_complete_database.db` | **9.9G** | 2026-03-17 | **2026-07-21 (live, in active use)** | `/System/Volumes/Data` |
| `database/wt_ops_v2.db` | 2.3G | 2026-06-08 | 2026-07-21 (live, in active use) | `/System/Volumes/Data` |
| `database/flex_investigation_archive.db` | 2.7G | 2026-06-18 | 2026-06-22 | `/System/Volumes/Data` |
| `database/flex_complete_database.backup_pre_opsbridge_20260604_170906.db` | **8.1G** | **2026-06-04 17:09:06** | 2026-06-04 17:09:30 | `/System/Volumes/Data` |
| `database/flex_complete_database.backup_pre_lineage_20260604_162419.db` | **8.1G** | **2026-06-04 16:24:19** | 2026-06-04 16:24:43 | `/System/Volumes/Data` |
| `database/flex.db` | 4.0K | 2026-04-11 | 2026-05-05 | `/System/Volumes/Data` |
| `database/wt_alerts.db` | 12K | 2026-07-11 | 2026-07-21 (live) | `/System/Volumes/Data` |
| `db_backup/flex_complete_database.db` | **3.0G** | **2026-03-05 17:21:31** | 2026-03-05 17:21:39 | `/System/Volumes/Data` |
| `db_backup/pumpswap_tokens.db` | 1.1M | 2026-03-05 | 2026-03-05 | `/System/Volumes/Data` |
| `db_backup/pumpfun_curves.db` | 12K | 2026-03-05 | 2026-03-05 | `/System/Volumes/Data` |
| Various 0-byte placeholder `.db` files (`watchtower.db`, `flex_watchtower.db`, `flex.db` in multiple dirs, `portal.db`, `token_analysis.db`, `portal_vsol.db`, `tokens.db`, `db_backup/*.db` others, `logs/flex.db`, `src/flex.db`) | 0B each | various | various | `/System/Volumes/Data` |

All files live on the same filesystem/volume (`/System/Volumes/Data`,
the one at 100% capacity, 1.8GB free) — none are on a separate volume,
network mount, or cloud-synced location detectable from this host.

## Naming convention

Both candidate files follow the pattern
`flex_complete_database.backup_pre_<label>_<YYYYMMDD>_<HHMMSS>.db` —
a manual, ad hoc pre-change snapshot convention (not machine-generated
by any cron/script found in this repo — see `backup_chain_analysis.md`),
taken immediately before two specific code changes landed
(`<label>` matches commit subjects found in git history, see
`backup_dependency_audit.md`).

## Compression

Neither candidate file is compressed — both are raw SQLite database
files (confirmed via file size proportionality to the live DB's own
size at the time, and successful direct `sqlite3` querying without any
decompression step).

## Not found

- No off-host backup location referenced anywhere in this repo's
  scripts, docs, or config.
- No cloud backup integration (no AWS S3 / GCS / rsync-to-remote script
  found).
- No automated backup cron job (`crontab -l` shows three unrelated jobs:
  a Helius monitor every 5 min, a graph-analyzer sweep every 4 hours, and
  a migration-coverage audit hourly — none of them create or touch
  database backups).
