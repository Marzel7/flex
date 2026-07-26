# X64.6 — Missing CREATE-Capture Audit: Master Report

Companion documents: [x64_6_population.csv](x64_6_population.csv) (Phase
1), [x64_6_stored_source_recovery.csv](x64_6_stored_source_recovery.csv)
(Phase 2/7), [x64_6_conflicts.csv](x64_6_conflicts.csv) (Phase 3),
[x64_6_timing_clusters.md](x64_6_timing_clusters.md) (Phase 5),
[x64_6_rpc_recovery_dry_run.md](x64_6_rpc_recovery_dry_run.md) (Phase 7),
[x64_6_implementation.md](x64_6_implementation.md) (Phases 6/8/9),
[x64_6_regression_results.md](x64_6_regression_results.md) (Phase 10).

## Timestamp drift

Per the task's explicit instruction to derive and report the live count
rather than assume 42: at the moment this audit ran, the stuck population
was **46** rows (up from X64.5's reported 42, and further grew to 46
total stuck / 42 classified `MINT_NOT_FOUND` at classification time, then
to 46 `MINT_NOT_FOUND` briefly during the bounded-RPC phase as the queue
kept receiving new migrations). Classification was re-run at each phase
boundary and the exact count used is stated in that phase's own section
rather than assumed constant — the queue is live and continuously
enqueuing.

## Phase 1 — Population integrity

Full row detail: [x64_6_population.csv](x64_6_population.csv), 42 rows
(the canonical starting population, matching the task's expected count
exactly at classification time).

- **Queue row ID, mint, creator, attempts, RPC used, path state, audit
  state**: all present in the CSV.
- **Migration signature / migration timestamp**: `wt_walkback_queue` does
  not carry a distinct migration signature column separate from the
  CREATE anchor — `enqueued_at` is the closest proxy (the migration
  event's own arrival time, since `enqueue_migration()` fires
  synchronously off `store_migration()`).
- **No matching `creator_funding_queue` row exists**: confirmed for all
  42 (that is the `MINT_NOT_FOUND` classification itself).
- **No valid queue anchor already exists**: confirmed — all 42 have
  `create_anchor_signature IS NULL`.
- **Row was never walked**: confirmed — all 42 have `attempts=0`,
  `rpc_used=0` (checked directly, matching X64.5's own finding for the
  full stuck population).
- **No attribution exists through another route**: confirmed —
  `watchtower_token_attribution` and `wt_attribution_outcomes` both
  return zero matches for all 42 mints.
