# Database Current State: pumpswap_tokens.db

**Generated:** 2026-02-08

---

## Summary

The database contains **172,000+ records** across 36 tables with domain/address tracking data, creator metadata, and CEX wallet information. However, the core **token_analysis** table is empty (0 records).

---

## Record Count Summary

| Table | Records | Purpose |
|-------|---------|---------|
| **creator_receivers** | **53,979** | ⭐ Creator → Recipient transfer tracking |
| **address_domains** | **42,226** | ⭐ Address → Domain mappings (persistent tags) |
| **creator_service_history** | **39,058** | Creator service usage (jitotip, axiom, etc) |
| **address_tags** | **15,626** | Address metadata and domain references |
| **address_classification** | **11,201** | Address classification (CEX, bot, etc) |
| **domain_registry** | **3,994** | ⭐ Domain name registry (SNS domains) |
| **creator_tags** | **2,956** | Creator-specific tags (CEX funding, protocols) |
| **creator_inbound_transfers** | **1,144** | Pre-migration SOL transfers to creators |
| **address_labels** | **97** | Manual address labels |
| **cex_wallets** | **10** | CEX exchange wallets (Binance, Coinbase, Gate.io) |
| **blocksec_batch_log** | **7** | AML batch processing logs |
| **listener_settings** | **3** | Runtime configuration |
| **protocol_fees** | **2** | Pump.fun protocol fee tracking |
| **creator_infra_interactions** | **1** | Creator infrastructure usage |
| **polling_settings** | **1** | Polling configuration |
| **Empty Tables** | **0** | 19 other tables (wallet_cluster_nodes, creator_networks, etc) |

**Total: 172,000+ records**

---

## Key Tables Explained

### 1. **creator_receivers** (53,979 records)

Tracks who receives SOL from each creator (likely trading/transfer activity post-migration).

**Schema:**
```sql
CREATE TABLE creator_receivers (
    creator_address TEXT NOT NULL,
    receiver_address TEXT NOT NULL,
    amount_sol REAL,
    transaction_signature TEXT,
    timestamp INTEGER,
    first_detected_at TEXT,
    receiver_type TEXT,
    receiver_name TEXT,
    PRIMARY KEY (creator_address, receiver_address)
)
```

**Sample:**
- Creator: `5xokXvDCqzadQCoZtsBnkkPLdQjeUoUXCrxqk9AaypPe`
- Receiver: `4uks6GfvhLaqJxWrZZYYxfbU24Kz7318VLXQozKQav6V`
- Amount: 0.05 SOL
- Timestamp: 1738098673 (2026-01-30)

---

### 2. **address_domains** (42,226 records)

Maps wallet addresses to primary domain names (SNS domains or referenced domains).

**Sample:**
```
8FhkMDysBTAQ6cY9nsD8anyiD9s84ortwYAcEpyE9635  →  Flip.gg: Trusted Since 2021🎰-20509.flipgg.sol
```

**Purpose:** Link addresses to their web presence/domain identity for clustering analysis.

---

### 3. **creator_service_history** (39,058 records)

Tracks which services/tools creators use (Jitotip, Axiom, DeBridge, etc).

**Sample:**
```
Creator: 9EtzUVJBCmnQK5D7h3sUoocrKQNFTEmzPLVxzb9kuoUL
Service: uses_jitotip
Amount: 0.006 SOL
Created: 2026-02-05 12:36:13
Tip Percentage: 99.92%
```

---

### 4. **address_tags** (15,626 records)

Persistent address metadata with domain associations.

**Schema:**
```sql
address, tag_type, tag_value, source, first_seen_at
```

**Sample:**
```
Creator123ABC → domain_referenced: alice.sol (tx_extraction, 2025-09-22)
Creator123ABC → domain_referenced: bob.sol (tx_extraction, 2025-09-22)
Creator123ABC → domain_referenced: dex.sol (tx_extraction, 2025-09-22)
```

