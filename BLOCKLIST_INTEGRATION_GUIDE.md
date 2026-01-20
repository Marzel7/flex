# Creator Blocklist Integration Guide

## Overview

The rug creator blocklist system automatically tracks creators who launch rugged tokens and provides pre-buy checking to prevent trading with known serial ruggers.

**Storage:** All blocklist data is stored in the `creator_blocklist` table of the database for persistence, real-time updates, and querying. No JSON files needed.

**Automatic Updates:** When the WebSocket listener detects a rug, the creator is automatically added to the blocklist in the database.

## Quick Start

### 1. Check Token Before Buying

```python
from utils.creator_blocklist_checker import check_token_safety

# Before making a purchase
is_safe, reason = check_token_safety(token_mint)

if not is_safe:
    print(f"⚠️ Skipping purchase: {reason}")
    return

# Safe to buy
print(f"✅ Token is safe: {reason}")
# Continue with buy logic
```

### 2. Get Creator Details

```python
from utils.creator_blocklist_checker import get_token_creator_info

creator_info = get_token_creator_info(token_mint)

if creator_info:
    print(f"Creator: {creator_info['creator']}")
    print(f"Status: {creator_info['status']}")
    print(f"Rugs: {creator_info['rug_count']}")
```

## Integration Points

### A. Buy Token Script (`utils/buy_token.py`)

Add pre-buy check at line 89 (before trading):

```python
# Add at the top of the file
from utils.creator_blocklist_checker import check_token_safety

# Then in main(), after trader initialization and before buy:

    # CHECK BLOCKLIST BEFORE BUYING
    is_safe, reason = check_token_safety(token_mint)
    if not is_safe:
        print(f"\n❌ PURCHASE BLOCKED")
        print(f"Reason: {reason}")
        print(f"\nThis token's creator has a history of rugs.")
        print(f"Use --force-buy to override (not recommended)")
        if "--force-buy" not in sys.argv:
            return
    else:
        print(f"✅ Creator check: {reason}\n")

    try:
        print("[1/4] Getting quote from Jupiter...")
```

### B. Trading Executor (`utils/trading_executor.py`)

Add to `buy_token()` method:

```python
async def buy_token(self, token_mint: str, sol_amount: float,
                    user_keypair, slippage_bps: int = 500,
                    check_creator: bool = True, **kwargs):
    """
    Buy a token.

    Args:
        token_mint: Token to buy
        sol_amount: Amount of SOL to spend
        user_keypair: Keypair to sign with
        slippage_bps: Slippage in basis points
        check_creator: Check blocklist before buying (default True)
    """

    # Check blocklist if enabled
    if check_creator:
        from utils.creator_blocklist_checker import check_token_safety
        is_safe, reason = check_token_safety(token_mint)
        if not is_safe:
            return {
                'status': 'BLOCKED',
                'error': f'Creator blocklist: {reason}',
                'signature': None,
                'output_amount': None
            }

    # Continue with normal buy logic...
```

### C. WebSocket Listener (`pumpfun_curve_listener.py`)

Already integrated! When a rug is detected:

```python
# In _update_price_in_db():
if rug_detected:
    # Get creator and add to blocklist
    creator = get_creator_from_database(token_mint)
    asyncio.create_task(self._add_rug_creator_to_blocklist(token_mint, creator))
```

The blocklist is updated automatically in real-time as rugs are detected.

## Database Schema

### `creator_blocklist` Table

All blocklist data is stored in the `creator_blocklist` table:

```sql
CREATE TABLE creator_blocklist (
    creator_address TEXT PRIMARY KEY,
    rug_count INTEGER DEFAULT 0,
    first_rug_detected_at TIMESTAMP,
    last_rug_detected_at TIMESTAMP,
    rugged_tokens TEXT,  -- JSON array of token mints
    reputation TEXT,     -- 'MALICIOUS' (2+ rugs), 'SUSPICIOUS' (1 rug)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Updated Automatically:** When WebSocket listener detects a rug, the creator is inserted/updated in this table.

**Query Examples:**

```sql
-- Get all blocked creators
SELECT creator_address, rug_count, reputation FROM creator_blocklist ORDER BY rug_count DESC;

-- Get serial ruggers (2+ rugs)
SELECT creator_address, rug_count FROM creator_blocklist WHERE rug_count >= 2;

-- Get watch list (1 rug)
SELECT creator_address FROM creator_blocklist WHERE rug_count = 1;

