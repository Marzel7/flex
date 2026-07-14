# WATCHTOWER — Investigation Report
**Status:** Active | **Started:** 2026-05-17 | **Last updated:** 2026-05-18

---

## What It Is

A large-scale coordinated Solana token launch operation. A central wallet (`WATCHTOWER`) collects a fixed daily fee from hundreds of operator wallets simultaneously. The same operator network funds creator wallets that launch tokens on pump.fun. Creator profits are recycled back into the operation's bankroll via a layered relay structure.

The operation is invisible to standard coordination detectors because every operator wallet is funded by a unique, unrelated funder — there is no shared non-CEX upstream. The only linking signal is the simultaneous fee sweep.

---

## Scale

| Layer | Count | Notes |
|---|---|---|
| Operator wallets (fee payers) | **644+ confirmed** | 481 on May 17, 163 new on May 18 — zero overlap |
| Creators visible in our DB | **106** | Identified via `.10203928` SOL funding fingerprint |
| Tokens launched | **~106** | One token per creator, all April 2026 |
| Best peak market cap | **$286k** | Modest individually; coordinated at scale |
| Total operators provisioned | **481** | All bootstrapped by DEPLOYER in March 2026 |

### Daily Fee Sweeps
| Date | Time (UTC) | Wallets | Duration | Collected |
|---|---|---|---|---|
| 2026-05-17 | 07:02 | 481 | ~4 seconds | ~1.37 SOL → TREASURY (07:06) |
| 2026-05-18 | 04:57 | 163 new | ~20 seconds | ~0.46 SOL → TREASURY (05:00) |

Fixed fee: exactly **0.002839 SOL** per wallet per sweep.

---

## Timeline

| Date | Event |
|---|---|
| March 2026 | DEPLOYER bootstraps 481 operator wallets. Each receives 0.0123 SOL setup fee. DEPLOYER extracts 35.4 SOL to PROFIT-1 then goes dormant. |
| 2026-03-24 | **DEPLOYER goes silent.** Never active again. |
| Apr 23–28 2026 | 106 creators (visible to us) launch tokens. Each funded via `.10203928` SOL scripted init. Each pays 0.002839 SOL/day to WATCHTOWER. |
| Apr 30 2026 | PROFIT-RELAY-1 collects 819 SOL from 14 creators in a single day. Routes to AGGREGATOR pair. |
| May 10 2026 | PROFIT-RELAY-2 collects 735 SOL from 9 creators. Routes back toward TREASURY-UP via relay chain. |
| 2026-05-17 07:02 | 481 wallets sweep 0.002839 SOL each to WATCHTOWER in 4 seconds. |
| 2026-05-17–18 | SIGNALLER activates new wallets via dust signals. TREASURY funds them 15–250 SOL within ~1 min. |
| 2026-05-18 04:57 | 163 entirely new wallets sweep to WATCHTOWER in 20 seconds. WATCHTOWER sweeps 18.4 SOL to TREASURY at 05:00. |

---

## Money Flow

### Full Loop (confirmed)

```
TREASURY-UP (6jeT3W) ←──── 1,000 SOL ──── PROFIT-RELAY-3 (N3TKf3w)
  │                                                  ↑
  └── 1,500 SOL ──→ TREASURY (44orWS68)    100 SOL ← 5GZvPqY (PROFIT-RELAY-4)
          │                                           ↑
          ├── ↔ WATCHTOWER (5Ww9G6)         92 SOL ← PROFIT-RELAY-2 (7UyCwmSU)
          │     [daily fees in, re-feeds out]          ↑  [+ 0.24 SOL direct to WATCHTOWER]
          │                                  735 SOL ← 9 creators (May 10)
          └── 1,350 SOL ──→ SWEEP-1 (kFycb9)
                    ├── 863 SOL ──→ EXTRACTION (HY8Q4X)  [trail cold]
                    └── 200 SOL ──→ TREASURY-UP           [partial recycle]

106 creators (Apr 2026)
  ├── daily fee ──→ WATCHTOWER
  └── profits ──→ PROFIT-RELAY-1 / PROFIT-RELAY-2
          ├── PROFIT-RELAY-1 ──→ AGGREGATOR-Gc5 ↔ AGGREGATOR-Gop ──→ vpZCCRPvxtnH [extraction/CEX]
          └── PROFIT-RELAY-2 ──→ 5GZvPqY ──→ PROFIT-RELAY-3 ──→ TREASURY-UP  [loop closed]
                              └──→ RELAY-2vBd5o

481 operator wallets ──→ 0.002839 SOL each ──→ WATCHTOWER  [daily, ~4 seconds]

[Unknown root]
  └── DEPLOYER (4gE46F) [dormant Mar 24]
          └── bootstrapped 481 operators in March ──→ PROFIT-1 (35.4 SOL extracted)
```

