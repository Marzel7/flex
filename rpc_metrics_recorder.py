"""
RPC Metrics Recorder - Production-grade credit accounting and metrics collection.

Tracks Helius credit usage by section, provider, and method.
Maintains rolling counters and exposes metrics via HTTP endpoint.
"""

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import RLock
from typing import Dict, List, Optional, Tuple
import json

# ============================================================================
# CREDIT SCHEDULE (configurable)
# ============================================================================

CREDIT_SCHEDULE = {
    # Standard RPC methods
    "getHealth": 1,
    "getClusterNodes": 1,
    "getSystemProgram": 1,

    # Historical/Archival
    "getSignaturesForAddress": 10,
    "getTransaction": 10,
    "getSignatureStatuses": {
        "default": 1,
        "searchTransactionHistory": 10,
    },

    # Helius-exclusive RPC
    "getTransactionsForAddress": 100,

    # Helius Enhanced Transactions API (REST pseudo-methods)
    # Source: https://www.helius.dev/docs/billing/credits
    "helius_enhanced_addresses_transactions": 100,  # Per request (official Helius rate)
    "helius_enhanced_transactions_batch": 100,      # Per request (official Helius rate)

    # Streaming (handled separately via bytes)
    # "laserstream_bytes": 3 per 0.1MB
    # "enhanced_ws_bytes": 3 per 0.1MB
}

# Streaming credits: 3 credits per 0.1MB = 3 credits per 102400 bytes
STREAMING_CREDITS_PER_BYTE = 3.0 / (0.1 * 1024 * 1024)


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class RequestRecord:
    """Single RPC request record"""
    timestamp: float
    section: str
    provider: str
    method: str
    mode: str
    status_code: int
    latency_ms: float
    retries: int
    bytes_in: int = 0
    bytes_out: int = 0
    credits: int = 0
    error: Optional[str] = None


@dataclass
class SectionStats:
    """Aggregated stats for a section"""
    requests: int = 0
    errors: int = 0
    rate_limits_429: int = 0
    latencies: deque = field(default_factory=lambda: deque(maxlen=1000))
    credits_total: int = 0
    credits_by_method: Dict[str, int] = field(default_factory=dict)
    credits_by_provider: Dict[str, int] = field(default_factory=dict)

    @property
    def avg_latency(self) -> float:
        if not self.latencies:
            return 0.0
        return sum(self.latencies) / len(self.latencies)

    @property
    def p95_latency(self) -> float:
        if not self.latencies:
            return 0.0
        sorted_lat = sorted(self.latencies)
        idx = int(len(sorted_lat) * 0.95)
        return sorted_lat[idx] if idx < len(sorted_lat) else sorted_lat[-1]


# ============================================================================
# RPC METRICS RECORDER
# ============================================================================

