# Quick Start - Trading Commands

## Setup (One-Time)

```bash
# Copy example configuration
cp .env.example .env

# Edit .env with your credentials
nano .env
```

## Trading Commands

### Buy BONK
```bash
python3 buy_token.py DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263
```

### Sell BONK
```bash
python3 sell_token.py DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263 <amount>
```

Example: Sell 500 million BONK tokens
```bash
python3 sell_token.py DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263 500000000
```

### Buy BlackWhale (PumpFun)
```bash
python3 buy_token.py 8y45AJzCUBSZL1UDFQRzCKovQBLQFudBrpPeg5yNpump
```

### Sell BlackWhale
```bash
python3 sell_token.py 8y45AJzCUBSZL1UDFQRzCKovQBLQFudBrpPeg5yNpump <amount>
```

## What Happens

1. ✅ Automatically loads credentials from `.env`
2. ✅ Gets a quote from Jupiter
3. ✅ Detects if token is complex (BONK) and uses optimized routing
4. ✅ Builds, signs, and submits transaction
5. ✅ Confirms transaction on mainnet
6. ✅ Records trade in `test_trades.json`
7. ✅ Provides Solscan link to verify

## Output Example

```
======================================================================
Buying DezXAZ8z
======================================================================
Token: DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263
Wallet: HhijUXN96yy1Pdrk3tmTDiV1HWw1LrQjhdX3DDusPnn9
Amount: 0.001 SOL (~$0.25)
Slippage: 5%
⚠️  Using legacy transaction format (complex token)

[1/4] Getting quote from Jupiter...
[TRADER] Quote received: 1467168138 tokens, impact: 0.00%
[TRADER] Getting swap instructions...
[TRADER] Using direct route strategy (smaller serialized size)
...
[3/4] Result:
  Status: confirmed
  Signature: 2ZGVooZF9SS2pjfmJjPtQbfXj7YU9gvuinEKTJngmgurvt7351AFSPNPje8pwWLcmFMofr3ae1XckDzkXE7JGBn4
  Output: 1467168138 tokens

✅ Check on Solscan:
   https://solscan.io/tx/2ZGVooZF9SS2pjfmJjPtQbfXj7YU9gvuinEKTJngmgurvt7351AFSPNPje8pwWLcmFMofr3ae1XckDzkXE7JGBn4
```

## Token Mints Reference

| Token | Mint Address | Notes |
|-------|-------------|-------|
| BONK | `DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263` | Complex routing, auto-optimized |
| BlackWhale | `8y45AJzCUBSZL1UDFQRzCKovQBLQFudBrpPeg5yNpump` | PumpFun token |

## Key Features

✅ **Automatic .env Loading** - No bash wrapper needed
✅ **Token Detection** - BONK automatically uses optimized routing
✅ **Direct Routes** - Complex tokens use simplified routing to stay within size limits
✅ **Full Transparency** - See every step of the transaction
✅ **Audit Trail** - All trades logged to `test_trades.json`
✅ **Solscan Integration** - Direct links to verify transactions

## Troubleshooting

**"HELIUS_API_KEY not set"**
- Ensure `.env` file exists in project root
- Check `.env` has `HELIUS_API_KEY=your_key_here`

**Transaction shows "confirmed" but no tokens received**
- This typically means insufficient liquidity for the swap
- Try with a larger amount
- Check Solscan link to verify what happened

**Transaction size too large**
- The system automatically handles this for BONK
- If it happens for other tokens, they can be added to `COMPLEX_TOKENS` in the script

## For More Details

See `ENV_SETUP.md` for:
- Complete environment configuration
- Security best practices
- Advanced usage patterns
- Integration with other modules
