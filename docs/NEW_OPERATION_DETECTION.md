# New Operation Detection — Pump.fun Coordinated Launch Group

**Date:** 28 May 2026  
**Status:** Research complete — implementation pending  
**Confirmed tokens:** IFO, GDNR  
**Circumstantial tokens:** BOYS, DOGE, FROGGY, PSYCHOSIS

---

## What This Operation Is

A coordinated pump.fun token launch operation distinct from WATCHTOWER. Key differences:

- **Funded via Binance CEX withdrawals** — no on-chain treasury funding chain
- **5-6 hop phantom relay chain** — relay addresses have no persistent on-chain history, making forward tracing impossible
- **Multiple concurrent launches** — several operators active simultaneously, each independently funded
- **All tokens migrate to PumpSwap** — operation only runs tokens to completion
- **Profits recycled through shared infrastructure** back to treasury for redeployment

---

## Confirmed Infrastructure

### Treasury (WATCHTOWER equivalent)
```
Bj6DJuAjJXVEWcGYjacXnS9fQ5f8cyvgDtEYndN4rydD
```
- Receives all profits via `8ivf9RmLS5E` fee node
- Redeploys capital to new operator batches
- Active since 25 May 2026
- Inbound: 2,000+ SOL/week in the observed window

### Profit Relay Layer
```
AHE2LraoSrweFw5awXmiQDdxnt2RpZZPaZRRsnRgQQwn  — profit accumulator / gas payer
5RgpGNeDqN6peRbEAZAs4hZtFhte3u2Fn7vhSVpHQ1HT  — routing/fee node
8ivf9RmLS5E61sL8qBT5gcHnDga3ZPE1caMCDAveGRnx  — fee node feeding Bj6DJuAj
```

### Distribution Layer (feeds operators)
```
67oLzEW7B8wLeuv7G8bKUbb13Hb7vpo8LLfNUTKtSPsy  — swarm buyer funder (since 20 Apr)
HzetSvJPTVjHs5SknsybGEPumwdG1hLtDjciShXKpS2u  — distribution hub (since 15 May)
FhN9DmDy4Fb2yTh4h2fHY9Chq45aMNaqronPoQfLtfAQ  — distribution hub (since 26 May)
CezuGUn2FoiJYhNynRGENiYxxngwtHWhttn7Re4Tbx4A  — distribution hub (since 15 May)
```

### Sub-Provisioner Pool
```
BmFdpraQhkiDQE6SnfG5omcA1VwzqfXrwtNYBwWTymy6  — fed by EiMJefb6, funds operators
EiMJefb6bdJuFvgvGXbaSW9d8Cg8v5N4yx2T27jejQWt  — KuCoin deposit, relay
```

### Apex Treasury Pair (active since Feb 2026)
```
D78TsGCgYz4jNa1ZgYY3cHgCrd5TKiua4WRoTPQF2hAr
D5ESGx5ssTT92aPH12rspYLPMit8nEwBECw9WwLwa7ch
```
- Cycle SOL between each other at the top of the stack
- Feed downward via `7PDptmY4SariwZ3wtVcwN1Qqxgw8okAsp2G4xirJgar8`

### Binance Funding Wallet
```
5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9  — Binance 2 hot wallet
```
- NOT monitorable — shared CEX hot wallet with thousands of txs/second
- Funds operators directly alongside unrelated personal transactions
- Same wallet confirmed for IFO (26 May 09:14), GDNR (26 May 04:24), BOYS (28 May 09:42), FROGGY (26 May 23:05)

---

## Complete Operation Flow

```
Binance 2 (5tzFkiKs)
    ↓ multi-recipient batch tx (5-15 SOL per operator)
Operator wallet (fresh, no prior history)
    ↓ launches token on pump.fun
    ↓ ~70 swarm wallets buy in 3-second burst (funded via 67oLzEW7 → HzetSvJP chain)
    ↓ bonding curve fills → migrates to PumpSwap
    ↓ operator sweeps migration proceeds (8-200 SOL)
    → one-use hop wallet
        → 5RgpGNe (routing fee node)
            → AHE2Lrao (profit accumulator)
                → Bj6DJuAj (treasury, via 8ivf9RmLS5E fee node)
                    → next wave of operators (4-5 phantom relay hops)
```

---

## Confirmed Token Links

