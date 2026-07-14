2snHHreXbpJ7UwZxPe37gnUNf7Wx7wv6UKDSR2JckKuS deBridge Finance

---

# Investigations

## WATCHTOWER — Large-Scale Fee Collection Operation
**Status:** Active investigation — massively expanded | **Added:** 2026-05-17 | **Revised:** 2026-05-18

### Overview
`5Ww9G6` is a **fee collection wallet** for a large coordinated token launch operation. Two sweeps confirmed across two days, each a separate independent batch of wallets:
- **2026-05-17 07:02 UTC**: 481 unique wallets swept 0.002839 SOL each in ~4 seconds
- **2026-05-18 04:57 UTC**: 163 new wallets (zero overlap with May 17 batch) swept 0.002839 SOL each in ~20 seconds, WATCHTOWER swept 18.4 SOL to TREASURY at 05:00

**Total confirmed operators: 644+** across two batches. None of the 481 May 17 wallets are in our DB. The entire network is unknown to us.

### Key Wallets
| Address | Label | Role |
|---|---|---|
| `5Ww9G6XuSHgXLoNmWusVz2SbESAeL7Q6stZMeEhPU25H` | **WATCHTOWER** | Fee collection wallet — receives 0.002839 SOL from all operator wallets |
| `4gE46F7x7RtP6KQTbLxXjyDL4bWscDPNYzv5y5E4Afm8` | **DEPLOYER** | **Dormant since 2026-03-24.** One-time bootstrapper — initialised all 481 operator wallets in March, collected 0.0123 SOL setup fee per wallet (not ongoing), extracted 35.4 SOL profit to PROFIT-1, then went silent. |
| `4KrAL5ZSt4AFXB6h8cMrPbtBJTjVgRJbgY4doKUmqCSd` | **PROFIT-1** | Received 35.4 SOL from DEPLOYER — likely profit extraction wallet |
| `44orWS68MqXG198M3YXyZoNrYtsNhgnNhtUT5SavqJFM` | **TREASURY** | Main operation treasury/router. Bidirectional with WATCHTOWER. Received 1,500+ SOL from `6jeT3W`, swept 1,350 SOL to `kFycb9`. WATCHTOWER sends proceeds here; TREASURY re-feeds WATCHTOWER. Active today. |
| `6jeT3WyrfwLxox3yAmchDg7ZQvS8XK8XXbkviPUudUW1` | **TREASURY-UP** | Sent 1,500 SOL into TREASURY — likely the operation's primary bankroll wallet. Also received 200 SOL back from `kFycb9`. Scanning. |
| `kFycb9QoQaRgLy3zZpF4Zw5DM5gKoT5HkZogSerq1Hd` | **SWEEP-1** | Received 1,350 SOL from TREASURY, immediately swept 863 SOL to `HY8Q4X` and 200 SOL back to `6jeT3W`. Extraction router. |
| `HY8Q4XytMWBetFCT88W2yitW5UnipG8MtqS5WaRF6NGd` | **EXTRACTION** | Received 863 SOL from SWEEP-1 — only 1 tx. Dead-end wallet, trail cold. |
| `5Ww9aZYFFVTXTsNEBSv6FxT4vNcCExKo2Wckk68FU25H` | **VANITY-DUST** | Sent 0.00001 SOL dust to TREASURY at 06:07. Similar prefix to WATCHTOWER (`5Ww9…`). Possible vanity address — role unclear. Scanning. |
| `44orA1BxQfFaX2iMjRbWstoqqWBE7ag8BD93ikxR4JFM` | **SIGNALLER** | Dust-only wallet (`44or` vanity prefix, same family as TREASURY). Sends 0.00001 SOL to all network nodes — WATCHTOWER, TREASURY-UP, and newly funded operators — as on-chain trigger signals. Dusted `8qWLjTU` at 20:44, TREASURY then sent 70 SOL to that same wallet at 20:45. |
| `8qWLjTUjTNNriswPdCzSZjW6E1PMpvfCLyEvdJWhu3Tx` | **ACTIVATING** | ⚡ Received dust from SIGNALLER at 20:44, then 70 SOL from TREASURY at 20:45 — **activation in progress right now**. Likely becoming a creator imminently. Scanning at priority 950. |
| `Gs7zXNYwdd2X1PoyBbsJBCuNTz6EyTT5KSd38tLMEmif` | **ACTIVE-LAUNCHER** | Already live — receiving 8.38 SOL and distributing to multiple addresses at 20:54. Scanning. |
| `2vBd5o7ppBLVXo2TyTfSXhWgK9LMKxEszuNFLU7ZKnUz` | **RELAY-2vBd5o** | Intermediary funded by TREASURY (116 SOL x5), TREASURY-UP (88 SOL x10), and `H8WASJHhi3y` (68.3 SOL). Also receives creator profits via `5GZvPqY` (37 SOL confirmed). Total ~329+ SOL in. Role: relay/aggregator inside the network. |
| `H8WASJHhi3yQW4akyyx89Egvyyvve9q5ZW28H9VPAn3Z` | **CREATOR-goblinmaxxing** | Token creator — launched `goblinmaxxing` (2026-05-09, peaked $3.4M, migrated). Sent 68.3 SOL to RELAY-2vBd5o — suggests profit recycling back into the network. LOW risk score. |
| `4LpEjcq3PwkE9Hwt1xLdYHCxyNYB13wEahUPCRkzZa9Q` | **PROFIT-RELAY-1** | Collected 819 SOL from 14 creators (all on Apr 30). Drained — sent 568 SOL to AGGREGATOR-Gc5TEqe9. Also received 690 SOL re-feed from Gc5TEqe9 (circular staging). |
| `7UyCwmSUcG7utdSPikn5caL9QwbEnAs1aDcbdWvGs37A` | **PROFIT-RELAY-2** | Collected 735 SOL from 9 creators (all on May 10). Drained — sent 92 SOL to `5GZvPqY`, **0.24 SOL directly to WATCHTOWER** (confirms profit loop closes back to infrastructure). |
| `Gc5TEqe9VfQx3mpz3hac8z95LkMBoTUUieErCbEJASTv` | **AGGREGATOR-Gc5** | Cycles SOL with `Gopx5xiH` (1,336↔1,643 SOL wash pattern). Active Apr 15–30. Sent 235 SOL to `6DXb2ZfrX` and 170 SOL to `BQ7KynuK`. Trail continues via `vpZCCRPvxtnH`. |
| `Gopx5xiHoDw3fe7hF96SHgw4wQRMgaxT9HRuySM4wRkg` | **AGGREGATOR-Gop** | Cycles with AGGREGATOR-Gc5. Also sent 646 SOL to `vpZCCRPvxtnH` (extraction). |
| `N3TKf3wMBNu8XmZsTSnk2xWQ2LjiGvUJh1ae9Lc3dW7` | **PROFIT-RELAY-3** | Received 100 SOL from `5GZvPqY`. Active May 17–18 (662 SOL balance). **Sent 1,000 SOL to TREASURY-UP** — closes the profit loop back into bankroll. Also sent 1,700 SOL to `4C6ThYFB` (unknown). |
| `5GZvPqYggF9HS59xBazaTVogMGyCmdMV3sE4oWzJv5Y7` | **PROFIT-RELAY-4** | Received 92 SOL from PROFIT-RELAY-2. Sent 100 SOL to PROFIT-RELAY-3 (→TREASURY-UP) and 37 SOL to RELAY-2vBd5o. Active May 7–10. |

