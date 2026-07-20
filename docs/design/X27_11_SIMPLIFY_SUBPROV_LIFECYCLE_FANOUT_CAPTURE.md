# X27.11 — Simplify Subprov Lifecycle into Fan-Out Capture → Creator Watch

**Status: design/investigation sprint. No code, schema, or runtime behaviour
was changed.** Per direction after Phase 4, this sprint stops at a concrete,
evidence-backed proposal (conclusion B) rather than implementing it — the
actual state-machine/ownership/telemetry changes are scoped as a separate
follow-up implementation sprint (suggested name: X28.0) so the "we proved the
architecture" and "we changed the runtime" steps stay cleanly separated with
an easy rollback point.

## Objective

Determine whether the subprov's own websocket subscription needs to remain
open for the lifetime of a `SESSION_TTL_SEC` window, or whether it is only
needed for the initial fan-out discovery window — with durable CREATE
coverage handed off to `ProgramCreateWatcher` independently of the parent
subprov's lifecycle.

## Phase 1 — Current lifecycle trace (code-verified)

Full call-chain, verified directly against `src/core/ws_cascade.py` (WS) and
`src/core/ws_cascade_store.py` (STORE):

```
confirmed treasury transfer / wrap-close on CDC or TEMP candidate
  → store.start_session()  [STORE:1076-1169, INSERT wt_active_subprov_sessions]
      called from 4 sites:
        _handle_treasury_tx   [WS:3053]  — treasury→wallet SOL transfer
        _handle_cdc_tx        [WS:3297] — CDC wallet wrap-closes to real destination
        _temp_candidate_sweep [WS:2848] — parked TEMP_PROVISION_CANDIDATE wrap-closes
        _handle_subprov_tx sub-subprov branch [WS:3534] — existing subprov plain-transfers onward
  → WS subscription opened: mgr.subscribe(subprov, "hot_subprov")  [WS:5276]
      (self-promotes to "subprov" kind on ack; reattached wholesale on reconnect
       from every ACTIVE row in wt_active_subprov_sessions [WS:2402+])
  → subprov tx observed (live WS notification OR sweep catch-up — same code path)
      → _handle_subprov_tx()  [WS:~3460]
          → extract_close_destinations(tx)  [wrap_close_detector.py]  — fan-out extraction
          → store.open_candidate_watch()  [STORE:1771]  — INSERT wt_candidate_websocket_watches
          → "PROTECT FIRST, CLASSIFY SECOND" gate  [WS:3577-3618]:
                PRE_CREATE (subprov has 0 prior launches)  → arm ALL candidates, always
                POST_CREATE, burst_size <= 6                → arm
                POST_CREATE, burst_size in 7-8              → arm
                POST_CREATE, burst_size in 9-10              → defer (INTEL only)
                POST_CREATE, burst_size >= 11                → defer (INTEL only)
            if armed: prog_watcher.add_candidates(watcher_metas_all, conn)  [WS:3605]
  → ProgramCreateWatcher.active_candidates[candidate] = {...}  [WS:1283]
      (in-memory dict; matched against the ONE global pump.fun logsSubscribe stream
       — see Phase 2)
  → later CREATE observed on the shared pump.fun program stream
      → checked purely as `creator in self.active_candidates`  [WS:952/964/1052/1097/1167/1523, etc.]
      → Cascade.process_candidate_sig() → durable launch write (wt_watchtower_launches)
  → subprov session TTL expiry / rejection
      → store.expire_stale_sessions() or reject_unproven_sessions()
      → cleanup_pass() calls mgr.unsubscribe(subprov) AND prog_watcher.evict_by_subprov(subprov)  [WS:4364-4376]
      → evict_by_subprov() DELETES the subprov's candidates from active_candidates  [WS:1338-1348]
```

### Key facts established (with citations)

1. **Session creation is bimodal**: either a raw treasury→wallet transfer
   (the common first trigger) or a confirmed wrap-close (secondary
   promotion path for CDC/TEMP candidates and sub-subprov chains). Both
   funnel into the same `start_session()`/subscription/extraction pipeline.
