# Token Query API Guide (`/api/tokens/query`)

## Overview

The `/api/tokens/query` endpoint provides flexible, server-side token querying with support for dynamic sorting, filtering, and pagination. This enables the UI to display tokens with user-controlled display options without requiring backend code changes.

## Endpoint Details

**URL:** `/api/tokens/query`
**Method:** `GET`
**Response Type:** `application/json`

## Query Parameters

### Sorting Parameters

| Parameter | Values | Default | Description |
|-----------|--------|---------|-------------|
| `sort_by` | `peak`, `date`, `price`, `risk` | `peak` | Column to sort by |
| `order` | `asc`, `desc` | `desc` | Sort order (ascending/descending) |

**Sort Column Details:**
- **peak**: `peak_percent_change` - Historical peak % gain since pool creation
- **date**: `first_seen` - Token detection timestamp (newest = most recently detected)
- **price**: `dexscreener_price_usd` - Current token price in USD
- **risk**: `funding_risk_level` - Risk assessment level (for display, not truly sortable, but supported)

### Filtering Parameters

| Parameter | Values | Default | Description |
|-----------|--------|---------|-------------|
| `limit` | `1-1000` | `20` | Maximum results to return |
| `risk_filter` | Risk level string | (none) | Filter by specific risk level |
| `bot_filter` | `with_bots`, `no_bots` | (none) | Filter by bot detection status |

**Risk Filter Values:**
- `UNKNOWN` - Risk not assessed
- `LOW` - Low risk
- `LOW+` - Low risk with bot detection
- `MEDIUM` - Medium risk
- `HIGH` - High risk
- `CRITICAL` - Critical risk / Confirmed rug pull

**Bot Filter Values:**
- `with_bots` - Only tokens with detected bot activity
- `no_bots` - Only tokens without bot activity

## Response Format

```json
{
  "tokens": [
    {
      "base_mint": "Token mint address",
      "name": "Token name",
      "symbol": "Token symbol",
      "peak_percent_change": 3931.56,
      "current_price_usd": 0.000001,
      "sol_balance": 1234.56,
      "risk_level": "LOW+",
      "bot_activity": "MEDIUM",
      "detected": "2026-01-07 18:59:18.238890",
      "initial_price": 0.00000001
    }
  ],
  "count": 20,
  "query_params": {
    "sort_by": "peak",
    "order": "desc",
    "limit": 20,
    "risk_filter": null,
    "bot_filter": null
  }
}
```

### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `base_mint` | string | Token mint address on Solana |
| `name` | string | Token name |
| `symbol` | string | Token symbol (or "UNKNOWN" if not available) |
| `peak_percent_change` | float | Historical peak gain as percentage |
| `current_price_usd` | float | Current price in USD (from DexScreener) |
| `sol_balance` | float | SOL balance in liquidity pool |
| `risk_level` | string | Funding risk assessment level |
| `bot_activity` | string | Bot activity level (NONE/LOW/MEDIUM/HIGH) |
| `detected` | string | ISO timestamp when token was detected |
| `initial_price` | float | Initial price when pool was created |

## Usage Examples

### 1. Default Query - Top 20 by Peak %
```
GET /api/tokens/query
```
Returns the 20 tokens with highest peak performance (default behavior).

### 2. Newest Tokens First
```
GET /api/tokens/query?sort_by=date&order=desc&limit=10
```
Returns the 10 most recently detected tokens.

### 3. Highest Current Prices
```
GET /api/tokens/query?sort_by=price&order=desc&limit=20
```
Returns the 20 tokens with highest current USD prices.

### 4. Filter CRITICAL Risk Only
```
GET /api/tokens/query?risk_filter=CRITICAL
```
Returns top 20 CRITICAL risk tokens (confirmed or suspected rug pulls).

### 5. Tokens with Bot Activity
```
GET /api/tokens/query?bot_filter=with_bots&limit=30
```
Returns top 30 tokens with detected bot activity, sorted by peak performance.

### 6. Safe Tokens (No Bots)
```
GET /api/tokens/query?bot_filter=no_bots&sort_by=peak&limit=20
```
Returns top 20 tokens without bot detection, sorted by peak performance.

### 7. Combined Filters
```
GET /api/tokens/query?bot_filter=with_bots&risk_filter=HIGH&sort_by=peak&limit=10
```
Returns top 10 tokens that have BOTH bots detected AND HIGH risk, sorted by peak performance.

### 8. Safe Newest Tokens
```
GET /api/tokens/query?bot_filter=no_bots&sort_by=date&order=desc&limit=15
```
Returns 15 most recently detected tokens that don't have bot activity.

### 9. All Risk Levels
```
GET /api/tokens/query?sort_by=risk&limit=50
```
Returns 50 tokens, grouped/sorted by risk level (UNKNOWN, LOW, LOW+, MEDIUM, HIGH, CRITICAL).

### 10. High Peak Gains with Low Risk
```
GET /api/tokens/query?risk_filter=LOW&sort_by=peak&order=desc&limit=20
```
Returns top 20 tokens with LOW risk assessment (best case: low-risk high-gain opportunities).

## Query Examples with Python

### Using requests library
```python
import requests

# Fetch top 10 CRITICAL risk tokens
response = requests.get(
    'http://localhost:5002/api/tokens/query',
    params={
        'risk_filter': 'CRITICAL',
        'limit': 10
    }
)
tokens = response.json()['tokens']

# Fetch newest safe tokens
response = requests.get(
    'http://localhost:5002/api/tokens/query',
    params={
        'sort_by': 'date',
        'order': 'desc',
        'bot_filter': 'no_bots',
        'limit': 15
    }
)
newest_safe = response.json()['tokens']
```

