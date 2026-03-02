# Managing Creator Outgoing Scan Growth (1345 creators, ~150 new/day) – Doc + Claude Prompt

**Context:** FLEX has ~1,345 total creators and is adding ~150 creators/day.  
**Problem:** If `creator_outgoing_scan` scans every creator at a fixed cadence, workload grows unbounded and appears “exponential.”  
**Goal:** Make background scanning scale with **new activity + budget**, not with total creators.

---

## 1) The core principle

### Make cost proportional to change
A creator should only “consume scan work” when:
- it is **new** (hot window), or
- it shows **new activity**, or
- it has **high priority** (risk/signal)

---

## 2) Required state per creator (scan cursor + change detection)

Add / ensure these fields exist in the scan state:

- `creator_address` (PK)
- `before_signature` (cursor for progressive deepening)
- `last_head_signature` (newest signature from last scan)
- `last_scan_time`
- `next_scan_time` (for tiered scheduling)
- `tier` (`hot|warm|cold`)
- `updated_at`

### Why `last_head_signature` matters
On each scan:
1. Fetch 1 page of signatures.
2. If the **first signature** equals `last_head_signature`, there is no new work → stop immediately.
3. Otherwise, update `last_head_signature` and proceed (up to page cap).

This changes scaling from “scan everything” to “scan only what changed.”

---

## 3) Tiered cadence (hot / warm / cold)

Given ~150 new creators/day:

Approximate population sizes:
- **Hot (≤24h):** ~150 creators
- **Warm (1–7d):** ~900 creators
- **Cold (>7d):** ~295 creators

### Suggested cadences (background, not speed-critical)
- **Hot:** every **2 hours**, `MAX_PAGES_PER_CYCLE=2`
- **Warm:** every **12 hours**, `MAX_PAGES_PER_CYCLE=1`
- **Cold:** every **7 days**, `MAX_PAGES_PER_CYCLE=1`

> These cadences are safe starting points. Because you also short-circuit on `last_head_signature`, real usage will usually be lower.

---

## 4) Budget gating (optional but recommended even for monitoring-only)

Even if you don’t know the budget yet, implement a budget gate so the system can never “run away”:

- `OUTGOING_DAILY_BUDGET_CREDITS` (config/env, optional)
- Each `getSignaturesForAddress` request costs **10 credits** (Helius schedule).
- Decrement remaining daily budget per request.
- Stop scanning when budget <= 0; resume next cycle/day.

This provides a hard ceiling without implementing an automatic “governor.”

---

## 5) Work slicing (prevents burst work)

Instead of “scan all creators every 12h,” run continuously but bounded:

- Every minute, process up to **K creators** whose `next_scan_time <= now`
- Use your existing **RateLimiter** (RPS) and low **concurrency**
- Recompute `next_scan_time` after scan based on tier + activity

This creates stable load as the creator set grows.

---

## 6) Priority (scan the most valuable creators first)

When the queue is long (or budget is tight), rank creators by `priority_score`:

Suggested inputs:
- + recent creator (new)
- + suspicious network / high risk tags
- + large funding amount
- + previous malicious links
- - dormant creators
- - known benign patterns (optional)

The queue pops highest-priority creators first.

---

## 7) Implementation checklist (minimal changes)

1) Extend SQLite schema:
   - Add `last_head_signature` and `next_scan_time` to your cursor/state table.
2) Add helper functions:
   - `get_due_creators(limit=K)`
   - `update_creator_scan_state(...)`
3) Update scan loop:
   - Determine tier based on `first_seen_time` or creator age.
   - Apply tier cadence.
   - Apply `MAX_PAGES_PER_CYCLE` based on tier.
   - Short-circuit if head signature unchanged.
4) Add optional daily credit budget:
   - `remaining_credits_today()`
   - decrement per request
   - stop when depleted

---

# Claude Prompt (copy/paste)

```text
I have a background job `creator_outgoing_scan` in a Python async system (FLEX).
It scans creator addresses and uses `getSignaturesForAddress` (Helius RPC; 10 credits/request).
Creators:
- total creators: 1345
- new creators/day: ~150
The scan is not speed-critical; I want it efficient and scalable.

I already implemented rate limiting + backoff per request (see CREATOR_OUTGOING_SCAN_EFFICIENCY_PATCH.md).
Now I want to prevent growth blowups as creator count increases.

Implement the following architecture:

1) Per-creator scan state stored in SQLite:
   - creator_address (PK)
   - before_signature (cursor)
   - last_head_signature (newest signature from last scan)
   - last_scan_time
   - next_scan_time
   - tier (hot|warm|cold)
   - updated_at
Provide SQL schema and migration.

2) Tiered cadence:
   - hot (<=24h): every 2 hours, MAX_PAGES_PER_CYCLE=2
   - warm (1-7d): every 12 hours, MAX_PAGES_PER_CYCLE=1
   - cold (>7d): every 7 days, MAX_PAGES_PER_CYCLE=1

3) Change detection:
   - fetch 1 page
   - if first signature == last_head_signature -> stop immediately (no new work)
   - else update last_head_signature and proceed (up to page cap)

4) Work slicing:
   - implement `get_due_creators(K)` and process only due creators per tick
   - schedule a loop that runs every 60s and processes up to K creators

5) Optional budget gate (even for monitoring-only):
   - config OUTGOING_DAILY_BUDGET_CREDITS (nullable)
   - each request costs 10 credits
   - stop scanning when daily budget exhausted

Output:
- Production-ready Python code (SQLite helpers + scheduler loop + scan logic)
- Clear function boundaries (db.py, outgoing_scan.py)
- No governors/auto-throttling beyond rate limiter already in place
- Keep existing record_request instrumentation

Do NOT summarize. Provide code only.
```

---

## Notes
This approach makes cost scale with **new activity**, not total creators, and gives you a safety ceiling even before you finalize a budget.
