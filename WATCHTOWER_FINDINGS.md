# WATCHTOWER Operation — Intelligence Report
*Last updated: 2026-05-28*

---

## What This Is

WATCHTOWER is a coordinated token launch operation on Solana running from at least April 2026 through the present. It uses a fixed three-layer infrastructure to provision creator wallets, launch tokens on pump.fun, and extract profits — while maintaining operational separation between the treasury layer and the launch layer.

The infrastructure has not changed. The fingerprint has. That is the central detection challenge.

---

## Infrastructure (Confirmed as of 2026-05-21)

| Role | Address | Function |
|------|---------|----------|
| **TREASURY-UP** | `6jeT3WyrfwLxox3yAmchDg7ZQvS8XK8XXbkviPUudUW1` | Tops up TREASURY; the operational bankroll |
| **TREASURY** | `44orWS68MqXG198M3YXyZoNrYtsNhgnNhtUT5SavqJFM` | Distributes SOL to sub-provisioners |
| **SIGNALLER** | `44orA1BxQfFaX2iMjRbWstoqqWBE7ag8BD93ikxR4JFM` | Sends dust activation to creator wallets before launch |
| **PROFIT-RELAY 1** | `N3TKf3wMBNu8XmZsTSnk2xWQ2LjiGvUJh1ae9Lc3dW7` | Profit extraction → feeds TREASURY-UP (23k SOL in) |
| **PROFIT-RELAY 2** | `EYjGUZamSQ9vJBxZ4yj7pCK2XaZ99MAEQx9xRMrzyMx1` | Buyer-side profit collector — confirmed May 21 (see below) |

All core infra addresses live and webhookd as of 2026-05-21.

### Sub-Provisioners (Known)

| Address | TREASURY inflow | Date | Wave | Wallets funded | Fingerprint |
|---------|----------------|------|------|----------------|-------------|
| `C745erBxwn4sJZGDRZpi71FPV3MA3kBQUXWbeJxRsGS4` | 300 SOL | 2026-05-17 | May | 20 wallets | 0.01003928 SOL |
| `Gs7zXNYwdd2X1PoyBbsJBCuNTz6EyTT5KSd38tLMEmif` | 1,000 SOL | 2026-05-18 | May | 40 wallets | 0.01003928 SOL |
| `F17dbo3EeumSte7hEBgn6wDAv65BEN4U8eba9zXcNTg` | 2,650 SOL | 2026-05-20 | May | Unknown — fanout unconfirmed |
| `96b4e8qvhjEPsXDtenEgs1VLBTkFqAcgYV8tE16Mgt7h` | 1,850 SOL | 2026-04-28 | April | Unknown |
| `kFycb9QoQaRgLy3zZpF4Zw5DM5gKoT5HkZogSerq1Hd` | 1,350 SOL | 2026-05-16 | Pre-May | Unknown |
| **`G2BbetUgzETGhK8w43YbcwD9yx74CGg649z8nvWn6Ntd`** | **195 SOL (3 tranches)** | **2026-05-21** | **May 21** | **5,000–9,000+ wallets** | **~0.0142 SOL** |

---

## Operational Flow

```
TREASURY-UP (6jeT3W)
    └─► TREASURY (44orWS68)
            └─► Sub-provisioner (rotates each wave)
                    └─► Creator wallets (funded at fingerprint amount)
                                └─► pump.fun token launch
                                        └─► Coordinated buyers buy in (pump AMM)
                                                └─► Profits swept to PROFIT-RELAY
                                                        └─► TREASURY-UP (recycled)

PROFIT-RELAY 2 (EYjGUZam) ◄─── 94+ buyer wallets sweep profits
        │
        └─► Also distributes SOL back to active market-making wallets (e.g. GGxoBCGZ)
```

Sub-provisioners are **one-time-use intermediaries** — they receive a lump sum from TREASURY, fan it out to N creator wallets in a short burst, then go quiet. **Exception: G2BbetUgz is fanning continuously for 13+ hours** — a new operational pattern.

---

## Fingerprint Evolution

The `03928` suffix was the persistent invariant through May 18. It has now been abandoned.

| Wave | Fingerprint | Sub-provisioner | Creators funded |
|------|-------------|-----------------|-----------------|
| April 2026 | `0.10203928` SOL | Multiple | 106 confirmed |
| May 17–18 | `0.01003928` SOL | C745erBx, Gs7zXNYw | 60 confirmed |
| May 21 | `~0.01420000` SOL (varies ±0.001) | G2BbetUgz | 5,000–9,000+ staged |
| Investigated, rejected | `0.00203928` SOL | None — no TREASURY link | 530 false positives reverted |

**The fingerprint has changed again and dropped the `03928` suffix entirely.** The May 21 wave uses a variable amount around 0.0142 SOL with slight per-wallet variation — likely to defeat exact-match detection.

**Fingerprint reliability caveat — confirmed false positive:** `bwamJzztZsepfk...`, a confirmed serial deployer (NOT WATCHTOWER), received three payments at exactly `0.01427496 SOL` from unrelated wallets. This means:

- **Amount alone is not sufficient** as a WATCHTOWER detector
- The `~0.0142` group of 74 migrations identified in the reverse-attribution scan may include false positives from high-volume non-WATCHTOWER creators who happen to have received funding at that amount
- Detection must require the full structural signature: **single total funder + fresh wallet (zero prior history) + unique funder (not repeated across creators)**
- Any scan using amount-only matching against `creator_funders` for the `~0.0142` fingerprint should apply these filters before classifying as WATCHTOWER

---

## Two-Layer Buyer Operation (Confirmed 2026-05-21)

### Discovery

At 16:26–16:43 UTC on May 21, `EYjGUZamSQ9vJBxZ4yj7pCK2XaZ99MAEQx9xRMrzyMx1` received **178+ SOL from 129 distinct wallets** in a 17-minute burst. This triggered reclassification from SUB_PROV to PROFIT-RELAY 2.

### What the buyer wallets are

The 129 counterparty addresses in the DB are **pass-through intermediaries** — each has exactly 1 transaction (the sweep itself). The actual signers behind them are **94 unique trading wallets** that each control multiple pass-through addresses. These 94 wallets:

- Traded exclusively on the **pump.fun AMM** (`pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA`)
- Bought and sold exactly **two tokens**: `H3VTG1ed8JH8K6q7A1Zk6NRJgksWfBs3q7peYUoxpump` (SpaceX Cup / SPCXCUP) and `CrjAFUG78otnbSdD8rhmrWu9h9ukMN2CA4t1XA8zpump` (Fitness Coin / Fitcoin)
- **Did not launch any tokens** — pump.fun fee program (`6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`) never appeared in their tx history
- Are coordinated buy-side support: they pump the bonding curve after launch, accumulate tokens/SOL, then simultaneously sweep profits to EYjGUZam

### The two tokens they traded

| Token | Mint | Launched | Creator wallet | WATCHTOWER link |
|-------|------|----------|----------------|-----------------|
| **Fitness Coin (Fitcoin)** | `CrjAFUG78otnbSdD8rhmrWu9h9ukMN2CA4t1XA8zpump` | 2026-05-21 04:02 UTC | `FsBx6AQCv7AbzmTDqVCP8BDzyEVtUJYVMinh2SnekLAt` | Funded by G2BbetUgz (TREASURY-linked) — **CONFIRMED** |
| **SpaceX Cup (SPCXCUP)** | `H3VTG1ed8JH8K6q7A1Zk6NRJgksWfBs3q7peYUoxpump` | 2026-05-21 16:39 UTC | `GGxoBCGZQ1BcNQvjNuTqBkETyYYoYXmHi6Ld2t8wM13T` | Funded by EYjGUZam directly — **STRONG** |

Both launches used pump.fun AMM exclusively (not Raydium). The profit sweep from 94 buyer wallets began within minutes of the SpaceX Cup launch.

### Market-making wallet pattern (GGxoBCGZQ1B)

`GGxoBCGZQ1BcNQvjNuTqBkETyYYoYXmHi6Ld2t8wM13T` is the most active wallet observed. It:
- Receives SOL from EYjGUZam, buys tokens, sells tokens, sweeps profits back to EYjGUZam — **cycling continuously**
- Made 34 transactions on May 21 alone, alternating between LAUNCH(pump.fun) and SOL sweeps
- Traded both Fitcoin and SPCXCUP repeatedly throughout the day
- Is not a one-time creator wallet — it's a persistent market-making/price-support wallet

---

## May 21 Provisioning Wave (G2BbetUgz) — Active

| Attribute | Value |
|-----------|-------|
| Sub-provisioner | `G2BbetUgzETGhK8w43YbcwD9yx74CGg649z8nvWn6Ntd` |
| TREASURY funding | 70 SOL (May 20 21:26) + 55.8 SOL (May 21 05:05) + 70 SOL (May 21 10:33) = **195.8 SOL** |
| Fanout started | 2026-05-21 ~04:06 UTC |
| Fanout still active | Yes — confirmed at 17:03 UTC |
| Per-wallet amount | ~0.0142 SOL (varies slightly each tx) |
| Wallets provisioned | **5,000–9,000+** (fanout ongoing) |
| Wallets in wt_staged_wallets | 1,000 backfilled from RPC scrape — incomplete |

