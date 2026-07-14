# WATCHTOWER — Real-Time Monitoring System: Complete Technical Architecture

**Version:** 1.0  
**Status:** Design Document  
**Audience:** Python/SQLite developer with existing WATCHTOWER detection infrastructure

---

## Existing Infrastructure Inventory

Before designing new components, understand what is already production-grade:

**Already built:**
- `watchtower_detector.py` — rule-based creator linkage scoring (7 rules, strong/weak evidence model)
- `watchtower_operator_scanner.py` — 2-hop RPC downstream scan of known fee payers, stateless variant for worker/API separation
- `webhook_watchtower()` route in `main.py` — live Helius webhook handler for TREASURY/SIGNALLER/SUB_PROV/PROFIT_RELAY events
- `watchtower_events` table — monotonic sequenced event log with writer-thread sequence assignment
- `wt_sub_provisioners`, `wt_staged_wallets`, `wt_creator_launches` tables — entity lifecycle tables
- `watchtower_infra_events` table — raw infra wallet activity log
- `_check_watchtower_migration()` — background thread that cross-references migrations against known staged wallets
- `helius_watch.py` — dynamic address enrollment via shared webhook GET/PUT pattern
- `db_writer.py` — single-writer thread with monotonic event sequencing, `BEGIN IMMEDIATE` batching

**Key patterns to preserve:**
- Stateless RPC worker → POST to `/api/internal/` pattern (do not open SQLite in worker threads)
- `db_write_queue.enqueue_write()` for all writes (never bypass single-writer thread)
- `emit_event()` for all `watchtower_events` inserts (sequence assigned by writer)
- `helius_watch.py` `register_creator_address()` for candidate enrollment

---

## A. Real-Time Event Ingestion

### Webhook Architecture

The system uses **two dedicated Helius webhooks**, not one:

**Webhook 1: WATCHTOWER-INFRA** (already exists: `106e20f6`)
- Watches: TREASURY, SIGNALLER, TREASURY-UP, all confirmed SUB_PROV addresses, all confirmed PROFIT_RELAY addresses
- Type: `enhanced`, transactionTypes: `["TRANSFER"]`
- Delivery URL: `POST /api/webhook/watchtower` (already implemented)
- Purpose: sub-provisioner detection, creator candidate identification, profit sweep detection

**Webhook 2: WATCHTOWER-CANDIDATES** (already partially exists as `CREATOR_MOVEMENT_WEBHOOK_ID`)
- Watches: dynamically enrolled creator candidates (wt_staged_wallets with state DORMANT_FUNDED + ACTIVATED)
- Type: `enhanced`, transactionTypes: `["TRANSFER", "UNKNOWN"]`
- Delivery URL: `POST /api/webhook/watchtower-candidate`
- Purpose: detect first move of staged wallets, confirm pump.fun fee account touch

The 10,000-address limit per Helius webhook is not a concern for creator candidates (hundreds, not thousands). It IS a potential concern for trader swarms at scale — design around enrolling only high-confidence candidates.

**Critical architectural rule:** Do NOT enroll the pump.fun fee account `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P` as a webhook watch address. Use it only as a confirmation predicate when a watched candidate interacts with it.

### WebSocket Subscriptions

```
pAMM program: pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA
  → logsSubscribe (mentions pAMMBay6...)
  → filter: only process if a wallet in wt_staged_wallets appears in accountKeys
```

The existing `pumpfun_curve_listener.py` WebSocket infrastructure handles `logsSubscribe` with reconnect logic — extend it rather than building a second WS manager. Add a side filter: before dispatching any pAMM log event to the main migration pipeline, check if any account key matches `wt_staged_wallets`. If yes, also dispatch to `_handle_wt_pamm_event()`.

### Transaction Decoding Pipeline

```python
# For webhook payloads (Helius enhanced format):
def decode_wt_webhook_tx(tx: dict) -> WTEvent | None:
    sig        = tx.get("signature")
    block_time = tx.get("timestamp") or tx.get("blockTime") or int(time.time())
    
    # Primary signal extraction
    native_transfers = tx.get("nativeTransfers") or []
    token_transfers  = tx.get("tokenTransfers") or []
    instructions     = tx.get("instructions") or []
    
    # Identify infra wallet involvement
    for t in native_transfers:
        src  = t.get("fromUserAccount") or ""
        dest = t.get("toUserAccount") or ""
        amt  = (t.get("amount") or 0) / 1e9
        
        # Check against _WT_INFRA_ROLES (existing dict in main.py)
        # Check against enrolled candidates (in-memory set, refreshed every 60s)
        # Check against known pAMM program
        ...

# For RPC-fetched transactions (raw JSON):
def decode_wt_rpc_tx(tx_data: dict, watched_address: str) -> list[OutboundTransfer]:
    # Already implemented in watchtower_operator_scanner._extract_outbound()
    # Reuse that function directly
```

### Event Normalization Layer

Every decoded event becomes a `WTRawEvent` dict before hitting the write queue:

```python
{
    "event_class": "INFRA_MOVE" | "CANDIDATE_MOVE" | "PAMM_TOUCH" | "FANOUT_BURST",
    "sig":         str,
    "block_time":  int,
    "src":         str,
    "dest":        str,
    "amount_sol":  float,
    "infra_role":  str | None,   # "TREASURY", "SIGNALLER", etc.
    "candidate_id": str | None,  # wallet in wt_staged_wallets
    "raw_payload": str | None,   # JSON, capped at 64KB
}
```

This normalization happens synchronously in the webhook handler before any DB write, making downstream logic testable in isolation.

### Candidate-Scoped Dynamic Address Enrollment

When a wallet is promoted to `CREATOR_CANDIDATE` (score ≥ 60) or enters `wt_staged_wallets` with state `DORMANT_FUNDED`:

```python
async def enroll_candidate_for_monitoring(address: str) -> bool:
    """Add to WATCHTOWER-CANDIDATES webhook. Uses existing helius_watch.register_creator_address()."""
    from src.creators.helius_watch import register_creator_address
    result = await register_creator_address(address)
    # Log to wt_discovery_log (discovery_type='enrolled_candidate')
    emit_event("CANDIDATE_ENROLLED", wallet_address=address, ...)
    return result is not None
```

Enrollment is triggered from:
1. `wt_staged_wallets` INSERT — immediately after recording a new wallet from SUB_PROV fanout
2. Creator scoring engine when score crosses 60 threshold
3. Backfill on startup for all `DORMANT_FUNDED` wallets not yet enrolled

De-enrollment (to stay under webhook limits) occurs 48 hours after a wallet reaches `LAUNCHED` or `ABANDONED` state.

### Deduplication, Reorg Handling, Replay

**Deduplication:**
- All `watchtower_infra_events` uses `signature TEXT PRIMARY KEY` — idempotent INSERT OR IGNORE on every webhook delivery
- `watchtower_events` does NOT deduplicate by design (two real events = two rows); dedup is by `event_sequence` uniqueness
- Candidate promotions: `wt_staged_wallets` and `watchtower_launch_candidates` use `ON CONFLICT(...) DO UPDATE` — safe to replay

**Chain reorgs:** Helius enhanced webhooks include `slot` numbers. Store slot in `watchtower_infra_events` (add column). If a subsequent webhook delivers the same slot with a different signature, treat as reorg. For WATCHTOWER purposes, reorgs do not require reversal — the detection signals remain valid (a funded wallet is still funded). Mark reorged signatures with `reorged=1` column, do not delete.

**Replay/backfill:** The existing pattern in `backfill_watch_second_hop.py` and `backfill_watch_history.py` applies. For any new sub-provisioner detected, immediately trigger:
```python
scan_operator_downstream_stateless(address, rpc_url)  # existing function
# POST result to /api/internal/operator-graph
```
Helius 30-day lookback: if a sub-provisioner is discovered >30 days after its funding event, the transaction history is unavailable. Mitigate by processing promptly and caching signatures locally in `wt_infra_sig_cache`.

---

## B. Entity Model

### Entities and Their Properties

