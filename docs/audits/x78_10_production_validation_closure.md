# X78.10 — Cross-Process Write-Lane Production Validation & X78 Closure Gate

## Summary

X78.9 (`b2d15d63`) was deployed to all 13 supervised processes and validated
under genuine, unprompted production contention across a soak window (with
one clock reset — see Phase 22). Two additional real defects were found and
fixed live during validation (`46f6827e`, `6b5b5c56`, `430dd60b`). A third
defect was found, root-caused, and reproduced twice, but is **not** fixed in
this milestone — it is carved out as a precise, scoped blocker for X78.11.

**The core X78.9 acceptance test — can one wedged process still cause
indefinite platform-wide blocking? — was answered NO, repeatedly, under
real conditions, not synthetic ones.**

---

## Part A — Pre-deployment baseline (Phase 1-2)

At investigation start (`2026-08-08T08:34:03Z`), the platform was already
live-exhibiting the pre-X78.9 vulnerability: `creator_resolution_worker`
(then PID 45449) had held the cross-process write lock for 300+ seconds via
`rpc_metrics_recorder.py:_try_claim_reset_day` — the same call site later
identified as X78.10's root-caused blocker. `creator_funding_queue` was
backlogged at 16,387 pending. HEAD was confirmed at `b2d15d63` with no
uncommitted changes on the four X78.9 files specifically (unrelated
in-flight work on other files in the shared repo, left untouched throughout).

## Part B — Supervised deployment (Phase 3-4)

All 13 Python-based supervised processes (excluding `watchtower_ngrok`, a
pure tunnel with no DB involvement) were identified as importing the shared
locking primitives and restarted sequentially via `supervisorctl`, verifying
health before each next step, per explicit instruction. No SIGKILL was used
except once on the already-wedged pre-deployment `creator_resolution_worker`
instance (SIGTERM did not take effect on that specific wedge; this was
before X78.9 was live). All 13 confirmed RUNNING on `b2d15d63` by
`09:08:40Z`.

## Part C — Normal contention validation (Phase 5-8)

**Phase 5 (price-worker singleton)**: A 6-way concurrent burst against
`/api/price/health` immediately post-restart initially timed out — traced
to a cold-cache "first call ever" code path (pre-existing, unrelated to the
singleton fix), not lock contention. A second burst once the cache warmed
completed in ~30ms/request across all 6 concurrent requests with zero lock
errors, confirming the fix.

**Phase 6-8**: Within minutes of deployment, the write lane recorded its
first real `CrossProcessDatabaseWriteTimeout` under natural production
load: waiter PID 68047 (gunicorn), thread `ThreadPoolExecutor-0_5`,
`rpc_cache.py:68`, bounded at `wait_seconds: 60.003`, full owner diagnostics
attached. The gunicorn worker survived (no `WORKER TIMEOUT`/SIGKILL), the
API stayed responsive (33ms), and the caller (`rpc_cache.py`) logged a
controlled `"Failed to ensure table"` message rather than crashing. This
is the single clearest before/after proof point of the whole milestone —
**pre-X78.9, this exact shape of event was what caused the original
7.5-hour outage; post-X78.9, it was a 60-second, fully diagnosed,
fully-recovered non-event.**

## Part D — Defects found and fixed during validation

Three real defects were found, root-caused, fixed, tested, and deployed
during this pass — none were speculative, all were caught by genuine
production behavior:

1. **`get_price_service()` singleton race** (`46f6827e`) — identical
   unguarded `if _x is None` pattern to the `get_price_worker()` bug X78.9
   Phase 12 fixed, in a sibling module not audited in that pass. Fixed with
   the same dedicated-lock double-checked-locking pattern. 4 tests.

2. **Unguarded lock release in `TrackedConnection._acquire_write_lane`'s
   exception path** (`6b5b5c56`) — a pre-existing gap in `db_locking.py`
   (the only release call site in the file not wrapped in
   `except RuntimeError: pass`) that X78.9 made reachable in practice by
   causing far more exceptions to flow through that path than before.
   Caught live as `RuntimeError: release unlocked lock` masking the real
   timeout error. Fixed by applying the file's own established guard
   pattern. 4 tests.

