"""Bounded DEV-only live-current layer for the operation Monitor.

The durable worker owns strict opening and completed 15-minute evidence.  This
module supplies the intentionally separate, ephemeral 15-second current-price
clock for the isolated DEV monitor only.  It retains one compact current/peak
projection per fact; it never retains raw responses or per-tick observations.
"""
from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.ops.dev_provider_budget import BudgetDenied
from src.ops.operation_monitor_worker import MonitorQueue
from src.ops.provider_rate_limit_gate import ProviderRateLimited

CADENCE_SECONDS = 15
MAX_ACTIVE_TOKENS = 64
GLOBAL_CALLS_PER_MINUTE = 20
PER_TOKEN_CALLS_PER_MINUTE = 4
TERMINAL_DRAWDOWN_PERCENT = 85.0


@dataclass
class ActivePrice:
    operation_id: str
    mint: str
    entry_mc_usd: float
    entry_timestamp: int
    current_mc_usd: float
    peak_mc_usd: float
    peak_timestamp: int
    due_at: int


class DevCurrentPriceScheduler:
    """One-process fair scheduler; restart reconstructs only nonterminal facts."""

    def __init__(self, *, db_path: str, queue: MonitorQueue,
                 fetch: Callable[[str, int, int], list[dict]],
                 now: Callable[[], float] = time.time) -> None:
        if os.getenv("MONITOR_RUNTIME") != "dev":
            raise RuntimeError("DEV_CURRENT_PRICE_SCHEDULER_REQUIRES_DEV_RUNTIME")
        self.db_path = str(Path(db_path).resolve())
        self.queue = queue
        self.fetch = fetch
        self.now = now
        self.active: dict[str, ActivePrice] = {}
        self._cursor = 0

    def sync(self) -> int:
        """Discover qualified active facts without admitting pending opening rows."""
        with sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True) as con:
            rows = con.execute("""
                SELECT operation_id,mint,entry_mc_usd,entry_timestamp,latest_mc_usd,
                       running_peak_mc_usd,running_peak_timestamp,next_observation_at
                FROM operation_monitor_facts
                WHERE lower(operation_id) IN ('watchtower','watchtower_deep')
                  AND monitor_state='MONITORING_ACTIVE'
                  AND entry_status='QUALIFIED'
                  AND entry_mc_usd IS NOT NULL AND entry_timestamp IS NOT NULL
                ORDER BY assignment_timestamp DESC LIMIT ?
            """, (MAX_ACTIVE_TOKENS,)).fetchall()
        now = int(self.now())
        fresh: dict[str, ActivePrice] = {}
        for op, mint, entry, entry_ts, current, peak, peak_ts, due in rows:
            previous = self.active.get(str(mint))
            fresh[str(mint)] = ActivePrice(
                str(op), str(mint), float(entry), int(entry_ts),
                float(current if current is not None else entry),
                float(peak if peak is not None else current if current is not None else entry),
                int(peak_ts if peak_ts is not None else entry_ts),
                previous.due_at if previous else now,
            )
        self.active = fresh
        return len(fresh)

    def _persist(self, item: ActivePrice, *, current: float, high: float, timestamp: int) -> bool:
        peak, peak_ts = item.peak_mc_usd, item.peak_timestamp
        if high > peak:
            peak, peak_ts = high, timestamp
        drawdown = (peak - current) * 100.0 / peak if peak else 0.0
        terminal = drawdown >= TERMINAL_DRAWDOWN_PERCENT
        with sqlite3.connect(self.db_path) as con:
            result = con.execute("""
                UPDATE operation_monitor_facts
                SET latest_mc_usd=?,latest_mc_timestamp=?,current_multiple=?,
                    running_peak_mc_usd=?,running_peak_timestamp=?,running_peak_multiple=?,
                    drawdown_percent=?,last_observation_at=?,next_observation_at=?,
                    monitor_state=?,monitor_completed_at=?,evidence_status=?,updated_at=?
                WHERE operation_id=? AND mint=? AND monitor_state='MONITORING_ACTIVE'
            """, (current, timestamp, current / item.entry_mc_usd, peak, peak_ts,
                  peak / item.entry_mc_usd, drawdown, timestamp,
                  None if terminal else timestamp + CADENCE_SECONDS,
                  'PRICE_MONITOR_COMPLETE_COLLAPSED' if terminal else 'MONITORING_ACTIVE',
                  timestamp if terminal else None,
                  'LIVE_CURRENT_COMPACT' if not terminal else 'LIVE_CURRENT_TERMINAL',
                  timestamp, item.operation_id, item.mint))
        if not result.rowcount:
            return False
        item.current_mc_usd, item.peak_mc_usd, item.peak_timestamp = current, peak, peak_ts
        if terminal:
            self.queue.enqueue_terminal_ath_finalization({
                'operation_id': item.operation_id, 'mint': item.mint,
                'cohort_class': 'PROSPECTIVE_MONITOR_COHORT',
                'monitor_state': 'PRICE_MONITOR_COMPLETE_COLLAPSED',
                'next_observation_at': None, 'final_proven_ath_mc': None,
                'entry_method': 'FIRST_FULL_POST_MIGRATION_SECOND_MC',
                'entry_timestamp': item.entry_timestamp, 'entry_mc_usd': item.entry_mc_usd,
                'monitor_completed_at': timestamp,
            }, provenance='DEV_CURRENT_PRICE_TERMINAL')
            self.active.pop(item.mint, None)
        return True

    def tick(self) -> dict[str, int | str]:
        """Make at most one fair live request per scheduler tick."""
        now = int(self.now())
        self.sync()
        if not self.queue.provider_eligible(now=now):
            return {'state': 'PROVIDER_BACKOFF', 'active': len(self.active), 'updated': 0}
        ordered = sorted(self.active)
        if not ordered:
            return {'state': 'IDLE', 'active': 0, 'updated': 0}
        start = self._cursor % len(ordered)
        for offset in range(len(ordered)):
            mint = ordered[(start + offset) % len(ordered)]
            item = self.active.get(mint)
            if item is None or now < item.due_at:
                continue
            self._cursor = (start + offset + 1) % len(ordered)
            try:
                self.queue.admit_provider_dispatch(mint, 'LIVE_CURRENT', now=now,
                    global_limit=GLOBAL_CALLS_PER_MINUTE, token_limit=PER_TOKEN_CALLS_PER_MINUTE)
                candles = self.fetch(mint, max(item.entry_timestamp, now - 120), now)
            except ProviderRateLimited as error:
                self.queue.apply_provider_rate_limit(metadata=error.metadata, now=now)
                item.due_at = now + CADENCE_SECONDS
                return {'state': 'HTTP_429', 'active': len(self.active), 'updated': 0}
            except (BudgetDenied, RuntimeError, ConnectionError):
                item.due_at = now + CADENCE_SECONDS
                return {'state': 'DEFERRED', 'active': len(self.active), 'updated': 0}
            usable = [row for row in candles if float(row.get('close', 0) or 0) > 0]
            item.due_at = now + CADENCE_SECONDS
            if not usable:
                return {'state': 'NO_OBSERVATION', 'active': len(self.active), 'updated': 0}
            latest = max(usable, key=lambda row: int(row.get('timestamp', 0) or 0))
            self._persist(item, current=float(latest['close']),
                          high=max(float(row.get('high', row['close']) or row['close']) for row in usable),
                          timestamp=int(latest.get('timestamp') or now))
            return {'state': 'UPDATED', 'active': len(self.active), 'updated': 1}
        return {'state': 'NOT_DUE', 'active': len(self.active), 'updated': 0}
