# ALPHA_SIMILARITY_ANALYSIS

**Date:** 2026-06-02
**Objective:** Compute Operation Alpha's operator fingerprint, score every WATCH_LIKE_NEW_OP cluster against it, and determine whether Alpha was a one-off or part of a campaign family.
**Method:** 3-hop funding chain trace + Helius RPC verification + weighted fingerprint scoring.

---

## 1. Operation Alpha — Reference Fingerprint

| Dimension | Value |
|-----------|-------|
| **Creator funding pattern** | Each creator funded once by a single-use fanout wallet (~1.14 funders/creator) |
| **Fanout depth** | 3 hops: `HUB → tier-2 → fanout → creator` |
| **Fanout count** | 29 single-use fanout wallets (1 per creator) |
| **Provisioning amounts** | `2.10203928` SOL (89.7%) and `5.10203928` SOL (10.3%) — 100% fingerprint |
| **Sweep timing** | Single 2-minute profit sweep, Apr 30 09:36–38 UTC |
| **Profit aggregation** | Single-hub sweep: 29 creators → `4LpEjcq3` → 567 SOL → `FKzaUUEz` |
| **Migration profile** | 100% migration, avg peak mcap ~$80k, max $234k, 6.9-day launch window |
| **Hub address** | `4LpEjcq3PwkE9Hwt1xLdYHCxyNYB13wEahUPCRkzZa9Q` |

---

## 2. ALPHA_SIMILARITY_SCORE — Scoring Model

Each WATCH_LIKE_NEW_OP cluster scored 0–100 across 7 weighted dimensions:

| Dimension | Weight | Alpha Reference |
|-----------|--------|-----------------|
| Fingerprint amount presence | 25 | 100% `.10203928` |
| Funding amount range | 20 | 2.1–5.1 SOL |
| Single-use funders | 20 | ~1.14 funders/creator |
| Migration rate | 15 | 100% |
| Market-cap profile | 10 | $30k–$300k band |
| Launch window | 5 | ~6.9 days |
| Scale | 5 | 29 tokens |

**Bands:** ≥55 HIGH · 35–54 MODERATE · <35 LOW

---

## 3. ALPHA_SIMILARITY_SCORE — Results

| Cluster | Tokens | **Score** | FP% | Avg Fund | Funders/Creator | Mig% | Verdict |
|---------|--------|-----------|-----|----------|-----------------|------|---------|
| **#79** | 2 | **57.8** | 100% | 8.54 | 4.0 | 100% | 🔴 **HIGH — Alpha-style** |
| #84 | 4 | 47.0 | 0% | 0.92 | 4.0 | 100% | 🟡 MODERATE |
| #87 | 2 | 43.2 | 0% | 1.68 | 3.0 | 100% | 🟡 MODERATE |
| #78 | 2 | 40.5 | 0% | 2.76 | 4.6 | 100% | 🟡 MODERATE |
| #83 | 2 | 37.6 | 0% | 3.94 | 1.0 | 0% | 🟡 MODERATE |
| #75 | 142 | 35.9 | 2.1% | 4.72 | 40.7 | 100% | 🟡 MODERATE (retail scatter) |
| #86 | 2 | 35.5 | 0% | 3.97 | 2.0 | 0% | 🟡 MODERATE |
| #74 | 2 | 34.6 | 0% | 10.95 | 3.0 | 100% | ⚪ LOW |
| #76 | 3 | 31.3 | 0% | 5.88 | 6.6 | 100% | ⚪ LOW |
| #82 | 6 | 27.1 | 0% | 2.50 | 3.0 | 0% | ⚪ LOW |
| #85 | 2 | 26.2 | 0% | 0.20 | 1.0 | 0% | ⚪ LOW |
| #80 | 2 | 23.8 | 0% | 0.98 | 3.0 | 0% | ⚪ LOW |
| #77 | 2 | 23.3 | 0% | 1.30 | 3.6 | 0% | ⚪ LOW |
| #81 | 2 | 22.6 | 0% | 1.41 | 4.0 | 0% | ⚪ LOW |

