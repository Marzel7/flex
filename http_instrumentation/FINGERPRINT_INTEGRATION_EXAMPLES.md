# Wallet Fingerprint Clustering - Integration Code Examples

This document provides copy-paste ready code for integrating fingerprint clustering into your extractors.

---

## Example 1: Basic Integration in funder_incoming_extractor.py

### Setup (in __init__)

```python
from http_instrumentation.wallet_fingerprint_clustering import (
    WalletFingerprintCluster,
    FingerprintAction
)

class FunderIncomingExtractor:
    def __init__(self, db_path: str, ...):
        self.db_path = db_path
        # ... existing init code ...

        # NEW: Initialize fingerprint cluster
        self.fingerprint_cluster = WalletFingerprintCluster(db_path)
        logger.info("[FINGERPRINT] Cluster initialized")
```

### Usage in extract_transfers_for_funder()

Replace the existing scan logic with this:

```python
async def extract_transfers_for_funder(
    self,
    funder: str,
    creator: str,
    funder_inbound_sol: float,
    is_top_n: bool = False,
    budget_guard = None
):
    """
    Extract funder transfers with fingerprint clustering.

    NEW: Checks fingerprint cache before scanning
    """

    # =====================================================================
    # STEP 1: Check Fingerprint Cache
    # =====================================================================

    action, cached_type, cached_confidence = self.fingerprint_cluster.lookup_wallet(funder)

    if action == FingerprintAction.SKIP:
        # HIGH CONFIDENCE CACHED: Skip scan entirely
        logger.info(
            f"[FINGERPRINT] SKIP {funder[:8]}: "
            f"cached {cached_type} (confidence={cached_confidence:.2f})"
        )

        # Record metrics (0 credits spent)
        self.record_request(
            creator_address=creator,
            funder_address=funder,
            section='funder_incoming',
            credits_estimated=0,
            credits_actual=0,
            deep_scan_pages=0,
            budget_exhausted=0 if not budget_guard else budget_guard.is_exhausted(creator),
            tombstone_skip=0,
            prefilter_shortlist=1,
            funder_inbound_sol=funder_inbound_sol,
            funder_inbound_count=0,
            fingerprint_cache_hit=1,      # ← NEW METRIC
            fingerprint_refresh=0
        )
        return  # Exit - no scan performed

    elif action == FingerprintAction.REFRESH:
        # MEDIUM CONFIDENCE CACHED: Light refresh scan
        logger.info(
            f"[FINGERPRINT] REFRESH {funder[:8]}: "
            f"updating {cached_type} (confidence={cached_confidence:.2f})"
        )

        # Run Pass A only (1 page = ~50 credits)
        try:
            wallet_type, confidence = await self.scanner.pass_a_fingerprint(funder)

            # Update fingerprint with fresh data
            self.fingerprint_cluster.save_fingerprint(
                funder,
                wallet_type=wallet_type,
                confidence=confidence,
                pages_scanned=1,
                skip_reason='REFRESH'
            )

            logger.debug(
                f"[FINGERPRINT] Updated {funder[:8]}: "
                f"{wallet_type} (conf={confidence:.2f})"
            )

            # Record metrics (1 page = ~50 credits)
            self.record_request(
                creator_address=creator,
                funder_address=funder,
                section='funder_incoming',
                credits_estimated=50,
                credits_actual=50,
                deep_scan_pages=1,
                budget_exhausted=0 if not budget_guard else budget_guard.is_exhausted(creator),
                tombstone_skip=0,
                prefilter_shortlist=1,
                funder_inbound_sol=funder_inbound_sol,
                funder_inbound_count=0,
                fingerprint_cache_hit=0,
                fingerprint_refresh=1         # ← NEW METRIC
            )
            return

        except Exception as e:
            logger.error(f"[FINGERPRINT] Refresh failed for {funder}: {e}")
            # Fall through to FULL_SCAN

    # =====================================================================
    # STEP 2: Full Scan (cache miss or low confidence)
    # =====================================================================

    # Either action == FingerprintAction.FULL_SCAN or we fell through from REFRESH error

    logger.debug(f"[FINGERPRINT] FULL_SCAN {funder[:8]}")

    try:
        # Pass A: 1-page fingerprint
        pages_scanned = 1
        wallet_type, confidence = await self.scanner.pass_a_fingerprint(funder)

        # Decide if Pass B is needed
        should_deep_scan = await self.scanner.should_do_pass_b(
            wallet=funder,
            wallet_type=wallet_type,
            inbound_sol=funder_inbound_sol,
            is_top_n=is_top_n,
            creator=creator
        )

        if should_deep_scan:
            # Pass B: Deep multi-page scan (up to 5 pages)
            pages_scanned = await self.scanner.pass_b_deep_scan(
                wallet=funder,
                creator=creator,
                max_pages=5
            )

        # Estimate credits
        credits_estimated = pages_scanned * 50

        # NEW: Save fingerprint for future use
        self.fingerprint_cluster.save_fingerprint(
            funder,
            wallet_type=wallet_type,
            confidence=confidence,
            pages_scanned=pages_scanned,
            skip_reason='FULL_SCAN'
        )

        # Record metrics
        self.record_request(
            creator_address=creator,
            funder_address=funder,
            section='funder_incoming',
            credits_estimated=credits_estimated,
            credits_actual=credits_estimated,
            deep_scan_pages=pages_scanned,
            budget_exhausted=1 if budget_guard and budget_guard.is_exhausted(creator) else 0,
            tombstone_skip=0,
            prefilter_shortlist=1,
            funder_inbound_sol=funder_inbound_sol,
            funder_inbound_count=0,
            fingerprint_cache_hit=0,
            fingerprint_refresh=0             # ← NEW METRIC
        )

    except Exception as e:
        logger.error(f"[FINGERPRINT] Scan failed for {funder}: {e}")
        self.record_request(
            creator_address=creator,
            funder_address=funder,
            section='funder_incoming',
            credits_estimated=0,
            error=str(e),
            fingerprint_cache_hit=0,
            fingerprint_refresh=0
        )
```

