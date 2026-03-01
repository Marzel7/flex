# RPC Metrics Integration Guide

This guide shows how to integrate the RPC Metrics Recorder into your existing FLEX code.

---

## Quick Start

### 1. Initialize Recorder (in main entry point)

```python
# In pumpfun_curve_listener.py or main.py startup:
from rpc_metrics_recorder import initialize_recorder

# Initialize with your plan's monthly credit budget
initialize_recorder(plan_monthly_credits=1_000_000)  # e.g., 1M credits/month
```

### 2. Wrap RPC Calls

For synchronous RPC calls in `funder_helius_extractor.py`:

```python
import time
from rpc_metrics_recorder import record_request

def get_transactions_helius(
    address: str,
    *,
    limit: int = 100,
    max_pages: int = 1,
    # ... other params
) -> List[Dict]:
    """Get transactions with metrics recording"""
    all_transactions = []
    current_before = None
    pages_fetched = 0

    for page_num in range(max_pages):
        attempt = 0
        while attempt < retries:
            start_time = time.time()

            try:
                url = f"https://api.helius.xyz/v0/addresses/{address}/transactions?api-key={HELIUS_API_KEY}&limit={limit}"
                if current_before:
                    url += f"&before={current_before}"

                response = requests.get(url, timeout=timeout)
                latency_ms = (time.time() - start_time) * 1000

                # Record the request (before error handling)
                record_request(
                    section="funder_incoming",           # Component section
                    provider="helius_enhanced",           # Provider
                    method="helius_enhanced_addresses_transactions",  # Method
                    status_code=response.status_code,     # HTTP status
                    latency_ms=latency_ms,                # Request time
                    mode="realtime",                      # Or "background"
                    retries=attempt,                      # Retry count so far
                    bytes_out=len(response.content) if response.ok else 0,  # Response size
                )

                # ... rest of error handling and retry logic

                if response.status_code == 200:
                    data = response.json()
                    # ... process data
                    break

            except requests.Timeout:
                latency_ms = (time.time() - start_time) * 1000
                record_request(
                    section="funder_incoming",
                    provider="helius_enhanced",
                    method="helius_enhanced_addresses_transactions",
                    status_code=504,  # Gateway timeout
                    latency_ms=latency_ms,
                    mode="realtime",
                    retries=attempt,
                    error="Timeout",
                )
                # ... retry logic

            except Exception as e:
                latency_ms = (time.time() - start_time) * 1000
                record_request(
                    section="funder_incoming",
                    provider="helius_enhanced",
                    method="helius_enhanced_addresses_transactions",
                    status_code=500,  # Internal error
                    latency_ms=latency_ms,
                    mode="realtime",
                    retries=attempt,
                    error=str(e),
                )
                # ... retry logic
```

### 3. Async RPC Calls

For async calls in `realtime_creator_funding_extractor.py`:

```python
import time
from rpc_metrics_recorder import record_request

async def fetch_creator_data_async(address: str):
    """Fetch creator data with metrics"""
    start_time = time.time()

    try:
        async with self.session.post(
            self.rpc_url,
            json={"jsonrpc": "2.0", "method": "getTransaction", "params": [...]},
            timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            latency_ms = (time.time() - start_time) * 1000
            response_text = await resp.text()

            record_request(
                section="creator_funding",
                provider="helius_rpc",
                method="getTransaction",
                status_code=resp.status,
                latency_ms=latency_ms,
                mode="realtime",
                bytes_out=len(response_text),
            )

            if resp.status == 200:
                return await resp.json()
            else:
                return None

    except asyncio.TimeoutError:
        latency_ms = (time.time() - start_time) * 1000
        record_request(
            section="creator_funding",
            provider="helius_rpc",
            method="getTransaction",
            status_code=504,
            latency_ms=latency_ms,
            mode="realtime",
            error="Timeout",
        )
        return None
```

### 4. Batch/Streaming Calls

For batch transaction fetches:

```python
import time
from rpc_metrics_recorder import record_request

def helius_batch_get_transactions(sigs: List[str]) -> Dict:
    """Fetch multiple transactions with metrics"""
    start_time = time.time()

    try:
        url = "https://api.helius.xyz/v0/transactions"
        response = requests.post(
            url,
            json={"transactions": sigs},
            timeout=30,
        )
        latency_ms = (time.time() - start_time) * 1000

        record_request(
            section="funder_incoming",
            provider="helius_enhanced",
            method="helius_enhanced_transactions_batch",
            status_code=response.status_code,
            latency_ms=latency_ms,
            mode="background",  # Batch is typically background work
            bytes_out=len(response.content) if response.ok else 0,
        )

        if response.status_code == 200:
            return response.json()
        return {}

    except Exception as e:
        latency_ms = (time.time() - start_time) * 1000
        record_request(
            section="funder_incoming",
            provider="helius_enhanced",
            method="helius_enhanced_transactions_batch",
            status_code=500,
            latency_ms=latency_ms,
            mode="background",
            error=str(e),
        )
        return {}
```

