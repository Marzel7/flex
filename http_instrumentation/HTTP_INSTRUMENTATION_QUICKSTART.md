# HTTP Instrumentation - Quick Start

**Time:** 5 minutes to understand, 30 minutes to implement

---

## What's Missing (Right Now)

Your `realtime_creator_funding_extractor.py` calls:
```
https://api-mainnet.helius-rpc.com/v0/addresses/{creator}/transactions
```

These calls **do NOT appear in your metrics dashboard** because they're not recorded.

Your `funder_incoming_extractor.py` calls:
```
https://api.helius.xyz/...
```

These calls **DO appear** because they call `record_request()`.

---

## The Fix (3 Small Changes)

### Change 1: Import the wrapper (top of realtime_creator_funding_extractor.py)

**Add this line after existing imports:**
```python
from http_instrumentation import async_request_json
```

### Change 2: Replace the HTTP call (line ~1027)

**OLD CODE (currently unrecorded):**
```python
async with self.session.get(
        query_url,
        timeout=aiohttp.ClientTimeout(total=30)
    ) as resp:
    if resp.status == 429:
        print(f"[REALTIME_FUNDING]    ⚠ Rate limited (429) on page {page_num}", flush=True)
        break
    if resp.status != 200:
        txt = await resp.text()
        print(f"[REALTIME_FUNDING]    ⚠ Helius HTTP {resp.status} on page {page_num}", flush=True)
        break
    page = await resp.json()
```

**NEW CODE (auto-recorded):**
```python
page = await async_request_json(
    self.session,
    "GET",
    query_url,
    timeout=aiohttp.ClientTimeout(total=30),
    section="creator_funding",
    source_file="realtime_creator_funding_extractor",
    record_func=record_request,
)

if page is None:
    print(f"[REALTIME_FUNDING]    ⚠ Helius request failed on page {page_num}", flush=True)
    break
```

**After this block, the code already has:**
```python
if not isinstance(page, list) or len(page) == 0:
    print(f"[REALTIME_FUNDING]    [PAGE {page_num}] No more transactions", flush=True)
    break
```

This still works (page is guaranteed to be list or None).

### Change 3: Extend metrics schema (rpc_metrics_recorder.py)

**Add these columns to the table creation:**
```python
# Modify the CREATE TABLE statement to include:
host TEXT,                      # e.g., "api-mainnet.helius-rpc.com"
path_group TEXT,                # e.g., "/v0/addresses/{addr}/transactions"
credits_estimated INTEGER DEFAULT 0,  # e.g., 100
```

**Update record_request() signature:**
```python
def record_request(
    section,
    provider,
    method,
    status_code,
    latency_ms,
    mode,
    retries,
    source_file,
    error=None,
    host=None,              # ← NEW
    path_group=None,        # ← NEW
    credits_estimated=None, # ← NEW
):
    # ... insert into database with these new fields ...
```

---

## Expected Result

**Before:**
```
Metrics Dashboard shows:
  - helius_api: 500 credits (from funder extractor)
  - helius_enhanced: 0 credits (MISSING!)
  Total: 500 credits
```

**After:**
```
Metrics Dashboard shows:
  - helius_api: 500 credits (funder extractor)
  - helius_enhanced: 2000 credits (creator extractor - NOW VISIBLE!)
  Total: 2500 credits
```

---

## How It Works

1. **async_request_json()** replaces raw `session.get()`
2. It detects provider by hostname: `api-mainnet.helius-rpc.com` → `helius_enhanced`
3. It auto-generates method name: `/v0/addresses/.../transactions` → `helius_enhanced_address_transactions`
4. It calls `record_request()` with all details (status, latency, errors, estimated credits)
5. All metrics flow into your dashboard automatically

---

## Verification

**Step 1:** Make changes above

**Step 2:** Run creator extraction for 1 token:
```python
python pumpfun_curve_listener.py
# ... wait for token to launch and extraction to complete ...
```

**Step 3:** Check metrics:
```bash
curl http://localhost:8001/dashboard
# or visit in browser
```

**Step 4:** Look for "helius_enhanced" in provider list
- Should see entries with method=`helius_enhanced_address_transactions`
- Should see credits_estimated > 0
- Should see latency_ms around 500-2000

---

## Optional: Add Dashboard Query Functions

In `rpc_metrics_api.py`, add:

```python
@app.route('/api/metrics/credits-by-provider')
def credits_by_provider():
    """Credits used per provider over last 24h"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            provider,
            SUM(credits_estimated) as total_credits,
            COUNT(*) as call_count,
            ROUND(AVG(latency_ms), 0) as avg_latency_ms,
            SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) as error_count
        FROM wallet_scan_metrics
        WHERE created_at >= datetime('now', '-24 hours')
        GROUP BY provider
        ORDER BY total_credits DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    return jsonify([
        {
            'provider': r[0],
            'total_credits': r[1],
            'call_count': r[2],
            'avg_latency_ms': r[3],
            'error_count': r[4],
        }
        for r in rows
    ])
```

Then in dashboard HTML:
```html
<div id="credits-by-provider"></div>

<script>
fetch('/api/metrics/credits-by-provider')
    .then(r => r.json())
    .then(data => {
        let html = '<table>';
        html += '<tr><th>Provider</th><th>Credits</th><th>Calls</th><th>Errors</th></tr>';
        data.forEach(row => {
            html += `<tr>
                <td>${row.provider}</td>
                <td>${row.total_credits}</td>
                <td>${row.call_count}</td>
                <td>${row.error_count}</td>
            </tr>`;
        });
        html += '</table>';
        document.getElementById('credits-by-provider').innerHTML = html;
    });
</script>
```

---

## That's It!

**3 changes** = complete visibility into all RPC/API calls

**Files modified:**
1. `realtime_creator_funding_extractor.py` (import + 1 block replace)
2. `rpc_metrics_recorder.py` (schema + signature)
3. `rpc_metrics_api.py` (optional: add dashboard queries)

**Files created:**
1. `http_instrumentation.py` (already done)

**Result:** 100% instrumentation of all HTTP calls with automatic credit tracking.
