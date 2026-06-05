# WATCHTOWER Signaller Census — Investigation & Findings

**Date:** 2026-06-04 · **Method:** on-chain (Helius), birth-anchored census of the 8 confirmed provisioning hubs + a 3-hub non-launch negative set. All amounts are Solana block-time deltas from each hub's true earliest transaction (T0).

**TL;DR:** The signalling layer is **exactly SIGNALLER_1 + SIGNALLER_2**. Both appear at **8/8 (100%)** of confirmed launches at **0.00001 SOL**, within +3–66s of hub birth. **No recurring third signaller exists** — only one single-occurrence near-miss (`44or4i`, 1/8). The 1e-09 "dust" is the **buy-swarm fan-out** (32 distinct one-time wallets), not signalling. **Critically: `TREASURY + S1 + S2` is necessary but NOT sufficient** — reserve and swarm hubs also receive S1+S2 dust without launching, so the dual-signaller signature marks "WATCHTOWER hub armed," not "launch." The launch discriminator remains the fee-sized creator-seed leg.

---

## Q1 — How many unique signaller wallets exist?

Signalling tier (amount ≈ 1e-5 SOL), ranked by hubs hit:

| Wallet | Hubs hit | Pings | Amount | Verdict |
|--------|----------|-------|--------|---------|
| `44orA1Bx…` (SIGNALLER_1) | **8 / 8** | 17 | 0.00001 | CORE |
| `44o1Hecb…` (SIGNALLER_2) | **8 / 8** | 14 | 0.00001 | CORE |
| `44or4iwE…` | **1 / 8** | 1 | 0.0000100690 | single-occurrence candidate |

> S1/S2 ping **repeatedly** at some hubs (FFgRdyPk, 8p4rdS8C: 9 alternating pings each) — same two wallets, multiple sends. There is **no fourth** sender in the signalling tier across all 8 launches.

## Q2 — What constitutes a "signal"? (natural size clusters)

| Amount | Frequency | Distinct senders | Interpretation |
|--------|-----------|------------------|----------------|
| **0.00001** (1e-5) | 31 | **2** (S1, S2) | the signalling layer |
| 0.0000100690 | 1 | 1 (`44or4i`) | near-miss outlier, one hub |
| **0.000000001** (1e-9) | 32 | **32** | buy-swarm fan-out — NOT signalling |

The 1e-9 tier is decisively *not* signalling: **32 distinct senders, each appearing at exactly one hub**, arriving at +118–280s (the post-seed swarm-activation window), in bursts of 5–29 per hub. Zero recurrence = noise w.r.t. the signalling question.

## Q3 / Q5 — Which signallers hit each hub, and launch coverage

| Hub | S1 | S2 | Other (1e-5 tier) | S1 lag | S2 lag |
|-----|----|----|-------------------|--------|--------|
| 2ujRcf1fwQ | ✓ | ✓ | — | +5s | +14s |
| FFgRdyPk   | ✓ | ✓ | — | +5s | +21s |
| 8p4rdS8C   | ✓ | ✓ | — | +6s | +14s |
| DzRrCaXN   | ✓ | ✓ | — | +4s | +20s |
| 5U1YLtzw   | ✓ | ✓ | — | +3s | +17s |
| 7wCgSrbp   | ✓ | ✓ | `44or4i` (+14s) | +5s | +23s |
| 596MHAC    | ✓ | ✓ | — | +5s | +9s |
| HS9NA3E    | ✓ | ✓ | — | +4s | +13s |

| Signaller | Launch coverage |
|-----------|-----------------|
| **S1** | **8/8 = 100%** |
| **S2** | **8/8 = 100%** |
| `44or4i` | 1/8 = 12.5% |

**Both S1+S2 present at 8/8 = 100%.** Timing: S1 lands +3–6s, S2 +9–23s after hub birth — both well before the creator seed (T3, typically +50–540s, per the provisioning-hub investigation).

## Q4 — Are signallers emitted by sub-provisioners? Funding ancestry

| Signaller | Address construction | Funding history | Parent role | Confidence |
|-----------|---------------------|-----------------|-------------|------------|
| S1 `44orA1Bx…JFM` | `44or` prefix / `FM` suffix vanity | known WATCHTOWER infra | WATCHTOWER (registry) | CONFIRMED |
| S2 `44o1Hecb…FM` | same vanity scheme | known WATCHTOWER infra | WATCHTOWER (registry) | CONFIRMED |
| `44or4i…JCnCFM` | **same `44or`/`FM` vanity scheme** | 22 sigs over a long lifespan; 0.000905881-SOL round-trips with varied counterparties (`DDY9dMkg`, `9AB8BVgT`, `Mp48HhQK`) — relay-like, not a dedicated dust emitter | unresolved (vanity suggests WATCHTOWER, behavior suggests relay) | **CANDIDATE only** |

No evidence of `TREASURY → SUB_PROVISIONER → SIGNALLER`. S1/S2 emit dust directly. `44or4i` shares the operator's vanity address scheme (narrative similarity) but appears at **only one hub** — by the evidence-first constraint, address resemblance is **not** sufficient to classify it a signaller.

## Q6 — Can hub confirmation be generalized? (recall vs precision)

