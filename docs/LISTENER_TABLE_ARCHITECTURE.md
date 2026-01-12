# Listener Table Architecture - Option A Implementation

**Proposed Change**: Display ALL tokens in the listener table, but only fetch live price data for the top 25 performers.

## Current Architecture

**Status**: Top 25 performers displayed with live price updates
- Main table limited to 25 tokens (best performers)
- Funding summary shows 26 risk tokens (all CRITICAL/HIGH/MEDIUM)
- Price data fetched live every 30s-5min (sliding scale)

**Problem**: Users can't see the other 67-70 tokens in the database without looking at database directly

## Proposed Architecture (Option A)

**Display**: All ~93 tokens in main table
**Price Updates**: Only top 25 performers get live fetches

### Benefits

1. **Complete Visibility**: Users see all tokens at a glance
2. **Efficient Resource Use**: Expensive price fetches only for top performers
3. **Risk Context**: Can compare suspicious tokens alongside performers
4. **Cached Prices**: Low performers show last-known price (from database)
5. **Performance**: Reduces API calls to DexScreener/Helius by ~65%

### Implementation Strategy

```
1. Change get_tokens_to_display() in main.py:
   - Instead of LIMIT 25, fetch ALL tokens
   - Sort by % change to identify top 25
   - Mark top 25 for "needs_price_update" flag

2. Modify price update daemon:
   - Only fetch prices for tokens with "needs_price_update" = True
   - Update once every N minutes depending on age

3. Display Logic:
   - Live Prices: Top 25 (updated every 30s-2min)
   - Cached Prices: Others (shown from database, age indicated)

4. UI Indication:
   - Top 25: Price timestamp recent (< 5 min) → "LIVE"
   - Others: Price timestamp older → "CACHED (30 min old)"
```

### Query Changes

**Current (Top 25 only)**:
```sql
SELECT * FROM pools
WHERE hidden_from_table = 0
  AND initial_price_usd > 0
ORDER BY ((dexscreener_price_usd - initial_price_usd) / initial_price_usd) * 100 DESC
LIMIT 25
```

**New (All tokens, identify top 25)**:
```sql
SELECT *,
       ROW_NUMBER() OVER (ORDER BY ((dexscreener_price_usd - initial_price_usd) / initial_price_usd) * 100 DESC) as rank
FROM pools
WHERE hidden_from_table = 0
  AND initial_price_usd > 0
ORDER BY rank
-- Returns all tokens with 'rank' column showing 1-25 for top performers
-- Price daemon can filter WHERE rank <= 25 for live updates
```

### Price Update Strategy

**Current**:
```python
# Updates every 30s-5min based on age
for pool in get_pools_needing_update():
    fetch_live_price(pool)
    update_database(pool)
```

**New**:
```python
# Only update top 25
top_25_ids = get_top_25_performers()  # Cache this, update every 5 min

for pool in top_25_ids:
    if time_since_update > 30s:  # Aggressive for top performers
        fetch_live_price(pool)
        update_database(pool)

# Others get updated rarely (weekly or on demand)
```

### Display Columns

Could add a "Status" column to indicate price freshness:

```
Name    Current Price    % Change   Status         Risk     ...
PUMP    $0.00000123     +125%      ✓ LIVE (3s)    LOW      ...
TOKEN2  $0.00000045     -45%       ~ CACHED (2h)  CRITICAL ...
TOKEN3  $0.00000078     +80%       ~ CACHED (4h)  MEDIUM   ...
```

Legend:
- `✓ LIVE (Xs)` - Price updated in last 5 minutes
- `~ CACHED (Xh)` - Price last updated X hours ago
- `⚠ STALE (Xd)` - Price last updated X days ago

## API Efficiency

### Current (Top 25)
```
Requests/minute: 25 tokens × 6 updates/hour = 2.5 requests/min
Requests/hour: ~150 API calls
```

### New (Top 25 only)
```
Requests/minute: 25 tokens × 2 updates/min (30s interval) = ~1.67 requests/min
Requests/hour: ~100 API calls
Total reduction: 33% fewer API calls
```

## Implementation Steps

1. **Phase 1**: Modify `get_tokens_to_display()` to return all tokens with rank column
2. **Phase 2**: Update listener to display all tokens with status indicator
3. **Phase 3**: Modify price update daemon to only fetch top 25
4. **Phase 4**: Add caching layer for non-top-25 tokens
5. **Phase 5**: Test and optimize

## Risk Considerations

1. **Database Size**: More tokens in memory (~93 vs 25)
   - Not a concern for JSON response size (~50KB vs ~25KB)

2. **UI Performance**: More rows to render
   - Browser can handle 100+ rows easily
   - Consider pagination or scrolling if becomes issue

3. **Price Accuracy**: Older prices for non-top-25
   - Acceptable since they're not actively trading
   - Users checking stale tokens would do manual refresh

4. **API Rate Limiting**: Fewer calls, so less likely to hit limits

## Rollout Plan

1. **Week 1**: Modify get_tokens_to_display() to test with all tokens
2. **Week 2**: Add status indicator column to UI
3. **Week 3**: Implement selective price fetching (top 25 only)
4. **Week 4**: Monitor and optimize performance

## Code References

**Files to Modify**:
- `main.py`: `get_tokens_to_display()` method (~line 999-1030)
- `main.py`: Price update daemon (~line 2357-2452)
- `tests/test_pumpswap_listener.py`: Display columns and status (line ~1707)

**Backward Compatibility**: 
- No database schema changes needed
- API endpoint (`/api/pools`) returns same format
- Client-side changes only (display more rows)