| Token | Mint | Creator | Peak MC | Hard Link |
|-------|------|---------|---------|-----------|
| IFO | `AaQUnaDZ9eN7MveS9WPk57ErpXJSLTPe4qCNRcBqpump` | `3MDzWC5d1svKidMWMhnhRyFkHmgSNNpBHqRVn3erGthp` | $11.9k | `AHE2Lrao` in profit tx |
| GDNR | `GFiwpA6rSfEYcQ7P3VJMraJMbUea58bjcQ3vBeyXpump` | `77QRyXQ3PQJe5uYL17NtC7B7fEwcLnQssMfrWer3jiAn` | $126k | `BmFdpraQ` directly funded operator |

**Proof:** Both tokens' profits flow through `5RgpGNe` → `AHE2Lrao` → `Bj6DJuAj` on 28 May (108 SOL consolidated at 12:48 UTC).

---

## Token Qualification Criteria

### Tier 1 — Hard proof (one is sufficient)

A shared non-Binance wallet appears in the token's funding chain OR profit extraction path matching any known infrastructure address:

- `BmFdpraQhkiDQE6SnfG5omcA1VwzqfXrwtNYBwWTymy6`
- `EiMJefb6bdJuFvgvGXbaSW9d8Cg8v5N4yx2T27jejQWt`
- `AHE2LraoSrweFw5awXmiQDdxnt2RpZZPaZRRsnRgQQwn`
- `5RgpGNeDqN6peRbEAZAs4hZtFhte3u2Fn7vhSVpHQ1HT`
- `Bj6DJuAjJXVEWcGYjacXnS9fQ5f8cyvgDtEYndN4rydD`
- `HzetSvJPTVjHs5SknsybGEPumwdG1hLtDjciShXKpS2u`
- `FhN9DmDy4Fb2yTh4h2fHY9Chq45aMNaqronPoQfLtfAQ`
- `CezuGUn2FoiJYhNynRGENiYxxngwtHWhttn7Re4Tbx4A`
- `67oLzEW7B8wLeuv7G8bKUbb13Hb7vpo8LLfNUTKtSPsy`
- `D78TsGCgYz4jNa1ZgYY3cHgCrd5TKiua4WRoTPQF2hAr`
- `D5ESGx5ssTT92aPH12rspYLPMit8nEwBECw9WwLwa7ch`

### Tier 2 — Corroborating (requires 4+ signals)

- Operator funded by `5tzFkiKs` same day as launch
- Operator wallet has no prior on-chain history
- Token migrated to PumpSwap
- Profit destinations are phantom addresses (0 on-chain history)
- Launch within 1-2 hours of funding
- Swarm buy pattern — coordinated burst in <10 second window, ~70 wallets, ~0.014 SOL each

### Check order for a new token

1. Get fee payer from the migration tx — that's the real operator (not `pf_ws_creator` from DB)
2. Check operator's inbound funding for any Tier 1 address
3. Check operator's outbound profit sweep for any Tier 1 address
4. If no Tier 1 hit, count Tier 2 signals

---

## Pre-Launch Detection Plan (TO IMPLEMENT)

### The Problem
`Bj6DJuAj` fires large outbound batches 1-2 hours before token launches. Each outbound goes through 4-5 phantom relay hops before reaching the operator. By the time SOL lands on the operator, it's untraceable through normal Helius API.

### The Solution — Chain Walking

When `Bj6DJuAj` fires a large outbound:

1. **Webhook fires** — `Bj6DJuAj` outbound detected (add to Helius webhook)
2. **Extract hop-1 recipient** from the tx
3. **Poll hop-1** via `getSignaturesForAddress` every 2-3 seconds
4. When hop-1 forwards: **extract hop-2 recipient**
5. Repeat until reaching a wallet that:
   - Has no prior on-chain history
   - Receives SOL and does NOT immediately forward it
   - That wallet is the **operator**
6. **Add operator to Helius webhook** — watch for pump.fun `create` instruction (`6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`)
7. When create fires: **we have the mint before anyone else**

### Timing
- `Bj6DJuAj` → operator: relay chain settles in <60 seconds
- Operator funding → launch: 1-2 hours
- Total detection window: **~1-2 hours pre-launch**

### Addresses to add to Helius webhook now
```
Bj6DJuAjJXVEWcGYjacXnS9fQ5f8cyvgDtEYndN4rydD  — watch for large outbound
AHE2LraoSrweFw5awXmiQDdxnt2RpZZPaZRRsnRgQQwn  — watch for large inbound (post-migration signal)
5RgpGNeDqN6peRbEAZAs4hZtFhte3u2Fn7vhSVpHQ1HT  — routing node
```

