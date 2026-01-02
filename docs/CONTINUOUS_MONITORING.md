# Continuous PumpSwap Listener - Real-Time Monitoring

## Overview

The standalone PumpSwap listener now runs in continuous mode, monitoring for new token launches and updating prices every 60 seconds.

## Features

### Real-Time New Launch Detection
- Checks database every second for new tokens
- Automatically detects launches with 🆕 indicator
- Tracks cumulative token count

### Automatic Price Refresh
- Updates all token prices every 60 seconds
- No need to restart the listener
- Continuous market monitoring

### Live Price Table
Each refresh cycle displays:
- **Symbol**: Token name
- **Price (USD)**: Current vault-based price
- **SOL Balance**: Liquidity in pool
- **Market Cap**: Current vault balance × price
- **FDV**: Fully diluted valuation
- **Match**: Accuracy ratio vs DexScreener
- **Token Address**: Full mint address

## Usage

### Start the Listener
```bash
python tests/test_pumpswap_listener.py
```

Output:
```
[LISTENER] Starting continuous PumpSwap listener (independent from main.py)
[LISTENER] Monitoring for new launches and updating prices every 60 seconds
```

### Stop the Listener
Press `Ctrl+C` to gracefully shut down.

## How It Works

### Initialization
1. Loads all PumpSwap tokens from SQLite database
2. Tracks initial tokens by mint address
3. Starts continuous monitoring loop

### Each Second
- Checks database for any new tokens
- Detects if new launches occurred
- Announces new launches with count

### Every 60 Seconds
1. Fetches current prices from blockchain vaults
2. Calculates market cap and FDV
3. Displays live summary table
4. Shows Match ratios vs DexScreener

### New Launch Detection
When new tokens are detected:
```
[NEW LAUNCHES] Detected 2 new token(s):

🆕 NEW  TokenA      ...
   → $0.00045678 | 125.50 SOL | ✓ ACTIVE

🆕 NEW  TokenB      ...
   → $0.00012345 | 98.75 SOL | ✓ ACTIVE
```

## Table Output

Example refresh output:
```
[UPDATE] Refreshing prices at 12:34:56
====================================================================================================
Symbol          Price (USD)          SOL Balance     Market Cap           FDV                  Match        Token Address
====================================================================================================
DjxJzWa4        $0.00027298          226.71 SOL      $28.34K              $272.97K             ✓ 0.99x      DjxJzWa4hSVJLmcmmQkcKJU6iEXLK5...
FILECOin        $0.00054988          676.45 SOL      $84.56K              $549.88K             ✓ 1.00x      55P9NF8mgHWaykebCt2kFdmZvcscVx...
====================================================================================================

[RESULT] ✓ Fetched 10/10 prices | 2 active | 8 low/drained
```

## Match Indicators

The Match column shows how closely our on-chain prices compare to DexScreener:

| Indicator | Range | Meaning |
|-----------|-------|---------|
| ✓ | 0.95-1.05x | Excellent match (within 5%) |
| ~ | 0.90-1.10x | Good match (within 10%) |
| ⚠ | Other | Warning - significant deviation |
| N/A | — | No DexScreener price available |

## Key Metrics

### Price Sources
- **On-chain calculation**: SOL Balance / Token Balance × $125
- **No external APIs**: Pure vault-based pricing
- **Real-time**: Updated every 60 seconds

### Liquidity Status
- **Active (✓)**: SOL balance >= 1 (tradeable)
- **Low Liquidity (⚠)**: SOL balance < 1 (low activity)
- **Drained**: No balances (pool inactive)

### Market Metrics
- **Market Cap**: Price × Current vault token balance
- **FDV**: Price × Total supply from blockchain
- **SOL Balance**: Current liquidity in vault

## Configuration

### Refresh Interval
Default is 60 seconds. To change:
```python
refresh_interval = 60  # Change this value in run_listener()
```

### Database Path
Automatically uses: `pumpswap_tokens.db` in project root

## Error Handling

### No Tokens Found
```
[ERROR] No tokens found in database
```
Solution: Ensure `pumpswap_tokens.db` exists and contains PumpSwap tokens with signatures.

### Database Connection Error
```
[ERROR] Could not load from database: [error details]
```
Solution: Check file permissions and database integrity.

### Price Fetch Failures
Tokens with missing or invalid signatures are skipped automatically.

## Performance

- **Startup Time**: < 1 second
- **Price Fetch**: ~5-10 seconds per token (parallel RPC calls recommended)
- **Memory**: ~20-50 MB (depends on token count)
- **Network**: Light (1-2 RPC calls per token per refresh)

## Integration

The listener is **completely independent** from `main.py`:
- Uses only SQLite database as data source
- Makes direct RPC calls to blockchain
- No Flask server required
- Can run alongside main.py without conflicts

## Advanced Usage

### Debug Mode
To enable debug output for specific tokens:
```python
debug_enabled = symbol == 'DjxJzWa4'  # Change in run_listener()
```

### Custom Token List
Instead of loading from database, you can hardcode tokens:
```python
test_tokens = [
    {'base_mint': '...', 'symbol': 'TOKEN1', 'signature': '...', ...},
    {'base_mint': '...', 'symbol': 'TOKEN2', 'signature': '...', ...},
]
```

## Examples

### Monitor Specific Tokens
Modify the SQL query in `load_tokens_from_db()`:
```python
cursor.execute('''
    SELECT ... FROM pools
    WHERE is_pumpswap = 1 AND symbol IN ('TOKEN1', 'TOKEN2')
''')
```

### Change Refresh Frequency
Faster updates (every 30 seconds):
```python
refresh_interval = 30
```

Slower updates (every 5 minutes):
```python
refresh_interval = 300
```

## Troubleshooting

### Listener stops unexpectedly
Check for RPC API rate limits or network issues.

### Prices not updating
Verify Helius API key is set: `export HELIUS_API_KEY="..."`

### Match ratio shows 0.00x
Token may not have a DexScreener price - this is normal for new/low-volume tokens.

## Future Enhancements

Potential features to add:
- [ ] Alert on price movements (% change threshold)
- [ ] Export to CSV for analysis
- [ ] WebSocket updates for real-time display
- [ ] Trade history logging
- [ ] Liquidity drain detection
- [ ] Multi-pair comparison
