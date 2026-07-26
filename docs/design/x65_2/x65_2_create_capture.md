# X65.2 — Phase 2: CREATE Capture Audit

Read-only, per-launch classification of what happened to CREATE-event
evidence for the 12 `UNRESOLVED` launches, using only existing log and
database evidence. No inference of facts not directly observed.

## Method

For each of the 12 mints, checked (in order): `token_analysis.create_tx_signature`,
`token_analysis.pf_ws_creator`/`earliest_tx_creator`, `wt_create_event_ledger`,
`wt_create_ledger_pending`, `wt_create_ledger_conflicts`, `webhook_birth_queue`,
and grepped all four retained log files
(`listener.log`, `.log.1`, `.log.2`, `.log.3`, spanning
2026-07-18T14:46 → present) for each mint's markers:
`[PUMPPORTAL] 🟢 Birth`, `[PREMIG_BIRTH_SEED]`, `[BIRTH] ⚠ Failed`,
`[CREATE_MINT_RESOLVED]`, `[EVENT] 🚀 MIGRATION DETECTED`,
`[DB] ✅ Created minimal token entry`.

## Key code paths identified

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

## Per-launch findings

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
always accompanies a birth). Its **total** absence across exactly this
12-launch set, while `🟢 Birth` fires for 10 of them, means: the
function was entered and its caller's post-call log line printed, but
the callee's own pre-return success log line did not — the only
place in `_insert_bonding_curve_token()` where this is possible is if
`sig` (the `create_tx_signature` argument) was empty/falsy at call
time, which would make `create_tx_signature` NULL in the `INSERT`
values tuple while everything else (including `creator` →
`pf_ws_creator`) still writes correctly. This matches every observed
row exactly: `pf_ws_creator` populated, `create_tx_signature` NULL.

(Note: `[PREMIG_BIRTH_SEED]` fires from inside `_insert_bonding_curve_token`
itself before that function returns to its caller in the `create`
handler, so its absence is not explained by a later overwrite —
whatever happened, happened inside this one call.)

A secondary, unrelated code defect was found while tracing this: the
handler's own in-process dedup guard (line 10816) checks
`mint not in self.completed_launches`, but line 10817 inserts `sig`
(the signature, not the mint) into that same set — a type confusion
that makes the mint-side check permanently vacuous (mints and
signatures never collide as strings). This does not explain the
`sig`-empty anomaly above, but it means the guard cannot correctly
prevent a mint from being processed twice via this path either;
flagged for Phase 7's fix design, not corrected here per the read-only
constraint.

## Classification (exactly one label per launch, not merged)

| Category | Launches | Count |
|---|---|---|
| **PERSIST_FAILED** | CmoCuZ9J2YT1QH, HHcXBLbnuSWdYi, EQZfBpWpQc5BEU, DpTtRHY6PSuxxJ, CvP9vVUCpoDuMd, 4WfoYERYFw3AQW, EDNvjVDjKVfRsq, c5Zye8yFd1AGrS, 9Mn2t7yX2TmSSM, FzNgpR11RYACas | 10 |
| **UNKNOWN** (log retention window does not cover the birth event; cannot distinguish NOT_OBSERVED from PERSIST_FAILED without evidence that has already rotated out of retention) | B3Fq8SqBtsxsWw, 71TKvknpvwRcjd | 2 |

Definitions applied exactly as specified in the task:
- **PERSIST_FAILED**: the CREATE event WAS observed (proven by the
  `[PUMPPORTAL] 🟢 Birth]` log line, `pf_ws_creator` populated from the
  same event) but the durable `create_tx_signature` field was never
  persisted, and no `wt_create_event_ledger` row exists — a capture
  that reached the application layer but did not fully persist.
- **UNKNOWN**: insufficient retained evidence to classify further (no
  birth log in the retained window at all, for either success or
  failure) — explicitly not guessed into NOT_OBSERVED or
  PERSIST_FAILED without direct evidence either way.

No launch is classified OBSERVED_NOT_PERSISTED, PURGED, or
PIPELINE_SKIPPED — none of the 12 show evidence matching those specific
definitions (OBSERVED_NOT_PERSISTED would require evidence of
observation with no persistence attempt at all, which doesn't fit since
`pf_ws_creator` IS persisted; PURGED would require evidence of a prior
persisted row later deleted, not observed; PIPELINE_SKIPPED would
require evidence the pipeline deliberately bypassed these mints, not
observed).

## What this means for Phase 3 (Funding Lineage)

Because `create_tx_signature` is NULL for all 12, and the funding-lineage
extraction pipeline (`extract_funding_for_new_token()`, per
`docs/CLAUDE.md`) is triggered from the CREATE-side path using the
resolved creator and CREATE context, a missing/failed
`create_tx_signature` persist is consistent with — and sufficient to
fully explain — funding-lineage extraction never having run for these
12 launches, independent of any separate funding-side failure. Phase 3
will verify this directly rather than assume it.