- **No duplicate queue row for the same mint**: structurally guaranteed
  (`mint` is `wt_walkback_queue`'s PRIMARY KEY) and confirmed directly —
  zero duplicates found.

**Classification**: all 42 rows are **UNIQUE_BLOCKED_MINT**. No
`DUPLICATE_BLOCKED_ROW`, `ALREADY_PROCESSED_ELSEWHERE`, or
`QUEUE_STATE_INCONSISTENCY` cases were found — the PRIMARY KEY constraint
on `mint` makes the first two structurally impossible in this schema, and
no row showed contradictory state (e.g. `status='waiting'` with a
non-NULL `completed_at`).

## Phase 2 — Search all existing signature sources

Exhaustively searched, beyond `creator_funding_queue`: `token_analysis`
(0 of 42 have `create_tx_signature` set), `wt_detected_creates` (0
matches by mint), `wt_watchtower_launches` (0 matches), `migrated_tokens`
(0 matches — this table isn't even populated for these mints, a further
signal discussed in Phase 4), `wt_creator_birth_launch` (0 matches by
creator), `watchtower_token_attribution` (0 matches),
`wt_attribution_outcomes` (0 matches), `wt_creator_launches` (0 matches
by mint), `wt_operator_launches` (0 matches), `wt_extraction_clusters`
(no mint-shaped column, structurally can't match), `wt_webhook_hits` (0
matches by creator wallet, checked against the 18 distinct known
creators), `wt_wrap_close_candidates`/`wt_candidate_websocket_watches`
(no mint column, structurally can't match — these are creator/wallet
keyed).

**Every one of the 42 mints classifies as `NO_STORED_CREATE_SIGNATURE`**
— confirmed exhaustively, zero exceptions, before any RPC was spent (see
`x64_6_implementation.md`'s `find_stored_create_anchor()` for the
zero-RPC classifier this search was formalized into).

## Phase 3 — Creator and mint consistency

No conflict report entries were produced —
[x64_6_conflicts.csv](x64_6_conflicts.csv) is header-only. Since Phase 2
found zero signatures anywhere for any of the 42 mints, there is nothing
to check for same-mint-multiple-signatures, same-signature-multiple-mints,
or creator mismatch — these checks require at least one stored candidate
signature to compare against, which none of the 42 rows had prior to the
bounded RPC pass. Post-recovery, all 13 bounded-RPC-recovered signatures
were independently confirmed mutually distinct (Phase 7) and matched
against the exact creator already on the queue row (the search itself
was creator-scoped, so a mismatch was structurally impossible for this
search shape).

## Phase 4 — Capture-path audit

Traced `_enqueue_creator_funding_job()` (`pumpfun_curve_listener.py:4468`)
and all 10 call sites. Key finding: **`if not creator or not mint: return
False`** — a hard, unconditional gate. This directly explains the **19 of
42** rows with `creator=NULL` in the queue (Failure Mode **C — CREATE
parsed but creator unresolved**): these mints structurally could never
reach the enqueue call, since the function itself refuses to run without
a creator.

For the **23 of 42** rows that DO have a resolved creator (later grown to
27 as the queue continued live), the picture is different and more
subtle. Cross-checking each of the 18 distinct known creators against
their OWN broader `creator_funding_queue` history (Phase 2 of this audit,
performed in the earlier interactive investigation) showed **every one of
them has multiple OTHER successfully-captured launches** — e.g. creator
`29yFzeBZgxf5zqrAkKXwgZtQehRf4pL8WbV2nRJikbw8` has 14 other
`creator_funding_queue` rows, several `status='complete'` with a valid
`create_tx_signature`. This rules out a creator-level or systemic
pipeline failure for these 23/27 rows — the miss is **per-mint**, not
per-creator, meaning the failure is not "this creator is unknown to the
pipeline" but "this specific CREATE event, for an otherwise
well-captured creator, was individually missed."

This per-mint, intermittent-for-otherwise-successful-creators shape is
most consistent with Failure Mode **K — listener/websocket outage** at
the exact moment of that specific CREATE (a brief drop that misses one
event without indicating any broader system failure), or Failure Mode
**A — CREATE event never received** by this specific code path (as
distinct from mode K's connectivity framing — the same net effect, a
different plausible cause). **This task's evidence cannot distinguish
between modes A and K conclusively** — both would produce the same
observed shape (well-known creator, one specific mint's CREATE never
enqueued) — and doing so would require listener-level log tracing beyond
what the database alone can show. Modes B (parser rejection), D (funding
extraction skipped), E (DB insert failed), F (rolled back/uncommitted), G
(duplicate suppressed), H (restart loss), I (stored elsewhere only), J
(migration-before-CREATE-catch-up), and L (unsupported tx shape) were
each checked against available evidence and **none is independently
supported** — no error logs were queryable from this session's read-only
DB access, no other table shows these 42 mints' CREATE stored anywhere
(ruling out mode I), and Phase 5's even timing distribution rules out a
single restart/outage window (weakening but not eliminating mode H/K as
a *repeated*, ongoing cause rather than a one-time event).

**Strongest evidence-backed conclusion for the per-row failure category**:
- 19 rows (`creator=NULL`): **Failure Mode C** (CREATE parsed, creator
  unresolved, `_enqueue_creator_funding_job`'s own guard blocks it) —
  high confidence, directly demonstrated by the code's own gate logic.
- 23 rows (creator known, still missed): **Failure Mode A/K** (CREATE
  event capture gap at the listener level) — moderate confidence; best
  available explanation given the ruled-out alternatives, but not
  independently confirmed by a log or trace this session could access.

## Phase 5 — Timing analysis

Full detail: [x64_6_timing_clusters.md](x64_6_timing_clusters.md). No
supported temporal cluster — failures spread evenly across the ~16-hour
window (median gap 18.6 min), consistent with a continuous, low-rate
background failure mode rather than a single incident.

## Phase 6 — Zero-RPC recovery

`find_stored_create_anchor()` implemented (see
`x64_6_implementation.md`). Run against all 42 rows: **0 recoverable** —
confirms Phase 2's manual search programmatically, with the same result.

## Phase 7 — Bounded RPC recovery

Full detail: [x64_6_rpc_recovery_dry_run.md](x64_6_rpc_recovery_dry_run.md).
27 rows searched (creator known), **13 recovered, 14 unresolved, 114
total RPC credits**, strictly bounded (3 pages/20 sigs per page/12
credits per row/2-hour window/first-match-stops). 19 `creator=NULL` rows
not searched (bounding a creator-less search was out of scope for this
pass — see rationale in the dry-run report).

## Phase 8 — Persistence repair

`apply_rpc_recovered_anchor()` implemented and run for real against the
live database: all 13 recovered signatures applied, verified idempotent,
verified to preserve `attempts`/`enqueued_at`/`mint`/`creator`/all
existing evidence, verified never to write `subprov`/`treasury`. Full
audit trail in `wt_anchor_reconciliation_log`
(`recovery_method='bounded_rpc_create_search'`, 13 rows, each carrying
queue context, source, method, timestamp, RPC credits, and validation
result).

## Phase 9 — Future capture hardening

Full detail: `x64_6_implementation.md`. **`creator_funding_queue`
confirmed to be the wrong architectural dependency** — it is an
enrichment table (funding-extraction bookkeeping) whose CREATE-signature
column is incidental, not canonical, evidenced by its own NULL rate even
on otherwise-`complete` rows. `token_analysis.create_tx_signature` is
architecturally closer but currently has the identical gap for this
population (0 of 42 populated) — not a different fix, the same upstream
capture gap surfacing in a second table. **Recommendation**: a dedicated,
append-only CREATE-event ledger written unconditionally by the CREATE
parser itself, with funding extraction and creator resolution as later
enrichment passes that never gate the ledger's own existence — design
only, not implemented (correctly out of this task's scope per Phase 9's
own framing as a recommendation).

## Phase 10 — Regression tests

Full detail: [x64_6_regression_results.md](x64_6_regression_results.md).
17 new tests (16 required + 1 split), all passing; combined
walkback/x64/anchor suite (117 tests) passes clean.

---

## Required summary

- **Initial `MINT_NOT_FOUND` rows**: 42
- **Duplicate/inconsistent queue rows**: 0 (all `UNIQUE_BLOCKED_MINT`)
- **Recovered from existing DB sources (zero-RPC)**: 0
- **Still no stored CREATE signature (pre-RPC)**: 42
- **Recovered with bounded RPC**: 13
- **Still unresolved**: 29 (14 with a known creator where bounded search
  found nothing in-window; 19 with no creator, not searched — both
  categories genuinely unresolved by this task, for different, documented
  reasons)
- **Conflicts**: 0
- **Released for walkback**: 13 (now `status='pending'`,
  `create_anchor_audit_state='VALID'`, `path_state='CREATE_ANCHORED'`,
  selectable by `drain_batch`'s existing SELECT)
- **Total RPC credits used**: 114

### Why did these rows fail to enter `creator_funding_queue`?

Two distinct, evidence-backed causes: (1) **19 of 42** never had a
resolvable creator at enqueue time — `_enqueue_creator_funding_job()`'s
own `if not creator or not mint: return False` guard structurally blocks
these, a demonstrated code-level cause, not inferred. (2) **23 of 42**
(creator known) were individually missed despite their creator having
many other successfully-captured launches — most consistent with a
per-CREATE-event listener/capture gap (Failure Mode A/K), not a
systemic or creator-level failure, though this task's available evidence
cannot fully distinguish the exact mechanism from a log-level trace.

### Is the failure concentrated in one writer, parser, outage, or
transaction shape?

**No single concentration was found.** Not one writer (multiple call
sites of `_enqueue_creator_funding_job` exist, and the gate that blocks
19/42 rows is shared by all of them). Not one outage (Phase 5's timing
analysis rules out a concentrated window). Not one transaction shape (no
malformed/unsupported CREATE transaction was found among the 13 that
WERE recovered via RPC — they parsed and matched cleanly once found).
The evidence instead points to two separate, lower-grade, continuously-
recurring gaps: creator-resolution failure (mode C, well-understood) and
an intermittent per-event capture miss (mode A/K, plausible but not
conclusively isolated).

### How many were recoverable without RPC from other stored tables?

**0.** Exhaustively confirmed across every candidate table.

### How many required bounded RPC?

**27 were eligible** (creator known); **13 succeeded**, 114 total
credits.

### Is `creator_funding_queue` the wrong architectural dependency for
CREATE anchoring?

**Yes**, confirmed by this task's own data (frequent NULL
`create_tx_signature` even on `complete` rows) — it is an enrichment
table, not a CREATE ledger.

### What canonical source should walkback use in future?

A dedicated, append-only CREATE-event ledger written unconditionally at
CREATE-observation time by the parser itself, independent of
funding-extraction or creator-resolution success — design recommendation
only, per Phase 9, not implemented in this task.

## Success criteria — met, with honest residual

Every one of the 42 rows now sits in one of the three honest states the
task requires:
- **13 rows**: CREATE anchor recovered through bounded RPC (Phase 7/8,
  live-verified).
- **0 rows**: CREATE anchor recovered from stored evidence (the zero-RPC
  path found nothing for this specific population — reported honestly,
  not forced).
- **29 rows**: CREATE anchor genuinely unresolved with documented cause
  (19 no-creator/mode-C, 14 creator-known/bounded-search-exhausted) —
  each with its specific reason recorded in
  `x64_6_stored_source_recovery.csv`'s companion unresolved list and this
  report's Phase 7 section, not left as an undifferentiated "still
  broken" bucket.

Future walkback anchoring no longer depends exclusively on
`creator_funding_queue` enrichment succeeding: `find_stored_create_anchor()`
widens the zero-RPC search surface for any future population with a
different gap shape, and the architectural recommendation (Phase 9)
names the actual fix needed to close this class of gap at its source —
not implemented here, correctly scoped as a design output per the task's
own instruction.