---

### 5. **address_classification** (11,201 records)

Classification of addresses (CEX, bot, contract, unknown, etc).

**Sample:**
```
Address: 9rtFJK7ivpZYaoKsmKbS4bQzbTK9NkmRkhJk2Jry5CM4
Classification: unknown
Confidence: 0
Last Checked: 2026-02-02
```

---

### 6. **domain_registry** (3,994 records)

Registry of all discovered domain names with metadata.

**Sample:**
```
Domain: vitalik.sol
Type: owned
First Seen: 1769965976
Owner: creator123
Confidence: 1.0
Source: test

Domain: meechiedev.sol
Type: owned
Owner: 9iaawVBEsFG35PSwd4PahwT8fYNQe9XYuRdWm872dUqY
Source: sns_resolution
Confidence: 1.0
```

---

### 7. **creator_tags** (2,956 records)

Creator-specific metadata tags.

**Sample:**
```
Creator: DRS3dm4rGQ4mk5QBDRXZjX82veK7vVqUUDDuHK8RvW2Z
Tag: "Funded by CEX"
Description: "Creator received funding from MEXC exchange wallet"
Added: 2026-01-29

Creator: pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ
Tag: "Protocol Fee"
Description: "Pump.fun protocol fee recipient"
Added: 2026-01-29

Creator: E11VncaS618AYfKQpQg8tbNb1nTW1yVxSihFgo19JEER
Tag: "uses_axiom"
Description: "Creator uses Axiom automation/oracle services"
Added: 2026-01-31
```

---

### 8. **cex_wallets** (10 records) ⭐

Known CEX exchange wallets with high confidence.

**Sample:**
```
Coinbase Hot Wallet:      GeiExVmVuconFfuxtC8mWBbGe1zxvTa3M8fcEcNc9gS (95% confidence)
Binance Hot Wallet:       98rDvzr6D1mtM... (95% confidence)
Gate.io Hot Wallet:       u6PJ8DtQuPFnfmwHbGFULQ4u4EgjDiyYKjVEsynXq2w (95% confidence)
MEXC Hot Wallet:          ASTyfSima4LLAdDgoFGkgqoKowG1LZFDr9fAQrg7iaJZ (95% confidence)
Kraken Hot Wallet:        6LY1JzAFVZsP2a2xKrtU6znQMQ5h4i7tocWdgrkZzkzF (100% confidence)
Binance 2:                5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9 (100% confidence)
Unknown Exchanges:        2 wallets detected automatically
```

---

### 9. **creator_inbound_transfers** (1,144 records)

Pre-migration SOL transfers to creators (preparation funding).

**Sample:**
```
Creator: 4BqQmoJ6gq6QJ1H6ZybmoAQSb2vjfiojV9rYhk2DbFio
Funder: 3dtsLyZd2Tt1AkF2fywHsSxZxSgxACgNs1R7NvjhztxR
Amount: 0.171964799 SOL
Timestamp: 1768726557 (2025-09-14)
Direction: in
Source: intermediary
```

---

### 10. **listener_settings** (3 records)

Runtime configuration for the listener.

```
listen_to_price_updates: true (Enable price tracking)
auto_extract_funding: true (Enable real-time funding extraction)
listen_to_launches: true (Enable token launch detection)
```

---

## Empty Tables (0 records)

These tables are defined but have no data yet:

| Table | Purpose |
|-------|---------|
| token_analysis | ❌ Core token analysis (analysis results, risk scores) |
| creator_funders | Creator funding relationships |
| creator_networks | Coordinated funding groups |
| wallet_cluster_nodes | Network clustering analysis |
| wallet_cluster_edges | Network connections |
| cluster_exit_events | Network exit patterns |
| cluster_fingerprints | Network signatures |
| clustering_alerts | Clustering alerts |
| creator_watch | Creator watchlist |
| creator_state | Creator state machine |
| creator_tx_ledger | Creator transaction ledger |
| creator_portfolio | Creator token holdings |
| creator_outgoing_transfers | Creator SOL outflows |
| creator_sol_transfers | SOL transfer tracking |
| creator_sol_flows | SOL flow analysis |
| creator_recipients_unified | Unified recipient tracking |
| network_coordinators | Coordinated actor tracking |
| recipient_cross_references | Cross-reference tracking |
| blocksec_aml_cache | AML cache (API results) |
| polling_settings | Polling configuration |

