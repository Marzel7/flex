# WATCHTOWER Provisioning-Hub Pattern — Investigation & Reference

**Date:** 2026-06-04
**Status:** Confirmed on-chain (Helius RPC). Deterministic, evidence-based.
**TL;DR:** The WATCHTOWER operation launches tokens through a **fleet of ephemeral provisioning hubs**, not a single wallet. The real pre-CREATE signal is `TREASURY (700/800 SOL) + dual signaller pings → hub → fee-sized creator seed → CREATE (+1s)`, with a **~150s median lead before CREATE**. The current ARMED interception watches *recipient* wallets, which only surface the **post-CREATE** trader-swarm leg — it is watching the wrong layer. **Proven real-time-actionable (§6b):** at T2 (both signallers in, ≤23s after treasury) the hub is already uniquely identifiable as a fresh wallet, with **53–538s (median 132s) of warning before the creator seed** — recall 8/8 (100%; bounded re-verification recovered 2 launches the census missed, for **8 confirmed launch hubs**), strict precision 8/11 (73%), or 8/9 (89%) excluding not-yet-acted reserve hubs, and **zero** unrelated-wallet false positives. The bare treasury+dual-signaller rule is necessary-not-sufficient (one 700/800 dual-signaller hub distributes to swarms without seeding a creator); combining it with the fee-sized creator-seed leg is the precise distinguisher.

---

## 1. Background — how we got here

This started from a question about the "ignition pattern" (TREASURY large SOL + two 0.00001 SOL dust signallers → same recipient within seconds). Investigation established, in order:

1. **The ignition pattern fires AFTER CREATE, not before.** For TRUMPBIBI, the cited TREASURY 70-SOL + dual-signaller → `6SRRTV` event was at 18:01, **~21 minutes after** the 17:40:14 CREATE. `6SRRTV` was a fresh wallet that immediately fanned out to trader wallets that **bought** the token. → It's **campaign activation**, not a launch precursor.

2. **Tracing the creator's funding upstream revealed the REAL precursor.** The TRUMPBIBI creator was funded ~1s before CREATE via single-use burner relays (`J3FhMQWw` → `J6Js5jux`) that trace back to a hub, **HS9NA3E**, which TREASURY had funded 700 SOL + dual signallers ~67s earlier.

3. **A forward census from TREASURY proved HS9NA3E is one of a FLEET** — see results below.

---

## 2. The confirmed pattern (per launch)

```
  TREASURY (44orWS68…)
        │  700 or 800 SOL
        ▼
  PROVISIONING HUB  ◄── SIGNALLER_1 (44orA1Bx…) 0.00001 SOL dust
  (ephemeral)       ◄── SIGNALLER_2 (44o1Hecb…) 0.00001 SOL dust
        │
        ├── fee-sized seed (0.005–0.35 SOL, often via burner relay) ──► CREATOR ──► CREATE (+1s)
        │
        └── (Phase 2, ~50s+ later) large fan-out ──► trader-swarm wallets ──► coordinated BUYs
```

**Two distinct tiers off the same machinery:**
| Tier | TREASURY amount | Behavior |
|------|-----------------|----------|
| **Launch tier** | **700 / 800 SOL** | hub seeds a **CREATOR** → CREATE +1s |
| **Swarm tier** | **~70 SOL** | hub fans out to **180–1686 trader wallets** (no creator) |
| Pass-through | 150–250 SOL / non-dual | no fanout |

---

## 3. Census results (forward from TREASURY, 29 recent hubs)

Method: TREASURY's recent ~300 txs → 30 large (≥50 SOL) outflows → 29 distinct hubs → paginated per-hub analysis (dual-signaller? fanout? fee-sized creator-seed → CREATE?). **Validated against HS9NA3E before trusting results.**

| Metric | Count |
|--------|-------|
| Hubs analyzed | **29** |
| Dual-signaller hubs | **22 / 29 (76%)** |
| Active-fanout (>20) hubs | **16 / 29** |
| **Creator-seeding hubs (FULL pattern)** | **8 / 29** (6 in census + 2 recovered by bounded re-verify, §6b) |

### The 8 confirmed launch-provisioning hubs (each = one launch)

