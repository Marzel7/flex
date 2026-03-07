# Helius vs Local RPC Metrics Audit Script

## Overview

This script compares **Helius billed RPC credits** against **locally tracked RPC metrics** to identify discrepancies, track missing instrumentation, and validate credit accounting.

---

## What It Does

The audit script:

1. **Fetches Helius usage** - Reads current Helius daily credit usage from `rpc_metrics_config.py`
2. **Waits 60 seconds** - Establishes a baseline for consistent comparisons
3. **Runs creator extraction** - Executes 3 iterations of creator funding extraction
   - Captures Helius credits before/after
   - Captures local RPC metrics before/after
   - Computes deltas for both systems
4. **Runs funder extraction** - Executes 3 iterations of funder transfer extraction
   - Same before/after measurement approach
5. **Analyzes discrepancies** - Identifies where Helius ≠ Local tracking
6. **Outputs results** - Saves structured results in JSON and CSV

---

## Why This Matters

The audit reveals:

- **Local tracking gaps**: If Helius > Local, some RPC calls aren't instrumented
- **Estimation errors**: If Local > Helius, credit model is overestimating
- **Cache behavior**: Differences show which operations are cached vs metered
- **Attribution bugs**: Helps identify missing source_file or method tracking

---

## Running the Audit

### Basic Run
```bash
python3 helius_rpc_audit_script.py
```

### What You'll See
```
================================================================================
HELIUS vs LOCAL RPC METRICS AUDIT
================================================================================
Started: 2026-03-07T08:50:29.356826

📊 Fetching initial Helius usage...
   Helius credits today: 163438

⏳ Waiting 60 seconds for baseline...
  1/60  2/60  3/60  ...  60/60✅ Baseline wait complete

🎲 Selecting random creator from database...
   Selected creator: DTdHa4auX68jFtXv9wkzMYCahg295AnRuwvm6moW6meZ

🔄 Running 3 creator extraction iterations...
✅ CREATOR #1: Helius Δ=0 | Local Δ=0 | Diff=0 | Calls Δ=0
✅ CREATOR #2: Helius Δ=0 | Local Δ=0 | Diff=0 | Calls Δ=0
✅ CREATOR #3: Helius Δ=52 | Local Δ=0 | Diff=52 | Calls Δ=0

🔄 Running 3 funder extraction iterations...
✅ FUNDER #1: Helius Δ=0 | Local Δ=0 | Diff=0 | Calls Δ=0
✅ FUNDER #2: Helius Δ=0 | Local Δ=0 | Diff=0 | Calls Δ=0
✅ FUNDER #3: Helius Δ=0 | Local Δ=0 | Diff=0 | Calls Δ=0

✅ Results written to helius_audit_results.json
✅ Results written to helius_audit_results.csv

================================================================================
AUDIT SUMMARY
================================================================================

CREATOR:
  Helius Total Δ:  52 credits
  Local Total Δ:   0 credits
  Total Diff:      52 credits
  Avg Diff/Run:    17.3 credits
  Status: ⚠️  HELIUS HIGHER - 52 untracked credits

FUNDER:
  Helius Total Δ:  0 credits
  Local Total Δ:   0 credits
  Total Diff:      0 credits
  Avg Diff/Run:    0.0 credits
  Status: ✅ GOOD MATCH (within 10 credits)

OVERALL:
  Total Helius Δ:  52 credits
  Total Local Δ:   0 credits
  Net Difference:  52 credits
  Accuracy:        0.0%
```

---

## Output Files

### JSON Format (`helius_audit_results.json`)

```json
[
  {
    "timestamp": "2026-03-07T08:53:16.182839",
    "phase": "creator",
    "iteration": 1,
    "creator": "DTdHa4auX68jFtXv9wkzMYCahg295AnRuwvm6moW6meZ",
    "duration_seconds": 0.7,
    "helius_before": 163440,
    "helius_after": 163440,
    "helius_delta": 0,
    "local_credits_before": 26665,
    "local_credits_after": 26665,
    "local_credits_delta": 0,
    "local_calls_before": 3736,
    "local_calls_after": 3736,
    "local_calls_delta": 0,
    "helius_vs_local_diff": 0,
    "returncode": 0,
    "output": "Creator extraction completed"
  },
  ...
]
```

### CSV Format (`helius_audit_results.csv`)

```
timestamp,phase,iteration,creator,duration_seconds,helius_before,helius_after,helius_delta,local_credits_before,local_credits_after,local_credits_delta,local_calls_before,local_calls_after,local_calls_delta,helius_vs_local_diff,returncode,output
2026-03-07T08:53:16.182839,creator,1,DTdHa4auX68jFtXv...,0.7,163440,163440,0,26665,26665,0,3736,3736,0,0,0,Creator extraction completed
...
```

