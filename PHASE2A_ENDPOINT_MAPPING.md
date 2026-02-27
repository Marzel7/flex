# Phase 2A — Endpoint Implementation Map

This document identifies which endpoints need conditional routing based on `app.has_networks_release`.

## Priority Endpoints (Use networks_release table)

These endpoints directly work with network data and should benefit from the optimized `networks_release` table:

### High Priority — Network Retrieval

| Endpoint | Location | Use Case |
|----------|----------|----------|
| `/api/funding-networks` | Line 10634 | List all funding networks (use networks_release) |
| `/api/funding-networks-list` | Line 10705 | Network list with metadata (use networks_release) |
| `/api/funding-network-details/<id>` | Line 10780 | Network detail view (use networks_release) |
| `/api/network-tokens/<network_name>` | Line 12410 | Tokens in a network (use networks_release) |
| `/api/funder-networks` | Line 10590 | Networks by funder (use networks_release) |
| `/networks` | Line 12572 | Network dashboard (use networks_release) |
| `/creator-network/<network_name>` | Line 16089 | Creator network page (use networks_release) |

### Medium Priority — Network Analysis

| Endpoint | Location | Use Case |
|----------|----------|----------|
| `/api/network-coordinators` | Line 6947 | Coordinators in networks (can use networks_release) |
| `/api/funding-network` | Line 6805 | Single network fetch (may use networks_release) |
| `/api/funding-network-3tier/<creator_address>` | Line 7008 | 3-tier network for creator (can use networks_release) |
| `/api/build-funding-networks` | Line 10957 | Build networks (may trigger build_networks_release) |

## Implementation Template

Add this pattern to each endpoint:

```python
@app.route('/api/funding-networks')
def api_funding_networks():
    """Fetch funding networks."""

    if app.has_networks_release:
        # ✅ NEW PATH: Phase 2A optimization
        # Query networks_release table directly
        # Benefits:
        # - Faster queries (precomputed networks)
        # - Atomic version tracking
        # - Stability state information
        conn = sqlite3.connect(DB_PATH, timeout=30)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM networks_release ORDER BY created_at DESC")
        # ... process results

    else:
        # ✅ OLD PATH: Legacy computation
        # Existing code that builds networks dynamically
        # Works exactly as before
        # ... existing implementation

    return jsonify(result)
```

## Quick Reference: Line Numbers

```python
# NETWORK ENDPOINTS TO UPDATE
api_funding_networks                    # Line 10634 - HIGH PRIORITY
api_funding_networks_list              # Line 10705 - HIGH PRIORITY
api_funding_network_details            # Line 10780 - HIGH PRIORITY
api_network_tokens                     # Line 12410 - HIGH PRIORITY
api_funder_networks                    # Line 10590 - HIGH PRIORITY
networks_dashboard                     # Line 12572 - HIGH PRIORITY
creator_network_page                   # Line 16089 - HIGH PRIORITY

api_network_coordinators               # Line 6947  - MEDIUM PRIORITY
api_funding_network                    # Line 6805  - MEDIUM PRIORITY
api_funding_network_3tier              # Line 7008  - MEDIUM PRIORITY
api_build_funding_networks             # Line 10957 - MEDIUM PRIORITY
```

## Implementation Order

### Phase 2A-1: Core Network Retrieval (Week 1)
- [ ] `/api/funding-networks` - Replace with networks_release queries
- [ ] `/api/funding-networks-list` - Add networks_release path
- [ ] `/api/funding-network-details/<id>` - Direct table lookup

### Phase 2A-2: Network Pages (Week 1-2)
- [ ] `/networks` - Dashboard using networks_release
- [ ] `/creator-network/<network_name>` - Page using networks_release

### Phase 2A-3: Network Analysis (Week 2)
- [ ] `/api/funder-networks` - Funder network lookup
- [ ] `/api/network-tokens/<network_name>` - Token list by network
- [ ] `/api/network-coordinators` - Coordinator data

### Phase 2A-4: Advanced Features (Week 3)
- [ ] `/api/funding-network` - Single network fetch
- [ ] `/api/funding-network-3tier/<creator_address>` - 3-tier network
- [ ] `/api/build-funding-networks` - Trigger build_networks_release

## Testing Strategy

For each endpoint, test both scenarios:

```bash
# Scenario 1: With networks_release table
curl http://localhost:5002/api/funding-networks
# Expected: Fast response using networks_release

# Scenario 2: Without networks_release table
curl http://localhost:5002/api/funding-networks
# Expected: Response using legacy path

# Both should return identical structure
```

## Rollback Plan

If an endpoint breaks with new path:
1. Remove the conditional check (revert to legacy path only)
2. Deploy updated code
3. No database changes needed
4. App continues working

## Benefits Per Endpoint

| Endpoint | Benefit |
|----------|---------|
| `api_funding_networks` | O(1) table lookup vs O(N) computation |
| `api_funding_networks_list` | Precomputed metadata ready |
| `api_funding_network_details` | Direct row fetch by network_id |
| `api_network_tokens` | Atomic token list from networks_release |
| `networks` | Dashboard data available instantly |
| `creator_network` | Cross-funder relationships precomputed |

## Monitoring

After each endpoint update, monitor:
```
[CAPABILITY_CHECK] Phase 2A networks_release: ENABLED
```

And verify response times improve or stay the same.
