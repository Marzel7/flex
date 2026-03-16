# Deployment Checklist — Program-Account Discovery

## Pre-Deployment Verification

### Code Status
- [x] `program_account_pool_discovery.py` created
- [x] `pumpfun_curve_listener.py` modified
- [x] Both files compile without errors
- [x] Imports all available
- [x] No syntax errors

### Tests
- [x] Fixture tests created
- [x] Case 2 (Helper PDA rejection) passing
- [x] Case 3 (Post-migration discovery) passing
- [x] All assertions validated

### Documentation
- [x] Architecture guide written
- [x] Implementation guide written
- [x] Test strategy documented
- [x] Deployment guide ready

---

## Deployment Steps

### Step 1: Verify RPC Configuration
- [ ] Check `RPC_HTTP` is set correctly in environment
- [ ] Confirm RPC API key available (optional but recommended)
- [ ] Test RPC connectivity: `curl {RPC_URL} -d '{"jsonrpc":"2.0","method":"getSlot","id":1}'`

### Step 2: Deploy Code
- [ ] Push changes to main branch
- [ ] Verify `program_account_pool_discovery.py` is in `src/core/`
- [ ] Verify `pumpfun_curve_listener.py` is updated
- [ ] Run: `python3 -m py_compile src/core/*.py` to verify

### Step 3: Run Offline Tests
```bash
python test_discovery_with_fixtures.py
```
- [ ] Should show "✅ Passed: 2/2"
- [ ] No errors or failures

### Step 4: Start Listener
```bash
python src/core/pumpfun_curve_listener.py
```
- [ ] Listener starts without errors
- [ ] Connects to WebSocket
- [ ] Ready for token detection

---

## Live Monitoring (Next 3-5 Tokens)

For each token launch:

### During Token Detection
```bash
tail -f listener.log | grep "\[POOL"
```

- [ ] See `[POOL_DETECT]` logs (migration scan)
- [ ] See `[POOL_DISCOVER_FALLBACK]` logs if no migration pool
- [ ] See `[POOL_DISCOVERY_PROGRAM]` logs (program-account query)

### Expected Log Patterns

**Best Case** (Pool in migration TX):
```
[POOL_DETECT] ✅ Pool PDA identified: {addr}
[POOL] ✅ Auto-registered pool
```

**Fallback Case** (Pool found later):
```
[POOL_DETECT] No valid pool found
[POOL_DISCOVER_FALLBACK] Attempt 1/3 (waited 10s)
[POOL_DISCOVERY_PROGRAM] Found N candidates
[POOL_DISCOVERY_PROGRAM] ✅ Candidate validated
[POOL] ✅ Auto-registered pool
```

**No Pool Found**:
```
[POOL_DETECT] No valid pool found
[POOL_DISCOVER_FALLBACK] All strategies exhausted
```

### After Each Token Launch

1. **Check Database**:
   ```sql
   SELECT COUNT(*), COUNT(DISTINCT base_account) FROM token_pool_accounts;
   ```
   - [ ] Counts should be equal (no duplicates)
   - [ ] New pool registered

2. **Check Pricing API**:
   ```bash
   curl http://localhost:5002/api/price/{mint}
   ```
   - [ ] Pool shows up
   - [ ] Price calculated
   - [ ] Vaults are unique

3. **Check Logs**:
   ```bash
   grep "{mint}" listener.log | tail -20
   ```
   - [ ] No errors
   - [ ] Clear discovery path shown
   - [ ] Pool successfully registered

---

## Success Criteria

After 3-5 token launches:

- [ ] All detected tokens have pools registered
- [ ] No duplicate vault addresses in database
- [ ] Discovery logs show expected patterns
- [ ] Pricing data appears in API
- [ ] No helper PDAs in database
- [ ] Log output clearly shows which discovery path was used

---

## Rollback Plan

If issues found:

1. **Quick Rollback**:
   ```bash
   git revert <commit-with-program-account-changes>
   python src/core/pumpfun_curve_listener.py
   ```

2. **Revert to Original Retry Logic**:
   - Restore original `_retry_pool_discovery()` method
   - Listener reverts to transaction-only fallback

---

