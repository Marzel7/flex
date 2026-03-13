#!/bin/bash

# Register sample pools for WebSocket testing
# These are well-known Raydium pools with high liquidity

API_BASE="http://localhost:5002/api/price"

echo "🔗 Registering test pools for WebSocket..."
echo ""

# USDC pool (very common, high liquidity)
echo "1️⃣  Registering USDC pool..."
curl -X POST "$API_BASE/pool/register" \
  -H 'Content-Type: application/json' \
  -d '{
    "pool_accounts": [
      {
        "mint": "EPjFWaLb3odRvqA8E8h6UPs4mkfrEFAJiUbhA84wHvHU",
        "base_account": "8K3HWwYvMKSRP9LsNYqEfKdtwq33P99qiDPVvfySN6qf",
        "quote_account": "kinXVgW7KPBCw5d4qz5x6W5eWTSAm9CAxaKeVXya5Ek",
        "base_token": "EPjFWaLb3odRvqA8E8h6UPs4mkfrEFAJiUbhA84wHvHU",
        "quote_token": "So11111111111111111111111111111111111111112",
        "base_decimals": 6,
        "quote_decimals": 9,
        "pool_program": "raydium_amm"
      }
    ]
  }' 2>/dev/null | jq '.'
echo ""

# COPE pool
echo "2️⃣  Registering COPE pool..."
curl -X POST "$API_BASE/pool/register" \
  -H 'Content-Type: application/json' \
  -d '{
    "pool_accounts": [
      {
        "mint": "8HGyAAB1yoM1ttS7pnqw6AFXeiTWEwzommLaYzrSF1xN",
        "base_account": "GqvjJcg3X4pnAJ8GYKR4fH7hVRSj9G1ky9tyN5qEyrGn",
        "quote_account": "FLUXbeam8Z1F6yHsMjsDT2H91PPA6Pc4MH2FAqDZjUFs",
        "base_token": "8HGyAAB1yoM1ttS7pnqw6AFXeiTWEwzommLaYzrSF1xN",
        "quote_token": "So11111111111111111111111111111111111111112",
        "base_decimals": 6,
        "quote_decimals": 9,
        "pool_program": "raydium_amm"
      }
    ]
  }' 2>/dev/null | jq '.'
echo ""

# MSOL pool
echo "3️⃣  Registering mSOL pool..."
curl -X POST "$API_BASE/pool/register" \
  -H 'Content-Type: application/json' \
  -d '{
    "pool_accounts": [
      {
        "mint": "mSoLzYCxHdgNd4vkUcj9Xr8V2znhTW5mhTKSREm5LSb",
        "base_account": "XHjYrGrHuZxJZhCkHbaC1zP42FbuLBc73BcV1m7VHhq",
        "quote_account": "3u7kTWfUtjETNyukK8btkbqyioKMVqWorsKWDcVZqn4",
        "base_token": "mSoLzYCxHdgNd4vkUcj9Xr8V2znhTW5mhTKSREm5LSb",
        "quote_token": "So11111111111111111111111111111111111111112",
        "base_decimals": 9,
        "quote_decimals": 9,
        "pool_program": "raydium_amm"
      }
    ]
  }' 2>/dev/null | jq '.'
echo ""

echo "✅ Test pools registered!"
echo ""
echo "Next steps:"
echo "1. Restart services: ./scripts/restart.sh"
echo "2. WebSocket should connect automatically"
echo "3. Check health: curl http://localhost:5002/api/price/health | jq '.pool_stats.ws'"
echo ""