---

## Example 2: Update record_request() Function

Add these two parameters:

```python
def record_request(
    self,
    # ... existing parameters ...
    fingerprint_cache_hit: int = 0,   # NEW
    fingerprint_refresh: int = 0,     # NEW
):
    """
    Record wallet scan metrics with fingerprint tracking.
    """

    try:
        conn = sqlite3.connect(self.db_path, timeout=30)
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO wallet_scan_metrics (
                funder_address,
                creator_address,
                section,
                credits_estimated,
                credits_actual,
                deep_scan_pages,
                budget_exhausted,
                tombstone_skip,
                prefilter_shortlist,
                funder_inbound_sol,
                funder_inbound_count,
                fingerprint_cache_hit,    -- NEW
                fingerprint_refresh,      -- NEW
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                funder_address,
                creator_address,
                section,
                credits_estimated,
                credits_actual,
                deep_scan_pages,
                budget_exhausted,
                tombstone_skip,
                prefilter_shortlist,
                funder_inbound_sol,
                funder_inbound_count,
                fingerprint_cache_hit,    # NEW
                fingerprint_refresh,      # NEW
            )
        )

        conn.commit()
        conn.close()

    except Exception as e:
        logger.error(f"Error recording request: {e}")
```

---

## Example 3: Add API Endpoint for Fingerprint Stats

Add to your Flask app or optimization_api.py:

