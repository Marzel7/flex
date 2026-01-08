# Pump.Fun Bonding Curve Listener

Real-time bonding curve completion detector with automatic pre-migration rug risk analysis.

## Overview

This listener monitors the Pump.Fun bonding curve program via Helius WebSocket RPC and:

1. **Detects bonding activity** in real-time (~3-8 seconds after on-chain)
2. **Filters by market cap** - Only analyzes tokens >= $50,000 USD
3. **Polls for completion** - Checks bonding curve PDA every 10 seconds
4. **Analyzes risk** - Runs pre-migration analyzer when curve completes
5. **Provides rug scores** - Returns detailed risk assessment with classification

## Quick Start

```bash
python3 pumpfun_curve_listener.py
```

Expected output:
```
[LISTENER] Starting pump.fun curve completion listener...
[WS] ✅ Subscribed to bonding curve program logs
[EVENT] 📍 Detected bonding activity for 8xNvpTr3Q9...
[MARKET_CAP] Fetching market cap for 8xNvpTr3Q9...
[FILTER] ✅ Market cap $125,000 >= $50,000 - PROCEEDING
[EVENT] ⏳ Still waiting for curve completion (60s elapsed)...
[EVENT] 🔴 Bonding curve COMPLETE: 8xNvpTr3Q9...
[ANALYZER] 🔍 Analyzing 8xNvpTr3Q9...
[ANALYZER] 🟢 Low | Score: 12.5% | 8xNvpTr3Q9...
```

## Configuration

### Market Cap Threshold

Set in `pumpfun_curve_listener.py`:
```python
MARKET_CAP_THRESHOLD_USD = 50000  # Only analyze tokens >= $50k USD
```

### RPC Endpoints

Automatically uses Helius API key from `.env`:
```
HELIUS_API_KEY=your_api_key_here
```

Falls back to Solana mainnet if key not provided.

## How It Works

### 1. WebSocket Subscription

Subscribes to Pump.Fun program logs via Helius WebSocket:
- Program ID: `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`
- Commitment: `confirmed`
- Filters: `mentions` the Pump.Fun program

### 2. Mint Extraction

Parses log lines for `Mint:` token to extract token addresses:
```
Mint: 8xNvpTr3Q9d5FYqGd8xA7zK5q3vW2c8P1n9...
```

### 3. Market Cap Filtering

Fetches market cap from DexScreener API:
```
GET https://api.dexscreener.com/latest/dex/tokens/{mint}
```

Only proceeds if `marketCap >= $50,000 USD`

### 4. Curve Completion Polling

Checks bonding curve PDA every 10 seconds:
- PDA: `find_program_address([b"bonding_curve", mint_bytes], PUMPFUN_PROGRAM_ID)`
- Checks: `sold_tokens >= max_supply` from account data

Max polling time: 20 minutes (120 * 10 second intervals)

### 5. Pre-Migration Analysis

When curve completes, runs `PumpFunPreMigrationAnalyzer`:

**Pre-Migration Metrics (6 factors):**
- Bonding curve activity patterns
- Transaction velocity
- Whale concentration
- Creator behavior
- Wash trading indicators
- Liquidity distribution

**AMM Gap Window Metrics (5 factors):**
- Wallet concentration: 30% weight
- Exit asymmetry: 25% weight
- Wash trading: 20% weight
- Creator activity: 15% weight
- Liquidity: 10% weight

**Output:**
```
Risk Level: 🟢 Low / 🟡 Medium / 🔴 High / ☠️ Critical
Rug Probability: 0.0% - 100.0%
```

## API Integrations

### DexScreener (Market Cap)
```
GET https://api.dexscreener.com/latest/dex/tokens/{mint}
Response: { pairs: [{ marketCap: number }] }
Timeout: 5 seconds
```

### Solana RPC (Curve PDA)
```
GET_ACCOUNT_INFO(curve_pda, encoding=base64)
Struct format: <Q (unsigned 64-bit) at offsets 8 (sold) and 16 (max_supply)
```

## Data Structures

### Listener State
```python
self.seen_mints: Set[str]           # All mints ever detected
self.filtered_mints: Set[str]       # Mints passing market cap filter
self.completed_curves: Dict[str, float]  # {mint: completion_timestamp}
self.analyzed_tokens: Dict[str, Dict]    # {mint: analysis_result}
```

### Analysis Result
```python
{
    "amm_risk_level": "🟢 Low",
    "amm_rug_probability": 0.125,
    # ... additional metrics
}
```

## Status Output

During execution, the listener prints:

| Prefix | Meaning |
|--------|---------|
| `[LISTENER]` | Core listener operations |
| `[WS]` | WebSocket connection status |
| `[EVENT]` | Bonding activity detected or curve completed |
| `[MARKET_CAP]` | Market cap fetch attempt |
| `[FILTER]` | Market cap filtering result |
| `[ANALYZER]` | Pre-migration analysis result |

## Error Handling

**API Timeouts**: Gracefully fall back to 0 market cap (filtered out)
**Missing PDAs**: Returns `False` for curve incomplete
**Invalid Mints**: Skipped silently with no analysis
**WebSocket Errors**: Reconnect with 1-second delay

## Performance

- **Memory**: ~50 MB (token caching)
- **CPU**: Minimal (async I/O)
- **Network**: 1-2 API calls per detected token + continuous WebSocket
- **Latency**: ~8-12 seconds (detection to analysis)

## Monitoring

### Summary Statistics
```
[LISTENER] Summary:
  Total detected: 42
  Passed market cap filter: 8
  Curves completed: 3
  Curves analyzed: 3
```

### File: `test_curve_listener_validation.py`
Quick validation without full WebSocket listener:
```bash
python3 test_curve_listener_validation.py
```

Tests:
- Market cap API integration
- DexScreener parsing
- Listener initialization
- Configuration validation

## Stopping the Listener

Press `Ctrl+C` to stop gracefully. Outputs final summary with statistics.

## Future Enhancements

- [ ] Persistent database storage of analyzed tokens
- [ ] Discord/webhook alerts for high-risk tokens
- [ ] Custom market cap threshold per token type
- [ ] Risk score trending over time
- [ ] Whale wallet tracking across curve transitions
