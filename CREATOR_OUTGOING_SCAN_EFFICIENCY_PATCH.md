# Creator Outgoing Scan – Efficiency Patch (Background, Not Speed-Critical)

This doc shows **exact edits** to make `creator_outgoing_scan` efficient (low 429s, predictable burn) while keeping it purely a background monitor.

You shared the current function below (instrumented), which is a good start but still **bursts** and **does not back off** on 429s:

```python
async def rpc_get_signatures(session: aiohttp.ClientSession, address: str, limit: int = 25) -> List[dict]:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignaturesForAddress",
        "params": [address, {"limit": limit}]
    }
    try:
        start_time = time.time()
        async with session.post(RPC_HTTP, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            latency_ms = (time.time() - start_time) * 1000
            record_request(...)
            if resp.status != 200:
                return []
            data = await resp.json()
            return data.get("result") or []
    except Exception as e:
        record_request(...)
        return []
```

---

## 1) What’s causing 429s (even with concurrency caps)

- A semaphore limits **in-flight** requests, but you can still send bursts (many fast 200/429 responses per second).
- No **requests/sec** shaping (token bucket / rate limiter).
- No 429 **Retry-After** handling.
- No exponential backoff + jitter to avoid thundering herds.
- No pagination/cursor strategy, so scans often re-hit similar hot ranges.

Because this is not speed-critical, we optimize for:
- **0–5% 429s**
- **stable credits/min**
- **predictable runtime**
- **no burst spikes on restart**

---

## 2) Target behavior (recommended settings)

### Background scan defaults
- `REQ_PER_SEC`: **5–10** (start at 8)
- `CONCURRENCY`: **2–3**
- `limit`: **25** signatures per page (fine)
- `max_pages`: **2** per creator per cycle (progressive deepening)
- Respect `Retry-After` and back off

---

## 3) Drop-in building blocks

### A) A tiny async rate limiter (no dependencies)
```python
import asyncio, time

class RateLimiter:
    def __init__(self, rate_per_sec: float):
        self._interval = 1.0 / max(rate_per_sec, 0.1)
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            if now < self._next:
                await asyncio.sleep(self._next - now)
            self._next = max(self._next + self._interval, time.monotonic() + self._interval)
```

### B) Backoff helper (uses Retry-After if present)
```python
import random

async def sleep_backoff(attempt: int, retry_after_s: float | None):
    if retry_after_s is not None:
        await asyncio.sleep(retry_after_s + random.uniform(0, 0.25))
    else:
        await asyncio.sleep(min(2 ** attempt, 30) + random.uniform(0, 0.25))
```

---

## 4) Patch: rpc_get_signatures with efficiency mode

### What’s new
- **rate limiter**: smooth request rate
- **retries**: limited, with backoff
- **Retry-After**: respected
- **jitter**: prevents synchronized retry storms
- **`before` cursor** support (needed for max_pages pagination)

```python
from typing import List, Optional
import aiohttp, time

# Configure these in your module/config
OUTGOING_RPS = 8.0
OUTGOING_MAX_RETRIES = 3

outgoing_limiter = RateLimiter(OUTGOING_RPS)

async def rpc_get_signatures(
    session: aiohttp.ClientSession,
    address: str,
    *,
    limit: int = 25,
    before: Optional[str] = None,
    source_file: str = "creator_outgoing_extractor",
) -> List[dict]:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignaturesForAddress",
        "params": [address, {"limit": limit, **({"before": before} if before else {})}],
    }

    for attempt in range(OUTGOING_MAX_RETRIES + 1):
        await outgoing_limiter.acquire()
        start_time = time.time()

        try:
            async with session.post(
                RPC_HTTP,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                latency_ms = (time.time() - start_time) * 1000

                retry_after_hdr = resp.headers.get("Retry-After")
                retry_after_s = float(retry_after_hdr) if retry_after_hdr else None

                record_request(
                    section="creator_outgoing_scan",
                    provider="helius_rpc",
                    method="getSignaturesForAddress",
                    status_code=resp.status,
                    latency_ms=latency_ms,
                    mode="background",
                    source_file=source_file,
                    retries=attempt,
                    retry_after_ms=(retry_after_s * 1000) if retry_after_s else None,
                )

                # Success
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("result") or []

                # Rate limit: backoff and retry
                if resp.status == 429 and attempt < OUTGOING_MAX_RETRIES:
                    await sleep_backoff(attempt, retry_after_s)
                    continue

                # Non-200, non-429: don't spam retries
                return []

        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            record_request(
                section="creator_outgoing_scan",
                provider="helius_rpc",
                method="getSignaturesForAddress",
                status_code=0,
                latency_ms=latency_ms,
                mode="background",
                source_file=source_file,
                retries=attempt,
                error=str(e),
            )
            if attempt < OUTGOING_MAX_RETRIES:
                await sleep_backoff(attempt, None)
                continue
            return []

    return []
```

**Result:** A single function change can reduce “burst 429 storms” to rare events.

---

## 5) Pagination strategy (max_pages + cursor)

To prevent re-scanning and to spread work across cycles:

### A) Add a per-creator cursor (SQLite table)
- `creator_outgoing_cursor(creator_address PRIMARY KEY, before_signature TEXT, updated_at TIMESTAMP)`

### B) Each scan cycle, do only N pages per creator
```python
MAX_PAGES_PER_CYCLE = 2

before = load_before_cursor(creator_address)  # from SQLite
all_sigs = []

for _ in range(MAX_PAGES_PER_CYCLE):
    sigs = await rpc_get_signatures(session, creator_address, limit=25, before=before)
    if not sigs:
        break
    all_sigs.extend(sigs)
    before = sigs[-1]["signature"]  # last signature becomes cursor

save_before_cursor(creator_address, before)
```

This cuts load instantly vs doing `max_pages=5` every run, and makes the scan **incremental**.

---

## 6) Practical tuning checklist

If you still see 429s:
1) Reduce `OUTGOING_RPS` from 8 → 5
2) Reduce `CONCURRENCY` to 2
3) Reduce `MAX_PAGES_PER_CYCLE` to 1

If you want faster completion:
- increase RPS gradually, watching 429% and credits/min.

---

## 7) Why this is “efficient” (even if slower)

- Fewer retries → fewer wasted requests and less noisy metrics
- Smooth request rate → avoids exceeding short-window limits
- Cursor pagination → avoids re-downloading the same history repeatedly
- Progressive deepening → spreads cost across time while still covering history

---

## 8) Integration notes (minimal changes)

1) Drop `RateLimiter` + `sleep_backoff` into `creator_outgoing_extractor.py`.
2) Replace your `rpc_get_signatures` with the patched version above.
3) Ensure your `record_request(...)` supports optional fields:
   - `retries`
   - `retry_after_ms`
4) In the creator loop, add `before` cursor persistence + `MAX_PAGES_PER_CYCLE`.

That’s it.

If you want, paste your creator loop (where `rpc_get_signatures` is called) and I’ll write the exact cursor-table SQL + helper functions to drop in.