```python
from flask import Blueprint, jsonify
from wallet_fingerprint_clustering import WalletFingerprintCluster

fingerprint_bp = Blueprint('fingerprint', __name__)

@fingerprint_bp.route('/api/fingerprint/stats')
def api_fingerprint_stats():
    """Get wallet fingerprint cache statistics."""
    try:
        cluster = WalletFingerprintCluster('flex_complete_database.db')

        stats = cluster.get_stats(hours=24)
        by_type = cluster.get_type_distribution()
        savings = cluster.estimate_credits_saved()

        return jsonify({
            'status': 'success',
            'data': {
                'cache_stats': stats,
                'type_distribution': by_type,
                'savings': savings,
                'timestamp': datetime.now().isoformat()
            }
        })

    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@fingerprint_bp.route('/api/fingerprint/frequent')
def api_fingerprint_frequent():
    """Get most frequently scanned wallets."""
    try:
        cluster = WalletFingerprintCluster('flex_complete_database.db')
        frequent = cluster.get_top_frequent_wallets(limit=20)

        data = [
            {
                'wallet': addr[:8],
                'wallet_full': addr,
                'type': wtype,
                'confidence': conf,
                'scans': scans
            }
            for addr, wtype, conf, scans in frequent
        ]

        return jsonify({
            'status': 'success',
            'data': data,
            'count': len(data)
        })

    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

# Register blueprint
app.register_blueprint(fingerprint_bp)
```

---

## Example 4: Dashboard Card Component

```html
<div class="metric-card">
    <div class="metric-label">🎯 Wallet Fingerprints</div>
    <div id="fingerprint-content" style="padding: 1rem 0; font-size: 0.9rem;">
        <div style="color: #06b6d4;">Loading...</div>
    </div>
</div>

<script>
async function loadFingerprintMetrics() {
    try {
        const response = await fetch('/api/fingerprint/stats');
        const result = await response.json();

        if (result.status === 'success') {
            const stats = result.data.cache_stats;
            const savings = result.data.savings;

            document.getElementById('fingerprint-content').innerHTML = `
                <div style="margin: 0.5rem 0;">
                    <span style="color: #a78bfa;">Total cached:</span>
                    <strong>${stats.total_fingerprints || 0}</strong>
                </div>
                <div style="margin: 0.5rem 0;">
                    <span style="color: #22c55e;">High confidence:</span>
                    <strong>${stats.high_confidence || 0}</strong> (skip)
                </div>
                <div style="margin: 0.5rem 0;">
                    <span style="color: #fbbf24;">Medium conf:</span>
                    <strong>${stats.medium_confidence || 0}</strong> (refresh)
                </div>
                <div style="margin: 0.5rem 0;">
                    <span style="color: #ef4444;">Low conf:</span>
                    <strong>${stats.low_confidence || 0}</strong> (full scan)
                </div>
                <div style="margin-top: 0.75rem; padding-top: 0.75rem; border-top: 1px solid rgba(255,255,255,0.1);">
                    <span style="color: #06b6d4;">Est. savings:</span>
                    <strong style="color: #22c55e;">${savings.total_estimated_credits_saved || 0}</strong> credits
                </div>
            `;
        }
    } catch (error) {
        console.error('Fingerprint metrics error:', error);
    }
}

// Load on page load
loadFingerprintMetrics();

// Auto-refresh every 60 seconds
setInterval(loadFingerprintMetrics, 60000);
</script>
```

---

## Example 5: Monitoring Query

```python
def print_fingerprint_report(db_path: str):
    """Print wallet fingerprint cache report."""

    cluster = WalletFingerprintCluster(db_path)

    print("\n" + "=" * 80)
    print("🎯 WALLET FINGERPRINT CLUSTERING REPORT")
    print("=" * 80)

    # Overall stats
    stats = cluster.get_stats(hours=24)
    print(f"\n📊 CACHE STATISTICS (24h):")
    print(f"   Total fingerprints: {stats['total_fingerprints']}")
    print(f"   Active (24h):       {stats['active_24h']}")
    print(f"   High confidence:    {stats['high_confidence']} (skip)")
    print(f"   Medium confidence:  {stats['medium_confidence']} (refresh)")
    print(f"   Low confidence:     {stats['low_confidence']} (full)")
    print(f"   Avg scans/wallet:   {stats['avg_scans_per_wallet']:.1f}")
    print(f"   Avg confidence:     {stats['avg_confidence']:.3f}")

    # Type distribution
    by_type = cluster.get_type_distribution()
    print(f"\n💰 WALLET TYPES:")
    for wtype, info in by_type.items():
        print(f"   {wtype:10} {info['count']:4} wallets  "
              f"(avg conf: {info['avg_confidence']:.2f}, "
              f"avg scans: {info['avg_scans']:.1f}, "
              f"active 7d: {info['active_7d']})")

    # Frequent wallets
    frequent = cluster.get_top_frequent_wallets(limit=10)
    if frequent:
        print(f"\n⭐ TOP FREQUENT WALLETS:")
        for addr, wtype, conf, scans in frequent[:10]:
            print(f"   {addr[:8]}... {wtype:8} conf={conf:.2f}  scans={scans}")

    # Savings estimate
    savings = cluster.estimate_credits_saved()
    print(f"\n💾 ESTIMATED SAVINGS:")
    print(f"   Skipped scans:      {savings['estimated_skipped_scans']}")
    print(f"   Credits saved:      {savings['estimated_skipped_credits']}")
    print(f"   Refresh scans:      {savings['estimated_refreshed_scans']}")
    print(f"   Refresh cost:       {savings['estimated_refreshed_credit_cost']}")
    print(f"   Refresh savings:    {savings['estimated_refreshed_credits_saved']}")
    print(f"   TOTAL SAVED:        {savings['total_estimated_credits_saved']} credits")

    print("\n" + "=" * 80)
```

