# Subprov Distribution Tier + WebSocket Launch Coverage

**Date:** 2026-06-18
**Scope:** (1) the confirmed subprov-funds-subprov distribution model, (2) honest assessment of real-time WS launch coverage — *are we missing launches?*

---

## Part 1 — The Distribution Tier (subprovs fund other subprovs)

### Confirmed model (on-chain verified)
```
root treasury  →  distribution subprov  →  subprov  →  wrap-close creator  →  launch
```

**SUBPROV role is NON-EXCLUSIVE.** A single wallet can be *both*:
- a **subprov** (wrap-closes to creators), **and**
- a **funder of other subprovs** (the distribution tier),

…without being promoted to a treasury. This was verified on-chain (temp Helius key,
getSignatures + getTransaction only):

- **Efm1jBsiGv8k** wrap-closes to creators (real subprov) **and** funds 31 downstream
  subprovs — 10/10 sampled downstream wallets were themselves real wrap-close subprovs.
- **Dtwi** is a treasury/distribution node, *not* a subprov: its "wrap-closes" are WSOL
  self-wrap/unwrap (close.destination = itself, not a creator) — i.e. trading, not
  provisioning. Correctly excluded.
- The chain `5JWii73 (treasury) → Dtwi (treasury) → Efm (subprov) → 31 subprovs` is the
  treasury-mesh + distribution tier in one.

### Funding purpose: NEW token vs TOP-UP (verified, 4 nodes)
A distribution subprov funds its fleet in **two modes**, with a clean amount signature:

| Mode | Amount signature | Meaning |
|------|------------------|---------|
| **TOP-UP** (majority) | **0.001◎ dust** to already-active subprovs | keep-alive / heartbeat |
| **PROVISION** (one bulk load) | **single large transfer, 84–1,183◎** | working/seed capital |

Efm: 28 dust top-ups + 3 provisioning (incl. 1,057◎). Verified across Efm, 6N1Y5W5Z
(1,183◎), 5X97NRox (84◎), F93YD4ac (254◎). **Rule:** edge amount ≤0.002◎ = TOP_UP;
≥~2◎ to a fresh recipient = PROVISION. Zero-RPC derivable from `wt_ops_v2_edges.amount_sol`.

**Distribution nodes are COMMON, not rare** — every busy subprov sampled funds other
subprovs (4/4, 6/6, 2/2 of sampled recipients were subprovs). Local data just never
traced it (the pipeline traces *upward* only and labels every funder a "treasury").

### What shipped (commits 8aa10f3, c767f6c, dc9eb55, de7440b)
- **Schema** (`wt_discovered_subprovs`): added `immediate_funder`, `funder_is_subprov`
  (additive). `treasury` (root) untouched — no re-rooting.
- **Derivation** (`src/core/subprov_distribution.py`): `mid_tier_subprovs()` —
  read-only, **mechanism-guarded** (both ends must be real wrap-close producers, which
  filters out the ~5,615 raw mid-chain nodes that are collectors/sweep/swarm).
- **API** (`/api/ops-v2/intel/subprovs`): per-subprov `immediate_funder`/`funder_is_subprov`
  + `distribution_nodes` block.
- **UI**: 🪢 DISTRIBUTION banner + per-row "🪢 via <funder>" badge.

**Open follow-up:** the pipeline only traces *upstream*; to populate the distribution
tier broadly it must trace a subprov's *downstream* fan-out (not built). The Efm tier is
real because seeded from the verified on-chain trace. New/top-up edge classification is
scoped but not yet built (verify-first complete; build is the open next step).

---

## Part 2 — WebSocket Launch Coverage: ARE WE MISSING LAUNCHES?

### Short answer
**Launches are being caught — but almost entirely by the post-migration FARM DETECTOR,
not the real-time WS cascade.** The cascade ledger shows 0 in 24h despite ~36+ real
WATCHTOWER launches. The cascade isn't broken; it has a **structural coverage boundary**.

### The 24h numbers
| Source | WATCHTOWER launches caught (24h) |
|--------|----------------------------------|
| **Farm detector** (`wt_farm_launches`, post-migration) | **36** (130 farm launches, PLAIN_XFER) |
| Discovery (`wt_discovered_subprovs`) | 2 |
| **Real-time WS cascade** (`wt_watchtower_launches`) | **0** |
| Migration listener (broad capture) | 86 migrations total (all captured) |

Concrete example: **FAouag…pump** — $99.8k peak, migrated ~6h ago, classified WATCHTOWER.
Funder `5V9vUiB1…`. It is **NOT** in `wt_watchtower_launches`, **NOT** in
`wt_wrap_close_candidates`. It was attributed by the **farm detector** after migration.

### Why the real-time cascade misses them (root cause)
1. **Mechanism boundary — the dominant reason.** These operations fund creators via
   **PLAIN_XFER** (plain SOL transfer), not the WSOL **wrap-close** cycle. The entire
   real-time cascade is built to detect the wrap-close mechanism, so **plain-transfer
   farms are structurally invisible to it.** 130/130 recent farm launches are PLAIN_XFER.
2. **Not subscribed.** FAouag's funder is not a confirmed/webhooked treasury — only
   **1 of 218** farm-launch funders is webhooked, so the cascade WS isn't watching them.
