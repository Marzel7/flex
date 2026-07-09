# Mint Prediction Hypothesis — Validation Report
*Validated 2026-06-01 against real on-chain data*

---

## Hypothesis Under Test

> SUB_PROV → Relay1 → Relay2 → Creator  
> Once the creator wallet is identified, the mint address can be derived or predicted before the CREATE transaction.

---

## Conclusion: D

**CREATE is the earliest practical signal.**

Mint prediction is impossible. The strategy of buying before CREATE is not viable.

---

## Critical Finding: The Mint Is a Vanity Keypair, Not a PDA

Every pump.fun mint is an Ed25519 keypair generated client-side where the base58 pubkey ends in `pump`. The creator's bot grinds random keypairs until one satisfies this constraint.

**The mint signs the CREATE transaction.** PDAs cannot sign — they have no private key. A signer must be a keypair. This alone disproves the PDA derivation theory.

Tested all reasonable seed combinations against pump.fun program `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`:

| Seeds | Gaynald result | Match? |
|-------|---------------|--------|
| `["mint", creator_bytes]` | `bdTKcHHf87X9...` | No |
| `["mint"]` | `FDiRjtM88XEy...` | No |
| `[creator_bytes]` | `69P7a3KMe7xv...` | No |
| `["token", creator_bytes]` | `5o9xRHSmwbP1...` | No |

Zero matches across all three launches. **The mint cannot be predicted from the creator pubkey alone.**

The private key is generated milliseconds before the tx fires and discarded immediately after. There is no way to know it in advance.

---

## Per-Launch Evidence

| Launch | Mint | Creator new? | Funded → CREATE | Mint derivable? |
|--------|------|-------------|----------------|-----------------|
| Gaynald Trump | `CUdwRcEH...pump` | Yes — brand new | **1 second** | No |
| TRUMPCUM | `8AYsSaPy...pump` | No — pre-existing | Pattern broken | No |
| Sellategy | `3Cj1XSsk...pump` | Yes — brand new | **356 seconds** | No |

### Gaynald Trump
- Creator `8RW8MeyB` funded at `1780261006` (20:56:46)
- CREATE tx at `1780261007` (20:56:47) — **1 second later**
- In practice: same or consecutive Solana slot (400–800ms)
- A monitoring bot observing the relay payment would need to identify creator, derive mint, and submit buy — all in ≤400ms. Not feasible.

### TRUMPCUM
- Creator `6NV84W76` was **pre-existing** — active wallet with prior history
- SUB_PROV relay funded it at 10:07:41 but CREATE happened at 09:23:28 — **44 minutes before the relay**
- The relay was a buy-in after launch, not the initial creator seeding
- This breaks the "brand new creator wallet" assumption

### Sellategy
- Creator `HLucJQyQ` funded at `1780327840`
- CREATE at `1780328196` — **356 seconds (5.9 min) later**
- Real actionable window — but irrelevant because mint is not derivable

---

## Answers to Validation Questions

### Q1: Can the creator wallet be identified before CREATE?
**Sometimes.** Sellategy had 356 seconds. Gaynald had 1 second. TRUMPCUM broke the pattern entirely (creator pre-existed). Not reliable enough to depend on.

### Q2: Is the mint derivable from creator information alone?
**No.** Definitively disproved. The mint is a vanity keypair ground client-side. No deterministic relationship to creator pubkey exists.

### Q3: Timing statistics
| Metric | Value |
|--------|-------|
| Minimum gap (funded → CREATE) | 1 second |
| Maximum gap | 356 seconds |
| Sample size | 3 launches |

Sample too small for reliable statistics. More launches needed before drawing timing conclusions.

### Q4: Could a production bot buy before CREATE?
**No.** Even with a 356-second window, no mint = no buy instruction possible. The pump.fun buy instruction requires the mint address to construct. The bonding curve PDA `["bonding-curve", mint_bytes]` also requires the mint.

### Q5: Failure modes found
- **Gaynald**: 1-second window — effectively zero blocks
- **TRUMPCUM**: Creator was pre-existing — "brand new wallet" assumption fails
- **Relay chain**: Pattern consistent for Gaynald and Sellategy, broken for TRUMPCUM

---

## What Still Has Value

The relay chain signal is not useless — it just cannot enable pre-CREATE buying.

**Advance warning** — detecting SUB_PROV → Relay1 transition gives seconds to minutes of notice that a CREATE is imminent. This enables:
- Pre-warming RPC connections
- Pre-computing priority fees
- Positioning buy infrastructure to fire the moment CREATE is observed

**CREATE monitoring remains the earliest actionable signal.** A bot watching for pump.fun CREATE transactions from known WATCHTOWER creator wallets (or from wallets funded via known relay chains) can buy in the same or next slot — 400–800ms after CREATE lands.

---

## Recommended Architecture

```
WATCH relay chain  →  advance notice only
                        ↓
                   pre-position bot
                        ↓
                   observe CREATE tx
                        ↓ (~400ms)
                   submit buy (same/next slot)
```

**Do not invest engineering effort in mint prediction. It is not possible.**

---

## Next Step

Move SUB_PROV monitoring from Helius webhook (2-3 min lag) to WebSocket `accountSubscribe` for near-real-time relay detection. This maximises the advance warning window and enables fastest possible reaction to CREATE.
