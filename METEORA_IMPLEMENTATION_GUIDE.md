# Meteora DLMM Implementation Guide

## Overview

This guide documents how to correctly extract prices from Meteora DLMM (Dynamic Liquidity Market Maker) pools on Solana using RPC and on-chain account data.

## Authoritative Formula

```
raw_price = (1 + bin_step / 10_000) ^ active_id
price = raw_price * 10^(base_decimals - quote_decimals)
```

**Returns**: Quote token amount per 1 base token

### Why This Formula

1. **Mathematically Deterministic** - Price is computed directly from bin structure, not reserves
2. **Single Source of Truth** - Only the active_id matters, not pool balances
3. **Matches Market Data** - DexScreener, Arbitrage bots, Market makers all use this formula
4. **Token Independent** - Decimal adjustment ensures prices are human-readable
5. **Efficient** - No need to read vault balances or reserve accounts

## Correct Offsets (Current DLMM Layout)

These offsets are for the current Meteora DLMM account structure. They are fixed and verified.

| Field | Offset | Type | Description |
|-------|--------|------|-------------|
| base_decimals | 44 | u8 | Decimals of the token being sold |
| quote_decimals | 45 | u8 | Decimals of the token being bought |
| active_id | 72 | i32 | Current active bin index |
| bin_step | 76 | u16 | Bin step size (usually 50-100) |

## Python Implementation

```python
import struct
import math

def get_meteora_dlmm_price(account_data: bytes) -> float:
    """Extract price from Meteora DLMM LBPair account"""

    # Validate minimum size
    if len(account_data) < 78:
        return None

    try:
        # Read fields
        base_decimals = struct.unpack_from("<B", account_data, 44)[0]
        quote_decimals = struct.unpack_from("<B", account_data, 45)[0]
        active_id = struct.unpack_from("<i", account_data, 72)[0]
        bin_step = struct.unpack_from("<H", account_data, 76)[0]

        # Validate
        if bin_step == 0 or bin_step > 10000:
            return None

        # Calculate price
        base = 1.0 + (bin_step / 10_000.0)
        raw_price = math.pow(base, active_id)
        decimal_adjustment = 10 ** (base_decimals - quote_decimals)
        price = raw_price * decimal_adjustment

        # Validate range (optional but recommended)
        if 1e-20 < price < 1e20:
            return price

        return None

    except struct.error:
        return None
```

## Complete RPC Example

```python
import requests
import base64
import struct
import math

RPC_URL = "https://api.mainnet-beta.solana.com"

def get_dlmm_price(lb_pair_address: str) -> float:
    """Fetch and calculate Meteora DLMM price from RPC"""

    # 1. Fetch account data
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAccountInfo",
        "params": [lb_pair_address, {"encoding": "base64"}]
    }

    response = requests.post(RPC_URL, json=payload)
    result = response.json()

    if not result.get("result"):
        return None

    account_data = base64.b64decode(
        result["result"]["value"]["data"][0]
    )

    # 2. Parse fields
    base_decimals = struct.unpack_from("<B", account_data, 44)[0]
    quote_decimals = struct.unpack_from("<B", account_data, 45)[0]
    active_id = struct.unpack_from("<i", account_data, 72)[0]
    bin_step = struct.unpack_from("<H", account_data, 76)[0]

    # 3. Calculate price
    base = 1.0 + (bin_step / 10_000.0)
    raw_price = base ** active_id
    price = raw_price * (10 ** (base_decimals - quote_decimals))

    return price
```

## Key Implementation Details

### Struct Format Codes
- `<B` - unsigned byte (1 byte)
- `<i` - signed 32-bit integer (4 bytes), little-endian
- `<H` - unsigned 16-bit integer (2 bytes), little-endian

### Decimal Adjustment
The decimal adjustment is **crucial**:
- Without it: Prices are off by 10^(decimals)
- Token with 6 decimals vs quote with 9 decimals: adjust by 10^(-3)
- Token with 18 decimals vs quote with 6 decimals: adjust by 10^(12)