**Note:** Cluster 75 (142 tokens) scores MODERATE only because of migration rate — its funding is large-amount, CEX-sourced, 40+ funders/creator (retail scatter). It is structurally the *opposite* of Alpha despite the score.

---

## 4. The Critical Finding: Cluster #79 + The Hub Family

Cluster #79 scored HIGH because both its tokens trace through `BcSScwFvvUCCJT3s3DjZj1FjfwKL178egkzDxGLSwLUg` via the **identical Alpha structure** — `2.10203928` fingerprint amount through single-use fanout wallets — but **NOT** through Alpha's `4LpEjcq3` hub.

This prompted a sweep for **all hubs** that fund creators via `x.10203928` single-use fanout. Result:

| Hub | Tokens | Classification | FP Amounts | First Activity |
|-----|--------|----------------|------------|----------------|
| `7UyCwmSUcG7…` | 20 | **WATCHTOWER** (confirmed) | 1.10203928 | May 9 |
| `4LpEjcq3…` | 31 | **Operation Alpha** (no WT) | 2.10 / 5.10 | Apr 30 |
| `BcSScwFvv…` | 2 | Alpha-style (no WT) | 2.10203928 | Apr 15 |
| `4r65bgGW…` | 3 | mixed | 1.10 / 2.10 | — |
| `6FdUQoBL…` | 3 | Alpha-style | 1.10203928 | — |

**There are at least 5 distinct hubs using the same fingerprint-fanout provisioning model.** One is confirmed WATCHTOWER. The rest have no treasury/signaller link.

---

## 5. Hub Comparison: Alpha vs BcSScwFvv

| Property | HUB-A (Alpha / 4LpEjcq3) | HUB-B (BcSScwFvv) |
|----------|--------------------------|-------------------|
| First activity | Apr 30 2026 | **Apr 15 2026** (earlier) |
| Structure | 3-hop fanout | 4-hop fanout |
| Provisioning | 2.10 / 5.10 SOL | 2.10 SOL |
| Inbound pattern | Many small profit sweeps | Many small profit sweeps |
| Role | Profit collector | Profit collector |
| Shared upstream funders | — | **0 shared with HUB-A** |
| WATCHTOWER link | None | None |

Both hubs are **independent profit-collector endpoints** (no shared funders), but structurally identical. BcSScwFvv predates Alpha by 15 days.

---

## 6. Answer: One-Off or Campaign Family?

**Operation Alpha is NOT a one-off. It is one campaign in a family of fingerprint-fanout operations.**

Evidence:
- **5 distinct hubs** use the same `x.10203928` single-use fanout provisioning model
- **4 of the 5 have no WATCHTOWER link** (only `7UyCwmSUcG7` is confirmed WT)
- The non-WT hubs are **independent** (no shared upstream funders) but **structurally identical**
- They are **time-staggered**: BcSScwFvv (Apr 15) → Alpha/4LpEjcq3 (Apr 30) → suggesting serial campaigns with fresh infrastructure each run

This is consistent with **one operator (or a small set) running serial campaigns**, deliberately rotating hub infrastructure between runs to break correlation — the same anti-tracing discipline seen at the fanout-wallet level, applied at the campaign level.

---

## 7. Distinction From WATCHTOWER

The fingerprint amounts (`x.10203928`) are shared across **both** WATCHTOWER and this non-WT campaign family. This confirms the earlier conclusion:

> **Fingerprint amounts alone cannot classify a token as WATCHTOWER.**

The discriminator between WATCHTOWER and the Alpha family is **funding origin**, not fingerprint:
- **WATCHTOWER** (`7UyCwmSUcG7`): TREASURY capital + SIGNALLER activation → confirmed lineage
- **Alpha family** (`4LpEjcq3`, `BcSScwFvv`, etc.): unknown capital source, no treasury, no signaller, profit-collector hubs

---

## 8. Hub Resolution — RPC Confirmed (2026-06-02)

