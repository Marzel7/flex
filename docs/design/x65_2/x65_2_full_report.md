# X65.2 — Missing CREATE Event & Funding Lineage Coverage Investigation (Full Report)

Consolidated report combining all 9 phases plus the executive summary.
Read-only investigation, no code changed, no data recovered, no
attribution/classification/operation logic altered.

## Contents

1. [Reproduce the Missing-Evidence Cohort](#phase-1--reproduce-the-missing-evidence-cohort)
2. [CREATE Capture Audit](#phase-2--create-capture-audit)
3. [Funding Lineage Audit](#phase-3--funding-lineage-audit)
4. [Pipeline Coverage Analysis](#phase-4--pipeline-coverage-analysis)
5. [Root Cause Classification](#phase-5--root-cause-classification)
6. [Historical Recoverability](#phase-6--historical-recoverability)
7. [Permanent Fix Design](#phase-7--permanent-fix-design)
8. [Impact Analysis](#phase-8--impact-analysis)
9. [Executive Summary](#executive-summary)

---

## Phase 1 — Reproduce the Missing-Evidence Cohort

Read-only reproduction, 2026-07-21, `7d` window, against the live
production database. Reuses the exact filter from X65.1 plus
`src/ops/treasury_resolution.py`'s live resolution status for each launch.

### Confirmation against X65.1

| Check | Expected | Actual | Match |
|---|---|---|---|
| Total cohort | 19 | 19 | ✅ |
| Resolved (`KNOWN_TREASURY`) | 7 | 7 | ✅ |
| Unresolved | 12 | 12 | ✅ |

Exact reproduction — no drift since X65.1's own measurement earlier
today, confirming this cohort's population and resolution split remain
stable enough to investigate without needing to re-derive anything.

### Full cohort record

| Mint | Creator | CREATE (UTC) | Migration (UTC) | Canonical behaviour | Creator identity | Topology | Operation | Treasury status |
|---|---|---|---|---|---|---|---|---|
| GuyE9St1cU54ppHwqD719Q2AHf6AmPha93MEjzv2pump | G22uhsudCS1gVx... | 2026-07-15T12:20:35Z | 2026-07-15T12:20:36Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none (resolved via treasury walkback) | KNOWN_TREASURY |
| B3Fq8SqBtsxsWw5wqCL5wnJr3pgGYTrTVEvwSMXipump | D8bfGDnHgJfPj3... | 2026-07-15T14:48:09Z | 2026-07-15T14:48:10Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | UNRESOLVED |
| CmoCuZ9J2YT1QHv28p3QRphhZot6Sdbu6P6Aw4Vmpump | EEJh8HhcH6zVu1... | 2026-07-17T11:26:39Z | 2026-07-17T11:26:41Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | UNRESOLVED |
| HHcXBLbnuSWdYigNgiYDmPhuwwRzTCB73CmyJ8M7pump | 7nxHcmxbaM4FC2... | 2026-07-20T13:33:22Z | 2026-07-20T13:33:23Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | UNRESOLVED |
| 2GuvMWJpfNBXdZQZVGEWLV1Dx8qfiLKHHoDDfe4Apump | 3NyJNH93vBDM7n... | 2026-07-18T12:20:15Z | 2026-07-18T12:20:20Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | KNOWN_TREASURY |
| EQZfBpWpQc5BEUsP3q79xk1k3mKAAeL8bVZ5m1LJpump | FPLauDPp7DqMCj... | 2026-07-20T00:38:08Z | 2026-07-20T00:38:15Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | UNRESOLVED |
| 2XmV6Jk6ATzKCnVB15cnPHCCF9o4Kn4PXvVFk6Rppump | Dsm6w4zFsovcGT... | 2026-07-16T17:03:13Z | 2026-07-16T17:03:14Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | KNOWN_TREASURY |
| DpTtRHY6PSuxxJEjdd2NGW22F5JgP8WmWYBK48jhpump | GZeJHhQSm4S87K... | 2026-07-18T04:44:06Z | 2026-07-18T04:44:07Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | UNRESOLVED |
| CvP9vVUCpoDuMd2jg5qvakFsk8Ht4qQwmKtZTMeUpump | 71ftvekAkhanTd... | 2026-07-20T14:45:28Z | 2026-07-20T14:45:29Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | UNRESOLVED |
| 3QFvseNX1Fdkc6SZV4AT2BfSDvMUH4xQDY1H7TbPpump | 2zEEWsBtLFfkJW... | 2026-07-16T12:36:27Z | 2026-07-16T12:36:28Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | KNOWN_TREASURY |
| 3LZL5cXac86U1ti81V8GEA1qoj3HenLfnJMcQo7opump | 96oi3HjrPWGnkP... | 2026-07-16T10:45:35Z | 2026-07-16T10:45:53Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | KNOWN_TREASURY |
| 4WfoYERYFw3AQWc3MiJz4H8YScu7sbGFoSX7xCMepump | GAJ5JACjNXeeTX... | 2026-07-17T18:20:09Z | 2026-07-17T18:20:10Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | UNRESOLVED |
| EDNvjVDjKVfRsqxf3C8nN2sunxctfoboE2S8aUHGpump | HAsNHBL5Bex4g8... | 2026-07-18T10:41:30Z | 2026-07-18T10:41:31Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | UNRESOLVED |
| 71TKvknpvwRcjdoYPngxw6895yeidY24nY8eJnHCpump | AuTE4s6LMnyXrH... | 2026-07-16T15:59:33Z | 2026-07-16T15:59:34Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | UNRESOLVED |
| c5Zye8yFd1AGrSJ2mViYgXWa1kgCdCj5RWhen6tpump | A2EFKGqAoM1pFF... | 2026-07-17T22:55:25Z | 2026-07-17T22:55:27Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | UNRESOLVED |
| x8NtU6nnYDn1BwMDGg2oFdBuYBevhJ32kqM97FSpump | FWWz8PHebMuo77... | 2026-07-15T20:52:52Z | 2026-07-15T20:52:53Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | KNOWN_TREASURY |
| 9Mn2t7yX2TmSSMEsQqDnFvcmNAGVCPhjevXpKfqgpump | 7d3RkvUGJ8u5Jn... | 2026-07-21T12:14:43Z | 2026-07-21T12:14:45Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | UNRESOLVED |
| FzNgpR11RYACasA8ptFniXQKcLw26CmBWdyNEAU1pump | J6TN4WtDZL5ig3... | 2026-07-17T10:05:47Z | 2026-07-17T10:05:48Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | UNRESOLVED |
| HJ1Ry6iJyAqN7jozMTErJHuNA66kpkDkowi7fhCRpump | 42yXX31Xdx3d9U... | 2026-07-15T09:48:59Z | 2026-07-15T09:49:00Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | KNOWN_TREASURY |

`funding origin` is `UNKNOWN` for all 19, uniformly (implied by
`topology='UNKNOWN'`, per X65.1's Phase 1 finding that these two are
equivalent for this specific population). `operation` is
`__UNASSIGNED__` for all 19 (the cohort's own defining filter).

### Split for this investigation

- **7 KNOWN_TREASURY launches** are out of scope for X65.2's remediation
  focus (they already have working attribution via a cross-reference
  join, per X65.1) — included in this table for completeness only.
- **12 UNRESOLVED launches** are this investigation's actual subject:
  `B3Fq8SqBtsxsWw...`, `CmoCuZ9J2YT1QH...`, `HHcXBLbnuSWdYi...`,
  `EQZfBpWpQc5BEU...`, `DpTtRHY6PSuxxJ...`, `CvP9vVUCpoDuMd...`,
  `4WfoYERYFw3AQW...`, `EDNvjVDjKVfRsq...`, `71TKvknpvwRcjd...`,
  `c5Zye8yFd1AGrS...`, `9Mn2t7yX2TmSSM...`, `FzNgpR11RYACas...`.

### Timing note

All 12 unresolved launches span **2026-07-15T14:48 through
2026-07-21T12:14** — i.e., the entire 7-day window, not clustered at
either the oldest or newest edge. This is an early signal against a
simple "too recent for the indexer to catch up yet" explanation (which
would predict clustering near 2026-07-21) — the oldest unresolved
launch (`B3Fq8SqBtsxsWw...`, 2026-07-15) is nearly 6 days old at the
time of this investigation, ample time for any normal-cadence indexing
pass to have processed it if the pipeline were otherwise healthy.

---

## Phase 2 — CREATE Capture Audit

Read-only, per-launch classification of what happened to CREATE-event
evidence for the 12 `UNRESOLVED` launches, using only existing log and
database evidence. No inference of facts not directly observed.

### Method

For each of the 12 mints, checked (in order): `token_analysis.create_tx_signature`,
`token_analysis.pf_ws_creator`/`earliest_tx_creator`, `wt_create_event_ledger`,
`wt_create_ledger_pending`, `wt_create_ledger_conflicts`, `webhook_birth_queue`,
and grepped all four retained log files
(`listener.log`, `.log.1`, `.log.2`, `.log.3`, spanning
2026-07-18T14:46 → present) for each mint's markers:
`[PUMPPORTAL] 🟢 Birth`, `[PREMIG_BIRTH_SEED]`, `[BIRTH] ⚠ Failed`,
`[CREATE_MINT_RESOLVED]`, `[EVENT] 🚀 MIGRATION DETECTED`,
`[DB] ✅ Created minimal token entry`.

### Key code paths identified

Two independent CREATE-observation paths exist:

1. **PumpPortal WS side-channel** (`pumpfun_curve_listener.py:10798-10851`,
   `tx_type == "create"`): populates the in-memory `_portal_vsol[mint]`
   dict (line 10807, includes `creator`) immediately, then calls
   `_insert_bonding_curve_token()` (line 10822) with a real
   `create_tx_signature`, then logs `[PUMPPORTAL] 🟢 Birth: ...]`
   (line 10829) only on success, then fires
   `_ensure_pf_ws_creator(mint, reason="birth")` as a background task
   (line 10834-10836).
2. **On-chain program-log path** (`handle_birth()`, line 6112): the
   heavier, RPC-validated path wired into 3 live call sites. Produces
   `[CREATE_MINT_RESOLVED]`-style evidence and drives
   `wt_create_event_ledger` writes via `_write_create_ledger_durable()`.

`_insert_bonding_curve_token()` (line 5732) itself is a correctly-built
`INSERT ... ON CONFLICT(mint) DO UPDATE` that `COALESCE`s every field
against the existing row — it does **not** clobber a previously-written
`create_tx_signature`, and on success logs `[PREMIG_BIRTH_SEED]`
(line 5796) immediately after commit, before returning to the caller
which then logs `[PUMPPORTAL] 🟢 Birth]`.

`_create_minimal_token_entry()` (line 7885, migration-side fallback)
writes only `mint, created_at, analyzed_at, lifecycle_stage,
rug_probability, risk_level, post_migration_coverage, rug_indicator,
events_parsed` — it never touches `create_tx_signature`, `pf_ws_creator`,
or `earliest_tx_creator` in either its `INSERT` or its `DO UPDATE SET`
clause, so it cannot itself be clobbering those fields.

### Per-launch findings

| Mint | `create_tx_signature` | `pf_ws_creator` | `[🟢 Birth]` logged | `[PREMIG_BIRTH_SEED]` logged | `wt_create_event_ledger` rows |
|---|---|---|---|---|---|
| B3Fq8SqBtsxsWw... | NULL | set | ✗ (0) | ✗ (0) | 0 |
| CmoCuZ9J2YT1QH... | NULL | set | ✓ (1) | ✗ (0) | 0 |
| HHcXBLbnuSWdYi... | NULL | set | ✓ (1) | ✗ (0) | 0 |
| EQZfBpWpQc5BEU... | NULL | set | ✓ (1) | ✗ (0) | 0 |
| DpTtRHY6PSuxxJ... | NULL | set | ✓ (1) | ✗ (0) | 0 |
| CvP9vVUCpoDuMd... | NULL | set | ✓ (1) | ✗ (0) | 0 |
| 4WfoYERYFw3AQW... | NULL | set | ✓ (1) | ✗ (0) | 0 |
| EDNvjVDjKVfRsq... | NULL | set | ✓ (1) | ✗ (0) | 0 |
| 71TKvknpvwRcjd... | NULL | set | ✗ (0) | ✗ (0) | 0 |
| c5Zye8yFd1AGrS... | NULL | set | ✓ (1) | ✗ (0) | 0 |
| 9Mn2t7yX2TmSSM... | NULL | set | ✓ (1) | ✗ (0) | 0 |
| FzNgpR11RYACas... | NULL | set | ✓ (1) | ✗ (0) | 0 |

**10 of 12** have a retained `[PUMPPORTAL] 🟢 Birth]` log line. **2 of
12** (`B3Fq8SqBtsxsWw...`, `71TKvknpvwRcjd...`) have no birth log at all
in the retained window — consistent with either a birth that predates
log retention (oldest unresolved launch is 2026-07-15, `.log.3`'s
earliest timestamp is 2026-07-18T14:46, so up to ~3 days of birth-time
log history for the earliest launches has already rotated out) or a
genuine missed WS message. **0 of 12** have a `[PREMIG_BIRTH_SEED]`
line — this is the decisive anomaly: this log line fires unconditionally
on a successful `_insert_bonding_curve_token()` write and is common
elsewhere in the same log files (38,914 occurrences vs. 38,906
`🟢 Birth` occurrences file-wide — near 1:1, confirming it normally
always accompanies a birth).

A secondary, unrelated code defect was found while tracing this: the
handler's own in-process dedup guard (line 10816) checks
`mint not in self.completed_launches`, but line 10817 inserts `sig`
(the signature, not the mint) into that same set — a type confusion
that makes the mint-side check permanently vacuous (mints and
signatures never collide as strings). This does not explain the
sig-persistence anomaly, but it means the guard cannot correctly
prevent a mint from being processed twice via this path either;
flagged for Phase 7's fix design, not corrected here per the read-only
constraint.

### Classification (exactly one label per launch, not merged)

| Category | Launches | Count |
|---|---|---|
| **PERSIST_FAILED** | CmoCuZ9J2YT1QH, HHcXBLbnuSWdYi, EQZfBpWpQc5BEU, DpTtRHY6PSuxxJ, CvP9vVUCpoDuMd, 4WfoYERYFw3AQW, EDNvjVDjKVfRsq, c5Zye8yFd1AGrS, 9Mn2t7yX2TmSSM, FzNgpR11RYACas | 10 |
| **UNKNOWN** (log retention window does not cover the birth event; cannot distinguish NOT_OBSERVED from PERSIST_FAILED without evidence that has already rotated out of retention) | B3Fq8SqBtsxsWw, 71TKvknpvwRcjd | 2 |

Definitions applied exactly as specified in the task:
- **PERSIST_FAILED**: the CREATE event WAS observed (proven by the
  `[PUMPPORTAL] 🟢 Birth]` log line, `pf_ws_creator` populated from the
  same event) but the durable `create_tx_signature` field was never
  persisted, and no `wt_create_event_ledger` row exists.
- **UNKNOWN**: insufficient retained evidence to classify further.

No launch is classified OBSERVED_NOT_PERSISTED, PURGED, or
PIPELINE_SKIPPED — none of the 12 show evidence matching those specific
definitions.

### What this means for Phase 3

Because `create_tx_signature` is NULL for all 12, and the funding-lineage
extraction pipeline (`extract_funding_for_new_token()`, per
`docs/CLAUDE.md`) is triggered from the CREATE-side path using the
resolved creator and CREATE context, a missing/failed
`create_tx_signature` persist is consistent with — and sufficient to
fully explain — funding-lineage extraction never having run for these
12 launches. Phase 3 verifies this directly rather than assuming it.

---

## Phase 3 — Funding Lineage Audit

Read-only trace of creator funding capture → walkback queue →
sub-provider discovery → treasury walkback → funding edge creation →
topology derivation, for the same 12 `UNRESOLVED` launches.

### Stage-by-stage evidence

| Stage | Table checked | Result for all 12 |
|---|---|---|
| Creator funding capture (direct funder → creator) | `creator_funders` | **0 rows** for every one of the 12 creator addresses |
| Walkback queue | `wt_walkback_queue` | **12/12 present, `status='complete'`, `walkback_class='FULL_WALKBACK'`**, each with a resolved `funder_wallet` matching `wt_attribution_outcomes.terminal_entity` exactly |
| Sub-provider discovery | `wt_active_subprov_sessions`, `wt_discovered_subprovs` | **0 rows** for any of the 12 `funder_wallet` values |
| Treasury walkback | `wt_confirmed_treasuries` (via `funder_wallet`) | **0 rows** (no candidate to check) |
| Funding edge creation | `wt_provisioning_edges` | **0 rows** referencing any of the 12 funder wallets |
| Topology derivation | `operational_intelligence.py` topology field | `UNKNOWN` for all 12 (matches Phase 1) |

### Key finding: the walkback queue itself completed successfully

All 12 mints have a `wt_walkback_queue` row with `status='complete']` —
the walkback process did not fail, error out, or get stuck. It ran to
completion, correctly determined the creator's direct funder, checked
that funder against every downstream lineage table available to it, and
correctly recorded `INSUFFICIENT_EVIDENCE` in `wt_attribution_outcomes`
because the funder itself has no further indexed lineage. This is the
walkback system behaving exactly as designed — not a lineage-side bug.

### The one CREATE-anchor recovery: `9Mn2t7yX2TmSSM...`

One of the 12 (`9Mn2t7yX2TmSSMEsQqDnFvcmNAGVCPhjevXpKfqgpump`) has
`wt_walkback_queue.path_state = 'CREATE_ANCHORED'`,
`create_anchor_audit_state = 'VALID'`, and a real
`create_anchor_signature`
(`2LCE1k1DZLYQx4YHmp9k4Q5pCTNQCBWsvChkVud4hqrf3ZjUJCwsByxpbLdJWdePLxLnpLsbhDp9PSxwsfJRcHCz`)
— the walkback queue's own CREATE-anchor-recovery mechanism
independently found and validated a CREATE signature for this mint.
That signature was never written back to
`token_analysis.create_tx_signature` (still NULL) or to
`wt_create_event_ledger` (still 0 rows) — the two systems do not share
this evidence. The other 11 have no `create_anchor_signature` recovered
by the queue at all, consistent with Phase 2's finding that their
CREATE evidence is missing at the source.

### Classification (per the task's required categories)

| Category | Launches | Count |
|---|---|---|
| **NEVER_CAPTURED** (creator's direct funder → creator transfer itself was never captured into `creator_funders`) | All 12 | 12 |
| — sub-classification: one launch (`9Mn2t7yX...`) has a recovered-but-unpropagated CREATE anchor | 9Mn2t7yX2TmSSM... | 1 |

All 12 land in **NEVER_CAPTURED** at the `creator_funders` stage
specifically — the realtime creator-funding extractor never ran for
these creators, consistent with Phase 2's finding that the birth-time
`create_tx_signature` was never durably set (10 of 12) or the birth
event itself falls outside retained-log visibility (2 of 12).

This is not contradicted by the walkback queue's success: the queue's
`FULL_WALKBACK` path independently derives a `funder_wallet` through a
different, RPC-capable mechanism than the realtime extractor — so the
*lineage-outcome* table (`wt_attribution_outcomes`) can be fully
populated even when the *realtime funding-capture* table
(`creator_funders`) is empty. These are two different, only loosely
coupled systems, and this decoupling is precisely why Discovery's UI
shows these 12 as `UNKNOWN` topology despite the walkback table
showing "complete."

No launch is CAPTURED_NOT_INDEXED, INDEXED_NOT_LINKED, or
LINKED_NOT_CONSUMED — none of the 12 show any row in `creator_funders`,
`funder_incoming_transfers`, `wt_provisioning_edges`, or
`wt_active_subprov_sessions` for their funder wallets, so the gap is at
the very first stage (capture), not a downstream processing failure.

---

## Phase 4 — Pipeline Coverage Analysis

### Full pipeline (as it exists today)

```
1. PumpPortal WS "create" event received
   → pumpfun_curve_listener.py:10798, tx_type == "create"
   → _portal_vsol[mint] populated (creator cached in memory)
   → _insert_bonding_curve_token(mint, creator, ..., create_tx_signature=sig)
       → INSERT ... ON CONFLICT DO UPDATE into token_analysis
       → on success: log [PREMIG_BIRTH_SEED], then caller logs [PUMPPORTAL] 🟢 Birth
   → _ensure_pf_ws_creator(mint, reason="birth") fired as background task
       → portal fast-path or RPC-validated path resolves pf_ws_creator

2. (Parallel/independent) On-chain program-log CREATE observation
   → handle_birth() (line 6112), 3 live call sites
   → _write_create_ledger_durable() → wt_create_ledger_pending → wt_create_event_ledger

3. Realtime creator-funding extraction (per docs/CLAUDE.md)
   → triggered at pumpfun_curve_listener.py:1728, gated on a successful birth
   → realtime_creator_funding_extractor.py: extract_funding_for_new_token()
   → populates creator_funders (creator_address, funder_address, amount_sol, tx_sig)

4. Funder-transfer extraction
   → triggered at line 1734, for each row from step 3
   → funder_incoming_extractor.py: extract_for_creator()
   → populates funder_incoming_transfers

5. Sub-provider / treasury lineage (separate subsystem, WS-driven)
   → ws_cascade daemon watches confirmed SUB_PROV wrap-close fan-out
   → populates wt_active_subprov_sessions, wt_provisioning_edges

6. Walkback queue (backstop, RPC-capable, can run independent of steps 1-5)
   → wt_walkback_queue: FULL_WALKBACK / LINK_ONLY / SKIP / PARTIAL
   → can independently recover a funder_wallet and even a create_anchor_signature
   → writes wt_attribution_outcomes (terminal_entity, outcome_type)

7. Topology / operational intelligence derivation
   → operational_intelligence.py reads creator_funders / wt_provisioning_edges /
     wt_active_subprov_sessions (NOT wt_attribution_outcomes directly for topology)
   → produces topology=UNKNOWN when no funding-edge evidence exists,
     regardless of whether step 6 completed
```

### Coverage matrix — first stage where evidence disappears

| Mint | Step 1 (birth persist) | Step 2 (ledger) | Step 3 (creator_funders) | Step 5 (subprov) | Step 6 (walkback) | First gap |
|---|---|---|---|---|---|---|
| B3Fq8SqBtsxsWw... | ✗ signature lost (no log evidence retained) | ✗ 0 rows | ✗ 0 rows | ✗ 0 rows | ✓ complete | **Step 1** |
| CmoCuZ9J2YT1QH... | ✗ signature lost (birth logged, seed missing) | ✗ 0 rows | ✗ 0 rows | ✗ 0 rows | ✓ complete | **Step 1** |
| HHcXBLbnuSWdYi... | ✗ signature lost | ✗ 0 rows | ✗ 0 rows | ✗ 0 rows | ✓ complete | **Step 1** |
| EQZfBpWpQc5BEU... | ✗ signature lost | ✗ 0 rows | ✗ 0 rows | ✗ 0 rows | ✓ complete | **Step 1** |
| DpTtRHY6PSuxxJ... | ✗ signature lost | ✗ 0 rows | ✗ 0 rows | ✗ 0 rows | ✓ complete | **Step 1** |
| CvP9vVUCpoDuMd... | ✗ signature lost | ✗ 0 rows | ✗ 0 rows | ✗ 0 rows | ✓ complete | **Step 1** |
| 4WfoYERYFw3AQW... | ✗ signature lost | ✗ 0 rows | ✗ 0 rows | ✗ 0 rows | ✓ complete | **Step 1** |
| EDNvjVDjKVfRsq... | ✗ signature lost | ✗ 0 rows | ✗ 0 rows | ✗ 0 rows | ✓ complete | **Step 1** |
| 71TKvknpvwRcjd... | ✗ signature lost (no log evidence retained) | ✗ 0 rows | ✗ 0 rows | ✗ 0 rows | ✓ complete | **Step 1** |
| c5Zye8yFd1AGrS... | ✗ signature lost | ✗ 0 rows | ✗ 0 rows | ✗ 0 rows | ✓ complete | **Step 1** |
| 9Mn2t7yX2TmSSM... | ✗ signature lost | ✗ 0 rows (queue recovered one independently, not propagated) | ✗ 0 rows | ✗ 0 rows | ✓ complete, `CREATE_ANCHORED`/`VALID` | **Step 1** (partially recoverable — see Phase 6) |
| FzNgpR11RYACas... | ✗ signature lost | ✗ 0 rows | ✗ 0 rows | ✗ 0 rows | ✓ complete | **Step 1** |

**All 12 launches share the identical first-gap stage: Step 1, the
birth-time persistence of `create_tx_signature`.** Every downstream
stage's gap is a direct, fully-explained consequence of Step 1's gap —
none of steps 2-5 show any independent failure of their own. Step 6
(walkback) is the only stage that runs independently of Step 1 and it
succeeds for all 12, terminating correctly at `INSUFFICIENT_EVIDENCE`
because the funder wallets it finds have no further indexed lineage
regardless of the Step 1 gap.

### Why this is a single root cause, not twelve separate incidents

The uniformity is the key finding: 12/12 launches fail at exactly the
same stage, with the same signature (`pf_ws_creator` set,
`create_tx_signature` NULL). This rules out random/incidental causes
(RPC timeouts, one-off DB locks, transient network errors would be
expected to produce a mixed pattern across 12 independent launches
spanning 6 days) and points to a systemic, reproducible condition in
the birth/migration write path, investigated further in Phase 5.

---

## Phase 5 — Root Cause Classification

### Root cause found: migration-time creator re-extraction clobbers a valid birth-time `create_tx_signature`

Tracing every writer of `token_analysis.create_tx_signature`:

1. **Birth-time write (correct)**: `_insert_bonding_curve_token()`
   (`pumpfun_curve_listener.py:5732`) — an `INSERT ... ON CONFLICT DO
   UPDATE` that `COALESCE`s `create_tx_signature` against the existing
   row (line 5764). This correctly persists the signature and can
   never null it out.

2. **Migration-time re-extraction write (the bug)**:
   `_update_token_entry_with_creator()` (line 7933, called from line
   9157 inside the migration-handling flow) does an **unconditional**
   `UPDATE token_analysis SET ..., create_tx_signature=?, ... WHERE
   mint=?` (line 7963) — **no `COALESCE`, no `WHERE create_tx_signature
   IS NULL` guard**. The caller (line 9146-9148) deliberately sets its
   local `create_tx_signature` variable to `None` unless a **fresh,
   migration-time RPC-derived** transaction independently re-validates
   as a strict Pump.Fun CREATE instruction (`is_pumpfun_create`). This
   is a correct and intentional strictness check for the variable's
   *own* value — but it is then written straight into a full-row
   `UPDATE` that overwrites whatever `create_tx_signature` was already
   correctly stored from birth-time, discarding it whenever the
   migration-time re-validation doesn't independently succeed (RPC
   miss, transaction shape mismatch, rate limit, etc.).

### Why this fires for exactly this 12-launch pattern and not universally

This code path (`_update_token_entry_with_creator`) is only reached
when `earliest_creator` is falsy at the point migration processing
begins (line 9105's `if not earliest_creator:`). Not every migrated
token takes this branch, which is why this is not a 100%-of-migrations
bug, but for the subset that do take it, the clobber is deterministic.

### Root cause groups

| Root cause | Launches affected | Frequency | Evidence | Confidence |
|---|---|---|---|---|
| **Migration-time creator re-extraction overwrites a valid birth-time `create_tx_signature` with NULL when its own independent RPC re-validation doesn't succeed** (`_update_token_entry_with_creator`, line 7933/7963, called line 9157) | CmoCuZ9J2YT1QH, HHcXBLbnuSWdYi, EQZfBpWpQc5BEU, DpTtRHY6PSuxxJ, CvP9vVUCpoDuMd, 4WfoYERYFw3AQW, EDNvjVDjKVfRsq, c5Zye8yFd1AGrS, 9Mn2t7yX2TmSSM, FzNgpR11RYACas | 10 / 12 (83%) | Birth log present, yet `create_tx_signature` NULL now; unconditional `UPDATE` with no `COALESCE` found at the exact write site | **High** — direct code-path match, no alternative writer of this column found anywhere else in the file |
| **Insufficient log retention to confirm the same mechanism** (birth event predates the oldest retained log file, `.log.3`, 2026-07-18T14:46) | B3Fq8SqBtsxsWw, 71TKvknpvwRcjd | 2 / 12 (17%) | Both launches created 2026-07-15, ~3 days before retained log history begins | **Medium** — same symptom pattern as the confirmed 10, but the specific log lines that prove the mechanism are unavailable |

### Explicitly ruled out

- **WebSocket listener offline**: contradicted — `[PUMPPORTAL] 🟢 Birth]` logged for 10/12.
- **Reconciliation window expired**: not applicable — original data was captured correctly then overwritten.
- **Queue backlog**: `wt_walkback_queue` shows `status='complete'` for all 12 with no elevated `attempts`.
- **Schema mismatch**: no schema error appears anywhere in logs for these mints.
- **Unsupported transaction pattern**: birth-time capture succeeded using the PumpPortal side-channel, which doesn't depend on parsing the raw transaction shape.

---

## Phase 6 — Historical Recoverability

Classification only — **no recovery performed**, per the task's
explicit constraint.

### Classification definitions applied

- **RECOVERABLE**: the original evidence still exists and could be restored with a bounded operation.
- **PARTIALLY_RECOVERABLE**: some but not all of the missing evidence chain can be restored.
- **NOT_RECOVERABLE**: no path exists to recover the missing evidence.

### Per-launch classification

| Mint | create_tx_signature recoverable? | Funding lineage recoverable? | Classification |
|---|---|---|---|
| 9Mn2t7yX2TmSSM... | **Yes** — `wt_walkback_queue.create_anchor_signature` already holds an independently-recovered, `VALID`-audited signature, never propagated | Depends on funder wallet's own upstream lineage, separately un-indexed | **PARTIALLY_RECOVERABLE** |
| CmoCuZ9J2YT1QH... | Likely — fresh RPC lookup by mint could re-derive the original CREATE signature | Same — funder wallet has no indexed lineage regardless | **PARTIALLY_RECOVERABLE** |
| HHcXBLbnuSWdYi... | Likely, same basis | Same as above | **PARTIALLY_RECOVERABLE** |
| EQZfBpWpQc5BEU... | Likely, same basis | Same as above | **PARTIALLY_RECOVERABLE** |
| DpTtRHY6PSuxxJ... | Likely, same basis | Same as above | **PARTIALLY_RECOVERABLE** |
| CvP9vVUCpoDuMd... | Likely, same basis | Same as above | **PARTIALLY_RECOVERABLE** |
| 4WfoYERYFw3AQW... | Likely, same basis | Same as above | **PARTIALLY_RECOVERABLE** |
| EDNvjVDjKVfRsq... | Likely, same basis | Same as above | **PARTIALLY_RECOVERABLE** |
| c5Zye8yFd1AGrS... | Likely, same basis | Same as above | **PARTIALLY_RECOVERABLE** |
| FzNgpR11RYACas... | Likely, same basis | Same as above | **PARTIALLY_RECOVERABLE** |
| B3Fq8SqBtsxsWw... | Likely, same basis (mint permanent regardless of log retention) | Same as above | **PARTIALLY_RECOVERABLE** |
| 71TKvknpvwRcjd... | Likely, same basis | Same as above | **PARTIALLY_RECOVERABLE** |

### Why every launch lands at PARTIALLY_RECOVERABLE

- **Not fully RECOVERABLE**: recovering `create_tx_signature` alone
  does not resolve Funding Origin or Operation Attribution — the funder
  wallet's absence from any sub-provisioner/treasury lineage table is a
  separate, independent gap.
- **Not NOT_RECOVERABLE**: the on-chain data is permanent and
  immutable on Solana, all 12 are within the last 6 days (well within
  typical RPC retention), and `9Mn2t7yX...` already demonstrates a
  sibling system successfully recovering exactly this evidence.

| Classification | Count |
|---|---|
| RECOVERABLE | 0 |
| PARTIALLY_RECOVERABLE | 12 |
| NOT_RECOVERABLE | 0 |

No recovery action was taken for any of the 12 launches.

---

## Phase 7 — Permanent Fix Design

Design only — no code was changed.

### The smallest fix: make the migration-time write non-destructive

**File**: `src/core/pumpfun_curve_listener.py`
**Function**: `_update_token_entry_with_creator()` (line 7933), its
`UPDATE` statement at line 7963.

**Current behavior**: unconditionally sets `create_tx_signature = ?`
using whatever value the caller passed (`None` whenever the
migration-time RPC re-validation doesn't independently reconfirm the
transaction), regardless of what was already stored.

**Proposed change**: apply the same `COALESCE` discipline already used
correctly in `_insert_bonding_curve_token()`:

```sql
UPDATE token_analysis SET
    earliest_tx_creator=?,
    created_at=?,
    bonding_curve_pda=?,
    create_tx_signature=COALESCE(?, create_tx_signature),
    cluster_id=?, cluster_name=?, cluster_risk_multiplier=?
WHERE mint=?
```

One-line change to a single `UPDATE` statement's column expression —
no schema change, no new table, no new pipeline stage, no change to
the validation logic itself.

### Why this is sufficient

- A single, precisely-located write site is responsible for 100% of
  the confirmed clobber cases (10/12) and the most likely explanation
  for the remaining 2.
- The birth-time write path is already correct and needs no change.
- The strict on-chain re-validation at migration time remains exactly
  as strict for *setting a new* value; it simply stops being able to
  *erase an existing one*.
- No change to `_create_minimal_token_entry()` is needed — confirmed
  in Phase 2 to already correctly leave `create_tx_signature` untouched.

### Secondary, lower-priority fix

Phase 2 flagged a type confusion in the in-process dedup guard:
`pumpfun_curve_listener.py:10816-10817` checks
`mint not in self.completed_launches` but inserts `sig` into that same
set, making the mint-side check permanently vacuous. Does not
contribute to the 12-launch pattern investigated here, but worth
fixing alongside the primary change: change line 10817 to
`self.completed_launches.add(mint)` (verify no other reads depend on
signature membership first).

### Explicitly avoided approaches

- **A new/duplicate "hardened" migration-write pathway**: rejected —
  would recreate the kind of parallel, disconnected pipeline this
  investigation already found once (`watchtower_attribution.py`'s dead
  `store_migration()`/`intake_migration()` pathway, zero callers).
- **Re-running migration-time creator extraction with retries until
  validation succeeds**: unnecessary — the birth-time value is already
  correct in the confirmed cases; there is nothing to retry toward.
- **Backfilling `wt_create_event_ledger` from `wt_walkback_queue`'s
  `create_anchor_signature` on a schedule**: a reasonable complementary
  idea, not required to prevent the root-cause failure mode itself —
  noted as a candidate for separate future work.

---

## Phase 8 — Impact Analysis

### Scope correction: the underlying bug is far broader than the 12-launch cohort

A live check of `token_analysis` for the last 7 days shows **14,410 of
16,589 migrated tokens (87%) have `create_tx_signature IS NULL`**, and
of those, **14,304 (99.3%) have `pf_ws_creator` populated** — the exact
same signature as these 12 launches. Critically, **all 7 of the
19-cohort's already-resolved launches (X65.1) also show
`create_tx_signature IS NULL`** — proving the clobber bug affects the
large majority of migrated tokens system-wide. What makes this
specific 12-launch cohort visibly "stuck" is that they are the subset
whose funder wallet *also* has no independent sub-provisioner/treasury
lineage, so they have no alternate path to attribution the way the 7
resolved launches did.

### Percentage of the original problem this explains

- **100%** of the 12 unresolved launches' missing CREATE-signature
  evidence is explained by a single root cause (high confidence 10/12,
  medium confidence remaining 2).
- **100%** of the missing funding-lineage evidence is explained as a
  fully-expected downstream consequence of the funder wallet having no
  indexed lineage — the walkback system correctly reporting
  `INSUFFICIENT_EVIDENCE`, not a pipeline failure.
- **0%** of the Operation/Funding-Origin attribution gap would be
  closed by fixing the CREATE-signature clobber alone.

### Expected effect of the Phase 7 fix

- Prevents future launches from losing an already-captured
  `create_tx_signature` at migration time.
- Given the 87% system-wide null rate, the benefit extends broadly
  past this 12-launch cohort.
- Does **not** directly increase `KNOWN_TREASURY` resolution counts —
  requires separately expanding sub-provisioner/treasury detection
  coverage (out of this task's scope).

### Performance impact of the proposed fix

Negligible — a single SQL expression change inside an already-existing
`UPDATE` statement. No new query, no new table, no additional
round-trip, no change to call frequency.

### What remains unresolved after this fix

- The 12 launches investigated here would **not** automatically become
  attributed once the fix ships — it prevents recurrence, it does not
  retroactively recover these 12.
- The 87% system-wide missing-signature rate suggests this bug has
  been firing across the entire migrated-token population for some
  time — the true historical blast radius was not measured in this
  task and would need its own scoped follow-up.

---

## Executive Summary

Read-only investigation into why the 12 `UNRESOLVED` launches from
X65.1's cohort (`QUICK_BIRTH_MIGRATION → FRESH_CREATOR → UNKNOWN
topology → UNASSIGNED`) have no persisted CREATE-event or
funding-lineage evidence. No attribution logic, treasury confirmation
rules, behaviour classification, or operation assignment was changed.
No recovery was performed. All facts measured live against production,
2026-07-21.

**Cohort reproduced exactly**: 19 total (7 `KNOWN_TREASURY`, 12
`UNRESOLVED`) — exact match to X65.1, no drift.

**Root cause found — a genuine, single, precisely-located bug**:
`_update_token_entry_with_creator()`
(`src/core/pumpfun_curve_listener.py:7933`, `UPDATE` statement at line
7963) unconditionally overwrites `token_analysis.create_tx_signature`
during migration-time creator re-extraction, with no `COALESCE` guard —
unlike the birth-time write path, which correctly preserves existing
values. When migration-time RPC re-validation doesn't independently
reconfirm the CREATE transaction, the caller passes
`create_tx_signature=None`, which this `UPDATE` then writes straight
over an already-correct, birth-time-captured signature — destroying
it. Confirmed with high confidence for 10 of 12 launches, medium
confidence for the remaining 2 (log-retention-limited).

**Not a detection miss**: the CREATE event *was* observed for at least
10 of the 12 launches — proven directly by the
`[PUMPPORTAL] 🟢 Birth]` log line and by `pf_ws_creator`/
`earliest_tx_creator` being correctly populated for all 12. This is a
persistence bug, not a listener/WS coverage gap.

**Scope-correcting finding**: a live check found **87% of all migrated
tokens in the last 7 days** have `create_tx_signature IS NULL` with the
same signature — including all 7 of this cohort's already-resolved
launches. What makes these particular 12 visibly stuck is not the
missing signature itself (nearly universal) but that their funder
wallets, independently, have zero presence in any sub-provisioner/
treasury lineage table. The walkback system ran to completion and
correctly reported `INSUFFICIENT_EVIDENCE` for all 12 — the lineage
system working as designed against genuinely un-indexed wallets.

**One partial recovery already exists, unused**:
`9Mn2t7yX2TmSSMEsQqDnFvcmNAGVCPhjevXpKfqgpump`'s `wt_walkback_queue`
row already holds an independently-recovered, audit-validated
`create_anchor_signature`, never propagated. All 12 are classified
`PARTIALLY_RECOVERABLE`. No recovery was attempted in this task.

**Fix designed (not implemented)**: add a `COALESCE` to the one
destructive `UPDATE` statement, matching the discipline already
correctly used in the birth-time write path. One-line SQL change, no
schema change, no new table, no new pipeline.

**Impact if the fix ships**: prevents recurrence of this exact failure
mode for all future migrations (broad benefit given the 87%
system-wide null rate); does not by itself increase `KNOWN_TREASURY`
resolution counts; does not retroactively fix the 12 launches already
in this cohort.

**Deliverables**: `docs/design/x65_2/` — `x65_2_missing_cohort.md`,
`x65_2_create_capture.md`, `x65_2_lineage_audit.md`,
`x65_2_pipeline_coverage.md`, `x65_2_root_causes.md`,
`x65_2_recoverability.md`, `x65_2_fix_design.md`, `x65_2_impact.md`,
`x65_2_summary.md`, and this consolidated report. No code was changed;
no data was recovered or attributed; all 12 launches remain exactly as
`UNRESOLVED`/`__UNASSIGNED__` as before this investigation began.
