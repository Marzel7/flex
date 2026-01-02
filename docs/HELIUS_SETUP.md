# Helius RPC Setup for Integration Testing

## Quick Start

### Option 1: One-Time Test Run
```bash
export HELIUS_API_KEY="your_api_key_here"
python3 -m pytest tests/test_trading_executor_integration.py -v -s
```

### Option 2: Using Helper Script
```bash
export HELIUS_API_KEY="your_api_key_here"
./run_integration_tests.sh
```

### Option 3: Permanent Setup (Recommended)
Add to your shell profile (`~/.bash_profile`, `~/.zshrc`, etc.):
```bash
export HELIUS_API_KEY="your_api_key_here"
```

Then reload:
```bash
source ~/.zshrc  # or ~/.bash_profile
```

## What Tests Will Pass With Helius Key

With `HELIUS_API_KEY` set, you'll see:

### ✅ Now Passing Tests
1. **test_rpc_connectivity** - Verify Helius RPC is responding
2. **test_blockhash_fetching** - Fetch real blockhash from blockchain
3. **test_transaction_history_persistence** - Already passing
4. **test_invalid_token_mint** - Already passing
5. **test_zero_amount_handling** - Already passing

### ⏭️ Still Skipping (Expected)
1. **test_jupiter_quote_real** - Jupiter requires separate auth
2. **test_swap_instructions_real** - Depends on Jupiter
3. **test_transaction_building_with_real_data** - Depends on Jupiter
4. **test_full_buy_flow_simulation** - Depends on Jupiter
5. **test_full_sell_flow_simulation** - Depends on Jupiter

## Verifying Setup

```bash
# Check if API key is set
echo $HELIUS_API_KEY

# Should output your API key if set correctly
# If empty, it's not set
```

## RPC Endpoints Explained

### Helius Free Tier
- **Endpoint:** `https://mainnet.helius-rpc.com/?api-key=YOUR_KEY`
- **Requests/sec:** 10
- **Price:** Free
- **Great for:** Testing and development

### Helius Pro Tier
- **Price:** $19-99/month
- **Requests/sec:** 100+
- **Features:** Priority, webhooks, advanced APIs
- **Great for:** Production trading

## Testing Without Helius

The unit tests work perfectly without any API keys:

```bash
# No API key needed - all 15 tests pass
python3 -m pytest tests/test_trading_executor.py -v
```

## Troubleshooting

### "HELIUS_API_KEY not set" message
**Solution:** Export the variable before running tests
```bash
export HELIUS_API_KEY="your_key"
python3 -m pytest tests/test_trading_executor_integration.py -v -s
```

### "Unauthorized" error from Helius
**Problem:** Invalid or expired API key
**Solution:**
1. Check your key: `echo $HELIUS_API_KEY`
2. Verify it on https://www.helius.dev/
3. Generate a new key if needed

### "Still getting 401 from Jupiter"
**This is expected** - Jupiter requires a separate API key (different from Helius)
- For now, use the mocked unit tests
- Jupiter's free tier no longer works without auth

## Test Output Examples

### With Helius Key Set
```
test_rpc_connectivity PASSED
  ✓ RPC Connectivity: Got slot 262543201

test_blockhash_fetching PASSED
  ✓ Blockhash Fetching: 6FVwEXXxT8qn6QvWnqZr7XvMKqJpNqvAkV4pP9kH8oU
```

### Without Helius Key
```
test_rpc_connectivity SKIPPED
  RPC endpoint returned non-JSON (possibly overloaded)

test_blockhash_fetching SKIPPED
  RPC parsing failed
```

## Next Steps After Helius Setup

1. **Verify RPC works:**
   ```bash
   export HELIUS_API_KEY="your_key"
   python3 -m pytest tests/test_trading_executor_integration.py::TestIntegrationWithRealRPC::test_rpc_connectivity -v -s
   ```

2. **Run all integration tests:**
   ```bash
   python3 -m pytest tests/test_trading_executor_integration.py -v -s
   ```

3. **Run unit tests (no API key needed):**
   ```bash
   python3 -m pytest tests/test_trading_executor.py -v
   ```

4. **Ready for production:**
   Once verified with Helius, you can use the same RPC endpoint for real trading:
   ```python
   trader = TokenTrader(
       rpc_endpoint="https://mainnet.helius-rpc.com/?api-key=YOUR_KEY",
       network="mainnet"
   )
   ```

## Security Notes

⚠️ **Never commit your API key to git**

Best practices:
1. Keep key in `~/.bash_profile` or `~/.zshrc` (local only)
2. Use environment variable (what we're doing)
3. Use `.env` file with `.gitignore` (if using python-dotenv)
4. Rotate keys regularly
5. Use Helius's key rotation feature

## Questions?

- Helius Docs: https://www.helius.dev/documentation
- Solana Docs: https://docs.solana.com/
- Trading Executor Docs: See TRADING_EXECUTOR_README.md