### Using fetch (JavaScript)
```javascript
// Fetch highest current prices
const response = await fetch(
  '/api/tokens/query?sort_by=price&order=desc&limit=20'
);
const data = await response.json();
const highestPriced = data.tokens;

// Fetch tokens with bots + HIGH risk
const response = await fetch(
  '/api/tokens/query?bot_filter=with_bots&risk_filter=HIGH'
);
const suspiciousTokens = await response.json();
```

## UI Implementation Patterns

### Pattern 1: Tabbed View
```
Tab 1: "By Peak Gains"    → sort_by=peak&order=desc
Tab 2: "Newest Tokens"    → sort_by=date&order=desc
Tab 3: "Highest Price"    → sort_by=price&order=desc
Tab 4: "Risk Overview"    → sort_by=risk
Tab 5: "With Bots"        → bot_filter=with_bots
Tab 6: "Safe Tokens"      → bot_filter=no_bots
```

### Pattern 2: Dropdown Filters
```
Sort By: [Peak ▼] [Date ▼] [Price ▼] [Risk ▼]
Risk Level: [All ▼] [CRITICAL ▼] [HIGH ▼] [LOW ▼] [LOW+ ▼]
Bot Activity: [Any ▼] [With Bots ▼] [No Bots ▼]
Show: [20 ▼] [50 ▼] [100 ▼]
```

### Pattern 3: Dynamic Query Builder
```
// JavaScript example
function buildQuery(options) {
  const params = new URLSearchParams();

  if (options.sortBy) params.append('sort_by', options.sortBy);
  if (options.order) params.append('order', options.order);
  if (options.limit) params.append('limit', options.limit);
  if (options.riskFilter) params.append('risk_filter', options.riskFilter);
  if (options.botFilter) params.append('bot_filter', options.botFilter);

  return `/api/tokens/query?${params}`;
}

// Usage
const url = buildQuery({
  sortBy: 'peak',
  order: 'desc',
  riskFilter: 'CRITICAL',
  limit: 20
});
```

## Performance Considerations

- **Database Query Time**: <100ms for typical queries (all 290 tokens)
- **Network Latency**: ~50-200ms depending on network conditions
- **Recommended Polling**: Query on user interaction, not continuous polling
- **Caching Suggestion**: Cache results for 30-60 seconds at the UI level

## Error Handling

### Successful Response
- **Status Code**: `200 OK`
- **Response**: Token array with metadata

### Error Response
- **Status Code**: `500 Internal Server Error`
- **Response**: `{ "error": "error message" }`

### Common Issues
1. **Invalid sort_by**: Falls back to default (peak)
2. **Invalid order**: Falls back to default (desc)
3. **Invalid limit**: Falls back to default (20), enforced max is 1000
4. **Non-existent risk_filter**: Returns empty results
5. **SQL injection**: Queries are parameterized, safe from SQL injection

## Integration Checklist

- [ ] Understand all available sort options
- [ ] Understand all available filters
- [ ] Test basic queries (no filters)
- [ ] Test sorted queries (each sort option)
- [ ] Test filtered queries (each filter)
- [ ] Test combined queries (multiple filters)
- [ ] Implement UI controls for sorting
- [ ] Implement UI controls for filtering
- [ ] Implement proper error handling
- [ ] Add loading indicators
- [ ] Cache results at UI level (optional)
- [ ] Test with real data
- [ ] Verify response times acceptable
- [ ] Add pagination if needed (use limit parameter)

## Current Database Statistics

- **Total Tokens**: 290
- **With Peak Data**: 290 (100%)
- **With Bot Detection**: 254 (87%)
- **With Risk Assessment**: 290 (100%)
- **Risk Distribution**:
  - CRITICAL: 21 tokens
  - HIGH: 11 tokens
  - MEDIUM: 54 tokens
  - LOW+: 107 tokens
  - LOW: 97 tokens
  - UNKNOWN: 0 tokens

## Real Data Examples

### Top 5 by Peak Gain
```
Token: UNKNOWN │ Peak: 3931.56% │ Risk: LOW+ │ Bots: MEDIUM
Token: UNKNOWN │ Peak: 3930.88% │ Risk: LOW+ │ Bots: MEDIUM
Token: UNKNOWN │ Peak: 3930.73% │ Risk: LOW+ │ Bots: MEDIUM
Token: UNKNOWN │ Peak: 3930.63% │ Risk: LOW │ Bots: NONE
Token: UNKNOWN │ Peak: 3930.61% │ Risk: LOW+ │ Bots: MEDIUM
```

### CRITICAL Risk Tokens (Top 5)
```
Token: UNKNOWN │ Peak: 189.04% │ Risk: CRITICAL │ Bots: LOW
Token: UNKNOWN │ Peak: 179.68% │ Risk: CRITICAL │ Bots: LOW
Token: UNKNOWN │ Peak: 179.46% │ Risk: CRITICAL │ Bots: LOW
Token: UNKNOWN │ Peak: 125.50% │ Risk: CRITICAL │ Bots: LOW
Token: UNKNOWN │ Peak: 63.03% │ Risk: CRITICAL │ Bots: MEDIUM
```

## Notes

- The endpoint is designed to be flexible and extensible
- Additional filter parameters can be added without changing the API structure
- The `initial_price_usd` field represents the price when the pool was first created
- Bot activity levels: NONE (no bots), LOW (1-5 tx), MEDIUM (6-20 tx), HIGH (21+ tx)
- Risk assessment is based on three-layer analysis: Helius wallet metrics, coordination detection, and bot detection
