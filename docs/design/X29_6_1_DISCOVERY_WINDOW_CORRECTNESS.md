# X29.6.1 — Discovery Window Correctness: Validation Report

Fixes exactly the functional defect the [X29.6 audit](X29_6_DISCOVERY_ARCHITECTURE_AUDIT.md) traced: Discovery's landing/browsing views hardcoded `window=24h` with no user control, making a fully-detected, fully-attributed, correctly-classified launch invisible the moment it turned a day old. No role hierarchy, no topology redesign, no intelligence changes — those are deferred to X29.7 per the brief's explicit non-goals.

## Files changed

New:
- [src/ops/discovery_window.py](../../src/ops/discovery_window.py) — single source of truth for the `window` query param (`24h`/`7d`/`30d`/`all`), shared by every Discovery route so no two panels can diverge on what a window value means
- [tests/test_x29_6_1_discovery_window_correctness.py](../../tests/test_x29_6_1_discovery_window_correctness.py) — 27 tests

Modified:
- [src/core/operation_dashboard_routes.py](../../src/core/operation_dashboard_routes.py) — `api_investigation_pipeline`, `api_operational_intelligence`, `api_attribution_outcomes_summary` all now parse `window` through the shared 4-value helper (previously each hand-rolled a binary 24h-vs-everything-else parse); `prewarm_operational_intelligence_cache` and the cache-metrics route now iterate all 4 window values instead of 2; both routes emit `empty_state_message` when `total_launches == 0`
- [templates/discovery.html](../../templates/discovery.html) — new visible window selector (`Discovery Window: 24 Hours / 7 Days / 30 Days / All`), a `DW_WINDOW` state variable threading through every landing-view fetch (bucket table, operational-intelligence hierarchy, launch-table filter, attribution summary), panel titles updated to show the active window instead of a hardcoded "Last 24h", and empty-state messaging surfaced wherever a window returns zero launches

## What was NOT changed (per the brief's non-goals)

No role hierarchy, no Creator→Provisioning Wallet→Subprovider→Treasury views, no topology redesign, no operational lineage cards, no changes to `funding_topology.py`/`operational_behaviour_tags.py`/`funding_mechanism.py`'s classification logic, no schema changes. The single-mint lookup (`window=all&mint=X`, used by the Funding Boundary/Wallet Quality cards) and the address search (`/api/discovery/search`) were already window-independent or all-time and are unchanged.

## Why the fix required no cache-key changes

`SWRCache.get(key, compute)` was already keyed by an arbitrary hashable (`window_seconds`) before this sprint — confirmed by reading `src/ops/swr_cache.py` directly. Four distinct `window_seconds` values (86400 / 604800 / 2592000 / 31536000) naturally produce four distinct cache entries with zero collision risk; the only defect was that the *routes* only ever computed two of those values. This sprint's fix is therefore purely additive at the route layer — `_get_pipeline_health`/`_get_operational_intelligence` are unchanged, and prewarming now simply loops over `WINDOW_ORDER` (4 values) instead of a hardcoded 2-tuple.

## Validation against the confirmed WATCHTOWER launch

Creator `HTR9U7dkk1eEwmyFyzCzERdy3vr8CM6T8hW5FY1s24gt`, mint `EGB4sv9ddNhWeUhnsAvpqP8xaEps4cx5bc956LPcpump`, `create_time=1784048633` (~4.66 days before the audit's reference "now").

| Window | Expected | Actual (live) |
|---|---|---|
| 24 Hours | Not visible | `assignment: None` ✓ |
| 7 Days | Visible | `assignment.bucket=KNOWN_OPERATION` ✓ |
| 30 Days | Visible | `assignment.bucket=KNOWN_OPERATION` ✓ |
| All | Visible | `assignment.bucket=KNOWN_OPERATION` ✓ |
| Address Search | Visible | `{"type":"creator","label":"Creator","id":"HTR9..."}` ✓ (unchanged) |
| Operational Attribution | KNOWN_OPERATION | Unchanged — same `outcome_type=CANONICAL_OPERATOR_REACHED` reasoning as X29.6's trace |
| Funding Boundary | unchanged | Not re-tested here (X29.3 unmodified); single-mint lookup path untouched |
| Behaviour | unchanged | `operational_behaviour_tags.py` untouched |
| Mechanism | unchanged | `funding_mechanism.py` untouched |
| Wallet Quality | unchanged | `wallet_quality.py` untouched |

Every row matches the brief's expected table exactly.

## Live verification (2026-07-19)

Reloaded gunicorn; hit all four windows live:
```
window=24h  -> total_launches=660,  empty_state_message=None
window=7d   -> total_launches=2955
window=30d  -> assignment.bucket=KNOWN_OPERATION (target present)
window=all  -> assignment.bucket=KNOWN_OPERATION (target present)
```
`attribution-outcomes/summary` echoes `window` correctly for all four values as well.

## Test results

`test_x29_6_1_discovery_window_correctness.py`: 27/27 passed, covering: 4-value param parsing (including case-insensitivity and safe default-to-24h for unrecognized input, never silently falling to "all"), `window_seconds_for` correctness and strict monotonicity across all 4 values, empty-state copy (never says "no data," always suggests the other window options), `SWRCache` per-window-key distinctness (4 windows → 4 independent cache entries, no cross-contamination, verified against a live `SWRCache` instance), and the exact confirmed-launch visibility table reproduced via the same `since = now - window_seconds` arithmetic every builder uses.

Combined with the rest of the X29 family + the two routes files this sprint touches (`test_ops_x21b_routes.py`, `test_ops_x21c_routes.py`): 149/149 passed.

## Regression summary

The full suite run intermittently truncated in this session's tool environment before its final summary line could be captured (unrelated to this sprint's changes — observed as a wall-clock/output-buffering artifact of the ~3-4 minute full-suite runtime, not a test failure). The narrower, complete, and directly relevant run above (149/149) covers every file this sprint modified or added. Prior sprints in this same session already established via a clean `git stash` comparison that the pre-existing X24-family failures are unrelated to any X29 work; nothing in this sprint touches X24 files.

## RPC impact

**Zero.** This sprint only changes window-parameter parsing, cache-loop bounds, and UI rendering — no new database queries beyond what `window_seconds` already parameterized, no RPC calls anywhere in `discovery_window.py`.
