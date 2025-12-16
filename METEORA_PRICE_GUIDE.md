# Meteora Token Price Fetcher

A comprehensive Python tool for fetching real-time token prices from Meteora pools on Solana.

## Features

- **Multi-Protocol Support**: Handles both DAMM V2 and DLMM pools
- **On-Chain Price Fetching**: Extracts prices directly from vault balances
- **Automatic Pool Detection**: Detects pool type automatically
- **Batch Processing**: Fetch prices for multiple pools at once
- **API Comparison**: Compares on-chain prices with DexScreener data
- **Verbose Output**: Detailed diagnostics available with `-v` flag

## Scripts Available

### 1. `meteora_price_fetcher.py` (Recommended)
The comprehensive, feature-rich price fetcher with batch support and pool auto-detection.

**Usage:**
```bash
# Single pool
python meteora_price_fetcher.py 7htwpWDYmQAzMRehy9S2afcdK6oVmMD6eprywrMaswNi

# Multiple pools
python meteora_price_fetcher.py pool1_addr pool2_addr pool3_addr

# Verbose output with details
python meteora_price_fetcher.py pool_addr -v

# Batch fetch with details
python meteora_price_fetcher.py pool1 pool2 pool3 --verbose
```

**Output Example:**
```
Pool: 7htwpWDYmQAzMRehy9S2afcdK6oVmMD6eprywrMaswNi
Type: DAMM_V2
On-chain price:    0.000000002327774465
DexScreener price: Not indexed (pool may be too new)

================================================================================
SUMMARY
================================================================================
Successfully fetched: 1/1 pools
  7htwpWDYmQAzMReh... : $0.0000000023
```

### 2. `get_price.py` (Simple Single Pool)
Lightweight script for basic price querying of a single pool.

**Usage:**
```bash
python get_price.py <pool_address>
```

## How It Works

### DAMM V2 Pools (Constant Product AMM)

1. **Fetch Creation Transaction**: Gets the earliest transaction that created the pool
2. **Extract Vault Addresses**: Identifies SPL Token accounts from the transaction
3. **Fetch Vault Balances**: Queries the balance of each vault via RPC
4. **Handle Decimals**: Reads decimal information from token mint accounts
5. **Calculate Price**: Computes spot price as `vault_B_balance / vault_A_balance`
6. **Validate**: Ensures price is within reasonable bounds (0 < price < 1e10)

### DLMM Pools (Dynamic Liquidity Market Maker)

Uses the binary mathematical formula derived from Meteora's DLMM specification:

```
price = (1 + bin_step/10_000)^active_id * 10^(base_decimals - quote_decimals)
```

Where:
- `active_id` is the current bin ID (offset 72 in pool account, type i32)
- `bin_step` is the bin step size (offset 76, type u16)
- `base_decimals` is the base token decimal places (offset 44, type u8)
- `quote_decimals` is the quote token decimal places (offset 45, type u8)

## Key Implementation Details

### Token Account Structure
Token accounts have a specific binary layout parsed at:
- **Offset 0-32**: Mint address (32 bytes)
- **Offset 64-72**: Token amount (8 bytes, little-endian u64)
- **Offset 72-73**: Decimals (1 byte, u8)

### Mint Account Structure
Mint accounts store decimals at:
- **Offset 44-45**: Decimals (1 byte, u8)

### Vault Identification
The script identifies vaults by:
1. Extracting all accounts from the pool creation transaction
2. Filtering by Solana address length (44 characters)
3. Checking owner field must be SPL Token Program (`TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA`)
4. Excluding the pool address itself

## Supported Meteora Programs

| Program | Type | Program ID |
|---------|------|-----------|
| DAMM V2 | Constant Product AMM | cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG |
| DLMM | Dynamic Liquidity Market Maker | Lbry5nCI5mNyvrYBxCJryAu2hVggA74g2MPhtVomjcc |

## Error Handling

