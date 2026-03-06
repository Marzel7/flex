# RPC Metrics Recorder Patch - Add Real Credits Savings Tracking
# Date: 2026-03-05
# Purpose: Track actual credits saved by cache hits

# ============================================================================
# PATCH 1: Update _persist_rpc_metric function signature and INSERT
# ============================================================================

def _persist_rpc_metric(
    timestamp: float,
    section: str,
    provider: str,
    method: str,
    status_code: int,
    latency_ms: float,
    credits: int,
    mode: str = "realtime",
    retries: int = 0,
    source_file: str = "unknown",
    error: Optional[str] = None,
    # NEW PARAMETERS:
    cache_action: str = "none",  # 'skip', 'refresh', 'full_scan', 'none'
    credits_saved: int = 0,       # Actual credits avoided
):
    """
    Write RPC metric to database for persistent cross-process tracking.

    Args:
        timestamp, section, provider, method, status_code, latency_ms, credits: Standard RPC metrics
        mode, retries, source_file, error: Additional context
        cache_action: Type of cache action ('skip', 'refresh', 'full_scan', 'none')
        credits_saved: Actual credits that were avoided due to caching
    """
    try:
        import os as os_module
        pid = os_module.getpid()
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.execute(
            """
            INSERT INTO rpc_metrics
            (timestamp, section, provider, method, status_code, latency_ms, credits,
             mode, retries, source_file, error, process_pid, cache_action, credits_saved)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (timestamp, section, provider, method, status_code, latency_ms, credits,
             mode, retries, source_file, error, pid, cache_action, credits_saved),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        # Fail silently to not disrupt normal operation
        pass


# ============================================================================
# PATCH 2: Update RPCMetricsRecorder.record_request() signature
# ============================================================================

class RPCMetricsRecorder:
    """Thread-safe metrics recorder for RPC requests"""

    # ... existing __init__ and other methods ...

    def record_request(
        self,
        section: str,
        provider: str,
        method: str,
        status_code: int,
        latency_ms: float,
        mode: str = "realtime",
        retries: int = 0,
        bytes_in: int = 0,
        bytes_out: int = 0,
        source_file: str = "unknown",
        error: Optional[str] = None,
        # NEW PARAMETERS (backward compatible):
        cache_action: str = "none",  # 'skip', 'refresh', 'full_scan', 'none'
        credits_saved: int = 0,       # Actual credits avoided
    ) -> int:
        """
        Record a single RPC request with optional cache metrics.

        Args:
            section: Component section (listener, creator_funding, funder_incoming, etc.)
            provider: Provider (helius_rpc, helius_enhanced, public_rpc_fallback, etc.)
            method: RPC method (getTransaction) or pseudo-method
            status_code: HTTP/RPC status code
            latency_ms: Request latency in milliseconds
            mode: realtime or background
            retries: Number of retries before success
            bytes_in: Request body bytes
            bytes_out: Response body bytes
            source_file: File/process making the call
            error: Error message if failed
            cache_action: Type of cache hit ('skip', 'refresh', 'full_scan', 'none')
            credits_saved: Actual credits avoided due to caching

        Returns:
            Credits consumed for this request
        """
        with self._lock:
            # Compute credits
            credits = self._compute_credits(method, status_code)
            ts = time.time()

            # Create record
            record = RequestRecord(
                timestamp=ts,
                section=section,
                provider=provider,
                method=method,
                mode=mode,
                status_code=status_code,
                latency_ms=latency_ms,
                retries=retries,
                source_file=source_file,
                bytes_in=bytes_in,
                bytes_out=bytes_out,
                error=error,
            )

            # Store in memory
            self._history.append(record)
            if len(self._history) > self._max_history:
                self._history.pop(0)

            # Update statistics
            if section not in self._section_stats:
                self._section_stats[section] = SectionStats()

            section_stats = self._section_stats[section]
            section_stats.request_count += 1
            section_stats.total_latency_ms += latency_ms
            section_stats.credits_total += credits
            section_stats.credits_by_method[method] = section_stats.credits_by_method.get(method, 0) + credits
            section_stats.credits_by_provider[provider] = section_stats.credits_by_provider.get(provider, 0) + credits

            self._total_requests += 1
            self._total_credits += credits
            self._daily_credits += credits

            # Persist to database
            _persist_rpc_metric(
                timestamp=ts,
                section=section,
                provider=provider,
                method=method,
                status_code=status_code,
                latency_ms=latency_ms,
                credits=credits,
                mode=mode,
                retries=retries,
                source_file=source_file,
                error=error,
                cache_action=cache_action,      # NEW
                credits_saved=credits_saved,    # NEW
            )

            return credits


# ============================================================================
# PATCH 3: Add convenience method to get real cache savings
# ============================================================================

    def get_real_cache_savings(self, hours: int = 24) -> Dict[str, Any]:
        """
        Get actual documented cache savings from database.

        Args:
            hours: Time window to analyze (default 24)

        Returns:
            Dict with real savings statistics
        """
        try:
            import sqlite3
            conn = sqlite3.connect(DB_PATH, timeout=10)
            cur = conn.cursor()

            cutoff = f"datetime('now', '-{hours} hours')"

            # Total savings in window
            cur.execute(f"""
                SELECT SUM(credits_saved), COUNT(*),
                       COUNT(CASE WHEN cache_action='skip' THEN 1 END),
                       COUNT(CASE WHEN cache_action='refresh' THEN 1 END)
                FROM rpc_metrics
                WHERE cache_action IN ('skip', 'refresh')
                  AND recorded_at >= {cutoff}
            """)
            total_saved, total_hits, skip_count, refresh_count = cur.fetchone() or (0, 0, 0, 0)

            # Total requests in window
            cur.execute(f"""
                SELECT COUNT(*)
                FROM rpc_metrics
                WHERE recorded_at >= {cutoff}
            """)
            total_requests = cur.fetchone()[0] or 1

            # By section
            cur.execute(f"""
                SELECT section, COUNT(*), SUM(credits_saved)
                FROM rpc_metrics
                WHERE cache_action IN ('skip', 'refresh')
                  AND recorded_at >= {cutoff}
                GROUP BY section
                ORDER BY SUM(credits_saved) DESC
            """)
            by_section = {row[0]: {'hits': row[1], 'saved': row[2]} for row in cur.fetchall()}

            conn.close()

            return {
                'window_hours': hours,
                'total_credits_saved': total_saved or 0,
                'total_cache_hits': total_hits or 0,
                'skip_hits': skip_count or 0,
                'refresh_hits': refresh_count or 0,
                'cache_hit_rate': f"{100.0 * (total_hits or 0) / total_requests:.1f}%",
                'by_section': by_section,
            }
        except Exception as e:
            print(f"[WARNING] Could not retrieve cache savings: {e}", flush=True)
            return {}


# ============================================================================
# USAGE EXAMPLES
# ============================================================================

"""
Example 1: Record a cache skip
---

from rpc_metrics_recorder import _recorder

# Wallet fingerprint cache hit (would have been 200 credits)
_recorder.record_request(
    section="funder_incoming",
    provider="helius_rpc",
    method="helius_address_feed",
    status_code=200,
    latency_ms=0.0,  # No actual request made
    source_file="funder_incoming_extractor",
    cache_action="skip",          # NEW
    credits_saved=200,            # NEW (full scan avoided)
)

---

Example 2: Record a cache refresh
---

# Wallet fingerprint cache refresh (would have been 200, did light 50-credit scan)
_recorder.record_request(
    section="funder_incoming",
    provider="helius_rpc",
    method="helius_address_feed",
    status_code=200,
    latency_ms=45.0,
    source_file="funder_incoming_extractor",
    cache_action="refresh",       # NEW
    credits_saved=150,            # NEW (full scan avoided, light scan used)
)

---

Example 3: Creator cache skip
---

# Creator funding cache hit
_recorder.record_request(
    section="creator_funding",
    provider="helius_rpc",
    method="creator_funders_extraction",
    status_code=200,
    latency_ms=0.0,
    source_file="realtime_creator_funding_extractor",
    cache_action="skip",          # NEW
    credits_saved=150,            # NEW (creator extraction avoided)
)

---

Example 4: Retrieve savings statistics
---

savings = _recorder.get_real_cache_savings(hours=24)
print(f"Credits saved (24h): {savings['total_credits_saved']}")
print(f"Cache hit rate: {savings['cache_hit_rate']}")
print(f"By section: {savings['by_section']}")
"""
