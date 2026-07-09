# Single-Writer SQLite Architecture
## WATCHTOWER Intelligence Platform — DB Concurrency Redesign

---

## Root Cause

The app and external scan scripts both open `sqlite3.connect()` directly. SQLite serialises all writers at the OS file-lock level. `WAL mode does not fix this` — it only allows concurrent readers alongside one writer. Two simultaneous writers will always produce `database is locked`.

The existing `DB_WRITE_LOCK` threading.RLock in `db_locking.py` is only respected by threads **inside** the Flask process. External scripts (`watchtower_operator_scanner.py`, any subprocess) have no knowledge of it.

### ⚠️ Critical: `pumpfun_curve_listener.py` is a third concurrent writer

The curve listener is a **separate long-running process** (`python -m src.core.pumpfun_curve_listener`). It writes `lifecycle_stage`, `migrated_at`, and migration detection data directly to `token_analysis` with no coordination with the Flask app. This is the most important writer to handle correctly — migration data is time-sensitive and must not be delayed.

**Migration data must never be delayed.** The single-writer architecture must ensure:
- Migration writes from the curve listener have the **highest priority** in the write queue
- The flush interval for migration domain is **50ms**, not 500ms
- OR: the curve listener POSTs to a dedicated `POST /api/internal/migration-event` endpoint with a synchronous flush guarantee

The curve listener's write path (`lifecycle_stage = 'migrated'`, `migrated_at`) feeds directly into:
- The WATCHTOWER detection pipeline (operators discovered via migrated creators)
- The May creator scan (relies on accurate `migrated_at` timestamps)
- All dashboard token lists ordered by `migrated_at DESC`

**Any added latency here cascades into missed operator detections.**

**Core invariant to enforce:**
> Exactly one OS-level process ever issues `BEGIN IMMEDIATE` on `flex_complete_database.db`

---

## Target Architecture

```
┌─────────────────────────────────────────────┐
│              FLASK APP                       │
│                                             │
│  Webhook Handler  ──┐                       │
│  Activation Poller ─┤──► enqueue_write()   │
│  Second-Hop Worker ─┤         │            │
│  Enrichment Pipes  ─┘         ▼            │
│                        threading.Queue     │
│                               │            │
│                               ▼            │
│                    ┌─ DB WRITER THREAD ─┐  │
│                    │  (single writer)   │  │
│                    │  executemany batch │  │
│                    │  BEGIN IMMEDIATE   │  │
│                    │  commit every 500ms│  │
│                    └────────────────────┘  │
│                               │            │
│                               ▼            │
│                    flex_complete_database.db│
└─────────────────────────────────────────────┘

         ▲                          ▲
         │ POST /api/internal/*     │ POST /api/internal/*
         │                          │
┌────────────────┐      ┌────────────────────┐
│ Operator       │      │ Second-Hop Worker  │
│ Scanner        │      │ (standalone mode)  │
│ (external)     │      └────────────────────┘
│                │
│ RPC calls only │
│ no sqlite3     │
└────────────────┘
```

---

## New Files

| File | Role |
|---|---|
| `src/core/db_write_queue.py` | In-memory queues, dedup cache, `enqueue_write()`, `drain_batch()` |
| `src/core/db_writer.py` | Single writer thread, `executemany` batching, retry, dead-letter |
| `src/core/internal_api.py` | Flask Blueprint — all `/api/internal/*` ingestion routes |

---

## 1. Write Queue (`src/core/db_write_queue.py`)

One queue per domain. Priority drain order: webhook → poller → scan → enrichment.