### Scale
| Date | Time (UTC) | Wallets | Duration | WATCHTOWER Collected | Swept to TREASURY |
|---|---|---|---|---|---|
| 2026-05-17 | 07:02 | **481** | ~4 seconds | ~1.37 SOL | 11.4 SOL (07:06) |
| 2026-05-18 | 04:57 | **163** (new batch) | ~20 seconds | ~0.46 SOL | 18.4 SOL (05:00) |

- All wallets send exactly **0.002839 SOL** — fixed operator fee
- Each daily batch is **entirely distinct wallets** — zero overlap between days
- **644+ total operators confirmed** across both sweeps
- 163 May 18 wallets queued for scanning (`watchtower_fee_payer_may18`, priority 800)

### Structure
```
TREASURY-UP (6jeT3W) ←──────────────────────────── 1,000 SOL ← PROFIT-RELAY-3 (N3TKf3w)
  └── 1,500 SOL → TREASURY (44orWS68)                               ↑
        ├── ↔ WATCHTOWER (5Ww9G6) [bidirectional]        100 SOL ← 5GZvPqY (PROFIT-RELAY-4)
        └── 1,350 SOL → SWEEP-1 (kFycb9)                            ↑
              ├── 863 SOL → EXTRACTION (HY8Q4X)       92 SOL ← PROFIT-RELAY-2 (7UyCwmSU)
              └── 200 SOL → TREASURY-UP (6jeT3W)                     ↑ + 0.24 SOL → WATCHTOWER
                                                       735 SOL ← 9 creators (May 10)

[Unknown — program-mediated]
  └── DEPLOYER (4gE46F) [DORMANT since 2026-03-24]
        ├── initialised 481 operator wallets (March 2026, one-time)
        └── PROFIT-1 (4KrAL5Z) — 35.4 SOL extracted

106 creators (funded via .10203928, active April 2026)
  ├── 0.002839 SOL each → WATCHTOWER (5Ww9G6)    [daily fee]
  └── profits → PROFIT-RELAY-1/2 (4LpEjcq3, 7UyCwmSU)
        ├── PROFIT-RELAY-1 → AGGREGATOR-Gc5 (Gc5TEqe9) ↔ AGGREGATOR-Gop (Gopx5xiH)
        │     └── → vpZCCRPvxtnH (extraction / CEX)
        └── PROFIT-RELAY-2 → 5GZvPqY → PROFIT-RELAY-3 → TREASURY-UP  [loop closed]
                           └── → RELAY-2vBd5o

481 operator wallets (provisioned March, active May)
  └── 0.002839 SOL each → WATCHTOWER (5Ww9G6)   [07:02:30–07:02:34 UTC, 4 seconds]

VANITY-DUST (5Ww9a…) → 0.00001 SOL → TREASURY [role unknown]
```

