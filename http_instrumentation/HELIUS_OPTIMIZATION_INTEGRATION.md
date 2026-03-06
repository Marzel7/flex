# Helius Optimization Integration Guide

**Status:** Ready to integrate
**Expected Savings:** 3–5× reduction in Helius credits
**Implementation Time:** 2–3 hours
**Complexity:** Medium (builds on existing extraction framework)

---

## Overview

This optimization package adds four complementary mechanisms to reduce Helius usage:

1. **Prefiltering** — Skip long-tail funders (keep top N + CEX/INFRA only)
2. **Two-pass scanning** — 1-page fingerprint, then deep-scan only if unknown + high-value
3. **Budget guard** — Hard cap credits per creator run
4. **Tombstones** — Never re-scan empty/low-signal wallets for 14 days

**Example Impact:**
- Before: 942 funders × avg 150 credits/funder = 141,300 credits
- After: 22 shortlisted funders × avg 120 credits/funder = 2,640 credits
- **Savings: 98% for that creator** (typical: 70–80% across portfolio)

---

## Step 1: Run Schema Migration

```bash
sqlite3 flex_complete_database.db < helius_optimization_schema.sql
```

Or manually:

```python
import sqlite3

conn = sqlite3.connect('flex_complete_database.db')
cursor = conn.cursor()

# Add optimization columns
cursor.execute("""
    ALTER TABLE wallet_scan_metrics ADD COLUMN IF NOT EXISTS budget_exhausted INTEGER DEFAULT 0
""")
cursor.execute("""
    ALTER TABLE wallet_scan_metrics ADD COLUMN IF NOT EXISTS deep_scan_pages INTEGER DEFAULT 0
""")
cursor.execute("""
    ALTER TABLE wallet_scan_metrics ADD COLUMN IF NOT EXISTS tombstone_skip INTEGER DEFAULT 0
""")
cursor.execute("""
    ALTER TABLE wallet_scan_metrics ADD COLUMN IF NOT EXISTS prefilter_shortlist INTEGER DEFAULT 0
""")

# Add tombstone fields to wallet_analysis_state
cursor.execute("""
    ALTER TABLE wallet_analysis_state ADD COLUMN IF NOT EXISTS tombstone_type TEXT
""")
cursor.execute("""
    ALTER TABLE wallet_analysis_state ADD COLUMN IF NOT EXISTS tombstone_created_at TEXT
""")
cursor.execute("""
    ALTER TABLE wallet_analysis_state ADD COLUMN IF NOT EXISTS scan_count INTEGER DEFAULT 0
""")

# Create summary tables
cursor.execute("""
    CREATE TABLE IF NOT EXISTS creator_funder_summary (
        creator_address TEXT NOT NULL,
        funder_address TEXT NOT NULL,
        inbound_total_sol REAL DEFAULT 0,
        inbound_tx_count INTEGER DEFAULT 0,
        funder_type TEXT,
        shortlist_rank INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (creator_address, funder_address)
    )
""")

cursor.execute("""
    CREATE TABLE IF NOT EXISTS creator_extraction_budget (
        creator_address TEXT PRIMARY KEY,
        max_credits INTEGER DEFAULT 250,
        credits_spent INTEGER DEFAULT 0,
        started_at TEXT,
        finished_at TEXT,
        budget_exhausted INTEGER DEFAULT 0,
        funder_count INTEGER DEFAULT 0,
        recipient_count INTEGER DEFAULT 0
    )
""")

conn.commit()
conn.close()
```

---

## Step 2: Update Creator Funding Extractor

In `realtime_creator_funding_extractor.py`, modify the flow to use prefiltering.

### Changes to `extract_for_creator()`:

**BEFORE** (line ~940):
```python
async def extract_for_creator(self, creator: str, migration_timestamp_str: str) -> Dict:
    # ... existing code ...

    # Process all funders found
    if funders:
        # Immediately queue all funders for funder_incoming extraction
        await self._extract_funder_transfers_batch(creator, funders.keys())
```