### Why so many wallets?

Three likely explanations, not mutually exclusive:
1. **Attrition buffer** — most wallets will never be activated. Operation provisions 50–100× what it needs and selects a subset via SIGNALLER.
2. **Pre-loaded inventory** — wallets sit dormant for weeks/months and are activated in future waves. April wallets were provisioned before April.
3. **Buyer wallets mixed in** — as seen in May 17-18, some provisioned wallets are buy-side supporters, not launchers. At 0.0142 SOL, this amount is consistent with a minimal "activation" balance for a buyer wallet.

**SIGNALLER activity is the key signal.** The subset of these 9k wallets that receive SIGNALLER dust = the real launch cohort. We are monitoring this via webhook.

---

## What We Still Don't Know

1. **How the buyer wallets were originally provisioned** — the 94 trading wallets behind the EYjGUZam sweep have substantial tx history (20–50 txs each) but we haven't traced their funding source. They may have been provisioned by a separate sub-provisioner not yet identified, or funded through non-TREASURY channels.

2. **The complete token list** — we confirmed Fitcoin and SPCXCUP. The buyer wallets may have traded additional tokens we haven't scanned for. Their full tx history only shows the last 50 sigs.

3. **EYjGUZam's full outbound history** — we know it receives from buyer wallets and distributes to market-makers like GGxoBCGZ. We haven't mapped all its outbound recipients. It may be funding more than one market-making wallet.

4. **The connection between G2BbetUgz provisioned wallets and the buyer layer** — are the 9k G2BbetUgz recipients the next wave of buyer wallets, or a new creator cohort, or both? We won't know until SIGNALLER activates some of them.

5. **How many tokens have actually launched** — we confirmed 2 today. With 106 April creators each launching tokens, the total launch count may be far higher than our 90-token figure. Many may have launched via Raydium or after our scan window.

6. **The PROFIT-RELAY 1 → TREASURY-UP flow** — N3TKf3w has 23k SOL flowing to TREASURY-UP. We haven't mapped which creator wallets feed into N3TKf3w vs EYjGUZam. Two separate profit collection networks, or the same one with different roles?

---

## April 2026 Wave

**Period:** April 27 – May 2026
**Creators confirmed:** 106
**Confirmation method:** Direct pump.fun fee payer observation
**Tokens launched:** 106 (April CONFIRMED cohort only — 92 migrated, 14 bonding curve)
**Fingerprint:** `0.10203928` SOL per creator wallet

All 106 wallets were observed paying pump.fun fees, linking them to specific mints. This is the only CONFIRMED cohort — we have direct on-chain evidence of launch activity.

---

## May 2026 Wave

**Period:** Provisioned May 17–18. No launches observed as of May 21.
**Creators confirmed:** 60 (STRONG evidence — 3-hop TREASURY trace, not fee payer)
**Fingerprint:** `0.01003928` SOL per creator wallet
**Status:** 45 wallets staged/dormant. 15 wallets with prior trading history (likely buyer/supporter wallets).

---

## TREASURY Activity (Post-May 17)

| Date | Recipient | Amount | Status |
|------|-----------|--------|--------|
| 2026-05-28 | `7J14JJYc5qoTDaWDeCUaQBu3r84iVRPUYUmt7weHNRqK` | 1,000+200 SOL | **OPERATOR MASTER WALLET — TAILUNG op (see case study)** |
| 2026-05-28 | `8qDEgY6u5JpBmCP1CShMFRSdHFka2FEdgUvfk8wbX4We` | 70 SOL | **SWARM PROVISIONER — TAILUNG buy wallets (now closed)** |
| 2026-05-21 | `EYjGUZamSQ9vJBxZ4yj7pCK2XaZ99MAEQx9xRMrzyMx1` | 600 SOL | **RECLASSIFIED: PROFIT-RELAY 2** |
| 2026-05-21 | `G2BbetUgzETGhK8w43YbcwD9yx74CGg649z8nvWn6Ntd` | 70+55.8+70 SOL | **CONFIRMED SUB-PROV — actively fanning 9k wallets** |
| 2026-05-20 | `F17dbo3EeumSte7hEBgn6wDAv65BEN4U8eba9zXcNTg` | 2,650 SOL | Unknown — fanout unconfirmed |
| 2026-05-20 | `3UnqbigDhZvHpQFR29aDhKLEWSNkFcMbuNCrSDk6iikj` | 70 SOL | Unknown |
| 2026-05-19 | `N3TKf3wMBNu8XmZsTSnk2xWQ2LjiGvUJh1ae9Lc3dW7` | 1,390 SOL | PROFIT-RELAY 1 (recycled profit back to TREASURY) |
| 2026-05-18 | `8XLpiAhWQoCDZjJKdiz9HPvvy1GCJCjF6Cks6Zss4tcH` | 140 SOL | Unknown |

---

## Current Platform State (2026-05-21 ~17:00 UTC)

| Metric | Value |
|--------|-------|
| Confirmed WATCHTOWER creators | 166 |
| April cohort (CONFIRMED) | 106 |
| May cohort (STRONG) | 60 |
| Staged / dormant May wallets | 45 (May 17-18 wave) |
| May 21 wave staged wallets | **4,992 staged** (4,947 backfilled + 45 live-caught) |
| Tokens launched (confirmed) | 106 (April cohort) + Fitcoin + SPCXCUP today |
| TREASURY outflows tracked | 216+ |
| SIGNALLER activations tracked | 422 |
| Buyer wallets identified | 94 (behind EYjGUZam sweep) |
| SOL swept by buyers today | 178+ SOL |
| Webhook status | Live |
| Sub-provisioner fanout detector | Webhook-triggered |
| Staged wallet poller | 10-min intervals |

---

## Detection System Architecture

### Live (Webhook-Driven)
- **Helius webhook** fires on TREASURY, SIGNALLER, TREASURY-UP, PROFIT-RELAY 1&2, all known SUB_PROVs
- New TREASURY outbound ≥50 SOL → fanout scan triggered (2 RPC calls)
- Fanout confirmed → sub-provisioner recorded, recipients added to `wt_staged_wallets`
- SIGNALLER dust → `watchtower_events` entry, candidate upgraded
- SUB_PROV outbound → auto-stages recipient in `wt_staged_wallets`
- PROFIT-RELAY inbound → `profit_sweep` event fired

### Known Gap — Backfill
- **G2BbetUgz was not in `_WT_INFRA_ROLES` until May 21 17:00** — its 9,000+ fanout recipients were not auto-staged. **4,947 wallets backfilled** via `getTransaction` RPC scrape of 14,843 signatures. Total in `wt_staged_wallets`: 4,992. Server restarted — live staging active for all future G2BbetUgz outbound.

### Polled (10-minute intervals)
- **Staged wallet poller** — checks `wt_staged_wallets` (DORMANT_FUNDED) for first outbound tx
- First tx classified: `pump_fee | raydium_lp | meteora_lp | large_sol_outbound | unknown`
- On activation → `watchtower_events` entry, state updated to ACTIVATED

### Known Gap — Creator → Migration Link (Not Yet Built)
The staged wallet poller detects when a creator wallet launches (`pump_fee` activation) but **does not extract the mint address** from that transaction, and **does not link back when that token migrates** from pump.fun → PumpSwap.

This means: if a G2BbetUgz-provisioned creator launches a token that migrates, the migration pipeline has no way to flag it as WATCHTOWER-linked. The join that would close this gap is:

```sql
SELECT ta.mint, ws.wallet_address AS creator
FROM wt_staged_wallets ws
JOIN token_analysis ta ON ta.earliest_tx_creator = ws.wallet_address
WHERE ws.first_move_type = 'pump_fee'
  AND ta.lifecycle_stage = 'migrated'
```

**What needs to be built:**
1. On `pump_fee` activation — parse the creation tx to extract the new mint, store it on `wt_staged_wallets.launched_mint`
2. Cross-join `wt_staged_wallets` ↔ `token_analysis` on creator address every 5 minutes — any migrated token whose creator is in `wt_staged_wallets` fires a `watchtower_events` alert
3. Surface in the UI as a distinct signal: **🔴 WATCHTOWER CREATOR MIGRATED**

Until this is built, WATCHTOWER-launched tokens that migrate will pass through the platform undetected.

---

## False Lead: `0.00203928`

In early May, 530 wallets were bulk-confirmed as WATCHTOWER-related based on matching the `00203928` amount in `creator_funders`. A 3-hop funder trace of 3 sampled wallets found no TREASURY link within 3 hops — all funding chains resolved to normal pump.fun trading activity.

