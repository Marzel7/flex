# Funder RPC Metrics Audit Guide

## Overview

This document explains the Helius RPC metrics audit for the **funder funding extraction** system, which tests whether the `funder_incoming_extractor.py` module properly consumes billable Helius credits when analyzing funder funding sources.

## What We're Testing

### The Funder Extraction Pipeline

When a new token is created, Flex performs a 3-step funding extraction:

1. **Step 1: Creator Funding** (`realtime_creator_funding_extractor.py`)
   - Finds all wallets that sent SOL to the token creator
   - Uses `helius_enhanced_addresses_transactions` API (~100 credits per extraction)
   - Populates `creator_funders` table

2. **Step 2: Funder Incoming Extraction** (`funder_incoming_extractor.py`) ← **WHAT WE'RE TESTING**
   - For each funder from Step 1, analyzes their transaction history
   - Finds WHERE the funders got their money (funder sources)
   - Uses Helius Enhanced API to parse transaction details
   - Populates `funder_incoming_transfers` table
   - **Expected cost**: ~100-300 credits per funder (depends on transaction count)

3. **Step 3: Network Clustering**
   - Rebuilds funding network relationships
   - Identifies coordinated funding patterns

## Audit Methodology

### What Changed in This Audit

**Previous Approach (Problematic)**:
- Selected 1 creator
- Ran funder extraction 3 times on the **same creator's funders**
- Results: Cache hits → 0 RPC credits for iterations 2-3

**New Approach (Fixed)**:
- Selects 3 **different creators**
- Each creator has different funders
- Forces fresh extraction of diverse funders
- Reveals true credit consumption pattern

### Audit Flow

```
1. Initial Helius Usage Check
   ↓
2. Wait 60 seconds (baseline)
   ↓
3. CREATOR PHASE (3 iterations):
   - Select random creator #1
   - Measure: Before Helius credits
   - Run: Creator funding extraction
   - Wait: 60 seconds
   - Measure: After Helius credits
   - Delta = Credits consumed by creator extraction
   ↓
4. FUNDER PHASE (3 iterations):
   - Select random creator #2 (different from #1)
   - Reset fully_analyzed flag (force fresh extraction)
   - Measure: Before Helius credits
   - Run: Funder extraction for creator #2's funders
   - Wait: 60 seconds
   - Measure: After Helius credits
   - Delta = Credits consumed by funder extraction
   ↓
5. Repeat for creators #3, etc.
   ↓
6. Generate Results Report
```

## Key Variables

### Database Tables Used

**creator_funders** - Results from Step 1 (Creator Funding)
```sql
SELECT * FROM creator_funders
WHERE creator_address = 'some_creator'
LIMIT 5;
```
Columns: creator_address, funder_address, amount_sol, fully_analyzed

**funder_incoming_transfers** - Results from Step 2 (Funder Incoming)
```sql
SELECT * FROM funder_incoming_transfers
WHERE funder_address = 'some_funder'
LIMIT 5;
```
Columns: funder_address, sender_address, amount_sol, transaction_signature

### RPC Methods Called

In `funder_incoming_extractor.py`:

1. **get_transactions_helius()** - Fetch address transaction feed
   - API: `https://api.helius.xyz/v0/addresses/{address}/transactions`
   - Cost: FREE (standard address feed)
   - Returns enriched transaction data

2. **helius_batch_get_transactions()** - Batch transaction details
   - API: `https://api.helius.xyz/v0/transactions`
   - Cost: Varies (may be enhanced or standard)
   - Returns: nativeTransfers for parsing
   - Recorded as: `helius_enhanced_transactions_batch` in metrics

3. **Fallback: RPC getTransaction** - Pure RPC if Helius fails
   - Cost: 1 credit each
   - Slow and less accurate
   - Last resort only

## Expected Results

### Ideal Scenario

For **3 funder iterations** with diverse funders:

