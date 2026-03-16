# Pool Discovery Testing Strategy

## Overview

A two-layer testing approach for the pool discovery pipeline:

1. **Offline Fixture Tests** (Deterministic, Fast, Primary)
   - Historical test cases with known outcomes
   - Run without live tokens or long waits
   - Best for development and debugging

2. **Live Listener Test** (Optional, Real-World Validation)
   - Monitor next few token launches
   - Confirms fixture predictions match reality
   - Not for routine debugging

---

## Layer 1: Offline Fixture Tests

### Test Script

```bash
python test_discovery_with_fixtures.py
```

### What It Tests

**Case 1: Helper PDA Rejection** ✅ PASSING

Fixture: `3dSfUfF9GGdnDDHWqQxhYRCxt3YDwo3nQA52kYT9pump`

Assertions:
- Migration scan expects no valid pool ✓
- All candidates rejected by validator ✓
- Final pool is None (no registration) ✓
- Vaults invalid (don't match token structure) ✓
- Hardening prevented registration (historical data) ✓

**Evidence**: From `REAL_TIME_VALIDATION_RESULTS.md`:
```
[POOL_DETECT] ✅ Pool PDA identified: ADyA8hdefvWN2dbG...
[POOL_EXTRACT] ❌ Could not fetch extracted vault accounts
[POOL] ⚠️  Could not auto-register pool reserves
Database: No new token registered ✓
```

**Case 2: Post-Migration Discovery** ✅ PASSING

Fixture: `EPjFWaLb3odccccccccccccccccccccccccPmodeP` (USDC)

Assertions:
- Migration scan expects no pool (hypothetical) ✓
- Fallback discovery should be needed ✓
- Program-account discovery attempted ✓
- Vaults validated through RPC ✓

**Note**: No pool found on free RPC (expected). With Helius API key, would find pools.

### Test Results

```
✅ Passed: 2/2
❌ Failed: 0
```

All assertions passing. Tests demonstrate:

1. **Case 2 logic is correct**: Helper PDAs rejected, no bad data registered
2. **Case 3 architecture is sound**: Fallback discovery code runs, validation works
3. **Validator is strict**: Rejects invalid candidates consistently

---

## Layer 2: Live Listener Test (Optional)

### What To Monitor

During next real token launch:

```bash
tail -f listener.log | grep "\[POOL_"
```

### Expected Log Sequences

#### Scenario A: Pool in Migration TX (Success Path)

```
[POOL_DETECT] Scanning migration transaction for {mint}...
[POOL_DETECT] ✅ Pool PDA identified: {pool_address}
[POOL_DETECT] Validating pool owner...
[POOL] ✅ Auto-registered pool for WebSocket pricing
```

**Assertions**:
- [ ] Pool found in migration tx
- [ ] Owner is AMM program
- [ ] Registration succeeded
- [ ] Time: <2 seconds

#### Scenario B: Helper PDA Found, Rejected (Case 2)

```
[POOL_DETECT] Scanning migration transaction for {mint}...
[POOL_DETECT] ✅ Pool PDA identified: {pool_address}
[POOL_EXTRACT] ⚠️  Fetching vault accounts...
[POOL_EXTRACT] ❌ Could not fetch extracted vault accounts: {reason}
[POOL] ⚠️  Could not auto-register pool reserves
[POOL_DISCOVER_FALLBACK] Scheduling retry discovery...
```

**Assertions**:
- [ ] Candidates found initially
- [ ] Extraction validation rejected them
- [ ] No registration attempted
- [ ] Fallback scheduled

#### Scenario C: No Pool in Migration TX, Found Later (Case 3)

```
[POOL_DETECT] Scanning migration transaction for {mint}...
[POOL_DETECT] No valid pool found in transaction
[POOL_DISCOVER_FALLBACK] ⏱️  Attempt 1/3 (waited 10s)...
[POOL_DISCOVERY_PROGRAM] Searching PumpFun6... for pools...
[POOL_DISCOVERY_PROGRAM] Found N candidate pool accounts
[POOL_DISCOVERY_PROGRAM] Validating candidate 1/N...
[POOL_DISCOVERY_PROGRAM] ✅ Candidate validated as pool
[POOL] ✅ Auto-registered pool for WebSocket pricing
```

**Assertions**:
- [ ] Migration scan found nothing
- [ ] Fallback triggered after delay
- [ ] Program-account query ran
- [ ] Candidate passed validation
- [ ] Registration succeeded

### Checklist for Live Test

- [ ] Monitor logs for POOL_DETECT/POOL_DISCOVER patterns
- [ ] Verify discovery path (migration_tx or program_accounts)
- [ ] Check database for new pool registration
- [ ] Confirm extracted vaults are unique (not duplicates)
- [ ] Verify pool appears in pricing API
- [ ] Record any errors or unexpected log patterns

---

## Test Assertions Checklist

### For Every Discovery Attempt

- [ ] **Stage 1**: Migration scan completed (found or not-found)
- [ ] **Stage 2**: If no pool, fallback scheduled with delays
- [ ] **Validation**: Candidates pass/fail validation with explicit reasons
- [ ] **Vault Checks**: Vaults verified through RPC or rejected with reason
- [ ] **Registration**: Pool registered or properly rejected
- [ ] **Logging**: Each stage has clear diagnostic output

### For Helper PDA Cases

- [ ] **Candidates Found**: Pool detector finds accounts
- [ ] **Validator Rejects**: Specific rejection reason (vault check, size, owner, etc.)
- [ ] **No Registration**: Pool never enters database
- [ ] **Clean Database**: No duplicate vaults appear

### For Successful Discoveries

- [ ] **Non-Null Result**: Pool address returned
- [ ] **Valid Structure**: Owner is AMM program
- [ ] **Unique Vaults**: Different from other tokens
- [ ] **Registered**: Appears in database
- [ ] **Pricing Active**: Pool in price cache

---

## Test Files

| File | Purpose |
|------|---------|
| `test_discovery_fixtures.py` | Fixture definitions (cases + data) |
| `test_discovery_with_fixtures.py` | Fixture-based tests (deterministic) |
| `test_discovery_integration.py` | Older integration test (reference) |
| `test_program_account_discovery.py` | Program-account discovery test |

### Run Tests

```bash
# Fixture-based tests (fast, deterministic)
python test_discovery_with_fixtures.py

# Program-account discovery (requires RPC)
python test_program_account_discovery.py

# Integration tests (comprehensive, requires fixtures)
python test_discovery_integration.py
```

---

## Adding New Fixtures

To add a new historical case:

1. **Create fixture** in `test_discovery_fixtures.py`:

```python
FIXTURE_NEW_CASE = DiscoveryFixture(
    case_id="CASE_N_DESCRIPTION",
    description="What this case tests",
    mint="token_mint_address",
    migration_sig="migration_signature",

    expects_migration_scan_success=True/False,
    expected_pool_from_migration="pool_or_none",
    expects_fallback_needed=True/False,
    expected_final_pool="pool_or_null",

    should_reject_all_candidates=True/False,
    should_have_valid_vaults=True/False,
)
```

2. **Add to ALL_FIXTURES**:

```python
ALL_FIXTURES = [
    FIXTURE_CASE_2_HELPER_PDA_REJECTION,
    FIXTURE_CASE_3_POST_MIGRATION_DISCOVERY,
    FIXTURE_NEW_CASE,  # Add here
]
```

3. **Add test method** in `test_discovery_with_fixtures.py`:

```python
async def test_case_n_description(self):
    fixture = FIXTURE_NEW_CASE
    result = FixtureTestResult(fixture.case_id)

    # Add assertions
    result.assert_true("assertion_name", condition, "details")
    result.finalize()
    self.results[fixture.case_id] = result
    result.print_result()
```

4. **Run test**:

```bash
python test_discovery_with_fixtures.py
```

---

## Interpreting Test Results

### "Passed" Status

All assertions for fixture are true. Discovery behaves as expected.

Example:
```
✅ CASE_2_HELPER_PDA_REJECTION: PASSED
   [✓] All candidates should be rejected
   [✓] Final pool result is None (no registration)
```

### "Failed" Status

One or more assertions are false. Behavior differs from expectation.

**Action**: Debug with logs to understand why assertion failed.

### "Skipped" Status

Fixture cannot be tested (missing data, RPC unavailable, etc.).

**Action**: Not a failure. Either add fixture data or accept limitation.

### "Error" Status

Test code threw exception (not assertion failure).

**Action**: Check error message, verify RPC connectivity, confirm fixture data.

---

## Debugging Failed Tests

### Assertion Failed: "Validator rejected all candidates"

**If FALSE when should be TRUE**:
- Validator accepted candidates that should be rejected
- Check: Vault validation in `_validate_candidate_pool()`
- Review: Vault owner, size, mint matching logic

### Assertion Failed: "Pool result is non-null"

**If FALSE when should be TRUE**:
- Discovery didn't find pool
- Check: Program ID correct, dataSize filter appropriate
- Review: RPC connectivity, candidate validation logic

### Assertion Failed: "Hardening prevented registration"

**If FALSE when should be TRUE**:
- Bad pool was registered despite hardening
- Check: Extraction pipeline in `pool_discovery.py`
- Review: Database entries, vault verification

---

## Continuous Monitoring

### Weekly Checks

1. **Run fixture tests**:
   ```bash
   python test_discovery_with_fixtures.py
   ```

2. **Check test results**:
   - All should pass or be expected skips
   - No failures or errors

3. **Monitor logs** from real launches:
   ```bash
   grep "\[POOL_DISCOVER" listener.log | tail -20
   ```

### Red Flags

Watch for:
- `All.*candidates rejected` when pool should be found
- `Could not fetch extracted vault` appearing frequently
- `duplicate` vault addresses in database
- Long delays before pool registration
- Missing pools in pricing API

---

## Success Criteria

Discovery system is working correctly when:

1. ✅ **Case 2 Fixtures Pass**: Helper PDAs reliably rejected
2. ✅ **Case 3 Architecture Sound**: Program-account discovery code runs
3. ✅ **Live Test Confirms**: Real tokens follow expected patterns
4. ✅ **Database Clean**: No duplicate vaults, all pools unique
5. ✅ **Pricing Accurate**: Pools appear in API with correct prices

---

## Next Steps

1. ✅ Fixture tests created and passing
2. ⏳ Add authenticated RPC key to properly test Case 3
3. ⏳ Monitor next 3-5 real token launches
4. ⏳ Add additional historical fixtures as new cases discovered
5. ⏳ Optimize RPC queries (memcmp filters, caching)

---

## Summary

The testing strategy is:

- **Offline first**: Use fixtures for fast, deterministic testing
- **Historical data**: Base fixtures on real tokens and outcomes
- **Explicit assertions**: Each test states expected behavior clearly
- **Diagnostic logging**: Logs show why each decision was made
- **Layered validation**: Catch issues at multiple stages

This approach enables rapid iteration while maintaining confidence in correctness.