All 530 were reverted. The lesson: **fingerprint without chain trace is WEAK evidence**. The `03928` suffix appeared in unrelated wallets by coincidence. The May 21 wave has dropped the suffix entirely.

---

---

## Staged Account Migration Catchability — Status Report

*As of 2026-05-21 ~19:00 UTC*

### The core question

Of the accounts stored in `wt_staged_wallets`, how many will be caught by the migration pipeline when they launch and migrate?

### Current staged wallet inventory

| Provisioner | Wallets staged | Wave | Status |
|------------|---------------|------|--------|
| `G2BbetUgz` | **4,947** | May 21 | DORMANT_FUNDED — no activations yet |
| `Gs7zXNYwdd2X1P` | **32** | May 17–18 | DORMANT_FUNDED |
| `C745erBxwn4s` | **13** | May 17 | DORMANT_FUNDED |
| **Total** | **4,992** | — | All DORMANT_FUNDED — none activated yet |

Zero activations recorded. The poller has not yet caught a first-move transaction on any staged wallet. The server restart required to activate live G2BbetUgz staging happened at ~17:00 UTC today.

---

### Likelihood of being caught at migration

**Current answer: near zero for all 4,992 staged wallets.**

Here is why, broken down by pipeline stage:

#### Stage 1 — Activation detection (staged poller → `pump_fee`)

The poller runs every 10 minutes and checks for first outbound tx. On `pump_fee` detection it will now call `_extract_mint_from_pump_tx()` and write to `wt_creator_launches`.

**Catchability here: HIGH** — once a wallet activates, the poller will see it within 10 minutes and bind the mint. The mint extractor looks for a `pump`-suffixed account with pre-balance=0, which is the standard pump.fun creation pattern.