**TREASURY** (`wt_infra` role = TREASURY)
- Properties: `address`, `total_outflow_sol`, `last_active_at`, `known_sub_provisionees`
- Not stored separately — represented in `_WT_INFRA_ROLES` dict and `watchtower_infra_events`

**Sub-Provisioner** (table: `wt_sub_provisioners`)
- A wallet that receives ≥50 SOL from TREASURY and fans out to N child wallets within 2h
- Properties: `address`, `funding_amount`, `funded_by`, `fanout_count`, `fanout_amount`, `fanout_fingerprint`, `scan_status`, `last_scanned_at`
- Discovery: TREASURY outbound ≥50 SOL → scan_operator_downstream → confirm if fanout ≥5 wallets

**Relay Wallet**
- Intermediate hop between TREASURY and creator candidate
- Receives round SOL (e.g. 1.1), adds `.00203928` when forwarding
- Properties: same as sub-provisioner but role = RELAY
- Differentiated by: receives one large transfer, makes one specific-amount forward transfer

**Creator Wallet** (table: `wt_staged_wallets` → `wt_creator_launches`)
- A wallet that launches a pump.fun token
- Properties: `wallet_address`, `provisioner_address`, `state`, `provisioned_at`, `first_move_at`, `first_move_type`, `evidence_grade`

**Trader Wallet** (new table: `wt_trader_wallets` — not yet built)
- Fresh wallet receiving ~0.015 SOL, performing ATA setup + pAMM buy/sell cycles
- Properties: `wallet_address`, `provisioner_address`, `campaign_id`, `state`, `total_buys`, `total_sells`, `net_pnl_sol`, `last_sweep_at`

**Token** — already in `token_analysis` / `wt_creator_launches`

**Launch Campaign** (new table: `wt_campaigns` — not yet built)
- A provisioning event (one sub-provisioner → N creators + M traders over a time window)
- Properties: `campaign_id`, `sub_provisioner`, `started_at`, `creator_count`, `trader_count`, `token_mints_json`

### Lifecycle State Machines

**Creator Wallet:**
```
DORMANT
  → FUNDED           [trigger: sub-provisioner outbound with X.10203928 pattern detected]
  → CREATOR_CANDIDATE [trigger: score ≥ 60; enrolled in Helius webhook]
  → LAUNCHED         [trigger: wallet sends tx to pump.fun fee account 6EF8rrecthR5Dk...]
  → ABANDONED        [trigger: no activity 48h after LAUNCHED, or balance swept]

wt_staged_wallets.state values: DORMANT_FUNDED → ACTIVATED → LAUNCHED → ABANDONED
```

**Trader Wallet (new):**
```
FUNDED          [sub-provisioner outbound ~0.015 SOL, round amount]
  → TRADER_ACTIVE [trigger: ATA setup + first pAMM buy]
  → SWEPT         [trigger: large outbound back to provisioner]
  → RELOADED      [trigger: new inbound from provisioner after SWEPT]
  → ACTIVE_AGAIN  [loops back to TRADER_ACTIVE]
```

**Sub-Provisioner:**
```
DORMANT
  → ACTIVATED  [trigger: large inbound from TREASURY ≥50 SOL]
  → DEPLOYING  [trigger: fanout burst ≥5 wallets within 2h of ACTIVATED]
  → RECYCLING  [trigger: inbound transfers from trader wallets (profit sweeps)]
```

**Campaign:**
```
PROVISIONING  [sub-provisioner activated, wallets being funded]
  → CREATOR_LIVE    [at least one creator in campaign has hit pump.fun]
  → TRADERS_DEPLOYED [≥10 trader wallets active with pAMM buys]
  → AMM_ACTIVE      [bonding curve filling, pAMM activity ongoing]
  → SWEEPING        [wallets sending SOL back to provisioner]
  → COMPLETE        [no new pAMM activity for 24h, sweeps done]
```

---

## C. Detection Engine

### 1. Creator Candidate Scoring (0–100)

The existing `detect_watchtower_linkage()` provides binary strong/weak detection. Replace with a continuous score for ranked alerting:

```python
def score_creator_candidate(address: str, conn: sqlite3.Connection) -> tuple[int, dict]:
    """
    Returns (score_0_to_100, evidence_dict).
    
    Component scores (max points):
      freshness           : 20 pts — no prior tx history in any known table
      funding_lineage     : 25 pts — hops from candidate → sub-provisioner → TREASURY
      amount_fingerprint  : 20 pts — X.10203928 or known variant pattern
      amm_absence         : 15 pts — zero pAMM interactions since funding
      tx_count            : 10 pts — ≤3 total tx (creator lifecycle is 1-3 tx)
      outbound_inactivity : 10 pts — no outbound tx except the one pump.fun create
    """
    score = 0
    evidence = {}
    
    # FRESHNESS (20 pts)
    # Fresh = not in creator_risk_scores, no token_analysis creator match, 
    # no address_activity entry, no funder_incoming_transfers history
    is_fresh = _check_wallet_freshness(address, conn)
    if is_fresh:
        score += 20
        evidence['freshness'] = 20
    
    # FUNDING LINEAGE (25 pts, decays by hop distance)
    # 25 pts: funder IS known sub-provisioner (0 hops)
    # 15 pts: funder's funder IS known sub-provisioner (1 hop = relay)
    # 8  pts: funder's funder's funder IS TREASURY (2 hops)
    lineage_score, lineage_path = _trace_lineage(address, conn, max_hops=3)
    score += lineage_score
    evidence['lineage'] = {'score': lineage_score, 'path': lineage_path}
    
    # AMOUNT FINGERPRINT (20 pts)
    # 20 pts: exact X.10203928 match (known provisioning pattern)
    # 10 pts: X.00203928 or X.01003928 (variant patterns)
    # 5  pts: any amount ending in .028 (weak fingerprint)
    fp_score = _score_funding_fingerprint(address, conn)
    score += fp_score
    evidence['fingerprint'] = fp_score
    
    # AMM ABSENCE (15 pts)
    # 15 pts: zero pAMM program interactions (pAMMBay6...)
    # 0  pts: any pAMM interaction found
    pAMM_clean = _check_no_pamm_activity(address, conn)
    if pAMM_clean:
        score += 15
        evidence['amm_absence'] = 15
    
    # TX COUNT (10 pts)
    # Uses address_activity.tx_24h and tx_1h; also rpc_response_cache if available
    # 10 pts: ≤3 total tx; 5 pts: 4-10 tx; 0 pts: >10 tx
    tx_score = _score_tx_count(address, conn)
    score += tx_score
    evidence['tx_count'] = tx_score
    
    # OUTBOUND INACTIVITY (10 pts)
    # 10 pts: no outbound transfers except to pump.fun fee account
    # 5  pts: only one non-infra outbound
    # 0  pts: multiple outbound destinations
    ob_score = _score_outbound_inactivity(address, conn)
    score += ob_score
    evidence['outbound_inactivity'] = ob_score
    
    return min(score, 100), evidence


def _trace_lineage(address: str, conn: sqlite3.Connection, max_hops: int) -> tuple[int, list]:
    """Walk creator_funders and wt_sub_provisioners recursively up to max_hops."""
    path = []
    current = address
    for hop in range(max_hops):
        funder_row = conn.execute(
            "SELECT funder_address, amount_sol FROM creator_funders "
            "WHERE creator_address = ? LIMIT 1",
            (current,)
        ).fetchone()
        if not funder_row:
            break
        funder = funder_row[0]
        path.append(funder)
        
        # Check if funder is a known sub-provisioner
        sp_row = conn.execute(
            "SELECT 1 FROM wt_sub_provisioners WHERE address=?", (funder,)
        ).fetchone()
        if sp_row:
            score = 25 - (hop * 10)  # 25, 15, 8 ...
            return max(score, 5), path
        
        # Check if funder is TREASURY
        if funder == "44orWS68MqXG198M3YXyZoNrYtsNhgnNhtUT5SavqJFM":
            return 20 - (hop * 5), path
        
        current = funder
    
    return 0, path
```

