# RPC 2/3-Hop Analysis: WATCH Token Creator Signal Token Linkage

**Date:** 2026-06-02  
**Objective:** Identify WATCHTOWER coordination fingerprints in WATCH tokens via blockchain RPC  
**Status:** Analysis framework ready, awaiting RPC execution with Helius API key  

---

## Executive Summary

**Discovery:** `HLRKtAqU5qS9RsNekYndot7AHwDWX2CRoCr4NLWSgfk` (creator of token #2210, -97.97% loss) shows a **"nay image price 0.0000151" transaction on Solscan** — the same signature as known WATCHTOWER-orchestrated transactions.

**Hypothesis:** The signal token `39xCqVsexsszuf3wi4g1S3WuahYkcEcLnYwzo6ZvddbpPUP3nQHrefGkAr7Ury3CeHL2CNP47C5SrvyED9masctj` transferred at amount **0.0000151** is a **coordination marker** connecting WATCHTOWER infrastructure to WATCH token creators.

**Method:** 2/3-hop RPC analysis to trace:
- **HOP 1:** All transactions TO each WATCH creator
- **HOP 2:** Filter for signal token transfers (0.0000151 amount)
- **HOP 3:** Identify source wallets and validate fan-out patterns (95+ recipients in 60s window)

---

## WATCH Token Population (Last 3 Days)

**Total creators:** 50 unique creators  
**Time window:** 2026-06-02 09:37:10 UTC → 2026-06-02 13:15:44 UTC (3.95 hours)  
**Tokens launched:** 300+ tokens across 50 creators  

### Sample Creators for Analysis

| # | Creator | Tokens | Status | Findings |
|---|---------|--------|--------|----------|
| 1 | `EMY3p9DDneFvnZStJaMM77mRjnVLUK1oKqhhv4jSj77Q` | 1 | UNKNOWN | ← **Check for signal token** |
| 2 | `73x9HHjBYVr6YhfNohAbEsDdFpwZRbgVX2UcJZq1VMn` | 4 | UNKNOWN | ← **Check for signal token** |
| 3 | `3oApZug9zFHshrer3kJnHPtjSCXrQh2FAib2CzT8Q4KT` | 10 | UNKNOWN | ← **Check for signal token** |
| 4 | `7FVfSdnR9VPGjMtmBP1Hz9C2DFTpoNX8gVVRmnimnGt9` | 34 | UNKNOWN | ← **Check for signal token** |
| 5 | `HLRKtAqU5qS9RsNekYndot7AHwDWX2CRoCr4NLWSgfk` | 1 | **🔴 MATCH** | ✅ **Has 0.0000151 signal token on Solscan** |

---

## Signal Token Details

**Mint:** `39xCqVsexsszuf3wi4g1S3WuahYkcEcLnYwzo6ZvddbpPUP3nQHrefGkAr7Ury3CeHL2CNP47C5SrvyED9masctj`  
**Transfer Amount:** 0.0000151 (exact, likely intentional precision)  
**Label on Solscan:** "nay image price" (possible instruction memo or program identifier)  
**Purpose:** Appears to be a **coordination marker token** used to signal WATCHTOWER involvement  

---

## RPC Analysis Method

### HOP 1: Find Creator Transaction History

```bash
curl -X POST https://api.helius.xyz/v0/addresses/{CREATOR}/transactions \
  -H 'Content-Type: application/json' \
  -d '{"api-key": "YOUR_API_KEY"}'
```

**What to look for:**
- Token transfers (tokenTransfers array)
- Mint matching signal token
- Direction: INBOUND (source → creator)
- Amount: exactly 0.0000151

**Success indicator:** Find at least one matching transfer

---

### HOP 2: Filter for Signal Token Transfers

From the HOP 1 response, extract transfers matching:

```json
{
  "mint": "39xCqVsexsszuf3wi4g1S3WuahYkcEcLnYwzo6ZvddbpPUP3nQHrefGkAr7Ury3CeHL2CNP47C5SrvyED9masctj",
  "amount": 0.0000151,
  "destination": "{CREATOR}",
  "source": "{UNKNOWN_WALLET}"  // ← This is what we want
}
```

**Capture:** Source wallet address

---

### HOP 3: Validate Fan-Out Pattern

For each source wallet found in HOP 2:

```bash
curl -X POST https://api.helius.xyz/v0/addresses/{SOURCE_WALLET}/transactions \
  -H 'Content-Type: application/json' \
  -d '{"api-key": "YOUR_API_KEY"}'
```

**Analysis:**
- Count outbound token transfers in 60-second window
- Look for 95+ concurrent transfers (SUB_PROV fan-out signature)
- Check timing alignment with target creator's token creation time

**Success indicator:** 
- Source has 95+ transfers within 60s
- Timing matches creator's token launch ± 60s
- Pattern matches known WATCHTOWER orchestration signatures

---

## Expected Findings

### If Signal Token Found (Match Confirmed)

```
Creator: HLRKtAqU5qS9RsNekYndot7AHwDWX2CRoCr4NLWSgfk
├─ HOP 1: Found inbound transfer of signal token
├─ HOP 2: Source = [Unknown SUB_PROV wallet]
└─ HOP 3: Source sent 95+ transfers in 60s window
    └─ WATCHTOWER COORDINATION CONFIRMED ✅
```

**Implication:** This WATCH token is NOT organic — it's part of coordinated attack

### If No Signal Token Found (Organic)

```
Creator: EMY3p9DDneFvnZStJaMM77mRjnVLUK1oKqhhv4jSj77Q
├─ HOP 1: Searched all transactions
├─ HOP 2: No signal token transfers found
└─ WATCHTOWER COORDINATION NOT DETECTED
    └─ Likely organic/retail WATCH token ✅
```

**Implication:** This WATCH token appears to be genuinely fresh/independent

---

## Execution Steps

### Step 1: Prepare RPC Environment
```bash
export HELIUS_API_KEY="your_api_key_here"
export SIGNAL_TOKEN="39xCqVsexsszuf3wi4g1S3WuahYkcEcLnYwzo6ZvddbpPUP3nQHrefGkAr7Ury3CeHL2CNP47C5SrvyED9masctj"
export SIGNAL_AMOUNT="0.0000151"
```

### Step 2: Run 2-Hop Analysis on All 50 Creators
For each creator from last 3 days:
1. HOP 1: `getTransactions({creator})`
2. HOP 2: Filter for `mint == SIGNAL_TOKEN && amount == SIGNAL_AMOUNT`
3. HOP 3: For each match, validate source wallet fan-out pattern

### Step 3: Aggregate Findings
- Count total creators with signal token matches
- Identify unique source wallets (SUB_PROVs)
- Cross-reference with known orchestration patterns
- Calculate % of WATCH tokens linked to WATCHTOWER

### Step 4: Validate Timing Correlation
For each match:
- Compare transfer timestamp with token creation timestamp
- Expected: ±60 second alignment
- Deviation > 5 minutes = likely unrelated

---

## Database Schema for Results

```sql
CREATE TABLE wt_watch_rpc_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    creator TEXT NOT NULL,
    mint TEXT,
    signal_token_found BOOLEAN DEFAULT 0,
    source_wallet TEXT,
    source_outbound_count INTEGER,
    source_outbound_window_sec INTEGER,
    timing_delta_sec INTEGER,
    is_watchtower_linked BOOLEAN DEFAULT 0,
    analysis_timestamp REAL NOT NULL,
    created_at INTEGER DEFAULT (strftime('%s','now'))
);

CREATE INDEX idx_wt_watch_analysis_creator ON wt_watch_rpc_analysis(creator);
CREATE INDEX idx_wt_watch_analysis_linked ON wt_watch_rpc_analysis(is_watchtower_linked);
```

---

## Key Metrics to Track

For each creator:

| Metric | Target | Implication |
|--------|--------|-------------|
| Signal token found | YES | Coordination marker present |
| Source wallet outbound count | 95+ | SUB_PROV fan-out confirmed |
| Timing delta | <60s | Synchronized orchestration |
| Multiple matches | >1 source | Multiple WATCHTOWER clusters active |

---

## Risk Assessment

### High Confidence Match (All 3 conditions met)
- ✅ Signal token transfer found
- ✅ Source has 95+ fan-out in 60s
- ✅ Timing within ±60s of token creation
- **Probability WATCHTOWER:** 99%

### Medium Confidence (2/3 conditions)
- ✅ Signal token found
- ✅ Timing aligned
- ❌ Source fan-out <95
- **Probability WATCHTOWER:** 60-70%

### Low Confidence (1/3 conditions)
- ✅ Signal token found
- ❌ Source fan-out pattern unclear
- ❌ Timing misaligned
- **Probability WATCHTOWER:** 20-30%

---

## Comparison with Known WATCHTOWER Signatures

### From Earlier Analysis (BOT_SWARM_FINGERPRINTS.md)

**Orchestration Trio (Confirmed WATCHTOWER):**
- `Gp7RKGWpRugY45fbbZ56fbg7RChAzpze7jfWUPeDxJdr`
- `HuQbfsgZgknYmDEb8tin8HpXZRyPXUGm5z1pCSYh8CWn`
- `9y5Hq2hvUMy2zpEMuMHyDp7n5X4nZyDLaYPm5VgV7VjZ`

**Rapid Fire (Confirmed WATCHTOWER):**
- `FXp6jM7uC4iji6LYP3ah3XNfkTXB145gBYWgieeqGf78` (66 tokens in 50 min)

**Expected Signal Token Behavior:**
- These known wallets should also have signal token transfers in their history
- If NOT found: indicates hidden secondary WATCHTOWER network
- If found: validates signal token as coordination marker

---

## Next Steps

1. **Execute HOP 1-3 analysis** on 50 WATCH creators
2. **Aggregate results** into wt_watch_rpc_analysis table
3. **Identify unique source wallets** (SUB_PROVs)
4. **Cross-reference** with known WATCHTOWER infrastructure
5. **Calculate coordination percentage** of "fresh" WATCH tokens
6. **Update threat model:** How many seemingly organic WATCH tokens are actually WATCHTOWER-linked?

---

## Hypothesis Testing

**H1 (Null):** WATCH tokens are organic, independent launches with no WATCHTOWER involvement
- Expected: 0-5% signal token match rate
- If observed: Continue treating WATCH as low-risk

**H2 (Alternative):** WATCH tokens are partially WATCHTOWER-coordinated via signal token marker
- Expected: 20-50% signal token match rate
- If observed: Revise threat model, implement signal-token-based detection

**H3 (Strong):** Most WATCH tokens are WATCHTOWER-orchestrated despite appearing fresh
- Expected: >70% signal token match rate
- If observed: "WATCH token" is WATCHTOWER deception strategy

---

## Conclusion

This RPC analysis will definitively answer whether WATCH tokens are:
- ✅ **Genuinely organic** (hypothesis H1)
- ⚠️ **Partially coordinated** (hypothesis H2)
- 🔴 **Systematically WATCHTOWER** (hypothesis H3)

The signal token (0.0000151 amount, specific mint) is the key to distinguishing real fresh creators from WATCHTOWER's hidden coordination layer.

**Ready to execute on Helius RPC.** Provide API key to begin 2/3-hop analysis.

