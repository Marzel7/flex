# PumpSwap Integration Plan

## Overview
PumpSwap is the Raydium V4 pool destination where PumpFun tokens migrate after their bonding curve completes. We monitor PumpSwap pools to detect and track tokens that originated from PumpFun's bonding curve phase.

## Key Architecture

### PumpFun Program
- **Program ID**: `6EF8rQNi3ECMFzvZR2Yy5z2kh2eCMJQEt1g5TQPVe1fq`
- Uses bonding curves for initial token sales
- Creates Raydium V4 pools when bonding curve completes

### Integration Approach
Monitor Raydium V4 pools to identify and track tokens that migrated from PumpFun:
1. **Raydium V4 Program Monitoring** - already implemented
2. **PumpFun creator identification** - detect PumpFun-originated tokens
3. **Token metadata analysis** - look for PumpFun/PumpSwap markers
4. **Bonding curve history tracking** - optional, for context

## Detection Methods

### Method 1: Raydium V4 Event Monitoring (PRIMARY)
```
- Monitor Raydium V4 pool creation events (already implemented)
- Check token metadata for PumpFun creator/origin markers
- Identify tokens that migrated from PumpFun bonding curve
- Prices calculable from vault balances on PumpSwap pool
```

### Method 2: WebSocket Real-Time Updates
```
Connect to Solana validators for:
- Program subscription to Raydium V4 program
- Listen for PumpSwap pool creation events
- Get notified when PumpFun tokens migrate
- Real-time price tracking via vault balances
```

### Method 3: Token Metadata Analysis
```
Check token properties:
- Creator authority (PumpFun identifier)
- Token metadata (name/symbol/description)
- Social links (website/twitter/discord)
- Migration timestamp (when moved to PumpSwap)
- Original bonding curve program ID
```

## Implementation Strategy

### Phase 1: Enhance Existing Raydium Monitor
```python
# Add to RaydiumMonitor class:

def is_pumpswap_token(self, token_data: Dict) -> bool:
    """Check if token migrated from PumpFun to PumpSwap (Raydium V4)"""
    # Check metadata for PumpFun origin markers
    # Look for bonding curve history in token data
    # Verify against known PumpFun creator patterns

def get_pumpfun_origin_info(self, token_mint: str) -> Optional[Dict]:
    """Get PumpFun bonding curve history"""
    # Fetch metadata about original bonding curve
    # Get creator information
    # Track original launch time and progress

def track_pumpswap_pool(self, pool_address: str, token_mint: str):
    """Track PumpSwap pool for migrated token"""
    # Store association with original bonding curve
    # Monitor vault-based pricing (same as other Raydium V4)
    # Track migration timestamp
```

### Phase 2: WebSocket Monitoring
```python
async def listen_to_pumpswap_migrations():
    """Real-time monitoring of PumpFun→PumpSwap migrations"""
    # Subscribe to Raydium V4 program logs
    # Filter for PumpFun-origin tokens
    # Emit events when tokens migrate from PumpFun
    # Track pool creation and initial pricing
```

### Phase 3: Data Integration
```
Store in database:
- PumpFun token metadata
- Bonding curve state
- Creator information
- Migration history
- Raydium pool mapping
```

## Database Schema Changes

```sql
-- Add PumpSwap tracking to existing pools table
ALTER TABLE pools ADD COLUMN is_pumpswap BOOLEAN DEFAULT FALSE;
ALTER TABLE pools ADD COLUMN pumpfun_creator TEXT;
ALTER TABLE pools ADD COLUMN bonding_curve_address TEXT;
ALTER TABLE pools ADD COLUMN pumpfun_migration_timestamp TIMESTAMP;
ALTER TABLE pools ADD COLUMN pumpfun_launch_time TIMESTAMP;

-- Create PumpSwap-specific tracking table
CREATE TABLE pumpswap_tokens (
    id INTEGER PRIMARY KEY,
    mint TEXT UNIQUE NOT NULL,
    raydium_pool TEXT UNIQUE,
    name TEXT,
    symbol TEXT,
    creator TEXT,
    website TEXT,
    twitter TEXT,
    discord TEXT,
    image_uri TEXT,
    description TEXT,
    bonding_curve TEXT,
    bonding_curve_start TIMESTAMP,
    pumpfun_launch_price REAL,
    pumpfun_final_price REAL,
    pumpswap_pool_created TIMESTAMP,
    pumpswap_initial_price REAL,
    price_change_pct REAL,
    created_timestamp TIMESTAMP,
    updated_timestamp TIMESTAMP
);
```

