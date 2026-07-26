# X65.2 — Phase 5: Root Cause Classification

## Root cause found: migration-time creator re-extraction clobbers a valid birth-time `create_tx_signature`

Re-examining Phase 2 in light of Phase 4's uniform Step-1 gap: the
initial theory (`sig` empty at the `_insert_bonding_curve_token()` call
site) is contradicted by the fact that `[PUMPPORTAL] 🟢 Birth]` DID log
for 10 of 12 launches — that log line only fires inside the same `if
mint and sig` block (line 10816) that gates the insert call, so `sig`
must have been truthy for those 10. The real mechanism is different and
was found by tracing every writer of `token_analysis.create_tx_signature`:

1. **Birth-time write (correct)**: `_insert_bonding_curve_token()`
   (`pumpfun_curve_listener.py:5732`) — an `INSERT ... ON CONFLICT DO
   UPDATE` that `COALESCE`s `create_tx_signature` against the existing
   row (line 5764: `create_tx_signature = COALESCE(token_analysis.create_tx_signature, excluded.create_tx_signature)`).
   This correctly persists the signature and can never null it out.

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
   miss, transaction shape mismatch, rate limit, etc. — any reason the
   second, independent validation could fail even though the first,
   birth-time capture succeeded).

## Why this fires for exactly this 12-launch pattern and not universally

This code path (`_update_token_entry_with_creator`) is only reached
when `earliest_creator` is falsy at the point migration processing
begins (line 9105's `if not earliest_creator:`) — i.e., for tokens
where the *migration-time* code doesn't yet see a creator already
attached to the row through the normal fast paths it checks first. Not
every migrated token takes this branch, which is why this is not a
100%-of-migrations bug, but for the subset that do take it (this
cohort's 10 confirmed + likely both of the 2 `UNKNOWN`-classified
launches, though their earlier stage cannot be independently confirmed
from retained logs), the clobber is deterministic: any migration whose
RPC re-validation doesn't cleanly reconfirm the original CREATE
transaction loses its already-correct signature.

## Root cause groups

| Root cause | Launches affected | Frequency | Evidence | Confidence |
|---|---|---|---|---|
| **Migration-time creator re-extraction overwrites a valid birth-time `create_tx_signature` with NULL when its own independent RPC re-validation doesn't succeed** (`_update_token_entry_with_creator`, line 7933/7963, called line 9157) | CmoCuZ9J2YT1QH, HHcXBLbnuSWdYi, EQZfBpWpQc5BEU, DpTtRHY6PSuxxJ, CvP9vVUCpoDuMd, 4WfoYERYFw3AQW, EDNvjVDjKVfRsq, c5Zye8yFd1AGrS, 9Mn2t7yX2TmSSM, FzNgpR11RYACas | 10 / 12 (83%) | Birth log present + `[PREMIG_BIRTH_SEED]` implied by successful birth insert, yet `create_tx_signature` NULL now; unconditional `UPDATE` with no `COALESCE` found at the exact write site; strict validation gate (line 9146-9148) confirmed to null the local variable on any non-pass | **High** — direct code-path match, no alternative writer of this column found anywhere else in the file |
| **Insufficient log retention to confirm the same mechanism** (birth event predates the oldest retained log file, `.log.3`, 2026-07-18T14:46) | B3Fq8SqBtsxsWw, 71TKvknpvwRcjd | 2 / 12 (17%) | Both launches created 2026-07-15, ~3 days before retained log history begins; `create_tx_signature` NULL and `pf_ws_creator` set is consistent with the same root cause but cannot be directly confirmed via log evidence | **Medium** — same symptom pattern as the confirmed 10, but the specific log lines that prove the mechanism (`🟢 Birth`, absence of `[PREMIG_BIRTH_SEED]`) are unavailable for direct inspection |

## Explicitly ruled out

- **WebSocket listener offline**: contradicted — `[PUMPPORTAL] 🟢
  Birth]` logged for 10/12, proving the WS connection was live and
  processing messages normally at birth time for those launches.
- **Reconciliation window expired**: not applicable — this is not a
  reconciliation-catch-up gap; the original data was captured
  correctly and then overwritten by a later, unrelated code path.
- **Queue backlog**: `wt_walkback_queue` shows `status='complete'` for
  all 12 with no elevated `attempts` counts — no backlog evidence.
- **Schema mismatch**: `token_analysis.create_tx_signature` is a
  normal TEXT column; no schema error appears anywhere in logs for
  these mints.
- **Unsupported transaction pattern**: the birth-time capture succeeded
  using the PumpPortal side-channel, which doesn't depend on parsing
  the raw transaction shape at all — this rules out a CREATE
  instruction shape the parser couldn't handle, for the 10 confirmed
  cases.
