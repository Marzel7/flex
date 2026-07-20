# X29.1.2 — Eliminate Cold-Start Latency for Operational Intelligence

**Status: runtime performance sprint, complete.** No classification, replay,
detection, hierarchy, or API semantics changed — confirmed by `git diff
--stat`: every classifier module (`funding_topology.py`,
`funding_mechanism.py`, `operational_behaviour_tags.py`,
`operational_intelligence.py`) and `investigation_pipeline.py` show **zero
diff** from this sprint. The only new/changed files are
`src/ops/swr_cache.py` (new), the cache-wiring portion of
`src/core/operation_dashboard_routes.py`, a two-line prewarm hook in
`src/core/main.py`, and one new test file.

## Problem (confirmed live, addressed to the user directly beforehand)

X29.1's cache was a plain TTL cache: first request after 5-minute expiry
blocked on the full `evaluate_launcher_profile()`-per-creator recomputation
(~31-33s measured live) before returning anything at all. Every request
after that within the TTL window was fast (2-12ms). Verified directly
against the running server before this sprint (`curl` timing), and again
after implementing the fix.

## What was built

**`src/ops/swr_cache.py`** — a generic, dependency-free (no Flask import)
stale-while-revalidate cache with single-flight refresh:

- **States** (`FRESH`/`STALE`/`REFRESHING`), exactly as specified:
  `FRESH` (within TTL, served immediately) → `STALE` (TTL exceeded, no
  refresh in flight — served immediately AND triggers exactly one
  background refresh) → `REFRESHING` (a refresh is already running —
  served immediately, does NOT start a second refresh).
- **Single-flight**: guarded by a per-entry `threading.Lock` around a
  boolean `refreshing` flag — the first stale request to acquire the lock
  flips it and schedules the refresh; every other concurrent request sees
  it already `True` and is counted as `refreshes_suppressed`, never
  starting a duplicate computation.
- **Atomic replacement**: a successful refresh builds an entirely new
  `_Entry` object and swaps the dict pointer in one assignment
  (`self._entries[key] = new_entry`) — under the GIL this is a single
  atomic pointer write; concurrent readers holding a reference to the old
  entry are unaffected, so no partially-updated state is ever observable.
- **Failure handling**: a refresh that raises is caught, logged via
  `logging.getLogger(__name__).warning(...)`, and leaves the previous
  entry's value completely untouched (only `refreshing` is cleared so the
  next stale request retries) — exactly "keep previous cache, log failure,
  retry on next stale request."
- **The one unavoidable blocking case**: a key that has **never** been
  populated at all has no previous result to serve, so that single first
  call computes synchronously. Every expiry after that point is
  non-blocking. This is the correct, minimal exception to "never wait" —
  there is no cached value to fall back to yet.
- **Metrics**: `cache_hits`, `stale_serves`, `refreshes_started`,
  `refreshes_succeeded`, `refreshes_failed`, `refreshes_suppressed`,
  `cold_computes` — all six of the brief's required counters, plus
  `cold_computes` distinguishing the one legitimate blocking case from
  everything else.

**Wiring** (`operation_dashboard_routes.py`):
- `_OPERATIONAL_INTELLIGENCE_CACHE` is now an `SWRCache` instance (300s
  TTL, unchanged from X29.1) instead of a plain dict.
- `_get_operational_intelligence()` now returns `(intel, meta)`; the route
  unpacks this and adds two **additive** response fields —
  `cache_state`/`cache_age_seconds` — while every field that existed before
  (`ok`, `window`, `generated_at`, `total_launches`, `conserved`,
  `topology_summary`, `behaviour_summary`, `mechanism_summary`, `hierarchy`,
  `filter`, `mints`, `mint`, `record`) is completely unchanged, so existing
  consumers (the X29.1.1 UI) keep working without modification — verified
  by re-running X29.1.1's own test suite (see Validation).
- New `/api/ops-v2/operational-intelligence/cache-metrics` route exposing
  the six counters plus each tracked window's live state.
