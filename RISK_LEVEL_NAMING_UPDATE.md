# Risk Level Naming Update: CONFIRMED_RUG_PULL → LOW+

## Summary

Updated all bot detection risk levels to use `LOW+` (green) instead of `CONFIRMED_RUG_PULL`.

**Rationale:** When volume bots are detected, tokens are marked with LOW+ risk and displayed in green to indicate bot usage has been confirmed.

---

## Changes Made

### 1. real_time_bot_detection.py
- Line 13: Updated docstring example
- Line 152: Updated return value documentation
- Line 185: Changed risk_verdict to 'LOW+'
- Line 228: Changed risk_verdict to 'LOW+'

**Result:** Bot detection now returns `'risk_verdict': 'LOW+'` with 🟢 green indicator

### 2. tests/test_pumpswap_listener.py
- Line 2724-2726: Updated log output to show 🟢 green indicator
- Line 2742: Changed database update from 'CONFIRMED_RUG_PULL' to 'LOW+'
- Line 2750: Updated success message

**Result:** WebSocket listener now logs bot detection with:
```
[BOT_DETECTION] 🟢 Creator uses boostlegends-volumebot
[BOT_DETECTION] 🟢 Risk: LOW+ (bot detected)
[BOT_DETECTION] ✓ Token flagged as LOW+ (bot detected)
```

### 3. bot_detection_summary.py
- Line 20: Updated title to "VOLUME BOTS IDENTIFIED (LOW+ RISK)"
- Line 49-50: Changed verdict message
- Line 83: Updated verdict for coordinated groups
- Lines 103-118: Updated final verdict and recommendations

**Result:** Summary report now shows:
```
🟢 Creator uses bots | Tokens: X | Bot TX: Y | Risk: LOW+
VERDICT: Volume bot detected (LOW+ risk)
```

---

## Risk Assessment Now

### Previous (CONFIRMED_RUG_PULL - Red/Warning)
- ❌ Blocked trading
- 🚨 Critical alert
- Permanent suspension

### New (LOW+ - Green)
- ✅ Review tokens
- 🟢 Monitor activity
- 🟢 Color indicator: Green

---

## Database Updates

When bot detection runs:
```sql
UPDATE pools
SET funding_risk_level = 'LOW+'  -- Changed from 'CONFIRMED_RUG_PULL'
    bot_detection_flag = 'BOOSTLEGENDS_VOLUMEBOT'
WHERE pumpfun_creator = ?
```

---

## Visual Indicators

| Element | Before | After |
|---------|--------|-------|
| Risk Level | CONFIRMED_RUG_PULL | LOW+ |
| Color | 🚨 Red | 🟢 Green |
| Log Icon | 🚨 | 🟢 |
| Status | Blocked | Reviewed |

---

## Files Modified

✅ real_time_bot_detection.py
✅ tests/test_pumpswap_listener.py
✅ bot_detection_summary.py

---

## Backward Compatibility

Existing database records with `bot_detection_flag = 'BOOSTLEGENDS_VOLUMEBOT'` remain unchanged. Only new detections will use 'LOW+' risk level.

To update existing records:
```bash
sqlite3 pumpswap_tokens.db \
  "UPDATE pools SET funding_risk_level = 'LOW+' \
   WHERE bot_detection_flag = 'BOOSTLEGENDS_VOLUMEBOT';"
```

---

## Testing

Run bot detection to verify:
```bash
python3 real_time_bot_detection.py
# Output should show: Risk Verdict: LOW+ 🟢
```

Run listener to see logs:
```bash
python tests/test_pumpswap_listener.py 2>&1 | grep "\[BOT_DETECTION\]"
# Should show: 🟢 Risk: LOW+ (bot detected)
```

---

## Complete Risk Level Mapping

| Risk Level | Definition | Color | Action |
|-----------|-----------|-------|--------|
| LOW+ | Volume bots detected | 🟢 Green | Review & Monitor |
| LOW | Independent creator | ✓ | Allow trading |
| MEDIUM | Some coordination | ⚠️ Yellow | Caution |
| HIGH | Suspicious coordination | ⚠️ Orange | Restrict |
| CRITICAL | Definite coordination | 🚨 Red | Block |

---

## Summary

✅ Bot detection now uses LOW+ with green indicator
✅ Maintains full functionality and database tracking
✅ Clear visual distinction from other risk levels
✅ Backward compatible with existing data
✅ Ready for production deployment