3. **All 70 wrap-close candidates in 24h were rejected as BUY_SWARM** (0 armed). The
   buy-swarm filter is doing its job on swarms, but with real launches confirmed present,
   it warrants an audit to ensure it isn't *also* over-rejecting genuine wrap-close
   creators (see Open Questions).

### Current WS connectivity state (live)
- `ws_cascade` daemon: **running**, WS **connected** (`✓ WS connected wss://mainnet.helius-rpc.com/`).
- **0 active sessions, 0 candidate watches** right now; last wrap_close 23h ago, last
  create 27h ago — consistent with "no qualifying *wrap-close* activity," not a dead WS.
- **260 active subprov sessions** + **12 webhooked treasuries** in the subscription set.
- **DEGRADATION: ~390 cascade event-write failures** (`ws_cascade_store.py:211`,
  "database is locked") over the window — cross-process write contention on the hot DB.
  265 were `TREASURY_WEBSOCKET_OPENED` (telemetry), but **17 `WRAP_CLOSE_FANOUT_DETECTED`**
  and 21 `SUBPROV_SESSION_OPENED_WS` failed — those ARE on the detection path. Most
  retries succeed (events table is being written), but some detection events are lost.

### CORRECTED conclusion (premise: every SUBPROV-funded token is wrap-close — confirmed)
An earlier draft blamed a "PLAIN_XFER coverage boundary." That was WRONG. Re-verified:
- **Subprov-funded launches ARE wrap-close.** FAouag's funder `5V9vUiB1` is a confirmed
  wrap-close subprov (11 wrap-closes). The "PLAIN_XFER" farm launches are funded by
  `5tzFkiKsc`/`9obNtb5G` — wallets that are NOT subprovs (0 in wrap-close/discovered
  tables); they're a SEPARATE non-WATCHTOWER operator class (plain-transfer + advanceNonce
  durable-nonce) that the mechanism-agnostic farm detector also clusters. So PLAIN_XFER is
  not a WATCHTOWER coverage gap at all.

**THE REAL ROOT CAUSE — the buy-swarm filter over-rejects real creators.** Traced FAouag
minute-by-minute in `watchtower_events`:
- 11:57 treasury_funded (DchJquEZ→5V9vUiB1) → 11:59 FORWARD_WALK_STARTED →
  12:01:41 SUBPROV_SESSION_STARTED. The cascade was subscribed AND watching.
- FAouag migrated **12:03:20 — inside the active session window** (11:57→12:13).
- At that exact second the cascade logged a **burst of ~23 BUY_SWARM_REJECTED** wallets.
  5V9vUiB1's same-instant wrap-close fan-out was classified as a buy-swarm and the WHOLE
  batch rejected — **including the real creator FCdghVKg** (which has NO candidate event
  at all — never recorded). 5V9vUiB1 is marked `state=BUY_SWARM` (11 rows).
- The farm detector then caught FAouag ~11 min post-migration (RPC funder trace,
  wrap_close=1, seed 5.15◎) as the backstop.

So: the real-time cascade MISSED a genuine wrap-close subprov launch that was fully within
its coverage (subscribed, active session, right mechanism) because the **[[buy-swarm-vs-creator]]
same-instant-fan-out rule swept the real creator into a rejected swarm batch.** This is a
classification FALSE-NEGATIVE, not a coverage boundary or (primarily) the DB-lock issue.

### MEASUREMENT PASS (supersedes the buy-swarm theory above)
A 7-30d measurement DISPROVED the buy-swarm false-negative hypothesis. Numbers
(watchtower_events + wt_farm_launches):
- 322 real launch creators. Only **9 (2.8%) had ANY cascade event**; **0 were BUY_SWARM_REJECTED.**
- 695 BUY_SWARM_REJECTED / 681 wallets — **0 became a farm-launch creator** (0 false negatives).
- FAouag misled: its creator was NEVER DETECTED, not rejected.
- 218 launch funders (subprovs); only **21 (10%) ever had a cascade session**; **196 unwatched.**
- Subscription set: 17 confirmed treasuries, **12 webhooked**. Real launches come from a far
  larger subprov set the WS never watches.

**ROOT CAUSE = SUBSCRIPTION COVERAGE, not classification.** The cascade can only detect a
wrap-close from a subprov it subscribes to. Baseline real-time detection = **2.8% (9/322)**.

### Next steps (prioritised by measurement)
1. **#1 — close the subscription gap.** 34 blind-spot launch funders are already in
   wt_discovered_subprovs, **33 with treasury_known**, but were never promoted to live WS
   subscription. Promote discovered+treasury_known subprovs → live watch set. Staged:
   Phase 1 the 33 known; measure detection %; Phase 2 review/auto-confirm path for the rest;
   Phase 3 broader expansion.
2. **Secondary (separate) — fix `ws_cascade_store.py:211` lock failures.** Even watched
   subprovs lose some detection events to DB locks (FAouag's subprov was watched yet missed).
   Do NOT mix with subscription promotion.
3. **NOT a gap — buy-swarm (0 false negatives)** and **PLAIN_XFER (separate operator class).**
   Do not touch buy-swarm logic; do not add PLAIN_XFER logic.
