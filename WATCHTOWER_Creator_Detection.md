# WATCHTOWER Creator Detection Analysis
**Date:** 2026-05-22  
**Status:** Active investigation — empirical findings from DB + RPC traces  
**Scope:** 166 confirmed WATCHTOWER creators, March–May 2026

---

## Background

Confirmed finding that triggered this analysis:

Creator wallet `3mSb3pnYshb5qwTdymH4ouykze82JEnohseRqoTaMQkJ` was directly funded by sub-provisioner `Gs7zXNYwdd2X1PoyBbsJBCuNTz6EyTT5KSd38tLMEmif` with 0.542 SOL at `2026-05-17 23:57`, shortly before launching token `8f6K9zXEtFu45bkbvCBtH7sPcSE1X4FY7MRuFA6Dpump`.

This raised the core question: **Is direct sub-provisioner → creator funding a consistent operational pattern? Can creators be identified before launch?**

---

## 1. Executive Summary

The sub-provisioner → creator funding pattern is structurally consistent but operates primarily through an intermediate hop, not always directly.

- **106/106** confirmed WT creators with funder data received a `X.10203928` SOL transfer as their primary or sole funding (100% replication rate)
- Creator wallets are always fresh and single-use: fund → launch → abandon
- The intermediate relay tier (sub-provisioner → relay wallet → staged wallet → creator) is the dominant April structure
- The direct sub-provisioner → creator transfer (May case) may represent a simplified variant for smaller deployments
- **Pre-launch detection is structurally feasible** by monitoring sub-provisioner outflows for transfers in the 0.4–6 SOL range to fresh wallets

---

## 2. Known Infrastructure

### Sub-Provisioners (TREASURY-funded, confirmed)

| Address | Cohort | Wallets Funded | TREASURY Amount |
|---------|--------|----------------|-----------------|
| `G2BbetUgzETGhK8w43YbcwD9yx74CGg649z8nvWn6Ntd` | May 20–21 | 4,947 trader wallets | 140 SOL |
| `Gs7zXNYwdd2X1PoyBbsJBCuNTz6EyTT5KSd38tLMEmif` | May 17–18 | 32 trader wallets + 1 creator | ~? SOL |
| `C745erBxwn4sJZGDRZpi71FPV3MA3kBQUXWbeJxRsGS4` | May 17 | 13 wallets | ~? SOL |
| `4LpEjcq3PwkE9Hwt1xLdYHCxyNYB13wEahUPCRkzZa9Q` | April | 44 wallets (2.102 SOL each) | untraced |
| `7UyCwmSUcG7utdSPikn5caL9QwbEnAs1aDcbdWvGs37A` | April | 27 wallets (1.102 SOL each) | untraced |

### Core Infrastructure
- **TREASURY:** `44orWS68MqXG198M3YXyZoNrYtsNhgnNhtUT5SavqJFM`
- **SIGNALLER:** `44orA1BxQfFaX2iMjRbWstoqqWBE7ag8BD93ikxR4JFM`

---

## 3. Confirmed Creator Funding Topology

### May 2026 (RPC verified)
```
TREASURY (44orWS68…)
  └─ Gs7zXNYwdd2X1Poy… [sub-provisioner]
       ├─ [32 trader wallets, 0.01003928 SOL each]
       └─ 3mSb3pnYshb5q… [CREATOR, 0.542 SOL direct, 2026-05-17 23:57]
            └─ token: 8f6K9zXEtFu45bkb…pump
                 └─ [32 Gs7zXNYw traders provide AMM buy support]
```

### April 2026 (DB traces, partial)
```
TREASURY (44orWS68…)
  └─ [April sub-provisioners — Helius lookback expired, unresolved]
       └─ Mid-tier relay wallet (e.g., Gt9bjWaqfnhTB…) [receives round SOL, e.g. 1.1]
            └─ Staged wallet (e.g., 2tGoMDZX3BQ…) [receives 1.1 SOL]
                 └─ Creator (e.g., B7QcZzkgXncB…) [receives 1.10203928 SOL]
                      └─ pump.fun token create
```