**Score thresholds:**
- 0–59: CANDIDATE (log only)
- 60–84: `CREATOR_CANDIDATE` alert, enroll in Helius webhook
- 85–100: `HIGH_CONFIDENCE_CREATOR` alert, active monitoring priority

### 2. Trader Swarm Detection

```python
def detect_trader_swarm(sub_provisioner: str, conn: sqlite3.Connection, 
                        window_seconds: int = 7200) -> dict | None:
    """
    Detects simultaneous fanout from sub_provisioner to N fresh wallets with 
    tiny round amounts within window_seconds.
    """
    # Query wt_sub_provisioners fanout data + watchtower_operator_graph edges
    edges = conn.execute("""
        SELECT child_address, amount_sol, first_seen_at
        FROM watchtower_operator_graph
        WHERE operator_address = ?
          AND amount_sol < 0.1          -- trader amounts are small
          AND amount_sol > 0.005
          AND ABS(amount_sol - ROUND(amount_sol, 3)) < 0.0001  -- round amounts
        ORDER BY first_seen_at
    """, (sub_provisioner,)).fetchall()
    
    if len(edges) < 10:
        return None
    
    # Find densest 2-hour window
    times = [e['first_seen_at'] for e in edges]
    window_edges = _sliding_window_max(edges, window_seconds)
    
    if len(window_edges) < 10:
        return None
    
    # Verify ATA setup burst: check for token account creation tx in same window
    ata_count = _count_ata_setups_in_window(
        [e['child_address'] for e in window_edges], 
        window_edges[0]['first_seen_at'],
        window_edges[-1]['first_seen_at'],
        conn
    )
    
    return {
        "provisioner": sub_provisioner,
        "wallet_count": len(window_edges),
        "window_start": window_edges[0]['first_seen_at'],
        "window_end": window_edges[-1]['first_seen_at'],
        "avg_amount_sol": sum(e['amount_sol'] for e in window_edges) / len(window_edges),
        "ata_setups_detected": ata_count,
        "confidence": "HIGH" if ata_count > len(window_edges) * 0.5 else "MEDIUM"
    }
```

### 3. Synchronized Activity Detection

```python
def detect_synchronized_pamm_activity(token_mint: str, conn: sqlite3.Connection,
                                       window_seconds: int = 300) -> dict | None:
    """
    Detects N wallets linked to same provisioner interacting with same token within window.
    Uses wt_trader_wallets JOIN on campaign_id once that table exists.
    Currently: checks watchtower_operator_graph child_address set against token buyers.
    """
    # Get wallets known to be in the WATCHTOWER network
    known_wallets = set(conn.execute(
        "SELECT child_address FROM watchtower_operator_graph"
    ).scalars())  # plus wt_staged_wallets
    
    # Get wallets that interacted with this token's pAMM within window
    # Requires wt_pamm_interactions table (new) or sol_transfers filtered by pAMM
    interactors = conn.execute("""
        SELECT DISTINCT wallet_address, MIN(block_time) as first_at
        FROM wt_pamm_interactions
        WHERE token_mint = ? AND block_time > ?
        GROUP BY wallet_address
    """, (token_mint, int(time.time()) - window_seconds)).fetchall()
    
    linked = [w for w in interactors if w['wallet_address'] in known_wallets]
    
    if len(linked) < 10:
        return None
    
    # Cluster by provisioner
    by_provisioner = {}
    for w in linked:
        prov = conn.execute(
            "SELECT operator_address FROM watchtower_operator_graph WHERE child_address=?",
            (w['wallet_address'],)
        ).fetchone()
        if prov:
            by_provisioner.setdefault(prov[0], []).append(w)
    
    largest = max(by_provisioner.values(), key=len, default=[])
    if len(largest) < 10:
        return None
    
    return {
        "token_mint": token_mint,
        "linked_wallets": len(largest),
        "provisioner": max(by_provisioner, key=lambda k: len(by_provisioner[k])),
        "window_seconds": window_seconds,
    }
```

### 4. Sweep-Cycle Detection

```python
def detect_sweep_epoch(provisioner: str, conn: sqlite3.Connection,
                        window_seconds: int = 3600) -> dict | None:
    """
    Detects cluster of wallets returning SOL to provisioner within 1h window.
    Data source: watchtower_infra_events (direction=inbound, infra_address=provisioner)
    or wt_pamm_interactions profit routing.
    """
    now = int(time.time())
    sweeps = conn.execute("""
        SELECT counterparty, amount_sol, block_time
        FROM watchtower_infra_events
        WHERE infra_address = ?
          AND direction = 'inbound'
          AND block_time > ?
        ORDER BY block_time
    """, (provisioner, now - window_seconds)).fetchall()
    
    if len(sweeps) < 3:
        return None
    
    # Only count wallets we've seen in watchtower network
    known = set(conn.execute(
        "SELECT child_address FROM watchtower_operator_graph WHERE operator_address=?",
        (provisioner,)
    ).scalars())
    
    linked_sweeps = [s for s in sweeps if s['counterparty'] in known]
    
    if len(linked_sweeps) < 3:
        return None
    
    return {
        "provisioner": provisioner,
        "sweep_count": len(linked_sweeps),
        "total_sol_swept": sum(s['amount_sol'] for s in linked_sweeps),
        "window_start": linked_sweeps[0]['block_time'],
        "window_end": linked_sweeps[-1]['block_time'],
    }
```

### 5. Funding Topology Tracing

```python
def trace_funding_topology(address: str, conn: sqlite3.Connection, 
                            max_hops: int = 5) -> dict:
    """
    BFS from address upward through creator_funders → wt_sub_provisioners → TREASURY.
    Returns a graph dict suitable for serialization and storage.
    """
    TREASURY = "44orWS68MqXG198M3YXyZoNrYtsNhgnNhtUT5SavqJFM"
    
    visited = {}  # address → node_info
    queue   = [(address, 0, None)]
    edges   = []
    
    while queue:
        current, hop, parent = queue.pop(0)
        if current in visited or hop > max_hops:
            continue
        
        # Classify node
        role = _classify_wallet(current, conn)  # CREATOR | SUB_PROV | RELAY | TREASURY | UNKNOWN
        visited[current] = {"address": current, "hop": hop, "role": role}
        
        if parent:
            funder_row = conn.execute(
                "SELECT amount_sol FROM creator_funders WHERE creator_address=? AND funder_address=?",
                (current, parent)
            ).fetchone() or conn.execute(
                "SELECT funding_amount FROM wt_sub_provisioners WHERE address=? AND funded_by=?",
                (current, parent)
            ).fetchone()
            edges.append({"from": parent, "to": current, 
                         "amount_sol": funder_row[0] if funder_row else None, "hop": hop})
        
        if current == TREASURY:
            break  # reached root
        
        # Expand upward
        funder = conn.execute(
            "SELECT funder_address FROM creator_funders WHERE creator_address=? LIMIT 1",
            (current,)
        ).fetchone()
        if funder:
            queue.append((funder[0], hop + 1, current))
    
    return {"nodes": list(visited.values()), "edges": edges, 
            "reaches_treasury": TREASURY in visited,
            "hop_count": max(v["hop"] for v in visited.values()) if visited else 0}
```

### 6. Launch Confirmation

