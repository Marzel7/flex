"""
RPC Metrics Recorder v2 - Enhanced Monitoring with Success/Retry Tracking

Adds comprehensive observability for:
- Success vs attempted credits
- Retry diagnostics
- 429 rate limit tracking with attempt numbers
- Section taxonomy validation
- Source file attribution improvements
"""

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from threading import RLock
from typing import Dict, List, Optional, Set
import json
import logging

logger = logging.getLogger(__name__)

# ============================================================================
# ENUMS & CONSTANTS
# ============================================================================

class Section(str, Enum):
    """Allowed metric sections"""
    LISTENER = "listener"
    CREATOR_FUNDING = "creator_funding"
    FUNDER_INCOMING = "funder_incoming"
    CREATOR_OUTGOING_SCAN = "creator_outgoing_scan"
    UI_API = "ui_api"
    BACKGROUND_ENRICHMENT = "background_enrichment"

    @classmethod
    def is_valid(cls, value: str) -> bool:
        try:
            cls(value)
            return True
        except ValueError:
            return False

    @classmethod
    def all_sections(cls) -> Set[str]:
        return {s.value for s in cls}


# ============================================================================
# CREDIT SCHEDULE (from rpc_metrics_config.py)
# ============================================================================

CREDIT_SCHEDULE = {
    # Standard RPC methods (low cost)
    "getHealth": 1,
    "getClusterNodes": 1,
    "getSystemProgram": 1,
    "getVersion": 1,
    "getEpochInfo": 1,
    "getGenesisHash": 1,
    "getIdentity": 1,
    "getInflationGovernor": 1,
    "getInflationRate": 1,
    "getInflationReward": 1,
    "getLargestAccounts": 1,
    "getLeaderSchedule": 1,
    "getMaxRetransmitSlot": 1,
    "getMaxShredInsertSlot": 1,
    "getMultipleAccounts": 1,
    "getProgramAccounts": 5,
    "getSignatureStatuses": 1,
    "getSlot": 1,
    "getSlotLeader": 1,
    "getSlotLeaders": 1,
    "getSupply": 1,
    "getTokenAccountsByDelegate": 1,
    "getTokenAccountsByOwner": 1,
    "getTokenLargestAccounts": 1,
    "getTokenSupply": 1,
    "getTransactionCount": 1,
    "getAccountInfo": 1,
    "getBalance": 1,
    "getTokenAccountBalance": 1,
    "getBlock": 1,
    "getBlockTime": 1,
    "getSignaturesForAddress": 10,
    "getTransaction": 10,

    # Helius-exclusive RPC methods (high cost)
    "getTransactionsForAddress": 100,

    # Helius Enhanced Transactions API (REST pseudo-methods)
    "helius_enhanced_addresses_transactions": 100,
    "helius_enhanced_transactions_batch": 100,
}

# Streaming credits: 3 credits per 0.1MB = 3 credits per 102400 bytes
STREAMING_CREDITS_PER_BYTE = 3.0 / (0.1 * 1024 * 1024)


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class RequestRecord:
    """Single RPC request record with enhanced tracking"""
    timestamp: float
    section: str
    provider: str
    method: str
    mode: str
    status_code: int
    latency_ms: float
    retries: int
    source_file: str = "unknown"
    bytes_in: int = 0
    bytes_out: int = 0
    credits: int = 0
    error: Optional[str] = None
    # v2 additions
    is_retry: bool = False  # True if status_code == 429 or retries > 0
    is_success: bool = True  # True if status_code == 200
    attempt_number: int = 1  # Which attempt in the chain (1-indexed)
    retry_after_ms: Optional[int] = None  # From Retry-After header if 429


@dataclass
class RetryDiagnostics:
    """Per-section retry tracking"""
    total_requests: int = 0
    total_retries: int = 0
    avg_retries_per_request: float = 0.0
    max_retries_per_request: int = 0
    requests_with_retries: int = 0  # Count of requests with retries > 0
    retry_by_method: Dict[str, int] = field(default_factory=dict)  # method -> retry count


@dataclass
class RateLimitDiagnostics:
    """429 rate limit diagnostics"""
    total_429_count: int = 0
    last_5min_429_count: int = 0
    by_section: Dict[str, int] = field(default_factory=dict)
    by_method: Dict[str, int] = field(default_factory=dict)
    by_source_file: Dict[str, int] = field(default_factory=dict)
    avg_retry_after_ms: float = 0.0
    attempts_by_attempt_number: Dict[int, int] = field(default_factory=dict)


