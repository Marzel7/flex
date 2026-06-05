# WATCHTOWER Investigation: Has N3 Become a Treasury-Substitute Launch Funder?

**Date:** 2026-06-05 · **Method:** on-chain only (Helius). Every hop verified; no assumed lineage. N3 = `N3TKf3wMBNu8XmZsTSnk2xWQ2LjiGvUJh1ae9Lc3dW7` (labeled SUB_PROV / PROFIT-RELAY-3).

**TL;DR:** **YES — N3 is acting as a TREASURY-substitute launch funder, but currently as a RARE path, not a high-volume lane.** A confirmed launch (`8Dj1Bx…pump`) traces to N3 in 3 verified hops through an N3-funded fresh hub, with **no TREASURY, no signaller dust, no existing hub signature** — invisible to all four detection layers (real, confirmed gap). N3 funded **5 fresh hub-like wallets** in 30d; tracing all 5 found **only 1 confirmed launch (Cgwr5F)** — the other 4 received N3 funding but did not launch within the bounded trace (reservoir-like). **Verdict: confirmed capability + confirmed detector gap, but occasional scale (1 of 5), not a demonstrated dominant second lane.**

---

## Objective 1 — Launch chain verification → **CONFIRMED (HIGH)**

The chain originally proposed (`N3→Cgwr5F→2uQYMZ→3ivVqa→Dp8ouM`) was **mis-traced in the middle** — `2uQYMZ` was born 06-05 15:36, *after* the 14:39 launch, and `3ivVqa` does not fund the creator. Verifying every hop revealed the **true path**:

| Hop | Amount | Time (UTC) | Signature | Verdict |
|-----|--------|-----------|-----------|---------|
| N3 → Cgwr5FAa | 600 SOL | 06-05 14:16:41 | `4KT1oq41…` | ✓ |
| N3 → Cgwr5FAa | 796 SOL | 06-05 14:35 | (2nd injection) | ✓ |
| Cgwr5FAa → Bf1vzHBT | 401.5 SOL | 06-05 | (verified outbound) | ✓ |
| Bf1vzHBT → Dp8ouMv1 (creator) | 1.112039 SOL | 06-05 14:39:20 | (verified inbound) | ✓ |
| Dp8ouMv1 → `8Dj1Bx…pump` | CREATE/migrate | 06-05 14:39:23 | — | ✓ |

- `Cgwr5FAa` = fresh wallet (23 sigs), born 06-05 14:16, **funded entirely by N3** at birth.
- Creator seed = **1.112039 SOL** (the `.112…928` fingerprint family seen on relay-funded creators).
- **Creator reaches N3 in 3 hops.** Structure is identical to the provisioning-hub topology with **N3 in the TREASURY slot.**

**Verdict: CONFIRMED, HIGH confidence.** (Note: the *specific* intermediate wallets in the original report were wrong; the N3→creator linkage is nonetheless real via the corrected path.)

## Objective 2 — N3 infrastructure census (30 days) → **N3 is a major funding hub**

N3 had **2,929 txs in 30d** and made **78 outbound transfers ≥100 SOL**. Most recipients are **high-activity established wallets** (1000-sig cap) — including core infra, which is expected relay behavior:

- TREASURY-UP `6jeT3…` (22,709 SOL), TREASURY `44orWS68…` (4,240), PROFIT-RELAY-4, COLLECTOR-8 → **N3 moves large volume back into core WATCHTOWER infra** (it IS a profit relay).

The launch-relevant signal is the **fresh** recipients (low sig count = recently born, candidate hubs like Cgwr5F):

| Fresh hub | Received | Sigs | First N3-funded | Downstream launch? |
|-----------|----------|------|-----------------|--------------------|
| E3iYtwKUdqqt | 1,790 SOL | 111 | 06-02 02:30 | not found (shallow check) |
| **Cgwr5FAa** | **1,396 SOL** | **23** | **06-05 14:35** | **YES — `8Dj1Bx…pump`** ✓ |
| yUpm7rKXPs7J | 601 SOL | 4 | 06-05 13:58 | not found |
| 43PKjr22AFXt | 390 SOL | 2 | 06-05 13:27 | not found |
| ELEBhBjCSNAg | 390 SOL | 73 | 06-03 15:05 | not found |

→ **5 fresh hub-like wallets** funded by N3 in 30d. **1 confirmed launch** (Cgwr5F). The other 4 are structurally similar (fresh + provisioning-tier funding) but their downstream launches were **not found in a shallow 1-hop check** — Cgwr5F itself only revealed its launch at *2 hops* (via Bf1vzHBT), so these 4 are **unresolved, not cleared**.

## Objective 3 — Shadow-hub discovery

A SHADOW_HUB = fresh wallet + ≥100 SOL from WATCHTOWER infra + creator-linked, but **TREASURY absent, signallers absent**.

| Wallet | Funding source | Amount | Status |
|--------|----------------|--------|--------|
| **Cgwr5FAa** | **N3** | 1,396 SOL | **CONFIRMED SHADOW_HUB** (→ launch) |
| E3iYtwKUdqqt | N3 | 1,790 SOL | CANDIDATE (fresh, provisioning-tier; launch unconfirmed) |
| yUpm7rKXPs7J | N3 | 601 SOL | CANDIDATE |
| 43PKjr22AFXt | N3 | 390 SOL | CANDIDATE |
| ELEBhBjCSNAg | N3 | 390 SOL | CANDIDATE |

