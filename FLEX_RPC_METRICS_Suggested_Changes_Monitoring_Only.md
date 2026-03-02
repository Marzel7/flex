# FLEX RPC Metrics Dashboard – Suggested Changes (Monitoring Only)

**Date:** 2026-03-02  
**Scope:** Monitoring + observability improvements only (no automatic cost governor behavior).  
**Based on:** Current production tracking summary and dashboard behavior. fileciteturn6file0

---

## 1) What the dashboard is telling you right now

From the latest snapshot: fileciteturn6file0

- **creator_outgoing_scan** is generating the vast majority of requests.
- **HTTP 429 rate limiting is extremely high** (923/1017 requests ≈ 90.8%).
- **Burn rate spikes** during background scans (e.g., ~303 credits/min).
- **Source file attribution shows “unknown”** for many calls because some processes were not restarted after instrumentation changes.
- There is a **small credit mismatch** vs Helius dashboard (+27 credits) due to uninstrumented endpoints/streaming/old processes.

This is excellent visibility — but it also highlights a few monitoring-focused improvements to make the metrics more accurate and actionable.

---

## 2) Suggested changes (Monitoring-only)

### A) Restart processes to eliminate “unknown” source attribution
**Issue:** “unknown” is a key signal that an old process is still running without updated instrumentation. fileciteturn6file0

**Change (operational):**
- Restart `pumpfun_curve_listener`
- Restart `creator_outgoing_extractor` background task runner
- Restart Flask `main.py` process (if it makes RPC calls)
- Confirm source file breakdown shows real module names instead of “unknown”.

**Acceptance check:**
- `GET /metrics/rpc/source-files` should show the expected Python modules (no large “unknown” bucket). fileciteturn6file0

---

### B) Fix remaining uninstrumented Flask endpoints (eliminate the +27 credit drift)
**Issue:** Two endpoints are listed as not instrumented:  
- `/api/validate-transaction`  
- `/api/transaction/<signature>` fileciteturn6file0

**Change (code):**
- Wrap any RPC/Helius calls inside those endpoints with `record_request(...)` and set:
  - `section="ui_api"`
  - `source_file="main"` (or the actual module)
  - method tag matching your call (`getTransaction`, etc.)

**Acceptance check:**
- Credit mismatch vs Helius shrinks (ideally to near-zero aside from streaming).
- Those requests appear under `ui_api` in per-section stats.

---

### C) Make the dashboard explicitly separate “failures” vs “credits”
**Issue:** With heavy 429s, a reader may assume all failed calls consumed credits.
Depending on provider/accounting, retries/429 handling may or may not be billable.

**Change (UI + API schema):**
Add explicit fields in the JSON and dashboard:
- `credits_success_only`
- `credits_all_requests`
- `requests_success`
- `requests_failed`
- `requests_429`

This makes analysis unambiguous when error rates spike. fileciteturn6file0

**Acceptance check:**
- During a 429 storm, dashboard still communicates *true* cost vs *wasted* attempt volume.

---

### D) Add a “Retry view” (monitoring-only)
**Issue:** A large amount of burn-rate volatility comes from retries, but dashboards often hide retry behavior inside error rate.

**Change:**
Track and display:
- `retries_total` per section/method
- `avg_retries_per_request`
- `top_methods_by_retries`

**Acceptance check:**
- You can immediately identify whether a section is expensive because of raw workload, or because it’s retrying too aggressively.

---

### E) Improve 429 diagnostics (monitoring-only)
**Issue:** You know you’re getting 429s, but not *why* (bursting vs steady over-limit).

**Change:**
Record extra metadata when status=429:
- `retry_after_ms` (parsed from header when present)
- `attempt_number`
- `provider_rate_limit_bucket` (if you have multiple providers/keys)
- optional: `queue_depth` / `in_flight` when recorded

**Acceptance check:**
- You can distinguish “bursts” from “sustained over-limit” in logs and dashboard.

---

### F) Align section naming across the codebase (prevents split buckets)
**Issue:** Monitoring systems lose accuracy when different modules use slightly different section tags.

**Change:**
Enforce an enum/constant list:
- `listener`
- `creator_funding`
- `funder_incoming`
- `creator_outgoing_scan`
- `ui_api`
- `background_enrichment` fileciteturn6file0

**Acceptance check:**
- No unexpected new section names appear in the dashboard.

---

## 3) Suggested low-risk “operational tuning” (optional, still monitoring-oriented)

Even if you do *not* want automatic cost control, it’s reasonable to reduce noise in monitoring by preventing a background job from creating a wall of 429s.

These are manual configuration changes (not an automatic governor):

- Reduce `creator_outgoing_extractor` concurrency (e.g., 10 → 3–5).
- Respect `Retry-After` when present.
- Add exponential backoff on 429.

**Why include this here?**  
Because extreme 429 rates can distort your visibility (you mostly see failures rather than useful signal), and can create confusing “burn spikes.” fileciteturn6file0

---

## 4) Monitoring acceptance checklist (copy/paste)

**After changes, confirm:**

1. **Source file breakdown**
   - `unknown` bucket is small or zero. fileciteturn6file0

2. **Credit reconciliation**
   - FLEX ≈ Helius daily usage (streaming aside). fileciteturn6file0

3. **UI clarity**
   - Credits are shown separately for success vs all attempts.

4. **Retry visibility**
   - Top retries list exists; 429 storms clearly attributable.

5. **Consistent taxonomy**
   - Only the expected 6 sections appear.

---

## 5) Notes on “monitoring-only” philosophy

Everything above improves:
- accuracy of attribution (by section + source file),
- reconciliation vs provider billing,
- interpretability during rate-limit events,

without enforcing automatic throttling decisions.

---

**Reference:** RPC Metrics Tracking & Source File Monitoring summary. fileciteturn6file0
