# Phase 3A Benchmarks

This directory contains benchmarking tools to measure Phase 2C performance improvements.

## Purpose

The Phase 3A benchmark validates that the new `networks_release` path performs correctly and measures latency improvements compared to the legacy fallback path.

## Running the Benchmark

### Prerequisites

1. **Flask app must be running**:
   ```bash
   python3 main.py
   ```
   Default URL: `http://127.0.0.1:5002`

2. **Database must exist** with at least one network:
   ```bash
   # Check database
   sqlite3 pumpswap_tokens.db ".tables"
   ```

3. **Optional: Install requests library** (for better performance):
   ```bash
   pip install requests
   ```
   (Fallback: script uses `urllib` if `requests` not available)

### Run Benchmark

From the project root directory:

```bash
# Default: 20 iterations, output to benchmarks/PHASE3A_REPORT.txt
python3 benchmarks/phase3a_benchmark.py

# Custom URL and iterations
python3 benchmarks/phase3a_benchmark.py --url http://localhost:5002 --iterations 30

# Custom report location
python3 benchmarks/phase3a_benchmark.py --report custom_report.txt

# Help
python3 benchmarks/phase3a_benchmark.py --help
```

## What Gets Benchmarked

The script tests 4 endpoints in both **new path** and **legacy path** modes:

### API Endpoints
1. **GET /api/funding-networks**
   - Returns list of funders with network stats
   - New path: queries `networks_release`
   - Legacy path: queries `creator_funders` + aggregation

2. **GET /api/funding-network-details/1**
   - Returns details for a specific network ID
   - New path: uses `network_name_from_id()` helper + networks_release
   - Legacy path: queries legacy tables directly

### HTML Endpoints
3. **GET /networks**
   - Renders dashboard of all networks
   - New path: `get_networks_release_list(include_evidence=True)`
   - Legacy path: `atomic_network_names` + aggregation

4. **GET /creator-network/<network_name>**
   - Renders detail page for a specific network
   - New path: `get_network_release_by_name()` + `get_network_members()`
   - Legacy path: `creator_networks` + `creator_to_creator_networks`

## Understanding the Report

### Metrics

Each endpoint/mode combination reports:

- **Cold start (ms)**: First request time
  - Includes application warmup, query compilation, first disk access
  - Represents user's initial page load experience

- **Warm average (ms)**: Mean of requests 2..N
  - Represents steady-state performance
  - Most important metric for user experience

- **P95 latency (ms)**: 95th percentile of all request times
  - Represents tail latency (worst 5% of requests)
  - Important for user-facing SLAs

### Speedup Calculation

```
Speedup% = (Legacy Avg - New Avg) / Legacy Avg × 100
```

- **Positive %**: New path is faster
- **Negative %**: New path is slower (shouldn't happen)
- **Example**: "25% faster" means new path is 4ms out of 16ms baseline

## Force Mode Environment Variable

The benchmark uses an optional environment variable to test both paths:

```bash
PHASE2C_FORCE_MODE=new    # Force new path
PHASE2C_FORCE_MODE=legacy # Force legacy path
PHASE2C_FORCE_MODE=       # Use default capability check
```

**Note**: This is automatically managed by the benchmark script.
The toggle is isolated to `route_phase2c()` and marked for removal in Phase 3D.

## Network Selection

The benchmark automatically selects a representative network:

1. **New path mode**: Queries `networks_release` for the largest network
2. **Legacy path mode**: Falls back to `creator_networks` if needed
3. **Default**: Uses network with highest `network_size`

This ensures both paths test against realistic data.

## Interpreting Results

### Expected Outcomes

#### If networks_release is fully built:
- New path should be **faster** (precomputed data)
- New path cold start: 10-50ms
- New path warm average: 5-20ms
- Legacy path may show 50-200ms+ (due to aggregation)

#### If networks_release is empty:
- Fallback to legacy path automatically
- No new path benefit yet
- Useful for baseline measurements

### Common Issues

**"Status 404" errors**:
- Check endpoint path is correct
- Verify Flask app is running
- Check network_name exists in database

**"FAILED" with no status code**:
- App may be crashed or unresponsive
- Check Flask console for errors
- Increase timeout with curl/requests

**High P95 latency**:
- Database may be under lock
- Other processes reading/writing DB
- Normal if database is large (first query is slow)

## Removing Benchmarks (Phase 3D)

When benchmarking is no longer needed:

1. Delete `benchmarks/` directory:
   ```bash
   rm -rf benchmarks/
   ```

2. Remove `PHASE2C_FORCE_MODE` toggle from `route_phase2c()`:
   - Delete lines checking `os.environ.get('PHASE2C_FORCE_MODE')`
   - Keep the normal `app.has_networks_release` logic

3. Remove `Response` import if only used by benchmarks:
   - Check if other code imports `Response`
   - If not, remove from Flask imports

## File Structure

```
benchmarks/
├── phase3a_benchmark.py      # Benchmark script
├── PHASE3A_REPORT.txt        # Generated report (created on first run)
└── README.md                 # This file
```

## Example Report Output

```
================================================================================
PHASE 3A BENCHMARK REPORT
================================================================================
Timestamp: 2026-02-27T15:30:00.123456
Iterations per endpoint/mode: 20
Test network: MyNetwork (id=1)

--------------------------------------------------------------------------------
Endpoint: /api/funding-networks (API)
--------------------------------------------------------------------------------
  NEW PATH:
    Cold start:     45.23 ms
    Warm average:   12.34 ms
    P95 latency:    18.56 ms

  LEGACY PATH:
    Cold start:     120.45 ms
    Warm average:   98.76 ms
    P95 latency:    145.23 ms

  Speedup: 87.5% faster (new path)

...
================================================================================
```

## Troubleshooting

### "Could not find network data in database"
- Ensure database exists and has networks
- Run `sqlite3 pumpswap_tokens.db "SELECT COUNT(*) FROM networks_release;"`
- If empty, run Phase 1 build logic first

### "Connection refused" on base URL
- Verify Flask is running: `curl http://127.0.0.1:5002/`
- Check URL parameter: `--url http://localhost:5000` etc
- Ensure port matches Flask app configuration

### "Slow network responses"
- Check if database has locks: `lsof | grep pumpswap_tokens.db`
- Consider running benchmark on idle system
- Increase `--iterations` for more stable averages

## Next Steps

After benchmarking:

1. **Analyze results**: Compare new vs legacy latencies
2. **Validate correctness**: Ensure both paths return identical data
3. **Plan optimization**: Identify slow queries and optimize indices
4. **Phase 3D cleanup**: Remove benchmarking code when analysis complete

---

**Created**: Phase 3A
**Status**: Ready for performance validation
**Cleanup Plan**: Phase 3D (remove entire benchmarks/ directory and toggle)