class RPCMetricsRecorder:
    """Thread-safe metrics recorder for RPC requests"""

    def __init__(self, max_history: int = 10000, plan_monthly_credits: int = 0):
        """
        Initialize recorder.

        Args:
            max_history: Keep last N request records for diagnostics
            plan_monthly_credits: Total monthly credit budget (0 = unlimited)
        """
        self._lock = RLock()
        self._max_history = max_history
        self._plan_monthly_credits = plan_monthly_credits

        # History buffer (for diagnostics)
        self._history: deque = deque(maxlen=max_history)

        # Per-section stats
        self._section_stats: Dict[str, SectionStats] = defaultdict(SectionStats)

        # Global tracking
        self._start_time = time.time()
        self._total_credits = 0
        self._total_requests = 0
        self._total_errors = 0
        self._total_429s = 0

        # Daily reset tracking
        self._daily_reset_time = datetime.now()
        self._daily_credits = 0

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
        error: Optional[str] = None,
    ) -> int:
        """
        Record a single RPC request.

        Args:
            section: Component section (listener, creator_funding, funder_incoming, etc.)
            provider: Provider (helius_rpc, helius_enhanced, public_rpc_fallback, etc.)
            method: RPC method (getTransaction) or pseudo-method (helius_enhanced_addresses_transactions)
            status_code: HTTP/RPC status code
            latency_ms: Request latency in milliseconds
            mode: realtime or background
            retries: Number of retries before success
            bytes_in: Request body bytes
            bytes_out: Response body bytes
            error: Error message if failed

        Returns:
            Credits consumed for this request
        """
        with self._lock:
            # Compute credits
            credits = self._compute_credits(method, status_code)

            # Create record
            record = RequestRecord(
                timestamp=time.time(),
                section=section,
                provider=provider,
                method=method,
                mode=mode,
                status_code=status_code,
                latency_ms=latency_ms,
                retries=retries,
                bytes_in=bytes_in,
                bytes_out=bytes_out,
                credits=credits,
                error=error,
            )

            # Store in history
            self._history.append(record)

            # Update section stats
            section_stats = self._section_stats[section]
            section_stats.requests += 1
            section_stats.latencies.append(latency_ms)
            section_stats.credits_total += credits
            section_stats.credits_by_method[method] = section_stats.credits_by_method.get(method, 0) + credits
            section_stats.credits_by_provider[provider] = section_stats.credits_by_provider.get(provider, 0) + credits

            # Track errors
            if status_code >= 400:
                section_stats.errors += 1
                self._total_errors += 1

            if status_code == 429:
                section_stats.rate_limits_429 += 1
                self._total_429s += 1

            # Update global totals
            self._total_requests += 1
            self._total_credits += credits
            self._daily_credits += credits

            return credits

    def record_stream_bytes(
        self,
        section: str,
        provider: str,
        stream_name: str,
        bytes_count: int,
    ) -> int:
        """
        Record streaming data (LaserStream, WebSocket, etc.).

        Args:
            section: Component section
            provider: Provider
            stream_name: Stream identifier (laserstream, enhanced_ws, etc.)
            bytes_count: Bytes transferred

        Returns:
            Credits consumed
        """
        with self._lock:
            # Compute credits from bytes (3 credits per 0.1MB)
            credits = int(bytes_count * STREAMING_CREDITS_PER_BYTE) + (1 if (bytes_count * STREAMING_CREDITS_PER_BYTE) % 1 > 0 else 0)

            # Record as pseudo-request
            record = RequestRecord(
                timestamp=time.time(),
                section=section,
                provider=provider,
                method=f"{stream_name}_bytes",
                mode="streaming",
                status_code=200,
                latency_ms=0,
                retries=0,
                bytes_out=bytes_count,
                credits=credits,
            )

            self._history.append(record)

            # Update stats
            section_stats = self._section_stats[section]
            section_stats.requests += 1
            section_stats.credits_total += credits
            section_stats.credits_by_method[f"{stream_name}_bytes"] = section_stats.credits_by_method.get(f"{stream_name}_bytes", 0) + credits
            section_stats.credits_by_provider[provider] = section_stats.credits_by_provider.get(provider, 0) + credits

            self._total_requests += 1
            self._total_credits += credits
            self._daily_credits += credits

            return credits

    def _compute_credits(self, method: str, status_code: int) -> int:
        """
        Compute credits for a method based on schedule.

        Returns 0 for:
        - Failed requests (status >= 400)
        - Methods with "unknown" cost (user must verify with Helius)
        """
        # Failed requests may not consume credits (depends on plan)
        # Default: 400+ errors cost credits, but this is configurable
        if status_code >= 400:
            return 0  # Assume failed requests don't consume credits

        # Look up in schedule
        if method not in CREDIT_SCHEDULE:
            # Unknown method - return 0 and let user add it to CREDIT_SCHEDULE
            return 0

        schedule_entry = CREDIT_SCHEDULE[method]

        # If entry is "unknown" string, return 0 (user must configure)
        if schedule_entry == "unknown":
            return 0

        # If it's a dict (e.g., getSignatureStatuses with conditional), use default
        if isinstance(schedule_entry, dict):
            return schedule_entry.get("default", 0)

        # Try to convert to int
        try:
            return int(schedule_entry)
        except (ValueError, TypeError):
            return 0

    def get_summary(self) -> Dict:
        """Get high-level summary of credit usage"""
        with self._lock:
            now = time.time()
            uptime_seconds = now - self._start_time
            uptime_minutes = uptime_seconds / 60.0

            # Daily credit estimate
            hours_elapsed = uptime_seconds / 3600.0
            daily_estimate = (self._daily_credits / hours_elapsed * 24) if hours_elapsed > 0 else 0

            # Monthly estimate
            monthly_estimate = daily_estimate * 30

            # Burn rate (credits per minute)
            burn_rate = self._total_credits / max(uptime_minutes, 1)

            # Credits remaining (if plan specified)
            remaining = None
            if self._plan_monthly_credits > 0:
                remaining = max(0, self._plan_monthly_credits - int(monthly_estimate))

            return {
                "timestamp": datetime.now().isoformat(),
                "uptime_minutes": round(uptime_minutes, 2),
                "credits_today": self._daily_credits,
                "credits_total": self._total_credits,
                "credits_monthly_estimate": int(monthly_estimate),
                "credits_monthly_remaining": remaining,
                "credits_burn_rate_per_minute": round(burn_rate, 2),
                "requests_total": self._total_requests,
                "errors_total": self._total_errors,
                "rate_limits_total": self._total_429s,
                "sections_active": len(self._section_stats),
            }

    def get_section_stats(self) -> Dict[str, Dict]:
        """Get detailed stats per section"""
        with self._lock:
            result = {}
            for section, stats in self._section_stats.items():
                # Top 5 methods by credits
                top_methods = sorted(
                    stats.credits_by_method.items(),
                    key=lambda x: x[1],
                    reverse=True
                )[:5]

                result[section] = {
                    "credits": stats.credits_total,
                    "requests": stats.requests,
                    "errors": stats.errors,
                    "rate_limits_429": stats.rate_limits_429,
                    "avg_latency_ms": round(stats.avg_latency, 2),
                    "p95_latency_ms": round(stats.p95_latency, 2),
                    "top_methods": [{"method": m, "credits": c} for m, c in top_methods],
                }

            return result

    def get_top_methods(self, limit: int = 10) -> List[Dict]:
        """Get top methods by credits across all sections"""
        with self._lock:
            method_credits = defaultdict(int)
            method_requests = defaultdict(int)

            for stats in self._section_stats.values():
                for method, credits in stats.credits_by_method.items():
                    method_credits[method] += credits
                    method_requests[method] += 1

            sorted_methods = sorted(
                method_credits.items(),
                key=lambda x: x[1],
                reverse=True
            )[:limit]

            return [
                {
                    "method": method,
                    "credits": credits,
                    "requests": method_requests[method],
                }
                for method, credits in sorted_methods
            ]

    def get_alerts(self, burn_rate_threshold: float = 100.0) -> List[Dict]:
        """Check for alerts (budget, burn rate, etc.)"""
        alerts = []

        with self._lock:
            summary = self.get_summary()

            # High burn rate alert
            if summary["credits_burn_rate_per_minute"] > burn_rate_threshold:
                alerts.append({
                    "level": "warning",
                    "type": "high_burn_rate",
                    "message": f"Burn rate {summary['credits_burn_rate_per_minute']:.2f} credits/min exceeds threshold {burn_rate_threshold}",
                })

            # Budget depletion alert
            if summary["credits_monthly_remaining"] is not None:
                remaining_pct = (summary["credits_monthly_remaining"] / self._plan_monthly_credits * 100) if self._plan_monthly_credits > 0 else 100
                if remaining_pct < 20:
                    alerts.append({
                        "level": "warning" if remaining_pct > 5 else "critical",
                        "type": "budget_depletion",
                        "message": f"Only {remaining_pct:.1f}% of monthly budget remaining ({summary['credits_monthly_remaining']} credits)",
                    })

            # High error rate
            error_rate = (self._total_errors / max(self._total_requests, 1)) * 100
            if error_rate > 5:
                alerts.append({
                    "level": "warning",
                    "type": "high_error_rate",
                    "message": f"Error rate {error_rate:.1f}% exceeds threshold 5%",
                })

        return alerts

    def reset_daily(self):
        """Reset daily counters (call once per day)"""
        with self._lock:
            self._daily_reset_time = datetime.now()
            self._daily_credits = 0

    def export_json(self) -> str:
        """Export full metrics as JSON"""
        with self._lock:
            return json.dumps({
                "summary": self.get_summary(),
                "sections": self.get_section_stats(),
                "top_methods": self.get_top_methods(),
                "alerts": self.get_alerts(),
            }, indent=2)