-- Get rugged tokens for a creator
SELECT rugged_tokens FROM creator_blocklist WHERE creator_address = '...';
```

## Blocklist Categories

### 🚨 Serial Ruggers (MALICIOUS)
- **Criteria:** 2+ tokens launched, >40% rug rate
- **Action:** Always skip unless overridden
- **Example:** Creator with 5 tokens, 3 rugged = 60% rug rate

### 📝 Watch List (SUSPICIOUS)
- **Criteria:** 1 token launched, 100% rug rate
- **Action:** Skip or proceed with caution
- **Example:** One-hit rugger with perfect rug record

### ✅ Clean
- **Criteria:** 2+ tokens, <40% rug rate (or >0 successful tokens)
- **Action:** Safe to trade
- **Example:** Creator with 5 tokens, 0 rugs = 0% rug rate

## API Usage

### Check Safety

```python
is_safe, reason = check_token_safety("DzLqUcg9ExR8zJxuqerDr3qmpWeMEwiW4iD2prb8pump")

# Returns:
# (True, "Creator not in blocklist")  # Safe to buy
# (False, "🚨 SERIAL RUGGER - Creator has 2 confirmed rugs")  # Blocked
# (False, "📝 WATCH LIST - Creator has 1 rug, use caution")  # Suspicious
```

### Get Creator Info

```python
info = get_token_creator_info("token_mint")

# Returns:
{
    "creator": "8i2avmxgeHMz5VoZNo21mYGjZpkS6tY3csVkGseyE5Fu",
    "status": "blocked",
    "rug_count": 2,
    "rugged_tokens": ["mint1", "mint2"],
    "first_detected": "2026-01-19T12:00:00",
    "last_rug": "2026-01-19T12:30:00"
}
```

## Running Scripts

### View Current Blocklist

```bash
python3 scripts/view_rug_blocklist.py
```

Output shows:
- All blocked creators sorted by rug count
- Risk levels: 🚨 CRITICAL (3+), ⚠️ HIGH (2), 📝 WATCH (1)
- Affected tokens for each creator

### Analyze Creator Patterns

```bash
python3 scripts/analyze_creator_patterns.py
```

Regenerates `creator_block_list.json` from database analysis.

## Database Schema

### Token Creator Column

```sql
-- In token_analysis table
earliest_tx_creator TEXT  -- Creator extracted from earliest transaction

-- Query to see creators
SELECT DISTINCT earliest_tx_creator
FROM token_analysis
WHERE rug_indicator = 'quick_peak_low_mc'
ORDER BY earliest_tx_creator;
```

## Log Output Examples

### When Buying Blocked Token

```
❌ PURCHASE BLOCKED
Reason: 🚨 SERIAL RUGGER - Creator has 2 confirmed rugs

This token's creator has a history of rugs.
```

### When Rug Detected (Automatic)

```
[RUG] 🚨 DETECTED: DzLqUcg9... | Time: 23.5 min | Peak MC: $57,336
[BLOCKLIST] 🚨 SERIAL RUGGER: 8i2avmxg... | 2 rugs detected
```

### When Safe Token Checked

```
✅ Creator check: Creator not in blocklist
```

## Performance Notes

- **Blocklist loading:** <1ms (cached in memory)
- **Database lookup:** <10ms per token
- **Total pre-buy check:** <15ms
- **No network latency** (local file and database only)

## Refreshing Blocklist

If you update blocklist files while bot is running:

```python
from utils.creator_blocklist_checker import get_checker

checker = get_checker()
checker.refresh_blocklist()  # Force reload from disk
```

## Override Mechanism

For testing or manual trading:

```bash
python3 utils/buy_token.py <MINT> --force-buy
```

This skips the blocklist check (not recommended for production).

## Next Steps

1. **Integrate into buy_token.py** - Add safety check before purchase
2. **Monitor blocklist** - Watch logs for new rug detections
3. **Review periodically** - Check `view_rug_blocklist.py` for patterns
4. **Adjust thresholds** - Tune rug detection sensitivity if needed

## Troubleshooting

### "Creator unknown" message
- Token's earliest transaction hasn't been analyzed yet
- Backfill script needs to run: `python3 scripts/backfill_earliest_tx_creators.py`

### Blocklist not found
- `rug_creator_blocklist.json` hasn't been created yet
- Run listener and wait for first rug detection
- Or run analysis: `python3 scripts/analyze_creator_patterns.py`

### Old blocklist entries
- Entries accumulate as system detects more rugs
- Periodically review with `scripts/view_rug_blocklist.py`
- Can be safely cleared if starting fresh

## References

- Creator Extraction: [CREATOR_EXTRACTION_GUIDE.md](CREATOR_EXTRACTION_GUIDE.md)
- Rug Detection: Automatic on 10-second price updates
- Blocklist System: [scripts/view_rug_blocklist.py](scripts/view_rug_blocklist.py)
