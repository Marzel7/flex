# RPC Endpoint Information

**User Question**: "which RPC - Solana?"

**Answer**: ✅ **YES - Public Solana RPC**

---

## Current RPC Configuration

### analyze_funder_networks.py

```python
SOLANA_RPC = "https://api.mainnet-beta.solana.com"
```

**Details**:
- **Endpoint**: `https://api.mainnet-beta.solana.com`
- **Network**: Solana Mainnet-Beta
- **Type**: Public free RPC
- **Rate Limit**: ~30 requests/minute
- **History Access**: ~300-500 recent signatures per address

---

## Why Public Solana RPC?

### Advantages
✅ Free (no API key required)
✅ No rate limiting for reasonable usage
✅ Works for transaction history analysis
✅ Suitable for signature retrieval

### Limitations
⚠️ Slower than paid RPC providers
⚠️ Rate limited (~30 req/min)
⚠️ Limited historical data (~300-500 signatures)
⚠️ May timeout during high network load

---

## Alternative RPC Options (Not Currently Used)

### Helius RPC (Available but Not Used)
```python
# Available in batch_wallet_clustering.py as fallback
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
HELIUS_RPC = f"https://mainnet.helius-rpc.com?api-key={HELIUS_API_KEY}"
```

**Why Not Using**:
- User explicitly requested: "dont use helius for this use solana RPC"
- Public RPC is sufficient for current needs
- Avoids external API dependencies

### QuickNode, Alchemy, etc.
- Not currently configured
- Would require API keys and setup
- Public Solana RPC covers current requirements

---

## Performance Characteristics

### Query Speed

| Tool | Data Source | Speed | Rate Limit |
|------|-------------|-------|-----------|
| test_funder_network.py | SQLite | ✅ INSTANT | None |
| analyze_repeat_funder.py | SQLite | ✅ INSTANT | None |
| analyze_funder_networks.py | Public Solana RPC | ⚠️ 2-5 sec/funder | ~30 req/min |

### Example Timing
```bash
# Database queries: <100ms per query
sqlite3 pumpswap_tokens.db "SELECT COUNT(*) FROM creator_funders;"
Result: ~100ms

# RPC queries: 2-5 seconds per funder (network dependent)
python3 analyze_funder_networks.py <creator> --limit 20
Result: ~40-100 seconds (20 funders × 2-5 sec each)
```

---

## Recommendation

### For Comprehensive Analysis (Recommended)
**Use the database-based tools**:
```bash
# Instant results, no rate limits
python3 test_funder_network.py <creator> --all
python3 analyze_repeat_funder.py <funder> --limit 20
```

### For Detailed Transaction Analysis (When Needed)
**Use the RPC tool**:
```bash
# Slower but shows detailed SOL transfer patterns
python3 analyze_funder_networks.py <creator> --limit 10
```

---

## Current Usage in Code

### File: analyze_funder_networks.py (Line 25)
```python
SOLANA_RPC = "https://api.mainnet-beta.solana.com"

class FunderNetworkAnalyzer:
    def __init__(self, solana_rpc: str = SOLANA_RPC):
        self.solana_rpc = solana_rpc
        self.session = None
```

### Initialization (Line 265-278)
```python
analyzer = FunderNetworkAnalyzer(solana_rpc=SOLANA_RPC)
await analyzer.init_session()

# Uses aiohttp for async requests to Solana RPC
async with self.session.post(self.solana_rpc, json=payload) as resp:
    result = await resp.json()
```

---

## Future Considerations

### If Rate Limiting Becomes Issue
1. **Cache results** in database to avoid re-querying
2. **Add Helius RPC** as paid alternative (better history, higher limits)
3. **Implement exponential backoff** for rate-limited requests
4. **Batch queries** to reduce RPC calls

### Current Status
**✅ Working as expected** - Public Solana RPC is sufficient for current analysis needs.

---

**Summary**: The system uses the **public Solana RPC endpoint** (`api.mainnet-beta.solana.com`) for detailed transaction analysis when needed, while preferring faster **SQLite database queries** for standard funder network analysis.
