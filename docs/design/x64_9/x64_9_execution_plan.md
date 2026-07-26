# X64.9 — Phase 8: Execution Roadmap

An ordered, phased implementation plan. Each phase is independently
deployable, independently testable, and independently reversible (or,
where not reversible in the traditional sense, explicitly justified as
such in its own safety section per Phase 6). No phase depends on a
phase later than it in this ordering; each phase can be paused after
completion without blocking any other phase from later proceeding.

```
X64.9A
Remove funder_networks hot-DB copy (DROP TABLE only, no VACUUM)
   │
   ▼
X64.9B
Purge terminal wt_subprov_sig_retry rows (one-time, scoped, batched)
   │
   ▼
X64.9C
Build daily retention job for wt_subprov_sig_retry (automates X64.9B going forward)
   │
   ▼
X64.9D
Investigate + fix the creator_funding_ready queue backlog
(unblocks coordinated_creator_edges staleness as a side effect)
   │
   ▼
X64.9E
Refactor or mitigate the wt_candidate_websocket_watches existence-check
dependency, then purge EXPIRED rows (scoped, batched)
   │
   ▼
X64.9F
Build daily retention job for wt_candidate_websocket_watches
(automates X64.9E going forward)
   │
   ▼
X64.9G
Design + implement summary-table migration for wt_active_subprov_sessions'
operational-spend-proxy classifier findings, THEN (separately) purge
EXPIRED session rows
   │
   ▼
X64.9H
Archive tooling + code-repointing for prediction_decision_context +
token_prediction_events (monthly retention job)
   │
   ▼
X64.9I
Backup architecture improvements (Strategy B from X64.8, sequenced last
since it benefits most from the smaller operational footprint the
prior phases produce)
   │
   ▼
X64.9J (separately gated, maintenance window required)
VACUUM the hot database to physically reclaim space freed by X64.9A
```

## Phase-by-phase detail

### X64.9A — Remove `funder_networks` hot-DB copy

- **Independently deployable**: yes — no dependency on any other phase.
- **Independently testable**: yes — verify archive superset, drop
  table, confirm gone, confirm process health (Phase 6 safety contract).
- **Independently reversible**: no traditional rollback (data is gone
  from the hot DB), but fully mitigated by the pre-verified archive
  superset — functionally reversible via `ATTACH` + `INSERT ... SELECT
  FROM arch.funder_networks` if ever needed, though this is not
  expected to be necessary.