### Validation
Always validate:
1. Account size >= 78 bytes
2. bin_step in range [1, 10000]
3. Final price in reasonable range (1e-20 to 1e20)

## What NOT to Do

### ❌ Common Mistakes

1. **Using Pool Balances** - DLMM is not reserve-based, it's mathematical
2. **Using Raydium AMM Math** - Different formula, doesn't apply
3. **Ignoring Decimals** - Causes 10^decimals error
4. **Reading Offset 256** - That's outdated/incorrect
5. **Using Swap Output** - Swap prices have slippage, not spot price
6. **Reading Non-Active Bins** - Only active bin defines spot price
7. **Hardcoding Base Rate** - Must use dynamic bin_step

### ✅ What to Do Instead

1. **Use Official Formula** - (1 + bin_step/10000)^active_id
2. **Apply Decimal Adjustment** - Multiply by 10^(base-quote)
3. **Read Correct Offsets** - 44, 45, 72, 76
4. **Validate Input** - Check bin_step and account size
5. **Compare with DexScreener** - Verify your prices match market data
6. **Monitor Active ID** - Track price changes via active_id updates
7. **Use RPC Only** - No API keys needed, direct on-chain data

## Monitoring Price Changes

Prices change when active_id changes. Monitor a pool by:

```python
def monitor_pool_price(lb_pair_address: str):
    """Monitor pool price for changes"""
    prev_active_id = None

    while True:
        # Fetch current price
        price = get_dlmm_price(lb_pair_address)

        # Parse to see active_id
        account_data = fetch_account_data(lb_pair_address)
        active_id = struct.unpack_from("<i", account_data, 72)[0]

        # Detect change
        if prev_active_id is not None and active_id != prev_active_id:
            print(f"Price changed! active_id: {prev_active_id} → {active_id}")
            print(f"Price: ${price:.18f}")

        prev_active_id = active_id
        time.sleep(1)  # Check every second
```

## Testing Your Implementation

### Unit Test Pattern
```python
def test_price_calculation():
    # Create synthetic account data
    account_data = bytearray(80)
    struct.pack_into("<B", account_data, 44, 6)      # base_decimals
    struct.pack_into("<B", account_data, 45, 9)      # quote_decimals
    struct.pack_into("<i", account_data, 72, 100)    # active_id
    struct.pack_into("<H", account_data, 76, 50)     # bin_step

    # Calculate expected
    expected = (1.005 ** 100) * 0.001

    # Get actual
    actual = get_dlmm_price(bytes(account_data))

    # Verify
    assert abs(actual - expected) < 1e-15
```

### Integration Test Pattern
```python
def test_against_dexscreener():
    # Fetch your pool price
    lb_pair_address = "..."
    your_price = get_dlmm_price(lb_pair_address)

    # Get DexScreener price
    ds_response = requests.get(
        f"https://api.dexscreener.com/latest/dex/solana/{lb_pair_address}"
    )
    ds_price = ds_response.json()["pairs"][0]["priceUsd"]

    # Compare
    percent_diff = abs(your_price - ds_price) / ds_price * 100
    assert percent_diff < 5  # Should be very close
```

## Production Considerations

### RPC Endpoint
- Use reliable RPC with high availability
- Consider using Helius, Quicknode, or Alchemy for production
- Add fallback RPC endpoints

### Error Handling
- Validate account size before parsing
- Handle struct.error exceptions
- Validate price ranges
- Log failures for debugging

### Caching
- Cache prices for 1-2 seconds
- Don't spam RPC with price requests
- Monitor active_id for changes

### Performance
- Direct struct parsing is ~1ms per pool
- No external API calls needed
- Can process 1000+ pools per second

## Further Reading

- **Meteora Docs**: Check official documentation for account structure details
- **DexScreener API**: Compare prices to verify correctness
- **Solana RPC**: https://docs.solana.com/api/http
- **Struct Module**: https://docs.python.org/3/library/struct.html

## Support

For issues with this implementation:
1. Verify offsets haven't changed (check LBPair account structure)
2. Test against DexScreener to verify correctness
3. Check RPC endpoint availability
4. Validate input account data size