---

## Example 6: Background Cleanup Task

```python
import asyncio

async def cleanup_old_fingerprints():
    """Background task to clean up old fingerprints."""

    cluster = WalletFingerprintCluster('flex_complete_database.db')

    while True:
        try:
            # Clean up fingerprints not accessed in 30 days
            deleted = cluster.cleanup_old_fingerprints(days_old=30)

            if deleted > 0:
                logger.info(f"[FINGERPRINT] Cleaned up {deleted} old fingerprints")

            # Check in 24 hours
            await asyncio.sleep(86400)

        except Exception as e:
            logger.error(f"[FINGERPRINT] Cleanup error: {e}")
            await asyncio.sleep(3600)  # Retry in 1 hour

# Start in your async initialization
asyncio.create_task(cleanup_old_fingerprints())
```

---

## Example 7: Cache Hit Rate Monitoring

```python
def get_cache_hit_rate(db_path: str, hours: int = 24) -> float:
    """Calculate fingerprint cache hit rate."""

    try:
        conn = sqlite3.connect(db_path, timeout=10)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT
                SUM(fingerprint_cache_hit) as hits,
                COUNT(*) as total
            FROM wallet_scan_metrics
            WHERE created_at >= datetime('now', ? || ' hours')
            """,
            (f'-{hours}',)
        )

        row = cursor.fetchone()
        conn.close()

        if not row or row[1] == 0:
            return 0.0

        hits, total = row
        return (hits or 0) / total * 100.0

    except Exception as e:
        logger.error(f"Error calculating cache hit rate: {e}")
        return 0.0

# Use in reporting
def print_daily_report(db_path: str):
    # ... existing code ...

    cache_hit_rate = get_cache_hit_rate(db_path, hours=24)
    print(f"\n🎯 FINGERPRINT CACHE HIT RATE (24h): {cache_hit_rate:.1f}%")

    # Expected progression:
    # Day 1: 0-5%
    # Week 1: 15-25%
    # Month 1: 35-55%
```

---

## Integration Checklist

- [ ] Apply schema migration: `sqlite3 flex_complete_database.db < wallet_fingerprint_clustering_schema.sql`
- [ ] Copy `wallet_fingerprint_clustering.py` to `http_instrumentation/`
- [ ] Import module in extractor
- [ ] Initialize cluster in `__init__`
- [ ] Add fingerprint lookup before TwoPassScanner
- [ ] Update `record_request()` to accept new metrics
- [ ] Test with 1 creator extraction
- [ ] Monitor cache hit rate (should grow over time)
- [ ] Add API endpoints for monitoring
- [ ] Add dashboard card for visualization
- [ ] Setup background cleanup task

---

**Status**: Ready to integrate
**Time to implement**: 1-2 hours
**Lines of code to add**: 60-80 (per extractor)
**Expected payoff**: 5-10% additional credits saved
