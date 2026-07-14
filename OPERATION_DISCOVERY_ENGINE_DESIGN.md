# Operation Discovery Engine — Audit & Design

**Date:** 2026-06-03
**Purpose:** Explain why Operation Alpha was invisible to automated discovery, and design an engine that discovers WATCHTOWER / Alpha / future operators automatically.
**Status:** DESIGN ONLY — no implementation.

---

## Part 1: Why Alpha Was Invisible — Stage-by-Stage Audit

### The Core Finding

**All the data needed to discover Alpha existed from day one (Apr 21, 2026).** The 29 creator→fanout funding edges — each carrying the `x.10203928` fingerprint amount — were recorded in `creator_funders` the moment the tokens launched. The grouping signal sat unused for 6 weeks.

The only data that *didn't* exist until manual investigation (Jun 2) was the **hub→tier-2 edges** — but those were never required to group the tokens. The fingerprint amount + shared timing alone were sufficient.

### Stage 1 — Tokens

| Table | Contained Alpha tokens? |
|-------|------------------------|
| `watch_candidate_tokens` | ✅ 29 (as individual WATCH tokens) |
| `token_prediction_scores` | ✅ 29 (scored WATCH) |
| `token_analysis` | ✅ 29 (migration data) |
| `watchtower_dormant_seen` | ✅ 29 (dormant scanner saw them) |
| `wt_swarm_candidates` | ❌ 0 |
| `wt_swarm_corridors` | ❌ 0 |

**Data existed.** The tokens were known and scored. But each was processed as an isolated row — no edge connected token A to token B.

### Stage 2 — Creators

| Table | Contained Alpha creators? |
|-------|---------------------------|
| `creator_funders` (as creator) | ✅ 29 |
| `watchtower_dormant_seen` | ✅ 29 |
| `wt_graph_nodes` | ❌ 0 |
| `creator_funding_graph` | ❌ 0 |

**Critical gap:** the 29 creators were NEVER added to `wt_graph_nodes`. The graph that the clustering engine reads from had zero Alpha nodes. The creators existed in `creator_funders` but were never promoted into the operator graph.

### Stage 3 — Fanout Wallets (direct funders)

| Table | Contained Alpha fanout wallets? |
|-------|--------------------------------|
| `creator_funders` (as funder) | ✅ 33 |
| `wt_graph_nodes` | ❌ 0 |
| `wt_relay_counterparties` | ❌ 0 |

**The fanout edges existed** (`creator ← fanout, x.10203928 SOL`) but lived only in `creator_funders`. No graph table, no relay table, no edge table referenced them.

### Stage 4 — Capital Hub (`4LpEjcq3`)

| Table | Contained the hub? |
|-------|--------------------|
| `creator_funders` (as funder of tier-2) | ✅ 31 — **but created Jun 2 (manual)** |
| `wt_graph_nodes` | ❌ 0 |
| `wt_sub_provisioners` | ❌ 0 |
| `wt_relay_counterparties` | ❌ 0 |
| `wt_webhook_hits` | ❌ 0 |

The hub was completely invisible until manual RPC tracing. **But the hub was never needed to discover the operation** — see Part 2.

---

## Part 2: The Three Signals That Should Have Grouped Alpha Automatically

Without ever scanning the hub, three signals already in the database link the 29 tokens:

| Signal | Status in DB | Discovery Value |
|--------|--------------|-----------------|
| **A. Shared fingerprint amount** | ✅ 29/29 share `x.10203928` (since Apr 21) | STRONG — exact-match provisioning amount |
| **B. Shared timing window** | ✅ All 29 launched in a 6.9-day window | STRONG — tight coordinated burst |
| **C. Shared profit collector** | ⚠️ Not in `creator_outgoing_transfers` (sweep was direct on-chain, not captured) | Would need sweep-trace |

**Signals A + B alone are sufficient to form the cluster.** 29 fresh creators, each funded with the exact same unusual amount (`2.10203928` SOL), all within a 7-day window — that is a coordinated operation by any reasonable clustering threshold.

---

## Part 3: Why The Clustering Engine Failed

### Q1: If Alpha existed in the database, why did clustering fail to identify it?

The existing clustering engine (`_assign_extraction_clusters`, `_build_watch_clusters`) groups on **two signals only**:
- `shared_recipient` (creator_outgoing_transfers to common wallet)
- `shared_funder` (creator_funders with a funder that funds 2+ creators)

Alpha defeats **both**:
- **No shared recipient:** Alpha's profit sweep was a single on-chain event not captured in `creator_outgoing_transfers`, so no shared-recipient edge formed.
- **No shared funder:** Alpha's funders are **single-use fanout wallets** — each funds exactly ONE creator. The `HAVING COUNT(DISTINCT creator_address) >= 2` filter requires a funder to fund 2+ creators. Single-use fanout wallets NEVER trigger it.

This is the architectural blind spot: **the anti-tracing technique (single-use fanout wallets) is specifically designed to defeat shared-funder clustering.** The clustering engine only looks one hop up. The shared parent is two hops up.