3. **Listener startup crash-loop** (`430dd60b`) — `pumpfun_curve_listener
   ._ensure_db()`'s one-shot, unretried startup DDL turned a single bounded
   60s contention event into a fatal process crash; supervisord's immediate
   restart re-hit the same contention, producing 13 restarts in 21 minutes.
   Fixed with a bounded exponential-backoff-with-jitter retry wrapper
   (`_ensure_db_once` renamed from the original body), matching
   `creator_funding_worker.py`'s existing `_retry_on_nested_write`
   convention exactly. Retries ONLY `CrossProcessDatabaseWriteTimeout`;
   every other exception still fails immediately, unchanged. 4 tests.

Each fix triggered a redeploy scoped to only the processes that actually
needed it (all 13 for the first two shared-module fixes; listener-only for
the third, listener-local fix) — not blanket restarts for their own sake.

**34 tests total across all three fixes plus the original X78.9 suite, all
passing**, plus the full pre-existing 54-test regression sweep with no
regressions.

## Part E — Blocker found: NOT fixed in this milestone (Phase 12/13)

`creator_resolution_worker` was independently caught (twice) entering a
**permanent self-nested write-lease poisoning state**: a bounded
`CrossProcessDatabaseWriteTimeout` on one thread left `_thread_write_lease
.owner` permanently set, causing every subsequent write on that exact
thread to self-collide as `NestedDatabaseWriteError` forever, with zero
further successful cycles.

**Root cause fully identified**: `src/metrics/rpc_metrics_recorder.py
:_try_claim_reset_day` (lines 424-441) uses a bare `sqlite3.connect()`,
transparently routed through `db_locking.py`'s global monkeypatch into the
tracked write-lane machinery. Its `except Exception: return False` never
calls `conn.close()` if `commit()` raises, leaking both the SQLite
connection and — critically — the write-lane's thread-local ownership,
since `conn.close()` (the only path that would trigger
`TrackedConnection._release_write_lane()`) is skipped entirely on that
exception path.

**Reproduced twice** (PID 70588: 12 successful cycles vs 12,891 error log
lines before discovery; PID 82909 after a controlled restart: exactly 1
successful cycle, then repoisoned on the very next RPC-metrics flush,
captured at the precise log line). The second reproduction additionally
showed the poisoned process's `MainThread` becoming a *source* of
cross-process timeouts for other processes (the listener waited on it
three times, each cleanly bounded at 60.0s).

**This is not a failure of X78.9 — it is a demonstration of X78.9 working
exactly as designed.** The poisoned worker became a bad lock owner twice;
both times, every other process that hit it (the listener, other gunicorn
threads) waited a bounded, diagnosed 60 seconds and then continued or
recovered — no platform-wide outage, no crash-loop elsewhere, no manual
recovery required for anyone except the one already-known-bad process
itself. This is precisely the BEFORE/AFTER contrast X78.9 set out to prove,
observed live on a genuinely new, previously-undiscovered defect rather
than a synthetic one.

Per explicit instruction, this defect is **not patched inside X78.10** — it
is carved out as a scoped, precisely-root-caused blocker with a named
follow-up milestone: **X78.11 — RPC Metrics Recorder Lease-Poisoning
Repair**, targeting `_try_claim_reset_day` and any sibling raw
`sqlite3.connect()` call sites sharing the same missing-`conn.close()`-on-
exception defect shape.

## Part F — Soak (Phase 21-23)

Soak clock reset once (Phase 22), correctly, when the listener startup
crash-loop was found and fixed — that fix invalidated the prior window's
signal. **Not** reset again for the `creator_resolution_worker` blocker,
per explicit instruction, since that defect is pre-existing and unrelated
to the shared-lock changes under validation, and its containment is itself
a positive data point.

- **Authoritative window**: `2026-08-08T10:36:53Z` → `2026-08-08T12:10:33Z`
  (~94 minutes — exceeds the 60-minute minimum, close to the 120-minute
  preferred target).
- **13/13 processes** (excluding the one known-blocker) ran the full window
  with zero unexpected restarts.