```python
PUMPFUN_FEE_ACCOUNT = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

def handle_candidate_webhook_tx(address: str, tx: dict, conn: sqlite3.Connection) -> None:
    """
    Called when WATCHTOWER-CANDIDATES webhook fires for a monitored address.
    Checks if the candidate touched the pump.fun fee account.
    """
    native_transfers = tx.get("nativeTransfers") or []
    
    for t in native_transfers:
        src  = t.get("fromUserAccount") or ""
        dest = t.get("toUserAccount") or ""
        amt  = (t.get("amount") or 0) / 1e9
        
        if src == address and dest == PUMPFUN_FEE_ACCOUNT and amt > 0:
            # LAUNCH CONFIRMED
            mint = _extract_mint_from_tx(tx)  # from tokenTransfers or instructions
            conn.execute("""
                UPDATE wt_staged_wallets SET state='LAUNCHED', first_move_at=?, 
                    first_move_sig=?, first_move_type='pumpfun_create'
                WHERE wallet_address=?
            """, (tx.get("blockTime"), tx.get("signature"), address))
            
            conn.execute("""
                INSERT OR IGNORE INTO wt_creator_launches
                    (creator_wallet, mint_address, launch_tx, launched_at, 
                     launch_platform, evidence_grade, evidence_basis, launch_success_state)
                VALUES (?, ?, ?, ?, 'pump_fun', 'STRONG', 'fee_account_tx', 'launched_only')
            """, (address, mint, tx.get("signature"), tx.get("blockTime")))
            
            emit_event("PUMPFUN_CREATE_CONFIRMED", wallet_address=address, 
                      token_mint=mint, payload={"sig": tx.get("signature"), "amount_sol": amt})
            break
```

---

## D. Topology Graph

### Graph Schema

The existing `watchtower_operator_graph` table is an adjacency list. The design for the full topology graph extends it:

```sql
-- Nodes (unified wallet registry)
CREATE TABLE IF NOT EXISTS wt_graph_nodes (
    address        TEXT PRIMARY KEY,
    node_type      TEXT NOT NULL,  -- TREASURY | SUB_PROV | RELAY | CREATOR | TRADER | SWEEP_COLLECTOR | UNKNOWN
    campaign_id    TEXT,           -- NULL if not yet attributed
    first_seen_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    last_active_at INTEGER,
    state          TEXT,
    score          INTEGER,        -- creator candidate score if applicable
    evidence_json  TEXT
);

-- Edges (directed: from_address → to_address)
CREATE TABLE IF NOT EXISTS wt_graph_edges (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    from_address   TEXT NOT NULL,
    to_address     TEXT NOT NULL,
    edge_type      TEXT NOT NULL,  -- funded_by | swept_to | traded | created | dust_signalled
    amount_sol     REAL,
    tx_signature   TEXT,
    block_time     INTEGER NOT NULL,
    campaign_id    TEXT,
    UNIQUE(from_address, to_address, edge_type, tx_signature)
);

CREATE INDEX IF NOT EXISTS idx_wt_edges_from ON wt_graph_edges(from_address, block_time DESC);
CREATE INDEX IF NOT EXISTS idx_wt_edges_to   ON wt_graph_edges(to_address,   block_time DESC);
CREATE INDEX IF NOT EXISTS idx_wt_edges_campaign ON wt_graph_edges(campaign_id) WHERE campaign_id IS NOT NULL;

-- Campaigns
CREATE TABLE IF NOT EXISTS wt_campaigns (
    campaign_id      TEXT PRIMARY KEY,  -- UUID or hash(sub_provisioner + started_at)
    sub_provisioner  TEXT NOT NULL,
    started_at       INTEGER NOT NULL,
    ended_at         INTEGER,
    state            TEXT NOT NULL DEFAULT 'PROVISIONING',
    creator_count    INTEGER NOT NULL DEFAULT 0,
    trader_count     INTEGER NOT NULL DEFAULT 0,
    token_mints_json TEXT,             -- JSON array
    total_sol_deployed REAL,
    total_sol_swept    REAL,
    evidence_json    TEXT
);

CREATE INDEX IF NOT EXISTS idx_wt_campaigns_provisioner ON wt_campaigns(sub_provisioner);
CREATE INDEX IF NOT EXISTS idx_wt_campaigns_state       ON wt_campaigns(state);
```

### Recursive Lineage Tracing Algorithm

```python
def trace_lineage_recursive(start: str, conn: sqlite3.Connection, 
                             visited: set = None, depth: int = 0) -> list[dict]:
    """
    Walk wt_graph_edges upward (edge_type='funded_by') from start.
    Returns list of nodes encountered, ordered root-first.
    Terminates at TREASURY or depth > 6.
    """
    TREASURY = "44orWS68MqXG198M3YXyZoNrYtsNhgnNhtUT5SavqJFM"
    MAX_DEPTH = 6
    
    if visited is None:
        visited = set()
    if start in visited or depth > MAX_DEPTH:
        return []
    visited.add(start)
    
    path = []
    funders = conn.execute(
        "SELECT from_address, amount_sol, block_time FROM wt_graph_edges "
        "WHERE to_address = ? AND edge_type = 'funded_by' LIMIT 3",
        (start,)
    ).fetchall()
    
    for funder in funders:
        parent_path = trace_lineage_recursive(funder['from_address'], conn, visited, depth + 1)
        path.extend(parent_path)
        path.append({"address": funder['from_address'], "depth": depth + 1,
                    "amount_sol": funder['amount_sol'], "block_time": funder['block_time']})
        if funder['from_address'] == TREASURY:
            break
    
    return path
```

### Cluster Attribution

```python
def attribute_wallet_to_campaign(address: str, conn: sqlite3.Connection) -> str | None:
    """
    Walk upward from address to find which campaign (sub_provisioner) it belongs to.
    Check wt_graph_nodes.campaign_id first (cached). If NULL, trace lineage.
    """
    node = conn.execute(
        "SELECT campaign_id, node_type FROM wt_graph_nodes WHERE address=?", (address,)
    ).fetchone()
    
    if node and node['campaign_id']:
        return node['campaign_id']
    
    # Trace upward until we hit a sub-provisioner
    lineage = trace_lineage_recursive(address, conn)
    for entry in reversed(lineage):
        sp_row = conn.execute(
            "SELECT campaign_id FROM wt_campaigns WHERE sub_provisioner=?",
            (entry['address'],)
        ).fetchone()
        if sp_row:
            # Cache it back
            conn.execute(
                "INSERT OR IGNORE INTO wt_graph_nodes (address, node_type, campaign_id) VALUES (?, 'UNKNOWN', ?)",
                (address, sp_row['campaign_id'])
            )
            return sp_row['campaign_id']
    
    return None
```

### Temporal Graph Evolution

The `wt_graph_edges.block_time` column enables time-sliced queries:

```sql
-- Show who was funded in the last 2 hours
SELECT from_address, to_address, amount_sol, block_time
FROM wt_graph_edges
WHERE edge_type = 'funded_by' AND block_time > strftime('%s','now') - 7200
ORDER BY block_time DESC;

-- Campaign timeline: when were traders enrolled vs creators launched
SELECT 
    c.campaign_id,
    MIN(CASE WHEN n.node_type='CREATOR' THEN e.block_time END) as first_creator_at,
    MIN(CASE WHEN n.node_type='TRADER' THEN e.block_time END) as first_trader_at,
    COUNT(DISTINCT CASE WHEN n.node_type='CREATOR' THEN e.to_address END) as creators,
    COUNT(DISTINCT CASE WHEN n.node_type='TRADER' THEN e.to_address END) as traders
FROM wt_campaigns c
JOIN wt_graph_edges e ON e.campaign_id = c.campaign_id
JOIN wt_graph_nodes n ON n.address = e.to_address
GROUP BY c.campaign_id;
```

---

## E. Alerting System

### Alert Type Definitions

Each alert is written to `watchtower_events` with `event_type` matching the alert name, then optionally forwarded to an external notification channel (Telegram, webhook, etc.).

---

**`NEW_SUBPROVISIONER`**
```
Trigger:
  - TREASURY outbound ≥50 SOL to wallet W
  - W has ≥5 distinct outbound transfers within 2h of funding
  - W not already in wt_sub_provisioners
  
Confidence scoring:
  - 70 base
  - +15 if fanout amounts match known fingerprint pattern
  - +10 if fanout count ≥20
  - -20 if W has prior on-chain history (not fresh)
  
False positive analysis:
  - Other large-scale operators sending ≥50 SOL to intermediaries
  - CEX withdrawal addresses (will have many counterparties)
  - Mitigate: check that >80% of fanout destinations are fresh wallets
  
Escalation: auto-trigger scan_operator_downstream_stateless(); 
  if confirmed, promote to CONFIRMED_SUBPROVISIONER event
```

