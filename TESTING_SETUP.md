# Buy-Only Testing Setup Guide

## Goal
Test the Trading Executor with **SMALL amounts** (0.01 SOL = ~$1.50 per trade) before any real strategy.

## Prerequisites

### 1. Helius API Key ✅
You already have this!
```bash
export HELIUS_API_KEY="0ae07551-32df-4d9d-af2a-1925fb7f561f"
```

### 2. Test Wallet with SOL
You need a **separate test wallet** (NOT your main wallet):

**Option A: Generate New Keypair**
```bash
# This creates a new keypair.json file
python3 << 'EOF'
from solders.keypair import Keypair
import json

keypair = Keypair()
secret_key = list(keypair.secret_key)

with open("test_keypair.json", "w") as f:
    json.dump(secret_key, f)

print(f"Created test_keypair.json")
print(f"Public key: {keypair.pubkey()}")
print(f"Send SOL to this address: https://solscan.io/account/{keypair.pubkey()}")
EOF
```

**Option B: Use Existing Keypair**
```bash
# If you already have a keypair.json, just use it
cp /path/to/your/keypair.json test_keypair.json
```

### 3. Fund Your Test Wallet
Send SOL to your test wallet's public key:
- Need at least **0.5 SOL** to test
- Use any Solana wallet (Phantom, etc.)
- Or use devnet for free SOL (see below)

## Testing Steps

### Step 1: Verify Setup
```bash
# Check Helius is working
python3 verify_helius_setup.py
# Should show: ✓ RPC Working! ✓ Blockhash fetching

# Check unit tests still pass
python3 -m pytest tests/test_trading_executor.py -v
# Should show: 15 passed
```

### Step 2: Run Test Script

**Option A: Using File Input (Simplest)**
```bash
# Make sure your API key is set
export HELIUS_API_KEY="0ae07551-32df-4d9d-af2a-1925fb7f561f"

# Run the test script
python3 test_buy_only.py

# It will ask for:
# - Path to test_keypair.json
# - Token mint to buy
# - Token symbol (for logging)
```

**Option B: Using Environment Variables (Automated Setup)**

Use the provided setup helper script to safely configure your environment:

```bash
# Run the setup helper (interactive)
python3 setup_trading_env.py

# It will ask for:
# 1. Path to your keypair JSON
# 2. Your Helius API key
# 3. Whether to save to shell profile (.zshrc or .bash_profile)

# Then it shows you the export commands and optionally saves them
```

The script will:
- ✅ Load your keypair securely
- ✅ Generate proper export commands
- ✅ Optionally save to your shell profile for persistence
- ✅ Show you the exact commands to run

After setup, you can run tests without entering keypair path:
```bash
# If you saved to profile (one-time setup):
source ~/.zshrc  # or ~/.bash_profile

# Then just run:
python3 test_buy_only.py
```

**Security Note**: Environment variables are visible in shell history. For better security:
- Only use in testing environments
- Never commit to git or share the variable value
- Use a separate test wallet (not your main wallet)
- Rotate keys periodically

### Step 3: Choose a Token to Test
Pick any token on Solana. Here are some good test tokens:

```
USDC: EPjFWaLb3eTRSAujiFvvrDFiNQ15ghTjciXTo7j5X8f
USDT: Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenErt
```

Or grab the mint of any token you see:
- Go to https://dexscreener.com/solana
- Click any token
- Copy the mint address

### Step 4: Monitor Your Trade
After each test buy:
```bash
# Check transaction on-chain
https://solscan.io/tx/{SIGNATURE_FROM_TRADE}

# Check your tokens
https://solscan.io/account/{YOUR_WALLET_ADDRESS}

# Check token price
https://dexscreener.com/solana/{TOKEN_MINT}
```

## What Happens in the Test

1. **Load keypair** - Reads your test_keypair.json
2. **Check balance** - Verifies you have >= 0.5 SOL
3. **Get quote** - Asks Jupiter: "How many tokens for 0.01 SOL?"
4. **Build transaction** - Uses Solders to create signed transaction
5. **Submit** - Sends to Jito for MEV protection
6. **Record** - Saves result to test_trades.json

## Expected Results

### Success (✅)
```
Status: confirmed
Signature: 5xY4z9...
Output: 12345 tokens

✅ Trade recorded! Check it on-chain:
   https://solscan.io/tx/5xY4z9...
```

### Failure (❌)
```
Status: failed
Error: Slippage exceeded

❌ Trade failed (expected for some tokens)
```

## Troubleshooting

### "HELIUS_API_KEY not set"
```bash
export HELIUS_API_KEY="0ae07551-32df-4d9d-af2a-1925fb7f561f"
python3 test_buy_only.py
```

### "Invalid mint address"
- Mint addresses are exactly 44 characters
- Get from: https://dexscreener.com/solana
- Copy the full address

