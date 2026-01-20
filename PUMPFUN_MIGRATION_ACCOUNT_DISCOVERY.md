# Pump.Fun Migration Account Discovery

**Date**: 2026-01-19
**Critical Finding**: The "master operator" controlling 57 tokens is actually Pump.Fun's migration account
**Impact**: Reframes entire authority analysis - not a coordinated rug network, but Pump.Fun's infrastructure

---

## The Discovery

### **39azUYFW...UJjg** = Pump.Fun Migration Account

When tokens migrate from Pump.Fun to PumpSwap:
1. Pump.Fun's migration process becomes the signer
2. The `creator_address` field records `39azUYFW...UJjg` (Pump.Fun's migration account)
3. This appears for **57 out of 103 tokens** (55.4%)
4. These are **legitimate migrations**, not coordinated rugs

### Why This Appeared Suspicious

- Single account controls majority of tokens
- Looked like industrial-scale rug factory
- Actually just the standard Pump.Fun migration flow

---

## Corrected Analysis

### Real Coordinated Networks (Actual Red Flags)

| Operator | Tokens | Type | Status |
|----------|--------|------|--------|
| Dqcz2iTA...tWJq | 6 | 🚨 ACTUAL COORDINATED | Known network member |
| 9bAHNiCf...b9Dv | 5 | 🚨 ACTUAL COORDINATED | Known network member |
| 9ryBR3Sn...XLaq | 4 | ⚠️ POTENTIAL COORDINATED | New discovery |
| 112Mi2fd...qpjf | 4 | ⚠️ POTENTIAL COORDINATED | New discovery |
| dshAybqF...zEXc | 2 | 🚨 ACTUAL COORDINATED | Known network member |

**Total coordinated operators**: 5 (not 7)
**Total tokens in real networks**: 21 (not 89)
**Legitimate Pump.Fun migrations**: 57

---

## What We Learned

### ✅ Correct Understanding

1. **Pump.Fun migration account is visible** - `39azUYFW...UJjg` is Pump.Fun's address
2. **57 tokens are legitimate migrations** - Standard Pump.Fun→PumpSwap flow
3. **5 real coordinated networks remain** - Still need investigation/blocking
4. **System Program tokens (11 tokens)** - 11111111...1111 needs separate analysis

### Authority Sharing Still Valid For

- **Secondary networks**: Dqcz2iTA, 9bAHNiCf, 9ryBR3Sn, 112Mi2fd, dshAybqF
- **Rug rate analysis**: Still applies to non-Pump.Fun tokens
- **Network clustering**: Multiple small operators still coordinated

---

## Corrected Statistics

| Category | Count | % of Total |
|----------|-------|-----------|
| Pump.Fun migrations | 57 | 55.4% |
| System Program (error) | 11 | 10.7% |
| Actual coordinated networks | 21 | 20.4% |
| Independent operators | 14 | 13.6% |

---

## Implication for Detection

### Pump.Fun Tokens Don't Need Special Blocking

- Pump.Fun itself is legitimate platform
- The 57 tokens using this account are normal migrations
- No need to block `39azUYFW...UJjg` itself
- Individual tokens should be blocked based on their own risk assessment

### Real Networks Still Need Action

- Dqcz2iTA, 9bAHNiCf, 9ryBR3Sn, 112Mi2fd, dshAybqF
- These are the actual coordinated operators
- 21 tokens under real coordinated control
- These should remain blocked/flagged

---

## Next Steps

1. **Remove 39azUYFW from blocklist** (if it was added)
2. **Keep secondary networks blocked** - They're real threats
3. **Investigate 9ryBR3Sn and 112Mi2fd** - New coordinated operators (4 tokens each)
4. **Fix System Program tokens** - 11 tokens with incorrect data need review
5. **Re-run rug prediction** - With corrected understanding of legitimate migrations

---

## Files Updated

- `AUTHORITY_SHARING_ANALYSIS.md` - Clarified findings
- Database: No changes needed
- Blocklist: Verify 39azUYFW is NOT blocked (it's legitimate)

**Report Generated**: 2026-01-19 16:39:00 UTC
