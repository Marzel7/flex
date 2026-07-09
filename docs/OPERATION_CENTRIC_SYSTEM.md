# Operation-Centric Intelligence System — Implementation Summary

**Status: live and continuously watching.** As of 2026-06-09 the system is running under
supervisord, the store holds 10 operations / 3,886 candidates / 4,209 activity events, and
the scheduler has logged 139 runs. This document explains how it was built, how it works,
and how to operate it.

---

## 1. Why this exists (the problem it solves)

A long arc of WATCHTOWER audits proved the old attribution model was the wrong primary
lens:

- **Wallets rotate.** Known hubs (`7UyCwmSU`, `Azdpw5yk`, `C745erBx`, `4LpEjcq3`) went
  dormant on-chain (22–39 days) while the *same playbook* kept producing creators through
  fresh wallets. The wallet signature is ephemeral.
- **Attribution lags weeks.** The legacy engine only saw operations after a batch pass
  (the "June-2 backlog" effect) — it declared WATCHTOWER "DORMANT" while 300+ candidate
  creators were being funded in real time.
- **The deepest surviving signal is the *operation*, not the wallet.** An operation is a
  treasury-rooted infrastructure island that persists across wallet rotation.

So the system was rebuilt around **operations as the primary object**, discovered from
on-chain topology, persisted forever, and monitored continuously — answering *"which
operations are alive right now?"* instead of *"which wallets belong to WATCHTOWER?"*

---

## 2. The data model (resolved through audit)

```
FAMILY            soft behavioural grouping (shared playbook/template/topology).
  │               NEVER merges operations. A label only.
  ▼
OPERATION         a treasury-rooted infrastructure island. Stable UUID. Persists
  │               across wallet rotation. Merge two roots ONLY on hard infrastructure
  │               overlap (shared collector / pass-through / terminal / upstream /
  │               direct treasury↔treasury edge). NEVER merge on template/timing alone.
  ▼
TREASURY ROOT  →  COLLECTORS  →  PASS-THROUGHS  →  CANDIDATE CREATORS  →  MIGRATED CREATORS
```

**The merge test that fixed the model:** `Cgwr5FAa` and `yUpm7rKXPs` share the `1.11`
template and the same week, but have **zero shared infrastructure** (the 7 swarm
collectors partition cleanly between them). They persist as **two separate operations,
one family** — proving merge must be on infrastructure, never resemblance.

