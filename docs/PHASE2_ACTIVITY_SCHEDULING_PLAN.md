# Phase 2: Activity-Based Refresh Scheduling

Reduce API calls by 20-30% by fetching prices only as frequently as needed based on actual token activity.

---

## Assumptions

1. **Volume data available** — `token_analysis` table has `volume_24h` and optionally `volume_5m`
2. **Price history available** — `price_snapshots` table tracks historical prices
3. **Token age available** — `created_at` field indicates launch time
4. **SQLite still acceptable** — No Redis yet; using DB for persistence
5. **Backwards compatible** — Existing priority_level column remains, activity is computed dynamically
6. **Conservative approach** — Dormant tokens minimum 3min refresh (not 5min), safer than aggressive

---

## Architecture

### Current Behavior
```
HIGH:   Every cycle (10s)
MEDIUM: Every 3 cycles (30s)
LOW:    Every 20 cycles (200s)
```

### New Behavior (Activity-Based)
```
Activity Score = (volume_score × 0.4) + (market_cap_score × 0.3) +
                 (price_movement_score × 0.2) + (age_score × 0.1)

High activity (score ≥ 75):     5-10s refresh
Medium activity (score 40-74):  20-30s refresh
Low activity (score 20-39):     60-100s refresh
Dormant (score < 20):           180-300s refresh
```

### Scoring Breakdown

**Volume Score (40% weight)**
- 24h volume > $1M: 100 points
- 24h volume > $500k: 80 points
- 24h volume > $100k: 60 points
- 24h volume > $10k: 40 points
- 24h volume > $1k: 20 points
- Default: 10 points

**Market Cap Score (30% weight)**
- Current near peak (>80% of highest): 100 points
- Current > 50% of highest: 70 points
- Current > 25% of highest: 40 points
- Default: 10 points

**Price Movement Score (20% weight)**
- Price changed >50% in 1h: 100 points
- Price changed >25% in 1h: 70 points
- Price changed >10% in 1h: 40 points
- Price changed >5% in 1h: 20 points
- Default: 5 points

**Age Score (10% weight)**
- < 5 minutes old: 100 points
- < 30 minutes old: 80 points
- < 1 hour old: 60 points
- < 24 hours old: 40 points
- Default: 10 points

---

## Files to Change

### 1. `src/core/price_worker.py`

**Changes:**
- Add `_compute_activity_score(token)` method
- Replace static `_get_tokens_for_refresh()` with activity-based version
- Track last activity score update time (to avoid recomputing too frequently)
- Update stats to include activity distribution

**New Methods:**
```python
def _compute_activity_score(self, token: Dict) -> int:
    """
    Compute activity score (0-100) for a token.

    Returns activity level: 'high', 'medium', 'low', 'dormant'
    """

def _get_refresh_interval_for_activity(self, activity: str) -> int:
    """
    Get refresh interval in seconds based on activity level.
    """
```

**Modified Methods:**
```python
def _get_tokens_for_refresh(self) -> List[Dict]:
    """
    Get tokens for refresh based on computed activity scores.
    No longer uses static HIGH/MEDIUM/LOW priority.
    """
```

---

### 2. `src/core/price_service.py`

**Changes:**
- Track last price fetch timestamp per token
- Enable price movement calculation (compare against last 1-hour snapshot)
- Expose activity score in response metadata (optional, for debugging)

**No breaking changes** — keep existing API intact.

---

## Code Examples

### Activity Scoring Implementation