| Date | Hub (prefix) | TREASURY | Creator | CREATE lag | Mint |
|------|-------------|----------|---------|-----------|------|
| 05-30 09:23 | `2ujRcf1fwQ` | 800 | `6NV84W76QU` | seed 0.1121 | *(recovered §6b)* |
| 05-30 22:47 | `FFgRdyPkAR` | 800 | `CM4w4kciqv` | +1s | `DuZ8NT1jvCVm…` |
| 05-31 03:20 | `8p4rdS8CnV` | 800 | `FBg2gWHkyk` | seed 0.1121 | *(recovered §6b)* |
| 05-31 20:52 | `DzRrCaXNDG` | 800 | `8RW8MeyB9A` | +1s | `CUdwRcEH2fqE…` |
| 06-01 00:02 | `5U1YLtzw2k` | 800 | `DCwymCaLxh` | +1s | `CKgYLy6Ag2Ry…` |
| 06-03 05:59 | `7wCgSrbpaS` | 700 | `FGAjqetgFW` | +1s | `8AxtgGcY26DA…` |
| 06-03 09:07 | `596MHACdVh` | 700 | `8JRLpzLU` | +1s | `836gYoqQy6aG…` |
| 06-03 17:39 | `HS9NA3EThE` | 700 | `AsTY4Y1DRm` | +1s | `5657kvM8…` (TRUMPBIBI) |

> 8 is a **floor, not a ceiling** — the 2 recovered hubs show the census undercounts (newest-first pagination misses seeds, §8). Remaining 800-SOL dual-signaller hubs not yet tied to a launch are reserve/dormant or swarm-tier. The true launch count in this window is ≥8.

### Pre-CREATE lead time (TREASURY injection → CREATE)
`67s, 125s, 143s, 155s, 276s, 560s` — **median ~150s.** This is the interceptable window.

### Swarm-tier fanout volumes (70-SOL hubs, dual-signaller, no creator)
`HhtkwuxR` 1686, `29j7x6YZ` 1004, `AQMVxjaF` 392, `5qdiGGHX` 262, `6vUVNf3z` 227, `6SRRTV` 225, `BQqiKgh6` 194, `D2v34sQa` 193, `GDZ4HYj1` 184 — these are coordinated buy-armies.

---

## 4. Verdict on the original question

| Question | Answer |
|----------|--------|
| Is HS9NA3E a one-off or a reusable role? | **Reusable role.** 6 independent hubs, identical signature. |
| Remove HS9NA3E — does the pattern persist? | **Yes** — 5 other hubs show it identically. |
| Unique / rare / common / **dominant**? | **DOMINANT** — it is the operation's standard launch mechanism. |
| Pattern confidence | **Very high** — 6 hubs, identical TREASURY-tier + dual-signaller + creator-seed + CREATE-in-+1s, across 4 days, validated detection. |

---

## 5. Key wallets

| Role | Address |
|------|---------|
| TREASURY | `44orWS68MqXG198M3YXyZoNrYtsNhgnNhtUT5SavqJFM` |
| SIGNALLER_1 | `44orA1BxQfFaX2iMjRbWstoqqWBE7ag8BD93ikxR4JFM` |
| SIGNALLER_2 | `44o1Hecb4QUhqcRNYJBC6XZoeHWzkWAvenR5YYHRGbFM` |
| Hub (example, TRUMPBIBI) | `HS9NA3EThE5txxtu8Ke64HB16hKoiQqaPo4Vu8Fh1YPX` |

Hubs are **ephemeral** (HS9NA3E lived 9.1h, rotates per launch). **Detect the ROLE/signature, not static addresses.**

---

## 6. Interception architecture implications

| | Current (recipient-based) | Proposed (hub-based) |
|--|---------------------------|----------------------|
| Watches | TREASURY+signaller → **recipient**, arm on recipient | TREASURY 700/800 + dual signaller → **hub**, then hub's creator-seed outflow |
| Catches | **Post-CREATE** swarm leg (token already exists) | **Pre-CREATE** seed (~1s before CREATE) |
| Lead time | negative (after launch) | **~150s median** |
| Creator detection | misses (recipient ≠ creator) | direct (hub→creator edge) |
| False-positive control | arms on any recipient | gated on 700/800-tier + dual-signaller + fee-sized seed — tight, specific |