**`CREATOR_CANDIDATE`**
```
Trigger: creator scoring engine returns score ≥60
  
Evidence required:
  - At minimum: funding lineage score > 0 AND (fingerprint match OR freshness)
  
Confidence: score value (60–100)

False positive: 
  - Any other operation using X.10203928-like amounts (rare, but possible)
  - Fresh wallets funded by unknown addresses that happen to pass heuristics
  - Mitigate: require lineage to reach known infrastructure (score component > 0)

Action: enroll in WATCHTOWER-CANDIDATES webhook
```

**`HIGH_CONFIDENCE_CREATOR`**
```
Trigger: score ≥85
Confidence: score value

Action:
  - Priority enrollment in Helius webhook
  - Add to in-memory fast-check set (next webhook event checked within 100ms)
  - Alert: "HIGH CONFIDENCE CREATOR CANDIDATE — score={score}, wallet={address[:20]}"
```

**`TRADER_SWARM_DEPLOYMENT`**
```
Trigger: N≥10 fresh wallets funded from same source within 2h, 
         amounts are small (0.005–0.1 SOL) and round
         
Confidence:
  - 60 base
  - +10 per 10 additional wallets (cap at +20)
  - +15 if ATA setup burst detected within 30min of funding
  - +10 if funding source is known sub-provisioner
  
False positive:
  - Airdrop distributions to many wallets
  - Mitigate: amounts must be round (not airdrop-like) AND destinations must be fresh
```

**`PAMM_CAMPAIGN_DETECTED`**
```
Trigger: ≥10 wallets linked to same provisioner interacting with same token_mint
         within 300 seconds
         
Confidence:
  - 65 base
  - +20 if provisioner is confirmed WATCHTOWER sub-provisioner
  - +10 if creator of token is in wt_staged_wallets
  
False positive:
  - Popular token with many buyers who happen to be in known WATCHTOWER network
  - Mitigate: require provisioner linkage confidence ≥ MEDIUM for at least 7/10 wallets
```

**`PROFIT_SWEEP_EPOCH`**
```
Trigger: ≥3 wallets in wt_graph_nodes sending SOL to same provisioner within 1h

Confidence:
  - 75 base if provisioner is confirmed sub-provisioner
  - +10 if sweep count ≥10
  - +10 if total SOL swept ≥5 SOL
  
False positive: Low — requires multiple wallets to sweep to same address
```

**`RELOAD_CYCLE`**
```
Trigger: Provisioner sending SOL to wallets that previously swept to it (SWEPT state)

Confidence: 80 — this specific pattern is operationally distinctive

Action: Update wt_trader_wallets state to RELOADED
```

**`PUMPFUN_CREATE_CONFIRMED`**
```
Trigger: Monitored candidate (in wt_staged_wallets) sends tx touching pump.fun fee account

Confidence: 95 — on-chain confirmation

Action:
  - Update wt_staged_wallets.state = 'LAUNCHED'
  - Insert into wt_creator_launches
  - emit_event("PUMPFUN_CREATE_CONFIRMED", ...)
  - Alert immediately: "LAUNCH CONFIRMED — {address[:20]} minted {mint[:20]}"
```

**`NEW_CAMPAIGN_CLUSTER`**
```
Trigger: New sub-provisioner confirmed + associated creator(s) identified + 
         ≥10 trader wallets attributed to same campaign
  
Confidence: composite — avg of sub_prov confidence + creator confidence + swarm confidence

Action:
  - Create wt_campaigns record
  - Attribute all known wallets to campaign_id
  - Alert: "NEW CAMPAIGN CLUSTER — {N} creators, {M} traders, provisioner={address[:20]}"
```

---

## F. Data Storage

### Complete SQLite Schema (New Tables)

The existing tables (`wt_sub_provisioners`, `wt_staged_wallets`, `wt_creator_launches`, `watchtower_events`, `watchtower_infra_events`, `watchtower_operator_graph`, `watchtower_launch_candidates`) remain unchanged. Add:

```sql
-- Trader wallet tracking (new)
CREATE TABLE IF NOT EXISTS wt_trader_wallets (
    wallet_address    TEXT PRIMARY KEY,
    provisioner_address TEXT NOT NULL,
    campaign_id       TEXT,
    funded_at         INTEGER,
    funded_amount_sol REAL,
    state             TEXT NOT NULL DEFAULT 'FUNDED',
    -- 'FUNDED' | 'TRADER_ACTIVE' | 'SWEPT' | 'RELOADED' | 'ACTIVE_AGAIN' | 'DORMANT'
    state_changed_at  INTEGER,
    total_buys        INTEGER NOT NULL DEFAULT 0,
    total_sells       INTEGER NOT NULL DEFAULT 0,
    total_bought_sol  REAL NOT NULL DEFAULT 0,
    total_sold_sol    REAL NOT NULL DEFAULT 0,
    net_pnl_sol       REAL,
    last_pamm_at      INTEGER,
    last_sweep_at     INTEGER,
    sweep_destination TEXT,
    evidence_json     TEXT,
    created_at        INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    updated_at        INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE INDEX IF NOT EXISTS idx_wt_traders_provisioner ON wt_trader_wallets(provisioner_address);
CREATE INDEX IF NOT EXISTS idx_wt_traders_campaign    ON wt_trader_wallets(campaign_id) WHERE campaign_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_wt_traders_state       ON wt_trader_wallets(state);

-- pAMM interaction log (new — enables synchronized activity detection)
CREATE TABLE IF NOT EXISTS wt_pamm_interactions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    signature      TEXT NOT NULL UNIQUE,
    block_time     INTEGER NOT NULL,
    wallet_address TEXT NOT NULL,
    token_mint     TEXT NOT NULL,
    direction      TEXT NOT NULL,  -- 'buy' | 'sell'
    sol_amount     REAL NOT NULL,
    token_amount   REAL,
    campaign_id    TEXT,
    created_at     INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE INDEX IF NOT EXISTS idx_wt_pamm_wallet    ON wt_pamm_interactions(wallet_address, block_time DESC);
CREATE INDEX IF NOT EXISTS idx_wt_pamm_mint      ON wt_pamm_interactions(token_mint, block_time DESC);
CREATE INDEX IF NOT EXISTS idx_wt_pamm_campaign  ON wt_pamm_interactions(campaign_id, block_time DESC);
CREATE INDEX IF NOT EXISTS idx_wt_pamm_time      ON wt_pamm_interactions(block_time DESC);

-- Creator candidate scores (new — replace binary watchtower_related with continuous score)
CREATE TABLE IF NOT EXISTS wt_candidate_scores (
    wallet_address  TEXT PRIMARY KEY,
    score           INTEGER NOT NULL DEFAULT 0,
    score_breakdown TEXT,         -- JSON: {freshness: N, lineage: N, ...}
    lineage_path    TEXT,         -- JSON array of addresses
    reaches_treasury INTEGER NOT NULL DEFAULT 0,
    enrolled_at     INTEGER,      -- when added to Helius webhook
    scored_at       INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    rescored_at     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_wt_scores_score ON wt_candidate_scores(score DESC);

-- Graph nodes and edges (defined in section D above)
-- wt_graph_nodes, wt_graph_edges, wt_campaigns

-- Campaign table
CREATE TABLE IF NOT EXISTS wt_campaigns (
    campaign_id       TEXT PRIMARY KEY,
    sub_provisioner   TEXT NOT NULL,
    started_at        INTEGER NOT NULL,
    ended_at          INTEGER,
    state             TEXT NOT NULL DEFAULT 'PROVISIONING',
    creator_count     INTEGER NOT NULL DEFAULT 0,
    trader_count      INTEGER NOT NULL DEFAULT 0,
    token_mints_json  TEXT,
    total_sol_deployed REAL NOT NULL DEFAULT 0,
    total_sol_swept   REAL NOT NULL DEFAULT 0,
    evidence_json     TEXT,
    created_at        INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    updated_at        INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

-- Webhook enrollment registry (new — track what's in each Helius webhook)
CREATE TABLE IF NOT EXISTS wt_webhook_enrollments (
    wallet_address TEXT PRIMARY KEY,
    webhook_id     TEXT NOT NULL,
    enrolled_at    INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    de_enrolled_at INTEGER,
    is_active      INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_wt_enrollment_active ON wt_webhook_enrollments(is_active, webhook_id);
```