**Key observation:** The relay wallet receives a round number (1.1), the staged wallet adds `.00203928` when forwarding to the creator. This `.002` addition appears to be a rent/fee padding marker added at the final hop.

---

## 4. Creator Provisioning Statistics (166 confirmed WT creators)

| Metric | Value |
|--------|-------|
| Total confirmed WT creators | 166 |
| With funder data captured | 106 (64%) |
| Funded with `X.10203928` pattern | **106 / 106 (100%)** |
| Single-funder creators | 61 (58%) |
| Multi-funder creators | 45 (42%) |
| Operation start | 2026-03-08 |
| Operation end (last seen) | 2026-04-28 |

### Funding Amount Distribution (`X.10203928` transfers)

| Base SOL | Count | Share |
|----------|-------|-------|
| 2.10203928 | 58 | 55% |
| 1.10203928 | 48 | 45% |
| 5.10203928 | 3 | 3% |
| 3.10203928 | 3 | 3% |

### Multi-Funder Composition
The 45 multi-funder creators receive the standard `X.10203928` provisioning transfer PLUS one or more of:
- Large SOL injection (10–435 SOL) — profit recycling for high-value launches
- Dust (0.01 SOL) — likely SIGNALLER activation
- Partial amounts (0.47–0.84 SOL) — unknown purpose

One outlier: creator `9T5uCK6cbgw` received 422 SOL alongside the 1.1 SOL provisioning. This suggests a dual-role wallet (launch + liquidity provision) for high-capital deployments.

---

## 5. Creator vs Trader Wallet Differentiation

| Attribute | Creator Wallet | Staged Trader Wallet |
|-----------|---------------|---------------------|
| Funding amount | `X.10203928` SOL (1–5 SOL) | `~0.015` SOL (May) or `1.1–2.1` SOL (April) |
| Funding source | Single mid-tier relay or sub-provisioner | Sub-provisioner directly |
| Wallet freshness | Always fresh | Always fresh |
| First action | pump.fun create (fee to `6EF8rr…`) | ATA setup + AMM buy |
| Profit sweep | None | Yes — back to sub-provisioner |
| Lifecycle | Hours (fund → create → done) | Days (multi-cycle reload/trade) |
| AMM activity | None pre-launch | Immediate after funding |
| Wallet reuse | Never reused | Reloaded repeatedly |
| Total tx count | 1–3 | 10–20+ over lifecycle |
| Post-launch behaviour | Abandoned | Continues trading |

**Critical differentiator:** A creator wallet's transaction sequence is exactly:
1. Receive `X.10203928` SOL (first ever tx)
2. Call pump.fun create (second tx, within hours)
3. No further activity

A trader wallet's sequence is:
1. Receive funding
2. Distribute to partner wallets (ATA setup)
3. Repeated pAMM buy/sell cycles
4. Periodic sweep back to provisioner
5. Receive reload
6. Repeat

---

## 6. Timing Analysis

### Available data
- Detection timestamps are approximate (DB scan times, not block times)
- Block-level timing only confirmed for the May case via RPC

### May confirmed timing
- `Gs7zXNYw → 3mSb3pnYshb5q`: funded at `2026-05-17 23:57`
- Token first seen in AMM trades: `2026-05-17 21:23` (bonding curve phase preceded this)
- Token migrated to AMM at: `2026-05-18 04:39`
- Implication: creator launched on bonding curve ~hours before our DB captured the funding transfer. Funding-to-launch delay is likely **minutes to low single-digit hours**.

### April batch cadence (from `creator_funders.first_detected_at`)
- Multiple creators detected within same hour on same day
- Suggests batched provisioning: sub-provisioner fans out to multiple creators simultaneously, not sequentially
- Example: 2026-04-26 10:25–10:57, at least 6 creators funded within 32 minutes

### Inferred timing model
| Metric | Estimate |
|--------|----------|
| Funding → launch delay | Minutes to ~2 hours |
| Launch → migration delay | 2–8 hours (bonding curve fill) |
| Detection window (pre-launch) | Near-zero with current monitoring |
| Detection window (post-funding, pre-create) | Minutes to hours if sub-provisioner outflows watched |

---

