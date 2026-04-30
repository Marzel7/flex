# Creator Intelligence System

How Flex assigns the **MF tag**, **Named Networks**, **Wallet Clusters**, and **Farm Clusters** to creators.
All of these signals derive from a creator's pre-launch funding history.

---

## Foundation: Pre-Launch Funding Extraction

Everything starts with the `creator_funders` table. Before a token launches on pump.fun, the
creator wallet receives SOL from one or more funding wallets. Flex traces these inbound transfers
back to their original sender.

### How funding is extracted

When a token migrates to PumpSwap, Flex enqueues the creator in `creator_funding_queue`. A
background worker replays the on-chain transaction history for the creator wallet, walking backwards
from the token launch to find the original funding sources.

Each funding relationship is stored as:
```
creator_funders(creator_address, funder_address, amount_sol, is_cex, cex_exchange, source_type)
```

`is_cex` is set to `1` when the funder address is a known CEX hot wallet (Binance, Coinbase, OKX,
etc.). CEX-sourced funding is excluded from all coordination signals — receiving SOL from Binance
just means the creator bought SOL on an exchange.

**Coverage today:** ~2,985 creators have extracted funding data out of ~24,892 tracked creators.
Coverage improves continuously as the queue processes.

---

## Signal 1: MF Tag (Multi-Funder)

**Displayed as:** `MF` tag on the main dashboard creator column.

### What it means
The creator's funding wallet(s) have also funded other creators. This indicates shared
infrastructure — the same operator is seeding multiple wallets.

### How it is assigned

1. The `coordinated_funders` table holds funder addresses that appear in `creator_funders` for
   more than one creator (excluding CEX addresses).

2. At enrichment time, Flex queries:
   ```sql
   SELECT COUNT(DISTINCT cf.funder_address)
   FROM creator_funders cf
   WHERE cf.creator_address = <creator>
     AND cf.is_cex = 0
     AND cf.funder_address IN (SELECT funder_address FROM coordinated_funders)
   ```

3. If the count is ≥ 1, the creator receives the `Multi-Funder` tag, displayed as **MF**.

### What it does not mean
MF is a weak signal on its own. A funder appearing across two creators could be coincidence —
a friend funding two different people, or a small operator with two projects. The stronger signals
are Named Networks and Clusters, which require higher thresholds.

---

## Signal 2: Named Networks (Network_N)

**Displayed as:** `Network_140`, `Network_5`, etc. in the network column. Clicking navigates to
the full network page.

### What it means
A single non-CEX funder wallet has funded ≥ 2 creators. Each unique qualifying funder defines
one named network. All creators funded by that wallet are members of the same network.

### How it is built — `NetworkMembershipBuilder` (runs every 30 min)

1. **Qualify funders** — query `creator_funders`, group by `funder_address`, keep only those
   that funded ≥ 2 distinct creators with `is_cex = 0`.

2. **Exclude infrastructure** — remove addresses in the CEX/infra static registry
   (`build_excluded_set`). This catches addresses not already flagged by `is_cex`.

3. **Assign stable names** — each qualifying funder gets a permanent `Network_N` identifier
   stored in `funder_network_map`. Names are assigned once and never change even as the
   network grows.

4. **Write memberships** — for each qualifying funder, every creator they funded is written
   into `network_membership(network_name, creator_address)`.

5. **`networks_release` summary table** — `NetworksReleaseBuilder` aggregates per-network
   stats: member count, total SOL, CEX/infra funder flags, risk level, stability state.

### Key thresholds
| Parameter | Value |
|---|---|
| Minimum creators per funder | 2 |
| CEX funders | excluded |
| Infrastructure addresses | excluded |
| Name stability | permanent once assigned |

### Today's coverage
738 network memberships across 474 creators.

---

## Signal 3: Wallet Clusters

**Displayed as:** `Nw` size tag (e.g. `12w`) on the creator row in the main dashboard.

### What it means
A single funder wallet has funded 3+ creators with consistent SOL amounts (0.5–10 SOL per
transfer) over 2+ active days. This is a stronger signal than a Named Network because it adds
temporal consistency and transfer amount regularity checks.

### How it is built — `WalletClusteringEngine` (runs every 30 min)