### Event Log Structure (Replay Capability)

`watchtower_events` is already an append-only log with monotonic `event_sequence`. To enable full replay:

```sql
-- Add slot column for reorg detection (ALTER TABLE on existing)
ALTER TABLE watchtower_events ADD COLUMN slot INTEGER;
ALTER TABLE watchtower_infra_events ADD COLUMN slot INTEGER;

-- Replay query: events since sequence N
SELECT * FROM watchtower_events WHERE event_sequence > :last_processed_sequence ORDER BY event_sequence ASC;
```

The writer thread's monotonic counter (`_event_sequence_counter`) ensures strict ordering even when multiple threads emit events concurrently.

### Wallet-State Caching

```python
# In-memory cache (process-level, not persistent)
_candidate_set: set[str] = set()   # wallets in DORMANT_FUNDED or ACTIVATED
_infra_set: frozenset[str]         # already exists in watchtower_detector.py

def refresh_candidate_cache(conn: sqlite3.Connection) -> None:
    """Called every 60s from a background thread."""
    global _candidate_set
    rows = conn.execute(
        "SELECT wallet_address FROM wt_staged_wallets WHERE state IN ('DORMANT_FUNDED', 'ACTIVATED')"
    ).fetchall()
    _candidate_set = {r[0] for r in rows}

# Webhook handler fast-path: check in-memory set before DB lookup
if address in _candidate_set:
    handle_candidate_webhook_tx(address, tx, conn)
```

### Time-Series Indexing for Sweep/Reload Detection

```sql
-- All time-based queries use block_time DESC indexes:
-- watchtower_infra_events(infra_address, block_time DESC) -- already exists
-- wt_pamm_interactions(token_mint, block_time DESC) -- new
-- wt_pamm_interactions(wallet_address, block_time DESC) -- new

-- Sweep detection query (uses existing index efficiently):
SELECT counterparty, SUM(amount_sol) as total, COUNT(*) as tx_count, 
       MIN(block_time) as first_at, MAX(block_time) as last_at
FROM watchtower_infra_events
WHERE infra_address = :provisioner
  AND direction = 'inbound'
  AND block_time BETWEEN :window_start AND :window_end
GROUP BY counterparty
HAVING tx_count >= 1
ORDER BY total DESC;
```

---

## G. Auto-Discovery

### TREASURY Outflow Monitoring

Already implemented in `webhook_watchtower()`:
- TREASURY outbound ≥50 SOL → candidate sub-provisioner → `wt_sub_provisioners` with `scan_status='scanning'`
- Background thread calls `scan_operator_downstream_stateless()` → POST to internal API

**Gap to close:** The webhook only fires for future transactions. For any historical TREASURY outflows (from before webhook was active), run a one-time backfill:

```python
def backfill_treasury_outflows(rpc_url: str, db_path: str) -> None:
    """Pull TREASURY transaction history, identify large outflows, seed wt_sub_provisioners."""
    TREASURY = "44orWS68MqXG198M3YXyZoNrYtsNhgnNhtUT5SavqJFM"
    # getSignaturesForAddress(TREASURY, limit=1000)
    # For each sig: getTransaction → extract outflows ≥50 SOL
    # Insert into wt_sub_provisioners if not already present
```

### Fanout Burst Detection

```python
def detect_fanout_burst(address: str, sigs: list, rpc_url: str) -> dict | None:
    """
    Given a new wallet with N recent signatures, detect if it's performing a fanout.
    Trigger: fresh wallet, received large SOL inbound, then sent to ≥5 addresses within 2h.
    """
    # Group sigs by 2-hour window
    # If densest window has ≥5 outbound to fresh wallets → fanout detected
    # Return fingerprint of amounts for pattern matching
```

### Recursive Sweep Pattern Recognition

When `wt_trader_wallets` records a sweep (wallet → provisioner), check if that provisioner itself later sweeps upward:

```python
def check_recursive_sweep(sweep_destination: str, conn: sqlite3.Connection) -> None:
    """
    When a trader sweeps to address A, check if A later sweeps to TREASURY or TREASURY-UP.
    If yes: A is a profit relay, add to _WT_INFRA_ROLES.
    """
    # Query watchtower_infra_events for outbound from sweep_destination to TREASURY/TREASURY-UP
    # If found within 24h of receiving the trader sweep → mark as PROFIT_RELAY
    # Enroll A in WATCHTOWER-INFRA webhook
```

### Repeated Creator Funding Structure Matching

```python
def match_creator_funding_structure(amount_sol: float) -> dict:
    """
    Given a funding amount, check if it matches any known provisioning fingerprint.
    Uses the CONFIRMED_FINGERPRINT_BATCHES list in watchtower_detector.py.
    Returns {matched: bool, pattern: str, confidence: str}
    """
    amount_str = f"{amount_sol:.8f}"
    for pattern, label in CONFIRMED_FINGERPRINT_BATCHES:
        # Convert SQL LIKE pattern to Python string check
        suffix = pattern.replace('%', '')
        if amount_str.endswith(suffix):
            return {"matched": True, "pattern": suffix, "label": label, "confidence": "HIGH"}
    
    # Check for new variant: fractional part has .028 or .0203928-like structure
    frac = amount_sol % 1
    if 0.001 < frac < 0.005 and str(frac).endswith("3928"):
        return {"matched": True, "pattern": "variant_3928", "label": "possible_new_fingerprint", 
                "confidence": "MEDIUM"}
    
    return {"matched": False}
```

---

## H. Failure Mode Analysis

### False Positives

**Similar amounts from other operations:**
The `.10203928` pattern has zero false positives in 106 confirmed cases — a scripted constant this specific does not occur organically. The `.00203928` variant is shared with a separate operation (confirmed zero overlap in scan). Risk: minimal for fingerprint-based detection. Higher risk for scoring-based detection where multiple weak signals combine.

Mitigation: the lineage score component prevents scoring wallets that have no upstream connection to known infrastructure. A wallet must connect to a known entity (sub-provisioner, TREASURY, or infrastructure wallet) to score above 20.

**CEX withdrawal addresses:** Large outflows from exchanges can look like sub-provisioner fanouts. Mitigation: exclude addresses in `_EXCLUDED_PROGRAMS` and addresses with token program interactions or existing `creator_risk_scores` history.

### Topology Drift