## Known Issues & Mitigations

### Issue: "No candidates found"
**Mitigation**: Check if pool is actually created, wait longer, try different RPC

### Issue: "RPC timeout"
**Mitigation**: Use Helius API with key, increase timeout values

### Issue: "All candidates rejected"
**Mitigation**: This is expected for some tokens. Verify helper PDAs are being rejected correctly.

### Issue: "Duplicate vault addresses"
**Immediate Action**: 
1. STOP listener
2. Check database for duplicate entries
3. Verify validator is still running
4. Review validation logs

---

## Monitoring Dashboards

### Log Monitoring
```bash
# Watch all pool discovery logs
tail -f listener.log | grep "\[POOL"

# Count successful discoveries
grep "✅.*registered" listener.log | wc -l

# Find failed discoveries
grep "❌" listener.log | grep POOL

# Watch program-account queries
tail -f listener.log | grep "POOL_DISCOVERY_PROGRAM"
```

### Database Monitoring
```sql
-- Check for duplicates
SELECT base_account, COUNT(*) as cnt
FROM token_pool_accounts
GROUP BY base_account
HAVING cnt > 1;

-- Should return empty result

-- Check vault diversity
SELECT COUNT(DISTINCT base_account) as unique_pools
FROM token_pool_accounts;
```

### API Health
```bash
# Check pool stats
curl http://localhost:5002/api/price/health | jq '.pool_stats'

# Verify WebSocket is active
curl http://localhost:5002/api/price/health | jq '.pool_stats.ws'
```

---

## Performance Baselines

### Target Metrics
- **Discovery success rate**: >90% (for tokens with pools)
- **Average discovery time**: <35 seconds (if fallback needed)
- **Duplicate vault rate**: 0% (clean database)
- **RPC calls per token**: <10 (via filters)

### Monitoring Commands
```bash
# Average discovery time
grep "Pool registered" listener.log | 
  awk '{print $(NF-1)}' | 
  awk '{sum+=$1; count++} END {print "Avg:", sum/count, "seconds"}'

# Discovery rate
echo "Migration success: $(grep '\[POOL_DETECT\] ✅' listener.log | wc -l)"
echo "Fallback success: $(grep '\[POOL_DISCOVER_FALLBACK\] ✅' listener.log | wc -l)"
```

---

## Post-Deployment Checklist

### Day 1
- [ ] Listener running
- [ ] Logs monitoring set up
- [ ] Database monitoring in place
- [ ] API health checks working

### After 3 Tokens
- [ ] No unexpected errors
- [ ] Discovery working as predicted
- [ ] Database stays clean
- [ ] Pricing appears in API

### After 5 Tokens
- [ ] Confident in new discovery system
- [ ] All metrics within targets
- [ ] No regression from previous version
- [ ] Ready to consider permanent

---

## Communication Template

**Deployment Notification**:
```
[DEPLOYMENT] Pool Discovery Architecture Upgrade

Change: Program-account fallback discovery for pools not in migration TX

Impact:
- Improved pool discovery reliability
- Faster fallback when needed
- Stricter validation (helper PDAs rejected)

Testing:
- Offline tests: PASSED
- Live monitoring: PENDING (3-5 token launches)

Monitoring:
- Watch: [POOL_DISCOVER_FALLBACK] logs
- Check: No duplicate vault addresses
- Verify: Pool appears in pricing API

Status: ✅ READY FOR DEPLOYMENT
```

---

## Support Contacts

For issues during deployment:

1. **Code Issues**: Review `IMPLEMENTATION_GUIDE.md`
2. **Test Failures**: Run `test_discovery_with_fixtures.py`
3. **Live Issues**: Check logs and `TEST_STRATEGY.md` troubleshooting
4. **RPC Issues**: Verify Helius API key, consider upgrading RPC tier

---

## Final Sign-Off

- [ ] All checks passed
- [ ] Tests passing
- [ ] Documentation complete
- [ ] Ready for deployment
- [ ] Monitoring set up
- [ ] Rollback plan ready

**Status**: ✅ **APPROVED FOR DEPLOYMENT**

Deploy on next business day or after 3-5 test launches confirm behavior.
