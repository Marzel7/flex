# WATCHTOWER Token Launch Pattern
*Researched 2026-06-01*

## Overview

WATCHTOWER is a coordinated token launch and pump operation on Solana. It uses a multi-layer infrastructure to deploy capital, create tokens, and execute coordinated buy waves — all within seconds of each other.

---

## Infrastructure Hierarchy

```
TREASURY (44orWS68MqXG198M3YXyZoNrYtsNhgnNhtUT5SavqJFM)
  │  70,546 SOL lifetime throughput. Primary capital source.
  │
  ├── TREASURY_UP (6jeT3WyrfwLxox3yAmchDg7ZQvS8XK8XXbkviPUudUW1)
  │   Upstream capital pool. 19,487 SOL to TREASURY. DORMANT since May 21.
  │
  ├── SIGNALLER_1 (44orA1BxQfFaX2iMjRbWstoqqWBE7ag8BD93ikxR4JFM)
  ├── SIGNALLER_2 (44o1Hecb4QUhqcRNYJBC6XZoeHWzkWAvenR5YYHRGbFM)
  │   Dual-ping 0.00001 SOL to SUB_PROVs as pre-authorisation signals.
  │   ~37 signals/day. S1 fires first, S2 confirms within 3-16 seconds.
  │
  ├── SUB_PROV HUB — N3TKf3wMBNu8XmZsTSnk2xWQ2LjiGvUJh1ae9Lc3dW7
  │   128,651 SOL lifetime. 3,180 txs over 32 days. Scripted 10 SOL +
  │   bulk pairs. wSOL relay loops to obfuscate returns. Feeds downstream SUB_PROVs.
  │
  ├── SUB_PROV HUB — Dw7xNxxwuBnTfpwSrZMhcAQqFp4XNe9XNaVhhsvyx6Da
  │   24,280 SOL lifetime. 6.5 days active. Parallel hub to N3TKf3wM.
  │   Same scripted patterns. Funded by TREASURY + N3TKf3wM + profit returns.
  │
  ├── FANOUT SUB_PROVs (single-operation, 800 SOL each)
  │   DzRrCaXNDG5usCo4oEtAPW8wVrEAwysVddgobrdUjXJ1  (Gaynald Trump)
  │   5U1YLtzw2kkgsRZgnrVagbcAJSivdqzPtFjJAdPPceDW  (active Jun 1)
  │   8U7zfBcS7UWhpHiQLvExLNd6tvtEsGFX1MP1N8QhmoPK  (Sellategy)
  │   2ujRcf1fwQjW8cjUPK6krBJBMdbiMiSKvNscYjdbFW6R  (TRUMPCUM)
  │
  └── SWARM_PROVs (12 wallets, 70 SOL each → 800-4900 buyer wallets)
```

---

## Signal Protocol

Both SIGNALLERs send **0.00001 SOL** (dust) to a SUB_PROV as a pre-authorisation:

- S1 fires first
- S2 confirms within **3-16 seconds**
- Then alternating pings every ~10 seconds for ~90 seconds (monitoring active operation)
- Signals precede TREASURY funding by **seconds to hours**
- 16 unique SUB_PROV destinations identified

**Signal = imminent operation. Watch for dual-ping to known SUB_PROV.**

---

## Token Launch Flow

Confirmed across TRUMPCUM, Gaynald Trump, and Sellategy:

```
T+0:00  SIGNAL dual-ping → SUB_PROV

T+?     TREASURY → SUB_PROV (800 SOL)
        e.g. 44orWS68 → DzRrCaXNDG5usCo4oEtAPW8wVrEAwysVddgobrdUjXJ1

T+4min  SUB_PROV → Relay1 → Relay2 → CREATOR (tiny amount, 0.01-0.5 SOL)
        e.g. DzRrCaXN → GkBWhAtpG3LPs27BXJPBtEZHUMcsqqkivg4MgWnUVfdc
                      → AbvKfgijt1NsXQrnd9JmBZMKaUr8he...
                      → 8RW8MeyB9AzBS9TiZtTtuCh6yzib6PLrC7bRtmh3bfJe

T+4min  CREATOR creates token on pump.fun — 1 second after relay lands
+1sec   e.g. 8RW8MeyB → CREATE CUdwRcEH2fqEuKQkALbHzpv81XUKbEamCEPreHSBpump

T+4min  SUB_PROV → Splitter wallets → 1000s of buyer wallets (SAME BLOCK as create)
+1sec   e.g. DzRrCaXN → 28 SOL + 32 SOL coordinated buys hit bonding curve

T+61s   Creator sweeps profits back to SUB_PROV, is abandoned

T+1hr+  Remaining profits sweep back via 3-hop chain → SUB_PROV → TREASURY
```

---

## Creator Wallet Pattern

- **Brand new wallet** — created specifically for this one operation
- **First ever tx** = relay payment from SUB_PROV
- **Lifetime = 1 day** — born, creates token, sweeps profits, abandoned
- Does NOT trade its own token — purely a creation vehicle
- Receives tiny SOL (0.01–0.5 SOL) to cover pump.fun create fee + initial dev buy