The script handles various edge cases:

- **Empty Vaults**: Returns `None` if vault balances are zero
- **Missing Data**: Gracefully handles accounts with null data
- **Decimal Fallback**: Fetches decimals from mint if token account shows 0
- **Network Errors**: Includes timeout protection (15 seconds per RPC call)
- **Invalid Pools**: Detects and skips pools with insufficient data

## API Comparison

When DexScreener has the pool indexed, the script will:
1. Fetch the official market price from DexScreener
2. Compare with on-chain calculated price
3. Display percentage difference

Example:
```
On-chain price:    0.001234567890123456
DexScreener price: 0.001234000000000000
Difference:        0.05%
```

## GitHub Resources

This implementation is based on official Meteora resources:

- [Meteora DAMM V2 SDK (TypeScript)](https://github.com/MeteoraAg/damm-v2-sdk)
- [Meteora DAMM V2 Program (Rust)](https://github.com/MeteoraAg/damm-v2)
- [Meteora Virtual Price Calculator](https://github.com/L9T-Development/meteora-dynamic-pool-bondincurve-virtual-price)

## Requirements

```bash
pip install requests base58
```

## Examples

### Fetch Price and Store in Variable
```python
from meteora_price_fetcher import fetch_price

result = fetch_price("7htwpWDYmQAzMRehy9S2afcdK6oVmMD6eprywrMaswNi", verbose=True)
price = result["on_chain_price"]
print(f"Price: ${price:.10f}")
```

### Batch Fetch Multiple Pools
```bash
cat pool_addresses.txt | while read pool; do
    python meteora_price_fetcher.py "$pool"
done
```

### Verbose Debugging
```bash
python meteora_price_fetcher.py 7htwpWDYmQAzMRehy9S2afcdK6oVmMD6eprywrMaswNi -v
```

Output includes:
- Pool type detection
- Vault addresses and balances
- Token decimals
- Calculation parameters (for DLMM)

## Limitations

1. **DexScreener Lag**: Newly created pools may not be indexed on DexScreener immediately
2. **Vault Balance Changes**: Prices change constantly as trades execute
3. **Multiple Vault Pairs**: If more than 2 vaults exist, uses the first pair
4. **DLMM Formula**: Requires specific binary offsets; may not work with modified programs

## Testing

Test pools are available at:
- `7htwpWDYmQAzMRehy9S2afcdK6oVmMD6eprywrMaswNi` (DAMM V2, small balance)
- `47ptsotLniVCTXWDgaF6WHpxQXgfPe559621fda3E7BA` (DAMM V2, tiny balance)
- `7ZLUrUkVM9F1e46HjrZPXrPdsJ7pnbxLJRxubhKUghbS` (DAMM V2, large price)

```bash
python meteora_price_fetcher.py \
    7htwpWDYmQAzMRehy9S2afcdK6oVmMD6eprywrMaswNi \
    47ptsotLniVCTXWDgaF6WHpxQXgfPe559621fda3E7BA \
    7ZLUrUkVM9F1e46HjrZPXrPdsJ7pnbxLJRxubhKUghbS
```

## Troubleshooting

### "Transaction not found"
- Pool creation transaction may have been pruned
- Try with a different RPC endpoint

### "Could not find at least 2 vaults"
- Pool may not have been initialized yet
- Check if pool address is correct

### "Failed to fetch DLMM price"
- Pool is actually DAMM V2 (script will auto-detect)
- Check pool account size and structure

### DexScreener shows "Not indexed"
- Pool is too new to be indexed
- Wait 1-2 hours for DexScreener to pick it up
- On-chain price will still be available

## Performance

- Single pool fetch: ~2-3 seconds
- Batch of 3 pools: ~8-10 seconds
- Network latency is the main bottleneck (depends on RPC provider)

## Contributing

Based on the official Meteora protocol specifications and tested on Solana mainnet with real pool data.
