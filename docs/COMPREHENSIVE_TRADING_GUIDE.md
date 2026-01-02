# Trading Guide - Buy & Sell Tokens

Complete guide for using the automated token trading system with BONK, BlackWhale, and other Solana tokens.

## Table of Contents

1. [Setup](#setup)
2. [Quick Start](#quick-start)
3. [Trading Commands](#trading-commands)
4. [How It Works](#how-it-works)
5. [Complex Tokens (BONK)](#complex-tokens-bonk)
6. [Troubleshooting](#troubleshooting)
7. [Advanced Usage](#advanced-usage)

## Setup

### 1. Configure Environment

```bash
# Copy the example configuration
cp .env.example .env

# Edit with your credentials
nano .env
```

Your `.env` should contain:

```env
# Solana RPC Endpoint
HELIUS_API_KEY=your_helius_api_key_here

# Your trading wallet (private key as JSON array)
TRADING_KEYPAIR=[188, 77, 162, 197, ...]

# Jupiter API for routing
JUPITER_API_KEY=your_jupiter_api_key_here
```

### 2. Get API Keys

- **Helius API**: https://www.helius.dev (for RPC access)
- **Jupiter API**: https://jup.ag/api (for token routing)

### 3. Verify Setup

```bash
# Check .env file exists
ls -la .env

# Should output: .env (file exists)
```

## Quick Start

### Buy Tokens

**Buy 0.001 SOL of BONK:**
```bash
python3 buy_token.py DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263
```

**Buy 0.001 SOL of BlackWhale:**
```bash
python3 buy_token.py 8y45AJzCUBSZL1UDFQRzCKovQBLQFudBrpPeg5yNpump
```

**Buy any token (by mint address):**
```bash
python3 buy_token.py <TOKEN_MINT>
```

### Sell Tokens

**Sell 500 million BONK:**
```bash
python3 sell_token.py DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263 500000000
```

**Sell 50 million BlackWhale:**
```bash
python3 sell_token.py 8y45AJzCUBSZL1UDFQRzCKovQBLQFudBrpPeg5yNpump 50000000
```

**Sell custom amount:**
```bash
python3 sell_token.py <TOKEN_MINT> <AMOUNT>
```

## Trading Commands

### buy_token.py

**Usage:**
```bash
python3 buy_token.py <TOKEN_MINT>
```

**What it does:**
1. Loads credentials from `.env`
2. Gets current price quote from Jupiter
3. Builds swap transaction (optimized if complex token)
4. Signs transaction with your keypair
5. Submits to Solana mainnet
6. Returns transaction signature and token amount

**Default amounts:**
- Input: 0.001 SOL
- Slippage: 5%
- Gas tip: 50,000 lamports

**Output:**
```
Status: confirmed
Signature: 2ZGVooZF9SS2pjfmJjPtQbfXj7YU9gvuinEKTJngmgurvt7351AFSPNPje8pwWLcmFMofr3ae1XckDzkXE7JGBn4
Output: 1,467,168,138 tokens
```

### sell_token.py

**Usage:**
```bash
python3 sell_token.py <TOKEN_MINT> <AMOUNT>
```

**Arguments:**
- `TOKEN_MINT`: 43-44 character base58 address
- `AMOUNT`: Number of tokens in base units (integers only)

**Example - Sell 1 billion BONK:**
```bash
python3 sell_token.py DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263 1000000000
```

**Output:**
```
Status: confirmed
Signature: 5aZraSqAaWREkQuQeZDDXy9u31akpmrMnRJxusVrcW1yudNjoAnGW39hjk9st4PmA3mMo3gG7wSRYf7xvb5TmweB
Output: 0.000585 SOL (584,699 lamports)
```

## How It Works

### Step-by-Step Execution

1. **Load Configuration**
   - Reads API keys from `.env` file
   - Loads your wallet keypair

2. **Get Price Quote**
   - Queries Jupiter API for current token price
   - Shows estimated output amount
   - Calculates price impact

3. **Build Transaction**
   - Consolidates Jupiter instructions
   - Adds compute budget optimizations
   - Creates MessageV0 with Address Lookup Tables

4. **Sign & Submit**
   - Signs transaction with your keypair
   - Submits to Solana RPC (Helius)
   - Falls back to direct RPC if Jito unavailable

5. **Confirm & Log**
   - Waits for blockchain confirmation
   - Records trade in `test_trades.json`
   - Provides Solscan link for verification

## Complex Tokens (BONK)

### The Problem

Some tokens like BONK require complex routing through multiple DEX aggregators. This creates large transactions that exceed Solana's RPC limit of **1644 bytes**.

**Without optimization:**
- BONK transactions: 1710 bytes ❌ (too large)
- Error: `base64 encoded too large`

**With optimization:**
- BONK transactions: 858 bytes ✅ (within limit)

### How We Fix It

The system automatically detects BONK and uses **direct routes only** instead of complex aggregated routes.

### Auto-Detection

BONK is automatically detected and optimized. You'll see:

```
⚠️  Using legacy transaction format (complex token)
[TRADER] Using direct route strategy (smaller serialized size)
```

## Troubleshooting

### Error: "HELIUS_API_KEY not set"

**Cause:** `.env` file not found or not loaded

**Solution:**
```bash
# Verify .env exists
ls -la .env

# Check contents
cat .env | grep HELIUS_API_KEY
```

### Error: "Invalid mint address"

**Cause:** Token mint is wrong length or format

**Solution:**
- Solana mints are exactly 43-44 characters
- Use base58 encoding
- Example: `DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263`

### Transaction Confirmed But No Tokens Received

**Cause:** Insufficient liquidity or slippage exceeded

**Solutions:**
1. **Check amount** - Use at least 100M tokens for BONK
2. **Check price impact** - Quote shows impact percentage
3. **Verify on Solscan** - Check the transaction link

### Jito Endpoint Unavailable (404 Error)

**Not a problem** - System automatically falls back to direct RPC

```
Jito send failed: 404 Client Error
[TRADER] Jito failed, falling back to direct RPC send...
[TRADER] Transaction submitted via RPC: ...
```

This is normal and doesn't affect transaction success.

## Token Reference

### Tested & Working

| Token | Mint | Type | Buy | Sell | Notes |
|-------|------|------|-----|------|-------|
| BONK | `DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263` | Complex | ✅ | ✅ | Auto-optimized |
| BlackWhale | `8y45AJzCUBSZL1UDFQRzCKovQBLQFudBrpPeg5yNpump` | PumpFun | ✅ | ✅ | Standard routing |

### Recommended Trade Amounts

| Operation | Min Amount | Recommended |
|-----------|-----------|-------------|
| Buy | 0.001 SOL | 0.01-0.1 SOL |
| Sell (BONK) | 100M tokens | 500M-1B tokens |
| Sell (Other) | 10M tokens | 50M+ tokens |

## Security

### Protecting Your Keys

1. **Never share `.env` file**
2. **Never commit `.env` to git** (it's in `.gitignore`)
3. **Keep `.env` backups offline**
4. **Use separate wallet for testing**

### Transaction Verification

Always verify trades on Solscan:
```
https://solscan.io/tx/{signature}
```

## For More Information

- [ENV_SETUP.md](ENV_SETUP.md) - Environment configuration details
- [TRADING_QUICK_START.md](TRADING_QUICK_START.md) - Quick reference