At least **1 confirmed shadow hub**, **4 candidates** matching the signature. None carry TREASURY or signaller markers — all would be invisible to current hub discovery.

## Objective 4 — Detector gap analysis

| Detector | Result | Why |
|----------|--------|-----|
| **Hub Discovery** | **MISS** | Requires TREASURY 700/800 + **dual signaller**. Cgwr5F was funded by **N3** (not TREASURY) with **no signaller dust** → no signature match. |
| **Armed Detection** | **MISS** | Arming requires the TREASURY + signaller ignition event on a fresh wallet. N3-funding produces no such event → never arms. |
| **Lineage (`lineage_to_infra`)** | **MISS** | The creator reaches N3 at 3 hops (within the cap), but the lineage *did* run and returned negative — because N3 is **not in the launch-infra terminal set** the walk treats as WATCHTOWER-launch. Reaching N3 doesn't flag a launch. |
| **Attribution** | **MISS** | N3 is classified **PROFIT-RELAY-3 / SUB_PROV** (extraction/relay), **not** launch infrastructure. So even a hit on N3 reads as extraction lineage, not a launch. (This is the root mislabel: N3 is BOTH a profit relay AND, now, a launch funder.) |

**Root cause:** every layer keys on **"TREASURY + signaller"** as the launch signature, and N3 is typed as **extraction-only**. A launch funded by N3 has neither marker and the funder is mis-typed → 4/4 miss.

## Objective 5 — New operational lane vs anomaly → **probable new lane**

| Claim | Confidence | Basis |
|-------|-----------|-------|
| **N3 funded this creator** (`Dp8ouM`/`8Dj1Bx`) | **CONFIRMED (HIGH)** | 3 hops, every edge verified on-chain |
| **N3 is launch infrastructure** | **HIGH** | Funded a fresh hub (Cgwr5F) that produced a confirmed launch with provisioning-tier amounts |
| **Detector gap confirmed** | **CONFIRMED (HIGH)** | 4/4 detectors miss, mechanism explained and verified |
| **N3 operates multiple launch hubs** | **LOW** (revised down) | 5 fresh provisioning-tier hubs funded in 30d, but **the 4 candidates were traced 2-hop and produced 0 confirmable launches**; only Cgwr5F is confirmed. See census update below. |
| **Topology evolution confirmed** | **MEDIUM-LOW** (revised down) | Same hub→creator structure with N3 substituting for TREASURY is **proven once**; it is a real but currently **occasional** path, not a demonstrated high-volume second lane. |

### UPDATE — 4 candidate shadow hubs traced (2-hop, bounded)

| Hub | N3 funding | hop-1 fanout | Launches (2-hop) |
|-----|-----------|--------------|------------------|
| E3iYtwKUdqqt | 1,790 SOL | 15 | **0** |
| yUpm7rKXPs7J | 601 SOL | 1 | **0** |
| 43PKjr22AFXt | 390 SOL | 0 (not distributed) | **0** |
| ELEBhBjCSNAg | 390 SOL | 5 | **0** |

**0 of 4 candidates produced a confirmable launch.** Only **Cgwr5F (1 of 5)** is a confirmed N3→launch shadow hub. The other 4 resemble the **reservoir pattern** (funded, dormant or distributing-but-not-to-creators) — same shape as the 71 relay-funded dormant wallets. So N3 funds *many* fresh wallets; **most do not become launches** (at least not within the traced window).

**Conclusion (revised):** This is **not an isolated anomaly AND not (yet) a high-volume second lane** — it's a **confirmed but currently rare capability.** N3 demonstrably funded one detector-blind launch (Cgwr5F→8Dj1Bx), proving the gap is real; but 4 of 5 N3-funded fresh hubs did not launch, so the lane is **occasional, not dominant.** The detector gap is worth fixing regardless (one invisible launch is a real miss), but the operational scale is small so far.

**Two caveats on the "0 launches" for the 4:**
- The 2-hop trace was **bounded** (6×4 caps, after a stall) — "0" means none in the bounded check, not provably zero. Cgwr5F only revealed its launch at hop 2, so a deeper/wider trace could surface more.
- The 4 are **recent** (funded 06-02→06-05); like the reservoir, they could still launch. Worth watching, not closed.

---

## Recommended next steps (no detector change applied yet — evidence-gathering first)
1. **Watch the 4 dormant N3-funded hubs** for delayed launches (they're recent; add to the reservoir-style watch rather than concluding 0).
2. **Re-type N3** as a dual-role wallet (PROFIT-RELAY **and** LAUNCH_FUNDER) so lineage/attribution can flag N3-funded launches.
3. **Extend hub discovery** to a topology-agnostic signature: *fresh wallet + ≥provisioning-tier inflow from ANY known WATCHTOWER infra (not just TREASURY) + fee-sized creator-seed downstream* — which would catch shadow hubs like Cgwr5F.

*Every hop in the confirmed chain was verified on-chain. Claims about scale (multiple hubs/lane dominance) are marked MEDIUM precisely because only 1 launch is fully traced; the rest are candidates pending deeper verification.*