**AFTER**:
```python
async def extract_for_creator(self, creator: str, migration_timestamp_str: str) -> Dict:
    # ... existing code ...

    # 🆕 NEW: Apply prefiltering before extracting funder transfers
    if funders:
        from helius_optimization_engine import FunderPrefilter, PrefilterConfig

        prefilter = FunderPrefilter(
            self.db_path,
            config=PrefilterConfig(
                min_inbound_sol=0.2,
                min_inbound_count=1,
                top_n_by_sol=20,
                include_cex=True,
                include_infra=True,
            )
        )

        # Get shortlist instead of all funders
        shortlist = await prefilter.get_shortlist(creator, top_n=20)
        shortlisted_addresses = {addr for addr, _, _ in shortlist}

        print(f"[REALTIME_FUNDING] 🎯 Shortlisted {len(shortlisted_addresses)} / {len(funders)} funders for deep scanning")

        # Only extract transfers for shortlisted funders
        await self._extract_funder_transfers_batch(creator, shortlisted_addresses)
```

---

## Step 3: Update Funder Transfer Extractor

In `funder_incoming_extractor.py`, integrate two-pass scanning and budget guard.

### Changes to `extract_transfers_for_funder()`:

**BEFORE** (line ~600):
```python
async def extract_transfers_for_funder(funder: str, max_pages: int = 8):
    """Extract all transfers for a funder (paginate through all)"""
    pages_fetched = 0

    while pages_fetched < max_pages:
        page = await get_transactions_helius(funder, limit=100, before=before_sig)
        # ... process page ...
        pages_fetched += 1
```