```python
import threading, queue, time, logging
from dataclasses import dataclass, field

@dataclass
class WriteItem:
    domain: str           # "webhook" | "scan" | "poller" | "enrichment"
    label: str
    statements: list      # list of (sql, params)
    idempotency_key: str  # "" = no dedup
    enqueued_at: float = field(default_factory=time.monotonic)

_queues = {
    "migration":  queue.Queue(maxsize=500),   # curve listener — 50ms flush, never delayed
    "webhook":    queue.Queue(maxsize=2000),
    "poller":     queue.Queue(maxsize=500),
    "scan":       queue.Queue(maxsize=1000),
    "enrichment": queue.Queue(maxsize=500),
}
_DRAIN_ORDER = ["migration", "webhook", "poller", "scan", "enrichment"]

# Per-domain flush intervals (ms) — migration must never be delayed
_FLUSH_INTERVALS = {
    "migration":  50,
    "webhook":    500,
    "poller":     500,
    "scan":       500,
    "enrichment": 2000,
}

# Dedup cache — 60s TTL
_dedup_cache: dict[str, float] = {}
_dedup_lock = threading.Lock()
_DEDUP_TTL = 60.0

def _is_duplicate(key: str) -> bool:
    if not key:
        return False
    now = time.monotonic()
    with _dedup_lock:
        stale = [k for k, t in _dedup_cache.items() if now - t > _DEDUP_TTL]
        for k in stale: del _dedup_cache[k]
        if key in _dedup_cache: return True
        _dedup_cache[key] = now
        return False

def enqueue_write(item: WriteItem) -> bool:
    if _is_duplicate(item.idempotency_key):
        return False
    q = _queues.get(item.domain, _queues["enrichment"])
    try:
        q.put_nowait(item)
        return True
    except queue.Full:
        logging.warning(f"[WRITE_QUEUE] {item.domain} FULL — dropping {item.label}")
        return False

def drain_batch(max_items: int = 200) -> list:
    batch = []
    for domain in _DRAIN_ORDER:
        q = _queues[domain]
        while len(batch) < max_items:
            try: batch.append(q.get_nowait())
            except queue.Empty: break
    return batch

def queue_depths() -> dict:
    return {d: q.qsize() for d, q in _queues.items()}
```

**Overflow behaviour:**
- Webhook queue > 500 → WARNING
- Webhook queue > 1500 → CRITICAL + HTTP 429 to Helius (Helius will retry)
- Scan queue > 800 → worker slows POST rate

### Priority-triggered flush scheduling

A single `time.sleep(interval)` loop defeats per-domain latency requirements. A 50ms migration domain is meaningless if the writer is asleep for 500ms waiting for the next global tick.

The writer must wake immediately when a high-priority item arrives:

```python
import threading

_migration_signal = threading.Event()  # set when migration queue receives an item

def enqueue_write(item: WriteItem) -> bool:
    if _is_duplicate(item.idempotency_key):
        return False
    q = _queues.get(item.domain, _queues["enrichment"])
    try:
        q.put_nowait(item)
        # Signal writer to wake immediately for high-priority domains
        if item.domain in ("migration",):
            _migration_signal.set()
        return True
    except queue.Full:
        logging.warning(f"[WRITE_QUEUE] {item.domain} FULL — dropping {item.label}")
        return False

def _writer_loop(db_path: str):
    """
    Priority-triggered flush:
    - Migration signal wakes writer immediately
    - Otherwise waits up to 500ms (webhook/poller cadence)
    - Enrichment items are flushed whenever other domains also flush
    """
    while True:
        # Wait for migration signal OR 500ms timeout
        triggered = _migration_signal.wait(timeout=0.5)
        _migration_signal.clear()

        items = drain_batch(_MAX_BATCH)
        if items:
            _commit_batch(db_path, items)
```

This gives migration events sub-10ms write latency (network round-trip from curve listener → POST → enqueue → signal → flush) while enrichment and scan items are batched lazily at the 500ms cadence. The writer never burns CPU polling — `Event.wait()` is a blocking OS primitive.

---

## 2. DB Writer Thread (`src/core/db_writer.py`)

Single daemon thread. The only caller of `BEGIN IMMEDIATE`. Flushes every 500 ms or when batch reaches 300 items.

