# Pre-Launch Creator Detection — Issue Summary & Webhook Targeting

## The opportunity

A migrated creator's wallet receives an exact **`1.112039280 SOL`** template transfer
(`1.11` + ATA rent) **~58–60 minutes before it launches a token** (validated: N=25
creators, avg 58 min, median 59, range 52–60 — extraordinarily consistent). If we detect
that funding event in real time, we know the creator address with ~1 hour of lead time.

**Goal:** make it *very visible* the moment a creator is funded and about to go.

## The core problem: detection latency

The forward monitor (RPC polling every ~3 min) detects template funding **40 min – 11 h
after it happens on-chain** — because the funding pages out of the monitor's tx window
between polls. Result: **of 24 detected template creators, only 1 was caught within the
60-min window, 0 within 30 min.** By the time the monitor sees the creator, it has usually
already launched.

So a "creator about to launch" alert built on the monitor alone would mostly fire on
creators that **already went.** Real-time detection requires a **webhook**, not polling.

## Which accounts to webhook — and which are useless

The creator-funding chain is **single-use at every hop except the treasury:**

```
TREASURY (STABLE)  →  forwarder  →  pass-through  →  CREATOR
                      └──── all single-use, one creator each, then discarded ────┘
```

Evidence:
- Every confirmed launcher was funded `1.112039280` by a **PASS_THROUGH** wallet.
- Each pass-through funded **exactly 1 creator**, then died. Webhooking pass-throughs /
  forwarders / creators is pointless — they're gone after one use.
- The **treasury** is the only persistent wallet: it keeps firing (some every ~30 min) and
  survives for days/weeks. It is the only viable real-time trigger.

**Webhook target = the treasury roots** (7 tracked in `wt_ops_v2`). A treasury event fires
the instant a creator-provisioning starts; a short RPC chain-follow (2–3 hops) then
resolves the actual fresh creator address with the full ~60-min window intact.

## TX VOLUME — what level of traffic these accounts produce

Measured over 24 h via RPC (`getSignaturesForAddress`). **The treasuries split into two
sharply different volume classes:**

| Treasury | TX / 24h | Class | Webhook viable? |
|---|---|---|---|
| `yUpm7rKXPs…` | **~32** | clean provisioner | ✅ yes |
| `Cgwr5FAa6d…` | **~49** | clean provisioner | ✅ yes |
| `43PKjr22AF…` | **~59** | clean provisioner | ✅ yes |
| `2o8cW7kKvE…` | **~10,000+** | firehose | ⚠️ floods listener |
| `6EPrTWaVKy…` | **~10,000+** | firehose | ⚠️ floods listener |
| `DgSfU5gFe9…` | **~10,000+** | firehose | ⚠️ floods listener |
| `EtyBB1yap2…` | **~10,000+** | firehose | ⚠️ floods listener |

(The 10,000 figures hit a 10-page pagination cap — actual volume is likely higher.)

**TX type:** sampled high-volume treasuries are **100% `TRANSFER`** — pure SOL movement
(trading/sweep/distribution). The `1.112039280` creator-funding events are **interleaved
and infrequent** relative to the bulk transfers (0 in a recent 100-tx sample of the busy
treasuries) — they're a needle in the transfer haystack.

### Implication for webhook design
- **Webhooking all 7 treasuries indiscriminately = ~40,000+ webhook hits/day**, of which
  the vast majority are noise (non-template transfers). The listener + downstream
  processing must filter to **only `nativeTransfer.amount == 1.112039280`** (and the other
  library templates) and ignore the rest.
- **Cheap win:** the 3 low-volume provisioner treasuries (`yUpm`, `Cgwr`, `43PKjr22`) total
  **~140 tx/24h** combined — webhooking just these is nearly free and likely captures the
  cleanest provisioning streams. The 4 firehoses need server-side amount-filtering or should
  be webhooked with a tighter Helius `transactionTypes`/account filter if supported.

## Current webhook state (blocker)
- The candidate webhook config was pointed at a **dead Helius webhook** (`fec2b429`, 404) —
  now repointed to the live webhook **`106e20f6`** (ngrok `…/api/webhook/watchtower`, 17
  addresses) via `CREATOR_MOVEMENT_WEBHOOK_ID` env.
- **The listener is receiving 0 hits** (last `wt_webhook_hits` was days ago). Even with the
  config fixed and treasuries enrolled, **events are not currently flowing to
  `/api/webhook/watchtower`** — this must be diagnosed before real-time detection works.

## Chain-structure investigation — why "webhook the treasury" alone is insufficient

On-chain trace of a known launch (creator `8UQ35j29`, operation `5c31cdb5`, treasury
`yUpm`), following the `~1.11` template transfers UP:

```
creator 8UQ35j29  <- 1.112  EgB7X3NY (pass-through)
                  <- 1.110  7jFfegDU (pass-through)
                  <- 1.112  BLHxgS4p (COLLECTOR — where the 1.11 relay STARTS)
                  <- [treasury yUpm, via a NON-1.11 transfer]
```

**Two findings that constrain the design:**