**Recall** (against the 8 confirmed launches) — every rule is 100%, because S1 and S2 are each independently present at all 8:

| Rule | Recall |
|------|--------|
| TREASURY + S1 + S2 (current) | 8/8 = 100% |
| TREASURY + S1 only | 8/8 = 100% |
| TREASURY + S2 only | 8/8 = 100% |
| TREASURY + (S1 OR S2) | 8/8 = 100% |
| TREASURY + any 2 / any 1 sig-tier | 8/8 = 100% |

**Precision** (against a non-launch negative set — dual-signaller hubs that never seeded a creator):

| Non-launch hub | Type | S1 | S2 | `T+S1+S2` verdict |
|----------------|------|----|----|-------------------|
| HzXXtXSWFg | reserve (fan=0) | ✗ | ✗ | correctly rejects |
| buYSusFieX | reserve (fan=0) | ✓ | ✓ | **FALSE POSITIVE** |
| FuwcJ6f6 | active swarm, no creator | ✓ | ✓ | **FALSE POSITIVE** |

→ **`TREASURY + S1 + S2` does not distinguish a launch from a reserve/swarm hub.** The dual-signaller signature means "a WATCHTOWER hub just armed," not "a launch is imminent." This corroborates the provisioning-hub finding: the same machinery serves launch / reserve / swarm tiers; the **fee-sized creator-seed leg (0.005–0.35 SOL → fresh creator → CREATE +1s)** is what actually marks a launch.

---

## Deliverable B — Coverage Report (the 5 answers)

1. **How many signallers exist?** Two confirmed (S1, S2). One single-occurrence candidate (`44or4i`). No others.
2. **Do S1 and S2 cover all launches?** **Yes — 8/8 = 100%.** Both present at every confirmed launch.
3. **% of launches missed if only S1/S2 monitored?** **0%.** Monitoring S1+S2 misses no confirmed launch in this set.
4. **Are sub-provisioners acting as signallers?** **No evidence.** S1/S2 emit dust directly; no TREASURY→sub-prov→signaller chain observed.
5. **Optimal live detection rule?** S1/S2 are the right signaller set, but **dual-signaller alone over-fires** (reserve + swarm hubs trip it). The precise launch rule is:
   > `fresh wallet + TREASURY 700/800 SOL + (S1 AND/OR S2 dust) at T0` → **arm/watch**, then **confirm on the fee-sized creator-seed outflow** before treating as a launch.
   Use S1 OR S2 (not strictly both) for the *arming* signal — both hit 100% individually, so requiring both adds no recall and slightly raises miss-risk if one ping is dropped/delayed.

## Deliverable A — Proposed `wt_signallers` registry

| address | role | confidence | first_seen | last_seen | launch_hits | hub_hits | parent_role | status |
|---------|------|-----------|-----------|-----------|-------------|----------|-------------|--------|
| `44orA1BxQfFaX2iMjRbWstoqqWBE7ag8BD93ikxR4JFM` | signaller | 1.0 | 2ujRcf (05-30) | HS9NA3E (06-04) | 8 | 8 | WATCHTOWER | **CORE_SIGNALLER** |
| `44o1Hecb4QUhqcRNYJBC6XZoeHWzkWAvenR5YYHRGbFM` | signaller | 1.0 | 2ujRcf (05-30) | HS9NA3E (06-04) | 8 | 8 | WATCHTOWER | **CORE_SIGNALLER** |
| `44or4iwE1TfCPaigHHetm3c9SwwoQudPJtGTQXJCnCFM` | candidate | 0.2 | 7wCgSrbp (06-03) | 7wCgSrbp (06-03) | 1 | 1 | unresolved | **CANDIDATE_SIGNALLER** |

Status rules applied (evidence-gated, per the critical constraint):
- **CORE_SIGNALLER** = present at ≥80% of confirmed launches across multiple hubs (S1, S2: 100%).
- **CANDIDATE_SIGNALLER** = appears in the signalling tier but at <2 launches (`44or4i`: 1). NOT promoted on vanity-address similarity — requires recurrence across launches first.
- No SECONDARY_SIGNALLER qualifies (would need recurring multi-launch appearance below CORE threshold — none observed).
- 1e-9 swarm wallets are explicitly **excluded** (32 one-time senders ≠ signallers).

---

## Recommendations

1. **Keep monitoring S1 + S2** — they are the complete signalling layer (100% coverage). Hard-coding two addresses is, in this case, *correct* and evidence-backed — there is no hidden third signaller.
2. **Watch `44or4i` as a CANDIDATE** — log future appearances; promote to SECONDARY only if it recurs at ≥2 further launches. Its vanity-address match to the operator is suggestive but unproven.
3. **Do not use dual-signaller as the launch trigger** — it over-fires on reserve/swarm hubs (precision finding). Use it to *arm*, then gate the launch decision on the fee-sized creator-seed leg.
4. **Populate `wt_signallers`** as above; wire S1/S2 detection to feed the hub-arming signal, consistent with the existing `discover_provisioning_hubs` signature check.

*All findings from on-chain transaction evidence over the 8 confirmed provisioning hubs + a 3-hub non-launch control. No classification was made on dust transfers or address similarity alone.*
