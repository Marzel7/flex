# X64.7B Preflight — Phase 6/7: Operational Necessity and Risk Classification

## Phase 6 — Operational necessity

**Could production be restored without these files?**
Partially. `db_backup/flex_complete_database.db` (2026-03-05, 3.0G)
would still exist as a fallback restore point, but it is materially
staler (three and a half months old vs 47 days) and reflects an earlier,
smaller, structurally different schema (fewer tables, a fraction of the
current row count). Restoring from it would lose significantly more
data/schema history than restoring from either June 4 file would.

**Are newer backups sufficient?**
There are no newer *backups* at all (confirmed: no snapshot exists
between 2026-06-04 and now). The live database itself is the only
"newer" data point, and it is not a backup — it is the single
production copy currently at risk from the disk-full condition this
audit exists to help resolve.

**Do these provide unique recovery value?**
**Yes, real but bounded value**: they are the most recent static,
verified-intact snapshots that exist, and the only ones reflecting
post-March schema/data state. Their unique recovery value is specifically
"restore to June 4, 2026 state" — a 47-day-old recovery point. This value
is bounded by staleness: neither backup can recover anything written to
the live DB since June 4 (47 days of subsequent data, including all of
this session's X64.x work and the underlying WATCHTOWER detection
history accumulated since).

**Are they only historical checkpoints?**
Effectively yes, given their age and the absence of any operational
process (script, rollback runbook, restore procedure) that treats them
as active recovery infrastructure. They function today as a stale
point-in-time archive, not a maintained disaster-recovery asset.

## Phase 7 — Risk classification

| File | Classification | Reasoning |
|---|---|---|
| `backup_pre_lineage_20260604_162419.db` | **PROBABLY_SAFE_TO_DELETE** | Positive evidence on every required dimension: (1) no newer backup exists to supersede it, but it is itself 47 days stale and the underlying migration it protects against (`f0bf614`) has had 47 days of subsequent commits built directly on top of it with no rollback ever invoked; (2) zero code/script/rollback-procedure references found; (3) passes integrity check (`quick_check: ok`), confirming it is not corrupted (a corrupted backup would have essentially zero value, strengthening rather than weakening a delete case, but this one is intact so the classification rests on genuine staleness + non-use, not on the file being unusable); (4) explicitly already flagged for deletion in this project's own architecture review, independently, over a month ago, once the same "6+ weeks old" bar (now cleared) was met. |
| `backup_pre_opsbridge_20260604_170906.db` | **PROBABLY_SAFE_TO_DELETE** | Identical reasoning — same day, same absence of references, same integrity-check pass, same already-documented recommendation, protecting a migration (`50a70b6`) that has likewise had 47 days of stable, unreverted downstream development on top of it. |

**Neither file is classified `PROBABLY_SAFE_TO_ARCHIVE` instead of
`PROBABLY_SAFE_TO_DELETE`**, despite that being a less destructive
alternative, because: the disk is genuinely full (1.8GB free), so moving
16.2GB to "cold storage" **on the same volume** provides zero space
relief — cold storage would need to be a different filesystem/volume,
none of which was found configured or reachable from this host in this
audit. If such a location becomes available later, archiving is strictly
preferable to deletion and should be used instead if disk pressure isn't
the same immediate constraint.

## Final answers

**Why were these two backups created?**
Manual, ad hoc pre-migration safety snapshots taken the same afternoon
(2026-06-04) as two specific WATCHTOWER feature commits landed
(`f0bf614` lineage-aware attribution, `50a70b6` Operations-layer
bridging) — a defensive "snapshot before a risky write" practice, not an
automated or scheduled backup.

**What would be lost if they disappeared?**
The ability to restore the database to its exact 2026-06-04 state.
Given 47 days of subsequent, stable, unreverted development sits on top
of both migrations these snapshots were guarding against, and no process
has ever needed or referenced them since, the practical loss is
minimal — a stale point-in-time recovery option for two migrations that
have already proven stable in production for far longer than their own
documented "confirm stable" threshold.

**Are they referenced anywhere?**
Only descriptively, in one architecture-review document that itself
recommends deleting them — no code, script, or rollback procedure
references either file.

**Have they been superseded?**
Not by a newer *backup* (none exists) — but functionally superseded by
47 days of stable production operation on top of the exact migrations
they were created to protect against, which is the actual condition this
project's own prior review set as the bar for safe deletion, now met.

**Is deleting them a low-risk operation?**
**Yes, low risk, given all of the above** — no references, no
dependents, no automated process expects them, they are not the only
backup that would remain (the March 5 one, while much staler, still
exists), and their own project's prior review already reached the same
conclusion independently over a month ago once this exact staleness
threshold was crossed.

**If disk space must be reclaimed immediately, are these the best
candidates, or is there a safer alternative?**
**These are the best candidates.** No other large, unreferenced,
non-live file was found in this inventory that would free comparable
space (16.2GB) with comparably low risk. The only other large file
(`db_backup/flex_complete_database.db`, 3.0GB) is the sole remaining
full-database backup and should be preserved as the last point-in-time
fallback once the June 4 pair are removed — deleting it instead would
leave zero backups of any kind, a strictly worse outcome. Log files
(`logs/supervisor/*.log`, several MB each) are a much smaller, lower-
value secondary target if additional space is still needed after the
16.2GB reclaim.