**Risk:** Mint extractor is heuristic. If the creation tx structure differs from the assumed pattern (e.g. the creator uses a wrapper program, or the mint doesn't end in `pump`), the binding will fail silently and `launched_mint` will be NULL. No alert fires in that case.

#### Stage 2 — Migration detection (`_check_watchtower_migration`)

When a mint migrates, the hook in `pumpfun_curve_listener.py` cross-references the mint against `wt_creator_launches` and falls back to `token_analysis.earliest_tx_creator` → `wt_staged_wallets`.

**For the 4,992 staged wallets: catchability is ZERO right now.**

- `wt_creator_launches` table does not yet exist in the DB (created on next server restart)
- Even after restart, it will be empty until wallets activate
- The fallback join (`earliest_tx_creator` → `wt_staged_wallets`) only works if `token_analysis` has been populated with the creator address — this only happens after the platform independently discovers the token, which requires the token to be active on pump.fun and visible to the curve listener

**If the token is discovered independently before the staged wallet activates through the poller, the creator join will work.** But this is the exception, not the rule.

#### Stage 3 — Backfill at startup

The `_backfill_wt_creator_launches()` function runs 15s after server restart. It joins `wt_staged_wallets` ↔ `token_analysis` on `earliest_tx_creator` and `pf_ws_creator`.

**Current result from that join: 0 tokens found.**

None of the 4,992 staged wallets appear as a creator in `token_analysis` today. This means none of them have launched a token that the platform has seen. They are genuinely dormant.

---

### What we do catch today

The 92 migrated WATCHTOWER tokens that already exist in the system are **all from the April 2026 cohort** — 106 CONFIRMED creators identified via direct fee-payer observation. Their creator addresses are in `creator_risk_scores` but **not in `wt_staged_wallets`**, so the new `wt_creator_launches` pipeline does not cover them.

| Cohort | Creators | Launched tokens | Migrated | In wt_staged_wallets | Caught by new pipeline |
|--------|---------|----------------|---------|---------------------|----------------------|
| April (CONFIRMED) | 106 | 106 | **92** | 0 | ❌ No |
| May 17–18 (STRONG) | 60 | 0 | 0 | 45 | ✅ Will be when they launch |
| May 21 (G2BbetUgz) | 0 conf. | 0 | 0 | 4,947 | ✅ Will be when they launch |

The 92 already-migrated tokens are detected and scored, but through a separate pipeline (fee-payer observation, `creator_risk_scores.watchtower_related=1`). They were caught **before** the migration attribution gap existed as a problem — they were identified at creation time, not at migration time.

---

### Gaps

**Gap 1 — `wt_creator_launches` table doesn't exist yet**
The table is defined in `_ensure_watchtower_tables()` but that function only runs when the webhook fires or on server restart. Until a restart happens, the entire new pipeline is inert. The backfill, launch binding, and migration hook all depend on this table.
*Fix: restart the server.*

**Gap 2 — April cohort not in `wt_staged_wallets`**
The 106 April CONFIRMED creators were identified via fee-payer observation and stored in `creator_risk_scores`, not `wt_staged_wallets`. If any of them launch *additional* tokens in future, the new migration hook won't fire for them — it only checks `wt_staged_wallets`.
*Fix: seed `wt_staged_wallets` with the 106 April creators on restart, or extend `_check_watchtower_migration` to also check `creator_risk_scores.watchtower_related=1`.*

**Gap 3 — Mint extractor is heuristic, no failure alert**
`_extract_mint_from_pump_tx()` looks for accounts ending in `pump` with zero pre-balance. If this fails (wrong tx structure, wrapper program, non-standard mint), the activation is recorded with `first_move_type='pump_fee'` but `launched_mint=NULL`. The `WATCHTOWER_CREATOR_LAUNCHED` event never fires. Silent miss.
*Fix: add a fallback RPC call to get the token account owned by the creator wallet post-tx, and log a warning when extraction fails.*

**Gap 4 — 4,921 G2BbetUgz wallets not yet staged (still missing from backfill)**
The backfill extracted 9,868 recipients from 14,843 G2BbetUgz signatures but only 4,947 were inserted (4,921 already existed). The ~5,000 that "already existed" was actually a silent error — the wrong column names. After the fix, 4,947 unique wallets are now staged. But G2BbetUgz was still fanning as of 17:40 UTC — wallets provisioned after the backfill window (sig 14,843) are not yet staged unless the live webhook caught them. How many have been missed since 17:40 is unknown.
*Fix: re-run the sig scrape from sig 14,843 onward to catch the tail.*

**Gap 5 — Staged wallet poller polls 10-minute intervals, processes all wallets serially**
With 4,992 wallets all dormant, each poller cycle fires 4,992 `getSignaturesForAddress` RPC calls. At even 0.1s per call that is ~8 minutes per cycle — nearly the full interval. When activations start clustering (SIGNALLER event triggers multiple launches in minutes), the poller will lag significantly.
*Fix: on SIGNALLER dust event hitting a staged wallet, trigger immediate out-of-band activation check for that wallet rather than waiting for the next poll cycle.*

**Gap 6 — Migration hook only fires for tokens the curve listener discovers**
`_check_watchtower_migration` is called from `pumpfun_curve_listener.py`. If the curve listener is not running, or misses a migration event (network gap, restart), the hook never fires. The fallback backfill at startup will catch missed migrations retroactively — but only if the server restarts.
*Fix: add a periodic sweep (every 30 minutes) that queries `token_analysis` for newly-migrated tokens whose creator is in `wt_staged_wallets` and fires the event retroactively.*

---

---

## Reverse-Attribution Investigation — Migration-First Model

*As of 2026-05-21 ~19:30 UTC*

### The pivot

Rather than waiting for staged wallets to activate, we work backwards from confirmed operational outcomes: tokens that successfully migrated from pump.fun → PumpSwap. These are real, economically active events. The question is whether they reveal shared infrastructure, repeat actors, or WATCHTOWER branches we haven't mapped.

---

### What the migration data actually shows

**Total migrated tokens in DB:** 5,243  
**Last 14 days:** 1,650 migrations  
**Daily cadence:** 30–300 migrations/day (highly variable — 298 on Apr 28, 31 on May 7)  
**Creator resolved (last 14d):** 1,586/1,650 (96%) — `earliest_tx_creator` or `pf_ws_creator` populated  
**Creator unknown (last 14d):** 63 — migration recorded but no creator attributed

**WATCHTOWER-linked migrations in last 14 days: 0.**  
All 92 confirmed WT migrations occurred between 2026-04-11 and 2026-04-28. Nothing since.

---

### Prolific creators in recent migrations

The most operationally significant finding from reverse-attribution: a small number of creator wallets are responsible for a disproportionate share of migrations.

| Creator | Migrated tokens (14d) | WT-linked | Notes |
|---------|----------------------|-----------|-------|
| `bwamJzzt...` | **34** | No | 976 funder interactions, funded 189 SOL from single wallet |
| `AV7PjXHL...` | **18** | No | 19 funder interactions |
| `8oZiaf74...` | **13** | No | 13 funder interactions |
| `6ujZxnph...` | **11** | No | 180 funder interactions |
| `Gygj9QQb...` | **10** | No | Already known — market-maker for Fitcoin/SPCXCUP |
| `Dzp1SrZ4...` | **9** | No | — |
| `AK3xvfDF...` | **9** | No | — |
| `7ufmve7Z...` | **9** | No | — |
| `C7adgyY9...` | **8** | No | — |

None of these appear in `creator_risk_scores.watchtower_related=1`. None are in `wt_staged_wallets`.

**Key finding on `bwamJzzt`:** Its funders include three wallets (`452vsUD`, `5o18xTz`, `7HnmSHq`) that each sent exactly **0.01427496 SOL** — matching the G2BbetUgz May 21 fingerprint amount. **`bwamJzzt` is confirmed NOT WATCHTOWER.** It is a serial deployer with 976 funder interactions, a 189 SOL lump funder, and 34 repeated launches — the opposite profile of a WATCHTOWER creator. The amount match is coincidence or noise. This is a false positive in the fingerprint filter and means **`0.01427496` is not a reliable unique fingerprint** — it's an amount that appears in both WATCHTOWER provisioning AND in unrelated high-volume launchers' trading/funding activity.

---

### Why WATCHTOWER migrations stopped after April 28

Three possible explanations:

1. **April cohort ran out.** 106 creators, 92 migrated tokens, all in a 17-day window (Apr 11–28). If each creator launches ~1 token, the cohort is exhausted. The May cohort (60 STRONG creators, May 17–18 wave) has launched **zero** visible tokens so far.

2. **Detection gap.** The May cohort may be launching but the platform is not attributing them as WATCHTOWER because their creator wallets are not in `creator_risk_scores.watchtower_related`. The `wt_staged_wallets` → `token_analysis` join currently returns 0 tokens — these wallets haven't appeared as `earliest_tx_creator` in any migration yet.

3. **Operation shifted launch venue.** April cohort used pump.fun exclusively. Post-April activity may be using Raydium direct launches or other platforms that our pipeline doesn't attribute to the same creators. The curve listener only tracks pump.fun → PumpSwap migrations.

---

### What the reverse model needs that we don't have

**Gap A — No buyer/holder data per migration**  
The DB has `trade_simulations` and `trade_simulation_events` but no on-chain buyer history per mint. To answer "do migrated tokens share buyers?", we would need to either:
- Index the pump.fun AMM transaction logs per mint (not currently done)
- Use Helius `getAssetsByOwner` or parsed transaction history per mint  

Without this, Steps 3 (buyer layer), 4 (sweep analysis), and 6 (temporal buyer sync) from the proposed pipeline **cannot be executed from existing data alone**.

**Gap B — Profit sweep destination unknown for recent migrations**  
`watchtower_events` has 1,935 `profit_sweep` events but these are triggered by webhook hits on known PROFIT-RELAY addresses. For the 1,650 recent migrations by unknown creators, their profit destinations are not in the webhook list and therefore invisible.

**Gap C — No shared funder analysis tooling**  
`creator_funders` and `creator_infra_interactions` exist but there's no pipeline to cluster creators by shared funders. To find "do `bwamJzzt` and `AV7PjXHL` share upstream infrastructure?" requires a many-to-many funder graph traversal that isn't built yet.

**Gap D — `Gygj9QQb` is already known but not fully mapped**  
`Gygj9QQby4j2jryqyqBHvLP7ctv2SaANgh4sCb69BUpA` — the Fitcoin/SPCXCUP market-maker — appears as `pf_ws_creator` on 10 migrated tokens in 14 days and is already confirmed as funded by EYjGUZam (PROFIT-RELAY 2). It is not in `creator_risk_scores.watchtower_related`. **This is the most immediate false negative** — a confirmed WATCHTOWER-linked creator with 10 migrated tokens in 2 weeks, not flagged.

---

### Reverse-attribution scan results — recent migrations

Working backwards from 1,650 migrations in the last 14 days, filtering to creators with a single funding event at known WATCHTOWER fingerprint amounts:

| Fingerprint | Creators | Unique funders | Migrated tokens | Assessment |
|-------------|---------|----------------|----------------|------------|
| `~0.0142 SOL` (May 21 wave) | 29 | 34 | **74** | Unknown upstream — funders not in any WT table |
| `0.01003928` (May 17–18 wave) | 15 | 15 | **19** | Each funder is a unique 1-of-1 wallet — matches WT pattern exactly |
| `0.20303928` (unknown) | 46 | 46 | **59** | Parallel `03928` op, separate treasury stack. Active Apr 12 – May 18. **Not linked to known TREASURY.** See full investigation below. |
| `0.15253928` (unknown) | 5 | 5 | **5** | Same pattern |
| `0.30403928` (unknown) | 1 | 1 | **1** | Single case |

**The `0.20303928` cluster is significant.** Eight tokens migrated over May 8–18, each from a fresh creator funded by a unique single wallet at exactly `0.20303928 SOL`. This is the WATCHTOWER structural signature — 1 creator : 1 funder : 1 token — and the `03928` suffix is preserved. The funders are not in any existing infrastructure table. This is either:
1. A new sub-provisioner not yet mapped, operating in parallel to the known TREASURY branch
2. An extension of the May 17–18 wave using a different per-wallet amount

The `0.01003928` hits (15 creators, 19 migrations) are likely the May 17–18 wave actually launching — these match the known fingerprint exactly and have the same 1:1:1 structure.

**The `~0.0142` group (74 migrations)** is larger but has 34 different funders — each funding only 2–5 wallets. These may be G2BbetUgz-provisioned wallets that activated and launched, with their immediate funder being a pass-through wallet rather than G2BbetUgz directly. Or a separate operation entirely using a similar per-wallet amount.

**None of the funder wallets in any of these clusters appear in `creator_funders` themselves** — their own funding history is not in the DB, so we cannot trace them upstream to TREASURY from existing data. RPC investigation required to close this.

---

### `0.20303928` Cluster — Full RPC Investigation (2026-05-21)

Full scope (all migrations, not just 14-day window): **46 unique funders → 46 unique creators → 59 migrated tokens**, April 12 – May 18 2026.

**Architecture discovered (4 tiers):**

```
Tier 1 (Treasury): 5 high-capital wallets (200–1,400 SOL each)
  FoEjSxBvq79H...  (289 SOL)
  7YtJuczaj8TL...  (1,369 SOL)  
  ERGjpMqUfxv7...  (628 SOL)
  Gg2omVLVuWfU...  (214 SOL)
  EWwF4rZfUucg...  (1,163 SOL)
         ↓ batch fund (single tx each)
Tier 2 (Batch provisioners): per-group dispatch wallets
  7YtJuczaj8TL → funded 7 creator-funders in one tx
  FoEjSxBvq79H → funded 6 creator-funders in one tx
  ERGjpMqUfxv7 → funded 2 creator-funders
  Gg2omVLVuWfU → funded 2 creator-funders
  EWwF4rZfUucg → funded 1 creator-funder
         ↓ @0.20303928 SOL each
Tier 3 (Creator funders): 46 fresh wallets, 1-sig each
  Gdxq7aE2TGs1, 8L4GqWoZZoGs, AYyMgatG9PmZ, 67HmoWFQEUod...
         ↓ fund creator
Tier 4 (Creators): 46 fresh wallets, launch 1 token each
```

**Key distinctions from known WATCHTOWER:**

| Property | Known WATCHTOWER | `0.20303928` Cluster |
|----------|-----------------|----------------------|
| Treasury address | `44orWS68MqXG198M3YXyZoNrYtsNhgnNhtUT5SavqJFM` | **Different** — 5 unknown wallets |
| Sub-provisioners | 3UnqbigD, 67UoZBTB, G2BbetUgz | **Different** — 5 unknown batch wallets |
| Funding amount | `0.0100...` / `0.0142...` | **`0.20303928` SOL** (larger) |
| Batch dispatch | Single large fanout txs | **Same structure** — batch in single tx |
| Profit drain | Drains to CEX or PROFIT-RELAY | **Swept and closed** — drain wallets deleted |
| TREASURY linkage | Yes (confirmed via RPC) | **Not confirmed** — different wallet graph |

**Assessment:** This is a **parallel `03928`-suffix operation with a separate funding stack**. The `03928` amount suffix is the same family of operation (preserving the signature pattern), but the treasury, provisioners, and drain wallets are entirely distinct from the known WATCHTOWER infrastructure. All tier-1 and drain wallets have been fully swept and closed. The operation was active April 12 – May 18 with 59 confirmed migrations.

**This cluster is NOT connected to the WATCHTOWER TREASURY `44orWS68...`.** Checking all 50 signatures of each tier-1 wallet against TREASURY found zero matches. The cluster shares only the `03928` naming convention — it may be the same operator using a different wallet stack, or a copycat operation.

---

### The defining WATCHTOWER creator fingerprint

Data confirms a structural property of WATCHTOWER creator wallets that distinguishes them from serial launchers like `bwamJzzt`:

**Every WATCHTOWER creator has exactly one funder, and that funder is unique to them.**

From 166 confirmed/strong WT creators:
- 60 have zero entries in `creator_funders` (staging data not yet resolved)
- 61 have exactly **1 funder** — and every one of those single funders is a different address (the sub-provisioner wallet that funded only them)
- The remaining 45 have 2–19 funders — these are the April cohort where CONFIRMED evidence came from fee-payer observation, not funding chain alone; some may have pre-existing wallet history

This means `bwamJzzt` — with 976 funder interactions, a 189 SOL lump-sum from a single large wallet, and 34 repeated launches — is definitively **not WATCHTOWER**. It is a serial launcher using a personal funded wallet, the opposite of the WATCHTOWER model.

The WATCHTOWER model is:
- **1 creator wallet : 1 token : 1 funding event : 1 funder**
- Creator wallet is fresh — zero prior history
- Funder wallet is a sub-provisioner that funds hundreds of others at the same fingerprint amount
- No shared funders across creators — the sub-provisioner is the shared node, not the creator

`bwamJzzt` and the other prolific creators in recent migrations are **not the same operation**. They represent normal high-volume retail/bot launching behaviour and are correctly not flagged as WATCHTOWER-related.

### Full breakdown of all 166 creators

Accounting for all 166 confirmed/strong/weak creators:

| Group | Count | Explanation |
|-------|-------|-------------|
| CONFIRMED, 1 funder | 61 | Clean signal: single sub-provisioner, fresh wallet, 1 funding event |
| CONFIRMED, 2–19 funders | 45 | Same pattern underneath — 1 large lump (sub-provisioner) + noise |
| STRONG, 0 funders in DB | 45 | May 17–18 staged wallets — `creator_funders` not yet populated |
| WEAK, 0 funders in DB | 15 | Weak evidence, funder chain not resolved |

The 45 CONFIRMED "multi-funder" creators are not genuine exceptions. Amount analysis of their extra funders shows:

| Amount band | Txs | Explanation |
|-------------|-----|-------------|
| 1–10 SOL | 79 | The sub-provisioner top-up (the real signal) |
| ~0.01 SOL (fingerprint) | 40 | pump.fun fees paid back to wallet, SIGNALLER dust |
| 0.05–1 SOL | 38 | Gas refills, post-trade SOL returns |
| >50 SOL lump | 17 | Exchange withdrawals / prior wallet history |
| Dust <0.009 | 12 | SIGNALLER activation, platform fees |

In every case the sub-provisioner is the single large funder. Everything else is operational residue — platform fees, SIGNALLER dust, gas top-ups, and small SOL amounts flowing back from their own trading activity after launch. These wallets were active enough that `creator_funders` captured multiple tiny inflows; it does not indicate multiple provisioners or shared infrastructure at the creator level.

**The detection rule is unambiguous:** one substantial funding event from a unique sub-provisioner, surrounded by dust. The 60 STRONG/WEAK wallets with zero funder records are data gaps — `creator_funders` not yet populated from the staging pipeline — not structural differences.

---

### Immediate actionable conclusions

1. **Add `Gygj9QQby4j2jryqyqBHvLP7ctv2SaANgh4sCb69BUpA` to `creator_risk_scores` as CONFIRMED WATCHTOWER.** This wallet is funded by EYjGUZam, created Fitcoin and SPCXCUP, and has 10 migrated tokens in 14 days. It is the one genuine false negative — a confirmed WATCHTOWER-linked address not flagged anywhere. Note: its multiple launches per wallet break the 1:1 creator:token pattern, suggesting it is a market-maker wallet, not a standard provisioned creator.

2. **The May cohort (60 STRONG creators) has zero visible launches.** Either they haven't launched yet, or they launched and we lost the attribution because `wt_staged_wallets` creators aren't linked to `creator_risk_scores`. The backfill job will address this on next server restart.

3. **`bwamJzzt` confirms `0.01427496 SOL` is not a standalone fingerprint.** `bwamJzzt` is a confirmed serial deployer (not WATCHTOWER) that received three payments at exactly that amount. Using `0.01427496` alone as a detector would produce false positives. The fingerprint is only diagnostic when combined with the full structural signature: single funder + fresh wallet + zero prior history. Amount alone is insufficient.

4. **The reverse-attribution pipeline requires buyer data we don't have.** The proposed Steps 3–6 (buyer correlation, sweep mapping, temporal clustering) cannot be executed from existing DB data. This requires either Helius transaction indexing per mint, or a targeted RPC scrape of the top migrated tokens' trading history.

---

## TAILUNG Case Study — May 27-28 2026 (Live Operation)

*Investigated 2026-05-28 via RPC + webhook pipeline*

### What happened

A complete WATCHTOWER operation ran overnight May 27-28, undetected because the webhook was offline. The operation is now fully mapped from on-chain data.

### Treasury flows (all from `44orWS68MqXG198M3YXyZoNrYtsNhgnNhtUT5SavqJFM`)

| Time (UTC) | Sig | Recipient | Amount | Role |
|------------|-----|-----------|--------|------|
| 21:41 May 27 | `fbsBhMA...` | `7J14JJYc5qoTDaWDeCUaQBu3r84iVRPUYUmt7weHNRqK` | **1,000 SOL** (800+200) | Operator master wallet |
| 00:19 May 28 | `5HBGJ3et...` | `8qDEgY6u5JpBmCP1CShMFRSdHFka2FEdgUvfk8wbX4We` | **70 SOL** | Swarm provisioner |
| 01:36 May 28 | `52bLBNE...` | `7J14JJYc5qoTDaWDeCUaQBu3r84iVRPUYUmt7weHNRqK` | **200 SOL** | Operator top-up |

### Operation timeline

| Time (UTC) | Event |
|------------|-------|
| 21:41 May 27 | Treasury → `7J14JJYc` 1,000 SOL — operator capitalised |
| 22:27 May 27 | `7J14JJYc` → gas seed `V81CPdm` via 3-hop relay (0.11 SOL) |
| 22:27 May 27 | `V81CPdm` deploys **TAILUNG** (`BoDMS8u1rP3tvw3Zte9PNXWqB1rAbFwRxw5SYxVFpump`) on pump.fun (tx `jP5uXx3P...`) |
| 22:28 May 27 | `V81CPdm` immediate sell — 1.35 SOL extracted, swept to `7J14JJYc` |
| 00:19 May 28 | Treasury → `8qDEgY6u5` 70 SOL — swarm provisioning begins |
| 00:19–04:01 | `8qDEgY6u5` fans 70 SOL out to **~4,235 unique swarm wallets** who buy TAILUNG bonding curve |
| 05:00 May 28 | Bonding curve closes — `V81CPdm` receives ~160.5 SOL, sweeps to `7J14JJYc` |
| 05:56–06:00 | **~5,000 swarm wallets simultaneously sweep profits** back to `7J14JJYc` (~35 SOL) and `8qDEgY6u5` (~1.2 SOL) |
| 06:00 May 28 | `V81CPdm` sweeps final 15.2 SOL tranche to `7J14JJYc` |

### Profit summary

- `V81CPdm` (deployer): breakeven on wallet — all flows through to `7J14JJYc`
- `7J14JJYc` aggregate inflow from this operation: **~177+ SOL** from deployer sweeps + swarm profits
- Operator seeded deployer with **0.11 SOL gas**, treasury seeded swarm with **70 SOL**, returned **~177 SOL** — gross profit ~107 SOL on the swarm arm alone

### Structural pattern — two parallel arms

This operation uses a cleaner two-arm structure than the April cohort:

```
TREASURY (44orWS68)
    ├─► 7J14JJYc (1,000 SOL + 200 SOL) — OPERATOR MASTER WALLET
    │       └─► gas seed → V81CPdm (fresh deployer wallet)
    │                   └─► launches TAILUNG on pump.fun
    │                   └─► all profits sweep back to 7J14JJYc
    │
    └─► 8qDEgY6u5 (70 SOL) — SWARM PROVISIONER
            └─► ~4,235 buy wallets → buy TAILUNG bonding curve
            └─► profits sweep back to 7J14JJYc (via relay)
```

`7J14JJYc` is the single collection point for both arms. It receives capital from treasury and collects all profit. It is NOT a sub-provisioner in the traditional sense — it is a **persistent operator wallet** being topped up repeatedly.

### Why the webhook missed it

- Webhook `106e20f6` was live but the app was not running / ngrok tunnel was down when the 1,000 SOL tx landed at 21:41 May 27
- Even if the webhook had been live, `_WT_SWARM_TREASURY_SOL = 70.0 ±10` would only have triggered on the 70 SOL move at 00:19 — **1 hour 52 minutes after the token was already deployed**
- The 1,000 SOL move to `7J14JJYc` — the real operational kickoff — would have been caught by the ≥50 SOL detector, but only if `7J14JJYc` was unknown. Once enrolled, all subsequent activity on that wallet would have been real-time

### Detection gap — current threshold is too late

The 70 SOL swarm provisioner gate (`_WT_SWARM_TREASURY_SOL`) fires *after* the token is live and trading. The earlier signal is the **operator wallet top-up** — a large lump (100–1,000 SOL) from treasury to an unknown wallet hours before the launch.

**Recommended fix:** add a second threshold — any treasury outbound ≥100 SOL to an unknown wallet → immediately enrol it in the webhook and mark as `OPERATOR_WALLET`. The existing 70 SOL gate covers the swarm provisioner; the new gate covers the operator master wallet. Together they would catch the operation at T-2hrs instead of T+0.

### New infrastructure addresses (confirmed May 28)

| Address | Role | Evidence |
|---------|------|----------|
| `7J14JJYc5qoTDaWDeCUaQBu3r84iVRPUYUmt7weHNRqK` | Operator master wallet | Direct treasury funding (1,000+200 SOL); receives all deployer + swarm profits |
| `8qDEgY6u5JpBmCP1CShMFRSdHFka2FEdgUvfk8wbX4We` | Swarm provisioner | 70 SOL from treasury; fanned to ~4,235 buy wallets (9,235 total txs); ~5,000 recycle sweeps 05:56–06:00; now closed |
| `V81CPdmJmwX1ZbcSpUciXWx2fa6JffNjJTcjAUCD33Z` | Token deployer | Gas-seeded by `7J14JJYc`; launched TAILUNG; all profits to `7J14JJYc` |

---

## CHUBBYGOSLING Case Study — May 27-28 2026 (Parallel Operation)

*Investigated 2026-05-28 via RPC*

### Same pattern, larger scale

CHUBBYGOSLING ran concurrently with TAILUNG using the **identical two-arm structure**. The only structural difference is an additional relay hop (`Dw7xNxxw`) between treasury and the operator/provisioner, adding one layer of obfuscation.

### Treasury flows

| Time (UTC) | Sig | Path | Recipient | Amount | Role |
|------------|-----|------|-----------|--------|------|
| 11:28 May 27 | `3CCrNazN...` | Treasury → `Dw7xNxxw` → direct | `7DDbDeg2XwXJB4M9S6UU4y6zcKgF4oxz99K6kmwDzU46` | **800 SOL** | Operator master wallet |
| 11:28 May 27 | `42pyvF9y...` | Treasury → `Dw7xNxxw` → direct | `2ESU1NqEYiGcV3vvgTCqNpciCynuvGNk7t3TJcTgCZ5T` | **300 SOL** | Swarm provisioner (initial) |
| 13:06 May 27 | `2SVmfepq...` | `Dw7xNxxw` → direct | `2ESU1NqEYiGcV3vvgTCqNpciCynuvGNk7t3TJcTgCZ5T` | **276 SOL** | Swarm provisioner top-up |
| 15:42 May 27 | `NicHr4jj...` | Treasury → `Dw7xNxxw` | relay | **10 SOL** | Relay gas |
| 15:44 May 27 | `47Wa5nuh...` | Treasury → `Dw7xNxxw` | relay | **490 SOL** | Relay capital |
| 15:44 May 27 | `4NQHe8oH...` | `Dw7xNxxw` → direct | `2ESU1NqEYiGcV3vvgTCqNpciCynuvGNk7t3TJcTgCZ5T` | **500 SOL** | Swarm provisioner top-up |

Total provisioner funding: **1,076 SOL** across three tranches. Operator: **800 SOL**.

### Swarm scale

`2ESU1NqEYiGcV3vvgTCqNpciCynuvGNk7t3TJcTgCZ5T` — **22,000+ total transactions**, 2026-05-27 ~15:30 to 2026-05-28 08:31:

- Seeding phase: ~11,000 wallets funded at ~0.01 SOL each
- Recycle phase: ~11,000 wallets swept profits back to provisioner
- Profits routed: `2ESU1NqE` → `Dw7xNxxw` → `N3TKf3wM` (**PROFIT-RELAY 1** — confirmed infrastructure)

This is **2.5× the scale of TAILUNG** (22,000 vs 9,235 txs).

### Structural diagram

```
TREASURY (44orWS68)
    └─► Dw7xNxxwuBnTfpwSrZMhcAQqFp4XNe9XNaVhhsvyx6Da — PERSISTENT RELAY HUB
            ├─► 7DDbDeg2XwXJB4M9S6UU (800 SOL) — OPERATOR MASTER WALLET
            │       └─► gas seed → token deployer (TBD)
            │                   └─► launches CHUBBYGOSLING on pump.fun
            │                   └─► all profits sweep back to 7DDbDeg2
            │
            └─► 2ESU1NqE (300+276+500 = 1,076 SOL) — SWARM PROVISIONER
                    └─► ~11,000 buy wallets → buy CHUBBYGOSLING bonding curve
                    └─► profits sweep back → Dw7xNxxw → N3TKf3wM (PROFIT-RELAY 1)
```

### Pattern comparison — TAILUNG vs CHUBBYGOSLING

| | TAILUNG | CHUBBYGOSLING |
|---|---------|---------------|
| **Treasury → operator** | Direct (800+200 SOL → `7J14JJYc`) | Via `Dw7xNxxw` relay (800 SOL → `7DDbDeg2`) |
| **Treasury → provisioner** | Direct (70 SOL → `8qDEgY6u5`) | Via `Dw7xNxxw` relay (1,076 SOL → `2ESU1NqE`) |
| **Provisioner txs** | 9,235 | 22,000+ |
| **Wallets seeded** | ~4,235 | ~11,000 |
| **Profit route** | Swarm → operator direct | Swarm → relay → PROFIT-RELAY 1 |
| **Relay hop** | None (treasury-direct) | `Dw7xNxxw` persistent hub |

**Conclusion: identical two-arm structure.** Same treasury. Same operator-wallet + swarm-provisioner pattern. The relay is cosmetic — it adds one hop but doesn't change the operational logic. CHUBBYGOSLING is the same playbook at 2.5× scale.

### New infrastructure addresses (confirmed May 28)

| Address | Role | Evidence |
|---------|------|----------|
| `Dw7xNxxwuBnTfpwSrZMhcAQqFp4XNe9XNaVhhsvyx6Da` | Persistent relay hub | Routes treasury capital to operators + provisioners; receives profits back; also routes to PROFIT-RELAY 1 |
| `7DDbDeg2XwXJB4M9S6UU4y6zcKgF4oxz99K6kmwDzU46` | Operator master wallet (CHUBBYGOSLING) | 800 SOL via relay; equivalent role to `7J14JJYc` in TAILUNG |
| `2ESU1NqEYiGcV3vvgTCqNpciCynuvGNk7t3TJcTgCZ5T` | Swarm provisioner (CHUBBYGOSLING) | 1,076 SOL via relay; 22,000+ txs; seeded ~11,000 wallets |

---

## Corridor Resolution — Helio Payment Pattern

**Observed: 28 May 2026**

Two WATCHTOWER corridors fired and expired F5M on the same day without token launches. Both resolved as **Helio payments** instead.

### Pattern

| | Corridor 1 | Corridor 2 |
|---|---|---|
| Wallet | `B1zy7csmEnUC7Ma77V4sAE3pX16H1See7FwGxf4fHctp` | `7sUuvJaEVo739aNjbYoGpC4CUQmdcvumQjrdCTAEDqf9` |
| TREASURY funded | 10 SOL at 14:14:16 | 10 SOL at 00:17:09 |
| SIGNALLER lag | 116s, 10 dust txs | unknown |
| Resolution | `singleSolPayment` → 3.64 SOL | `singleSolPayment` → 3.60 SOL |
| Helio fee wallet | `FudPMePeNqmnjMX19zEKbBFwHnBgHvRFbnbtaG1cfbmo` | same |

Both operators made near-identical ~$290-295 USD payments via **Helio Program 1** (`dHeNgNVXeGzahCjGMVnRZbWaGqmB8MTNLPqKUcKqm8z`) within the same day. The consistent amount suggests they're paying for the **same service** — likely a bot, launch tool, or automation subscription used by WATCHTOWER operators.

### Detection Rule

Any corridor wallet whose **first non-dust tx after SIGNALLER activation** is a `singleSolPayment` to Helio Program → classify as **HELIO_PAYMENT**, mark corridor **ABORTED** immediately without waiting for F5M expiry.

**Helio fingerprint:**
- Instruction: `singleSolPayment`
- Payment: 3.5–3.7 SOL to merchant
- Fee: ~0.033 SOL to `FudPMePe…`
- Protocol fee: ~0.003 SOL to `JBGUGPm…`

---

## Open Questions / Active Predictions

1. **Will the 9k G2BbetUgz wallets launch tokens?** SIGNALLER activation is the trigger. If SIGNALLER dusts a subset, that's the real launch cohort — the rest are buyers or decoys. Falsification: if no SIGNALLER activity on any G2BbetUgz wallet within 7 days, reclassify as buyer-only provisioning.

2. **Are the 94 buyer wallets linked to a known sub-provisioner?** Their funding source is unknown. If they trace back to TREASURY via a separate provisioner, that's a parallel buyer-provisioning infrastructure we haven't mapped.

3. **How many tokens have launched from the April cohort that we missed?** 106 creators, 90 tokens confirmed. Some may have launched via Raydium after our scan window. GGxoBCGZ was actively trading tokens today that aren't in our DB — implies more launches we don't have visibility into.

4. **Is EYjGUZam a recycling node or a separate profit stream?** It receives from buyers AND distributes to market-makers. Its full outbound map is unknown. Could be a self-contained buy-side treasury separate from PROFIT-RELAY 1 (N3TKf3w).

5. **What is `F17dbo3EeumSte7hEBgn6wDAv65BEN4U8eba9zXcNTg` doing with 2,650 SOL?** Largest single TREASURY outflow we've seen. Fanout not confirmed. At the May 21 fingerprint (~0.0142 SOL), that's ~186,000 potential wallets. Needs fanout scan.

---

## May 29 — Live Session Findings

### Treasury Outflows (May 28–29)

| Time (UTC) | Sig (partial) | From | To | Amount | Role | Logged? |
|---|---|---|---|---|---|---|
| May 17, 05:17 | `J3cd2tHT…` | TREASURY | `C745erBx…` | 2 SOL | SUB_PROV | pre-watchtower |
| May 17, 20:44 | `37Uc2zoN…` | TREASURY | `Gs7zXNYw…` | 1,000 SOL | SUB_PROV | pre-watchtower |
| May 28, 00:19 | `5HBGJ3et…` | TREASURY | `8qDEgY6u5JpBmCP1CShMFRSdHFka2FEdgUvfk8wbX4We` | 70 SOL | SWARM_PROV | ✅ caught |
| May 27, 12:31 | `3qz1soJi…` | TREASURY | `AU1RkiGX5pE5Cr5warps6hbrwxVCBTbomFKvJJ9RXAm3` | 900 SOL | RELAY? | ❌ missed |
| May 28, 18:56 | (webhook) | TREASURY | `54LthT7ohPSe7yqVtnaq…` | 900 SOL | OPERATOR | ✅ caught |
| May 29, 05:29 | `2SKS8mZt…` | TREASURY | `8g2qFR27iDPZimUpmaySqg8TgmNKTDAwimEdBB6w96mn` | 200 SOL | TRADING CAPITAL MGR | ❌ missed |

### May 28 Full Timeline — SWARM + OPERATOR deployment

| Time (UTC) | Event |
|---|---|
| 00:19 | TREASURY → `8qDEgY6u5JpBmCP1CShMFRSdHFka2FEdgUvfk8wbX4We` 70 SOL (SWARM_PROV) |
| 03:16 | Swarm buyer `FaR1CU8XMhXX…` buys TAILUNG on Pump.fun AMM |
| 17:13 | `8qDEgY…` tops up `FaR1CU…` with 0.017 SOL gas |
| 18:56 | TREASURY → `54LthT7ohPSe7yqVtnaq…` 900 SOL — OPERATOR_WALLET_CANDIDATE detected |
| 18:57 | `54LthT7o…` fans out gas to ~60 deployer wallets, first batch webhook-enrolled |
| 19:00 | More deployers gassed (429 rate limit errors on webhook enrollment) |
| 19:06 | Final deployer wave (`2BDCU3K…` enrolled) |
| 20:10 | `EvQeohwRCq…` token launches — creator linked to `54LthT7o…`, flagged POSSIBLE STEALTH score=32 |

**74 minutes** from 900 SOL receipt to first token launch.

### `AU1RkiGX5pE5Cr5warps6hbrwxVCBTbomFKvJJ9RXAm3` — Unknown Relay

- Received 900 SOL from TREASURY at 12:31 UTC May 27
- ~2 hours later fanned out 50 txs across ~180 slots (~72 seconds)
- Balance now 0 — fully deployed
- Largest single outflow: **886 SOL back to TREASURY** — net cost ~14 SOL
- Role unclear — may be a consolidation/test relay or failed operation
- Not previously known; not caught by webhook (was not enrolled)

### `8g2qFR27iDPZimUpmaySqg8TgmNKTDAwimEdBB6w96mn` — Trading Capital Manager

- Received 200 SOL from TREASURY at 05:29 UTC May 29
- Balance: 199.77 SOL (capital sitting undeployed at time of observation)
- Operates a rapid buy/sell cycle on pump.fun via funded buyer wallets
- Uses SOL→WSOL wrap/unwrap via throwaway intermediary (`EUnVDB7L3pUp…`) to obscure direct link to buyer wallets
- Cycle time: ~2 minutes (fund → buy → sell → return profit)
- Confirmed buyer wallet: `FCCbc1xCdxqTXFoT7i3uCSswDHu7ZQbE5W9rTD55P1Ze`
  - 05:30:32 — receives 2.12 SOL via wrap intermediary
  - 05:30:33 — buys on pump.fun (~2.0 SOL)
  - 05:32:53 — sells (~2.66 SOL return)
  - 05:32:56 — returns 2.78 SOL to `8g2qFR27…` (~0.54 SOL profit)

### Infrastructure Updates

**`C745erBx…` and `Gs7zXNYw…` SUB_PROV status confirmed** — previously marked "needs re-verification". On-chain txs show direct TREASURY transfers (2 SOL and 1,000 SOL respectively on May 17). Comments updated in `_WT_INFRA_ROLES`.

**Webhook gap fixed** — TREASURY and all known infra addresses were not enrolled in the INFRA webhook at startup. The `init_webhook_system()` now calls `_enroll_permanent_infra()` on every startup to ensure all 10 core addresses (TREASURY, SIGNALLER, TREASURY_UP, PROFIT_RELAY, all SUB_PROVs) are permanently subscribed. Previously missing outflows like the 200 SOL → `8g2qFR27…` will now be caught immediately.

### Amount → Role Fingerprint (confirmed patterns)

| Treasury outflow amount | Role |
|---|---|
| 70 SOL | SWARM_PROV — funds ~5,000 micro buyer wallets (~0.014 SOL each) |
| 200 SOL | TRADING CAPITAL MGR — rapid pump.fun buy/sell cycling |
| 488–1,000 SOL | SUB_PROV — funds operator wallets for token factory operations |
| 900 SOL | OPERATOR — fans out 0.14–0.50 SOL to ~60 deployer/creator wallets |


---

## May 29 — Signaller Infrastructure & Helio Payment Pattern (Session 2)

### New Infrastructure Address

| Address | Role | Evidence |
|---|---|---|
| `44o1Hecb4QUhqcRNYJBC6XZoeHWzkWAvenR5YYHRGbFM` | SIGNALLER_2 | Fires 0.00001 SOL dust in lockstep with `44orA1Bx…` to operator wallets; same `44o` vanity prefix family |

**Vanity prefix family confirmed:** `44orWS68…` (TREASURY), `44orA1Bx…` (SIGNALLER), `44o1Hecb…` (SIGNALLER_2) — all deliberately generated with `44or`/`44o` prefix by the same operator.

### Operator Payment Wallet Pattern — `EUHceuJtFjvLgC4TKUs5BytbkwzYRFS56o5smpmqhDYh`

**Trigger:** TREASURY → `EUHceuJt…` 10 SOL

**Sequence:**
1. SIGNALLER (`44orA1Bx…`) and SIGNALLER_2 (`44o1Hecb…`) fire alternating 0.00001 SOL dust pings to `EUHceuJt…` repeatedly — dual confirmation signal
2. `EUHceuJt…` → **3.63 SOL `singleSolPayment`** to Helio (bot subscription fee)
3. `EUHceuJt…` → 6.37 SOL → `GcjLML…` → wrap WSOL → unwrap → **back to TREASURY** (change, via wrap obfuscation)

**Net cost: ~3.63 SOL** (~$298). Same Helio subscription amount seen in previous corridor resolutions (May 28). This is a recurring operator cost — they pay per deployment/session via Helio.

`EUHceuJt…` is a single-purpose **payment intermediary** — not a deployer or trader. TREASURY funds it with 10 SOL, it pays the Helio bill, returns the remainder to TREASURY via wrap/unwrap obfuscation.

### Wrap/Unwrap Obfuscation Pattern (confirmed recurring)

Used in two confirmed cases today:
- `8g2qFR27…` → `EUnVDB…` → wrap → unwrap → `FCCbc1…` (funding buyer wallet)
- `EUHceuJt…` → `GcjLML…` → wrap → unwrap → TREASURY (returning change)

**Fingerprint:** `createIdempotent` (Associated Token Program) + `syncNative` (Token Program) + `closeAccount` (Token Program) in same tx = SOL routing with no economic effect. Flag as obfuscation hop.

### Astralane Integration

- `astraRVUuTHjpwEVvNBeQEgwYx9w9CFyfxjYoobCZhL` = Astralane fee/signal wallet (domain: `4.astralane.sol`)
- Receives 0.00001 SOL dust ping when Astralane bot is activated
- Linked to `8g2qFR27…` trading capital manager (200 SOL from TREASURY)
- Astralane is the execution layer for the rapid pump.fun buy/sell cycling


---

## Treasury Outflow Amount → Operation Type Reference

When TREASURY (`44orWS68…`) sends SOL, the **amount is the operation fingerprint**. Each amount maps to a distinct role in the infrastructure:

---

### 10 SOL → Operator Payment / Helio Subscription

**Purpose:** Pay for bot/tool subscriptions (Helio platform).

**Sequence:**
1. TREASURY → payment wallet (10 SOL)
2. SIGNALLER-1 + SIGNALLER-2 ping the payment wallet repeatedly (dual activation)
3. Payment wallet → ~3.63 SOL `singleSolPayment` to Helio
4. Remainder → wrap/unwrap obfuscation → back to TREASURY

**Net cost:** ~3.63 SOL (~$298). Recurring per deployment session.
**Confirmed examples:** `EUHceuJt…` (May 29 01:44 UTC), two corridor wallets (May 28)

---

### 70 SOL → Swarm Provisioner (SWARM_PROV)

**Purpose:** Fund a swarm provisioner that seeds thousands of micro buyer wallets.

**Sequence:**
1. TREASURY → SWARM_PROV wallet (70 SOL)
2. SWARM_PROV fans out ~0.014 SOL to ~5,000 buyer wallets
3. Buyer wallets execute coordinated buys on pump.fun bonding curve
4. Volume/price manipulation — artificial FOMO generation

**Net deployment:** ~70 SOL ÷ 0.014 = ~5,000 wallets per provisioner.
**Confirmed examples:** `8qDEgY6u5JpBmCP1CShMFRSdHFka2FEdgUvfk8wbX4We` (May 28 00:19 UTC), `5DYd3VB2…`, `4nGZq5q4…`, `89KuKNoY…`

---

### 200 SOL → Astralane Trading Capital

**Purpose:** Fund automated pump.fun trading via Astralane bot platform.

**Sequence:**
1. TREASURY → trading orchestrator wallet (200 SOL)
2. 2 seconds later: `44osr4T83…` triggers `B91swy…` → dust ping to `astraRVUu…` (4.astralane.sol) + signals to 3 other wallets
3. SIGNALLER-1 + SIGNALLER-2 ping the orchestrator ~10 times alternating (arm sequence)
4. Orchestrator deploys capital to 5 buyer wallets via wrap/unwrap intermediary
5. Buyer wallets trade pump.fun tokens (buy → sell cycles in ~2 min)
6. Profits return to orchestrator; orchestrator redeploys for next round
7. Orchestrator pings TREASURY with dual 0.00001501/0.000015 SOL signals (status report)

**Astralane fee:** collected per-trade by `astraRVUu…` directly from AMM transactions.
**Confirmed example:** `8g2qFR27iDPZimUpmaySqg8TgmNKTDAwimEdBB6w96mn` (May 29 05:29 UTC) → traded BUTTHOLE token with 5 buyer wallets (`FCCbc1…`, `2e5thvkB…`, `CujxrUZZ…`, `H6d6xJuV…`, `G7Dg6Xu7…`)

---

### 900 SOL → Operator Wallet (Token Launch Arm)

**Purpose:** Fund an operator that arms ~60–100 deployer/creator wallets to launch tokens on pump.fun.

**Sequence:**
1. TREASURY → operator wallet (900 SOL)
2. Operator immediately fans out 0.14–0.50 SOL gas to ~60 deployer wallets
3. Each deployer wallet launches a token on pump.fun (~74 min from funding to first launch)
4. Tokens flagged by watchtower as POSSIBLE STEALTH / coordinated

**Confirmed example:** `54LthT7ohPSe7yqVtnaq…` (May 28 18:56 UTC) → `EvQeohwRCq…` token launched 20:10 UTC (score=32, POSSIBLE STEALTH)

---

### 800–1,000 SOL → Sub-Provisioner (SUB_PROV)

**Purpose:** Long-lived provisioner wallet that funds multiple operator cycles over time.

**Sequence:**
1. TREASURY → SUB_PROV (large lump sum)
2. SUB_PROV fans out 0.7–1.3 SOL to creator/operator wallets on an ongoing basis
3. Multiple token launch campaigns funded from single SUB_PROV over days/weeks

**Confirmed examples:**
- `CcdyBAT7…` — 800 SOL from TREASURY, 0.7–1.3 SOL fanout
- `Dw7xNxxw…` — 488 SOL, persistent relay hub (routes to operators + provisioners)
- `Gs7zXNYw…` — 1,000 SOL (May 17)
- `C745erBx…` — 2 SOL (May 17, smaller allocation)

---

### Summary Table

| Amount | Role | Next step | Detection window |
|---|---|---|---|
| **10 SOL** | Helio payment wallet | Pays ~3.63 SOL subscription, returns change | Immediate — dual SIGNALLER pings |
| **70 SOL** | SWARM_PROV | ~5,000 buyer wallets seeded in minutes | Immediate — webhook catches fanout |
| **200 SOL** | Astralane trading orchestrator | 5 buyer wallets armed, trading begins ~1 min | Immediate — Astralane signal chain fires |
| **900 SOL** | Operator (launch arm) | ~60 deployers gassed, first launch ~74 min | Immediate — operator fans out within seconds |
| **800–1,000 SOL** | SUB_PROV | Ongoing operator funding over days/weeks | Delayed — fanout is slow and distributed |


---

## May 29 — N3TKf3wM Reclassification & Signaller Chain Discovery

### N3TKf3wMBNu8XmZsTSnk2xWQ2LjiGvUJh1ae9Lc3dW7 — Reclassified SUB_PROV

Previously classified as `PROFIT_RELAY`. Confirmed incorrect — direct TREASURY capital transfers:

| Date | Amount |
|---|---|
| May 19 | 1,390 SOL + 10 SOL |
| May 23 | 290 SOL + 10 SOL |
| May 25 | 890 + 740 + 490 SOL + 3× 10 SOL |

Current balance: **1,123 SOL**. Role: **SUB_PROV** — receives large capital from TREASURY and redistributes to operators. The 10 SOL payments are Helio bot subscriptions. Also receives profit sweeps from swarm activity AND dust pings from signaller chains — it is a central hub in the operation.

### Signaller Relay Chain Discovery

A new signalling infrastructure layer discovered — multi-hop disposable relay chains all terminating at `N3TKf3wM…`:

```
8JFcYTVyPkeQ9VxB6mpcYjj63fUFgdLoxhgt9n8czVH8  (SIGNAL_DISTRIBUTOR — 1000+ txs, seeds relay chains)
    └── Hsj11jMXCMBS9ctmxsDyrAQwyqutxHa7vARGjFFzESCU
            └── GnztTFnwuVuse7yq8FygJRiz377Mx6vPYvEk5FADxMRs  (reused relay — funded twice)
                    └── 4bGC5KDSGJs3WZGgrMCaxynuoxbusa15zwPajg4jDWTn
                            └── FpjW3ag5WaT3rSTLon1VyR3bP6bdbmvxh7TKEG6mGWSu
                                    └── pK8U9bGH46XpJyoFfaCz2y3Tkyvzf8yM71mLmmVFCkW
                                            └── fEwXixFAK6oAjZkgnEWatmmtf7ieJ3pM7WcqMFm6Epw
                                                    └── F9WzJSQXNgafRct5HAr1CdvSWJkG6DbAMJC1LjWXKzdB
                                                            └── N3TKf3wM… ×6 pings (0.00001501 SOL each)
```

**6 hops** from distributor to PROFIT_RELAY. Each relay is a single-use wallet funded with ~0.0013 SOL — just enough for one ping chain and fees. `8JFcYTVy…` has seeded 1,000+ such chains.

**Dust amounts confirm WATCHTOWER fingerprint:**
- 0.00001501 SOL = SIGNALLER-1 (`44orA1Bx…`) amount
- 0.000015 SOL = SIGNALLER-2 (`44o1Hecb…`) amount

Pairs of relay wallets always fire in lockstep at these two amounts — the same 2-of-2 confirmation mechanic seen throughout WATCHTOWER signalling.

**PROOF OF WATCHTOWER LINK:** `N3TKf3wM…` receives direct capital from TREASURY (`44orWS68…`). Every wallet pinging it is part of the same operation.

### New Infrastructure Addresses

| Address | Role | Evidence |
|---|---|---|
| `8JFcYTVyPkeQ9VxB6mpcYjj63fUFgdLoxhgt9n8czVH8` | SIGNAL_DISTRIBUTOR | 1000+ txs seeding relay chains → N3TKf3wM |
| `F9WzJSQXNgafRct5HAr1CdvSWJkG6DbAMJC1LjWXKzdB` | Disposable signaller | 6× 0.00001501 pings to N3TKf3wM |

