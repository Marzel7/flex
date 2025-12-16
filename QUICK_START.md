# Quick Start - Meteora Price Fetcher

Get started in 30 seconds!

## Installation

```bash
pip install requests base58
```

## Basic Usage

```bash
# Fetch price for a single pool
python meteora_price_fetcher.py 7htwpWDYmQAzMRehy9S2afcdK6oVmMD6eprywrMaswNi
```

## Common Commands

```bash
# Single pool with details
python meteora_price_fetcher.py POOL_ADDRESS -v

# Multiple pools at once
python meteora_price_fetcher.py POOL1 POOL2 POOL3

# Check help
python meteora_price_fetcher.py
```

## Output Format

```
Pool: 7htwpWDYmQAzMRehy9S2afcdK6oVmMD6eprywrMaswNi
Type: DAMM_V2
On-chain price:    0.000000002327774465
DexScreener price: Not indexed (pool may be too new)
```

## What It Does

1. **Detects** if pool is DAMM V2 or DLMM
2. **Fetches** price directly from on-chain vault balances
3. **Compares** with DexScreener API (if available)
4. **Displays** the results with full precision

## Understanding the Output

- **Pool**: The pool account address
- **Type**: Either DAMM_V2 or DLMM
- **On-chain price**: Real-time price calculated from vault balances
- **DexScreener price**: Reference price from the API (if pool is indexed)

## Why Use This?

✅ **Real-time**: Prices update instantly as trades execute
✅ **On-chain**: No reliance on indexed APIs for newly created pools
✅ **Accurate**: Directly from vault balances (the source of truth)
✅ **Reliable**: Handles multiple pool types automatically
✅ **Simple**: One command, clean output

## For Developers

Import as a module:

```python
from meteora_price_fetcher import fetch_price, get_damm_v2_price

# Single pool
result = fetch_price("7htwpWDYmQAzMRehy9S2afcdK6oVmMD6eprywrMaswNi")
price = result["on_chain_price"]

# Direct DAMM V2 price
price = get_damm_v2_price("7htwpWDYmQAzMRehy9S2afcdK6oVmMD6eprywrMaswNi")
```

## Test It

Try these real pools:

```bash
python meteora_price_fetcher.py 7htwpWDYmQAzMRehy9S2afcdK6oVmMD6eprywrMaswNi
python meteora_price_fetcher.py 7ZLUrUkVM9F1e46HjrZPXrPdsJ7pnbxLJRxubhKUghbS
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "No transactions found" | Pool address might be incorrect |
| "Failed to fetch" | Check internet connection, RPC might be down |
| "DexScreener: Not indexed" | Pool is too new, on-chain price still works |
| Slow response | RPC endpoint latency, normal for Solana |

## Next Steps

- Read [METEORA_PRICE_GUIDE.md](METEORA_PRICE_GUIDE.md) for detailed documentation
- Check the [GitHub repositories](https://github.com/MeteoraAg) for protocol specs
- Use verbose mode (`-v`) to see vault details