```python
import sqlite3, threading, time, logging, os
from collections import defaultdict
from src.core.db_write_queue import drain_batch, WriteItem
from src.utils.db_locking import db_connect

_FLUSH_MS    = int(os.getenv("DB_WRITER_FLUSH_MS", "500"))
_MAX_BATCH   = int(os.getenv("DB_WRITER_MAX_BATCH", "300"))
_MAX_RETRIES = 5
_BASE_DELAY  = 0.25

_stats = {"batches_committed":0,"items_written":0,"lock_errors":0,
          "dead_letters":0,"last_flush_at":0.0,"avg_flush_ms":0.0}
_stats_lock = threading.Lock()

def _commit_batch(db_path: str, items: list) -> tuple:
    if not items: return 0, 0
    all_stmts = []
    for item in items:
        all_stmts.extend(item.statements)

    for attempt in range(_MAX_RETRIES):
        conn = None
        try:
            t0 = time.monotonic()
            conn = db_connect(db_path, timeout=30)
            conn.execute("BEGIN IMMEDIATE")

            # Group identical SQL for executemany
            groups = defaultdict(list)
            for sql, params in all_stmts:
                groups[sql].append(params)

            for sql, param_list in groups.items():
                if len(param_list) == 1:
                    conn.execute(sql, param_list[0])
                else:
                    conn.executemany(sql, param_list)

            conn.commit()

            # Checkpoint if large batch
            if len(all_stmts) > 1000:
                try: conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                except: pass

            elapsed_ms = (time.monotonic() - t0) * 1000
            with _stats_lock:
                _stats["batches_committed"] += 1
                _stats["items_written"] += len(all_stmts)
                _stats["last_flush_at"] = time.time()
                _stats["avg_flush_ms"] = round(0.9*_stats["avg_flush_ms"] + 0.1*elapsed_ms, 1)
            return len(all_stmts), 0

        except sqlite3.OperationalError as e:
            if conn:
                try: conn.rollback(); conn.close()
                except: pass
            with _stats_lock: _stats["lock_errors"] += 1
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_BASE_DELAY * (2 ** attempt))
        except Exception as e:
            if conn:
                try: conn.rollback(); conn.close()
                except: pass
            logging.error(f"[DB_WRITER] fatal error: {e}", exc_info=True)
            for item in items:
                logging.error(f"[DEAD_LETTER] {item.domain}:{item.label}")
            with _stats_lock: _stats["dead_letters"] += len(items)
            return 0, len(items)
        finally:
            if conn:
                try: conn.close()
                except: pass

    with _stats_lock: _stats["dead_letters"] += len(items)
    return 0, len(items)

def _writer_loop(db_path: str):
    interval = _FLUSH_MS / 1000.0
    logging.info(f"[DB_WRITER] started flush={_FLUSH_MS}ms batch={_MAX_BATCH}")
    while True:
        time.sleep(interval)
        try:
            items = drain_batch(_MAX_BATCH)
            if items:
                _commit_batch(db_path, items)
        except Exception as e:
            logging.error(f"[DB_WRITER] loop error: {e}", exc_info=True)

_writer_thread = None

def start_db_writer(db_path: str):
    global _writer_thread
    if _writer_thread and _writer_thread.is_alive(): return
    _writer_thread = threading.Thread(
        target=_writer_loop, args=(db_path,),
        daemon=True, name="db-single-writer"
    )
    _writer_thread.start()

    # Watchdog — restarts writer if it dies
    def watchdog():
        global _writer_thread
        while True:
            time.sleep(30)
            if not _writer_thread or not _writer_thread.is_alive():
                logging.critical("[DB_WRITER] writer died — restarting")
                start_db_writer(db_path)
    threading.Thread(target=watchdog, daemon=True, name="db-writer-watchdog").start()

def get_writer_stats() -> dict:
    with _stats_lock: return dict(_stats)
```

**Key design:**
- `BEGIN IMMEDIATE` — acquires write lock at transaction start, never escalates, never deadlocks with itself
- `executemany` grouping — identical SQL statements batched into single prepared-statement calls (~8x faster for sweep bursts)
- Connection opened and closed per flush — file is unlocked between flushes, readers checkpoint freely

---

## 3. Internal Ingestion APIs (`src/core/internal_api.py`)

Flask Blueprint. Auth via `X-Internal-Token` header. Routes enqueue writes and return immediately.

### Endpoints

```
POST /api/internal/migration-event    — ⚡ HIGHEST PRIORITY: lifecycle_stage + migrated_at from curve listener
POST /api/internal/operator-graph     — scan results (edges + candidates)
POST /api/internal/launch-candidate   — single candidate discovery
POST /api/internal/operator-state     — lifecycle state transition
POST /api/internal/raydium-launch     — Raydium pool detected
POST /api/internal/second-hop-links   — second-hop funder links
POST /api/internal/fee-payer-event    — webhook fee payment (internal forward)

GET  /api/internal/operators-to-scan  — list of unscanned operators (read-only)
GET  /api/internal/queue-health       — queue depths + writer stats
```

### Payload schemas