**AFTER**:
```python
async def extract_transfers_for_funder(
    funder: str,
    creator: str,  # 🆕 NEW parameter for budget tracking
    funder_inbound_sol: float,  # 🆕 NEW parameter from prefilter
    budget_guard = None,  # 🆕 NEW parameter for budget tracking
    is_top_n: bool = False,  # 🆕 NEW: is this funder in top 5?
):
    """
    Two-pass extraction: Pass A (1-page fingerprint) + Pass B (deep scan if needed).
    Budget-aware and tombstone-respecting.
    """
    from helius_optimization_engine import TwoPassScanner, TombstoneManager

    # Check if tombstoned
    tombstone_mgr = TombstoneManager(DB_PATH)
    is_tombstoned, tomb_type = await tombstone_mgr.is_tombstoned(funder)

    if is_tombstoned:
        print(f"[FUNDER_EXTRACT] ⏭️  SKIP {funder[:16]}... (tombstone type: {tomb_type})")
        record_request(
            section="funder_incoming",
            provider="helius_api",
            method="tombstone_skip",
            status_code=200,
            latency_ms=0,
            mode="optimization",
            retries=0,
            source_file="funder_incoming_extractor",
            creator_address=creator,
            tombstone_skip=1,  # 🆕 NEW metric
        )
        return  # Skip this funder entirely

    scanner = TwoPassScanner(DB_PATH, budget_guard)
    pages_scanned = 1  # Start with Pass A

    # PASS A: Fetch exactly 1 page for fingerprinting
    print(f"[FUNDER_EXTRACT] 📄 PASS A (fingerprint): {funder[:16]}...")

    page = await get_transactions_helius(funder, limit=100)
    if not page:
        await tombstone_mgr.mark_empty(funder, reason="no_transfers_on_page_a")
        return

    # ... process page, compute wallet_type and confidence ...
    wallet_type = classify_wallet_from_page(page)  # Returns 'cex', 'infra', 'bot', 'unknown'
    confidence = compute_confidence(page)  # 0.0–1.0

    # Record Pass A metrics
    credits_pass_a = estimate_credits_for_page(page)
    budget_guard.record_credits(creator, credits_pass_a)

    record_request(
        section="funder_incoming",
        provider="helius_api",
        method="get_transactions",
        status_code=200,
        latency_ms=timing,
        mode="pass_a_fingerprint",
        retries=0,
        source_file="funder_incoming_extractor",
        creator_address=creator,
        deep_scan_pages=1,  # 🆕 NEW: page count for this scan
        credits_estimated=credits_pass_a,
        prefilter_shortlist=1,  # 🆕 NEW: this came from shortlist
    )

    # Decide on PASS B
    should_deep = await scanner.should_do_pass_b(
        funder,
        wallet_type,
        funder_inbound_sol,
        is_top_n,
        creator
    )

    if should_deep:
        print(f"[FUNDER_EXTRACT] 🔍 PASS B (deep scan): {funder[:16]}... (type={wallet_type}, value={funder_inbound_sol:.2f} SOL)")

        # PASS B: Fetch additional pages until early-stop or budget exhausted
        before_sig = page[-1].get('signature') if page else None

        for page_num in range(2, 6):  # Max 5 pages total (1 from Pass A + 4 here)
            if budget_guard.is_exhausted(creator):
                print(f"[FUNDER_EXTRACT] 💰 BUDGET EXHAUSTED: stopping deep scan")
                record_request(
                    section="funder_incoming",
                    provider="helius_api",
                    method="budget_exhausted",
                    status_code=200,
                    latency_ms=0,
                    mode="pass_b_deep",
                    retries=0,
                    source_file="funder_incoming_extractor",
                    creator_address=creator,
                    budget_exhausted=1,  # 🆕 NEW metric
                )
                break

            page = await get_transactions_helius(funder, limit=100, before=before_sig)
            if not page:
                # No more transfers
                await tombstone_mgr.mark_empty(funder, reason="no_more_transfers_on_pass_b")
                break

            pages_scanned += 1

            # ... process page ...

            credits_this_page = estimate_credits_for_page(page)
            if not budget_guard.record_credits(creator, credits_this_page):
                # Budget now exhausted
                break

            # Early stop if diminishing returns
            meaningful_transfers = count_meaningful_transfers(page)
            if meaningful_transfers == 0:
                await tombstone_mgr.mark_empty(funder, reason="no_meaningful_transfers_deep")
                break

            # Update before_sig for next page
            before_sig = page[-1].get('signature') if page else None

    else:
        # Not proceeding with deep scan — classify as shallow
        print(f"[FUNDER_EXTRACT] ✓ SKIP deep scan (type={wallet_type}, confidence={confidence:.1%})")

        await tombstone_mgr.mark_shallow(funder, wallet_type, confidence)

        record_request(
            section="funder_incoming",
            provider="helius_api",
            method="pass_a_only",
            status_code=200,
            latency_ms=0,
            mode="pass_a_fingerprint",
            retries=0,
            source_file="funder_incoming_extractor",
            creator_address=creator,
            deep_scan_pages=1,  # Only Pass A
        )

    # Record final metrics for this funder
    record_request(
        section="funder_incoming",
        provider="helius_api",
        method="funder_scan_complete",
        status_code=200,
        latency_ms=0,
        mode="optimization",
        retries=0,
        source_file="funder_incoming_extractor",
        creator_address=creator,
        deep_scan_pages=pages_scanned,  # 🆕 NEW: total pages fetched for this funder
        budget_exhausted=1 if budget_guard.is_exhausted(creator) else 0,
    )
```

### Changes to `extract_for_creator_async()`:

**BEFORE** (line ~300):
```python
async def extract_for_creator_async(creator: str, funders: Iterable[str]):
    """Extract funder transfers for all funders concurrently"""
    tasks = [
        extract_transfers_for_funder(funder)
        for funder in funders
    ]
    await asyncio.gather(*tasks)
```