### 5. WebSocket/Streaming Calls

For streaming data (LaserStream, WebSocket):

```python
from rpc_metrics_recorder import record_stream_bytes

async def websocket_listener():
    """WebSocket listener with streaming metrics"""
    total_bytes = 0
    batch_size = 100_000  # Record metrics every 100KB

    async with websockets.connect(ws_url) as ws:
        async for message in ws:
            total_bytes += len(message.encode())

            # Record streaming bytes in batches
            if total_bytes >= batch_size:
                record_stream_bytes(
                    section="listener",
                    provider="helius_ws",
                    stream_name="enhanced_ws",
                    bytes_count=total_bytes,
                )
                total_bytes = 0

            # Process message...
```

---

## Integration Checklist

- [ ] Import `initialize_recorder` in main entry point
- [ ] Import `record_request` in RPC wrapper functions
- [ ] Call `initialize_recorder(plan_monthly_credits=...)` at startup
- [ ] Wrap all RPC calls with `record_request()` (before/after)
- [ ] Tag requests with correct `section` and `provider`
- [ ] Use `mode="realtime"` for token detection, `mode="background"` for background scans
- [ ] Record `latency_ms` from actual request timing
- [ ] Record `status_code` (HTTP code or RPC error)
- [ ] Capture response size in `bytes_out` when available
- [ ] Test metrics endpoint: `curl http://localhost:8001/metrics/rpc`
- [ ] View dashboard at `http://localhost:8001/dashboard`

---

## Section Tags Reference

Use these section tags when recording requests:

| Section | Purpose | Example |
|---------|---------|---------|
| `listener` | WebSocket listener for token creation | Pump.Fun stream, LaserStream |
| `creator_funding` | Extract creator funding relationships | Real-time token creator detection |
| `funder_incoming` | Extract funder's incoming transfers | Trace where funders got money |
| `creator_outgoing_scan` | Background scan of creator outgoing transfers | 12-hour enrichment job |
| `ui_api` | Flask API endpoints for dashboard | User-triggered analysis |
| `background_enrichment` | Background enrichment jobs | Batch processing, historical analysis |

---

## Provider Tags Reference

| Provider | Usage |
|----------|-------|
| `helius_rpc` | Standard Helius RPC endpoint (getTransaction, etc.) |
| `helius_enhanced` | Helius Enhanced Transactions API (REST endpoint) |
| `public_rpc_fallback` | Fallback to public RPC (Alchemy, Quicknode, etc.) |
| `other` | Other RPC providers |

---

## Method Tags Reference

Standard JSON-RPC methods:

```
getSignaturesForAddress    (10 credits)
getTransaction             (10 credits)
getSignatureStatuses       (1 or 10 credits)
getTransactionsForAddress  (100 credits)
```

Helius Enhanced REST pseudo-methods:

```
helius_enhanced_addresses_transactions  (1 credit per request)
helius_enhanced_transactions_batch      (5 credits per request)
```

Streaming:

```
laserstream_bytes          (3 credits per 0.1MB)
enhanced_ws_bytes          (3 credits per 0.1MB)
```

---

## Example: Complete Integration in funder_helius_extractor.py