### Key limitations
- Relay depth means forward tracing requires active polling, not just webhook
- New operation cycles may use fresh `AHE2Lrao`-equivalent addresses — watch for `Bj6DJuAj` seeding a new wallet
- `5tzFkiKs` (Binance) cannot be webhooked — CEX off-chain

---

## WATCHTOWER Corridor — Helio Payment Pattern

### Observed 28 May 2026

Two WATCHTOWER corridors fired and expired F5M without a token launch. Both followed an identical pattern:

**Corridor 1**
- Wallet: `B1zy7csmEnUC7Ma77V4sAE3pX16H1See7FwGxf4fHctp`
- TREASURY funded: 10 SOL at 14:14:16 UTC
- SIGNALLER dusted: 0.00001 SOL at 14:14:26–14:18:26 (10 dust txs, lag 116s)
- Resolution: `singleSolPayment` via Helio Program → 3.64 SOL to `21wG4F3ZR8gw` at 14:21:36

**Corridor 2**
- Wallet: `7sUuvJaEVo739aNjbYoGpC4CUQmdcvumQjrdCTAEDqf9`
- TREASURY funded: 10 SOL at 00:17:09 UTC
- Resolution: `singleSolPayment` via Helio Program → 3.596 SOL to `23vEM5sjg5ft`

### Pattern

Both operators made near-identical payments (~$290-295 USD, ~3.6 SOL) via Helio within the same day, using the same Helio fee infrastructure:
- Fee wallet 1: `FudPMePeNqmnjMX19zEKbBFwHnBgHvRFbnbtaG1cfbmo` (~0.033 SOL)
- Fee wallet 2: `JBGUGPmKUEHCpxGGoMoww...` (~0.003 SOL)

### Interpretation

These operators received WATCHTOWER funding and were activated by the SIGNALLER but used the SOL to purchase a service rather than launch a token. The near-identical amounts suggest they're paying for the **same product** — possibly:
- A bot or launch automation tool subscription
- A third-party service used by WATCHTOWER operators
- A per-launch fee to a service provider

### Detection Rule

Any WATCHTOWER corridor wallet whose **first non-dust tx** after SIGNALLER activation is a `singleSolPayment` to Helio Program should be immediately classified as **HELIO_PAYMENT** (not a launch) and the corridor marked ABORTED without waiting for F5M expiry.

**Helio Program identifier:** `dHeNgNVXeGzahCjGMVnRZbWaGqmB8MTNLPqKUcKqm8z` (or `Helio Program 1` in Solscan)

**Amount fingerprint:** 3.5–3.7 SOL payment + ~0.033 SOL fee + ~0.003 SOL protocol fee

---

## Known Blind Spots

- **Operator identity** — Binance withdrawal means no persistent on-chain operator identity
- **New infrastructure** — each cycle may create fresh relay addresses (`AHE2Lrao` was created day-of on 28 May)
- **pf_ws_creator unreliable** — pump.fun records first buyer not deployer; always verify against migration tx fee payer
- **Helius phantom addresses** — relay hop wallets have 0 Helius history; use RPC `getSignaturesForAddress` directly

---

## Reference Transactions

| Event | Signature |
|-------|-----------|
| IFO funding (Binance → operator) | `Nwka1cWEkPUz9Wo4UWrnKu4LAZ4HyKxXYPG2TQXWMXc5xTKXFLs5NywM2r6hwDKXkCizKWW4R9p4zbV3wm2xR9s` |
| IFO migration + profit sweep | `223Qpuxu8mZqbMGvs2tvAKeWoEMtgXk7GSF2Zuq8oWPzP7jNJWExBz9n2yhNkQ1k8CN6oyCBbkSiANjsuVi9Wtvu` |
| GDNR funding (Binance → operator) | `51Atxw3xVjRt2BNzffHde7HaLfMx5im9bJAAnqXXTu6MHqDfraMAttZSHEkR2RHghvccdcYrnD2SMSWgJbMepZdz` |
| GDNR funding (BmFdpraQ → operator) | `4sWgp3pR9qhbg5RNhtt5uFszP4JDyxkrYr99snsubkdap9UWpSbPK8cT9bvm2CcUNRR3fgMYs7PLDkerqfz1Zgwk` |
| Bj6DJuAj profit consolidation (108 SOL via AHE2Lrao/5RgpGNe) | 28 May 12:48 UTC |
| Original Binance batch (seeds IFO + GDNR same tx) | `Nwka1cWEkPUz9Wo4UWrnKu4LAZ4HyKxXYPG2TQXWMXc5xTKXFLs5NywM2r6hwDKXkCizKWW4R9p4zbV3wm2xR9s` |