**`POST /api/internal/operator-graph`**
```json
{
  "operator_address": "AbC...xyz",
  "scan_timestamp": 1716134400,
  "hop": 1,
  "edges": [
    {
      "child_address": "DeF...uvw",
      "relationship": "launch_wallet",
      "amount_sol": 0.05,
      "first_seen_at": 1716134300,
      "tx_signature": "2AbC..."
    }
  ],
  "launch_candidates": [
    {
      "address": "GhI...rst",
      "source_operator": "AbC...xyz",
      "candidate_reason": "post_fee_fanout",
      "confidence": "high",
      "evidence": {"funded_by": "AbC...xyz", "amount_sol": 0.05, "hop": 1}
    }
  ]
}
```

**`POST /api/internal/operator-state`**
```json
{
  "address": "AbC...xyz",
  "state": "operational",
  "state_changed_at": 1716134400,
  "evidence": {"trigger": "launch_detected", "mint": "TokenMint..."}
}
```

**`POST /api/internal/raydium-launch`**
```json
{
  "pool_address": "RayPool...",
  "mint": "TokenMint...",
  "creator_address": "AbC...xyz",
  "pool_program": "Raydium_CPMM",
  "initial_liquidity_sol": 5.5,
  "block_time": 1716134350,
  "tx_signature": "5AbC...",
  "operator_link": "AbC...xyz",
  "link_type": "fee_payer",
  "evidence": {}
}
```

### Response

All ingestion endpoints return `{"accepted": true, "queued": N}` immediately. Never block on DB writes.

---

## 4. Worker Redesign

### Principle: Workers are stateless RPC probes

Workers have **zero** `sqlite3` imports. They perform RPC work, build results in memory, POST to the app.

### `watchtower_operator_scanner.py` — new flow

```python
def scan_operator_downstream_stateless(address, rpc_url, hop=1, _visited=None):
    """Pure RPC probe — returns structured result, no DB."""
    # ... all existing RPC logic unchanged ...
    # Returns: {"operator_address":..., "edges":[...], "launch_candidates":[...]}

def post_to_app(result, app_url, secret):
    requests.post(
        f"{app_url}/api/internal/operator-graph",
        json=result,
        headers={"X-Internal-Token": secret},
        timeout=10,
    )
```

### Failure handling — local spill

If the app is unreachable, workers spill to `/tmp/flex_worker_spill/*.json`. On next startup, workers replay spilled payloads before beginning new scans.

```python
def post_with_retry(url, payload, secret, label):
    for attempt in range(5):
        try:
            resp = requests.post(url, json=payload,
                headers={"X-Internal-Token": secret}, timeout=10)
            if resp.status_code == 200: return True
            if 400 <= resp.status_code < 500:
                _spill(label, payload); return False
        except requests.ConnectionError:
            pass
        time.sleep(2 ** attempt)
    _spill(label, payload)
    return False
```

---

## 5. Deduplication

Two-layer strategy:

**Layer 1 — Queue-level (60s TTL):**
Idempotency keys structured as `{domain}:{primary_key}:{timestamp//60}`. Duplicate POSTs within the same minute produce one queue item.

**Layer 2 — SQL-level (durable):**
All writes use `INSERT ... ON CONFLICT DO UPDATE`. Unique constraints per table:

| Table | Conflict key |
|---|---|
| `watchtower_fee_payers` | `address` |
| `watchtower_operator_graph` | `(operator_address, child_address, relationship)` |
| `watchtower_launch_candidates` | `address` |
| `watchtower_raydium_launches` | `pool_address` |

Confidence escalation (monotonically increasing — never regresses):
```sql
confidence = CASE WHEN excluded.confidence='high' THEN 'high'
                  WHEN confidence='high'          THEN 'high'
                  ELSE excluded.confidence END
```

---

## 6. Operator Lifecycle State Model

```
provisioned → activated → operational → launching → extracting → dormant
                                    ↑___________________________|
```

| State | Trigger |
|---|---|
| `provisioned` | First fee payment received by webhook |
| `activated` | Creator appears in `token_analysis` |
| `operational` | At least one migrated token |
| `launching` | Raydium launch or `launch_wallet` child active |
| `extracting` | Outbound to COLLECTOR/AGGREGATOR detected |
| `dormant` | No fee or launch in 30 days |

