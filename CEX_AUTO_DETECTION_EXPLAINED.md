# CEX Auto-Detection System - Complete Explanation

## Overview

The system automatically detects and classifies Centralized Exchange (CEX) wallets using a **3-Layer Confidence Scoring System**. When addresses score high enough, they're automatically added to the `cex_wallets` table.

---

## How It Works (Layer 3 Auto-Detection)

### Layer 3: Transaction Heuristics

When an address doesn't have clear Solscan labels or SNS domains, the system analyzes transaction patterns to detect CEX wallets.

**Key Heuristics Analyzed:**

```
1. Transfer Volume & Consistency
   - High volume addresses (10K+ transactions)
   - Regular, consistent inflows/outflows
   → Indicates automated exchange operation

2. Multiple Token Transfers
   - Handles many different token types
   - Not holding single tokens long-term
   → Typical exchange behavior (deposits → trading → withdrawals)

3. Dust Filtering Patterns
   - Creates small placeholder transfers
   - Initiates transactions frequently
   → Exchange fee/operational pattern

4. Relationship Network
   - Connects to known exchange wallets
   - Acts as hub between multiple addresses
   → Central exchange infrastructure
```

---

## Confidence Scoring System

### Score Calculation

```
Confidence Score (0-150):

Layer 1: Solscan Labels (Direct Signal)
├─ +80 points: Direct CEX label from Solscan
│  (e.g., "Binance Hot Wallet", "Coinbase Custody")
│
Layer 2: SNS Primary Domains (Supporting)
├─ +15 points: Exchange-owned domain
│  (e.g., "binance.sol", "coinbase.sol")
│
Layer 3: Transaction Heuristics (Patterns)
└─ +0-30 points: Based on transaction behavior
   ├─ +10: High volume & consistency
   ├─ +8: Multiple token handling
   ├─ +7: Dust filtering patterns
   └─ +5: Network relationship to known CEX
```

### Classification Thresholds

```
Score ≥ 100  → cex_confirmed   (Automatically added to cex_wallets table)
Score 60-99  → cex_likely      (Flagged for manual review)
Score 30-59  → cex_possible    (Informational only)
Score < 30   → unknown         (Not classified as CEX)
```

---

## Example Classifications

### Example 1: Binance Hot Wallet
```
Address: 8iBa3q2N4F7h9K2vYwK9Q4kR5jL5xN2jB1fT2wX3yZ

Score Breakdown:
├─ Solscan Label "Binance Hot Wallet" → +80 points
├─ SNS Domain "binance.sol" → +15 points
└─ High transaction volume → +5 points
   TOTAL: 100 points → cex_confirmed ✓

ACTION: Automatically added to cex_wallets table
LOG: [AUTO-CEX] Classified as cex_confirmed (confidence: 100)
```

### Example 2: Unknown Exchange Via Heuristics
```
Address: 62qc2CNXwrYqQScmXxX...

Score Breakdown:
├─ No Solscan label → 0 points
├─ No SNS domain → 0 points
└─ Transaction patterns:
   ├─ 145 daily transactions → +8 points
   ├─ 8 different token types → +10 points
   ├─ Dust filtering detected → +7 points
   └─ Connected to Binance → +5 points
   TOTAL: 30 points → cex_possible ⚠

ACTION: Informational - Not auto-added (below 100 threshold)
LOG: [AUTO-CEX] cex_possible (confidence: 30) - manual review suggested
```

### Example 3: False Positive Prevention
```
Address: 5omhasVqrA2GB7i78yS1AVfQK3GvpzuP32VAUVHp3dkN (Creator)

Score Breakdown:
├─ No Solscan CEX label → 0 points
├─ No SNS domain → 0 points
└─ Transaction patterns:
   ├─ Only 5 outgoing transfers → 0 points
   ├─ Single token (their launch) → 0 points
   └─ No dust patterns → 0 points
   TOTAL: 0 points → unknown ✓

ACTION: Not classified as CEX
LOG: [AUTO-CEX] unknown - creator account
```

---

## The Auto-Detection Workflow

### Step 1: Extraction Completes
```
extract_for_creator() finishes processing a creator
    ↓
Identifies all funders & recipients encountered
    ↓
Non-blocking async task triggered: _run_automatic_cex_detection()
```

### Step 2: Classification Engine Runs
```
For each new address (up to 200):
    ↓
classify_address(address):
    ├─ Layer 1: Query Solscan API for labels
    ├─ Layer 2: Query SNS domains (Bonfida API)
    └─ Layer 3: Analyze transaction patterns
         ↓
         Calculate confidence_score (0-150)
         ↓
         Determine classification (confirmed/likely/possible/unknown)
```

### Step 3: High-Confidence Addresses Saved
```
If classification == cex_confirmed (score ≥ 100):
    ↓
    INSERT INTO cex_wallets:
    ├─ cex_address
    ├─ exchange_name (extracted from label)
    ├─ wallet_type (Hot Wallet, Cold Wallet, etc.)
    ├─ confidence_level (100+)
    ├─ discovery_source: "automatic"
    └─ is_active: 1
    ↓
    Log: [AUTO-CEX] Added {exchange_name} to cex_wallets
```

### Step 4: Future Recognition
```
Next extraction run:
    When recipient/funder = known CEX address:
    ↓
    Immediate Layer 2 lookup:
    SELECT FROM cex_wallets WHERE cex_address = ?
    ↓
    Found! Log CEX detection:
    [FUNDING] 🏛️ CEX FUNDER DETECTED: Binance Hot Wallet → creator
```

---

## Real-Time Integration