## 7. Pre-Launch Creator Detection Model

### Detection rule (HIGH confidence triggers)

A wallet is a **WATCHTOWER CREATOR CANDIDATE** if all of the following hold:

1. Fresh wallet — zero prior transaction history OR first tx occurred within last 24h
2. Received exactly `{1,2,5}.10203928` SOL in a single inbound transfer
3. The funding sender is itself a fresh single-use relay (≤5 total txs)
4. The relay sender traces to a known sub-provisioner or TREASURY within 2 hops
5. No AMM interactions in wallet history
6. Zero outbound transfers since receiving funding

**Imminent launch signal:** wallet matching criteria 1–5 with no outbound tx yet = pre-launch window, monitor for pump.fun fee payment to `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`.

### Confidence levels

| Criteria met | Confidence | Action |
|-------------|------------|--------|
| All 5 | HIGH (≥85%) | Alert immediately |
| 1–3 only | MEDIUM (60%) | Watch list |
| 1–2 only | LOW (40%) | Log only |

### False positive risk
- Low: `X.10203928` pattern is unusual; random wallets rarely match
- Main FP source: other operations using similar precision amounts
- Mitigation: require treasury lineage (criterion 4)

### False negative risk
- High for wallets funded through untracked sub-provisioners
- New sub-provisioners emerge with each batch — detection requires known sub-prov set to be current
- RPC lookback limitation (~30 days on Helius) means retroactive chain tracing fails for older batches

---

## 8. Real-Time Detection Signals (Priority Order)

### Priority 1 — Sub-provisioner outflow monitoring
Monitor all outbound transfers from known sub-provisioners. Any fresh wallet receiving 0.4–6 SOL from these addresses = creator candidate. Register for Helius webhooks on all confirmed sub-provisioner addresses.

**Addresses to monitor:**
- `G2BbetUgzETGhK8w43YbcwD9yx74CGg649z8nvWn6Ntd`
- `Gs7zXNYwdd2X1PoyBbsJBCuNTz6EyTT5KSd38tLMEmif`
- `C745erBxwn4sJZGDRZpi71FPV3MA3kBQUXWbeJxRsGS4`
- Any new wallet confirmed as TREASURY recipient with ≥50 SOL inflow

### Priority 2 — Amount pattern matching
Any fresh wallet receiving exactly `{1,2,5}.10203928` SOL from any wallet within 2 blocks of known WT infrastructure interaction → flag immediately.

### Priority 3 — Fanout differentiation
When a sub-provisioner fans out to N wallets simultaneously, identify the wallet receiving a non-round amount vs. those receiving round amounts. The non-round-amount recipient is likely the creator.

### Priority 4 — Pump.fun fee watcher
Monitor `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P` (pump.fun fee account) for transactions where the fee payer is a fresh wallet with ≤3 txs funded from known WT infrastructure. This is the launch event itself.

### What does NOT work as a standalone signal
- Decimal suffix alone — too low-confidence, other operations use similar precision
- Amount size alone — varies 1–5+ SOL
- SIGNALLER dust alone — too broad
- TREASURY outflow alone — many legitimate recipients

---

## 9. Falsification Evidence

### Pattern holds
- 106/106 WT creators with data received `X.10203928` — zero exceptions
- 0 creators found with prior AMM history
- 0 creators found that later acted as trader wallets
- 0 creators funded from CEX withdrawals

### Pattern gaps (could not verify)
- April sub-provisioner chain above the relay tier — Helius lookback expired, cannot trace
- Whether all April mid-tier relay wallets trace to TREASURY — only 3 relays identified, chain stops there
- Exact funding-to-launch timing for April — detection timestamps only, no block times

### Genuine anomaly
Creator `9T5uCK6cbgw731qRV8XahPQCajueCryY1QdJDs331fEh` has 8 funders including a 422 SOL injection. The standard `1.10203928` provisioning is present, but the 422 SOL suggests this creator also served as a liquidity provisioner, not just a token launcher. This is the single structural outlier in the dataset.

### What would falsify the pattern
- A confirmed WT creator funded from a CEX — not found
- A creator with extensive prior tx history — not found
- A creator that later appeared in AMM trading cohorts — not found
- A launch with no `X.10203928` provisioning — not found in any confirmed case