```
FUNDER PHASE RESULTS:
========================
Iteration 1: Creator A
  - Funder extraction: 5 funders × 20 credits avg = ~100 credits
  - Helius Δ: ~100 credits
  - Local Δ: ~100 credits
  - Match: ✅ Good

Iteration 2: Creator B
  - Funder extraction: 4 funders × 25 credits avg = ~100 credits
  - Helius Δ: ~100 credits
  - Local Δ: ~100 credits
  - Match: ✅ Good

Iteration 3: Creator C
  - Funder extraction: 6 funders × 15 credits avg = ~90 credits
  - Helius Δ: ~90 credits
  - Local Δ: ~90 credits
  - Match: ✅ Good

TOTAL FUNDER PHASE:
  - Total Helius: ~290 credits
  - Total Local: ~290 credits
  - Accuracy: ~100%
```

### Possible Variations

1. **Lower credits if some funders are cached** (first extraction flagged them as `fully_analyzed`)
   - Fingerprint clustering may SKIP already-analyzed wallets
   - This is actually correct behavior (optimization)

2. **Higher credits if funders are very active** (many transactions)
   - Each funder can trigger multiple batch calls
   - Active funders = more transfers = more API calls

3. **0 credits if extraction completes without RPC calls**
   - All data already cached in DB
   - Or extraction hits an error and returns early

## How to Interpret Results

### Good Match (within 10%)
```
Helius Δ:  100 credits
Local Δ:   100 credits
Diff:      0 credits
Accuracy:  100%
Status:    ✅ GOOD MATCH
```
Interpretation: Local metrics accurately tracking Helius billing

### Acceptable Match (10-20% variance)
```
Helius Δ:  100 credits
Local Δ:   110 credits
Diff:      10 credits
Accuracy:  90%
Status:    ✅ GOOD MATCH (within tolerance)
```
Interpretation: Local metrics slightly over-estimated, but tracking correctly

### Poor Match (>20% variance)
```
Helius Δ:  100 credits
Local Δ:   50 credits
Diff:      50 credits
Accuracy:  50%
Status:    ⚠️  MISMATCH
```
Interpretation: Local metrics missing significant credit consumption

## Debugging Guide

### If Funder Δ is 0 (No Credits Recorded)

1. **Check if extraction ran at all**
   ```bash
   tail audit_run_funder_focused.log | grep "Funder extraction completed"
   ```

2. **Check for cached results**
   - Query: `SELECT COUNT(*) FROM funder_incoming_transfers WHERE funder_address IN (...)`
   - If already populated from previous runs, extraction returns early

3. **Check for errors**
   - Look for "[EXTRACT]" or "[ERROR]" in logs
   - May indicate async/connection pool issues

4. **Verify Helius CLI is working**
   ```bash
   helius usage --json
   ```

### If Helius Δ is much higher than Local Δ

1. **Check what methods were called**
   - Query RPC metrics table for funder_incoming section
   - See which methods actually consumed credits

2. **Verify method attribution**
   - Some calls may be recorded under wrong source_file
   - Check `rpc_metrics_recorder.py` for proper attribution

3. **Check for batch calls**
   - Each `helius_batch_get_transactions` call may cost 100 credits
   - Multiple batches per funder = higher total

## Running the Audit

```bash
# Run the full audit
python3 helius_rpc_audit_script.py

# Monitor progress
tail -f audit_run_funder_focused.log

# View results
cat helius_audit_results.json | python3 -m json.tool
```

## Files Involved

- **helius_rpc_audit_script.py** - Main audit orchestration
- **funder_incoming_extractor.py** - Funder extraction implementation
- **rpc_metrics_recorder.py** - RPC metrics recording
- **helius_cli_monitor.py** - Helius CLI integration for usage data

## Conclusion

The funder audit verifies that the `funder_incoming_extractor.py` module:
1. ✅ Properly calls Helius Enhanced APIs for funder analysis
2. ✅ Consumes expected billable credits (100-300 per funder)
3. ✅ Accurately records metrics locally for cost tracking
4. ✅ Works with monitoring key (if applicable)

A successful audit shows ≥90% accuracy between Helius and local metrics, confirming the billing model is working as expected.