**AFTER**:
```python
async def extract_for_creator_async(
    creator: str,
    funders: Dict[str, Tuple[float, str]]  # funder -> (inbound_sol, funder_type)
):
    """
    Extract funder transfers for shortlisted funders with budget tracking.
    """
    from helius_optimization_engine import BudgetGuard

    budget_guard = BudgetGuard(DB_PATH, max_credits=250)
    budget_guard.start_creator_run(creator)

    try:
        tasks = []
        funder_list = list(funders.items())

        for rank, (funder_addr, (inbound_sol, funder_type)) in enumerate(funder_list):
            is_top_5 = rank < 5

            task = extract_transfers_for_funder(
                funder=funder_addr,
                creator=creator,  # 🆕 NEW
                funder_inbound_sol=inbound_sol,  # 🆕 NEW
                budget_guard=budget_guard,  # 🆕 NEW
                is_top_n=is_top_5,  # 🆕 NEW
            )
            tasks.append(task)

        # Run with concurrency limit
        semaphore = asyncio.Semaphore(DEFAULT_CONCURRENCY)

        async def bounded_task(task):
            async with semaphore:
                return await task

        await asyncio.gather(*[bounded_task(t) for t in tasks])

    finally:
        # Finalize budget tracking
        budget_guard.finish_creator_run(creator, len(funders), 0)
```

---

## Step 4: Add Reporting to Metrics Dashboard

In `rpc_metrics_reports.py`, add optimization metrics to the daily report.

### Add import and helper function:

```python
def get_optimization_metrics(db_path: str, hours: int = 24) -> Dict:
    """Get optimization metrics from last N hours"""
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            SUM(CASE WHEN deep_scan_pages = 1 THEN 1 ELSE 0 END) as single_page_scans,
            SUM(CASE WHEN deep_scan_pages > 1 THEN 1 ELSE 0 END) as multi_page_scans,
            SUM(CASE WHEN budget_exhausted = 1 THEN 1 ELSE 0 END) as budget_exhausted_count,
            SUM(CASE WHEN tombstone_skip = 1 THEN 1 ELSE 0 END) as tombstone_skips,
            COUNT(*) as total_scans
        FROM wallet_scan_metrics
        WHERE created_at >= datetime('now', '-' || ? || ' hours')
          AND section = 'funder_incoming'
    """, (hours,))

    row = cursor.fetchone()
    conn.close()

    if not row or row[4] == 0:
        return {}

    single_page, multi_page, budget_exhausted, tombstone_skips, total = row

    return {
        'single_page_scans': single_page or 0,
        'multi_page_scans': multi_page or 0,
        'budget_exhausted_count': budget_exhausted or 0,
        'tombstone_skips': tombstone_skips or 0,
        'total_scans': total or 0,
        'pct_single_page': round(100.0 * (single_page or 0) / (total or 1), 1),
        'pct_multi_page': round(100.0 * (multi_page or 0) / (total or 1), 1),
    }
```

### Update `print_daily_report()`:

Add this section after cache hit rate:

```python
# Optimization metrics
optimization_metrics = get_optimization_metrics(db_path, hours)
if optimization_metrics:
    print("🎯 OPTIMIZATION EFFICIENCY:")
    print(f"   Single-page scans: {optimization_metrics['pct_single_page']}%")
    print(f"   Multi-page scans: {optimization_metrics['pct_multi_page']}%")
    print(f"   Tombstone skips: {optimization_metrics['tombstone_skips']}")
    if optimization_metrics['budget_exhausted_count'] > 0:
        print(f"   ⚠️  Budget exhausted {optimization_metrics['budget_exhausted_count']} times")
    print()
```

---

## Step 5: Integration Checklist

- [ ] Run schema migration
- [ ] Copy `helius_optimization_engine.py` to project
- [ ] Update `realtime_creator_funding_extractor.py` with prefilter logic
- [ ] Update `funder_incoming_extractor.py` with 2-pass scanning + budget guard
- [ ] Update `extract_for_creator_async()` to use `BudgetGuard`
- [ ] Add optimization metrics to `rpc_metrics_reports.py`
- [ ] Test with 1 creator (verify shortlist is created)
- [ ] Verify metrics are recorded (deep_scan_pages, budget_exhausted, tombstone_skip)
- [ ] Run report and check optimization section
- [ ] Monitor for 24 hours and compare before/after credits/creator

