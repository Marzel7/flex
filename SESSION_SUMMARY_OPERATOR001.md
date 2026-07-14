# Session Handoff — OPERATOR_001 First-Class Identity

**Date:** 2026-06-03
**Resume at:** the AskUserQuestion below (identity-scope reconciliation). Answer that, then apply.

---

## Where we are (one line)
OPERATOR_001 is confirmed and persistently labeled; the registry + code to make it a
first-class `operator_identity` is **written but NOT yet applied to the live DB**, and
the running app is still on **old code**. One decision remains before finishing.

---

## ⏸️ THE PENDING DECISION (resume here)

Registry-driven logic assigns `operator_identity='OPERATOR_001'` to **3 ops**
(7172, 7175, 7184 — current IDs, will renumber) where `5E1Rvu` is the dominant
funding root among **all** known hubs (WT+ALPHA+operator). But **7 ops** currently
carry `human_name='OPERATOR_001'`. Reconcile:

- **Option A — Keep registry logic as-is (3 ops).** Most principled: identity only
  where 5E1Rvu genuinely wins the dominant-root contest vs WT/ALPHA hubs. Other 4
  get their true lineage. `human_name` stays on all 7. *(my recommendation)*
- **Option B — Force all 7 to OPERATOR_001.** identity follows human_name regardless
  of root contest. Consistent dashboards, overrides lineage for 4 ops.
- **Option C — Investigate the 4 first.** Check what 7159/7160/7162/7163's dominant
  roots resolve to (WATCHTOWER? ALPHA? UNKNOWN?) with the full hub set, then decide.

**Why the gap exists:** isolation test used operator-hubs-only as `_known_hubs`, so
only the purest 3 ops resolved to 5E1Rvu. Real `_discover_operations` uses the full
hub set, which can change which root "wins" for the other 4. This is correct behavior,
not a bug — hence the decision.

---

## ✅ DONE this session

1. **OPERATOR_001 confirmed** — 4/4 review tests passed (hub purity 16/16, timing 6.2d,
   corridor `.????928` family, downstream convergence 16/16 return-to-hub 630.96 SOL +
   14/16 shared collector `5Ww9G6Xu`). See memory `operator-001-confirmed.md`.

2. **Labeled persistently** — 7 live ops: `human_name='OPERATOR_001'`, `state='CONFIRMED'`,
   evidence JSON in `discovery_signals`. Verified to survive engine rebuilds (keyed on
   `(corridor_amount, window_start)`; rebuild preserves human_name+state but recomputes
   operator_identity).

3. **Registry table created + seeded (LIVE DB):**
   `wt_known_operator_hubs(hub_wallet PK, operator_identity, confidence, evidence_json, created_at)`
   — one row: `5E1Rvu19RQPwrGC6EoxF7DFgFWjgG4UtBrtVFAqzgSMQ → OPERATOR_001`, confidence 1.0.

4. **Code edits in `src/core/main.py` (committed to file, NOT yet running):**
   - Added helper `_get_known_operator_hubs(conn)` just before `_discover_operations`
     (creates table IF NOT EXISTS, returns {hub_wallet: operator_identity}).
   - In `_discover_operations`: folded operator hubs into `_known_hubs`
     (`_operator_hubs = _get_known_operator_hubs(conn)` + union into `_known_hubs`).
   - In identity block (after WT/ALPHA checks): `else:` branch assigns
     `identity = _operator_hubs[op_root]` when the op's dominant root
     (`max(roots, key=roots.get)`) is a registered operator hub.

5. **Audit data kept:** table `oneoff_hub5e1_outbound` (50 rows, 16 creators→3 recipients).
   One-off scan script was deleted.

---

## ⚠️ STATE / GOTCHAS

- **Live app = OLD code.** PID was 71514/96745 (gunicorn on :5002) running pre-edit
  main.py. Current `operator_identity` for the 7 ops still reads `UNKNOWN`. The new
  registry logic only takes effect after the app **reloads/restarts** and a rebuild runs.
- **Op IDs renumber every rebuild** (`_discover_operations` does DELETE+reINSERT,
  AUTOINCREMENT). Never key on operation_id across time. Seen this session:
  6998s → 7020s → 7126s → 7159s → 7172s. Always re-derive by hub.
- **DB is healthy.** An earlier "database disk image is malformed" was a STALE
  `/tmp/*.db-wal` from a bad copy — not the real DB. Live reads work fine
  (33 ops, registry=1, op001=7). Do NOT run `cp` of the .db with its -wal; use
  `sqlite3 .backup` or read live in `query_only` mode.
- **DB lock contention:** the live single-writer app holds WAL locks. `PRAGMA
  integrity_check` and `src.backup()` block behind it — avoid them. Read with
  `PRAGMA query_only=1`, timeout ~15s. Importing `src.core.main` runs heavy startup
  side-effects and deadlocks on the lock — do NOT trigger rebuilds via fresh import;
  use the app's own HTTP route `?refresh=1` (around main.py:36050) or restart the app.
- **Outbound worker is DEAD** (CREATOR_OUTBOUND_ENABLED off since 2026-05-05);
  creator_c2c_edges stale. Scan directly. (memory: `outbound-worker-disabled.md`)
- **Auto-apply stays OFF:** `WT_AUTO_APPLY_IDENTITY` unset everywhere. Keep it off.

---

## ▶️ NEXT STEPS after the decision

1. Answer A/B/C above.
   - If **A**: code is already correct — just restart the app so new code loads, then
     hit the discovery refresh route and confirm 3 ops show operator_identity=OPERATOR_001.
   - If **B**: add a post-assignment step in `_discover_operations` (or the carry-forward
     block) so any op with `human_name` matching a registry operator also gets that
     `operator_identity`. Then restart + rebuild.
   - If **C**: run the read-only probe with the FULL `_known_hubs` set to see the 4 ops'
     true roots, then pick A or B.
2. **Restart the live app** (gunicorn on :5002) to load edited main.py.
3. Trigger a rebuild (HTTP `?refresh=1`, not a fresh python import) and verify
   operator_identity is set as decided and survives a second rebuild.
4. Confirm the operators dashboard (http://localhost:5002/watchtower/operators)
   shows OPERATOR_001 as a filterable identity.

## Verification probe (read-only, safe to rerun)
Apply registry logic against live DB without writing — paste into `python3`:
(connect db, PRAGMA query_only=1, build op_hubs from wt_known_operator_hubs,
resolve each op's dominant 3-hop root over operator-hub set, list matches.)
Last run output: ops 7172 (4/4), 7175 (3/3), 7184 (3/3) → OPERATOR_001.
