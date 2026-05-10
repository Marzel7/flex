"""
launch_price_logger.py
Records the gap between token detection and first price fetch.

Detection time is read from token_analysis.created_at (written by the listener
process) so this works correctly across the two-process architecture.

Log file: logs/launch_price.log
Columns (TSV):
  event  timestamp_iso  unix_ts  detected_iso  gap_secs  price_usd  market_cap  source  mint
"""

import os
import time
import sqlite3
from src.utils.db_locking import db_connect
import threading
import logging
import logging.handlers

_LOG_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "logs", "launch_price.log")
)
_DB_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "database", "flex_complete_database.db")
)

_logger = logging.getLogger("launch_price")

# Per-process guard: only log first price once per mint per process lifetime
_first_price_logged: set[str] = set()
_lock = threading.Lock()

# Rotating file writer — 10 MB per file, 2 backups
os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
_rotating_handler = logging.handlers.RotatingFileHandler(
    _LOG_PATH, maxBytes=10 * 1024 * 1024, backupCount=2, encoding="utf-8"
)


def _write(line: str) -> None:
    try:
        record = logging.makeLogRecord({"msg": line, "levelno": logging.INFO})
        _rotating_handler.emit(record)
    except Exception as e:
        _logger.warning(f"[LAUNCH_PRICE_LOG] write failed: {e}")


def _get_detected_at(mint: str) -> float | None:
    """Read token detection time from DB (written by listener process)."""
    try:
        conn = db_connect(_DB_PATH, timeout=2)
        row = conn.execute(
            "SELECT created_at FROM token_analysis WHERE mint = ?", (mint,)
        ).fetchone()
        conn.close()
        if not row or not row[0]:
            return None
        val = row[0]
        # Handle unix float stored as number or string
        try:
            f = float(val)
            if f > 1_000_000_000:  # looks like unix timestamp
                return f
        except (TypeError, ValueError):
            pass
        # ISO string e.g. "2026-04-05T19:09:06Z"
        import datetime
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.datetime.strptime(str(val).split(".")[0].rstrip("Z"), fmt.rstrip("Z"))
                return dt.replace(tzinfo=datetime.timezone.utc).timestamp()
            except ValueError:
                continue
        return None
    except Exception:
        return None


def record_first_price(mint: str, price_usd: float, market_cap: float, source: str = "") -> None:
    """
    Call on the first price_update broadcast for a mint.
    Safe to call from any process — reads detection time from DB.
    """
    with _lock:
        if mint in _first_price_logged:
            return
        _first_price_logged.add(mint)

    now = time.time()
    detected_at = _get_detected_at(mint)

    if detected_at is not None:
        gap_secs = now - detected_at
        gap_str = f"{gap_secs:.1f}s"
        det_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(detected_at))
    else:
        gap_str = "unknown"
        det_iso = "unknown"

    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))

    line = (
        f"FIRST_PRICE\t{now_iso}\t{now:.3f}\t{det_iso}\t"
        f"{gap_str}\t{price_usd:.8f}\t{market_cap:.2f}\t{source}\t{mint}"
    )
    _write(line)
    _logger.info(
        f"[LAUNCH_PRICE] FIRST_PRICE mint={mint[:16]}… "
        f"gap={gap_str} price=${price_usd:.6f} mc=${market_cap:,.0f} source={source}"
    )
