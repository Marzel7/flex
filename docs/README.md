# PumpSwap Token Monitor & Trading

Real-time monitoring and trading system for PumpSwap token launches on Solana with:
- Live vault-based price calculation
- Automated token trading (buy/sell)
- Complex token handling (BONK, etc.)

## Quick Start

### Install Dependencies

```bash
pip install requests flask solders
```

### Configure Environment

```bash
cp .env.example .env
# Edit .env with your API keys and trading keypair
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

### Trade Tokens

```bash
# Buy BONK (0.001 SOL)
python3 utils/buy_token.py DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263

# Sell BONK (500 million tokens)
python3 utils/sell_token.py DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263 500000000

# Buy any token by mint address
python3 utils/buy_token.py 8y45AJzCUBSZL1UDFQRzCKovQBLQFudBrpPeg5yNpump
```

See [docs/TRADING_GUIDE.md](docs/TRADING_GUIDE.md) for detailed trading documentation.

## Features

### Monitoring
- **Real-time Detection**: Detects PumpSwap pool creation events within 3-8 seconds
- **Live Price Calculation**: Extracts prices directly from blockchain vault balances (no external API dependence)
- **Automatic Updates**: Background thread continuously refreshes prices based on pool age
- **On-chain Metadata**: Fetches token name, symbol, and image from Metaplex directly
- **Liquidity Status**: Identifies active tokens vs. drained/low-liquidity pools
- **Database Persistence**: SQLite stores all pool data for historical tracking

### Trading
- **Buy/Sell Tokens**: Execute trades directly from command line
- **Complex Token Support**: Automatic optimization for tokens like BONK (direct routes, size reduction)
- **Jupiter Integration**: Best-price routing through Jupiter aggregator
- **Slippage Protection**: Configurable slippage tolerance (default 5%)
- **Environment-based Configuration**: Secure `.env` file for API keys and keypairs
- **Audit Trail**: All trades logged with signatures and timestamps

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
├── main.py                              # Main monitoring application
├── trading_executor.py                  # Trading core (TokenTrader, JupiterClient)
│
├── tests/                               # Test suite
│   ├── test_pumpswap_detection.py      # Phase 1 tests (21)
│   ├── test_pumpswap_phase2.py         # Phase 2 tests (14)
│   ├── test_pumpswap_listener.py       # Real-time listener
│   ├── test_vault_price_template.py    # Price accuracy
│   ├── test_trading_executor.py        # Trading tests
│   └── test_buy_only.py                # Quick buy test
│
├── utils/                               # Utility and trading scripts
│   ├── load_env.py                     # Environment configuration loader
│   ├── buy_token.py                    # Buy tokens via Jupiter
│   ├── sell_token.py                   # Sell tokens via Jupiter
│   ├── get_price_from_pools.py         # Price lookup with freshness
│   ├── get_price_live_with_balances.py # Advanced price lookup
│   ├── get_pool_price.py               # Pool price helper
│   ├── check_balance.py                # Check wallet SOL balance
│   ├── add_migration_token.py          # Add detected migration tokens
│   ├── convert_base58_keypair.py       # Convert keypair formats
│   └── verify_helius_setup.py          # Verify Helius API setup
│
├── docs/                                # Detailed documentation
│   ├── TRADING_GUIDE.md                # Trading setup and commands
│   ├── ENV_SETUP.md                    # Environment configuration
│   ├── VAULT_PRICE_FIX_SUMMARY.md      # Price calculation proof
│   └── [other reference docs]          # Additional documentation
│
├── .env                                 # Configuration (secrets, gitignored)
├── .env.example                         # Configuration template
├── pumpswap_tokens.db                  # SQLite database (gitignored)
│
├── CLAUDE.md                            # Development guidelines
├── README.md                            # This file
└── .gitignore                           # Git ignore rules
```

## Status

✓ Production ready
✓ Real-time detection working
✓ Live vault-based prices verified
✓ All tests passing (35/35)
✓ Automatic price updates active