**Storage:** isolated DB `database/wt_ops_v2.db` (never contends with the live app's WAL).
Tables:
- `wt_ops_v2` (operation_uuid, treasury_root, family_uuid, confidence, …)
- `wt_ops_v2_wallets` (role: TREASURY/COLLECTOR/PASS_THROUGH/DIRECT_FUNDER/CREATOR)
- `wt_ops_v2_edges` (real on-chain signatures per hop)
- `wt_ops_v2_creators` (mint, on-chain `migration_time`, template_base)
- `wt_ops_v2_families` + `wt_ops_v2_operation_family_links` (soft links)
- `wt_operation_candidates` / `wt_operation_activity` / `wt_operation_lifecycle` /
  `wt_operation_wallet_cursor` (forward monitor)
- `wt_ops_v2_runs` (scheduler run log)

**Persistence rule:** DISCOVER / MERGE / EXPAND. Never DELETE, never full-rebuild, stable
UUIDs. Operations are persistent intelligence objects, not query outputs.

---

## 3. How it was built (phase by phase)

| Phase | What | File |
|---|---|---|
| **1 — Backward discovery** | A migrated creator → trace on-chain (creator ← funder ← pass-through ← collector ← treasury) → persist a treasury-rooted operation. Hybrid local-first / Helius-RPC. Rediscovered the `Cgwr5FAa` treasury automatically from one creator. | `operation_discovery_poc.py` |
| **1.1 — Hardening** | Real on-chain tx signatures per edge; pagination for collector/treasury classification (beats the 100-tx window); idempotent edges. | `operation_discovery_poc.py` |
| **Merge test** | Resolved operation identity: merge on infrastructure only; `Cgwr5FAa`≠`yUpm7rKXPs`. | `operation_merge_poc.py` |
| **1.2 — Automated intake** | Auto-pull recent migrated WATCH creators → trace → persist. No hardcoded seeds. Discovered 5 new treasuries on first run. | `operation_store_v2.py` |
| **2 — Forward monitor** | Poll known operation wallets for NEW outbound children; attach creator candidates BEFORE migration; drive the lifecycle state machine; one-hop follow catches the `1.11` creator leg. Incremental via per-wallet signature cursor. | `operation_forward_monitor.py` |
| **1.3 — Scheduler** | Standalone process: INTAKE (15 min) + FORWARD_MONITOR (3 min) loops; single-instance lock; per-cycle RPC budgets; jittered backoff; degraded mode; run log. Launched via supervisord. | `operation_scheduler.py` |
| **UI** | Operation-centric dashboards (see §5). | `operation_dashboard_routes.py` + templates |

---

## 4. How it works at runtime (continuous loop)

```
                          ┌─────────────────────────────────────────────┐
  migrated WATCH creator  │  INTAKE  (every 15 min)                      │
        ───────────────►  │   trace backward → DISCOVER/MERGE/EXPAND     │
                          │   a treasury-rooted operation in wt_ops_v2   │
                          └─────────────────────────────────────────────┘
                          ┌─────────────────────────────────────────────┐
  known operation wallets │  FORWARD MONITOR  (every 3 min)              │
        ───────────────►  │   poll for NEW children → attach candidate   │
                          │   creators PRE-migration → advance lifecycle │
                          └─────────────────────────────────────────────┘
       lifecycle state machine (automatic):
       DISCOVERED → ACTIVE → PROVISIONING → CREATORS_SEEN → MIGRATED → DORMANT → REACTIVATED
```

**Key principle:** INTAKE finds operations *after* migration (post-mortem); FORWARD
MONITOR catches expansion *before* launch. Together with the scheduler, the system no
longer needs manual audits to rediscover rotated infrastructure.

**Safety/isolation, enforced everywhere:**
- Reads/writes **only** `wt_ops_v2.db`. Reads the live DB read-only for the migration
  lookup. **Zero writes** to `wt_operations`, attribution, classification, or the live
  WATCH pipeline.
- On-chain activity time = `token_analysis.migrated_at` / RPC `block_time` — **never**
  `creator_funders.first_detected_at` (a FLEX detection stamp that collapses weeks of
  activity onto the batch-processing day and falsely shows dormant wallets as "active").
- RPC: incremental cursors (never re-trace), per-cycle budgets, backoff on 429/5xx.

---

## 5. The UI (Operations OS)

The product UI reuses the sophisticated campaigns dashboard shell (custom SVG force-graph,
tempo bar, panels, event feed) but is **powered entirely by wt_ops_v2** — no legacy
hub/corridor/attribution data.

**Sidebar → ◆ Operations OS:**

| Page | Route | Purpose |
|---|---|---|
| **Live Operations** (graph) | `/ops` and `/ops/live` | The reused dashboard: SVG graph of treasury-rooted topology (TREASURY anchor → COLLECTORS → PASS-THROUGHS → CREATOR candidates → CREATOR_HC migrated), tempo bar of operation states, LIVE OPERATIONS panel, CREATOR CANDIDATES, event feed, **scheduler health widget** (green = LIVE, red = DOWN). |
| **Pipeline & States** (cards) | `/ops/cards` | At-a-glance: Creator Pipeline hero (candidates → migrated → attributed), operation-state distribution, live operation cards. |
| **Operations Table** | `/ops/operations` | Sortable list: UUID · family · treasury · state · wallets · collectors · candidates · migrated · last activity. |
| **Operation Detail** | `/ops/operation/<uuid>` | One operation: overview, infrastructure table, creator pipeline, timeline, activity feed. |

**Graph mapping** (operation topology → existing node-type styling, zero CSS changes):
treasury→`TREASURY` (gold anchor), collectors→`COORDINATOR`, pass-throughs→`RELAY`,
candidates→`CREATOR` (cyan), migrated→`CREATOR_HC` (orange).

**API:** the dashboard reads `/api/ops-v2/{graph, campaigns, creators, tempo, metrics,
ops-overview, events-feed, scheduler}` — each returns the exact JSON shape the reused JS
expects, so no render-layer was rewritten. (23 routes total in the blueprint.)

**Legacy:** the old WATCHTOWER attribution pages (`/watchtower/operators` Discovery,
attribution) remain under the **WATCHTOWER Legacy** sidebar section as historical
reference — untouched, still on v1 data.

---

## 6. Current live state (2026-06-09)

- **Scheduler:** RUNNING under supervisord (`operation_scheduler`), 139 runs logged.
- **Store:** 10 operations · 1 family (WATCHTOWER-LIKE) · 141 wallets · 28 migrated
  creators · 3,886 candidates · 4,209 activity events.
- **Lifecycle:** 8 MIGRATED, 2 DORMANT (snapshot — fluctuates as the engine runs).

---

## 7. How to operate it

```bash
# Scheduler control (supervisord uses a unix socket; pass the config):
SUP="/Users/kevinkeaveney/anaconda3/envs/algotrader/bin/supervisorctl -c config/supervisor/supervisord.conf"
$SUP status operation_scheduler
$SUP start  operation_scheduler      # autostart=false — start manually after a full bounce
$SUP stop   operation_scheduler

# Scheduler status / run log (authoritative cadence record; stdout is buffered):
python -m src.core.operation_scheduler --status
sqlite3 database/wt_ops_v2.db \
  "SELECT job_type, datetime(started_at,'unixepoch','localtime'), status, rpc_used \
   FROM wt_ops_v2_runs ORDER BY started_at DESC LIMIT 10;"

# Manual one-off runs (don't need the daemon):
python -m src.core.operation_scheduler --once-intake
python -m src.core.operation_scheduler --once-forward
python -m src.core.operation_store_v2 --intake-watch --days 7 --limit 20
```

**Cadence config (supervisord env):** `OPS_INTAKE_INTERVAL_SEC=900`,
`OPS_FORWARD_INTERVAL_SEC=180`, `OPS_INTAKE_MAX_RPC=120`, `OPS_FORWARD_MAX_RPC=180`.

**Important:** the scheduler is `autostart=false`, so after any full supervisord/stack
restart it must be started manually (it doesn't crash — it exits cleanly and the lock
releases; it just won't auto-relaunch). Flip to `autostart=true` if you want it to always
come back with the stack.

---

## 8. Known limitations / deferred work

- **Secondary dashboard panels** (ACTIVE TREASURY CORRIDORS, DORMANT OPERATIONS,
  op-launches, sweeps overlay) currently render empty — pointed at a clean `_empty`
  endpoint to avoid legacy bleed; v2 wiring is the next pass. The graph, LIVE OPERATIONS,
  CREATOR CANDIDATES, tempo bar, event feed, and scheduler widget carry live v2 data.
- **Graph treasury→collector spokes:** treasury→collector edges aren't in
  `wt_ops_v2_edges` (chain edges are forwarder-to-forwarder), so creators link to their
  funders rather than drawing explicit treasury-center spokes. Topology still reads.
- **Candidate status** (PENDING→FUNDED→CREATOR→MIGRATED) is displayed but not yet
  auto-advanced — a separate engine task.
- **Attribution stage** in the pipeline shows 0 — attribution is a downstream enrichment,
  deliberately not wired into the v2 store (the store stays attribution-free by design).
- **Families page + Intelligence Map** deferred until a day of real data accrues (family
  scoring/naming may change once more operations land).

---

## 9. File map

| File | Role |
|---|---|
| `src/core/operation_discovery_poc.py` | Backward trace engine (hybrid local/RPC, real signatures, pagination) |
| `src/core/operation_merge_poc.py` | Treasury-merge evidence test (infrastructure-only) |
| `src/core/operation_store_v2.py` | Persistent store + automated intake runner + CLI |
| `src/core/operation_forward_monitor.py` | Forward expansion monitor + lifecycle state machine |
| `src/core/operation_scheduler.py` | Standalone scheduler (two cadence loops, lock, budgets, run log) |
| `src/core/operation_dashboard_routes.py` | Flask blueprint — all `/ops/*` pages + `/api/ops-v2/*` endpoints |
| `templates/watchtower_dashboard.html` | Reused dashboard shell, repointed to v2 (Operations OS) |
| `templates/ops_live.html` | Pipeline & States card view (`/ops/cards`) |
| `templates/ops_operations.html` | Operations table |
| `templates/ops_detail.html` | Operation detail page |
| `database/wt_ops_v2.db` | Isolated operation store |
| `config/supervisor/supervisord.conf` | `[program:operation_scheduler]` entry |

---

*The milestone: the operation-centric system is the primary intelligence product, running
continuously, discovering rotated infrastructure both after migration (intake) and before
launch (forward monitor), with a live graph UI — and it no longer requires manual audits.*