### Q2: Which missing graph edges prevented Alpha from forming a cluster?

```
WHAT EXISTED:                          WHAT WAS MISSING:
creator_1 ← fanout_1 (x.10203928)      fanout_1 → 4LpEjcq3  (hop-2 edge)
creator_2 ← fanout_2 (x.10203928)      fanout_2 → 4LpEjcq3  (hop-2 edge)
...                                     ...
creator_29 ← fanout_29 (x.10203928)    fanout_29 → 4LpEjcq3 (hop-2 edge)
```

The hop-1 edges (creator ← fanout) existed. The hop-2 edges (fanout → hub) did not. Without the hop-2 edge, there is no single node connecting all 29 creators — they look like 29 unrelated tokens with 29 unrelated funders.

**But the missing edge was unnecessary** — the fingerprint amount on the hop-1 edges is itself a grouping key. The engine just never used amount as a clustering dimension.

### Q3: What new tables / relationships / logic are required?

See Part 4 (the engine design).

### Q4: Would the revised system have discovered Alpha automatically as UNKNOWN_OPERATION_001?

**Yes.** A clustering pass over `creator_funders` grouping by `(fingerprint_amount, time_window)` would have grouped the 29 tokens on Apr 28 — the day the last one launched — into a single operation candidate with 29 members, before any human looked at it. It would have been auto-named `UNKNOWN_OPERATION_NNN` and surfaced on the page awaiting human naming/validation.

---

## Part 4: Operation Discovery Engine — Design

### Paradigm Shift

```
CURRENT:  TOKEN → classified_as (WATCHTOWER | WATCH_LIKE_NEW_OP | UNKNOWN)
DESIRED:  TOKEN → operation_id → operator_identity
```

The **operation** becomes the primary object. Tokens are members of operations. Operations are classified, not tokens.

### New Schema

```sql
CREATE TABLE wt_operations (
    operation_id        INTEGER PRIMARY KEY,
    auto_name           TEXT,          -- 'UNKNOWN_OPERATION_001'
    human_name          TEXT,          -- 'OPERATION_ALPHA' (set by human)
    operator_identity   TEXT,          -- 'WATCHTOWER' | 'ALPHA' | NULL (unknown)
    state               TEXT,          -- DISCOVERED | NAMED | CONFIRMED | DORMANT
    token_count         INTEGER,
    creator_count       INTEGER,
    discovery_signals   TEXT,          -- JSON: which signals grouped this op
    confidence          REAL,
    capital_hub         TEXT,          -- discovered hub address (if traced)
    profit_collector    TEXT,
    first_token_at      INTEGER,
    last_token_at       INTEGER,
    discovered_at       INTEGER,
    discovered_by       TEXT           -- 'fingerprint_timing' | 'shared_hub' | ...
);

CREATE TABLE wt_operation_members (
    operation_id        INTEGER,
    token_mint          TEXT,
    creator_wallet      TEXT,
    funding_amount      REAL,
    fanout_wallet       TEXT,
    join_signal         TEXT,          -- why this token joined
    PRIMARY KEY (operation_id, token_mint)
);

CREATE TABLE wt_operation_signals (
    -- The grouping keys: amount fingerprints, hubs, collectors, timing buckets
    signal_id           INTEGER PRIMARY KEY,
    signal_type         TEXT,          -- 'fingerprint_amount' | 'capital_hub' | 'profit_collector' | 'timing_window' | 'shared_relay'
    signal_value        TEXT,          -- '2.10203928' | hub_address | ...
    operation_id        INTEGER,
    member_count        INTEGER
);
```

### Discovery Algorithm

The engine runs as a periodic pass (after `_build_watch_candidates`). It groups WATCH-eligible tokens into operations using **multiple independent grouping dimensions**, then merges overlapping groups.

**Dimension 1 — Fingerprint Amount Grouping**
```
For each distinct funding amount that is "unusual" (high decimal precision,
appears across N≥3 fresh creators):
  → group all tokens whose creator was funded that exact amount
  → this catches Alpha (x.10203928) AND WATCHTOWER's provisioning amounts
```

**Dimension 2 — Capital-Hub Grouping (multi-hop)**
```
For each token, trace creator funding up to 4 hops (BFS).
Bucket tokens by the deepest non-disposable wallet reached (the hub).
  → groups single-use-fanout operations that defeat 1-hop shared-funder clustering
  → REQUIRES on-demand RPC backfill of hop-2/hop-3 edges (currently missing)
```

**Dimension 3 — Profit-Collector Grouping**
```
For each token, trace the creator's outbound sweep destination.
Bucket tokens by shared sweep destination.
  → groups operations by where profits aggregate
```

**Dimension 4 — Timing-Window Grouping**
```
Slide a 7-day window over token launch timestamps.
Within each window, sub-group by fingerprint amount.
  → confirms coordination (random tokens don't share amount AND window)
```

