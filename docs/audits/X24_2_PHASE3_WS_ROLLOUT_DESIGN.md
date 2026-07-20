# X24.2 Phase 3 — Controlled WebSocket Rollout Design

**Status: design only. `WS_SUBPROV_WATCH_ENABLED` remains `0`. No cohort has been enabled. This document does not authorize any change to that flag — it is the design to review before a separate, explicit decision to enable anything.**

## Why this is a separate decision from Phase 2

Phase 2's fair scheduler improves the RPC-sweep path, which is today's actual primary detector (confirmed in the X24.1/X24.2 reconciliation — `subprov_subs=0` in production heartbeats, `SUBPROV_WATCH_ENABLED=0`). Enabling the WS tier is an *additional*, independent lever: it would let some sessions be armed via a live push notification (near-instant) instead of waiting up to one fair-scheduler cycle for an RPC poll. It has not been enabled in at least 7+ weeks of commit history (per the X24.1 investigation), for reasons not documented in this repository. Re-enabling it, even partially, is a materially different kind of change than fixing the sweep's fairness — it opens new outbound WebSocket subscriptions against a paid third-party provider (Helius) at a volume this document has not yet measured against that provider's actual limits.

## Measured inputs (from live production data, read-only)

| Metric | Measured value |
|---|---|
| New eligible sessions per minute | ~9-10/min (91 in the last 10 min; 594 in the last 60 min) |
| Peak concurrent eligible sessions (point-in-time) | 283 |
| Sessions expiring/closing per minute | ~10.9/min (655 in the last 60 min) |
| Current active sessions by mechanism | WSOL_WRAP_CLOSE: 274 (100% of one snapshot); PLAIN_TRANSFER and SEEDED_ACCOUNT_CLOSE present historically but not in this exact snapshot — see caveat below |
| Confirmed-treasury-funded vs unknown-treasury among active sessions | 159 known / 115 unknown (in one snapshot) |
| Current process memory (RSS) | ~39 MB |
| Current process FD count | 60 |
| Current treasury-tier WS subscriptions (unaffected by this rollout) | 58, stable |

**Caveat on mechanism mix**: the one live snapshot taken during this design phase happened to show 100% `WSOL_WRAP_CLOSE` among currently-ACTIVE rows; this is a point-in-time artifact, not evidence that `PLAIN_TRANSFER`/`SEEDED_ACCOUNT_CLOSE` sessions are rare in general — historical `wt_watchtower_launches` data (X24.1) shows both mechanisms represented in real detected launches. A wider rollout should re-measure the mechanism mix over a longer window before finalizing per-mechanism subscription volume estimates.

## Unknowns that must be resolved before ANY live cohort is enabled

1. **Helius WebSocket subscription limits** (per-connection subscription count, per-account rate limits, plan-tier caps) — not documented anywhere in this repository. Must be confirmed against the actual Helius plan/dashboard before committing to a subscription count, not assumed from this codebase alone.
2. **Whether the existing single shared WS connection** (one `websockets` connection per the `SubscriptionManager`/`Cascade` architecture) has a practical subscription ceiling before Helius throttles or drops the connection. The treasury tier already holds 58 subscriptions on this same connection with no reported issue — that is the only real-world data point available, and it is roughly an order of magnitude below the potential subprov-tier volume (283 peak concurrent sessions) if fully enabled at once.

## Resource budget estimate (rough, pending provider-limit confirmation)

| Resource | Current (treasury tier only) | Projected if ALL active sessions were subscribed | Confidence |
|---|---|---|---|
| WS subscriptions | 58 | up to ~283 (peak concurrent), steady-state likely 100-200 given ~10/min arrival vs ~11/min expiry | Low — assumes linear scaling of the existing pattern; the actual ceiling per connection is unverified |
| RPC calls (sweep-driven catch-up, unaffected baseline) | ~10/cycle × 10/6s ≈ 100/min | Unchanged — Phase 3 explicitly keeps the sweep running as reconciliation/fallback even after WS is enabled | High — this is a Phase 3 requirement, not an estimate |
| RPC calls (WS-side catch-up-on-open, existing pattern) | N/A at 0 subprov subs today | Proportional to new-subscription rate: ~10/min if a live cohort of similar size to current arrival rate is enabled | Medium |
| Memory | ~39 MB baseline | Modest increase — each subscription is a small in-memory dict entry (`wallet_kind`, `wallet_sub`, pending-request bookkeeping); not expected to be the binding constraint | Medium |
| FDs | 60 | No material change — all subscriptions share the single existing WS connection FD; this is not a per-subscription FD cost | High |
| Unsubscribe/expiry rate | N/A | ~10.9/min, matching session expiry rate — each expiring session must be unsubscribed to avoid a slow accumulation of dead server-side subscriptions | High (directly measured) |
| Expected reduction in RPC sweep load | N/A | Proportional to the fraction of sessions covered live instead of by poll — cannot be quantified until the provider subscription ceiling is known | Unknown |