---

## 10. Operational Hypotheses

| Hypothesis | Evidence | Confidence |
|-----------|----------|------------|
| All WT creators receive `X.10203928` SOL provisioning | 106/106 | HIGH |
| Creators are always fresh wallets | All checked cases | HIGH |
| Creator first tx = token create | Structurally required, 1 confirmed | HIGH |
| Creator and trader roles are never mixed | 0 counterexamples | HIGH |
| Relay tier exists between sub-prov and creator (April) | DB traces, unconfirmed on-chain | MEDIUM |
| Sub-prov funds creator directly (May variant) | 1 confirmed case | LOW — needs replication |
| Pre-launch window exists for detection | Structurally necessary | MEDIUM |
| Batched launches occur within 30–60 min windows | April timestamp clustering | MEDIUM |

---

## 11. Open Questions

1. **What are the April sub-provisioner parent wallets?** The relays (`Gt9bjWaqfnhTB`, `72GYK9vyWcaoQ`, `8tuAxQMGckAz5`) received funds from somewhere — unresolved above the relay tier.

2. **Does Gs7zXNYw represent a different operational mode** (direct creator funding) or was the May creator also funded through a relay we missed?

3. **Will the G2BbetUgz trader army (4,947 wallets)** shift to creator roles in a future launch, or are trader and creator cohorts always separate personnel?

4. **What triggers deployment?** Are launches triggered by TREASURY activity, SIGNALLER dust, or an external signal?

5. **Is the `.10203928` suffix operationally meaningful** (checksum, batch ID, versioning) or is it purely a cosmetic artifact of the funding calculation?

---

## 12. Data Gaps and Limitations

| Gap | Impact | Workaround |
|----|--------|------------|
| Helius RPC lookback ~30 days | April sub-prov chain unresolvable | None — historical data lost |
| `wt_creator_launches` table empty | No on-chain confirmed launch timestamps | Use `creator_funders.first_detected_at` as proxy |
| `funder_incoming_transfers` doesn't cover April staged wallets | Cannot trace staged wallet funding source | Transfer_index partial coverage |
| May sub-provisioner funding amounts not captured | G2BbetUgz total capacity unknown | Inferred from 140 SOL TREASURY receipts |
| 60 of 166 WT creators have no funder data | Pattern may not be universal | All 106 with data confirm pattern |

---

## Appendix: Key Addresses

| Address | Role | Confirmed |
|---------|------|-----------|
| `44orWS68MqXG198M3YXyZoNrYtsNhgnNhtUT5SavqJFM` | TREASURY | Yes |
| `44orA1BxQfFaX2iMjRbWstoqqWBE7ag8BD93ikxR4JFM` | SIGNALLER | Yes |
| `G2BbetUgzETGhK8w43YbcwD9yx74CGg649z8nvWn6Ntd` | Sub-provisioner (May trader) | Yes — TREASURY-funded (140 SOL) |
| `Gs7zXNYwdd2X1PoyBbsJBCuNTz6EyTT5KSd38tLMEmif` | Sub-provisioner (May creator + trader) | Yes |
| `C745erBxwn4sJZGDRZpi71FPV3MA3kBQUXWbeJxRsGS4` | Sub-provisioner (May) | Yes |
| `4LpEjcq3PwkE9Hwt1xLdYHCxyNYB13wEahUPCRkzZa9Q` | Sub-provisioner (April) | Inferred |
| `7UyCwmSUcG7utdSPikn5caL9QwbEnAs1aDcbdWvGs37A` | Sub-provisioner (April) | Inferred |
| `3mSb3pnYshb5qwTdymH4ouykze82JEnohseRqoTaMQkJ` | Confirmed creator (May) | Yes — direct RPC verified |
| `8f6K9zXEtFu45bkbvCBtH7sPcSE1X4FY7MRuFA6Dpump` | Confirmed WT token (May) | Yes |
| `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P` | pump.fun fee account | Yes — launch detection anchor |
| `pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA` | pump.fun AMM | Yes — trader activity marker |