### "Insufficient balance"
- Need 0.5+ SOL in test wallet
- Send more SOL to the wallet address
- Wait a few seconds for confirmation

### "Slippage exceeded"
- Normal for volatile tokens
- Try a token with more liquidity (larger market cap)
- Or increase slippage in the script (currently 5%)

### Transaction takes too long
- Blockchain is sometimes busy
- Wait a minute or two
- Check on Solscan: https://solscan.io/tx/{signature}

## Understanding the Results

### test_trades.json
After each trade, results are saved:
```json
{
  "timestamp": "2025-01-02T10:30:45.123456",
  "type": "buy",
  "symbol": "USDC",
  "token_mint": "EPjFWaLb3eTRSAujiFvvrDFiNQ15ghTjciXTo7j5X8f",
  "input_amount_sol": 0.01,
  "output_amount": 14850,
  "signature": "5xY4z9wP...",
  "status": "confirmed",
  "error": null
}
```

### What Each Field Means
- **timestamp** - When the trade happened
- **symbol** - Token symbol (for logging)
- **input_amount_sol** - How much SOL you spent (0.01)
- **output_amount** - How many tokens you got
- **signature** - Unique transaction ID on-chain
- **status** - "confirmed" or "failed"
- **error** - Error message if failed

## Next Steps After Testing

### If Trades Succeed:
1. ✅ You've validated the trading flow works
2. ✅ You understand how transactions are built
3. ✅ You've tested the Helius RPC integration
4. Next: Add sell logic when ready

### What to Watch:
- Transaction signatures appear on Solscan
- Tokens appear in your wallet
- test_trades.json gets updated
- No errors in the console

## Safety Features

The test script includes:
- ✅ Small amounts (0.01 SOL) - Low risk
- ✅ 5% slippage - Protects from wild price swings
- ✅ Trade logging - Never lose track of what you did
- ✅ Balance check - Won't trade if balance too low
- ✅ Error handling - Gracefully handles failures

## Keypair Security Best Practices

### For Testing (What You're Doing):
- ✅ Use a **separate test wallet** (NOT your main wallet)
- ✅ Keep test amounts small (0.01 SOL)
- ✅ Keypair file option (safest): Store in private directory with restricted permissions
- ✅ Env var option: Only for short-lived testing sessions

### Never Do:
- ❌ Use your main wallet keypair for testing
- ❌ Commit keypair files to git
- ❌ Share TRADING_KEYPAIR environment variable value
- ❌ Log keypair values
- ❌ Store in plaintext in shared directories

### When Moving to Production:
- Consider hardware wallet integration
- Use encrypted keypair storage
- Implement proper access controls
- Audit all transactions
- Use dedicated production wallet (not test wallet)

## Advanced: Use Devnet Instead

If you want to test without real SOL:

```bash
# Get devnet SOL (free)
solana airdrop 1 <YOUR_DEVNET_ADDRESS> --url devnet

# Modify test script to use devnet:
# Change rpc_endpoint to: https://api.devnet.solana.com
# Most tokens won't exist on devnet though
```

## Comparison: File vs Environment Variable

| Aspect | File Input | Environment Variable |
|--------|-----------|---------------------|
| **Setup Time** | Every run | One-time setup |
| **Security** | Better - keypair on disk | Env vars in shell history |
| **Convenience** | Requires path each time | Automatic after setup |
| **Visibility** | Hidden on disk | Can see with `echo $TRADING_KEYPAIR` |
| **Portability** | Works on any machine with file | Only current shell/profile |
| **Recommended For** | Production, high security | Testing, iteration |

**Choose File Input If:**
- You want maximum security
- You're running on a shared system
- You plan to move keypair between machines
- You don't mind entering path each time

**Choose Environment Variable If:**
- You're doing repeated testing
- You want to avoid manual entry
- You're on a secure personal machine
- You saved it to your shell profile

## Quick Start Checklist

- [ ] **Step 1**: Generate or locate your test keypair
- [ ] **Step 2**: Fund test wallet with 0.5+ SOL
- [ ] **Step 3**: Set HELIUS_API_KEY environment variable
- [ ] **Step 4**: Choose loading method (file or env var)
- [ ] **Step 5**: Run `python3 test_buy_only.py`
- [ ] **Step 6**: Pick a token from DexScreener
- [ ] **Step 7**: Execute first 0.01 SOL buy
- [ ] **Step 8**: Check transaction on Solscan
- [ ] **Step 9**: Verify tokens in wallet
- [ ] **Step 10**: Review test_trades.json

Once all checks pass, you're ready to:
- Run more test trades to verify consistency
- Experiment with different tokens
- Plan your trading strategy
- Eventually move to sell logic

## Questions?

Check:
- QUICK_START.md - General usage
- TRADING_EXECUTOR_README.md - Full API reference
- setup_trading_env.py - Interactive setup helper
- test_trades.json - Your trade history
- https://solscan.io - To verify transactions

Good luck! 🚀