## Staged rollout plan

```
STAGE 0 — disabled (current state)
  WS_SUBPROV_WATCH_ENABLED=0. No change. This document changes nothing here.

STAGE 1 — shadow selection only
  Compute and log which sessions WOULD be selected for live WS subscription
  under each candidate cohort rule below, WITHOUT actually calling
  mgr.subscribe(). Purely observational — zero new WS connections, zero new
  RPC calls beyond what Phase 2 already does. Run for a full day-night cycle
  to see real volume/timing patterns (weekday vs off-hours variance).

STAGE 2 — limited cohort (single-digit subscriptions)
  Enable live WS subscription for a tiny, explicitly bounded cohort — e.g.
  the newest 5 confirmed-treasury-funded sessions only. Monitor subscription
  ack latency, unsubscribe correctness, and whether the sweep's
  duplicate-arming guard (idempotent on creator+create_sig; see Phase 4)
  correctly no-ops when both the sweep AND the WS path observe the same
  wrap-close.

STAGE 3 — bounded live cohort
  Widen to a percentage-based or capped-count cohort (e.g. "up to 25
  concurrent subprov WS subs, confirmed-treasury-funded only, oldest-session-
  evicted-first when at cap"). This is the first stage where the sweep's RPC
  load should measurably decrease for the covered cohort.

STAGE 4 — wider rollout
  Only after Stage 3 has run stably (measured FD/memory/lock health, no
  provider throttling observed) for a defined observation window, and only
  after the actual Helius subscription ceiling has been confirmed
  independently of this codebase.
```

## Candidate cohort controls (to select among at Stage 2/3, not decided here)

- **Newest N sessions** — simplest, but doesn't prioritise by confidence or value.
- **Confirmed-treasury-funded only** (`subprov_known=1` equivalent, or `treasury_wallet` in `wt_confirmed_treasuries`) — biases toward the highest-confidence sessions; in the one snapshot taken, 159/283 (56%) of active sessions were confirmed-treasury-funded, so this would meaningfully reduce cohort size versus "all sessions."
- **Mechanism-specific cohorts** — e.g. enable only `PLAIN_TRANSFER` sessions first, since that is the exact class X24.1 fixed and currently has zero live-path coverage of any kind (the RPC sweep DOES already cover it via `_handle_subprov_tx`, but a live cohort here would directly validate the X24.1 fix's real-world behaviour under actual production load).
- **Per-treasury caps** — bound how many concurrent subprov subscriptions any single treasury can hold, preventing one high-volume treasury from consuming the entire global cap.
- **Global subscription cap** — a hard ceiling (e.g. the `WS_MAX_ACTIVE_SUBPROVS`-style env var pattern already used elsewhere in this file) independent of cohort-selection logic, as a last-resort safety valve.

**No specific cohort or cap value is recommended here.** Choosing among these requires Stage 1's shadow-selection data and the confirmed Helius subscription ceiling — both currently unmeasured.

## The RPC sweep must remain active regardless

Per the sprint's explicit requirement, `subprov_sweep_pass()` (Phase 2's fair scheduler) is NOT disabled or bypassed by any stage of this rollout. It continues running unconditionally as reconciliation/fallback coverage — this is already true in the current Phase 2 implementation (no code change ties `subprov_sweep_pass` to `SUBPROV_WATCH_ENABLED` at all), so no further change is needed to satisfy this requirement; it is a property of the existing design, confirmed by inspection.

## Monitoring thresholds (to watch during any live stage)

- FD count on the cascade process — alert if it trends upward without bound (the exact regression class the X21D.3 incident already taught this codebase to watch for).
- WS subscription confirmation latency (`_ack_latencies` ring buffer, already instrumented in `SubscriptionManager`) — a rising trend would indicate the connection approaching a provider-side limit.
- `sweep_never_swept_gauge` / `sweep_expiring_60s_never_swept_gauge` (Phase 2's new metrics) — should trend toward zero as live coverage genuinely reduces sweep dependency, not just shift the same problem.
- Unsubscribe failures/timeouts — a rollback trigger if a session's cleanup is silently failing (leaking a server-side subscription).

## Rollback procedure

At every stage: set `WS_SUBPROV_WATCH_ENABLED=0` (or the relevant cohort-control env var) back to its disabled value and restart the cascade daemon. Because the sweep path is architecturally independent and always active, disabling the WS tier at any point returns the system to exactly its current, already-proven-functional state (RPC sweep as sole live detector) — there is no code path where disabling the WS tier leaves detection worse off than today.