### Previously Identified Sub-Network (7 creators)
These 7 were identified via the 1.10203928 SOL scripted init fingerprint and unique relay wallets. They are likely a subset of the 481:

| Creator | Risk | Relay Wallet | Relay SOL |
|---|---|---|---|
| `28dXSX5UsWrnkBeCJRHaTAb7VHNj8r3TMBgeQhxoUBMT` | WATCH | `AAegRTFvPfNrS9M2FT6Yk5GZnnQa5ZjkdUHHoQ8JTse` | 283.4 SOL |
| `toREm9EjYo1uYMHhHbuvtc6ziuvwk5FMmFncboTSdae` | WATCH | `HfJJcFy19nk5Ym7rMQZaGKHGK7rH9yNMznZNU9rNr5uD` | 187.1 SOL |
| `9BRVy6MdRfa75QEGXySPDezRSV5ropCUGvizLtKAUyQW` | WATCH | `FWBqu6jka8HYn2c1nv3w3Zq9nWxnWavQeETghZQB8R4T` | 98.1 SOL |
| `EJUyV8pzjhLmBCku7FoLZAAjhNa8vHfu7LpBkcprdXV4` | WATCH | `9FMvYRXCcswvMaaz7yoBbUCTA5UnxNNoL2dUXpLwwfBz` | 80.2 SOL |
| `J7JH1jMWtBRBhRnndUWAv7yR2ZGBXnnkcVfyqfKTVggG` | WATCH | `8xLym8XQTBHt89nkMLWdxz53xTbvQm4bi6XZ1XRUKKqw` | 45.0 SOL |
| `dqkRa3ZiNpboVeHCpVa5fbSNcXTyfhTwzDXbmjBitHk` | WATCH | `8JX3iKJHrLZq42cTiRo5dAJPcApMx3wH1bCwK7uhTJFX` | 30.2 SOL |
| `2QyrLscpVsSQMkWn8gNKLqQYgTfUcrH5yCKaaa46XRMV` | WATCH | `CyQL6W4UnrL2Qf3A9pKEooNvQksxJhQQfgzZUL6JHHz8` | 25.7 SOL |

### Dust Recipient Pattern (SIGNALLER targets)
SIGNALLER dusts wallet → TREASURY funds wallet (15-250 SOL) within ~1 min → wallet launches tokens.
Scanned 4/11 queued dust recipients so far (2026-05-18):

