# Phase 2A — Database Capability Check

## Overview

Safe rollout mechanism for the `networks_release` table that allows:
- ✅ Conditional routing to new paths
- ✅ Easy rollback by deploying older DB
- ✅ Parallel environment support
- ✅ Zero breaking changes

## How It Works

### 1. Automatic Detection
On first request, the Flask app checks if `networks_release` table exists:

```sql
SELECT name FROM sqlite_master WHERE type='table' AND name='networks_release'
```

Result is cached in `app.has_networks_release` (boolean flag).

### 2. Status Logging
When the app starts and receives the first request, you'll see:

```
[CAPABILITY_CHECK] Phase 2A networks_release: ENABLED    # Table exists
[CAPABILITY_CHECK] Phase 2A networks_release: DISABLED   # Table missing
```

## Using in Endpoints

### Example: Conditional Routing

```python
@app.route('/api/funding-networks')
def api_funding_networks():
    """Fetch funding networks, routing based on Phase 2A capability."""

    if app.has_networks_release:
        # ✅ New path: Use optimized networks_release table
        return get_networks_from_release()
    else:
        # ✅ Old path: Legacy computation
        return compute_networks_legacy()
```

### Template

```python
def my_endpoint():
    if app.has_networks_release:
        # NEW PATH - Phase 2A optimizations
        # ... use networks_release table
        # ... use new network clustering logic
    else:
        # OLD PATH - Legacy behavior
        # ... existing code
        # ... continues to work

    return result
```

## Deployment Scenarios

### Scenario 1: Rollout New Phase 2A DB
1. Deploy new database with `networks_release` table
2. Deploy updated `main.py` with capability check
3. First request detects table → `ENABLED`
4. New paths activated automatically

### Scenario 2: Rollback
1. Deploy old database **without** `networks_release` table
2. No code changes needed
3. First request detects missing table → `DISABLED`
4. Falls back to legacy paths automatically

### Scenario 3: Parallel Environments
- Environment A: Has `networks_release` → uses new paths
- Environment B: Missing `networks_release` → uses old paths
- **Same codebase works in both**

## Implementation Checklist

- [x] Capability check function (`check_networks_release_capability()`)
- [x] Flask initialization hook (`@app.before_request`)
- [x] Cached flag (`app.has_networks_release`)
- [ ] Update endpoints to use conditional routing (per endpoint)
- [ ] Test with and without `networks_release` table
- [ ] Monitor logs for `[CAPABILITY_CHECK]` entries

## Key Files

| File | Change |
|------|--------|
| `main.py` | Added capability check + initialization hook |
| `build_networks_release.py` | Creates `networks_release` table (already exists) |
| Any endpoint | Add `if app.has_networks_release:` for new logic |

## Error Handling

If the capability check fails (DB connection error, permission issue):
- Defaults to **old path** (conservative fallback)
- Logs error with `[CAPABILITY_CHECK] Error checking networks_release`
- App continues normally
- No breaking changes

## Monitoring

Check logs for these messages to verify capability detection:

```
[CAPABILITY_CHECK] Phase 2A networks_release: ENABLED    ✓ Table found
[CAPABILITY_CHECK] Phase 2A networks_release: DISABLED   ✓ Table missing
[CAPABILITY_CHECK] Error checking networks_release: ...   ⚠ Error detected
```

## Next Steps

1. **Identify endpoints** that need new Phase 2A paths
2. **Add conditional routing** using `if app.has_networks_release:`
3. **Test both scenarios**:
   - With `networks_release` table
   - Without `networks_release` table
4. **Deploy with confidence** knowing rollback is simple