All state transitions are written via `enqueue_write()` — never direct DB calls.

---

## 7. Migration Plan

### Phase 1 — Infrastructure (Week 1)
- Add `db_write_queue.py` + `db_writer.py`
- Start writer thread in `start_background_workers()`
- Add `/api/internal/queue-health` and `/api/db-health` writer stats
- **No existing writers changed yet** — writer idles, proves infrastructure

### Phase 2 — Webhook Handler (Week 1–2)
- Replace `webhook_watchtower()` DB writes with `enqueue_write()`
- Route returns in microseconds
- Highest-frequency writer eliminated from direct-write path

### Phase 3 — Activation Poller (Week 2)
- Refactor poller writes to `enqueue_write(domain="poller")`
- Poller read queries remain as direct connections (readers always fine)

### Phase 4 — External Workers (Week 3)
- `watchtower_operator_scanner.py` → stateless mode + POST to `/api/internal/operator-graph`
- `second_hop_lite_worker.py` → enqueue writes when in-process; POST when standalone
- Add `GET /api/internal/operators-to-scan` read-only route

### Phase 5 — Audit & Lock-down (Week 4)
```bash
grep -rn "sqlite3.connect\|db_connect" src/ | grep -v "# READER-OK"
```
Any remaining write callers must be converted or annotated. Add CI lint rule blocking new direct write calls in `src/analysis/` or `src/scripts/`.

---

## 8. Performance at 500 tx/min

**Throughput math:**
- 500 tx/min = 8.3 tx/sec
- 500 ms flush interval → ~4 webhook batches per flush → ~80 statements
- SQLite WAL handles 10,000+ simple statements/sec → comfortable headroom

**Operator sweep burst (252 operators × ~15 statements each = ~3,780 statements):**
- Worker POSTs in batches of 20 operators with pause between
- Writer commits in ~4 flush cycles (~2 seconds total)
- Individual commits ~10 ms each — invisible to dashboard readers

**Indexes to confirm exist:**
```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_wt_fee_payers_pk    ON watchtower_fee_payers(address);
CREATE UNIQUE INDEX IF NOT EXISTS idx_wt_op_graph_pk      ON watchtower_operator_graph(operator_address, child_address, relationship);
CREATE INDEX IF NOT EXISTS idx_wt_wallet_state_updated    ON watchtower_wallet_state(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_wt_candidates_last_signal  ON watchtower_launch_candidates(last_signal_at DESC);
```

---

## 9. SQLite Settings to Keep

```sql
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=30000;
PRAGMA wal_autocheckpoint=4000;
PRAGMA cache_size=-32000;
```

WAL + NORMAL gives ~3x write throughput vs DELETE + FULL. `busy_timeout=30000` means readers wait 30s before giving up — important during writer commits. Architecture eliminates the need to rely on this timeout, but it's a safety net.

---

## 10. DB as Operational State Infrastructure

> Originally: DB was persistence. Now: DB is operational state infrastructure. That is a completely different class of system.

The system is no longer storing historical records for reporting. It is now the **runtime state engine** for:

- **Realtime detection** — WATCHTOWER fee payments trigger immediate operator provisioning
- **Operational timelines** — every state transition is a live signal, not an audit trail
- **Lifecycle orchestration** — `provisioned → activated → launching` drives active monitoring decisions
- **Orchestration inference** — graph edges and candidate classifications inform what the scanner does next
- **Predictive monitoring** — launch wallet candidates are watched, not just recorded

This changes the correctness requirements entirely. A 500ms write delay on migration data is not a performance concern — it is a **detection miss**. An out-of-order event write is not a data quality issue — it breaks **timeline reconstruction**.

The architecture must be designed with these operational guarantees in mind, not retrofitted to them.

---

## 11. Event Sourcing — `watchtower_events`

> You are very close to an event-sourced system already. The biggest missing piece is a canonical append-only event log.

### Why this matters

Right now the state model is **derived state only** — `watchtower_wallet_state`, `watchtower_operator_graph`, and `watchtower_launch_candidates` are all computed projections. If the heuristics change, or a false positive is discovered, or a new relationship type is added, there is no way to replay history and rebuild state from first principles.

Investigations evolve. You will want to:
- Replay logic with updated detection rules
- Rebuild graphs after schema changes
- Re-score relationships with new confidence weights
- Test new heuristics against historical data
- Reconstruct operator timelines for reporting
- Debug false positives by tracing the exact sequence of evidence