### Profit Recycling (confirmed 2026-05-18)
Creator profits do not simply exit — they route back into the operation's bankroll:
- `PROFIT-RELAY-2 → WATCHTOWER`: 0.24 SOL direct payment (on-chain proof creators are part of the same operation)
- `PROFIT-RELAY-3 → TREASURY-UP`: 1,000 SOL (re-bankrolls TREASURY)
- `PROFIT-RELAY-4 → RELAY-2vBd5o`: 37 SOL (RELAY-2vBd5o is also fed by TREASURY directly)

---

## Key Wallets

| Address | Label | Role | Status |
|---|---|---|---|
| `5Ww9G6XuSHgXLoNmWusVz2SbESAeL7Q6stZMeEhPU25H` | **WATCHTOWER** | Daily fee collection. Receives 0.002839 SOL from all operators. Sweeps proceeds to TREASURY. | Active |
| `44orWS68MqXG198M3YXyZoNrYtsNhgnNhtUT5SavqJFM` | **TREASURY** | Main router. Bidirectional with WATCHTOWER. Re-feeds operators and new creators. | Active |
| `6jeT3WyrfwLxox3yAmchDg7ZQvS8XK8XXbkviPUudUW1` | **TREASURY-UP** | Primary bankroll. Sent 1,500 SOL → TREASURY. Receives profit recycling (1,000 SOL from PROFIT-RELAY-3). | Active |
| `4gE46F7x7RtP6KQTbLxXjyDL4bWscDPNYzv5y5E4Afm8` | **DEPLOYER** | One-time bootstrapper. Provisioned all 481 operators in March. Extracted 35.4 SOL to PROFIT-1. | **Dormant since 2026-03-24** |
| `44orA1BxQfFaX2iMjRbWstoqqWBE7ag8BD93ikxR4JFM` | **SIGNALLER** | Dust-only wallet (`44or` vanity). Sends 0.00001 SOL to activate new wallets — TREASURY funds them 15–250 SOL within ~1 min. | Active |
| `kFycb9QoQaRgLy3zZpF4Zw5DM5gKoT5HkZogSerq1Hd` | **SWEEP-1** | Extraction router. Received 1,350 SOL from TREASURY → 863 SOL to EXTRACTION, 200 SOL back to TREASURY-UP. | Active |
| `HY8Q4XytMWBetFCT88W2yitW5UnipG8MtqS5WaRF6NGd` | **EXTRACTION** | Received 863 SOL from SWEEP-1. Dead-end — trail cold. | Unknown |
| `4KrAL5ZSt4AFXB6h8cMrPbtBJTjVgRJbgY4doKUmqCSd` | **PROFIT-1** | Received 35.4 SOL from DEPLOYER. | Unknown |
| `2vBd5o7ppBLVXo2TyTfSXhWgK9LMKxEszuNFLU7ZKnUz` | **RELAY-2vBd5o** | Fed by TREASURY (116 SOL ×5), TREASURY-UP (88 SOL ×10), PROFIT-RELAY-4 (37 SOL), goblinmaxxing creator (68.3 SOL). ~329+ SOL total. | Active |
| `4LpEjcq3PwkE9Hwt1xLdYHCxyNYB13wEahUPCRkzZa9Q` | **PROFIT-RELAY-1** | Collected 819 SOL from 14 creators on Apr 30. Cycled with AGGREGATOR-Gc5 (690 SOL re-feed). | Drained |
| `7UyCwmSUcG7utdSPikn5caL9QwbEnAs1aDcbdWvGs37A` | **PROFIT-RELAY-2** | Collected 735 SOL from 9 creators on May 10. Sent 0.24 SOL directly to WATCHTOWER. | Drained |
| `5GZvPqYggF9HS59xBazaTVogMGyCmdMV3sE4oWzJv5Y7` | **PROFIT-RELAY-4** | Received 92 SOL from PROFIT-RELAY-2. Routed to PROFIT-RELAY-3 and RELAY-2vBd5o. | Drained |
| `N3TKf3wMBNu8XmZsTSnk2xWQ2LjiGvUJh1ae9Lc3dW7` | **PROFIT-RELAY-3** | Received 100 SOL from PROFIT-RELAY-4. **Sent 1,000 SOL to TREASURY-UP** — closes profit loop. Also 1,700 SOL to unknown `4C6ThYFB`. | Active (662 SOL) |
| `Gc5TEqe9VfQx3mpz3hac8z95LkMBoTUUieErCbEJASTv` | **AGGREGATOR-Gc5** | Cycles SOL with AGGREGATOR-Gop (wash pattern: 1,336 SOL in, 1,643 SOL out). Active Apr 15–30. | Drained |
| `Gopx5xiHoDw3fe7hF96SHgw4wQRMgaxT9HRuySM4wRkg` | **AGGREGATOR-Gop** | Cycles with AGGREGATOR-Gc5. Sent 646 SOL to `vpZCCRPvxtnH` (extraction endpoint, likely CEX). | Drained |

