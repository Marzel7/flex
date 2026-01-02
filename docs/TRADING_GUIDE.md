# Trading System Guide

## Overview
Complete token trading system with buy/sell functionality on Solana using Jupiter DEX aggregator.

## Prerequisites
- Python 3.9+
- Environment variables set:
  - `HELIUS_API_KEY` - Solana RPC endpoint
  - `TRADING_KEYPAIR` - JSON array format keypair
  - `JUPITER_API_KEY` - Jupiter DEX API key

## Quick Start

### Buy Tokens
```bash
# Buy with default 0.001 SOL
bash test buy_token.py <TOKEN_MINT>

# Example: Buy test token
bash test buy_token.py 8y45AJzCUBSZL1UDFQRzCKovQBLQFudBrpPeg5yNpump
```

**Output:**
- Quote from Jupiter DEX
- Transaction signature
- Number of tokens received
- Recorded in `test_trades.json`

### Sell Tokens
```bash
# Sell specified amount of tokens
bash test sell_token.py <TOKEN_MINT> <AMOUNT>

# Example: Sell 10M tokens
bash test sell_token.py 8y45AJzCUBSZL1UDFQRzCKovQBLQFudBrpPeg5yNpump 10000000
```

**Output:**
- Quote in SOL from Jupiter DEX
- Transaction signature
- SOL amount received (and lamports)
- Recorded in `test_trades.json`

## Features

### ✅ Working
- Real Jupiter DEX quotes
- Transaction building & signing
- Jito MEV protection (with RPC fallback)
- Persistent transaction logging
- Slippage protection (5% default)
- Pretty-printed transaction status

### Configuration
- **Slippage**: Default 5% (500 bps), configurable per trade
- **Tip Amount**: 50,000 lamports (~$0.006), configurable
- **Amount**: Buy = 0.001 SOL, Sell = custom amount
- **RPC**: Helius mainnet endpoint

## Trading History

All trades are logged in `test_trades.json`:

**Buy Record:**
```json
{
  "timestamp": "2026-01-02T15:33:21.742502",
  "type": "buy",
  "symbol": "TEST3",
  "token_mint": "8y45AJzCUBSZL1UDFQRzCKovQBLQFudBrpPeg5yNpump",
  "input_amount_sol": 0.001,
  "output_amount": 80596692,
  "signature": "5BmrSyzV88zV9dJQiKrUxRLaHvU61ZvPgCMUbTGszP767YmyReJ6LBc7pxgt7GibTQ2W5DV2RNWh9qQv2oEB6rf2",
  "status": "confirmed",
  "error": null
}
```

**Sell Record:**
```json
{
  "timestamp": "2026-01-02T15:36:50.774275",
  "type": "sell",
  "symbol": "TESTTOKEN",
  "token_mint": "8y45AJzCUBSZL1UDFQRzCKovQBLQFudBrpPeg5yNpump",
  "input_amount_tokens": 10000000,
  "output_amount_sol": 0.000123047,
  "output_amount_lamports": 123047,
  "signature": "4ShTHAtSUr7y5GtGTNRrh1BKH8vKTLxa7SNbz2hyimgiNGeJNweZHmVZLwQfD3SgNCXuqNsdt1av6CwLXiRoVzj4",
  "status": "confirmed",
  "error": null
}
```

## Checking Transaction Status
- View on Solscan: `https://solscan.io/tx/{SIGNATURE}`
- View token: `https://solscan.io/token/{TOKEN_MINT}`

## Wallet Balance
```bash
bash test check_balance.py
```

## API Information

### Jupiter DEX API
- **Endpoint**: `https://api.jup.ag/swap/v1`
- **Authentication**: `x-api-key` header
- **Rate Limit**: Free tier 60 RPS
- **Supports**: Real-time quotes and swap instruction building

### Helius RPC
- **Endpoint**: `https://mainnet.helius-rpc.com/?api-key={KEY}`
- **Features**: Fast, reliable Solana RPC endpoint
- **Used for**: Blockhash fetching, transaction submission

### Jito Bundle Endpoint
- **Endpoint**: `https://mainnet.block-engine.jito.wtf/api/v1`
- **Feature**: MEV-protected fast execution
- **Fallback**: Direct RPC submission if unavailable

## Troubleshooting

### "TOKEN_NOT_TRADABLE" Error
Some older tokens may be restricted. The system automatically uses fallback quotes.
Try with newer tokens (especially Pump.fun tokens).

### Jito 404 Error
Jito block engine is temporarily unavailable. System automatically falls back to direct RPC.

### Transaction Confirmation
- Transactions may take 10-60 seconds to confirm
- Check Solscan for transaction status
- Signature is recorded immediately, even if still pending

## Implementation Files

- **trading_executor.py** - Core trading logic
  - `JupiterClient` - Jupiter API integration
  - `JitoClient` - MEV-protected execution
  - `TokenTrader` - Main buy/sell interface
  
- **buy_token.py** - Buy command-line tool
- **sell_token.py** - Sell command-line tool  
- **check_balance.py** - Wallet balance checker
- **test** - Environment variable wrapper
- **test_trades.json** - Transaction log

## Performance Metrics

**Typical Trade Flow:**
1. Quote retrieval: ~1-2 seconds
2. Instruction building: ~1 second
3. Transaction building: ~1 second
4. RPC submission: <500ms
5. Confirmation: 10-60 seconds
- **Total**: ~12-65 seconds end-to-end

**Recent Test Results:**
- Buy: 0.001 SOL → ~80M tokens (consistent)
- Sell: 10M tokens → ~0.0001 SOL (1/3 liquidity)
- Success rate: 100% (pending Jito availability)

## Future Improvements

Potential enhancements:
- [ ] Portfolio tracking across multiple tokens
- [ ] Limit order support
- [ ] Stop-loss protection
- [ ] Historical price tracking
- [ ] Advanced analytics dashboard
- [ ] Automated trading bots
