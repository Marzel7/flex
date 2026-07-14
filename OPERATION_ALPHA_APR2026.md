# OPERATION ALPHA — April 2026

**Classification:** WATCH_LIKE_NEW_OP  
**Status:** DORMANT (completed single-run operation)  
**WATCHTOWER Link:** NONE CONFIRMED  
**Discovery Date:** 2026-06-02  
**Investigation Method:** 3-hop funding chain trace from known fingerprint amounts

---

## Executive Summary

A coordinated token launch operation ran between **April 21–30, 2026**, deploying **29 WATCH-style tokens** using an infrastructure architecture nearly identical to WATCHTOWER. The operation used the same provisioning fingerprint amounts (`2.10203928` and `5.10203928` SOL), the same multi-tier fanout wallet structure, and achieved full migration on all 29 tokens with a combined peak market cap of **$2.08M**.

Despite architectural similarity to WATCHTOWER, **zero direct links** to known WATCHTOWER infrastructure were found. This is an independent operation — either a separate team or a copycat using the same operational playbook.

The operation is **fully dormant**. All infrastructure went dark on **April 30, 2026** after a final profit sweep.

---

## Operational Flow

```
UNKNOWN CAPITAL SOURCE
         │
         ▼
4LpEjcq3PwkE9Hwt1xLdYHCxyNYB13wEahUPCRkzZa9Q
  [PROFIT COLLECTOR / CAPITAL HUB]
  Operated: Apr 30 09:36–38 UTC
         │
         ├── Tier-2 distribution hubs (multiple wallets)
         │       │
         │       ├── Fanout wallet #1 → Creator #1 (2.10203928 SOL) → Token → Migrated
         │       ├── Fanout wallet #2 → Creator #2 (2.10203928 SOL) → Token → Migrated
         │       ├── Fanout wallet #3 → Creator #3 (5.10203928 SOL) → Token → Migrated
         │       └── ... × 29 total
         │
         ▼  (Apr 30 09:37 — profit sweep)
FKzaUUEzwWgjykiMRBKBDmZDWudBf5URKzKQEHF6QPQR
  [PROFIT DESTINATION — unknown, no further history]
  Received: 567 SOL
```

---

## Infrastructure Detail

### Capital Hub / Profit Collector
**`4LpEjcq3PwkE9Hwt1xLdYHCxyNYB13wEahUPCRkzZa9Q`**

| Attribute | Value |
|-----------|-------|
| Role | Capital distributor + profit collector |
| Active window | Apr 30 09:36–38 UTC (2 minutes) |
| Inbound | 31 profit sweeps from creators (~0.01–1.8 SOL each) |
| Outbound | 567 SOL swept to `FKzaUUEz...` |
| WATCHTOWER link | **NONE** — zero treasury/signaller hits |

**Note:** This wallet aggregates profits from below (sweep-up pattern), unlike a WATCHTOWER SUB_PROV which distributes capital downward. It functions as both the capital distribution point and profit collection point for this operation.

---

### Provisioning Amounts

All 29 creators were funded with exact fingerprint amounts:

| Amount | Creators | Note |
|--------|----------|------|
| **2.10203928 SOL** | 26 | Matches WATCHTOWER April 2026 fingerprint |
| **5.10203928 SOL** | 3 | Larger deployment tier |

These amounts are identical to known WATCHTOWER provisioning amounts. The shared pattern indicates either:
- Same tooling / same operator (different infrastructure)
- Deliberate mimicry of WATCHTOWER operational fingerprint

---

### Fanout Wallets (29 total)

Each creator was funded by a **single-use disposable wallet** — funded once, forwarded funds once, never used again. This is the standard WATCHTOWER-style anti-tracing technique.

Example chain:
```
4LpEjcq3... (hub)
    ↓ 2.1 SOL
88p4FtpMp14EVtmN... (tier-2 intermediate)
    ↓ 2.1 SOL
7BX2gqSY8UbntsMG... (fanout wallet — single use)
    ↓ 5.10203928 SOL
CWkt8xqtuupsCGMG... (creator)
    → DTEUqduWxYvkTq... (token) → MIGRATED
```

---

### Token Operations