- **Estimated impact**: ~2.86GB reclaimed once `VACUUM` (X64.9J) runs;
  0 bytes physically reclaimed before that (SQLite marks pages free but
  doesn't shrink the file until `VACUUM`).

### X64.9B — Purge terminal `wt_subprov_sig_retry` rows (one-time)

- **Independently deployable**: yes.
- **Independently testable**: yes — status-distribution check before/after.
- **Independently reversible**: no rollback needed (dead data, per
  Phase 1/2's code-level proof).
- **Estimated impact**: ~393MB reclaimed in the ops DB (again, pending
  a `VACUUM` on `wt_ops_v2.db` for physical reclaim — a smaller, lower-
  risk `VACUUM` given that DB's smaller 2.4GB size, but still deferred
  to the same maintenance-window discipline as X64.9J).

### X64.9C — Automate X64.9B as a daily job

- **Independently deployable**: yes, though logically follows X64.9B
  (no reason to automate before confirming the one-time purge works).
- **Independently testable**: yes — dry-run mode first, then a single
  scheduled run, verified against the same status-distribution check.
- **Independently reversible**: yes — simply unschedule the cron entry;
  no data-level rollback needed since the job's own safety contract
  (Phase 6) prevents unsafe deletions.

### X64.9D — Investigate + fix the `creator_funding_ready` backlog

- **Independently deployable**: yes — this is an investigation, not
  dependent on any storage work above.
- **Independently testable**: yes — success is measured by
  `oldest_ready_age` trending down instead of up, and the
  graph-analyzer's `QUEUE_GUARD` eventually allowing a run to proceed.
- **Independently reversible**: depends entirely on what the fix turns
  out to be (unknown until investigated) — flagged here as its own
  phase specifically so it isn't silently rolled into the storage work
  and forgotten.
- **Estimated impact**: not a storage phase — the benefit here is
  operational health (a 23+-day-and-growing backlog is concerning on
  its own merits), with `coordinated_creator_edges` freshness as a
  side-effect bonus, not the primary goal.

### X64.9E — `wt_candidate_websocket_watches` existence-check mitigation + purge

- **Independently deployable**: yes, though should not start until the
  mitigation approach (refactor vs. retain-latest) is chosen and its
  regression test (Phase 6) is ready.
- **Independently testable**: yes — the existence-check regression test
  is the explicit gate for this phase's success.
- **Independently reversible**: the retain-latest approach preserves
  enough information that the existence check keeps working; the
  refactor approach (if chosen instead) is a code change, reversible by
  normal version control.
- **Estimated impact**: ~190-290MB reclaimed (Phase 3's revised, more
  conservative estimate vs. X64.8's original full-table figure).

### X64.9F — Automate X64.9E as a daily job

- Same reasoning as X64.9C, applied to the watches table.

### X64.9G — `wt_active_subprov_sessions` summary-table migration + purge

- **Independently deployable**: yes, but this is the single largest
  engineering lift in this roadmap (new summary table design, migration
  of existing `session_tag` classifications, then and only then a
  purge) — should be scoped and estimated as its own mini-project
  before committing to a timeline.
- **Independently testable**: yes — the operational-spend-proxy
  classifier's output (session_tag assignments) must be provably
  unchanged before/after the migration.
- **Independently reversible**: the summary table is additive (new
  table, no data loss) until the final purge step, which should only
  proceed after the summary table has been in production and validated
  for a meaningful period (recommend at least one full classifier cycle
  observed against the new summary table before trusting it as the
  sole record).
- **Estimated impact**: up to ~54MB directly, but the real value is
  unblocking a previously-BLOCKED table for future consideration, not
  the storage number itself.

### X64.9H — Archive `prediction_decision_context` + `token_prediction_events`

- **Independently deployable**: yes, fully decoupled from the queue-purge phases above.
- **Independently testable**: yes — row-count reconciliation and a
  quick_check on the archive DB, per Design 5 (Phase 4).
- **Independently reversible**: yes, in the sense that hot-DB rows are
  only deleted after the archive copy and code-repointing are both
  confirmed working — there's a natural safe window between archive-insert
  and hot-DB-delete.
- **Estimated impact**: ~454MB initially, then an ongoing monthly trickle.

### X64.9I — Backup architecture improvements (Strategy B)

- **Independently deployable**: yes, though most valuable after the
  phases above have shrunk the operational footprint (per X64.8 Phase
  7's own sequencing recommendation).
- **Independently testable**: yes — a test restore from each backup
  tier (operational, historical) should be exercised before relying on
  either in a real incident.
- **Independently reversible**: yes — this is additive tooling, not a
  destructive change to existing data.

### X64.9J — `VACUUM` (separately maintenance-window-gated)

- Deliberately placed last and separately gated — see Phase 6's safety
  contract. This is the only phase in this roadmap that is not safely
  interruptible and requires a dedicated window, explicit authorization
  (per this project's `--i-am-in-a-maintenance-window` convention), and
  active monitoring during execution.

## What this roadmap deliberately does NOT include

- Any action on `rpc_response_cache` (already healthy) or `sol_transfers`
  (live production dependency) — both excluded entirely per Phase 1/2.
- Any action on the unverified remainder of the "9+6" zero-reference
  table set — flagged for individual follow-up investigation (Phase 7,
  Low priority) but not included in this execution sequence until each
  is individually confirmed.
- The `transfer_index` + funder-transfer family archival — flagged as
  the largest remaining long-term opportunity but deliberately excluded
  from this roadmap's concrete phases given its high structural
  complexity (Phase 7); recommend scoping it as its own dedicated
  design task (an "X64.10", if this numbering convention continues)
  rather than folding it into this already-large roadmap.