| Wallet | Upstreams | Pattern | Note |
|---|---|---|---|
| `EHcCurttq` | 9 (all dust) | Launch relay | Outbound: 60 SOL to `AftNwPU` (unknown) |
| `2vBd5o` | 13 | Aggregator | Funded: TREASURY 116 SOL + TREASURY-UP 88 SOL + `H8WASJHhi` 68 SOL |
| `3yP29GdJ` | 17 | Unrelated high-value | Funded by `2xxUiJy` (986 SOL) + `C1kDfPR` (630 SOL) — different network |
| `13DwkLX` | 20 (all dust) | Launch relay | Outbound: 89 SOL to `4LRiL2A` (unknown) |

**Key**: wallets receiving only dust inflows but sending large outbound = pre-funded launch relays. The SIGNALLER dust is the activation signal, not the funding source.

### Why Standard Detection Misses This
All 481 wallets use unique relay funders — no shared non-CEX funder. `CoordinatedEdgesBuilder` is completely blind. The only linking signal is the simultaneous fee sweep to WATCHTOWER, which is not a pattern we currently detect.

### `.0203928` Fingerprint — Separate Operation (Confirmed 2026-05-18)
The `.0203928` fractional SOL fingerprint also appears in a distinct May wave (25 operators, 18 serial creators, 2,231 tokens, Apr 29–May 17). This was suspected to be the same network.

**Verdict: NOT the same operation.** Scanned all 25 May operators via RPC — zero transactions involving WATCHTOWER, DEPLOYER, TREASURY, or any known infrastructure wallet. Their funding sources are entirely different wallets. The `.0203928` fractional amount is shared software/tooling, not shared ownership.

WATCHTOWER confirmed scope: **644 operators, 106 creators, ~106 tokens** (Wave 1, Apr 2026).

### Next Steps
- [ ] **481 wallets scanning** (priority 800) — in progress, ~50/481 done; all return `second_hop_creators=0` (completely unknown network)
- [ ] **Scan DEPLOYER (`4gE46F`)** — queued; need to identify its upstream funder (the true root)
- [ ] **Scan PROFIT-2 (`44orWS68`)** — queued; determine if it's also funded by DEPLOYER or separate actor
- [ ] Investigate `4KrAL5ZSt4AFXB6h8cMrPbtBJTjVgRJbgY4doKUmqCSd` (PROFIT-1) — only 1 tx so far
- [ ] Build fee-sweep detector — flag wallets sending identical small amounts to same address simultaneously
- [ ] Update network diagram once all scans complete
- [ ] Cross-reference 481 operators against token_analysis once scans complete

---

## Gate Hot Wallet (u6PJ8D)
**Status:** Resolved | **Added:** 2026-05-16

`u6PJ8DtQuPFnfmwHbGFULQ4u4EgjDiyYKjVEsynXq2w` confirmed as Gate.io hot wallet.
- Added to `infra_wallets` as `cex / hot_wallet`
- Updated 70 rows in `creator_funders` SET `is_cex=1, cex_exchange='Gate'`

---

## OKX CEX-Masked Coordination
**Status:** Resolved — detector built | **Added:** 2026-05-16

42 creators funded through OKX hot wallet (CEX-masked). Core 11 received exactly 40 or 70 SOL each with strict daily rotation (one creator per day). Profit loop: creator → relay → aggregator → back to OKX via ENicYBBN bridge (1% + 0.1% fee).

CEX flag caused all 42 to be invisible to `CoordinatedEdgesBuilder`. Fixed by building `CexCoordinationDetector` which scores groups on 4 signals: amount clustering, timing cadence, risk profile, G7 rate.

**Results (first run):** 7 CEX-coordinated groups flagged, 6,413 edges written.
| Group | Creators | Confidence | Key Signal |
|---|---|---|---|
| Bitget Wallet | 3 | 0.487 | 2.7d cadence, 2/3 CRITICAL |
| Revolut | 16 | 0.463 | 4 exact 2 SOL, 1.5d cadence |
| Robinhood | 14 | 0.433 | 4 exact 3 SOL, 1.9d cadence |
| Padre | 10 | 0.350 | 3 exact 2 SOL |
| ChangeNow | 111 | 0.350 | 56/111 HIGH/CRITICAL |
| ChangeHero | 7 | 0.350 | 3 exact 2 SOL |
| WhiteBIT | 8 | 0.338 | 1.7d cadence (σ=0.7d) |