All 5 fingerprint-fanout hubs RPC-traced for WATCHTOWER infrastructure contact (full `_WT_INFRA_ROLES` set including primary `5Ww9G6X`):

| Hub | Tokens | WT Link | Evidence | Final Classification |
|-----|--------|---------|----------|---------------------|
| `7UyCwmSUcG7` | 20 | 🔴 YES | TREASURY 90+10 SOL inbound + SIGNALLER pings | **WATCHTOWER** |
| `4r65bgGW` | 3 | 🔴 YES | Swept **79.3 SOL → WATCHTOWER_PRIMARY** Apr 18 | **WATCHTOWER** |
| `4LpEjcq3` (Alpha) | 31 | ⚪ No | Zero WT contact | **ALPHA-FAMILY** |
| `BcSScwFvv` | 2 | ⚪ No | Zero WT contact | **ALPHA-FAMILY** |
| `6FdUQoBL` | 3 | ⚪ No | Zero WT contact | **ALPHA-FAMILY** |

**Correction from initial assessment:** `4r65bgGW` was initially flagged unconfirmed; full-address-set RPC trace shows it sweeps profits directly to the primary WATCHTOWER address. It is a WATCHTOWER sub-operation using the fanout model, **not** Alpha-family.

### Refined Campaign Picture

**Two distinct operators share the `x.10203928` fingerprint-fanout playbook:**

- **WATCHTOWER:** hubs `7UyCwmSUcG7`, `4r65bgGW` — capital from / profits to confirmed WT treasury infrastructure
- **ALPHA FAMILY:** hubs `4LpEjcq3`, `BcSScwFvv`, `6FdUQoBL` — no treasury, no signaller, independent profit-collector hubs

**Alpha-family timeline (time-staggered, fresh hub each run):**
```
BcSScwFvv  ── Apr 15  (earliest, 4-hop)
6FdUQoBL   ── May 1   (50 SOL single-source inbound)
4LpEjcq3   ── Apr 30  (Operation Alpha — largest, 31 tokens)
```

The 3 Alpha-family hubs share **no upstream funders** with each other — deliberate correlation-breaking, consistent with one operator rotating infrastructure between serial campaigns.

### Actions Taken
- ✅ Operation Alpha registered as **cluster #89** (DORMANT, 29 members, label `OPERATION_ALPHA`)
- ✅ `4r65bgGW` added to `_WT_INFRA_ROLES` as `PROFIT_RELAY` → its downstream tokens reclassified WATCHTOWER
- ✅ WATCHTOWER count: 43 → 44

### Remaining Actions
1. **Register the 3 Alpha-family hubs as their own clusters** for tracking (currently only `4LpEjcq3`/Alpha is registered as #89).
2. **Add fingerprint-fanout hub detection** as an automated discovery signal: any hub funding ≥2 creators via single-use `x.10203928` fanout → trace profit-sweep destination; if it lands on WT infra → WATCHTOWER, else → Alpha-family candidate.
3. **Monitor all 5 hubs** for re-activation.

---

## Appendix: Confirmed Hub Addresses

```
WATCHTOWER:
  7UyCwmSUcG7utdSPikn5caL9QwbEnAs1aDcbdWvGs37A   (treasury + signaller confirmed)

ALPHA FAMILY (no WATCHTOWER link):
  4LpEjcq3PwkE9Hwt1xLdYHCxyNYB13wEahUPCRkzZa9Q   (Operation Alpha — 31 tokens, Apr 30)
  BcSScwFvvUCCJT3s3DjZj1FjfwKL178egkzDxGLSwLUg   (cluster #79 — Apr 15, earlier campaign)
  4r65bgGW8bpKfffmfmFiYnfC2y6R1QDWcyFK74AfAvvm   (3 tokens — needs RPC confirmation)
  6FdUQoBL3fsvquoBgqQKPuVZ844oygXEYfiTUzESrB5u   (3 tokens — needs RPC confirmation)

PROFIT DESTINATION (Alpha):
  FKzaUUEzwWgjykiMRBKBDmZDWudBf5URKzKQEHF6QPQR   (567 SOL, no further history)
```