**Proposed detection rule (for a future build task):**
1. Detect **TREASURY 700/800-SOL outflow + both signaller dust pings to the same wallet** (the hub-arming event) — fires ~150s before CREATE.
2. Monitor that hub's outflows for the **fee-sized creator-seed leg** (0.005–0.35 SOL to a fresh wallet, possibly via a burner relay).
3. That fresh wallet is the creator → CREATE within ~1s. Arm/act in the window between hub-arming and the seed.

---

## 6b. Real-time detectability — is the pattern actionable EARLY ENOUGH? (2026-06-04)

The pattern's *existence* is proven. This section answers the operational question: **at T2 (both signallers landed), was the hub already identifiable, and how much time remained before the creator seed (T3)?** That T2→T3 gap is the actual interception window.

### Per-launch T0–T4 timeline (reconstructed on-chain)

T0 = treasury injection · T1 = signallers · T2 = both signallers in · T3 = creator seed · T4 = CREATE

| Hub | T0 (treasury) | T0→T2 | **T2→T3 (window)** | T2→T4 | T0→T4 | fresh at T2? |
|-----|--------------|-------|--------------------|-------|-------|--------------|
| HS9NA3E | 06-03 17:39:07 | 13s | **53s** | 54s | 67s | yes (0 prior sigs) |
| 5U1YLtzw | 06-01 00:02:05 | 17s | **107s** | 108s | 125s | yes |
| 7wCgSrbp | 06-03 05:59:07 | 23s | **119s** | 120s | 143s | yes |
| 596MHAC | 06-03 09:07:33 | 9s | **145s** | 146s | 155s | yes |
| DzRrCaXN | 05-31 20:52:11 | 20s | **255s** | 256s | 276s | yes |
| FFgRdyPk | 05-30 22:47:26 | 21s | **538s** | 539s | 560s | yes |

### Warning-time statistics (T2 → T3, the usable lead before the creator seed)
- **Average: 203s (3.4 min)** · **Median: 132s (2.2 min)** · **Min: 53s** · **Max: 538s**
- T2→T4 (to CREATE): avg 204s, median 133s, min 54s.
- T0→T4 (full treasury-to-CREATE lead): avg 221s, median 149s.
- **T0→T2 is only ~17s avg** — both signallers arrive within seconds of the treasury injection, so the full signature is complete almost immediately, leaving the bulk of the lead as actionable window.

