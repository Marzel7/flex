# Freda Token - PumpFun V1 Pool Structure Analysis

## Token Details
- **Mint**: `D6ix6FHk7ucNN2kxb6FR3RounMrgc22QNxRocmiZEPQs`
- **Migration Sig**: `4NGbbjTVjxWEw7sMQkFGEeMiLRdGi2pNHkDGGEnS5sDb71UMxiqHBJtHqja2KpRgPrq7Lq3N6QdEBMaPqHAyGXQb`

## Discovered Pool/Vault Structure
From successful extraction via `test_freda_pumpfun.db`:

```
Base Account (Vault):   HAViY3RRHDrBAwGJf8yhWsuPJYZ88kUSTjHfAGSiWyhq
Quote Account (Vault):  HAViY3RRHDrBAwGJf8yhWsuPJYZ88kUSTjHfAGSiWyhq (same!)
Base Token (Mint):      D6ix6FHk7ucNN2kxb6FR3RounMrgc22QNxRocmiZEPQs (FREDA)
Quote Token:            So11111111111111111111111111111111111111112 (SOL)
Base Decimals:          6
Quote Decimals:         9
Pool Program:           pumpfun_v1
```

## Key Observations

### 1. Single Account for Both Vaults
Unlike Raydium AMM pools which have separate base and quote token accounts, **PumpFun V1 uses a SINGLE account** for both vault addresses. This is a critical structural difference.

**Implication**: When extracting vaults from PumpFun V1 pool state at offsets 232-264 and 264-296, both offsets point to the same account address.

### 2. Discovery Method
The pool was discovered through:
- Migration transaction scanning
- Finding PumpSwap-owned accounts (program: `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`)
- Using vault pair discovery fallback

### 3. Vault Account Properties
The single vault account (`HAViY3...`) is:
- Owned by PumpSwap program (`pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA`)
- Approximately 290-310 bytes (standard PumpSwap pool size)
- Contains reserves for both FREDA and SOL

## Usage in Price Calculation

For WebSocket pricing:
1. Subscribe to account `HAViY3RRHDrBAwGJf8yhWsuPJYZ88kUSTjHfAGSiWyhq`
2. On account updates, decode reserves
3. Extract FREDA balance and SOL balance from the single account
4. Calculate price as: `SOL_balance / FREDA_balance * sol_price_usd`

## Code Implications

### Pool Registration
```python
{
    'base_account': 'HAViY3RRHDrBAwGJf8yhWsuPJYZ88kUSTjHfAGSiWyhq',
    'quote_account': 'HAViY3RRHDrBAwGJf8yhWsuPJYZ88kUSTjHfAGSiWyhq',  # Same!
    'base_token': 'D6ix6FHk7ucNN2kxb6FR3RounMrgc22QNxRocmiZEPQs',
    'quote_token': 'So11111111111111111111111111111111111111112',
    'base_decimals': 6,
    'quote_decimals': 9,
    'pool_program': 'pumpfun_v1'
}
```

### WebSocket Subscription
WebSocket will only subscribe to **one account** (since both base and quote are the same):
- Single subscription to `HAViY3RRHDrBAwGJf8yhWsuPJYZ88kUSTjHfAGSiWyhq`
- Avoids duplicate subscriptions
- More efficient than Raydium pools with separate accounts

### Reserve Extraction
The single PumpSwap pool account contains:
- FREDA token balance (base reserve)
- SOL balance (quote reserve)
- Both in a single account structure

This differs from Raydium AMM where vaults are separate SPL token accounts.
