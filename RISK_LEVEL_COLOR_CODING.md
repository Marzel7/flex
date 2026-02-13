# Risk Level-Based Color Coding for Funding Sources

**Date**: 2026-02-13
**Status**: ✅ COMPLETE & DEPLOYED

## Overview

Updated the funding patterns view to use **actual risk levels** for coloring, instead of treating all identified accounts as suspicious.

## Problem Solved

**Before**: All identified accounts appeared in RED with ⚠️ warning
- Axiom (trusted automation) → RED ⚠️ (wrong!)
- CEX hot wallets → RED ⚠️ (correct!)
- Infrastructure accounts → RED ⚠️ (wrong!)

**After**: Color code based on actual risk assessment
- 🟢 **GREEN ✓** = Trusted/neutral risk accounts
- 🔴 **RED ⚠️** = High-risk accounts
- 🟡 **YELLOW** = Medium risk or unknown

## Color Scheme

### 🟢 GREEN ✓ (Trusted - risk_level: neutral or low)
**Examples**:
- Axiom - Automation & monitoring infrastructure
- Raydium - DEX router
- Magic Eden - NFT marketplace
- Other legitimate service infrastructure

**What it means**: Safe account, not indicative of rug pull

### 🔴 RED ⚠️ (Risky - risk_level: high)
**Examples**:
- CEX hot wallets (Binance, Kraken, etc.)
- Known malicious accounts
- Blocklisted accounts

**What it means**: Potential coordination or exchange manipulation

### 🟡 YELLOW (Medium Risk or Unknown)
**When**:
- risk_level = 'medium'
- risk_level = 'unknown' (no data yet)

**What it means**: Unidentified account or moderate risk

## Summary Line Changes

**Before**:
```
← 5 senders → 3.00 SOL (1 identified ⚠️)
```
(All identified accounts grouped together as risky)

**After**:
```
← 5 senders → 3.00 SOL (1 risky ⚠️) (1 trusted ✓)
```
(Shows breakdown of risky vs trusted identified accounts)

## Individual Sender Display

**Before**:
```
• AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk [Axiom] ⚠️ → 0.10 SOL
```
(RED text, warning emoji - looks suspicious)

**After**:
```
• AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk [Axiom] ✓ → 0.10 SOL
```
(GREEN text, checkmark emoji - clearly trusted)

## Implementation Details

### API Changes

`/api/funding-network-3tier/<creator>` now returns `risk_level` for each sender:

```json
{
  "network_tiers": [
    {
      "senders": [
        {
          "sender_address": "AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk",
          "amount_to_funder": 0.10,
          "label": "Axiom",
          "risk_level": "neutral",  // ← NEW
          "is_known": true
        },
        {
          "sender_address": "5ki8DHxFTkiuPxuS7rrP...",
          "amount_to_funder": 25.50,
          "label": "Binance",
          "risk_level": "high",  // ← HIGH RISK
          "is_known": true
        }
      ]
    }
  ]
}
```

### Backend Logic

In `api_funding_network_3tier()`:
1. Gets `risk_level` from account mappings:
   - CEX accounts → risk_level = 'high'
   - Infrastructure accounts → risk_level from mapping (usually 'neutral')
   - Unknown accounts → risk_level = 'unknown'
2. Returns risk_level with each sender

### Frontend Logic

In `showFundingNetwork3Tier()` JavaScript:
```javascript
if (riskLevel === 'high') {
    senderColor = '#ef4444';  // RED
    badge = ' ⚠️';
} else if (riskLevel === 'neutral' || riskLevel === 'low') {
    senderColor = '#4ade80';  // GREEN
    badge = ' ✓';
} else if (riskLevel === 'medium') {
    senderColor = '#fbbf24';  // YELLOW
    badge = '';
}
```

## Interpretation Guide

### 🟢 GREEN Senders (Trusted)
- Safe to consider as normal funding
- Likely legitimate service infrastructure
- Not indicative of rug pull coordination

### 🔴 RED Senders (Risky)
- CEX involvement suggests exchange manipulation
- Multiple red senders = higher coordination risk
- Should be investigated for rug patterns

### 🟡 YELLOW Senders (Unknown or Medium)
- Need more investigation
- Could be legitimate or risky
- Label may help identify

## Risk Assessment with New Coloring

### ✅ Low Risk Example
```
← 3 senders → 5.00 SOL (1 trusted ✓)
• Axiom [Axiom] ✓ → 0.50 SOL (GREEN)
• UnknownWallet1 → 2.50 SOL (YELLOW)
• UnknownWallet2 → 2.00 SOL (YELLOW)
```
**Assessment**: Mostly unknown senders with trusted infrastructure. Low coordination signal.

### ⚠️ Medium Risk Example
```
← 4 senders → 10.00 SOL (1 risky ⚠️) (1 trusted ✓)
• Raydium [Raydium] ✓ → 0.50 SOL (GREEN)
• Binance [Binance] ⚠️ → 5.00 SOL (RED)
• UnknownWallet1 → 3.00 SOL (YELLOW)
• UnknownWallet2 → 1.50 SOL (YELLOW)
```
**Assessment**: CEX involvement (Binance) suspicious. Investigate if CEX is funding multiple creators.

### 🚨 High Risk Example
```
← 5 senders → 20.00 SOL (2 risky ⚠️)
• Binance [Binance] ⚠️ → 10.00 SOL (RED)
• Kraken [Kraken] ⚠️ → 8.00 SOL (RED)
• UnknownWallet1 → 2.00 SOL (YELLOW)
```
**Assessment**: Multiple CEX wallets funding same creator. High coordination risk. Likely rug.

## Benefits

1. **Clearer Intent**: Users can immediately tell trusted vs risky sources
2. **Reduced False Positives**: Legitimate infrastructure no longer triggers warnings
3. **Better Decision Making**: Risk assessment is more nuanced
4. **Accurate Labeling**: Axiom, Raydium, etc. show as safe (they are)
5. **Actionable Insights**: Focus on RED senders for coordination detection

## Files Modified

- `main.py`
  - Updated `api_funding_network_3tier()` to include risk_level in sender data
  - Updated `showFundingNetwork3Tier()` JavaScript to color by risk_level instead of is_known
  - Updated summary line to show risky vs trusted breakdown

## Testing

When funder extraction runs and populates `funder_incoming_transfers`:

1. **API Test**:
```bash
curl http://localhost:5002/api/funding-network-3tier/<creator> | jq '.network_tiers[0].senders[0].risk_level'
# Should return: "neutral", "high", "low", "medium", or "unknown"
```

2. **UI Test**:
- Open token in dashboard
- Click "View Funding Patterns"
- Look for:
  - GREEN ✓ senders (trusted infrastructure)
  - RED ⚠️ senders (risky CEX)
  - YELLOW senders (unknown)

## Status

✅ **COMPLETE & DEPLOYED**
- API returns risk_level for all senders
- UI colors based on risk_level
- Summary shows risky vs trusted counts
- Badges (✓ and ⚠️) clearly indicate trust level

**Ready for production use!**