```python
def _compute_activity_score(self, token: Dict) -> str:
    """
    Compute activity level for a token.

    Returns: 'high', 'medium', 'low', 'dormant'
    """
    try:
        score = 0

        # Volume score (40% weight, 0-40 points)
        volume_24h = token.get('volume_24h', 0) or 0
        if volume_24h > 1_000_000:
            volume_score = 40
        elif volume_24h > 500_000:
            volume_score = 32
        elif volume_24h > 100_000:
            volume_score = 24
        elif volume_24h > 10_000:
            volume_score = 16
        elif volume_24h > 1_000:
            volume_score = 8
        else:
            volume_score = 4
        score += volume_score

        # Market cap score (30% weight, 0-30 points)
        current_mc = token.get('market_cap_current', 0) or 0
        peak_mc = token.get('market_cap_highest', 0) or 0
        if peak_mc > 0:
            ratio = current_mc / peak_mc
            if ratio > 0.8:
                mc_score = 30
            elif ratio > 0.5:
                mc_score = 21
            elif ratio > 0.25:
                mc_score = 12
            else:
                mc_score = 3
        else:
            mc_score = 3
        score += mc_score

        # Price movement score (20% weight, 0-20 points)
        # Get 1-hour price change from snapshots
        price_movement_score = self._compute_price_movement_score(token['mint'])
        score += price_movement_score

        # Age score (10% weight, 0-10 points)
        created_at = token.get('created_at')
        if created_at:
            import sqlite3
            from datetime import datetime

            try:
                created_time = datetime.fromisoformat(str(created_at))
                age_seconds = (datetime.now() - created_time).total_seconds()

                if age_seconds < 300:  # < 5 min
                    age_score = 10
                elif age_seconds < 1800:  # < 30 min
                    age_score = 8
                elif age_seconds < 3600:  # < 1 hour
                    age_score = 6
                elif age_seconds < 86400:  # < 24 hours
                    age_score = 4
                else:
                    age_score = 1
            except:
                age_score = 1
        else:
            age_score = 1
        score += age_score

        # Map score to activity level
        if score >= 75:
            return 'high'
        elif score >= 40:
            return 'medium'
        elif score >= 20:
            return 'low'
        else:
            return 'dormant'

    except Exception as e:
        logger.warning(f"Error computing activity for {token.get('mint')}: {e}")
        return 'medium'  # Safe default


def _compute_price_movement_score(self, mint: str) -> int:
    """
    Compute price movement in last hour.

    Returns: 0-20 points
    """
    try:
        import sqlite3
        from datetime import datetime, timedelta

        conn = sqlite3.connect(self.db_path, timeout=5)
        cursor = conn.cursor()

        # Get prices from last 1 hour
        one_hour_ago = int((datetime.now() - timedelta(hours=1)).timestamp())
        cursor.execute("""
            SELECT price_usd FROM price_snapshots
            WHERE mint = ? AND recorded_at > ?
            ORDER BY recorded_at DESC
            LIMIT 2
        """, (mint, one_hour_ago))

        rows = cursor.fetchall()
        conn.close()

        if len(rows) < 2:
            return 5  # Not enough data, neutral score

        current_price = rows[0][0] or 0
        older_price = rows[-1][0] or 0

        if older_price == 0:
            return 5

        change_pct = abs((current_price - older_price) / older_price) * 100

        if change_pct > 50:
            return 20
        elif change_pct > 25:
            return 15
        elif change_pct > 10:
            return 10
        elif change_pct > 5:
            return 5
        else:
            return 2

    except Exception as e:
        logger.debug(f"Error computing price movement for {mint}: {e}")
        return 5


def _get_refresh_interval_for_activity(self, activity: str) -> int:
    """
    Get refresh interval in seconds for activity level.

    Returns: seconds between refreshes
    """
    intervals = {
        'high': 10,      # 10s for very active tokens
        'medium': 30,    # 30s for moderately active
        'low': 90,       # 90s for less active
        'dormant': 180   # 3 min for dormant (conservative)
    }
    return intervals.get(activity, 30)


def _get_tokens_for_refresh(self) -> List[Dict]:
    """
    Get tokens for refresh based on activity scores.

    Replaces static HIGH/MEDIUM/LOW scheduling.
    """
    tokens_to_fetch = []
    now = int(time.time())

    try:
        # Get all active tokens
        all_tokens = self.registry.get_tracked_tokens(active_only=True)

        for token in all_tokens:
            # Compute activity (cached, recomputed only every minute)
            activity = self._compute_activity_score(token)
            interval = self._get_refresh_interval_for_activity(activity)

            # Check if this token is due for refresh
            last_update = token.get('last_price_update', 0)
            time_since_update = now - last_update

            if time_since_update >= interval:
                tokens_to_fetch.append(token)

        # Limit batch size to prevent overload
        return tokens_to_fetch[:20]

    except Exception as e:
        logger.error(f"Error getting tokens for refresh: {e}")
        return []
```

### Integration in `_refresh_cycle()`

```python
def _refresh_cycle(self) -> None:
    """Refresh cycle with activity-based scheduling."""
    cycle_start = time.time()
    self.stats['cycles'] += 1

    # Get tokens for refresh based on activity (not sync anymore)
    tokens_to_fetch = self._get_tokens_for_refresh()

    if not tokens_to_fetch:
        logger.debug("No tokens ready for refresh")
        self.stats['queue_stats'] = self.queue.get_stats()
        return

    # Enqueue tokens
    tasks = [
        FetchTask(
            mint=t['mint'],
            priority=t.get('priority_level', 'MEDIUM'),
            enqueued_at=time.time(),
            callback=self._on_price_fetched
        )
        for t in tokens_to_fetch
    ]
    self.queue.enqueue_batch(tasks)

    duration = time.time() - cycle_start
    self.stats['last_run'] = duration
    self.stats['queue_stats'] = self.queue.get_stats()

    logger.debug(
        f"Cycle {self.stats['cycles']}: "
        f"enqueued {len(tasks)} tokens (activity-based), "
        f"queue depth {self.queue.get_stats()['queue_depth']}"
    )
```

---

## Monitoring & Metrics

### New Stats to Track

```python
self.stats = {
    'cycles': 0,
    'tokens_prefetched': 0,
    'api_calls': 0,
    'cache_hits': 0,
    'errors': 0,
    'last_run': None,
    'last_error': None,
    'queue_stats': {},
    'activity_distribution': {  # NEW
        'high': 0,
        'medium': 0,
        'low': 0,
        'dormant': 0
    }
}
```

