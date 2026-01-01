# PumpSwap Token Monitor

Real-time monitoring system for PumpSwap token launches on Solana with live vault-based price calculation.

## Quick Start

### Install Dependencies

```bash
pip install requests flask solders
```

### Run the Application

```bash
python main.py
```

This starts:
- WebSocket listener for real-time PumpSwap pool creation events
- Flask web server on port 5002 with real-time UI
- Automatic price updates on sliding scale (30s-5min intervals)

Web UI: http://localhost:5002

## Features

- **Real-time Detection**: Detects PumpSwap pool creation events within 3-8 seconds
- **Live Price Calculation**: Extracts prices directly from blockchain vault balances (no external API dependence)
- **Automatic Updates**: Background thread continuously refreshes prices based on pool age
- **On-chain Metadata**: Fetches token name, symbol, and image from Metaplex directly
- **Liquidity Status**: Identifies active tokens vs. drained/low-liquidity pools
- **Database Persistence**: SQLite stores all pool data for historical tracking

## Testing

Run tests from the `tests/` folder:

```bash
# Phase 1: Core pool detection (21 tests)
python tests/test_pumpswap_detection.py

# Phase 2: Advanced features (14 tests)
python tests/test_pumpswap_phase2.py

# Real-time listener: Detects new launches and price updates
python tests/test_pumpswap_listener.py

# Vault-based price accuracy: Verifies live price calculation
python tests/test_vault_price_template.py
```

All tests should pass before making code changes.

## Price Lookup Scripts

### Get Current Price (with freshness status)

```bash
python utils/get_price_from_pools.py <TOKEN_MINT>
```

Output: `Updated 30s ago ✓ (fresh)` with price, liquidity, and status

### Advanced Lookup (with vault details)

```bash
python utils/get_price_live_with_balances.py <TOKEN_MINT>
```

Shows: Price, SOL balance, token balance, and drain status

## Architecture

### Single-file Application (`main.py`)

1. **PumpSwapDatabase** - SQLite persistence
   - `pools` table: Current token state
   - `pool_history` table: Historical snapshots

2. **TokenMonitor** - WebSocket listener
   - Monitors PumpSwap program only (pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA)
   - Extracts live prices from vault balances
   - Detects pool creation in real-time

3. **Metadata Fetcher** - On-chain lookups
   - Direct Metaplex PDA queries
   - Fallback to external APIs

4. **Flask Web App** - Real-time UI
   - Single-page application
   - Client-side polling every 1 second
   - 30-second backup refresh

### Data Flow

1. WebSocket detects pool creation (~3-8 seconds)
2. Metadata fetcher retrieves token info from blockchain
3. New pool added to broadcast queue
4. Client polls every 1 second for updates
5. Total latency: ~8-12 seconds from on-chain event

## Price Accuracy

Our vault-based price calculation provides **more current** prices than DexScreener:

- **Our System**: Live vault balances + current SOL market rate = real-time prices
- **DexScreener**: Live vault data + cached SOL rates = stale USD conversion

Price calculation: `Price (SOL) = SOL Balance / Token Balance`, then convert to USD using current SOL rate.

See [docs/VAULT_PRICE_FIX_SUMMARY.md](docs/VAULT_PRICE_FIX_SUMMARY.md) for detailed analysis.

## Development

See [CLAUDE.md](CLAUDE.md) for:
- Code structure and component descriptions
- Where to make changes
- Important constraints (PumpSwap-only monitoring)

See [docs/](docs/) for detailed documentation:
- `VAULT_PRICE_FIX_SUMMARY.md` - Price calculation verification and accuracy proof
- `PUMPSWAP_PRICE_FETCHER_README.md` - Historical development notes

## Key Constraints

**DO NOT:**
- Add monitoring for Raydium V4, CPMM, Meteora, or other DEX types
- Modify price extraction without tests
- Change WebSocket subscription from PumpSwap program
- Update UI without verifying backend tests pass

## Project Structure

```
.
├── main.py                              # Main application
├── tests/                               # Test suite
│   ├── test_pumpswap_detection.py      # Phase 1 tests (21)
│   ├── test_pumpswap_phase2.py         # Phase 2 tests (14)
│   ├── test_pumpswap_listener.py       # Real-time listener
│   └── test_vault_price_template.py    # Price accuracy
├── utils/                               # Utility scripts
│   ├── get_price_from_pools.py         # Price lookup with freshness
│   ├── get_price_live_with_balances.py # Advanced price lookup
│   └── get_pool_price.py               # Pool price helper
├── docs/                                # Documentation
│   ├── VAULT_PRICE_FIX_SUMMARY.md      # Price calculation proof
│   └── PUMPSWAP_PRICE_FETCHER_README.md # Development notes
├── pumpswap_tokens.db                  # SQLite database
├── CLAUDE.md                            # Development guidelines
└── README.md                            # This file
```

## Status

✓ Production ready
✓ Real-time detection working
✓ Live vault-based prices verified
✓ All tests passing (35/35)
✓ Automatic price updates active