| Metric | Value |
|--------|-------|
| Total tokens launched | 29 |
| All migrated to DEX | ✅ Yes (100%) |
| Launch window | Apr 21–28, 2026 |
| Combined peak market cap | **$2,076,571** |
| Tokens exceeding $100k mcap | 8 |
| Tokens exceeding $1M mcap | 0 |
| Average peak mcap | $71,606 |
| Max single token mcap | $233,911 |
| Min single token mcap | $37,986 |

---

### Profit Collection

**April 30 09:36–38 UTC** — 2-minute profit sweep sequence:

1. 29 creator wallets sweep residual SOL → `4LpEjcq3`
2. `4LpEjcq3` aggregates 567 SOL
3. Single outbound transfer → `FKzaUUEzwWgjykiMRBKBDmZDWudBf5URKzKQEHF6QPQR`
4. `FKzaUUEz` has no further retrievable history

Total extracted: **~567 SOL** (~$85k at Apr 2026 SOL price)

---

## WATCHTOWER Link Assessment

### Checks Performed

| Check | Result |
|-------|--------|
| TREASURY (`44orWS68`) sent SOL to `4LpEjcq3` | ❌ 0 hits |
| SIGNALLER pinged `4LpEjcq3` | ❌ 0 hits |
| Op creators appeared as TREASURY counterparty | ❌ 0 hits |
| Op tokens in `wt_swarm_corridors` | ❌ 0 hits |
| Op fanout wallets shared with WATCHTOWER creators | ❌ 0 shared |
| Any known WATCHTOWER address in 3-hop chain | ❌ None found |

### Verdict: NO WATCHTOWER LINK CONFIRMED

The operation shares **architectural patterns** with WATCHTOWER:
- ✅ Same fingerprint provisioning amounts
- ✅ Same single-use fanout wallet structure
- ✅ Same 3-tier distribution hierarchy
- ✅ Same WATCH-style token profile (fresh creators, no shared funding)
- ✅ Same profit sweep pattern (creators → aggregator → destination)

But it has **no operational overlap** with WATCHTOWER:
- ❌ No TREASURY capital
- ❌ No SIGNALLER activation
- ❌ No shared fanout wallets with WT creators
- ❌ No shared infrastructure addresses
- ❌ Capital source unknown (not traceable to WT treasury)
- ❌ Profit destination unknown (not a known WT profit relay)

---

## Dormancy Confirmation

| Infrastructure | Last Activity | Status |
|----------------|--------------|--------|
| `4LpEjcq3` (hub) | Apr 30 09:38 UTC | **DARK** |
| All 29 fanout wallets | Apr 30 09:36 UTC | **DARK** |
| All 29 creators | Apr 21–28 (launches) | **DARK** |
| `FKzaUUEz` (profit dest) | No retrievable history | **DARK** |
| Dormant scanner observation | May 18, 2026 | Confirmed dormant |

**Operation fully completed and dark as of April 30, 2026.**

---

## Classification

| Field | Value |
|-------|-------|
| **Name** | OPERATION ALPHA (Apr 2026) |
| **Classification** | WATCH_LIKE_NEW_OP |
| **Confidence** | HIGH (90%) |
| **WATCHTOWER Link** | NONE |
| **Status** | DORMANT — completed |
| **Technique** | Clone/copycat of WATCHTOWER operational model |
| **Capital Source** | Unknown — not WATCHTOWER treasury |
| **Profit Extraction** | ~567 SOL via `4LpEjcq3` → `FKzaUUEz` |
| **Token Outcome** | 29/29 migrated, $2.08M combined peak mcap |

---

## Significance

This operation demonstrates that the WATCHTOWER provisioning model — fingerprint amounts, single-use fanout wallets, multi-tier distribution, WATCH-style fresh creators — is being used by **at least one independent actor** outside of the confirmed WATCHTOWER infrastructure.

This has two implications:

1. **The fingerprint amounts alone cannot classify a token as WATCHTOWER** — they appear in independent operations. This validates the current classifier design (fingerprint = soft signal only).

2. **WATCH_LIKE_NEW_OP is the correct classification** for this cluster — it exhibits coordinated WATCH-style behaviour without confirmed WATCHTOWER lineage.

The dormancy confirms this was a **single-run operation**, not an ongoing threat. Worth monitoring for re-emergence under new wallet infrastructure.

---

*Generated: 2026-06-02 | Source: 3-hop funding chain analysis + Helius RPC verification*