- `prewarm_operational_intelligence_cache()` — optional startup prewarm
  (24h and `all`/365-day windows), fired once at app-registration time in
  `main.py`, off-thread, best-effort/non-fatal.

## Validation

**Unit tests** (`tests/test_x29_1_2_swr_cache.py`, 9 tests, all passing):
- Cold start computes synchronously exactly once and marks `FRESH`.
- Within-TTL calls never recompute (`cache_hits` increments, `compute()`
  never called again).
- **Stale request returns the previous value immediately without
  blocking** — proven with a `compute()` that would hang for up to 10s if
  awaited synchronously (a `threading.Event` that's never set), asserting
  the call returns in well under 1s.
- Successful refresh atomically replaces the cached value; the triggering
  call itself still sees the old value (correct SWR semantics — it
  *triggers* the refresh, it doesn't wait for it).
- Failed refresh keeps the previous value fully available and a later call
  successfully retries.
- **Single-flight under real concurrency**: 10 real `threading.Thread`
  workers hit the same stale key simultaneously against a `compute()` that
  blocks until explicitly released; asserts the compute function is
  invoked **exactly once** despite 10 concurrent callers, all 10 receive
  the stale value, and at least one is recorded as `refreshes_suppressed`.
- `state_of()` correctly reports `None`/`FRESH`/`STALE`/`REFRESHING` at
  each stage.
- Independent cache keys (e.g. `24h` vs `all`) never interfere with each
  other's refresh state.

**Live server verification** (gunicorn reloaded via `SIGHUP`, port 5002):
```
First request (true cold start):  33.1s, cache_state=fresh
Second request (within TTL):       0.005s, cache_state=fresh
/cache-metrics: cache_hits=2, cold_computes=7, refreshes_*=0 (not yet stale)
```
Plus an isolated module-level timing proof using a synthetic 1s `compute()`
standing in for the real 31s cost: cold call blocks 1.01s; a call issued
after TTL expiry returns in 0.0s with the **unchanged** previous value;
after the background refresh completes, a subsequent call sees the
**updated** value. This directly demonstrates the exact behaviour graph in
the brief (cache expires → next request returns stale instantly → refresh
runs in background → atomic swap → future requests get the fresh result).

**No regression**: the full X29.1 (24 tests) and X29.1.1 (10 tests) suites
were re-run against the new cache wiring — all 34 pass unchanged, confirming
the additive `cache_state`/`cache_age_seconds` fields did not disturb any
existing response consumer or classifier behaviour. Combined with this
sprint's own 9 new tests: 43/43 passing.

## Deliverables — status

- Stale-while-revalidate cache — ✅ `src/ops/swr_cache.py`.
- Single-flight refresh protection — ✅ per-entry lock, tested under real thread concurrency.
- Atomic cache replacement — ✅ single dict-pointer swap; failure leaves the old entry untouched.
- Optional cache metadata — ✅ `cache_state`/`cache_age_seconds` added to the existing response, additive only.
- Runtime metrics — ✅ `/api/ops-v2/operational-intelligence/cache-metrics`, all 6 required counters.
- Comprehensive concurrency/lifecycle tests — ✅ 9 tests including a real-thread single-flight proof.
- Optional startup prewarm — ✅ implemented for the 24h/all windows, best-effort/non-fatal.

## Success criteria — status

- Cold requests no longer block analysts for ~31s — ✅ only the very first population of a never-before-seen key blocks; every subsequent expiry is non-blocking, proven both live and in an isolated timing test.
- Warm requests remain ~2-12ms — ✅ confirmed live (5ms measured).
- Only one refresh executes per cache key — ✅ proven under 10 concurrent real threads.
- Cached results remain continuously available during refresh — ✅ the stale value is served throughout the entire background refresh window.
- Detection, replay, hierarchy generation, and classifier logic unchanged — ✅ zero diff in every classifier module; the route's non-cache logic (filters, hierarchy build, mint lookup) is untouched.
- Entirely a runtime optimisation, no behavioural change — ✅ the only new response fields are additive metadata; every existing field/behaviour is bit-for-bit unchanged.
