# X64.8 — Phase 7: Backup Strategy

## Context

X64.7C recently deleted two 8.1GB stale full-database backups
(`backup_pre_lineage_20260604_162419.db`,
`backup_pre_opsbridge_20260604_170906.db`) under disk-full emergency
pressure (795Mi free at the time). X64.7D then attempted to create a
fresh full backup and hit the same underlying constraint: disk is
persistently tight (17Gi free of 228GB, 91% capacity as of this audit).
Any backup strategy recommended here must account for this being a
recurring, not one-off, constraint — a strategy that assumes ample free
space will fail again.

## Strategy A: Full production backup

Copy both databases (`flex_complete_database.db` +9.9GB, `wt_ops_v2.db`
+2.4GB) wholesale, on a schedule.

- **Pros**: simplest to reason about; single restore point covers
  everything; matches the existing manual-snapshot pattern already used
  twice before (the June 4 backups).
- **Cons**: ~12.3GB per snapshot; at 17Gi current free space, this
  strategy can sustain **at most one** additional full backup before
  hitting the same disk-full crisis this project has now hit twice
  (once triggering the X64.7C emergency). Also backs up ~2.86GB of
  already-known-dead data (`funder_networks` hot-DB copy) every single
  time until that's cleaned up, wasting nearly a quarter of each
  snapshot's size on data with zero ongoing value.
- **Recovery capability**: complete, single-step restore.

## Strategy B: Operational database backup + historical archive backup (separate cadence)

Split backup scope: back up the **operational core** (hot, frequently
changing, correctness-critical tables) frequently and cheaply, while
backing up the **historical/archive tier**
(`flex_investigation_archive.db` + any tables moved there per Phase 6's
candidates) much less often, since it changes slowly once data lands
there.

- **Pros**: directly matches this audit's own findings — the hot DB's
  single biggest table (`funder_networks`, 2.86GB) is already
  dead weight that a full-backup strategy would keep re-copying
  forever; splitting naturally excludes it once cleaned up per Phase 8.
  Also aligns with actual change velocity: operational tables need
  frequent, recent restore points; historical/archive tables need
  infrequent ones since they're near-append-only after migration.
- **Cons**: more moving parts — two backup jobs, two restore procedures,
  and if the operational/historical split isn't cleanly enforced (some
  tables genuinely straddle both, e.g. `transfer_index`), restore
  correctness requires care to reassemble a consistent point-in-time
  view across two backup sets.
- **Recovery capability**: full recovery still possible by restoring
  both, but recency differs — operational restore can be near-real-time
  stale, archive restore can be weeks/months stale, which is
  **appropriate** given the data's actual value curve as documented in
  Phase 5's retention analysis.

## Strategy C: Incremental backups

Use SQLite's WAL/backup API or a diff-based approach (e.g.
`sqlite3 .backup` combined with periodic full baselines plus
WAL-shipping, or file-level incremental tools) to avoid full-copy cost
on every backup cycle.

- **Pros**: dramatically lower marginal disk cost per backup point once
  a baseline exists; makes frequent backups (e.g. hourly) actually
  affordable on a disk this tight.
- **Cons**: meaningfully more implementation complexity — this project
  has no incremental-backup tooling today (confirmed: no backup
  automation exists at all per the prior X64.7B preflight audit, only
  three unrelated cron jobs). Building and validating incremental/WAL-
  shipping backup correctness is real, non-trivial engineering work, and
  restore procedures become more fragile (a broken link in the
  incremental chain can invalidate recovery).
- **Recovery capability**: strong if implemented correctly, but
  introduces a new class of restore-time failure mode (corrupted or
  missing incremental link) that this project has zero operational
  experience with today.

## Recommendation: Strategy B (operational + historical split), with Strategy C as a future enhancement

**Evidence**:
1. This audit already found the hot DB carries a large, purely
   backward-looking table (`funder_networks`) with zero ongoing
   operational value — Strategy A would keep backing that up forever
   unless it's separately excluded, effectively re-deriving Strategy B's
   split anyway just to avoid waste.
2. Disk headroom (17Gi) cannot sustainably support repeated full
   12GB+ backups — this is not a hypothetical risk, it already caused
   the X64.7C emergency once.
3. Strategy B requires no new tooling beyond what's already
   proven — the `ATTACH DATABASE` + separate-file pattern used for
   `flex_investigation_archive.db` is the same mechanism a
   historical-backup job would use.
4. Strategy C is the right *eventual* target once backup frequency
   needs increase (e.g. if RPO requirements tighten below daily), but
   building it now, before Strategy B's simpler split is even in place,
   would be solving a problem this project doesn't have evidence of yet
   (no automation exists today at any cadence).

**Concrete next step implied by this recommendation**: first execute
Phase 6's archive candidates (moving `funder_networks`'s redundant hot
copy out via cleanup, and evaluating the `prediction_decision_context` /
`wss_metrics` / reporting-only candidates), which shrinks the
operational-backup footprint meaningfully before any backup automation
is built — sequencing cleanup before backup-strategy implementation
avoids automating backups of data that's about to become historical
overhead.