# Global recorder instance
_recorder: Optional[RPCMetricsRecorder] = None


def initialize_recorder(plan_monthly_credits: int = 0) -> RPCMetricsRecorder:
    """Initialize global recorder instance"""
    global _recorder
    _recorder = RPCMetricsRecorder(plan_monthly_credits=plan_monthly_credits)
    return _recorder


def get_recorder() -> RPCMetricsRecorder:
    """Get global recorder instance"""
    global _recorder
    if _recorder is None:
        _recorder = RPCMetricsRecorder()
    return _recorder


def record_request(
    section: str,
    provider: str,
    method: str,
    status_code: int,
    latency_ms: float,
    mode: str = "realtime",
    retries: int = 0,
    bytes_in: int = 0,
    bytes_out: int = 0,
    error: Optional[str] = None,
) -> int:
    """Convenience function to record request with global instance"""
    credits = get_recorder().record_request(
        section, provider, method, status_code, latency_ms, mode, retries, bytes_in, bytes_out, error
    )

    # Also POST to API if running on localhost:8001 (for multi-process support)
    try:
        import requests
        requests.post(
            'http://localhost:8001/metrics/rpc/record',
            json={
                'section': section,
                'provider': provider,
                'method': method,
                'status_code': status_code,
                'latency_ms': latency_ms,
                'mode': mode,
                'retries': retries,
                'bytes_in': bytes_in,
                'bytes_out': bytes_out,
                'error': error,
            },
            timeout=1
        )
    except Exception:
        pass  # Fail silently if API not available

    return credits


def record_stream_bytes(
    section: str,
    provider: str,
    stream_name: str,
    bytes_count: int,
) -> int:
    """Convenience function to record streaming bytes with global instance"""
    return get_recorder().record_stream_bytes(section, provider, stream_name, bytes_count)