**Dimension 5 — Shared-Relay Grouping (existing)**
```
Keep the current shared_recipient / shared_funder logic for ops that
don't use single-use fanout.
```

**Merge step (union-find):**
```
Build a graph where tokens are nodes and a shared signal (any dimension)
is an edge. Connected components = operations.
A token grouped by BOTH fingerprint AND timing AND hub is high-confidence.
A token grouped by only one weak signal is low-confidence.
```

### Operator Identity Assignment (separate from discovery)

Once an operation is discovered, classify its **operator**:
```
WATCHTOWER  : operation's capital hub / profit collector traces to known WT infra
ALPHA       : operation matches the Alpha fingerprint (see ALPHA_SIMILARITY_ANALYSIS)
UNKNOWN     : coordinated but no known operator → keep auto_name, await human naming
```

This is where the existing `_WT_INFRA_ROLES`, Alpha-family hub set, and funding-origin logic plug in — but now applied at the **operation** level, not per-token.

### The Missing Edge Backfill (the one real data gap)

For Dimension 2 (hub grouping) to work automatically, the hop-2/hop-3 funding edges must exist. Currently they're only created by manual RPC scans. The fix:
```
A background worker that, for any fresh creator funded with a fingerprint
amount by a single-use wallet, automatically RPC-traces that funder upward
2-3 hops and writes the edges to creator_funders.
```
This is exactly the manual process used in the Alpha investigation, automated and triggered on token ingestion.

---

## Part 5: Desired Output

The Operators page changes from:
```
WATCHTOWER = 44
WATCH_LIKE_NEW_OP = 360
```
to:
```
WATCHTOWER
  ├─ Campaign 7UyCwmSUcG7  (20 tokens)
  ├─ Campaign 4r65bgGW     (3 tokens)
  └─ ... (per sub-prov hub)

ALPHA FAMILY
  ├─ Operation Alpha (4LpEjcq3)   29 tokens   DORMANT
  ├─ Campaign BcSScwFvv           2 tokens    DORMANT
  └─ Campaign 6FdUQoBL            3 tokens    DORMANT

UNKNOWN_OPERATION_003   17 tokens   ACTIVE   ← awaiting human naming
UNKNOWN_OPERATION_004    9 tokens   ACTIVE
```

Each operation is a row. Humans name and validate; the engine discovers.

---

## Part 6: Implementation Plan (phased)

**Phase 1 — Operation schema + discovery from existing signals**
- Create `wt_operations`, `wt_operation_members`, `wt_operation_signals`
- Implement Dimensions 1 (fingerprint), 4 (timing), 5 (shared-relay) — all use data already in `creator_funders` / `creator_outgoing_transfers`, no RPC needed
- Run union-find merge → auto-create `UNKNOWN_OPERATION_NNN` rows
- **This alone would have discovered Alpha** (fingerprint + timing)

**Phase 2 — Multi-hop edge backfill worker**
- Background worker that traces fingerprint-funded creators' funders 2-3 hops up
- Enables Dimension 2 (hub grouping) automatically
- Auto-promotes discovered hubs to `wt_operation_signals`

**Phase 3 — Operator identity layer**
- Apply `_WT_INFRA_ROLES`, Alpha-family set, funding-origin at operation level
- Auto-assign WATCHTOWER / ALPHA / UNKNOWN operator identity per operation
- Sweep-destination tracing for Dimension 3

**Phase 4 — Operators page rebuild**
- Page lists operations, not tokens
- Each operation expandable to its member tokens
- Human naming/validation UI (rename UNKNOWN_OPERATION_NNN → confirmed name)

**Phase 5 — Continuous discovery**
- Discovery pass runs on the 5-min pipeline cadence
- New coordinated operations surface as `UNKNOWN_OPERATION_NNN` automatically
- Alert when a new operation crosses a size/confidence threshold

---

## Answers to Specific Questions

1. **Why did clustering fail?** It only checks 1-hop shared-funder / shared-recipient. Alpha uses single-use fanout wallets (each funds 1 creator) which defeat the `COUNT >= 2` shared-funder filter by design. The shared parent is 2 hops up.

2. **Which missing edges?** The fanout→hub (hop-2) edges. But more fundamentally, the engine never used **funding amount** as a clustering dimension — and the fingerprint amount alone would have grouped Alpha without any hop-2 edge.

3. **What's required?** Operation-centric schema (`wt_operations`), multi-dimensional clustering (fingerprint amount + timing + multi-hop hub + profit collector), a union-find merge, and a multi-hop edge backfill worker.

4. **Would Alpha have appeared as UNKNOWN_OPERATION_001?** Yes — fingerprint-amount + timing-window grouping (both from data present since Apr 21) would have auto-formed the 29-token cluster on Apr 28, before any human investigation.

5. **Implementation plan:** 5 phases above. Phase 1 (fingerprint + timing discovery, no RPC) is the minimum viable engine and would retroactively discover Alpha immediately.