```python
"""
Complete example of integrating metrics into get_transactions_helius()
"""

import time
import requests
from typing import List, Dict, Optional
from rpc_metrics_recorder import record_request

def get_transactions_helius(
    address: str,
    *,
    limit: int = 100,
    max_pages: int = 1,
    before: Optional[str] = None,
    timeout: int = 15,
    retries: int = 3,
) -> List[Dict]:
    """Get transactions with production-grade metrics"""
    all_transactions = []
    current_before = before
    pages_fetched = 0

    for page_num in range(max_pages):
        attempt = 0
        while attempt < retries:
            request_start = time.time()

            try:
                url = f"https://api.helius.xyz/v0/addresses/{address}/transactions?api-key={HELIUS_API_KEY}&limit={limit}"
                if current_before:
                    url += f"&before={current_before}"

                print(f"[HELIUS] Page {page_num + 1}/{max_pages}: Fetching {limit} txs (attempt {attempt + 1}/{retries})", flush=True)

                response = requests.get(url, timeout=timeout)
                latency_ms = (time.time() - request_start) * 1000

                # RECORD THE REQUEST
                record_request(
                    section="funder_incoming",
                    provider="helius_enhanced",
                    method="helius_enhanced_addresses_transactions",
                    status_code=response.status_code,
                    latency_ms=latency_ms,
                    mode="realtime",
                    retries=attempt,
                    bytes_out=len(response.content) if response.ok else 0,
                    error=None if response.ok else response.reason,
                )

                # Handle rate limiting
                if response.status_code == 429:
                    sleep_time = 0.5 * (2 ** attempt)
                    sleep_time = min(sleep_time, 30.0)
                    print(f"[HELIUS] Rate limited (429). Sleeping {sleep_time:.1f}s...", flush=True)
                    time.sleep(sleep_time)
                    attempt += 1
                    continue

                # Handle server errors
                if response.status_code >= 500:
                    sleep_time = 0.5 * (2 ** attempt)
                    print(f"[HELIUS] Server error ({response.status_code}). Backing off {sleep_time:.1f}s...", flush=True)
                    time.sleep(sleep_time)
                    attempt += 1
                    continue

                # Handle other errors
                if response.status_code != 200:
                    print(f"[HELIUS] HTTP {response.status_code}: {response.text[:200]}")
                    return all_transactions if all_transactions else []

                data = response.json()
                if not isinstance(data, list):
                    print(f"[HELIUS] Invalid response (expected list)")
                    return all_transactions if all_transactions else []

                if not data:
                    print(f"[HELIUS] Page {page_num + 1}: No more transactions")
                    return all_transactions

                print(f"[HELIUS] Page {page_num + 1}: Got {len(data)} transactions", flush=True)
                all_transactions.extend(data)
                pages_fetched += 1

                # Early termination if fewer results than limit
                if len(data) < limit:
                    print(f"[HELIUS] Reached end (got {len(data)} < {limit})")
                    return all_transactions

                # Prepare cursor for next page
                current_before = data[-1].get("signature")
                if not current_before:
                    print(f"[HELIUS] No signature in last transaction, stopping")
                    return all_transactions

                break  # Success, exit retry loop

            except requests.Timeout:
                latency_ms = (time.time() - request_start) * 1000
                print(f"[HELIUS] Timeout (attempt {attempt + 1}/{retries})", flush=True)

                record_request(
                    section="funder_incoming",
                    provider="helius_enhanced",
                    method="helius_enhanced_addresses_transactions",
                    status_code=504,
                    latency_ms=latency_ms,
                    mode="realtime",
                    retries=attempt,
                    error="Timeout",
                )

                sleep_time = 0.5 * (2 ** attempt)
                time.sleep(sleep_time)
                attempt += 1

            except requests.ConnectionError as e:
                latency_ms = (time.time() - request_start) * 1000
                print(f"[HELIUS] Connection error: {e} (attempt {attempt + 1}/{retries})", flush=True)

                record_request(
                    section="funder_incoming",
                    provider="helius_enhanced",
                    method="helius_enhanced_addresses_transactions",
                    status_code=500,
                    latency_ms=latency_ms,
                    mode="realtime",
                    retries=attempt,
                    error=str(e),
                )

                sleep_time = 0.5 * (2 ** attempt)
                time.sleep(sleep_time)
                attempt += 1

            except Exception as e:
                latency_ms = (time.time() - request_start) * 1000
                print(f"[HELIUS] Unexpected error: {e}")

                record_request(
                    section="funder_incoming",
                    provider="helius_enhanced",
                    method="helius_enhanced_addresses_transactions",
                    status_code=500,
                    latency_ms=latency_ms,
                    mode="realtime",
                    retries=attempt,
                    error=str(e),
                )

                return all_transactions if all_transactions else []

        # If we exhausted retries
        if attempt >= retries:
            print(f"[HELIUS] Exhausted retries for page {page_num + 1}")
            break

    print(f"[HELIUS] Total: Fetched {len(all_transactions)} transactions across {pages_fetched} pages", flush=True)
    return all_transactions
```

---

## Running the Metrics API

### Option 1: Standalone Server

```bash
python -m rpc_metrics_api
# Server runs on http://localhost:8001
# Dashboard: http://localhost:8001/dashboard
# API: http://localhost:8001/metrics/rpc
```

### Option 2: Integrated with Main Flask App

```python
# In main.py startup:
import threading
from rpc_metrics_api import app as metrics_app
import uvicorn

# Run metrics API in background thread
def run_metrics_server():
    uvicorn.run(metrics_app, host="0.0.0.0", port=8001, log_level="warning")

metrics_thread = threading.Thread(target=run_metrics_server, daemon=True)
metrics_thread.start()
```

### Option 3: Docker

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY rpc_metrics_recorder.py .
COPY rpc_metrics_api.py .
COPY requirements.txt .

RUN pip install -r requirements.txt

EXPOSE 8001