An append-only event log makes all of this trivial.

### Schema

```sql
CREATE TABLE watchtower_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    event_sequence   INTEGER NOT NULL,   -- monotonic ingest counter, assigned by writer thread only
    event_type       TEXT    NOT NULL,
    wallet_address   TEXT,
    related_wallet   TEXT,
    token_mint       TEXT,
    payload_json     TEXT,
    source           TEXT,    -- "webhook" | "rpc_scan" | "poller" | "manual"
    created_at       INTEGER  NOT NULL   -- wall clock at time of enqueue (not write)
);

CREATE UNIQUE INDEX idx_wt_events_sequence ON watchtower_events(event_sequence);
CREATE INDEX idx_wt_events_wallet          ON watchtower_events(wallet_address, event_sequence ASC);
CREATE INDEX idx_wt_events_type            ON watchtower_events(event_type, event_sequence ASC);
CREATE INDEX idx_wt_events_created         ON watchtower_events(created_at DESC);
```

### Event ordering — why `event_sequence` matters

Wall clock timestamps (`created_at`) are imperfect. Under burst load — a sweep of 252 operators posting simultaneously — multiple events will share the same unix timestamp. Worse, a migration event enqueued 200ms before a fee payment event may be written to the DB after it if the writer flushes them in the wrong batch order.

`event_sequence` is a **monotonic ingest counter assigned exclusively by the writer thread** at commit time. It is never set by the caller, never derived from a clock, and never duplicated. Because there is only one writer thread, sequence numbers are strictly ordered by actual write order.

```python
# In db_writer.py — the ONLY place event_sequence is assigned
_event_sequence_counter = 0  # module-level, only touched by writer thread

def _assign_sequence(statements: list) -> list:
    """
    Replace the sentinel value -1 in event INSERT statements with
    the next monotonic sequence number.
    Called inside _commit_batch, never outside.
    """
    global _event_sequence_counter
    result = []
    for sql, params in statements:
        if "watchtower_events" in sql and params and params[0] == -1:
            _event_sequence_counter += 1
            params = (_event_sequence_counter,) + params[1:]
        result.append((sql, params))
    return result
```

Callers use `-1` as the sentinel:

```python
def emit_event(event_type, wallet_address=None, related_wallet=None,
               token_mint=None, payload=None, source="rpc_scan"):
    enqueue_write(WriteItem(
        domain="poller",
        label=f"event:{event_type}",
        statements=[(
            """INSERT INTO watchtower_events
               (event_sequence, event_type, wallet_address, related_wallet,
                token_mint, payload_json, source, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (-1, event_type, wallet_address, related_wallet,   # -1 = writer assigns sequence
             token_mint, json.dumps(payload or {}), source, int(time.time()))
        )],
        idempotency_key="",
    ))
```

### Why this matters for investigations

```sql
-- Deterministic operator timeline — sequence order, not clock order
SELECT event_sequence, event_type, related_wallet,
       datetime(created_at,'unixepoch') as wall_time
FROM watchtower_events
WHERE wallet_address = 'AbC...xyz'
ORDER BY event_sequence ASC;

-- Did fee_payment always precede state_transition for all operators?
SELECT e1.wallet_address,
       e1.event_sequence as fee_seq,
       e2.event_sequence as state_seq,
       e1.event_sequence < e2.event_sequence as correctly_ordered
FROM watchtower_events e1
JOIN watchtower_events e2 ON e2.wallet_address = e1.wallet_address
WHERE e1.event_type = 'fee_payment'
  AND e2.event_type = 'state_transition';

-- Reconstruct exact ingest order across ALL operators during a burst window
SELECT event_sequence, event_type, wallet_address, created_at
FROM watchtower_events
WHERE event_sequence BETWEEN 10000 AND 10500
ORDER BY event_sequence ASC;
```

`event_sequence` makes forensic reconstruction **deterministic**. Two analysts querying the same sequence range will always see the same event order, regardless of when they run the query or what the system clock said at ingest time.

### Event catalogue