---

## Interpreting Results

### Perfect Match
```
Status: ✅ GOOD MATCH (within 10 credits)
```
- Helius and local tracking agree (within 10 credits tolerance)
- No action needed

### Helius Higher (Untracked Calls)
```
Status: ⚠️  HELIUS HIGHER - 52 untracked credits
Interpretation: Some RPC calls are missing local instrumentation
Action: Check which methods/sections aren't being tracked
```

### Local Higher (Overestimation)
```
Status: ⚠️  LOCAL HIGHER - 42 over-estimated credits
Interpretation: Local credit model estimates too high
Action: Review credit calculation logic, check cache handling
```

---

## Key Metrics

| Field | Description |
|-------|-------------|
| `phase` | "creator" or "funder" extraction phase |
| `iteration` | Which run number (1-3 for each phase) |
| `helius_delta` | Credits charged by Helius during this run |
| `local_credits_delta` | Credits recorded locally during this run |
| `local_calls_delta` | RPC calls recorded locally during this run |
| `helius_vs_local_diff` | Difference (Helius - Local), positive = untracked |
| `returncode` | 0 = success, 1 = error in extraction |
| `duration_seconds` | How long the extraction took |

---

## Configuration

Edit these constants in the script to customize:

```python
FLEX_DB = "flex_complete_database.db"  # Database path
HELIUS_API_TIMEOUT = 10  # Seconds to wait for Helius response
WAIT_TIME_SECONDS = 60  # Baseline wait time
CREATOR_ITERATIONS = 3  # How many creator extraction runs
FUNDER_ITERATIONS = 3  # How many funder extraction runs
```

---

## Requirements

- Python 3.7+
- SQLite3 database with `creator_funders` table
- `rpc_metrics_config.py` with Helius usage data
- `realtime_creator_funding_extractor.py` module
- `funder_incoming_extractor.py` module
- RPC metrics recording enabled in `rpc_metrics_recorder.py`

---

## Troubleshooting

### "Cannot find a valid creator"
- The `creator_funders` table is empty
- Run the listener for a while to populate creator data

### "Failed to fetch Helius usage"
- `rpc_metrics_config.py` is missing or corrupted
- Check that `CURRENT_USAGE["credits_used_today"]` exists

### All deltas are 0
- Extraction may have been cached (data already processed)
- Try with a different creator address
- Check that extractors are actually making RPC calls

### "Unclosed client session" warnings
- Harmless aiohttp cleanup warnings
- Safe to ignore

---

## Advanced: Customizing the Audit

### Run Single Phase Only

Modify the audit in Python:

```python
audit = HeliusAudit()
audit.selected_creator = "YOUR_CREATOR_ADDRESS"

# Run only creator phase
for i in range(3):
    helius_before = audit.get_helius_usage()
    local_before = audit.get_local_metrics_summary()

    returncode, output, duration = audit.run_creator_extraction(
        "YOUR_CREATOR_ADDRESS"
    )

    # ... rest of logic
```

### Compare Multiple Creators

Loop through multiple creators:

```python
creators = [
    "CREATOR_1",
    "CREATOR_2",
    "CREATOR_3"
]

for creator in creators:
    audit.selected_creator = creator
    # Run audit for each
```

---

## Example Analysis

After running the audit, you can analyze results:

```bash
# See all mismatches
cat helius_audit_results.csv | awk -F',' '$15 != 0 { print }'

# Calculate total discrepancy
python3 -c "
import csv
total_diff = 0
with open('helius_audit_results.csv') as f:
    reader = csv.DictReader(f)
    for row in reader:
        total_diff += int(row['helius_vs_local_diff'])
print(f'Total discrepancy: {total_diff} credits')
"
```

---

## Next Steps

If audit reveals discrepancies:

1. **Identify gap**: Which RPC calls are missing?
2. **Check instrumentation**: Is the method being tracked?
3. **Verify cache**: Is the operation cached (should be free)?
4. **Test isolated**: Run a single extraction with fresh data
5. **Review metrics**: Check `rpc_metrics` table for the missing calls

---

## Related Documentation

- [RPC Metrics Dashboard](DASHBOARD_STATUS.md)
- [RPC Metrics Recorder](rpc_metrics_recorder.py)
- [Creator Funding Extractor](realtime_creator_funding_extractor.py)
- [Funder Extraction](funder_incoming_extractor.py)