### In `realtime_creator_funding_extractor.py`

**After extraction completes (line 1236-1239):**
```python
# Trigger automatic CEX detection asynchronously (non-blocking)
if funders:
    asyncio.create_task(self._run_automatic_cex_detection())
```

**This calls (line 1255-1281):**
```python
async def _run_automatic_cex_detection(self):
    """Run automatic CEX detection on classified funding addresses"""

    result = await classify_addresses_from_funding(max_addresses=200)

    if result.get("error"):
        print(f"[AUTO-CEX] Error: {result.get('error')}")
        return

    classified = result.get("classified", 0)
    confirmed = result.get("confirmed", 0)   # These were score ≥ 100
    likely = result.get("likely", 0)         # These were score 60-99

    if classified > 0:
        print(f"[AUTO-CEX] Classification complete: {classified} classified, " +
              f"{confirmed} confirmed, {likely} likely")
```

---

## Database Impact

### What Gets Saved
```sql
-- When a new address scores ≥ 100 (cex_confirmed):
INSERT INTO cex_wallets (
    cex_address,
    exchange_name,      -- "Binance", "Coinbase", "MEXC", etc.
    wallet_type,        -- "Hot Wallet", "Cold Wallet", "Custody", etc.
    confidence_level,   -- 100-150 (score)
    discovered_date,    -- CURRENT_TIMESTAMP
    discovery_source,   -- "automatic"
    notes,              -- Score breakdown & reasons
    is_active           -- 1 (enabled)
);

-- Example:
INSERT INTO cex_wallets VALUES (
    '8iBa3q2N4F7h9K2vYwK9Q4kR5jL5xN2jB1fT2wX3yZ',
    'Binance',
    'Hot Wallet',
    150,
    CURRENT_TIMESTAMP,
    'automatic',
    'Solscan label: Binance Hot Wallet (80) + SNS domain (15) + volume (5)',
    1
);
```

### What Gets Queried Later
```python
# During next extraction, when saving a funder/recipient:

# Layer 1: Check built-in mapping
if address in CEX_ACCOUNTS:
    → cex_exchange = "Binance"

# Layer 2: Check database (includes auto-discovered ones)
SELECT exchange_name, wallet_type FROM cex_wallets
WHERE cex_address = ? AND is_active = 1

# Result: Finds auto-detected CEX from previous run!
```

---

## Heuristics Detail (Layer 3)

### Transaction Pattern Analysis

```python
async def _score_cex_heuristics(address):
    """Score an address based on transaction patterns"""

    heuristic_score = 0
    patterns = []

    # Pattern 1: High Volume Consistency
    SELECT COUNT(*) FROM transactions WHERE account = ?
    If count > 10,000:
        heuristic_score += 10
        patterns.append("High transaction volume")

    # Pattern 2: Multiple Token Handling
    SELECT COUNT(DISTINCT mint) FROM token_transfers WHERE account = ?
    If count > 50:
        heuristic_score += 8
        patterns.append("Handles many token types")

    # Pattern 3: Dust/Fee Pattern
    SELECT COUNT(*) FROM transfers WHERE amount < 0.001
    If percentage > 10%:
        heuristic_score += 7
        patterns.append("Dust filtering pattern detected")

    # Pattern 4: Hub Topology
    SELECT COUNT(DISTINCT connected_account)
    If count > 100:
        heuristic_score += 5
        patterns.append("Central hub in transaction network")

    return {
        'score': heuristic_score,
        'pattern': ', '.join(patterns)
    }
```

---

## False Positive Prevention

The system explicitly excludes:

1. **Infrastructure Accounts** (line 88-99 in automatic_cex_detection.py)
   ```python
   if is_infrastructure_account(address):
       return UNKNOWN  # Skip deBridge, Jupiter, System Program, etc.
   ```

2. **Already Known CEX** (line 101-112)
   ```python
   if is_cex_account(address):
       return CONFIRMED  # Score: 150 (highest confidence)
   ```

3. **Creator Wallets** (implied by score < 30)
   - Limited transaction patterns
   - Few transfers
   - Single token focus
   - Won't reach 100 point threshold

---

## Log Output Examples

### When Classification Runs
```
[AUTO-CEX] Classification complete: 5 classified, 2 confirmed, 1 likely
(from 10 addresses analyzed)

[AUTO-CEX] New CEX detected: Binance (confidence: 100) - added to cex_wallets
[AUTO-CEX] Possible CEX: 62qc2CNX... (confidence: 45) - manual review suggested
```

### When Classified Address Is Encountered
```
[FUNDING] 🏛️ CEX FUNDER DETECTED: Binance Hot Wallet → creator
[FUNDING] 💸 OUTGOING TO CEX (AUTO-DETECTED): creator → Binance (confidence: 100)
```

---

## Summary

**Layer 3 Auto-Detection (Heuristics):**

✅ Analyzes transaction patterns (volume, diversity, topology)
✅ Adds 0-30 points based on CEX-like behavior
✅ When combined with Layers 1 & 2, can reach 100-point threshold
✅ Automatically inserts qualified addresses into `cex_wallets` table
✅ Runs non-blocking after extraction completes
✅ Prevents false positives by excluding infrastructure & creators

**Key Scores for Auto-Add:**
- Solscan CEX label alone: 80 + SNS (15) = 95 (needs 5 more)
- No labels but strong heuristics: 30 from patterns (not enough)
- Combination: 80 + 15 + 10 = 105 ✓ (auto-adds)

The system is **self-improving**: Each run finds and auto-adds new CEX wallets, which improves detection for future runs.
