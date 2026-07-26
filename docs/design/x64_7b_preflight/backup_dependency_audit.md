# X64.7B Preflight — Phase 2/4: Purpose and Usage Audit

## Why were these two backups created?

Confirmed via git history correlation (exact timestamps match commit
dates in the repository's own log, same-day):

- `backup_pre_lineage_20260604_162419.db` (2026-06-04 16:24:19) —
  immediately precedes commit `f0bf614 feat: lineage-aware WATCHTOWER
  attribution (provisioning-hub topology fix)`. This is the exact
  migration named in this project's own persistent memory
  ("Lineage-aware Rule 2 fix" — "closes post-May hub-topology detection
  gap... wt_provisioning_hubs table + ≤3-hop ancestry walk... backfill
  168→272").
- `backup_pre_opsbridge_20260604_170906.db` (2026-06-04 17:09:06) —
  immediately precedes commit `50a70b6 feat: bridge launch-attributed
  WATCHTOWER creators into the Operations layer`, preceded by its own
  scoping commit `b5635b1 docs: scope for bridging launch-attributed
  WATCHTOWER creators into Operations`.

Both are **manual, ad hoc pre-migration safety snapshots** taken by
whoever ran these two schema/data-mutating commits that same afternoon —
a defensive "snapshot before a risky write" pattern, not a scheduled or
automated backup regime (confirmed: no cron job or backup script creates
files matching this naming pattern — see `backup_inventory.md`).

## Which task/change created them

Directly identified above: `f0bf614` (lineage-aware attribution) and
`50a70b6`/`b5635b1` (Operations-layer bridging). Both are pre-existing,
already-merged feature commits from this project's WATCHTOWER
development history — not part of any X64.x work from this session.

## Does documentation reference them?

**Yes — one reference found**, in
`docs/ARCHITECTURE_REVIEW_CRITICAL.md` (dated 2026-06-19, committed
2026-07-09), item "#10 — Backup Files Consuming ~16GB Alongside Hot DB":

> "Two backup files... sit in the same database/ directory. These are
> from June 4... **Fix:** Move to cold storage or delete if the lineage
> and opsbridge migrations are confirmed stable (both are 6+ weeks
> old)."

This document independently and explicitly named these exact two files
as low-severity, conditionally-safe-to-delete, more than a month before
this audit — not a new observation.

## Do scripts reference them?

**No.** Searched for `ATTACH.*backup`, `restore.*backup_pre`, and the
literal filenames across all `.py`/`.sh` files in the repo — zero
matches beyond the one documentation reference above.

## Do rollback procedures reference them?

**No rollback procedure of any kind references these files** — no
script, no runbook, no comment in code mentions restoring from either
backup.

## Does any code expect them to exist?

**No.** No `sqlite3.connect()`, `ATTACH DATABASE`, file-existence check,
or path reference to either filename was found anywhere in the
Python/shell codebase.

## Usage audit (Phase 4)

- **Restore scripts**: none found.
- **Restore commands**: none found in any script or shell file.
- **SQLite ATTACH statements**: none found referencing either file.
- **Documentation**: the single `ARCHITECTURE_REVIEW_CRITICAL.md`
  reference is descriptive/advisory (recommending deletion), not
  instructional for restoring from them.
- **Logs**: no log file in `logs/` references either filename (searched
  `logs/supervisor/*.log` and top-level `logs/*.log` — no match).
- **Shell history**: not accessible to this audit (no `.bash_history`/
  `.zsh_history` readable in this environment/session).

**Direct answer: there is no evidence either backup has ever been used
for a restore, referenced by any script, or required by any documented
procedure, beyond the one architecture-review document that recommends
their deletion.**