| `event_type` | Trigger | Key fields |
|---|---|---|
| `fee_payment` | Webhook receives fee | `wallet_address`=payer, `related_wallet`=WATCHTOWER addr, `payload_json`={amount_sol, sig} |
| `operator_provisioned` | New address added to `watchtower_fee_payers` | `wallet_address`=operator |
| `signaller_activated` | SIGNALLER dust received | `wallet_address`=operator, `related_wallet`=SIGNALLER |
| `treasury_funded` | TREASURY sends SOL to operator | `wallet_address`=operator, `related_wallet`=TREASURY |
| `launch_candidate_detected` | Child wallet classified as `launch_wallet` | `wallet_address`=candidate, `related_wallet`=operator |
| `raydium_launch` | Raydium pool created linked to operator | `wallet_address`=creator, `token_mint`=mint, `related_wallet`=pool |
| `profit_routed` | Outbound to PROFIT-RELAY/COLLECTOR | `wallet_address`=operator, `related_wallet`=relay |
| `state_transition` | Lifecycle state change | `wallet_address`=operator, `payload_json`={from, to, evidence} |
| `graph_edge_discovered` | New operator→child edge | `wallet_address`=operator, `related_wallet`=child, `payload_json`={relationship, amount_sol, hop} |
| `operator_dormant` | 30 days no activity | `wallet_address`=operator |

### Integration with the writer

Events are just another `WriteItem`. Every state transition, graph edge, and candidate discovery that goes through `enqueue_write()` should also emit a corresponding event:

```python
def emit_event(event_type: str, wallet_address: str = None,
               related_wallet: str = None, token_mint: str = None,
               payload: dict = None, source: str = "rpc_scan") -> None:
    enqueue_write(WriteItem(
        domain="poller",
        label=f"event:{event_type}:{(wallet_address or '')[:20]}",
        statements=[(
            """INSERT INTO watchtower_events
               (event_type, wallet_address, related_wallet, token_mint,
                payload_json, source, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (event_type, wallet_address, related_wallet, token_mint,
             json.dumps(payload or {}), source, int(time.time()))
        )],
        idempotency_key="",  # events are always appended, never deduped
    ))
```

Events use `idempotency_key=""` — they are intentionally never deduplicated. Two identical fee payments are two real events.

### Future: replay and projection rebuilding

Because all events flow through `enqueue_write()` in timestamp order, the full operator timeline can be reconstructed at any time:

```sql
-- Reconstruct operator AbC...xyz's full history
SELECT event_type, related_wallet, payload_json, datetime(created_at,'unixepoch')
FROM watchtower_events
WHERE wallet_address = 'AbC...xyz'
ORDER BY created_at ASC;

-- Find all operators that received SIGNALLER dust before launching
SELECT DISTINCT e1.wallet_address
FROM watchtower_events e1
JOIN watchtower_events e2 ON e2.wallet_address = e1.wallet_address
WHERE e1.event_type = 'signaller_activated'
  AND e2.event_type = 'raydium_launch'
  AND e1.created_at < e2.created_at;
```

### Future: materialized graph cache

As the operator count grows past ~1,000, traversing `watchtower_operator_graph` for each dashboard load will become slow. The event log makes building a materialized cache straightforward:

```sql
CREATE TABLE watchtower_graph_cache (
    root_operator   TEXT NOT NULL,
    descendant      TEXT NOT NULL,
    depth           INTEGER NOT NULL,
    relationship    TEXT,
    total_sol       REAL,
    last_updated_at INTEGER,
    PRIMARY KEY (root_operator, descendant)
);
```

The cache is rebuilt by replaying `graph_edge_discovered` events. It is invalidated and rebuilt on demand when new edges arrive — not on every query. This is the correct pattern for second-hop discovery, May-gap reconstruction, and future creator prediction.

---

## 12. Writer Enforcement — Hard Rules

> Do NOT let Flask request threads, webhook handlers, or pollers accidentally bypass `enqueue_write()`. ONLY `db_writer.py` may call `BEGIN IMMEDIATE`.

### Rule

```
db_writer.py is the only file permitted to call BEGIN IMMEDIATE.
All other code is read-only or routes writes through enqueue_write().
```

### Layer 1 — Runtime assertion (add to `db_locking.py`)

Patch `sqlite3.connect` at startup to detect rogue write connections:

```python
import sqlite3, threading, logging, os, traceback

_WRITER_THREAD_NAME = "db-single-writer"
_ENFORCE = os.getenv("DB_WRITE_ENFORCEMENT", "warn")  # "warn" | "raise"

_original_connect = sqlite3.connect

def _patched_connect(*args, **kwargs):
    conn = _original_connect(*args, **kwargs)
    current = threading.current_thread().name
    if current != _WRITER_THREAD_NAME:
        # Allow read-only connections; block write-intent connections
        _original_execute = conn.execute
        def _guarded_execute(sql, *a, **kw):
            sql_upper = sql.strip().upper()
            if sql_upper.startswith(("INSERT", "UPDATE", "DELETE", "BEGIN IMMEDIATE", "BEGIN EXCLUSIVE")):
                msg = (f"[DB_ENFORCEMENT] Rogue write detected from thread '{current}'\n"
                       f"SQL: {sql[:120]}\n"
                       f"{''.join(traceback.format_stack()[-6:-1])}")
                if _ENFORCE == "raise":
                    raise RuntimeError(msg)
                else:
                    logging.critical(msg)
            return _original_execute(sql, *a, **kw)
        conn.execute = _guarded_execute
    return conn

sqlite3.connect = _patched_connect
```

Set `DB_WRITE_ENFORCEMENT=raise` in staging to catch violations hard. Use `warn` in production until confidence is high.

### Layer 2 — CI lint rule

Add to pre-commit or CI pipeline:

```bash
#!/bin/bash
# check_no_direct_writes.sh
VIOLATIONS=$(grep -rn \
  "\.execute\s*(\"INSERT\|\.execute\s*(\"UPDATE\|\.execute\s*(\"DELETE\|BEGIN IMMEDIATE" \
  src/ \
  --include="*.py" \
  | grep -v "db_writer.py" \
  | grep -v "# WRITER-OK" \
  | grep -v "# READER-OK")

if [ -n "$VIOLATIONS" ]; then
  echo "ERROR: Direct write SQL detected outside db_writer.py:"
  echo "$VIOLATIONS"
  exit 1
fi
```

Any legitimate exception (e.g. a one-time migration script) gets annotated `# WRITER-OK` with a comment explaining why.

### Layer 3 — Connection tagging

Tag all read-only connections so violations are immediately identifiable in logs:

```python
# In db_locking.py, db_connect() for read-only callers:
conn = sqlite3.connect(db_path, timeout=timeout)
conn.execute("PRAGMA query_only = ON")  # SQLite 3.8+ — prevents any write
```

`PRAGMA query_only = ON` causes SQLite to return an error if any write statement is attempted on that connection. Zero runtime cost, enforced at the SQLite level.

---

## 13. Real-Time Monitoring — What This Unlocks

Once the single-writer architecture is in place:

```
Workers scan aggressively → app stays responsive → queue absorbs bursts → writes stay ordered
```

This enables:

| Capability | How |
|---|---|
| **Live WATCHTOWER activation** | Webhook → event emitted → state transition queued → dashboard updated within 500ms |
| **Live Raydium launch detection** | Scan worker detects pool → POSTs to `/api/internal/raydium-launch` → event logged → alert fired |
| **Live operator lifecycle updates** | Poller detects state change → `emit_event("state_transition")` → UI refreshes |
| **Investigation replay** | Query `watchtower_events` for any operator — full timeline in chronological order |
| **Heuristic backtesting** | Run new detection logic against historical events without touching live data |
| **Creator prediction** | Query `launch_candidate` events for wallets that subsequently emitted `fee_payment` — train on confirmed patterns |

The event log is the foundation. Every other capability is a query on top of it.

---

## Summary

The fix is architectural, not a SQLite tuning problem. WAL, busy_timeout, and connection pooling all help at the margins — but as long as two OS processes open write connections simultaneously, locks are inevitable.

**Single writer thread + internal API ingestion = zero concurrent writers = zero lock errors.**

**Append-only event log = replay capability + investigation timeline + heuristic evolution.**

**`event_sequence` monotonic counter = deterministic forensic reconstruction regardless of clock drift or burst load.**

**Priority-triggered flush (`threading.Event`) = migration writes land in <10ms, enrichment batches lazily — no global sleep cadence defeating per-domain latency guarantees.**

**Runtime enforcement + CI checks = the invariant never breaks as the codebase grows.**

Implementation effort: ~4 weeks phased, zero downtime, fully rollback-safe at each phase.