@dataclass
class SectionStats:
    """Aggregated stats for a section with v2 enhancements"""
    requests_total: int = 0
    requests_success: int = 0
    requests_failed: int = 0
    requests_429: int = 0

    errors: int = 0
    rate_limits_429: int = 0

    # v2: Split credit tracking
    credits_success_only: int = 0  # Credits from status_code == 200
    credits_all_attempts: int = 0  # Credits from all requests (including failures & retries)
    credits_by_method: Dict[str, int] = field(default_factory=dict)
    credits_by_provider: Dict[str, int] = field(default_factory=dict)

    # Retry tracking
    retries_total: int = 0
    retries_by_method: Dict[str, int] = field(default_factory=dict)
    requests_with_retries: int = 0

    latencies: deque = field(default_factory=lambda: deque(maxlen=1000))

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

    @property
    def avg_retries_per_request(self) -> float:
        if self.requests_total == 0:
            return 0.0
        return self.retries_total / self.requests_total


# ============================================================================
# RPC METRICS RECORDER v2
# ============================================================================

class RPCMetricsRecorder:
    """Thread-safe metrics recorder for RPC requests with enhanced monitoring"""

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
        self._total_credits_success = 0
        self._total_credits_all_attempts = 0
        self._total_requests_success = 0
        self._total_requests_failed = 0
        self._total_retries = 0

        # Daily reset tracking
        self._daily_reset_time = datetime.now()
        self._daily_credits = 0

        # 429 tracking (for last 5 minutes)
        self._429_timestamps: deque = deque(maxlen=10000)

    def _compute_credits(self, method: str, status_code: int) -> int:
        """
        Compute credits for a method.

        Always charges credits, even for failures, per Helius documentation.
        """
        if method not in CREDIT_SCHEDULE:
            logger.warning(f"Unknown method {method} in credit schedule")
            return 1  # Default to 1 credit for unknown methods

        rate = CREDIT_SCHEDULE[method]

        # Handle dynamic rates (e.g., getSignatureStatuses)
        if isinstance(rate, dict):
            return rate.get("default", 1)

        return int(rate)

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
        attempt_number: int = 1,
        retry_after_ms: Optional[int] = None,
    ) -> int:
        """
        Record a single RPC request.

        Args:
            section: Component section (must be one of Section enum)
            provider: Provider (helius_rpc, helius_enhanced, public_rpc_fallback, etc.)
            method: RPC method (getTransaction) or pseudo-method
            status_code: HTTP/RPC status code
            latency_ms: Request latency in milliseconds
            mode: realtime or background
            retries: Number of retries before this attempt (0 = first attempt)
            bytes_in: Request body bytes
            bytes_out: Response body bytes
            source_file: File/process making the call
            error: Error message if failed
            attempt_number: Which attempt in retry chain (1-indexed)
            retry_after_ms: Retry-After header value if 429

        Returns:
            Credits consumed for this request
        """
        with self._lock:
            # Validate section (log warning but don't fail)
            if not Section.is_valid(section):
                logger.warning(f"Unknown section '{section}'. Valid sections: {Section.all_sections()}")

            # Compute credits
            credits = self._compute_credits(method, status_code)

            # Determine success/failure
            is_success = status_code == 200
            is_retry = status_code == 429 or retries > 0

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
                source_file=source_file,
                bytes_in=bytes_in,
                bytes_out=bytes_out,
                credits=credits,
                error=error,
                is_retry=is_retry,
                is_success=is_success,
                attempt_number=attempt_number,
                retry_after_ms=retry_after_ms,
            )

            # Store in history
            self._history.append(record)

            # Update section stats
            section_stats = self._section_stats[section]
            section_stats.requests_total += 1

            # v2: Track success vs failure
            if is_success:
                section_stats.requests_success += 1
                section_stats.credits_success_only += credits
            else:
                section_stats.requests_failed += 1

            # v2: Always track all attempts
            section_stats.credits_all_attempts += credits

            section_stats.latencies.append(latency_ms)
            section_stats.credits_by_method[method] = section_stats.credits_by_method.get(method, 0) + credits
            section_stats.credits_by_provider[provider] = section_stats.credits_by_provider.get(provider, 0) + credits

            # v2: Track retries
            if retries > 0:
                section_stats.retries_total += retries
                section_stats.retries_by_method[method] = section_stats.retries_by_method.get(method, 0) + retries
                section_stats.requests_with_retries += 1

            # Track errors
            if status_code >= 400:
                section_stats.errors += 1
                self._total_errors += 1

            if status_code == 429:
                section_stats.rate_limits_429 += 1
                section_stats.requests_429 += 1
                self._total_429s += 1
                self._429_timestamps.append(time.time())

            # Update global totals
            self._total_requests += 1
            self._total_credits += credits
            self._total_credits_all_attempts += credits
            self._daily_credits += credits

            if is_success:
                self._total_requests_success += 1
                self._total_credits_success += credits
            else:
                self._total_requests_failed += 1

            if retries > 0:
                self._total_retries += retries

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
                is_success=True,
                is_retry=False,
            )

            self._history.append(record)

            # Update section stats
            section_stats = self._section_stats[section]
            section_stats.requests_total += 1
            section_stats.requests_success += 1
            section_stats.credits_success_only += credits
            section_stats.credits_all_attempts += credits
            section_stats.credits_by_method[f"{stream_name}_bytes"] = section_stats.credits_by_method.get(f"{stream_name}_bytes", 0) + credits
            section_stats.credits_by_provider[provider] = section_stats.credits_by_provider.get(provider, 0) + credits

            # Update global totals
            self._total_requests += 1
            self._total_credits += credits
            self._total_credits_success += credits
            self._total_credits_all_attempts += credits
            self._total_requests_success += 1
            self._daily_credits += credits

            return credits

    def get_summary(self) -> Dict:
        """
        Get high-level summary with v2 enhancements.

        Returns:
            Dict with credits_success_only, credits_all_attempts, etc.
        """
        with self._lock:
            uptime = (time.time() - self._start_time) / 60
            daily_elapsed = (datetime.now() - self._daily_reset_time).total_seconds() / 60
            daily_rate = self._daily_credits / daily_elapsed if daily_elapsed > 0 else 0

            # Get 429s in last 5 minutes
            five_min_ago = time.time() - (5 * 60)
            count_429_last_5min = sum(1 for ts in self._429_timestamps if ts > five_min_ago)

            return {
                "timestamp": datetime.now().isoformat(),
                "uptime_minutes": round(uptime, 2),

                # v2: Success vs all attempts
                "credits_success_only": self._total_credits_success,
                "credits_all_attempts": self._total_credits_all_attempts,
                "credits_total": self._total_credits,

                "requests_total": self._total_requests,
                "requests_success": self._total_requests_success,
                "requests_failed": self._total_requests_failed,

                "errors_total": self._total_errors,
                "rate_limits_429_total": self._total_429s,
                "rate_limits_429_last_5min": count_429_last_5min,

                # Retry stats
                "retries_total": self._total_retries,
                "avg_retries_per_request": self._total_retries / self._total_requests if self._total_requests > 0 else 0,

                # Daily tracking
                "credits_today": self._daily_credits,
                "credits_per_minute": round(daily_rate, 2),

                "sections_active": len(self._section_stats),
            }

    def get_section_stats(self) -> Dict[str, Dict]:
        """
        Get detailed stats per section with v2 enhancements.

        Returns:
            Dict mapping section names to stats including credits_success_only
        """
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
                    # v2: Success tracking
                    "credits_success_only": stats.credits_success_only,
                    "credits_all_attempts": stats.credits_all_attempts,
                    "requests_success": stats.requests_success,
                    "requests_failed": stats.requests_failed,
                    "requests_429": stats.requests_429,

                    "requests": stats.requests_total,
                    "errors": stats.errors,
                    "rate_limits_429": stats.rate_limits_429,
                    "avg_latency_ms": round(stats.avg_latency, 2),
                    "p95_latency_ms": round(stats.p95_latency, 2),

                    # v2: Retry stats
                    "retries_total": stats.retries_total,
                    "avg_retries_per_request": round(stats.avg_retries_per_request, 2),
                    "requests_with_retries": stats.requests_with_retries,

                    "top_methods": [{"method": m, "credits": c} for m, c in top_methods],
                }

            return result

    def get_top_methods(self, limit: int = 10) -> List[Dict]:
        """Get top methods by credits with v2 breakdown"""
        with self._lock:
            method_credits_success = defaultdict(int)
            method_credits_all = defaultdict(int)
            method_requests = defaultdict(int)
            method_retries = defaultdict(int)

            for stats in self._section_stats.values():
                for method, credits in stats.credits_by_method.items():
                    # v2: Need to separate success from all - estimate based on proportion
                    # This is an approximation since we track at section level
                    if stats.requests_total > 0:
                        success_ratio = stats.requests_success / stats.requests_total
                        method_credits_success[method] += int(credits * success_ratio)
                    method_credits_all[method] += credits
                for method, retries in stats.retries_by_method.items():
                    method_retries[method] += retries

            # Get counts from history
            for record in self._history:
                method_requests[record.method] += 1

            result = []
            for method in method_credits_all:
                result.append({
                    "method": method,
                    "credits_success": method_credits_success.get(method, 0),
                    "credits_all_attempts": method_credits_all.get(method, 0),
                    "requests": method_requests.get(method, 0),
                    "retries_total": method_retries.get(method, 0),
                    "avg_retries": round(method_retries.get(method, 0) / max(method_requests.get(method, 1), 1), 2),
                })

            # Sort by all_attempts
            result.sort(key=lambda x: x["credits_all_attempts"], reverse=True)
            return result[:limit]

    def get_alerts(self, burn_rate_threshold: float = 100.0) -> List[Dict]:
        """
        Get active alerts including v2 rate limit details.

        Args:
            burn_rate_threshold: Alert if credits/min exceeds this

        Returns:
            List of alert dicts
        """
        with self._lock:
            alerts = []

            # Burn rate alert
            daily_elapsed = (datetime.now() - self._daily_reset_time).total_seconds() / 60
            if daily_elapsed > 0:
                burn_rate = self._daily_credits / daily_elapsed
                if burn_rate > burn_rate_threshold:
                    alerts.append({
                        "level": "warning",
                        "type": "high_burn_rate",
                        "message": f"Burn rate {burn_rate:.2f} credits/min exceeds threshold {burn_rate_threshold}",
                    })

            # High 429 rate alert
            five_min_ago = time.time() - (5 * 60)
            count_429_last_5min = sum(1 for ts in self._429_timestamps if ts > five_min_ago)
            if count_429_last_5min > 10:
                alerts.append({
                    "level": "warning",
                    "type": "high_rate_limit",
                    "message": f"{count_429_last_5min} rate limit errors in last 5 minutes",
                })

            # Error rate alert
            if self._total_requests > 0:
                error_rate = (self._total_errors / self._total_requests) * 100
                if error_rate > 5.0:
                    alerts.append({
                        "level": "warning",
                        "type": "high_error_rate",
                        "message": f"Error rate {error_rate:.1f}% exceeds threshold 5.0%",
                    })

            return alerts

    def get_rate_limit_diagnostics(self) -> Dict:
        """
        Get comprehensive 429 rate limit diagnostics.

        Returns:
            Dict with 429 breakdown by section, method, source file
        """
        with self._lock:
            by_section = defaultdict(int)
            by_method = defaultdict(int)
            by_source_file = defaultdict(int)
            retry_after_values = []
            attempt_counts = defaultdict(int)

            for record in self._history:
                if record.status_code == 429:
                    by_section[record.section] += 1
                    by_method[record.method] += 1
                    by_source_file[record.source_file] += 1
                    if record.retry_after_ms:
                        retry_after_values.append(record.retry_after_ms)
                    attempt_counts[record.attempt_number] += 1

            five_min_ago = time.time() - (5 * 60)
            count_429_last_5min = sum(1 for ts in self._429_timestamps if ts > five_min_ago)

            avg_retry_after = (sum(retry_after_values) / len(retry_after_values)) if retry_after_values else 0

            return {
                "timestamp": datetime.now().isoformat(),
                "total_429_count": self._total_429s,
                "last_5min_429_count": count_429_last_5min,
                "429_by_section": dict(by_section),
                "429_by_method": dict(by_method),
                "429_by_source_file": dict(by_source_file),
                "avg_retry_after_ms": round(avg_retry_after, 1),
                "attempts_by_attempt_number": dict(attempt_counts),
            }

    def get_retry_diagnostics(self) -> Dict[str, Dict]:
        """
        Get retry diagnostics per section.

        Returns:
            Dict mapping section names to retry stats
        """
        with self._lock:
            result = {}
            for section, stats in self._section_stats.items():
                result[section] = {
                    "total_requests": stats.requests_total,
                    "total_retries": stats.retries_total,
                    "avg_retries_per_request": round(stats.avg_retries_per_request, 2),
                    "requests_with_retries": stats.requests_with_retries,
                    "retries_by_method": dict(stats.retries_by_method),
                }
            return result

    def get_source_file_stats(self) -> Dict[str, Dict]:
        """
        Get stats grouped by source file with v2 enhancements.

        Returns:
            Dict mapping source files to stats including credits_success_only
        """
        with self._lock:
            source_stats = {}

            # Aggregate by source file
            for record in self._history:
                source = record.source_file or "unknown"
                if source not in source_stats:
                    source_stats[source] = {
                        'requests': 0,
                        'requests_success': 0,
                        'requests_failed': 0,
                        'requests_429': 0,
                        'credits_success_only': 0,
                        'credits_all_attempts': 0,
                        'errors': 0,
                        'rate_limits_429': 0,
                        'latencies': [],
                        'sections': {},
                        'methods': {},
                        'retries_total': 0,
                    }

                stats = source_stats[source]
                stats['requests'] += 1
                if record.is_success:
                    stats['requests_success'] += 1
                else:
                    stats['requests_failed'] += 1
                if record.status_code == 429:
                    stats['requests_429'] += 1

                # v2: Track success vs all attempts
                stats['credits_all_attempts'] += record.credits
                if record.is_success:
                    stats['credits_success_only'] += record.credits

                stats['latencies'].append(record.latency_ms)
                stats['retries_total'] += record.retries

                # Track sections and methods
                if record.section not in stats['sections']:
                    stats['sections'][record.section] = 0
                stats['sections'][record.section] += 1

                if record.method not in stats['methods']:
                    stats['methods'][record.method] = 0
                stats['methods'][record.method] += 1

                if record.status_code >= 400:
                    stats['errors'] += 1
                if record.status_code == 429:
                    stats['rate_limits_429'] += 1

            # Calculate percentiles and format for API
            result = {}
            for source, stats in source_stats.items():
                latencies = sorted(stats['latencies'])
                result[source] = {
                    'credits_success_only': stats['credits_success_only'],
                    'credits_all_attempts': stats['credits_all_attempts'],
                    'requests': stats['requests'],
                    'requests_success': stats['requests_success'],
                    'requests_failed': stats['requests_failed'],
                    'requests_429': stats['requests_429'],
                    'errors': stats['errors'],
                    'rate_limits_429': stats['rate_limits_429'],
                    'avg_latency_ms': round(sum(stats['latencies']) / len(stats['latencies']), 2) if stats['latencies'] else 0,
                    'p95_latency_ms': round(latencies[int(len(latencies) * 0.95)], 2) if latencies else 0,
                    'retries_total': stats['retries_total'],
                    'avg_retries_per_request': round(stats['retries_total'] / stats['requests'], 2) if stats['requests'] > 0 else 0,
                    'sections': dict(sorted(stats['sections'].items(), key=lambda x: x[1], reverse=True)),
                    'top_methods': [
                        {'method': m, 'calls': c}
                        for m, c in sorted(stats['methods'].items(), key=lambda x: x[1], reverse=True)[:5]
                    ],
                }

            return dict(sorted(result.items(), key=lambda x: x[1]['credits_all_attempts'], reverse=True))

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
                "rate_limit_diagnostics": self.get_rate_limit_diagnostics(),
                "retry_diagnostics": self.get_retry_diagnostics(),
                "alerts": self.get_alerts(),
            }, indent=2)


# ============================================================================
# GLOBAL INSTANCE & CONVENIENCE FUNCTIONS
# ============================================================================

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
    source_file: str = "unknown",
    error: Optional[str] = None,
    attempt_number: int = 1,
    retry_after_ms: Optional[int] = None,
) -> int:
    """Convenience function to record request with global instance"""
    credits = get_recorder().record_request(
        section, provider, method, status_code, latency_ms, mode, retries,
        bytes_in, bytes_out, source_file, error, attempt_number, retry_after_ms
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
                'source_file': source_file,
                'bytes_in': bytes_in,
                'bytes_out': bytes_out,
                'error': error,
                'attempt_number': attempt_number,
                'retry_after_ms': retry_after_ms,
            },
            timeout=1
        )
    except Exception:
        pass  # Fail silently if API not available

    return credits
