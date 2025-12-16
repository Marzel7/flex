# Meteora Price Fetching Scripts Index

Complete reference for all price-related scripts in this project.

## Primary Scripts (Ready to Use)

### 1. `meteora_price_fetcher.py` ⭐ RECOMMENDED
**The comprehensive price fetcher with all features.**

```bash
python meteora_price_fetcher.py POOL_ADDRESS [POOL_ADDRESS2...] [-v|--verbose]
```

**Features:**
- ✅ Auto-detects DAMM V2 and DLMM pools
- ✅ Batch processing support
- ✅ Verbose debugging mode
- ✅ DexScreener API comparison
- ✅ Summary report for multiple pools
- ✅ Full error handling

**Best for:** Production use, multiple pools, debugging

**Examples:**
```bash
# Single pool
python meteora_price_fetcher.py 7htwpWDYmQAzMRehy9S2afcdK6oVmMD6eprywrMaswNi

# Multiple pools with summary
python meteora_price_fetcher.py pool1 pool2 pool3

# Verbose output with vault details
python meteora_price_fetcher.py POOL -v
```

---

### 2. `get_price.py`
**Simple, lightweight single-pool fetcher.**

```bash
python get_price.py <pool_address>
```

**Features:**
- ✅ Simple, focused implementation
- ✅ Minimal output
- ✅ DexScreener comparison
- ✅ Good for scripts and automation

**Best for:** Simple scripts, single pools, integration

**Example:**
```bash
python get_price.py 7htwpWDYmQAzMRehy9S2afcdK6oVmMD6eprywrMaswNi
```

---

## Test & Debug Scripts

### 3. `test_damm_v2_vaults_simple.py`
**Tests DAMM V2 vault extraction with improved token account detection.**

Tests vault extraction on pool: `7htwpWDYmQAzMRehy9S2afcdK6oVmMD6eprywrMaswNi`

**Features:**
- ✅ Validates is_token_account() function
- ✅ Tests vault balance fetching
- ✅ Shows intermediate steps with checkmarks
- ✅ Good for understanding the extraction process

**Usage:**
```bash
python test_damm_v2_vaults_simple.py
```

**Output:**
```
✓ Creation transaction: ...
✓ Vault A: 2imG9BQEoj6i3XV7rfHU5k7ve8xYcBwoBySut468msZa
✓ Vault B: G5UUvvZKzcESYTHvJEhigJNKxcLdrJ5EnLfQeHQAsLsV
✓ Spot price (B per A): 0.000000002327774465
✓ SUCCESS - Extracted price from DAMM V2 pool
```

---

### 4. `check_vault_accounts.py`
**Inspects account types and properties.**

Verifies that identified vaults are actually SPL Token accounts.

**Features:**
- ✅ Displays account owner
- ✅ Shows lamport balance
- ✅ Identifies account type
- ✅ Fetches token balances for verification

**Usage:**
```bash
python check_vault_accounts.py
```

**Output:**
```
Account: 2imG9BQEoj6i3XV7rfHU5k7ve8xYcBwoBySut468msZa
  Owner: TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA
  Type: SPL Token Account
  Balance: 2679812883 (decimals: 6)
  Human: 2,679.81288300
```

---

### 5. `test_damm_v2_vaults.py`
**Advanced vault extraction test using Solana SDK.**

Uses `solders` library for RPC calls with type safety.

**Features:**
- ✅ Uses official Solana SDK
- ✅ Shows transaction accounts
- ✅ Searches for mint addresses
- ✅ Fetches vault balances
- ✅ Good for learning vault extraction

**Usage:**
```bash
python test_damm_v2_vaults.py
```

---

## Documentation Files

### 📖 `QUICK_START.md`
30-second getting started guide. Start here!

### 📖 `METEORA_PRICE_GUIDE.md`
Comprehensive technical documentation with:
- How it works (detailed)
- Binary structure explanations
- Error handling details
- GitHub resources
- Troubleshooting guide

### 📖 `IMPLEMENTATION_SUMMARY.md`
Architecture and implementation details:
- How detection works
- Data structures
- Performance metrics
- Code quality notes
- Future improvements

---

## Quick Comparison Table