---

## Relay Chain Structure (3 hops)

```
SUB_PROV → Relay1 → Relay2 → CREATOR
```

- Consistent 3-hop structure across all confirmed tokens
- Each hop strips a small fee (~0.002039 SOL)
- Total relay amount scales with operation: 0.01 SOL (TRUMPCUM) → 0.112 SOL (Gaynald) → 0.499 SOL (Sellategy)
- Entire relay chain executes in **same block** as token create

---

## Timing (confirmed examples)

| Token | TREASURY → SUB_PROV | Gap to CREATE | CREATE → Buyer wave |
|-------|---------------------|---------------|---------------------|
| TRUMPCUM | 800 SOL | ~21 min | Same block |
| Gaynald Trump | 800 SOL | **4.6 min** | Same block |
| Sellategy | 800 SOL | 188 min | Same block |

The variable gap (4.6 min vs 188 min) suggests operations are either **manually triggered** or **scheduled** — the signal is the go instruction, execution may be deferred.

---

## Coordinated Buyer Wave

- Buyers are **pre-funded and waiting** before token exists
- Hit the bonding curve in the **same block** as token creation
- No window for retail buyers to front-run the initial pump
- Example: Gaynald Trump — 28 SOL + 32 SOL bought in block 423451183 (same as CREATE)
- Retail first buy appeared 17 minutes later

---

## Detection Opportunity

The detection chain from earliest to latest signal:

1. **SIGNAL fires** (hours before) — dual-ping to known SUB_PROV → operation imminent
2. **TREASURY → SUB_PROV** (≥10 SOL) → operation confirmed, starting
3. **SUB_PROV → Relay1** (tiny amount to new/relay wallet) → creator funding in progress
4. **Relay1 → Creator** (brand new wallet receiving first tx) → **token creation in ~1 second**
5. **Token CREATE tx** → mint address now known, buyers already in

**Window 3→4 is the key detection point.** Watching known SUB_PROVs for small outbound to an unknown wallet via relay gives ~1 second before token creation.

### Mint Address Prediction

Pump.fun mint addresses are **deterministic PDAs**:
```
mint = PDA(["mint", creator_pubkey, nonce], pump_fun_program)
```

Once creator wallet is identified (step 3 above), mint address can be **derived before the CREATE tx fires**.

### Required Architecture for Real-time Detection

Current webhook delivery lag: **2-3 minutes** — too slow.

Required: **WebSocket `accountSubscribe`** on known SUB_PROVs:
- ~200ms notification latency
- Trace 3-hop relay to creator (~300ms, 3 RPC calls)
- Derive mint PDA (~1ms)
- Submit buy tx (~200ms)
- **Total: ~700ms — within the 1-second window**

---

## Known Tokens

| Token | Symbol | Mint | SUB_PROV | Creator |
|-------|--------|------|----------|---------|
| Gaynald Trump | Gaynald | CUdwRcEH2fqEuKQkALbHzpv81XUKbEamCEPreHSBpump | DzRrCaXNDG5usCo4oEtAPW8wVrEAwysVddgobrdUjXJ1 | 8RW8MeyB9AzBS9TiZtTtuCh6yzib6PLrC7bRtmh3bfJe |
| Trump Community | TRUMPCUM | 8AYsSaPyptd6dgQ1dvXsEbPZMuzM6MMRQXAJM9pQpump | 2ujRcf1fwQjW8cjUPK6krBJBMdbiMiSKvNscYjdbFW6R | 6NV84W76QUxAicY4dGACtuuTfCr6QJU3ZfyRmRP6CgY5 |
| Sellategy | Sellategy | 3Cj1XSskaWrKMo2xN4ucnUi94JFZXTSePGAv4sZApump | 8U7zfBcS7UWhpHiQLvExLNd6tvtEsGFX1MP1N8QhmoPK | HLucJQyQy6XmiudWYE5XA4t5y8o5WAJr1CoFE2BsFA2a |
| ULTRATRUMP | ULTRATRUMP | EPjFWaLb9gAKPFbuoDi2VLU549psLxc64cuba7JJSM5P* | N3TKf3wMBNu8XmZsTSnk2xWQ2LjiGvUJh1ae9Lc3dW7 | — |

*ULTRATRUMP mint address unconfirmed — does not end in `pump`

---

## Webhoooked Infrastructure (13 addresses)

All routing through one Helius webhook `106e20f6-f542-42b0-83d5-ca8c7b1a7162`:

- TREASURY, TREASURY_UP, SIGNALLER_1, SIGNALLER_2
- SUB_PROV HUB: N3TKf3wM
- SUB_PROVs: 2ujRcf1f, CcdyBAT7, Dw7xNxxw, 96b4e8qv, kFycb9, Gs7zXNYw, 2vBd5o7p
- TRADING_MGR: 8g2qFR27

**Next step: migrate SUB_PROV monitoring to WebSocket for sub-second detection.**