- **`watchtower_listener` restarted 3 times during the window** — all three
  traced individually and confirmed unrelated to the write lane: two
  `PUMPPORTAL FATAL` WebSocket reconnect failures (`ConnectionClosedError`,
  and a `502` from PumpPortal's own upstream nginx) and zero contention on
  `_ensure_db` (9-11ms completion, no retries needed on any of the three
  restarts). This is normal long-lived-WS-client behavior, not a database
  defect.
- **Error classification since soak baseline**, by source:

  | Source | New lines | NestedDBWE | CrossProcTimeout | db_locked | RuntimeError |
  |---|---|---|---|---|---|
  | creator_funding_worker | 874 | 76 (known, bounded `_retry_on_nested_write`) | 0 | 0 | 0 |
  | creator_resolution_worker | 10,011 | 1,250 (the blocker, fully documented) | 1 | 0 | 0 |
  | walkback_worker | 1 | 0 | 0 | 0 | 0 |
  | ws_cascade | 0 | 0 | 0 | 0 | 0 |
  | listener | 23 | 0 | 3 (all traced to waiting on the poisoned worker) | 0 | 0 |
  | infra_sync_scheduler | 6 | 0 | 0 | 0 | 0 |
  | operation_scheduler | 0 | 0 | 0 | 0 | 0 |
  | watchtower_api | 105 | 0 | 0 | 0 | 0 |

  Every occurrence outside the two known, already-classified sources
  (`creator_funding_worker`'s pre-existing bounded retry pattern;
  `creator_resolution_worker`'s single carved-out blocker) is zero.

- **Queue depths**: `creator_funding_queue` 16,387 → 16,539 (flat/noisy,
  RPC-bound arrival rate, not a regression). `creator_resolution_queue`
  1,833 → 1,818 (net decrease despite the blocker, from partial progress
  during the worker's brief healthy windows).

## Part G — Systemic comparison (Phase 24-25)

**BEFORE** (the incident that motivated X78.9): `creator_funding_worker`
wedged, held the flock for ~7.5 hours, every other writer blocked
indefinitely, listener/API/platform degraded, manual SIGKILL required.

**AFTER** (observed live, multiple times, in this validation):
`creator_resolution_worker` wedged in an even worse way — not just once,
but *permanently, self-re-inflicted, on every cycle* — yet no other process
was ever blocked for more than 60 seconds at a stretch, diagnostics
identified the exact waiter/owner/command every time, and the rest of the
platform (funding worker, walkback, cascade, schedulers, API) kept running
normally throughout.

**Can one live wedged process still cause indefinite blocking in another
process? No.** That is the precise, direct answer this soak produced.

Per Phase 25's explicit instruction, these remain classified separately and
are not collapsed into one number:
- `NestedDatabaseWriteError` = local correctness/concurrency defect
  (the `_try_claim_reset_day` blocker; `creator_funding_worker`'s
  already-bounded retry pattern).
- `CrossProcessDatabaseWriteTimeout` = bounded cross-process contention
  failure (working exactly as designed, every observed instance).
- No `SQLITE_BUSY` or WAL-pressure events were observed during this window.

## Remaining defect inventory (Phase 26)

- **BLOCKER**: `creator_resolution_worker` / `rpc_metrics_recorder.py
  :_try_claim_reset_day` permanent write-lease poisoning. Root-caused,
  reproduced twice, scoped for X78.11. This is the sole reason X78 remains
  open.
- **TECHNICAL DEBT**: `infra_sync_scheduler`'s long full-table-scan refresh
  duration (X78.8 design, not reopened here — no production blocker
  observed from it this session).
- **OBSERVATION**: PumpPortal upstream WebSocket reliability (2 reconnect
  failures in ~94 minutes, one due to PumpPortal's own 502) — unrelated to
  this milestone's scope, listener recovers cleanly and quickly every time.

## Root-cause ledger (Phase 27 — cumulative, X78.0-X78.10)

| # | Mechanism | Status | Commit |
|---|---|---|---|
| 1 | Missing connection cleanup paths (X78.0) | FIXED | — |
| 2 | `asyncio.to_thread` executor-thread-pool reuse amplification | HISTORICAL | — |
| 3 | Detached background descendants (X78.2) | FIXED | — |
| 4 | RPC cache same-thread nested ownership (X78.3) | FIXED | — |
| 5 | Timeout/cancellation grace-period gap (X78.4) | FIXED | — |
| 6 | `RiskScoringBuilder` connection attribution (X78.5) | FIXED | — |
| 7 | 70s read-heavy work under write ownership (X78.6) | FIXED | — |
| 8 | Creator-specific context full-table scans (X78.7) | FIXED (partial perf win) | — |
| 9 | Infrastructure sync hot-path ownership (X78.8) | FIXED (ownership moved to standalone scheduler) | — |
| 10 | Unbounded cross-process `flock()` | **FIXED** | `b2d15d63` |
| 11 | Price-worker singleton initialization race | **FIXED** | `b2d15d63` |
| 12 | Price-*service* singleton initialization race (sibling of #11, found in X78.10) | **FIXED** | `46f6827e` |
| 13 | `TrackedConnection._acquire_write_lane` unguarded exception-path release | **FIXED** | `6b5b5c56` |
| 14 | `pumpfun_curve_listener._ensure_db()` unretried startup DDL crash-loop | **FIXED** | `430dd60b` |
| 15 | `rpc_metrics_recorder.py:_try_claim_reset_day` permanent lease poisoning on exception | **OPEN** — root-caused, reproduced twice, scoped for X78.11 | — |

Mechanisms 10-14 are recorded separately from #15 per Phase 21's explicit
instruction: #10-14 are the systemic-amplification and singleton-race class
this milestone targeted and closed; #15 is a distinct local-correctness
defect that #10's fix successfully *contained* but did not and could not
itself fix.

## Component verdicts (Phase 28)

- **Creator Funding**: READY — sustained progress throughout the soak,
  bounded/self-recovering retry behavior on its own pre-existing
  `NestedDatabaseWriteError` pattern, no crash, no manual intervention.
- **Cross-Process Write Lane**: SAFE — bounded timeout proven under real
  contention multiple times, diagnostics accurate every time, no forced
  lock stealing, no platform-wide outage even when directly tested against
  a permanently-poisoned real writer.
- **Walkback**: READY — fully quiet for the entire soak window, zero errors.
- **ws_cascade**: READY — fully quiet, reconnected cleanly on every restart,
  actively processing (subprov detection, session lifecycle) throughout.
- **Listener**: READY — three restarts during the soak, all confirmed
  unrelated (WebSocket reconnect failures, one from PumpPortal's own
  upstream), all recovered in milliseconds with zero lock contention on
  startup.
- **Web/API**: READY — survived the platform's first real
  `CrossProcessDatabaseWriteTimeout` without a worker crash, singleton
  fixes confirmed under genuine concurrent load, fully responsive
  throughout.

## Platform verdict (Phase 29)

**NOT READY.** Not because of imperfection in the systems this milestone
targeted — every one of them passed decisively — but because
`creator_resolution_worker` is currently, verifiably, functionally
non-operational (1 successful cycle out of the last ~1,342 attempted) due
to a distinct, now-precisely-scoped defect. Calling the platform READY
while a production worker is silently doing zero effective work would
misrepresent the platform's actual state.

## X78 closure decision (Phase 30)

**X78 REMAINS OPEN.** Every other closure criterion from the original
charter is met: cross-process indefinite blocking is eliminated (proven,
repeatedly, under real conditions); no crash-loop persists anywhere except
the one already-carved-out blocker; no manual recovery was required during
the authoritative soak; lock timeouts are bounded and diagnosable; the
price-worker/price-service singleton races do not recur. The sole open
item is precise: `creator_resolution_worker` can still poison its own
thread-local write lease through `rpc_metrics_recorder.py
:_try_claim_reset_day`, and that needs a dedicated fix before X78 can
close.

**Next milestone: X78.11 — RPC Metrics Recorder Lease-Poisoning Repair**,
scoped narrowly to `_try_claim_reset_day` and any sibling raw
`sqlite3.connect()` paths sharing the same `except Exception` cleanup gap.

## Milestone integration (Phase 31)

**NOT READY TO PUSH.** X78 has not closed. The accumulated X78.0-X78.10
work (10 local commits: `b2d15d63`, `46f6827e`, `6b5b5c56`, `430dd60b`, plus
this closure doc) is stable and well-tested in isolation, but per the
charter's own closure gate, a stable GitHub milestone requires X78 complete
first. Recommend proceeding directly to X78.11 given the defect is already
fully root-caused and reproduced — this should be a small, well-scoped fix
rather than an open-ended audit.

---

Creator Funding: READY
Cross-Process Write Lane: SAFE
Platform: NOT READY
X78: REMAINS OPEN
GitHub Milestone: NOT READY TO PUSH