### Distinguishing characteristics that ALREADY existed at T2 (all 6 hubs)
- **Fresh wallet** — `prior_sigs_before_T0 = 0` for **all 6** (born at the treasury injection; no history).
- **Received 700/800 SOL from TREASURY** at T0.
- **Received BOTH signaller dust pings** (0.00001 SOL) within ~4–23s.
- **No fanout yet** at T2 (hadn't begun distributing).

→ At T2 the hub is a brand-new wallet that just received 700/800 SOL from TREASURY + both signallers and has done nothing else. This is an **extremely specific, rare signature.**

### Rule precision — `TREASURY 700/800 + both signallers + same fresh recipient`
Tested against all 700/800-SOL TREASURY hubs in the sampled window (11 rule matches). The 5 census "no-creator" candidates were re-verified with a **bounded T0→T0+30min outflow scan** (the original census missed seeds via the newest-first pagination gotcha — see §8; an earlier full-life scan stalled on the rate-limited shared RPC). Verified results:

| Hub | TREASURY | Window out_txs | Verdict |
|-----|----------|---------------|---------|
| `HzXXtXSWFg` | 700 | 0 | **RESERVE/DORMANT** (never distributed) |
| `buYSusFieX` | 800 | 0 | **RESERVE/DORMANT** (never distributed) |
| `FuwcJ6f6` | 800 | 1617 | **ACTIVE no-creator** (swarm-tier fanout, no CREATE) |
| `8p4rdS8CnV` | 800 | 507 | **TRUE POSITIVE (missed)** — seed 0.1121 SOL → creator `FBg2gWHkyk` → CREATE 05-31 03:20:01 |
| `2ujRcf1fwQ` | 800 | 675 | **TRUE POSITIVE (missed)** — seed 0.1121 SOL → creator `6NV84W76QU` → CREATE 05-30 09:23:28 |

**2 of the 5 "FP candidates" were actually missed launches** — raising the confirmed launch-hub count from 6 to **8**. (Both recovered TPs used the *identical* 0.1121 SOL seed amount — a recurring fee-seed fingerprint.)

**Final tally on the 11 rule matches:**
- **True positives (seeded a creator → CREATE): 8** (6 original + 2 recovered)
- **Reserve/dormant (armed, never distributed): 2** (`HzXXtXSWFg`, `buYSusFieX`)
- **Active no-creator (swarm-tier infra): 1** (`FuwcJ6f6`)
- **Genuine false positives (unrelated wallet): 0**

**Precision (verified):**
- **Strict: 8/11 = 73%** (treating the 2 reserves + 1 swarm hub as non-launch).
- **Excluding dormant/reserve (hadn't acted yet): 8/9 = 89%** among hubs that actually distributed — the lone non-launch distributor is `FuwcJ6f6`.
- **Mis-fire rate on genuinely-non-WATCHTOWER wallets: 0** — every one of the 11 matches was a treasury-funded dual-signaller hub, i.e. WATCHTOWER infrastructure.
- **Recall: 8/8 = 100%** — every confirmed launch hub matched the rule.

Key nuance on false positives: the non-launch matches are **not** unrelated wallets — they are **reserve/dormant hubs** (hadn't seeded yet) or a **swarm-tier distribution hub** (`FuwcJ6f6`, fans out to a buy-army without ever seeding a creator). So the bare `treasury 700/800 + dual signaller` rule reliably flags WATCHTOWER infrastructure (mis-fire rate ~0), but to specifically isolate a *launch* it must be combined with the **fee-sized creator-seed leg** (the T3 confirmation) — which is exactly the actionable distinguisher and still leaves the full T2→T3 warning window to act in.

### VERDICT: Could the system have identified the hub BEFORE the creator seed?

**YES — with substantial lead time.** At T2 (≤23s after the treasury injection), each hub was already uniquely identifiable as a fresh wallet with the 700/800-SOL + dual-signaller signature, **before any creator seed**. For the 6 hubs with reconstructed T0–T4 timelines, the available pre-seed window (T2→T3) was **53–538s, median 132s, minimum 53s** — every launch left at least ~1 minute of warning, most left 2–9 minutes. (Recall is **8/8** across all confirmed launch hubs; the 2 recovered via the bounded FP scan weren't timeline-reconstructed but share the identical signature.) The pattern is **detectable early enough to be operationally actionable.**

Caveat: the tightest case (HS9NA3E, 53s) shows the window can be under a minute, so detection-to-action latency must be low. But even the minimum gives more lead than the current recipient-based approach, which fires *after* CREATE.

---

## 7. Related findings (context)

- **Ignition pattern is post-CREATE** (campaign activation, not precursor) — the `6SRRTV`-style recipients.
- **WATCHTOWER classifier blind since 05-08** — none of these June hub-launches are classified WATCHTOWER; TRUMPBIBI isn't even a watch candidate. The hub mechanism is a **new, unclassified behavior** (the operation evolved its launch method after early May).
- **April WATCHTOWER launches (59 traced) showed 0 hubs** — genuine negative (infra was active then); the hub pattern is a **newer** mechanism, so the old classified set can't be used to test it. The forward-from-TREASURY method is the correct approach.

---

## 8. Method notes / gotchas (for reproducing)

- **`getSignaturesForAddress` is newest-first.** These hubs are high-volume (HS9NA3E = 3705 sigs / 9h); the relevant pre-CREATE activity is NOT in the most-recent N sigs. **Paginate to the injection time window** or you get false "no hub / fanout=0" results. This bug produced multiple false-0 results before being caught.
- **Always validate a negative trace against a known-positive case** (HS9NA3E → AsTY4Y1 → CREATE) before trusting it.
- **The shared Helius RPC key is rate-limited** (the live app uses it too). Run **sequential with ~0.05s spacing**, not concurrent — parallel requests drop 60%+ silently.
- `wt_creator_launches` is **NOT** a WATCHTOWER set — it's Relay.link/CEX-funded launches attributed to disproven provisioners. Do not use it as ground truth.

---

*Generated from on-chain transaction evidence. All timings are Solana block times (UTC). No inference beyond transaction history.*