Detects funder wallets matching all of:
- Funded ≥ 3 distinct creators (`min_creators = 3`)
- Transfer amounts between 0.5 and 10 SOL
- Active across ≥ 2 different days

Each qualifying funder becomes a `wallet_clusters` row containing the JSON array of creator
addresses it funded.

### Confidence score (0–100)

| Component | Max points | Condition |
|---|---|---|
| Creator count | 25 | ≥10→25, ≥5→18, ≥3→10 |
| Transfer consistency (low stddev) | 25 | stddev<1→25, <2→18, <3→10 |
| Duration | 25 | ≥7 days→25, ≥3→18, ≥1→10 |
| Activity (transfer count) | 25 | ≥20→25, ≥10→18, ≥5→10 |

Burst detection is also run: if 2+ creators were funded within a 1-hour window, `has_burst = 1`.

**Today:** 1,558 wallet clusters detected.

---

## Signal 4: Farm Clusters (FC#N)

**Displayed as:** `FC#12` badge in the cluster column, colour-coded by risk level
(red = HIGH/CRITICAL, amber = MEDIUM, green = LOW).

### What it means
A graph-level cluster of wallets where multiple funders and multiple creators are densely
interconnected — not just one funder funding many creators, but a web of funders and creators
all funding/being-funded by each other. This is the strongest coordination signal.

### How it is built — `GraphDevFarmDetectionEngine` (runs every 30 min)

1. Builds a directed graph from `transfer_index` (raw on-chain SOL transfers), with edges
   weighted by transfer count and volume.

2. Runs community detection (networkx) to find dense subgraphs where nodes are a mix of
   funders and creators.

3. Filters clusters to those with ≥ 2 funders **and** ≥ 3 creators.

4. Computes a **farm risk score (0–100)**:

   | Component | Max | Formula |
   |---|---|---|
   | Funder count | 25 | `min(funder_count / 5 * 25, 25)` |
   | Creator count | 25 | `min(creator_count / 10 * 25, 25)` |
   | Graph density | 30 | `density * 30` |
   | Classification confidence | 20 | `confidence * 20` |

5. Risk level classification:
   | Score | Level |
   |---|---|
   | ≥ 80 | CRITICAL |
   | ≥ 60 | HIGH |
   | ≥ 40 | MEDIUM |
   | < 40 | LOW |

**Today:** 11 farm clusters detected.

---

## How the signals relate

```
creator_funders (raw funding history)
        │
        ├─── ≥1 non-CEX funder also funds others   →  MF tag
        │
        ├─── ≥1 funder funds ≥2 creators            →  Named Network (Network_N)
        │         (stable name, clickable, navigate to /networks?network=Network_N)
        │
        ├─── ≥1 funder funds ≥3 creators            →  Wallet Cluster (Nw size tag)
        │         + consistent amounts + 2+ days
        │         + confidence score 0-100
        │
        └─── Dense funder-creator graph subgraph     →  Farm Cluster (FC#N)
                  + ≥2 funders + ≥3 creators
                  + risk score + level (LOW→CRITICAL)
```

A creator can simultaneously have all four signals — MF tag because their funder funded another
creator, Network_140 because that funder has a stable name, `12w` because the wallet cluster has
12 creators, and FC#3 because those wallets form a dense farm graph.

---

## Funding queue and coverage

Funding extraction is not instant. When a token migrates, the creator is enqueued in
`creator_funding_queue`. Until extraction completes:

- Creators with no extracted data and a recently migrated token show a **`? Funders`** tag
  (extraction not yet run)
- Creators queued but not yet processed show **`⏳ Funders`** (extraction pending)

Coverage can be monitored at `/funding-queue`.

---

## Data freshness

All four signals are rebuilt every 30 minutes by `scripts/run_graph_analyzers.py` via cron:

```
*/30 * * * * cd /path/to/flex && python3 scripts/run_graph_analyzers.py
```

Run order within each cycle:
1. WalletClusteringEngine
2. DevReputationUpdater
3. FunderOverlapAnalyzer
4. GraphDevFarmDetectionEngine
5. CoordinatedEdgesBuilder
6. C2CEdgeBuilder
7. **NetworkMembershipBuilder** ← assigns Named Networks
8. **NetworksReleaseBuilder** ← builds summary stats

A lock file (`logs/graph_analyzers.lock`) prevents overlapping runs.
