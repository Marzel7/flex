# CURRENT_WORK.md — Phase 3A Benchmarks (Validate Performance + Correctness)

## Objective

Create a small, repeatable benchmark workflow to measure Phase 2C performance gains
and ensure the new `networks_release` path behaves correctly compared to legacy.

Benchmarks must be runnable locally and produce a clear text report.

---

## Scope

Benchmark these 4 routes:

API:
1. GET /api/funding-networks
2. GET /api/funding-network-details/<int:network_id>

HTML:
3. GET /networks
4. GET /creator-network/<network_name>

Measure:
- Latency (ms) for N requests (e.g., 20)
- cold start (first request) and warm average (remaining)
- new path vs legacy path

---

## Key Constraint

Do NOT change endpoint logic.

Only add:
- a benchmark script
- (optional) a small toggle mechanism to force legacy/new mode for benchmarking

---

## Recommended Toggle Approach

Add a temporary environment variable to force routing mode for benchmarks:

- PHASE2C_FORCE_MODE = "new" | "legacy" | unset

Implementation guidance:
- If set to "new": treat app.has_networks_release = True
- If set to "legacy": treat app.has_networks_release = False
- If unset: use the normal capability check logic

This must be isolated and easy to remove later (Phase 3D cleanup).

---

## Deliverables

### 1) `benchmarks/phase3a_benchmark.py` (new file)
Script requirements:
- Accept base URL (default http://127.0.0.1:5002)
- Accept N iterations (default 20)
- Select one valid network_name dynamically from DB OR via a small config value
- Select one valid network_id (e.g., 1) for /api/funding-network-details
- Run in 4 modes:
  A) new path (force mode = new)
  B) legacy path (force mode = legacy)
  For each mode:
    - cold request timing
    - warm average timing
    - p95 timing (optional)
- Output a readable report to stdout and write to:
  `benchmarks/PHASE3A_REPORT.txt`

Use `requests` library if available; otherwise `urllib`.

### 2) Minimal docs
Add `benchmarks/README.md` explaining:
- how to run app
- how to run benchmark
- how to interpret report

---

## Benchmark Method (Suggested)

For each endpoint:
- Start timer
- GET endpoint
- Record elapsed_ms
- Repeat N times
- cold = first measurement
- warm_avg = mean(2..N)
- p95 = percentile 95 (optional)

Make sure to:
- use a Session/keep-alive if using requests (more realistic)
- include status code checks (fail fast if non-200)

---

## Data Selection

### network_name
Pick a real name by querying DB:
- prefer largest network (more realistic)
- or first network ordered by size desc

SQL for new path:
SELECT network_name FROM networks_release ORDER BY network_size DESC LIMIT 1;

Fallback if missing:
SELECT network_name FROM creator_networks WHERE network_name IS NOT NULL LIMIT 1;

### network_id
Use 1 for deterministic mapping (since Phase 2C uses ORDER BY network_name ASC mapping).
Also optionally select max id based on COUNT(networks_release).

---

## Definition of Done

- Benchmark script runs end-to-end
- Produces report comparing new vs legacy timings for 4 routes
- Does not modify endpoint logic (only optional forcing toggle)
- Can be run repeatedly and yields stable results

---

End of Instructions.