---

## The `.10203928` Fingerprint

When DEPLOYER provisioned operator wallets, the script sent exactly `X.10203928` SOL per wallet — a deterministic output of the automation that no human would choose. This fractional amount is consistent across all 112 funding transactions, identifying the 106 creators as part of the same provisioning batch.

A similar `.0203928` fingerprint appears in a separate May 2026 wave (25 operators, 18 creators, 2,231 tokens). **This is a different operation** — scanned all 25 operators across their full transaction history, zero overlap with WATCHTOWER/TREASURY/DEPLOYER. Shared software, different actors.

---

## Why Standard Detection Misses It

- Each of the 481 operator wallets has a **unique, unrelated funder** — no shared upstream wallet
- `CoordinatedEdgesBuilder` is completely blind — no common non-CEX funder means no edges are built
- The only linking signal is the **simultaneous identical-amount fee sweep**, which is not a pattern currently detected
- Profit relay wallets are single-use, single-day, then drained — no persistent address reuse

---

## Activation Mechanism (SIGNALLER)

SIGNALLER (`44orA1BxQf…`, `44or` vanity prefix matching TREASURY) is an on-chain trigger system:

1. SIGNALLER sends 0.00001 SOL dust to a target wallet
2. TREASURY sends 15–250 SOL to that same wallet within ~1 minute
3. Wallet launches tokens

Confirmed example: dusted `8qWLjTU` at 20:44, TREASURY sent 70 SOL at 20:45. The dust is a signal, not funding.

---

## What We Can and Cannot See

**Visible to us (106 creators):** The subset of operators that funded creators already in our `creator_funders` table, identified via the `.10203928` fingerprint. These are Wave 1, April 2026, ~1 token each, best peak MC $286k.

**Not visible (~375 operators):** The remaining operators funded creators we have no record of. Their tokens likely either never reached pump.fun migration threshold, died sub-$10k, or launched on a different platform. Scanning these operators' outbound transactions would be the only way to find them, requiring an external token index.

**The May daily sweeps (644+ operators):** Entirely distinct wallets from the April creator set. Their corresponding creators are unknown to us.

---

## Open Questions

- Who is the true root funder upstream of DEPLOYER?
- Where does `vpZCCRPvxtnH` (646 SOL from AGGREGATOR-Gop) ultimately go — CEX or bridge?
- What is `4C6ThYFB` (1,700 SOL from PROFIT-RELAY-3)?
- Are the May daily sweep batches (644 operators) a continuation of the same 481 March-provisioned operators, or a second independent cohort?
- What did the ~375 operators whose creators aren't in our DB actually launch?