---

## Data Characteristics

### Time Range
- **Earliest:** 2025-09-13 (Unix: 1768726557)
- **Latest:** 2026-02-05 (Unix: 1744041358)
- **Coverage:** ~5 months of data

### Domain Data
- **Total Domains:** 3,994 unique
- **Associated Addresses:** 42,226 mappings
- **Top Domain:** `meechiedev.sol` (SNS resolution)
- **Domain Types:** SNS domains (`.sol`), referenced domains in transactions

### Creator Data
- **Creators with Service History:** 2,956
- **Most Common Service:** Jitotip (MEV/fee tipping)
- **Other Services:** Axiom (automation), DeBridge (bridging)
- **CEX-Funded Creators:** At least 1 confirmed (DRS3dm4... funded by MEXC)

### CEX Integration
- **Known Exchanges:** Coinbase, Binance (2 wallets), Gate.io, MEXC, Kraken
- **Automated Detection:** 2 additional unknown exchange wallets detected
- **Confidence Levels:** 95-100%
- **Discovery Method:** Solscan labels + manual/automatic detection

---

## Analysis Insights

### What We Know
1. ✅ **Address → Domain Mapping:** 42,226 records
2. ✅ **Creator Service Usage:** 39,058 records
3. ✅ **Creator Receivers:** 53,979 transfer records
4. ✅ **Pre-Migration Funding:** 1,144 inbound transfer records
5. ✅ **CEX Wallet Registry:** 10 known exchanges
6. ✅ **Domain Registry:** 3,994 unique domains

### What's Missing
1. ❌ **Token Analysis:** 0 records (no risk scores, rug detection)
2. ❌ **Creator Networks:** 0 records (no coordination analysis)
3. ❌ **Wallet Clustering:** 0 records (no network graphs)
4. ❌ **Funding Relationships:** 0 records (creator_funders table empty)

---

## Database Usage

### Current Functions
The database is primarily serving as a **metadata and address tagging system**:
- Domain name resolution and mapping
- Address classification and labeling
- Creator service tracking
- CEX wallet identification
- Creator receiver tracking

### Potential Use Cases
Based on the data available:
1. **Domain Clustering:** Link addresses through shared domains
2. **Service Pattern Analysis:** Find creators using similar tools
3. **CEX Funding Detection:** Identify creators funded by known exchanges
4. **Address Timeline:** Track creation dates and service adoption
5. **Network Visualization:** Map creator → receiver relationships

---

## Configuration

**Database File:** `/Users/kevinkeaveney/Dev/claude/flex/pumpswap_tokens.db`

**Size:** ~30-50 MB (estimated)

**Connection:** SQLite 3 (local file)

**Listener Settings:**
```json
{
  "listen_to_price_updates": true,
  "auto_extract_funding": true,
  "listen_to_launches": true
}
```

---

## Next Steps

To populate the empty tables, the system would need to:

1. **Run token analysis:** Fetch and validate Pump.Fun CREATE transactions
2. **Analyze funding chains:** Populate creator_funders with pre-migration SOL data
3. **Cluster creators:** Build wallet_cluster_nodes from recipient relationships
4. **Score risk:** Populate token_analysis with rug probability and risk levels
5. **Coordinate detection:** Populate creator_networks with suspicious funding groups

---

**Generated:** 2026-02-08
**Database Status:** ✅ Active (metadata/tagging layer)
**Analysis Status:** ⏳ Pending (core tables empty)
