# WATCHTOWER — Operator Monitoring & Token Launch Findings

**Date:** 18 May 2026  
**Status:** Live monitoring active

---

## What WATCHTOWER Is

WATCHTOWER (`5Ww9G6XuSHgXLoNmWusVz2SbESAeL7Q6stZMeEhPU25H`) is a fee collection and coordination hub for a large-scale pump.fun token launch operation. Operators pay a fixed daily fee of **0.002839 SOL** to WATCHTOWER in exchange for provisioned wallets, launch coordination, and profit extraction infrastructure.

It is not an ownership attribution — it is a **paid service**. Each operator is a paying customer.

---

## Scale

| Metric | Value |
|--------|-------|
| Known operator wallets | 115 confirmed / 644+ estimated |
| Detection method | `.10203928` SOL funding fingerprint (April 2026 provisioning batch) |
| Tokens launched (in DB) | 115 tokens across 115 operators |
| Launch window | 14 April – 30 April 2026 |
| All tokens migrated | Yes — all reached PumpSwap |
| Last known launch | 30 April 2026 |

---

## How Operators Are Provisioned

Each operator wallet is set up with a consistent fingerprint:

1. **Funding:** Relay funder sends `X.10203928 SOL` to the operator — the `.10203928` fractional is a scripted constant, making every provisioning tx identifiable
2. **Dust activation:** `5Ww9G6` (WATCHTOWER) sends `0.001 SOL` to cover initial tx fees
3. **Launch:** Operator launches token on pump.fun
4. **Profit extraction:** After graduation, operator routes proceeds to a PROFIT-RELAY wallet
5. **Loop:** PROFIT-RELAY → AGGREGATOR → TREASURY-UP → TREASURY → re-funds operators

---

## Detection Rules

### Strong signals (trigger flag)
| Rule | Operators matched |
|------|-------------------|
| `confirmed_fingerprint_batch` — funded with `.10203928` SOL | 106 |
| `funded_by_infrastructure` — dusted by WATCHTOWER directly | 11 |
| `profit_relay_routing` — profits sent to known relay wallet | 23 |

### Weak signals (evidence only, not sufficient alone)
- `funding_fingerprint` — `.10203928` present but batch not yet confirmed
- `.0203928` / `.40203928` variants — related scripting, separate operation

---

## Infrastructure Wallets

| Address | Role |
|---------|------|
| `5Ww9G6XuSHgXLoNmWusVz2SbESAeL7Q6stZMeEhPU25H` | WATCHTOWER — fee collection hub |
| `44orWS68MqXG198M3YXyZoNrYtsNhgnNhtUT5SavqJFM` | TREASURY |
| `6jeT3WyrfwLxox3yAmchDg7ZQvS8XK8XXbkviPUudUW1` | TREASURY-UP |
| `4gE46F7x7RtP6KQTbLxXjyDL4bWscDPNYzv5y5E4Afm8` | DEPLOYER (dormant since 24 Mar 2026) |
| `44orA1BxQfFaX2iMjRbWstoqqWBE7ag8BD93ikxR4JFM` | SIGNALLER |
| `4LpEjcq3PwkE9Hwt1xLd…` | PROFIT-RELAY-1 |
| `7UyCwmSUcG7utdSPikn5…` | PROFIT-RELAY-2 (sent 0.24 SOL directly to WATCHTOWER) |
| `N3TKf3wMBNu8XmZsTSnk…` | PROFIT-RELAY-3 |
| `5GZvPqYggF9HS59xBazaTVogMGyCmdMV3sE4oWzJv5Y7` | PROFIT-RELAY-4 |
| `Gc5TEqe9VfQx3mpz3hac…` | AGGREGATOR-Gc5 |
| `Gopx5xiHoDw3fe7hF96S…` | AGGREGATOR-Gop |

---

## Top Tokens Launched by Operators

All tokens reached PumpSwap migration. Peak market caps:

| Token | Peak MC | Stage |
|-------|---------|-------|
| TIMETRUMP | $283,805 | migrated |
| TRUMP | $233,911 | migrated |
| Untweeney | $192,877 | migrated |
| TOGO | $192,214 | migrated |
| WARP | $190,371 | migrated |
| GUTES | $186,467 | migrated |
| MULANIA | $171,038 | migrated |
| MILF | $161,508 | migrated |
| MEMETOPIA | $160,603 | migrated |

All sub-$300K peak MC — consistent with coordinated low-cap launch farming rather than organic token growth.

---

## Live Monitoring

### How it works

The platform watches all 115 confirmed operator wallets. When any of them surfaces as a token creator in real-time data, a **🟠 DORMANT ACTIVATED** signal fires on the WATCHTOWER network diagram.

**Pipeline:**
```
pump.fun WebSocket / Helius birth webhook
    → token_analysis (creator field)
    → poller checks every 60s
    → creator IN watchtower_fee_payers?
    → 🟠 DORMANT ACTIVATED card
```

### Deduplication
Seen pairs (creator × mint) are persisted in `watchtower_dormant_seen` — signals survive app restarts and won't re-fire for historical tokens.

### Helius webhook
A live webhook (`106e20f6`) watches `5Ww9G6` for inbound transfers. Every new sender is automatically added to `watchtower_fee_payers` and queued for detection analysis at priority 850. This means new operators paying fees for the first time are captured in real-time and added to the watch list without manual intervention.

---

## Blind Spots

- **No payment history pre-webhook:** `tx_count` and `total_sol_sent` in `watchtower_fee_payers` are 0 for seeded operators — actual fee payment history requires a Helius historical pull against `5Ww9G6`
- **Direct Raydium launches:** Tokens launched directly on Raydium (not via pump.fun) are not ingested
- **Post-April operators:** The May 2026 `.0203928` wave (481 wallets on May 17, 163 on May 18) uses the same scripted tooling but has no confirmed on-chain link to WATCHTOWER infrastructure — treated as a separate operation until evidence emerges
- **DEPLOYER dormant:** `4gE46F7` has been inactive since 24 March 2026 — unknown if operation has moved to a new deployer

---

## Key Finding

WATCHTOWER operators show a consistent pattern: **provision → launch → migrate → extract profits → loop**. All 115 confirmed operators completed this cycle within a 16-day window in April 2026. The operation then went quiet. Monitoring is in place to detect if/when the next wave activates.