CMD ["python", "-m", "rpc_metrics_api"]
```

---

## API Endpoints Reference

### GET /metrics/rpc
Full metrics with summary, sections, top methods, and alerts.

**Response**:
```json
{
  "timestamp": "2026-03-01T10:30:45.123456",
  "summary": {
    "uptime_minutes": 125.5,
    "credits_today": 45230,
    "credits_total": 45230,
    "credits_monthly_estimate": 1356900,
    "credits_monthly_remaining": null,
    "credits_burn_rate_per_minute": 360.5,
    "requests_total": 1204,
    "errors_total": 12,
    "rate_limits_total": 2,
    "sections_active": 3
  },
  "sections": {
    "funder_incoming": {
      "credits": 35000,
      "requests": 350,
      "errors": 5,
      "rate_limits_429": 1,
      "avg_latency_ms": 245.3,
      "p95_latency_ms": 890.2,
      "top_methods": [
        {"method": "helius_enhanced_addresses_transactions", "credits": 35000}
      ]
    },
    "listener": {
      "credits": 8000,
      "requests": 8,
      "errors": 2,
      "rate_limits_429": 0,
      "avg_latency_ms": 125.5,
      "p95_latency_ms": 200.0,
      "top_methods": []
    },
    "ui_api": {
      "credits": 2230,
      "requests": 846,
      "errors": 5,
      "rate_limits_429": 1,
      "avg_latency_ms": 150.2,
      "p95_latency_ms": 450.1,
      "top_methods": [
        {"method": "getTransaction", "credits": 1200},
        {"method": "getSignaturesForAddress", "credits": 1030}
      ]
    }
  },
  "top_methods": [
    {"method": "helius_enhanced_addresses_transactions", "credits": 35000, "requests": 350},
    {"method": "getTransaction", "credits": 1200, "requests": 120},
    {"method": "getSignaturesForAddress", "credits": 1030, "requests": 103}
  ],
  "alerts": []
}
```

### GET /metrics/rpc/summary
Quick summary (for lightweight polling).

### GET /metrics/rpc/sections
Per-section breakdown only.

### GET /metrics/rpc/methods?limit=10
Top methods by credits (limit configurable).

### GET /metrics/rpc/alerts?burn_rate_threshold=100.0
Check for active alerts (budget, burn rate, errors).

### POST /metrics/rpc/reset?admin_token=SECRET_ADMIN_TOKEN
Reset daily counters.

---

## Example: Cost Governor Implementation

Once you have the recorder in place, implement cost controls:

```python
# In a background task, check metrics periodically:
import asyncio
from rpc_metrics_recorder import get_recorder

async def cost_governor():
    """Adjust system parameters based on credit burn rate"""
    while True:
        try:
            recorder = get_recorder()
            summary = recorder.get_summary()
            burn_rate = summary["credits_burn_rate_per_minute"]

            if burn_rate > 500:  # Threshold
                print("[COST_GOVERNOR] High burn rate detected, reducing funder scan depth", flush=True)
                # Reduce funder_incoming max_pages: 5 → 1
                # Reduce concurrent funder analysis
                os.environ["FUNDER_SCAN_MAX_PAGES"] = "1"
                os.environ["FUNDER_SCAN_CONCURRENCY"] = "2"

            elif burn_rate < 200:  # Back to normal
                print("[COST_GOVERNOR] Burn rate normalized, restoring scan depth", flush=True)
                os.environ["FUNDER_SCAN_MAX_PAGES"] = "5"
                os.environ["FUNDER_SCAN_CONCURRENCY"] = "10"

        except Exception as e:
            print(f"[COST_GOVERNOR] Error: {e}")

        await asyncio.sleep(60)  # Check every minute

# Start in background
asyncio.create_task(cost_governor())
```

---

## Troubleshooting

**Q: Metrics not recording?**
- Ensure `initialize_recorder()` is called at startup
- Ensure `record_request()` is imported and called in RPC wrappers
- Check Flask logs for errors

**Q: Dashboard shows 0 credits?**
- Verify that RPC calls are being made and `record_request()` is being called
- Check that methods are in `CREDIT_SCHEDULE`
- Add debug logging: `print(f"[METRICS] Recorded {credits} credits")`

**Q: API returns 403 Unauthorized on reset?**
- Replace `"SECRET_ADMIN_TOKEN"` with your actual admin token
- Or disable the reset endpoint for now by removing `admin_token` check

**Q: High latency metrics?**
- Verify that `time.time()` calls bracket the actual request
- Check network conditions and RPC provider health
- Consider increasing request timeout

---

## Next Steps

1. Integrate into `funder_helius_extractor.py` (highest credit usage)
2. Integrate into `realtime_creator_funding_extractor.py`
3. Add `record_request()` calls to any custom RPC wrapper
4. Deploy metrics API alongside Flask app
5. Monitor dashboard and adjust `CREDIT_SCHEDULE` based on actual Helius plan
6. Implement cost governor circuit-breaker for budget protection