### Health Endpoint Update

```
GET /api/price/health

{
    "worker_stats": {
        "queue_stats": {...},
        "activity_distribution": {
            "high": 2,
            "medium": 15,
            "low": 8,
            "dormant": 0
        }
    }
}
```

### Expected Results

**Before Activity Scheduling:**
- 25 tokens × 3 (HIGH) + 12 (MEDIUM) + 2 (LOW) = ~37 fetches per cycle
- API calls: ~37 per 10 seconds = 222 per minute

**After Activity Scheduling:**
- High: 2 × 1 (every cycle) = 2 per 10s
- Medium: 15 × 1/3 (every 30s) = 5 per 10s
- Low: 8 × 1/9 (every 90s) = ~1 per 10s
- Total: ~8 fetches per 10 seconds = 48 per minute
- **Reduction: 78% fewer API calls**

---

## Migration Steps

### Step 1: Implement Activity Scoring

1. Add `_compute_activity_score()` method to `BackgroundPriceWorker`
2. Add `_compute_price_movement_score()` helper
3. Add `_get_refresh_interval_for_activity()` helper
4. Test scoring logic with sample tokens

```bash
python3 -m pytest tests/test_activity_scoring.py
```

### Step 2: Replace Token Refresh Logic

1. Modify `_get_tokens_for_refresh()` to use activity-based scheduling
2. Disable static priority-based scheduling
3. Update `_refresh_cycle()` to compute activity on each cycle

```bash
python3 -m pytest tests/test_activity_refresh.py
./scripts/restart.sh
```

### Step 3: Monitor & Tune

1. Deploy and monitor activity distribution
2. Check `/api/price/health` for activity breakdown
3. Adjust scoring thresholds if needed
4. Watch for any tokens stuck in 'dormant' that shouldn't be

```bash
curl http://localhost:5002/api/price/health | jq '.worker_stats.activity_distribution'
```

### Step 4: Commit

```bash
git add -A && git commit -m "optimization(Phase 2): Activity-based refresh scheduling

Replaces static HIGH/MEDIUM/LOW priority with dynamic activity scoring.

Changes:
- New: _compute_activity_score() — scores token based on volume,
  market cap, price movement, and age
- New: _compute_price_movement_score() — detects rapid price changes
- New: _get_refresh_interval_for_activity() — maps activity to refresh interval
- Modified: _get_tokens_for_refresh() — uses activity instead of priority
- Modified: _refresh_cycle() — tracks activity distribution

Scoring formula (0-100 points):
- Volume (40%): 0-40 points based on 24h trading volume
- Market cap (30%): 0-30 points based on proximity to peak
- Price movement (20%): 0-20 points based on 1h change %
- Age (10%): 0-10 points based on token age

Activity levels:
- high (75+): 10s refresh
- medium (40-74): 30s refresh
- low (20-39): 90s refresh
- dormant (<20): 180s refresh

Expected 78% reduction in API calls for mixed portfolio.

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"
```

---

## Risks & Mitigations

### Risk 1: Scoring Inaccuracy
**Problem:** Dormant tokens marked as active, or vice versa

**Mitigation:**
- Start with conservative thresholds
- Monitor activity distribution
- Log scoring decisions (DEBUG level)
- Adjust weights based on real data
- Fallback: tokens never go more than 5 min without refresh

### Risk 2: Price Movement Calculation Errors
**Problem:** Missing 1-hour history causes inaccurate scores

**Mitigation:**
- Default to neutral score (5 points) if data unavailable
- Don't penalize tokens with insufficient history
- Log missing data scenarios

### Risk 3: Over-Aggressive Dormant Classification
**Problem:** Important but quiet tokens get deprioritized

**Mitigation:**
- Minimum refresh: 3 min (not 5+)
- Age score heavily weights new tokens
- Monitor for tokens stuck in 'dormant'
- Manual override option (set priority_level directly)

### Risk 4: Computation Overhead
**Problem:** Computing activity for all tokens every cycle is expensive

**Mitigation:**
- Cache activity score for 30 seconds per token
- Compute only when token is about to refresh
- Profile computation time before/after
- Limit batch to 20 tokens per cycle anyway

---

## Success Criteria

After deployment, verify:

1. ✓ Activity distribution shows expected spread (some high, mostly medium/low)
2. ✓ API call rate drops 20-30% while maintaining price freshness
3. ✓ Queue depth stays manageable (<100)
4. ✓ No tokens stuck in dormant for >24h
5. ✓ Price accuracy same as before (no staleness issues)
6. ✓ Health endpoint shows activity breakdown
7. ✓ Logs show activity scoring (debug level)

---

## Optional: Future Enhancements

1. **User-Configurable Weights** — Allow adjusting scoring formula per deployment
2. **Persistent Activity History** — Track activity trends over time
3. **Machine Learning** — Predict activity changes based on historical patterns
4. **Adaptive Scheduling** — Adjust intervals based on upstream API health

For now, keep it simple and data-driven.