2. **Extraction and ProgramWatcher-arming are already decoupled from each
   other** — every extracted candidate is persisted to
   `wt_candidate_websocket_watches` (subject to `SAVE_CANDIDATE_FANOUT`), but
   only a subset is armed in `ProgramCreateWatcher`, gated by the
   `_arm_pw` burst-size threshold at [WS:3594-3605](../../src/core/ws_cascade.py#L3594-L3605).
   **This threshold only applies in `POST_CREATE` phase** — a subprov's
   *first* fan-out burst (before it has ever produced a launch) is always
   armed in full, regardless of size. A **second** large burst (≥11 new
   destinations) from an already-productive subprov is deferred to
   INTEL-only and never armed — a real, separate truncation risk from
   `MAX_CANDIDATES` (see Phase 4).
3. **`evict_by_subprov()` is destructive to already-armed candidates**
   ([WS:1338-1348](../../src/core/ws_cascade.py#L1338-L1348)) — it deletes
   matching entries straight out of `ProgramCreateWatcher.active_candidates`,
   and if that drains the dict to zero while the shared program stream is
   `ACTIVE`, it closes the entire stream. This is called from both
   `expire_stale_sessions()`'s cleanup path and `reject_unproven_sessions()`'s
   Phase-D rejection path — i.e. **every current subprov-session-TTL expiry
   silently destroys any already-armed CREATE coverage for that subprov's
   candidates**, even candidates that were legitimately captured and armed
   minutes or hours earlier.
4. Registration *does* survive a process restart (any DB row still
   `state='WATCHING'` within `CANDIDATE_TTL_SEC` is reloaded by
   `resync_subscriptions()`'s `_recent_candidates()` query,
   [WS:2311-2340](../../src/core/ws_cascade.py#L2311-L2340)) — restart
   resilience already exists; it's *live eviction* that is the gap, not
   durability across restarts.
5. The sweep is explicitly documented in its own docstring as potentially the
   **primary**, not backup, detection path
   ([WS:4391-4395](../../src/core/ws_cascade.py#L4391-L4395)) when
   `WS_SUBPROV_WATCH_ENABLED=0` — meaning under that flag configuration,
   fan-out *discovery itself* (not just CREATE detection) depends on the
   subprov remaining an actively-swept session.

## Phase 2 — The true monitoring unit

**Answer: once a candidate is durably armed in `ProgramCreateWatcher.active_candidates`, continued subscription to the parent subprov is NOT required to detect that candidate's later CREATE.**

Verified directly:

```python
# WS:786-791
class ProgramCreateWatcher:
    """Subscribes ONCE to the pump.fun program via logsSubscribe.
    On each CREATE notification: fetches tx, checks creator against
    active_candidates dict, and on match delegates to Cascade.process_candidate_sig
    ...
```

`ProgramCreateWatcher` opens exactly one `logsSubscribe` for the entire
pump.fun program ([WS:1360-1387](../../src/core/ws_cascade.py#L1360-L1387)),
independent of any per-wallet subscription. Every CREATE-match code path
(lines 952, 964, 1052, 1097, 1167, 1523 in `ws_cascade.py`) checks membership
in `self.active_candidates` — a plain in-memory dict keyed by candidate
wallet — with **zero reference to the parent subprov's session state,
subscription status, or existence at match time**. The subprov's own WS
subscription (`"hot_subprov"`/`"subprov"` kind, managed by
`SubscriptionManager`) exists purely to observe *the subprov's own
transactions* (to extract fan-out destinations) — it plays no role in
matching a candidate's eventual CREATE.

**The true monitoring unit after fan-out capture is the individual candidate
account, watched via the single global program-log stream — not the
subprov.** All current code that unnecessarily couples creator-watch
lifetime to subprov-session lifetime is exactly the eviction wiring
identified in Phase 1, fact 3: `expire_stale_sessions()` /
`reject_unproven_sessions()` → `cleanup_pass()` → `evict_by_subprov()`.
Severing that wiring (so the subprov's own subscription can end without
touching its already-armed candidates) is the core of the proposed fix.

## Phase 3 — Fan-out completeness (measured from `wt_candidate_websocket_watches`)

Measured directly from `database/wt_ops_v2.db`, restricted to subprovs with
2-25 captured candidates (the >100/>1000 tier is dominated by buy-swarm-style
wallets — see note below — not genuine multi-round creator fan-out):

```
n subprovs with any candidates: 426
distribution: 1 candidate=81, 2-6=54, 7-25=63, 26-100=63, 101-1000=150, >1000=15

fanout_duration_seconds (first→last detected_at), 2-25-candidate subprovs (n=117):
  mean=5059s   p50=51s   p95=35,841s (~10h)   max=173,130s (~48h)

late_accounts_after_30s:  66/117 (56%)
late_accounts_after_60s:  54/117 (46%)
late_accounts_after_120s: 45/117 (38%)
```

Inspecting the actual inter-arrival gaps for the longest-tail subprovs shows
the distribution is **bursty and clustered, not a uniform trickle** — e.g.
`5JWii73Qc9Fz…` (13 candidates): gaps of `[119, 25, 19479, 2, 63578, 2, 1802,
10413, 3, 0, 0, 34897]` seconds — several tight sub-30s clusters separated by
a few isolated multi-hour gaps. This matches the previously-established
"distribution funding two-mode" pattern (dust top-ups vs. one bulk
provisioning transfer) surfacing on the candidate side: most fan-out
completes within a minute, but a legitimately-late single top-up hours later
is common enough (~40-45% of multi-candidate subprovs) that it cannot be
dismissed as noise.

**Limitation**: candidate counts above ~100 in this table are dominated by
same-instant buy-swarm-style fan-out (per the existing "buy-swarm vs
creator" classification — these wallets SWAP, never CREATE, and were already
established as a separate, non-creator-fanout phenomenon in prior sprints).
`detected_at` timestamps for that tier reflect batch-processing/backfill
order, not genuine on-chain arrival spacing, so they were excluded from the
timing analysis above rather than averaged in and silently distorting the
result.

**Implication for Phase 6**: a single fixed short quiet-period (e.g. 60s)
would correctly close the majority of subprovs quickly (median ~51s to
complete fan-out) but would truncate the ~40-45% with a legitimate late
top-up — this is why Phase 6 below specifies quiet-period **reset-on-new-
activity** plus a bounded reopen/late-fan-out path, not a single silent
timeout.

## Phase 4 — Existing limits audit

| Constant | Value (default) | Actual scope (verified) |
|---|---|---|
| `SESSION_TTL_SEC` | 1800s (30min), env `WS_SESSION_TTL_SEC` | [WS:55](../../src/core/ws_cascade.py#L55) — TTL for `wt_active_subprov_sessions.expires_at`; drives `expire_stale_sessions()` |
| `CANDIDATE_TTL_SEC` | 1800s, env `WS_CANDIDATE_TTL_SEC` | [WS:61](../../src/core/ws_cascade.py#L61) — TTL for `wt_candidate_websocket_watches.expires_at` AND the reload-on-reconnect window in `_recent_candidates()` |
| `MAX_CANDIDATES` | **0 = no cap**, env `WS_MAX_CANDIDATES` | [WS:62](../../src/core/ws_cascade.py#L62) — **vestigial/unenforced**. Its only two references anywhere in the codebase are the definition and a single startup log line ([WS:5342](../../src/core/ws_cascade.py#L5342)). The one function that would enforce it, `candidate_count_for_subprov()` ([STORE:2136-2144](../../src/core/ws_cascade_store.py#L2136-L2144)), has **zero callers** — confirmed by grep across both files. Its own comment documents *why* it was neutered: the 595Xin→HXNyboe incident, where counting EXPIRED rows against the cap silently pinned a long-active subprov and dropped a real wrap-close. `open_candidate_watch()` (the actual INSERT) has no cap check at all today. |
| `MAX_ACTIVE_SUBPROVS` | 10, env `WS_MAX_ACTIVE_SUBPROVS` | [WS:63](../../src/core/ws_cascade.py#L63) — a **per-cycle rotation cap on how many subprov sessions get an RPC catch-up sweep**, not a candidate cap (see X27.10). Also slices the reconnect resubscription list ([WS:2300](../../src/core/ws_cascade.py#L2300)). |
| `SWEEP_CONCURRENCY` | 4, env `WS_SWEEP_CONCURRENCY` | [WS:96](../../src/core/ws_cascade.py#L96) — bounds concurrent `catch_up_subprov()` RPC calls per cycle; unrelated to candidate counts. |

**Can a subprov with 100+ genuine fan-out accounts have all of them armed?**
Yes, in the common case: `PRE_CREATE`-phase bursts are armed in full with no
size threshold. **The one real truncation risk found** is the `_arm_pw`
burst-size gate itself (Phase 1, fact 2) — a **second** large burst
(≥11 new destinations) from a subprov that has *already* produced ≥1 launch
is deferred to INTEL-only and never armed. This is a deliberate,
already-documented design choice (distinguishing genuine large early fan-out
from suspicious repeated large fan-out after a launch already fired) — not a
capacity bug — but it is a second, independent gate from `MAX_CANDIDATES`
that any redesign must account for and not silently conflate with the
(actually inert) `MAX_CANDIDATES` cap the brief specifically asked about.

**Does expiry of the parent subprov remove its children from
ProgramCreateWatcher?** Yes — confirmed and cited in Phase 1, fact 3. This is
the central defect this sprint's proposal fixes.

## Phase 5 — Proposed simplified state machine (design only, not implemented)

**Subprov-side lifecycle** (replaces the current single `ACTIVE→EXPIRED` TTL
model):

```
NEW               — session row inserted, WS subscription requested
CAPTURING_FANOUT  — subscription active; extracting/persisting/arming candidates
QUIET_PERIOD      — no new valid fan-out observed for quiet_period_seconds (resettable)
FINAL_RECONCILIATION — one bounded RPC catch-up sweep (getSignatures since cursor) to
                       catch anything the WS notification path missed
FANOUT_COMPLETE   — reconciliation succeeded, all discovered candidates persisted+armed
SUBPROV_UNSUBSCRIBED — WS subscription for the subprov itself is dropped;
                       session row retained (not deleted) for lineage/audit
```

**Candidate-side lifecycle** (new, independent of the subprov's state above):

```
DISCOVERED         — extracted from a wrap-close/transfer tx
PERSISTED          — wt_candidate_websocket_watches row committed
ARMED_FOR_CREATE   — present in ProgramCreateWatcher.active_candidates
CREATE_DETECTED    — matched on the shared program stream, launch recorded
  | EXPIRED_BY_EXPLICIT_POLICY  — reaped by its OWN TTL, never by parent-subprov eviction
```

The critical design change: **candidate `ARMED_FOR_CREATE` state must not be
touched by any subprov-side state transition.** Concretely, this means:
`evict_by_subprov()` (or its replacement) must stop being called from
`expire_stale_sessions()`'s and `reject_unproven_sessions()`'s cleanup paths.
Those paths should still call `mgr.unsubscribe(subprov)` (drop the subprov's
*own* WS subscription) but must leave `active_candidates` untouched for that
subprov's already-armed candidates. A genuinely-owned eviction of a
candidate should only ever be driven by the candidate's *own* TTL/outcome
(`CREATE_DETECTED` or `EXPIRED_BY_EXPLICIT_POLICY`), never by the parent
subprov's session state.

This reuses the existing tables (`wt_active_subprov_sessions`,
`wt_candidate_websocket_watches`) — no parallel schema is needed. The state
machine above maps onto new/reinterpreted values of existing `state` columns
plus a small number of new timestamp columns (`quiet_period_started_at`,
`reconciled_at`) rather than new tables.

## Phase 6 — Fan-out completion rule (design only)

Given Phase 3's bimodal timing (median ~51s, but 40-45% of subprovs see a
legitimate late top-up), the proposed rule is:

```
eligible to transition CAPTURING_FANOUT → QUIET_PERIOD when:
  - at least one valid candidate has been captured, AND
  - no new valid candidate observed for quiet_period_seconds (default:
    derive from measured p95 of tight-cluster gaps, NOT the full-tail p95 —
    recommend starting at 90-120s based on Phase 3's clustering pattern,
    configurable via env, not hardcoded)

QUIET_PERIOD → FINAL_RECONCILIATION when quiet timer elapses without reset
  - any new valid candidate arriving during QUIET_PERIOD resets the timer
    and returns state to CAPTURING_FANOUT

FINAL_RECONCILIATION → FANOUT_COMPLETE only when:
  - a getSignatures-based catch-up sweep completes with a SUCCESS outcome
    (reusing catch_up_subprov()'s existing SUCCESS/RPC_TIMEOUT/RPC_ERROR
    outcome contract), AND
  - every candidate discovered by that sweep is persisted AND armed

  - a FAILED reconciliation (RPC_TIMEOUT/RPC_ERROR/NO_RESULT) must NOT close
    the session — retry with the same bounded backoff already used elsewhere
    in this codebase (COLD_SUB_RETRY_MAX-style pattern), staying in
    FINAL_RECONCILIATION until it succeeds or a separate stuck-state alarm
    fires (see Phase 9 telemetry)

FANOUT_COMPLETE → SUBPROV_UNSUBSCRIBED: drop the subprov's own WS
  subscription only; candidate ARMED_FOR_CREATE state is untouched (Phase 5)
```

**Late fan-out after closure**: if a new transaction from an already-
`SUBPROV_UNSUBSCRIBED` subprov is later observed (e.g. via the existing
periodic reconciliation backstop, not a live subscription), the proposed
design treats this as a **lightweight late-fan-out path** rather than fully
reopening the session: extract+persist+arm the new candidate(s) individually
without recreating the subprov's own WS subscription or resetting the whole
state machine. This avoids indefinitely reopening old sessions while still
capturing genuinely late top-ups, and is consistent with Phase 3's evidence
that late activity is real but low-volume (typically 1-2 additional
candidates, not a fresh large burst).

## Phase 7 — Reframing the sweep (design only)

Proposed replacement of "repeatedly sweep every ACTIVE subprov until
SESSION_TTL" with "one final reconciliation sweep per subprov, triggered by
quiet-period elapse" for the **common case**. The existing rotating sweep
(`subprov_sweep_pass`/`fair_sweep_candidates`, the subject of X27.10) would be
**retained**, narrowed to cover only the genuinely exceptional states the
brief lists:

- WS subscription never confirmed (cold-pending, already tracked via
  `COLD_SUB_STALE_SEC`/`_cold_retry_count`)
- WS disconnect / reconnect gap
- process restart (candidates reloaded per Phase 1 fact 4; subprov sessions
  still in `CAPTURING_FANOUT` at restart need a resumed quiet-period timer,
  not a fresh one)
- `FINAL_RECONCILIATION` failure (retried via the sweep's existing bounded
  RPC/backoff machinery)
- session stuck in `CAPTURING_FANOUT` past some outer safety bound (a
  distinct, generous ceiling from `quiet_period_seconds` — a true stuck-state
  detector, not a normal-path TTL)
- a detected gap between on-chain history and persisted candidate rows
  (reusing the existing `subprov_sig_gap_detected` signal already emitted
  today — see the live monitor stream — as the trigger)

This directly satisfies the constraint "do not remove existing recovery
behaviour until equivalent coverage is proven" — the rotating sweep is
narrowed in *scope* (which sessions it needs to visit each cycle should drop
sharply once most subprovs close via quiet-period+reconciliation instead of
riding the full 30-minute TTL), not removed, and the X27.10 capacity findings
(cap too conservative under current load) become significantly less urgent
if the eligible population it must cover shrinks to just the exceptional
states above.

## Phase 8 — ProgramCreateWatcher ownership model (design only)

Current: `evict_by_subprov()` implies ownership is grouped under the parent
session — confirmed invalid per Phases 1-2 (the actual match logic has no
notion of "parent" at all; `evict_by_subprov` is the only place that
introduces subprov-keyed grouping into `ProgramCreateWatcher`'s otherwise
candidate-keyed model).

Proposed: retain the existing per-candidate `active_candidates` dict
structure (already stores `subprov`, `treasury`, `wrap_sig`, `wrap_time`,
`amount` per candidate — i.e. provenance is already present in the value,
per [WS:1283](../../src/core/ws_cascade.py#L1283)), and make eviction
**exclusively candidate-driven**:

- remove the `evict_by_subprov()` calls from `expire_stale_sessions()`'s and
  `reject_unproven_sessions()`'s cleanup paths (Phase 5)
- keep `evict_by_subprov()` itself (or an explicitly-renamed equivalent) for
  the one case where subprov-level bulk eviction is actually semantically
  correct: a **rejected** `PROVISION_CANDIDATE` that never produced a single
  valid wrap-close at all (i.e. it turned out not to be a subprov) — because
  in that case there genuinely are no legitimate candidates to protect
  (`reject_unproven_sessions` already only fires after 2h with zero
  wrap-close evidence, per its own docstring) — this needs re-verification
  in the implementation sprint, not assumed here
- candidates otherwise expire only via their own `expires_at`/outcome, never
  via a parent-keyed bulk delete

This is exactly the "watched_account / source_subprov / source_treasury /
discovered_at / armed_at / state" shape the brief proposes — largely already
present in the `active_candidates` value dict and the
`wt_candidate_websocket_watches` row; the fix is behavioral (who's allowed to
delete whom), not a schema rewrite.

## Phase 9 — Proposed telemetry (design only)

New per-event telemetry (mirroring the existing `_metric()`/`emit_event()`
patterns already used throughout `ws_cascade.py`):

```
subprov_enrolled, subprov_ws_active, fanout_account_discovered,
fanout_account_persisted, fanout_account_armed, quiet_period_started,
quiet_period_reset, final_reconciliation_started,
final_reconciliation_succeeded, final_reconciliation_failed,
subprov_closed_after_fanout, late_fanout_detected,
create_after_parent_closure
```

New aggregate gauges (extending the existing `sweep cycle` log-line pattern
and `_subprov_sig_metrics` dict):

```
capturing_subprovs, quiet_period_subprovs, reconciliation_pending,
fanout_complete, parent_unsubscribed, armed_creator_accounts,
late_fanout_count, create_after_parent_unsubscribe_count,
fanout_accounts_dropped_or_rejected
```

`fanout_accounts_dropped_or_rejected` is the conservation-proof metric the
brief requires stay at zero — it should increment on any candidate that is
discovered but fails to reach `PERSISTED` or `ARMED_FOR_CREATE` for a reason
other than explicit invalidity (malformed pubkey, etc.), giving a direct
regression signal if the new lifecycle ever silently drops an account the
old one would have kept.

## Conclusion

**B — Architecture valid but implementation blocked.**

Phases 1-4's evidence directly supports the brief's proposed model: CREATE
detection for an already-armed candidate depends solely on
`ProgramCreateWatcher`'s single global program-log stream, not on the parent
subprov remaining subscribed (Phase 2, code-verified with zero
counter-evidence found — no code path was found where CREATE detection
requires the parent subprov to still be active). Fan-out timing (Phase 3)
supports a quiet-period + final-reconciliation model over the current fixed
30-minute TTL, provided the quiet period resets on new activity and a
bounded late-fan-out path exists for the measured ~40-45% long tail. The
specific current coupling that must be fixed before this can be safely
implemented is concrete and narrow: **`evict_by_subprov()`'s calls from
`expire_stale_sessions()` and `reject_unproven_sessions()`'s cleanup paths in
`cleanup_pass()`** ([WS:4364-4376](../../src/core/ws_cascade.py#L4364-L4376))
— this is the one piece of code causing "not C" (parent monitoring is not
actually required for detection) to coexist with "not yet A" (the fix isn't
implemented yet, so today's system still behaves as if it were required).

This is not C (continued parent monitoring required) — no code or on-chain
evidence was found showing CREATE coverage depends on the subprov staying
active; the dependency that does exist is purely on the *discovery* window,
not the *detection* window. It is not D (hybrid model required) — no
distinct exceptional subprov class was found that genuinely needs indefinite
continued monitoring; the "exceptional states" identified in Phase 7 (cold
subscriptions, disconnects, restarts, reconciliation failures) are all
already bounded, retry-driven conditions, not a structurally different
class of subprov.

## Recommended next step (not part of this sprint)

A follow-up implementation sprint (suggested: **X28.0 — Decouple Creator
Watch Lifetime from Subprov Session Lifetime**) should:

1. Fix the specific `evict_by_subprov()` coupling identified above first,
   as the smallest change that stops real, currently-occurring detection
   loss (this alone is likely valuable even before the rest of the new state
   machine lands).
2. Implement the `CAPTURING_FANOUT`/`QUIET_PERIOD`/`FINAL_RECONCILIATION`
   state machine (Phase 5-6), configurable and defaulting conservatively.
3. Retain the existing rotating sweep as a fallback (Phase 7) until the new
   quiet-period-driven path is proven equivalent or better on replay.
4. Add the telemetry in Phase 9, especially
   `fanout_accounts_dropped_or_rejected`, before/alongside the state-machine
   change so regressions are visible immediately.
5. Run the full test matrix and replay comparison the original brief's Phase
   10 specifies, comparing old vs. new on the same historical corpus before
   removing any existing recovery behaviour.
