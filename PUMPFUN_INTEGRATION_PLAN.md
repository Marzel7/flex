# PumpFun Integration Plan

## Overview
PumpFun is a token launchpad on Solana where creators launch tokens. All PumpFun tokens eventually trade on Raydium V4, so we can integrate PumpFun detection into our existing Raydium monitoring.

## Key Architecture

### PumpFun Program
- **Program ID**: `6EF8rQNi3ECMFzvZR2Yy5z2kh2eCMJQEt1g5TQPVe1fq`
- Uses bonding curves for initial token sales
- Creates Raydium V4 pools when bonding curve completes

### Integration Approach
Instead of relying on PumpFun's API (which is unstable), we use:
1. **Raydium V4 Program Monitoring** - already implemented
2. **On-chain metadata filtering** - identify PumpFun tokens
3. **Token metadata analysis** - look for PumpFun creator marks

## Detection Methods

### Method 1: Raydium V4 Event Monitoring (PRIMARY)
```
- Monitor Raydium V4 pool creation events
- Check token metadata for PumpFun markers
- Tokens with bonding curve → PumpFun launch
- Prices calculable from vault balances once on Raydium
```

### Method 2: WebSocket Real-Time Updates
```
Connect to Solana validators for:
- Program subscription to PumpFun program
- Listen for token launch events
- Monitor bonding curve state changes
- Get notified of Raydium migration
```

### Method 3: Token Metadata Analysis
```
Check token properties:
- Creator authority (PumpFun key)
- Bonding curve state
- Raydium pool association
- Creator website/social links
```

## Implementation Strategy

### Phase 1: Enhance Existing Raydium Monitor
```python
# Add to RaydiumMonitor class:

def is_pumpfun_token(self, token_data: Dict) -> bool:
    """Check if token is from PumpFun"""
    # Check for PumpFun markers in metadata
    # Look for bonding curve association
    # Verify against known PumpFun indicators

def track_bonding_curve_phase(self, token_mint: str) -> Optional[Dict]:
    """Monitor token during bonding curve phase"""
    # Price still tied to bonding curve formula
    # Not yet on Raydium
    # Get price from bonding curve math

def detect_raydium_migration(self, token_mint: str) -> Optional[str]:
    """Detect when bonding curve completes, pool created"""
    # Monitor for associated Raydium pool
    # Get pool address when migration happens
    # Switch from bonding curve → vault-based pricing
```

### Phase 2: WebSocket Monitoring
```python
async def listen_to_pumpfun_launches():
    """Real-time monitoring via WebSocket"""
    # Subscribe to program logs
    # Filter for PumpFun token launches
    # Emit events for new tokens
    # Track migration to Raydium
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
-- Add PumpFun tracking
ALTER TABLE pools ADD COLUMN is_pumpfun BOOLEAN DEFAULT FALSE;
ALTER TABLE pools ADD COLUMN bonding_curve TEXT;
ALTER TABLE pools ADD COLUMN bonding_curve_progress REAL;
ALTER TABLE pools ADD COLUMN pumpfun_creator TEXT;
ALTER TABLE pools ADD COLUMN raydium_migration_timestamp TIMESTAMP;

-- Create PumpFun-specific table
CREATE TABLE pumpfun_tokens (
    id INTEGER PRIMARY KEY,
    mint TEXT UNIQUE NOT NULL,
    name TEXT,
    symbol TEXT,
    creator TEXT,
    website TEXT,
    twitter TEXT,
    discord TEXT,
    image_uri TEXT,
    bonding_curve TEXT,
    bonding_curve_progress REAL,
    bonding_curve_start TIMESTAMP,
    raydium_pool TEXT,
    raydium_migrate_timestamp TIMESTAMP,
    created_timestamp TIMESTAMP,
    updated_timestamp TIMESTAMP
);
```

## Bonding Curve Pricing

When a token is still in bonding curve phase:
```
Price = (tokens_sold * BASE_PRICE) / (10^decimals)

The bonding curve determines the relationship between:
- Cost to buy token
- Tokens available
- Current progress through curve

Once enough volume is reached → Raydium pool created
```

## Integration with Main App

### Updated Pool Detection Flow
```
1. Detect Raydium V4 pool creation
2. Check if it's from PumpFun (metadata check)
3. If PumpFun:
   - Look for bonding curve state
   - Store creator info
   - Mark as PumpFun token
4. Track in pumpfun_tokens table
5. Monitor price via vaults (like other Raydium pools)
```

### UI Enhancements
```
For each pool, show:
- PumpFun badge if applicable
- Creator name/social links
- Bonding curve progress (if applicable)
- Migration status
- Days since launch
- Creator's other tokens
```

## Next Steps

1. **Test Phase 1**: Enhance RaydiumMonitor with PumpFun detection
   - Add metadata analysis
   - Add is_pumpfun_token() method
   - Store PumpFun metadata in database

2. **Test Phase 2**: WebSocket monitoring
   - Set up Solana WebSocket for program events
   - Filter for PumpFun program activities
   - Real-time token launch detection

3. **Test Phase 3**: Integration into main.py
   - Add PumpFun tracking to pool broadcast
   - Update UI with PumpFun info
   - Create PumpFun-specific alerts

4. **Optional Phase 4**: Bonding curve pricing
   - Fetch bonding curve state
   - Calculate prices during curve phase
   - Track curve progression

## Key Differences from Meteora

| Aspect | Meteora | PumpFun |
|--------|---------|---------|
| Entry Point | DLMM (multiple bins) | Bonding Curve |
| Final Venue | Meteora DLMM | Raydium V4 |
| Price Source | Active bin (complex) | Vault ratio (simple) |
| Detection | Look for DLMM pool | Look for token migration |
| Monitoring | Parse active bin | Track vault balances |

## Benefits of PumpFun Approach

1. **Simpler pricing** - once on Raydium, uses vault ratio (proven calculation)
2. **Larger market** - all PumpFun tokens end up on Raydium V4
3. **Better detection** - clear creator marks, less ambiguous
4. **Real-time data** - WebSocket provides immediate notifications
5. **Reuses existing code** - leverages our Raydium monitoring

## Timeline

- **Week 1**: Phase 1 (RaydiumMonitor enhancement) - 2-3 hours
- **Week 2**: Phase 2 (WebSocket monitoring) - 4-5 hours
- **Week 3**: Phase 3 (Integration & testing) - 3-4 hours
- **Week 4**: Phase 4 (Optional bonding curve) - 2-3 hours