| Script | Use Case | Complexity | Features |
|--------|----------|-----------|----------|
| `meteora_price_fetcher.py` | Production | Medium | Batch, auto-detect, verbose, API compare |
| `get_price.py` | Simple/Integration | Low | Single pool, API compare |
| `test_damm_v2_vaults_simple.py` | Testing/Learning | Medium | Step-by-step extraction |
| `check_vault_accounts.py` | Debugging | Low | Account inspection |
| `test_damm_v2_vaults.py` | Learning | High | SDK-based extraction |

---

## Getting Started

### Option 1: Just Want Prices? ⭐
```bash
python meteora_price_fetcher.py 7htwpWDYmQAzMRehy9S2afcdK6oVmMD6eprywrMaswNi
```

### Option 2: Simple Integration?
```bash
python get_price.py 7htwpWDYmQAzMRehy9S2afcdK6oVmMD6eprywrMaswNi
```

### Option 3: Understand How It Works?
```bash
# Read QUICK_START.md first
# Then run test scripts to see intermediate steps
python test_damm_v2_vaults_simple.py  # Shows step-by-step
```

### Option 4: Deep Dive?
```bash
# Read METEORA_PRICE_GUIDE.md for technical details
# Read IMPLEMENTATION_SUMMARY.md for architecture
# Run with -v flag to see what's happening
python meteora_price_fetcher.py POOL -v
```

---

## Dependencies

All scripts require:
```bash
pip install requests base58
```

Optional (for test scripts):
```bash
pip install solders  # For test_damm_v2_vaults.py
```

---

## Common Tasks

### Fetch price for a single pool
```bash
python meteora_price_fetcher.py POOL_ADDRESS
# or
python get_price.py POOL_ADDRESS
```

### Batch fetch multiple pools
```bash
python meteora_price_fetcher.py POOL1 POOL2 POOL3
```

### Debug why a pool fails
```bash
python meteora_price_fetcher.py POOL_ADDRESS -v
# or
python test_damm_v2_vaults_simple.py
```

### Check if accounts are valid tokens
```bash
python check_vault_accounts.py
```

### Learn the extraction process
```bash
python test_damm_v2_vaults_simple.py
```

---

## Test Pools

These real pools work with all scripts:

```bash
# Small balance pool
python meteora_price_fetcher.py 7htwpWDYmQAzMRehy9S2afcdK6oVmMD6eprywrMaswNi

# Large balance pool  
python meteora_price_fetcher.py 7ZLUrUkVM9F1e46HjrZPXrPdsJ7pnbxLJRxubhKUghbS

# Tiny balance pool
python meteora_price_fetcher.py 47ptsotLniVCTXWDgaF6WHpxQXgfPe559621fda3E7BA

# Or all three at once
python meteora_price_fetcher.py \
    7htwpWDYmQAzMRehy9S2afcdK6oVmMD6eprywrMaswNi \
    7ZLUrUkVM9F1e46HjrZPXrPdsJ7pnbxLJRxubhKUghbS \
    47ptsotLniVCTXWDgaF6WHpxQXgfPe559621fda3E7BA
```

---

## Troubleshooting

### Script won't run?
- Install dependencies: `pip install requests base58`
- Check Python version: `python --version` (need 3.7+)

### Getting "No transactions found"?
- Check pool address is correct
- Try a different RPC provider
- Pool may be too new (requires at least 1 confirmed tx)

### Price seems wrong?
- Use `-v` flag to see vault details
- Check if both vaults have balance > 0
- Prices change constantly with trades

### Can't import as module?
- Make sure script is in your Python path
- Use absolute imports: `from /path/to/meteora_price_fetcher import ...`

---

## Next Steps

1. **Try it**: `python meteora_price_fetcher.py <pool_address>`
2. **Learn**: Read QUICK_START.md
3. **Understand**: Read METEORA_PRICE_GUIDE.md
4. **Integrate**: Use get_price.py in your code
5. **Debug**: Use -v flag and test scripts

---

## Support

All scripts are self-contained and include comprehensive error messages. For issues:

1. Run with `-v` flag for verbose output
2. Check METEORA_PRICE_GUIDE.md for troubleshooting
3. Verify pool address with `check_vault_accounts.py`
4. Review test script output for step-by-step process
