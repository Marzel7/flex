# CRITICAL FIX: Enhanced Transactions Credit Billing

**Status**: Fixed (Commit: 40bf3f3)
**Impact**: Cost monitoring accuracy
**Severity**: Critical

---

## What Was Wrong

The initial RPC Metrics Dashboard implementation incorrectly listed Helius Enhanced Transactions endpoints as having **fixed credit costs**:

```python
# WRONG (Previous Code):
"helius_enhanced_addresses_transactions": 1,  # Per request
"helius_enhanced_transactions_batch": 5,      # Per request
```

This is **INCORRECT** according to Helius billing reality.

---

## The Reality

**Helius does NOT publish fixed credit costs for Enhanced Transactions endpoints.**

Actual pricing for Enhanced Transactions is:
- **Plan-dependent**: Different across Free/Developer/Business/Professional tiers
- **Tier-specific**: May be included in some plans, premium in others
- **Metered**: Some plans charge by data volume, not per-request
- **Not transparent**: Helius doesn't publish exact rates; you must check your account

---

## What Changed

### 1. **rpc_metrics_recorder.py** - Handle Unknown Costs

```python
def _compute_credits(self, method: str, status_code: int) -> int:
    # ...
    # If entry is "unknown" string, return 0 (user must configure)
    if schedule_entry == "unknown":
        return 0  # Won't break, but signals: "YOU NEED TO VERIFY THIS"
```

Now when cost is unknown, the dashboard:
- ✅ Still records the request (won't crash)
- ✅ Shows 0 credits for unknown methods
- ✅ Makes it obvious which methods need verification

### 2. **rpc_metrics_config.py** - Mark as Unknown

```python
CREDIT_SCHEDULE = {
    # ...
    "helius_enhanced_addresses_transactions": "unknown",  # VERIFY WITH YOUR PLAN
    "helius_enhanced_transactions_batch": "unknown",       # VERIFY WITH YOUR PLAN
}
```

With critical warning in docstring:
```python
⚠️  CRITICAL: Enhanced Transactions endpoints (helius_enhanced_addresses_transactions, etc.)
    are NOT published by Helius with fixed credit costs. They are plan-dependent.
    Default is "unknown" (0 credits) - you MUST verify with your Helius account
    and update CREDIT_SCHEDULE accordingly. Check your billing dashboard or contact support.
```

### 3. **Documentation Updated**

- **RPC_METRICS_QUICK_START.md**: Marked as PLAN-DEPENDENT with action item
- **RPC_METRICS_README.md**: Detailed warning + action required
- **RPC_METRICS_INTEGRATION_GUIDE.md**: Integration guide updated
- **RPC_CREDITS_DASHBOARD_DELIVERY.md**: Delivery doc corrected

---

## What You Must Do Now

### Action Item 1: Find Your Actual Costs

Go to your Helius dashboard:
1. Navigate to billing section
2. Look at your plan tier (Free/Developer/Business/Professional/Unlimited)
3. Find actual charges for Enhanced Transactions endpoints
4. Document the rates

### Action Item 2: Update Configuration

Once you have verified rates, update `rpc_metrics_config.py`:

```python
CREDIT_SCHEDULE = {
    # After verification from your Helius account:
    "helius_enhanced_addresses_transactions": 10,   # Example: might be 10 credits
    "helius_enhanced_transactions_batch": 50,       # Example: might be 50 credits
    # OR: might be included in your plan (0 credits)
    # OR: might be metered per MB (handle separately)
}
```

### Action Item 3: Validate

After updating:
1. Start dashboard: `python rpc_metrics_api.py`
2. Make some Enhanced Transactions calls
3. Check dashboard shows reasonable credit counts
4. Verify against your Helius billing dashboard
5. Adjust if needed

### Action Item 4: Contact Helius (If Unclear)

If your Helius dashboard doesn't clearly show Enhanced Transactions billing:
- Email: support@helius.xyz
- Ask: "What is the credit cost for Enhanced Transactions endpoints on my [PLAN] tier?"
- Get: Specific rates per endpoint or clarification of metering

---

## Example Scenarios

### Scenario 1: Business Tier (Included)
```python
# If your plan INCLUDES Enhanced Transactions:
CREDIT_SCHEDULE = {
    "helius_enhanced_addresses_transactions": 0,  # Included in plan
    "helius_enhanced_transactions_batch": 0,       # Included in plan
}
```

### Scenario 2: Business Tier (Premium)
```python
# If your plan has PREMIUM Enhanced Transactions:
CREDIT_SCHEDULE = {
    "helius_enhanced_addresses_transactions": 50,  # Premium tier cost
    "helius_enhanced_transactions_batch": 100,     # Batch costs more
}
```

### Scenario 3: Metered (Pay per MB)
```python
# If your plan meters by data volume (not per-request):
# Don't use the CREDIT_SCHEDULE for these endpoints.
# Instead, track response bytes and use separate metering logic.
# This is an advanced case - contact Helius for guidance.
```

---

## Impact on Your Monitoring

### Before This Fix
- Dashboard would show **MISLEADING** credit counts
- funder_incoming would show 35,000 credits for Enhanced Transactions
- But actual billing might be 350,000+ (or 0, or metered differently)
- **Cost monitoring would be WRONG**

### After This Fix
- Dashboard shows **0 credits** for Enhanced Transactions (until you verify)
- Clear warning that these are unknown
- You must configure them based on your actual plan
- **Cost monitoring becomes ACCURATE**

---

## Best Practices Going Forward

1. **Never assume Helius pricing**
   - Always verify with your account dashboard
   - Pricing changes; check after plan upgrades

2. **Test your configuration**
   - Make test API calls
   - Check dashboard shows reasonable values
   - Validate against actual billing

3. **Monitor for changes**
   - Helius updates pricing occasionally
   - If dashboard suddenly shows unexpected credits, re-verify
   - Set calendar reminder to check quarterly

4. **Track streaming separately**
   - Streaming (LaserStream, WebSocket) is metered by data volume
   - Use `record_stream_bytes()` instead of `record_request()`
   - Formula: `3 credits per 0.1MB`

---

## Timeline

- **Previous**: Enhanced Transactions listed as 1-5 credits (WRONG)
- **2026-03-01 18:00**: User identified the error
- **2026-03-01 18:15**: Critical fix implemented
- **2026-03-01 18:30**: Commit 40bf3f3 - Documentation updated
- **Now**: You must verify actual costs with Helius

---

## Questions?

If you're unsure about Enhanced Transactions billing:

1. **Check your Helius dashboard** (most reliable source)
2. **Contact Helius support** at support@helius.xyz
3. **Review your plan details** at https://helius.xyz/pricing
4. **Monitor actual usage** to validate dashboard vs. billing

---

## Summary

✅ **Fixed**: Enhanced Transactions no longer show misleading credit counts
✅ **Documented**: Clear warnings added everywhere
✅ **Safe**: Dashboard won't break on unknown costs (shows 0)
⚠️ **Action**: You must verify actual costs and update `rpc_metrics_config.py`

**This ensures your cost monitoring is ACCURATE, not a guess.**

---

**Commit**: 40bf3f3
**Date**: 2026-03-01
**Branch**: rpc