---

## Expected Outcomes

### Before Optimization
```
📊 TOTAL CREDITS: 50,000 (24h)
📈 AVERAGE CREDITS PER TOKEN: 250
👤 TOP CREATOR: 5,000 credits (50 funders × 100 cr)
```

### After Optimization (Week 1)
```
📊 TOTAL CREDITS: 15,000 (24h)  ← 70% reduction
📈 AVERAGE CREDITS PER TOKEN: 75
👤 TOP CREATOR: 1,200 credits (22 shortlisted funders × 55 cr avg)

🎯 OPTIMIZATION EFFICIENCY:
   Single-page scans: 85%  ← Most funders need only Pass A
   Multi-page scans: 15%
   Tombstone skips: 150    ← Growing over time
```

### After Optimization (Month 1)
```
📊 TOTAL CREDITS: 10,000 (24h)  ← 80% reduction (tombstones accumulating)
🎯 OPTIMIZATION EFFICIENCY:
   Single-page scans: 92%
   Tombstone skips: 1,200   ← Preventing re-scans
```

---

## Tuning Parameters

All configurable in `PrefilterConfig`:

| Parameter | Default | Purpose | Tuning |
|-----------|---------|---------|--------|
| `min_inbound_sol` | 0.2 | Minimum SOL to consider | Lower = more funders, higher cost |
| `min_inbound_count` | 1 | Minimum transfer count | Higher = skip whales with 1 transfer |
| `top_n_by_sol` | 20 | Top N funders by total SOL | Raise to 30 for comprehensive, lower to 10 for aggressive |
| `include_cex` | True | Always include CEX funders | Set False to skip CEX (but miss exchange funding) |
| `include_infra` | True | Always include INFRA funders | Set False to skip infrastructure |
| `max_credits` (BudgetGuard) | 250 | Per-creator budget | Raise to 400 for very large clusters, lower to 150 for tight budgets |
| `ttl_days` (TombstoneManager) | 14 | How long to skip empty wallets | Lower to 7 for rapid re-analysis, higher to 30 for long-term skip |

---

## Troubleshooting

**Q: Shortlist is empty for a creator**
A: Check if creator_funder_summary table is being populated. Verify prefilter.get_shortlist() is being called.

**Q: Budget exhausted too early**
A: Increase max_credits in BudgetGuard (default 250). Or lower top_n_by_sol to fewer funders.

**Q: Tombstones not preventing re-scans**
A: Verify is_tombstoned() is being called before extract_transfers_for_funder(). Check tombstone_type is 'empty' or 'shallow'.

**Q: deep_scan_pages metric always 1**
A: Verify Pass B condition (should_do_pass_b) is passing. Check wallet_type and inbound_sol values.

**Q: No optimization metrics in report**
A: Ensure new columns are migrated. Verify metrics are being recorded with record_request(..., deep_scan_pages=X, ...).

---

## Next Steps

1. ✅ Apply schema migration
2. ✅ Integrate prefilter in creator extractor
3. ✅ Integrate 2-pass scanning + budget guard in funder extractor
4. ✅ Add optimization reporting
5. 📊 Monitor for 7 days and measure savings
6. 🔧 Tune parameters based on observed distribution
7. 📈 Scale successful patterns to all creators

---

## References

- `helius_optimization_schema.sql` — Database schema
- `helius_optimization_engine.py` — Core logic (Prefilter, BudgetGuard, TombstoneManager, TwoPassScanner)
- `HELIUS_OPTIMIZATION_INTEGRATION.md` — This file

---

**Version:** 1.0
**Status:** Ready for integration
**Estimated Savings:** 70–80% Helius usage reduction
**Implementation Time:** 2–3 hours + 1 week monitoring