1. **The treasury is 3 hops from the creator**, and its outbound to the chain is NOT the
   `1.11` template — it funds a collector with a different amount. So a treasury webhook
   sees *"provisioning is starting"* (early trigger), **not** the creator address. To get
   the creator you must **chain-follow the `~1.11` path down 2–3 hops** after the trigger.

2. **Collectors are NOT a viable webhook target** (investigated as the closer, 2-hop
   alternative). On-chain lifespans:
   - `BLHxgS4p`: 100 txs in **0.1 h (~6 min)** then dead — burst-and-die sweep wallet.
   - `DaP4sh6F`: 100 txs in **0.1 h** then dead.
   - `AVCoBUSX`: 26 h, still active — a rare exception.
   - Most collectors fire for minutes then go quiet. You can't pre-position a webhook on a
     wallet that only exists for ~6 minutes — by the time you detect it to enrol, its burst
     is over.

**Conclusion: the treasury is the ONLY persistent, webhookable wallet in the chain** — but
webhooking it yields a *trigger*, not the creator. The chain-follow is mandatory. There is
no shortcut (collector / pass-through / creator are all single-use).

## The treasury accounts (the only viable webhook targets)

7 treasuries, each rooting one operation. **`Cgwr5FAa` and `yUpm7rKXPs` are the priority** —
both are repeat producers (7 launches each) AND low-volume (cheap to webhook):

| Treasury (full address) | Operation | Launches | TX/24h |
|---|---|---|---|
| `Cgwr5FAa6d39tqJXKgDkxhopgJuuJA6s8bZfZGY9hkTe` | de6473a7 | **7** | 46 🟢 |
| `yUpm7rKXPs7J2NXbBHARGBQ9ajyuYh9Pj1Zudu3f1iz` | 5c31cdb5 | **7** | 30 🟢 |
| `43PKjr22AFXtCMmLtQ1wxYojnjqEB86iFKK5qUYo3y3D` | 8c73b9a0 | 1 | 59 🟢 |
| `EtyBB1yap2TRkgAi1mxbXP4wjTKFX8mZxDrXLXU681Lp` | dfc0c765 | 1 | 10,164 🔴 |
| `2o8cW7kKvEubTQQd7qnZvenwXYSBkpdG6emuNVCuXsBG` | 1becb9ac | 1 | 16,347 🔴 |
| `DgSfU5gFe9BBV5iAYvJT1K6Qs4SeWaLvz46pgMVJJ9hK` | 48e3c407 | 1 | 16,362 🔴 |
| `6EPrTWaVKyZKSuzZz5J9SQktDiWfaFgMjdUNUkyVozuU` | 53fbb375 | 1 | 25,000+ 🔴 |

Hard ~170× volume cliff: the 3 clean treasuries total **135 tx/day**; the 4 firehoses total
**~68,000 tx/day**. Omit the 4 firehoses (especially `6EPrTWaV` at 25k+) from webhooking
until a server-side `1.112039280`-amount filter exists.

**Current state: 0 of 7 treasuries are webhooked.** The page exposes this; the confirm-gated
`POST /api/ops-v2/intel/enroll` can enrol them, but no enrolment has been triggered.

## Recommended path (not yet built)

**The treasury webhook is a TRIGGER, not the creator — the chain-follow (step 4) is the
piece that actually yields the creator address. Webhooking without it gives only a
"provisioning started" signal.**

1. **Enrol the 3 low-volume treasuries** (`Cgwr`, `yUpm`, `43PKjr22`) — ~135 tx/day, clean,
   low risk. Priority: `Cgwr` + `yUpm` (7 launches each). Confirm-gated:
   `POST /api/ops-v2/intel/enroll`.
2. **Fix the listener** — `/api/webhook/watchtower` is getting 0 hits (stale since ~June 6).
   This is the real blocker: even a perfect target list is useless if events don't arrive.
3. **Server-side filter** webhook hits to template amounts (`1.112039280` etc.) — required
   before the 4 firehose treasuries (~68k tx/day) can be added without drowning the listener.
4. **Chain-follow on trigger (MANDATORY)** — treasury fire → RPC-walk the `~1.11` path DOWN
   2–3 hops (treasury → collector → pass-through → creator) → resolve the fresh creator →
   emit `PRE_LAUNCH_CREATOR_DETECTED` with the ~60-min countdown. Without this, the webhook
   never produces a creator address.
5. **Loud, unmissable UI alert** — already built on the Pre-Launch Intelligence console
   (`/watchtower/intelligence`): LIKELY-TO-LAUNCH-NEXT countdowns + NO-WEBHOOK-EVENTS banner.
   It will populate the moment steps 2–4 feed it real-time creators.

## Reference: where the signals live
- Pre-launch creator detection (post-hoc): `wt_operation_activity` (real on-chain
  `block_time`, exact `…039280` template tail) — NOT `creator_funders.first_detected_at`
  (FLEX detection stamp that lags weeks).
- Lead-time / launch confirmation: `wt_ops_v2_creators.migration_time`.
- Webhook enrolment + hits: `wt_webhook_enrollments`, `wt_webhook_hits` (live DB).
- Endpoints: `/api/ops-v2/intel/pre-launch-creators`, `/launch-metrics`,
  `/enrollment-queue` (Tier-0 = pre-launch creators), `/enroll` (confirm-gated).