WATCHTOWER may change fingerprint amounts (easy — just change the script constant), change provisioner structure (harder), or add relay hops (increases detection latency but doesn't break detection if TREASURY monitoring is in place).

Detection lag from drift: if the new fingerprint is not in `CONFIRMED_FINGERPRINT_BATCHES`, fingerprint-based detection fails but lineage-based detection survives as long as TREASURY outflow monitoring catches the new sub-provisioner.

Response: when `NEW_SUBPROVISIONER` fires, scan its fanout and extract the new amount pattern. Add to `CONFIRMED_FINGERPRINT_BATCHES` dynamically (or store in DB table `wt_fingerprint_patterns`).

### RPC Gaps (Helius 30-Day Lookback)

If a sub-provisioner was funded >30 days ago, `getSignaturesForAddress` returns incomplete history. Existing wallets in `watchtower_operator_graph` are not affected (already scanned). New sub-provisioners are affected only if discovered late.

Mitigation:
1. TREASURY webhook fires in real-time → sub-provisioner scanned within minutes of funding → within 30-day window by definition
2. For backfill of pre-webhook era: already handled by `backfill_watch_history.py` and `backfill_watch_second_hop.py`
3. Cache all observed signatures in `wt_infra_sig_cache` table (signature, block_time, wallet_address) as they arrive via webhook

### Candidate Explosion

If WATCHTOWER scales to 5,000+ creator wallets per wave, the Helius webhook limit (10,000 addresses) becomes a concern.

Mitigation:
- Enroll only wallets with score ≥60 (not all staged wallets)
- De-enroll wallets 48h after LAUNCHED or ABANDONED
- Maintain enrollment count in `wt_webhook_enrollments`; when approaching 9,000, de-enroll oldest ABANDONED wallets first
- For very large waves (>5,000 simultaneous candidates): fall back to polling wt_staged_wallets every 60s against token_analysis (existing `watchtower_dormant_seen` pattern)

### Decoy Wallets / Adversarial Obfuscation

WATCHTOWER could add noise wallets to confuse detection — wallets funded with the fingerprint amount that never launch, or wallets that interact with pAMM but aren't actually coordinated.

Mitigation: decoys are expensive (each wallet costs real SOL to create and fund). Detection does not need to catch 100% — even catching 60% of wallets before launch provides operational value. The lineage graph approach is harder to spoof than amount-based detection.

### Noisy pAMM Traffic

`pAMMBay6...` processes thousands of transactions per minute. Filtering in the WebSocket listener must happen at the account key level, not at the program level.

Design: `logsSubscribe` with mentions filter, then in `_handle_log_event()`, check intersection of log account keys against `_candidate_set` (in-memory set). Only process if intersection is non-empty. This is O(1) per event with the set lookup.

### New Sub-Provisioner Not Yet in Known Set

The system currently hard-codes known sub-provisioners in `_WT_INFRA_ROLES`. A new sub-provisioner funded by TREASURY (not yet confirmed) won't have its outbound activity recognized as "SUB_PROV outbound."

Mitigation:
1. `NEW_SUBPROVISIONER` alert adds to `wt_sub_provisioners` table
2. `_WT_INFRA_ROLES` dict is refreshed from `wt_sub_provisioners WHERE scan_status='confirmed'` every 5 minutes
3. The webhook handler checks both the hardcoded dict AND the live DB table

```python
# In webhook_watchtower() hot path:
_WT_DYNAMIC_SUBPROVS: dict[str, str] = {}  # refreshed from DB every 5 min
_wt_dynamic_refresh_at: int = 0

def _maybe_refresh_dynamic_roles(conn):
    global _WT_DYNAMIC_SUBPROVS, _wt_dynamic_refresh_at
    now = int(time.time())
    if now - _wt_dynamic_refresh_at < 300:
        return
    rows = conn.execute(
        "SELECT address FROM wt_sub_provisioners WHERE scan_status='confirmed'"
    ).fetchall()
    _WT_DYNAMIC_SUBPROVS = {r[0]: "SUB_PROV" for r in rows}
    _wt_dynamic_refresh_at = now

# Then in the handler:
effective_roles = {**_WT_INFRA_ROLES, **_WT_DYNAMIC_SUBPROVS}
if src in effective_roles: ...
```

---

## I. Implementation

### Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         EXTERNAL DATA SOURCES                               │
│                                                                             │
│  Helius INFRA Webhook          Helius CANDIDATE Webhook                     │
│  (TREASURY, SIGNALLER,         (wt_staged_wallets wallets)                 │
│   SUB_PROVs, PROFIT_RELAYs)   ────────────┐                                │
│  ─────────────┐                           │                                 │
│               │                           │    Helius/pAMM WebSocket        │
│               │                           │    (logsSubscribe pAMM program) │
│               │                           │    ─────────┐                   │
└───────────────┼───────────────────────────┼─────────────┼───────────────────┘
                │                           │             │
                ▼                           ▼             ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                          FLASK APPLICATION (main.py)                         │
│                                                                              │
│  POST /api/webhook/watchtower   POST /api/webhook/watchtower-candidate       │
│  ─────────────────────────────  ───────────────────────────────────────────  │
│  │                              │                                            │
│  ▼                              ▼                                            │
│  _handle_infra_webhook()        _handle_candidate_webhook()                  │
│  • decode transfers             • check fee account touch → LAUNCH CONFIRMED │
│  • identify infra role          • check pAMM → TRADER_ACTIVE                 │
│  • detect sub-prov candidates   • score_creator_candidate()                  │
│  • detect creator candidates    • enroll_candidate_for_monitoring()           │
│  • emit events (queue)          • emit events (queue)                        │
│         │                              │                                     │
│         └──────────────┬──────────────┘                                     │
│                        ▼                                                     │
│                 db_write_queue                                               │
│                 (enqueue_write)                                              │
│                        │                                                     │
│                        ▼                                                     │
│                 db_writer thread                                             │
│                 (BEGIN IMMEDIATE, executemany, monotonic seq)                │
│                        │                                                     │
│                        ▼                                                     │
│              ┌─────────────────────┐                                         │
│              │       SQLite        │                                         │
│              │  (flex.db / flex_   │                                         │
│              │  complete_database) │                                         │
│              └─────────────────────┘                                         │
│                                                                              │
│  Background threads (daemon):                                                │
│  • _refresh_candidate_cache() every 60s                                      │
│  • _maybe_refresh_dynamic_roles() every 5min                                 │
│  • _run_sweep_detection() every 5min                                         │
│  • _check_watchtower_migration() on every migration event                    │
│                                                                              │
│  Background workers (stateless, POST to /api/internal/):                     │
│  • scan_operator_downstream_stateless() for new sub-provisioners             │
│  • score_creator_candidate() for new candidates                              │
└──────────────────────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                         DETECTION ENGINE                                     │
│                                                                              │
│  Creator Scoring     Swarm Detection    Sweep Detection    Launch Confirm    │
│  (score 0–100)       (fanout burst)     (epoch detector)   (fee account)     │
│       │                   │                   │                  │           │
│       └───────────────────┴───────────────────┴──────────────────┘           │
│                               │                                              │
│                               ▼                                              │
│                        Alert Generator                                       │
│                    (watchtower_events + external notify)                     │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Event Flow Diagrams

**Sub-Provisioner Discovery Flow:**
```
TREASURY sends ≥50 SOL to wallet W
  → Helius INFRA webhook fires
  → webhook_watchtower() identifies: role=TREASURY, direction=outbound, amount≥50
  → INSERT wt_sub_provisioners (scan_status='scanning')
  → emit_event('sub_provisioner_candidate', wallet=W)
  → background thread: scan_operator_downstream_stateless(W)
    → getSignaturesForAddress(W, limit=100)
    → getTransaction for each sig
    → extract outbound transfers to fresh wallets
    → if ≥5 fresh wallets in 2h window:
      → UPDATE wt_sub_provisioners SET scan_status='confirmed'
      → INSERT wt_sub_provisioners fanout data
      → INSERT watchtower_events type='sub_provisioner_detected'
      → for each child: INSERT wt_staged_wallets state='DORMANT_FUNDED'
      → for each child: enroll_candidate_for_monitoring(child)
      → ALERT: NEW_SUBPROVISIONER
```

**Creator Launch Confirmation Flow:**
```
Helius CANDIDATE webhook fires for monitored wallet W
  → POST /api/webhook/watchtower-candidate
  → _handle_candidate_webhook() decodes tx
  → FOR EACH native transfer in tx:
      IF src == W AND dest == PUMPFUN_FEE_ACCOUNT:
        → handle_candidate_webhook_tx(W, tx, conn)
        → extract mint from tokenTransfers or instructions
        → UPDATE wt_staged_wallets state='LAUNCHED'
        → INSERT wt_creator_launches
        → emit_event('PUMPFUN_CREATE_CONFIRMED', wallet=W, mint=mint)
        → ALERT: PUMPFUN_CREATE_CONFIRMED (confidence: 95)
        → _check_watchtower_migration will pick this up at migration time
```

**Trader Swarm Flow:**
```
New sub-provisioner SP confirmed (fanout_count ≥ 10)
  → Each child wallet C enrolled in Helius CANDIDATE webhook
  → C receives ATA setup tx → Helius fires
  → _handle_candidate_webhook() detects: C in _candidate_set, tx involves Token program
  → INSERT wt_pamm_interactions (if pAMM buy detected)
  → score_trader_wallet(C):
      IS round_amount? YES
      IS fresh? YES  
      IS ATA setup in same block? YES
      → classify as TRADER (not CREATOR)
      → INSERT wt_trader_wallets state='TRADER_ACTIVE'
  → detect_trader_swarm(SP, conn): 
      N wallets ATA-setup within 30min → TRADER_SWARM_DEPLOYMENT alert
```

### MVP Implementation Order

Build in this sequence for maximum detection value with minimum new code:

**Phase 1: Close existing gaps (1–2 days)**
1. Add `POST /api/webhook/watchtower-candidate` route — handles the dedicated candidate webhook (distinct from the existing infra webhook handler)
2. Add in-memory `_candidate_set` with 60s refresh — eliminates per-webhook DB lookup
3. Add `_maybe_refresh_dynamic_roles()` — makes newly confirmed sub-provisioners automatically watchable
4. Add `wt_candidate_scores` table and `score_creator_candidate()` function — replace binary detection with scored detection
5. Wire `enroll_candidate_for_monitoring()` to fire on every `wt_staged_wallets` INSERT

**Phase 2: Trader tracking (2–3 days)**
6. Add `wt_trader_wallets` table and schema
7. Add `wt_pamm_interactions` table and schema
8. Add pAMM log parsing in candidate webhook handler — classify buy/sell events from pAMM program mentions
9. Add `detect_trader_swarm()` — fires NEW alert on swarm detection
10. Add `detect_sweep_epoch()` with background sweep detection thread (every 5min)

**Phase 3: Graph and campaigns (2–3 days)**
11. Add `wt_graph_nodes`, `wt_graph_edges`, `wt_campaigns` tables
12. Add `attribute_wallet_to_campaign()` — retroactively attributes all known wallets
13. Add `detect_synchronized_pamm_activity()` — uses wt_pamm_interactions + wt_graph_nodes
14. Add campaign state machine transitions — auto-advance campaign state based on events

**Phase 4: Auto-discovery hardening (1–2 days)**
15. Add `wt_fingerprint_patterns` table — dynamic fingerprint storage extracted from new sub-provisioners
16. Add `backfill_treasury_outflows()` one-shot script
17. Add `check_recursive_sweep()` — auto-discover new profit relays
18. Add external notification channel (Telegram bot or webhook) for high-confidence alerts

### Highest-Value Immediate Features

In priority order:

1. **`POST /api/webhook/watchtower-candidate` + fee account detection** — closes the gap between candidate enrollment and launch confirmation. Currently the system knows wallets are staged but doesn't get real-time notification when they launch.

2. **`score_creator_candidate()` with continuous 0–100 score** — enables ranked alerting. Currently detection is binary. Scored detection catches more wallets earlier and reduces false negatives.

3. **`wt_trader_wallets` + pAMM buy/sell parsing** — currently the system tracks creators but has zero visibility into the trader swarm. Trader activity is the primary on-chain footprint of the operation.

4. **`_maybe_refresh_dynamic_roles()`** — the current system requires manual code updates to add new sub-provisioners to `_WT_INFRA_ROLES`. This makes it self-healing.

5. **Sweep epoch detector** — the profit extraction cycle is the most operationally critical moment. Detecting sweeps in real-time enables correlation of profits back to specific campaign clusters.

### Monitoring Dashboard Recommendations

Extend the existing WATCHTOWER dashboard (already at `/api/wt-staged-wallets` and related routes) with:

```
┌─────────────────────────────────────────────────────┐
│  WATCHTOWER LIVE MONITOR                            │
├────────────────┬────────────────┬───────────────────┤
│  INFRA STATUS  │  CANDIDATES    │  CAMPAIGNS        │
│                │                │                   │
│  TREASURY ●    │  Staged: 342   │  Active: 3        │
│  SIGNALLER ●   │  Enrolled: 127 │  Provisioning: 1  │
│  Sub-Provs: 7  │  Score ≥85: 12 │  Sweeping: 1      │
│  Profit Relays: 6│ Launched: 23 │  Complete: 12     │
├────────────────┴────────────────┴───────────────────┤
│  RECENT ALERTS (last 24h)                           │
│  🔴 PUMPFUN_CREATE_CONFIRMED — 3 launches           │
│  🟠 TRADER_SWARM_DEPLOYMENT — SP=G2Bb, N=847       │
│  🟡 CREATOR_CANDIDATE — score=87 — wallet=8qWL...  │
│  🟡 NEW_SUBPROVISIONER — G2Bb, fanout=4947         │
├─────────────────────────────────────────────────────┤
│  SWEEP ACTIVITY (last 1h)                           │
│  Total swept: 14.3 SOL across 31 wallets           │
│  Destination: G2BbetUg... (sub-provisioner)        │
└─────────────────────────────────────────────────────┘
```

Key metrics to surface:
- `wt_staged_wallets` count by state (bar chart over time)
- `watchtower_events` count by type (last 24h)
- `wt_pamm_interactions` volume (buys vs sells, SOL value)
- `wt_campaigns.total_sol_swept` vs `total_sol_deployed` (ROI)
- Active candidate enrollment count vs webhook limit

### Scaling Considerations

**10x wallet count (5,000+ trader wallets):**
- `_candidate_set` in-memory set remains O(1) lookup — no degradation
- Helius webhook enrollment limit hit at ~10,000. Solution: enroll only score ≥60 creators (hundreds, not thousands); traders are detected via pAMM WebSocket filter not webhook
- `wt_pamm_interactions` grows at ~50 rows/wallet/day → 250,000 rows/day at 5,000 traders. Add `block_time` range partitioning or monthly cleanup of rows older than 30 days
- `detect_synchronized_pamm_activity()` query becomes slow without covering index on `(token_mint, block_time, wallet_address)` — add this index
- `db_write_queue` batch size (`DB_WRITER_MAX_BATCH=300`) may need increase; watch `queue_depths()` metric

**10x sub-provisioner count (70+ sub-provisioners):**
- `_WT_DYNAMIC_SUBPROVS` dict still fits in memory easily
- `watchtower_operator_graph` grows proportionally — the (operator_address, child_address, relationship) primary key prevents bloat
- `detect_sweep_epoch()` runs per-provisioner — at 70 provisioners, run in parallel using ThreadPoolExecutor with max_workers=4

**SQLite write contention:** The single-writer thread pattern (`db_writer.py`) scales to ~500 writes/second burst. At 10x trader count, inbound webhook events during a sweep epoch could exceed this. If `queue_depths()` shows depth > 1000 consistently, consider:
1. Increase `DB_WRITER_MAX_BATCH` to 1000
2. Reduce `DB_WRITER_FLUSH_MS` to 200ms
3. If still insufficient: migrate `wt_pamm_interactions` to a separate SQLite database (write contention is isolated)

---

### Critical Files for Implementation

- `/Users/kevinkeaveney/Dev/claude/flex/src/core/main.py` — webhook handler (`webhook_watchtower()`), schema bootstrap (`_ensure_watchtower_tables()`), `_WT_INFRA_ROLES` dict, all Flask routes; all new webhook routes and schema changes go here
- `/Users/kevinkeaveney/Dev/claude/flex/src/analysis/watchtower_detector.py` — detection rules, `CONFIRMED_FINGERPRINT_BATCHES`, `score_creator_candidate()` goes here alongside existing `detect_watchtower_linkage()`
- `/Users/kevinkeaveney/Dev/claude/flex/src/analysis/watchtower_operator_scanner.py` — `scan_operator_downstream_stateless()`, all RPC-based scanning logic; trader swarm detection and pAMM parsing workers extend this module
- `/Users/kevinkeaveney/Dev/claude/flex/src/creators/helius_watch.py` — `register_creator_address()`, `_webhook_lock` pattern; `enroll_candidate_for_monitoring()` and de-enrollment logic extend this module
- `/Users/kevinkeaveney/Dev/claude/flex/src/core/db_writer.py` — `emit_event()`, monotonic sequence assignment; all new event types use this without modification, but `wt_pamm_interactions` and `wt_trader_wallets` write helpers belong here