## PumpFun Lifecycle → PumpSwap

### Phase 1: Bonding Curve (PumpFun)
- Tokens start on PumpFun bonding curve
- Price follows curve formula: increases as more buy
- Limited liquidity, only available on PumpFun

### Phase 2: Migration to PumpSwap (Raydium V4)
- Bonding curve completes or threshold reached
- Raydium V4 pool created automatically
- Token migrates to PumpSwap
- Price now determined by vault balances (SOL + Token)
- Full liquidity available for trading

### Our Focus: Phase 2 (PumpSwap)
```
We monitor Raydium V4 pools that receive PumpFun migrations:
- Detect when migration happens
- Store bonding curve metadata
- Calculate price from vault balances
- Track price change from bonding curve → PumpSwap
- Monitor for liquidity removal (same as other pools)
```

## Integration with Main App

### Updated Pool Detection Flow
```
1. Detect Raydium V4 pool creation (existing)
2. Check if it's a PumpSwap pool (metadata check)
3. If PumpSwap (PumpFun migration):
   - Look for bonding curve metadata
   - Store creator/PumpFun origin info
   - Mark as pumpswap token
   - Record migration timestamp
4. Track in pumpswap_tokens table
5. Monitor price via vaults (same as other Raydium V4 pools)
6. Compare bonding curve final price vs PumpSwap initial price
```

### UI Enhancements
```
For each pool, show:
- PumpSwap badge if migrated from PumpFun
- Creator name/social links
- Original bonding curve launch time
- Migration timestamp
- Bonding curve final price
- PumpSwap initial price
- Price change % (curve → swap)
- Days since PumpSwap migration
- Bonding curve address (for reference)
```

## Next Steps

1. **Test Phase 1**: Enhance RaydiumMonitor with PumpSwap detection
   - Add metadata analysis for PumpFun origin markers
   - Add is_pumpswap_token() method
   - Store PumpSwap metadata in database
   - Identify creator patterns from bonding curve

2. **Test Phase 2**: WebSocket monitoring
   - Set up Solana WebSocket for Raydium V4 events
   - Filter for PumpFun-origin migrations
   - Real-time detection when tokens move to PumpSwap

3. **Test Phase 3**: Integration into main.py
   - Add PumpSwap tracking to pool broadcast
   - Update UI with PumpSwap badges and info
   - Create PumpSwap-specific alerts
   - Track migration metrics

4. **Optional Phase 4**: Bonding curve history
   - Fetch bonding curve metadata
   - Store final curve prices
   - Compare curve → swap price changes
   - Track creator performance metrics

## Key Differences from Meteora

| Aspect | Meteora | PumpSwap (PumpFun) |
|--------|---------|---------|
| Entry Point | DLMM (multiple bins) | Bonding Curve (PumpFun) |
| Final Venue | Meteora DLMM | Raydium V4 (PumpSwap) |
| Price Source | Active bin (complex) | Vault ratio (simple) |
| Detection | Look for DLMM pool | Look for token migration from PumpFun |
| Monitoring | Parse active bin | Track vault balances (same as other Raydium V4) |
| Bonus Data | Limited metadata | Creator info, launch time, bonding curve history |

## Benefits of PumpSwap Approach

1. **Simpler pricing** - uses vault ratio (proven calculation, no complex bin parsing)
2. **Larger market** - all PumpFun tokens migrate to PumpSwap (Raydium V4)
3. **Better detection** - clear bonding curve origin marks, less ambiguous
4. **Real-time data** - WebSocket provides immediate migration notifications
5. **Reuses existing code** - 95% leverages our Raydium V4 monitoring
6. **Rich metadata** - bonding curve history, creator info, launch timing
7. **Measurable impact** - can track price changes from curve → swap migration

## Timeline

- **Week 1**: Phase 1 (RaydiumMonitor enhancement) - 2-3 hours
- **Week 2**: Phase 2 (WebSocket monitoring) - 4-5 hours
- **Week 3**: Phase 3 (Integration & testing) - 3-4 hours
- **Week 4**: Phase 4 (Optional bonding curve) - 2-3 hours
