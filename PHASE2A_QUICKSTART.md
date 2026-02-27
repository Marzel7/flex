# Phase 2A — Quick Start Guide

## One-Minute Overview

✅ **What's installed**: Database capability check in `main.py`
✅ **What it does**: Detects if `networks_release` table exists
✅ **Why**: Safe rollout - use new optimizations if available, else fallback to legacy
✅ **Status**: Ready to add conditional routing to endpoints

## Just Installed

```python
# In main.py (Lines 39-75)
✓ check_networks_release_capability()    # Checks table existence
✓ @app.before_request hook               # Initializes on first request
✓ app.has_networks_release               # Boolean flag (True/False)
```

## Test It

```bash
# Start the app
python main.py

# Look for this in logs:
[CAPABILITY_CHECK] Phase 2A networks_release: ENABLED   # Table exists
# OR
[CAPABILITY_CHECK] Phase 2A networks_release: DISABLED  # Table missing
```

## Use It in Code

Want to add a new path for an endpoint? This easy:

```python
@app.route('/api/funding-networks')
def api_funding_networks():
    if app.has_networks_release:
        # Use networks_release table
        return get_networks_optimized()
    else:
        # Use legacy path (existing code)
        return get_networks_legacy()
```

## Which Endpoints to Update?

See `PHASE2A_ENDPOINT_MAPPING.md` for:
- List of 11 endpoints that should use this
- Line numbers in main.py
- Priority order (high/medium)
- Implementation order

## Documentation

| File | Purpose |
|------|---------|
| `PHASE2A_CAPABILITY_CHECK.md` | How it works + examples |
| `PHASE2A_ENDPOINT_MAPPING.md` | Which endpoints + line numbers |
| `PHASE2A_IMPLEMENTATION_SUMMARY.md` | Complete details + next steps |
| `PHASE2A_QUICKSTART.md` | This file |

## Deploy with Confidence

```
Old DB (no networks_release)
↓
App detects: DISABLED
↓
Uses legacy paths
↓
Works as before ✓

New DB (with networks_release)
↓
App detects: ENABLED
↓
Uses new paths
↓
Fast + optimized ✓
```

## What's Next?

1. Update high-priority endpoints (see PHASE2A_ENDPOINT_MAPPING.md)
2. Test with both DB schemas
3. Deploy when ready
4. Monitor logs for `[CAPABILITY_CHECK]`

## Questions?

- **How it works**: See PHASE2A_CAPABILITY_CHECK.md
- **Line numbers**: See PHASE2A_ENDPOINT_MAPPING.md
- **Full details**: See PHASE2A_IMPLEMENTATION_SUMMARY.md
- **Code location**: `main.py` Lines 39-75

---

**Status**: ✅ Foundation installed | Next: Update endpoints
