"""Bounded 15-minute historical reconstruction with explicit provider gaps.

This is deliberately separate from opening qualification and the live monitor.
It persists only valid MCAP OHLC candles plus one idempotent coverage fact per
requested window; a missing provider candle is never manufactured as price data.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Callable

from src.ops.token_data_provider_bindings import BirdeyeProductionBinding, build_birdeye_ohlcv_request
from src.ops.watchtower_terminal_ath_finalizer import _candles

BUCKET_SECONDS = 900
MAX_PROVEN_BUCKETS_PER_REQUEST = 17
MAX_429_RETRIES_PER_REQUEST = 1
SOURCE = "BIRDEYE_HISTORICAL_15M"


class ProviderCapacityBlocked(RuntimeError):
    def __init__(self, headers: dict[str, str], *, request_at: int | None = None, response_at: int | None = None):
        super().__init__("HTTP_429")
        self.headers = headers
        self.request_at = request_at
        self.response_at = response_at


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def ceil_bucket(timestamp: int) -> int:
    return int(math.ceil(int(timestamp) / BUCKET_SECONDS) * BUCKET_SECONDS)


def expected_request_count(*, start_timestamp: int, end_timestamp: int) -> int:
    """Return the preflight-bounded request count for an aligned job window."""
    start, end = ceil_bucket(start_timestamp), int(end_timestamp)
    if end % BUCKET_SECONDS or end <= start:
        raise ValueError("HISTORICAL_WINDOW_INVALID")
    return math.ceil(len(range(start, end, BUCKET_SECONDS)) / MAX_PROVEN_BUCKETS_PER_REQUEST)


def ensure_historical_coverage_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS historical_price_coverage (
        operation_id TEXT NOT NULL, mint TEXT NOT NULL, resolution TEXT NOT NULL,
        window_start INTEGER NOT NULL, window_end INTEGER NOT NULL,
        requested_bucket_count INTEGER NOT NULL, returned_valid_candle_count INTEGER NOT NULL,
        no_provider_candle_count INTEGER NOT NULL, invalid_provider_row_count INTEGER NOT NULL,
        duplicate_provider_row_count INTEGER NOT NULL, request_count INTEGER NOT NULL,
        provenance_digest TEXT NOT NULL, updated_at INTEGER NOT NULL,
        PRIMARY KEY(operation_id,mint,resolution,window_start,window_end)
    )""")


def metric_value(candles: list[dict[str, float | int]], target: int) -> tuple[float | None, str]:
    row = next((x for x in candles if int(x["timestamp"]) == target), None)
    return (float(row["close"]), "QUALIFIED") if row else (None, "INSUFFICIENT_PROVIDER_COVERAGE")


@dataclass
class Historical15mReconstructor:
    db_path: str
    binding: BirdeyeProductionBinding
    before_dispatch: Callable[[str, str], Any] | None = None
    on_429: Callable[[dict[str, str], int, int], Any] | None = None
    wait_for_capacity: Callable[[], Any] | None = None
    now: Callable[[], float] = time.time
    max_429_retries_per_request: int = MAX_429_RETRIES_PER_REQUEST

    def _persist(self, operation_id: str, mint: str, start: int, end: int, candles: list[dict[str, float | int]], gaps: list[int], invalid: int, duplicate: int, requests: list[dict[str, Any]]) -> dict[str, int]:
        stamp = int(self.now())
        inserted = repaired = deduplicated = 0
        with sqlite3.connect(self.db_path) as conn:
            ensure_historical_coverage_schema(conn)
            for candle in candles:
                row = conn.execute("SELECT open_mc_usd,high_mc_usd,low_mc_usd,close_mc_usd FROM operation_monitor_observations WHERE operation_id=? AND mint=? AND observation_timestamp=? AND resolution='15m'", (operation_id,mint,int(candle["timestamp"]))).fetchone()
                params=(operation_id,mint,int(candle["timestamp"]),float(candle["close"]),"15m",SOURCE,_digest({"mint":mint,"timestamp":int(candle["timestamp"]),"resolution":"15m"}),_digest(candle),stamp,float(candle["open"]),float(candle["high"]),float(candle["low"]),float(candle["close"]))
                if row and all(x is not None for x in row):
                    deduplicated += 1; continue
                if row:
                    conn.execute("UPDATE operation_monitor_observations SET mc_usd=?,source=?,request_identity=?,provenance_digest=?,created_at=?,open_mc_usd=?,high_mc_usd=?,low_mc_usd=?,close_mc_usd=? WHERE operation_id=? AND mint=? AND observation_timestamp=? AND resolution='15m'", (float(candle["close"]),SOURCE,params[6],params[7],stamp,float(candle["open"]),float(candle["high"]),float(candle["low"]),float(candle["close"]),operation_id,mint,int(candle["timestamp"])))
                    repaired += 1
                else:
                    conn.execute("INSERT INTO operation_monitor_observations(operation_id,mint,observation_timestamp,mc_usd,resolution,source,request_identity,provenance_digest,created_at,open_mc_usd,high_mc_usd,low_mc_usd,close_mc_usd) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",params)
                    inserted += 1
            coverage=(operation_id,mint,"15m",start,end,len(range(start,end,BUCKET_SECONDS)),len(candles),len(gaps),invalid,duplicate,len(requests),_digest({"requests":[x["request_parameters"] for x in requests],"gaps":gaps,"candles":candles}),stamp)
            conn.execute("INSERT INTO historical_price_coverage(operation_id,mint,resolution,window_start,window_end,requested_bucket_count,returned_valid_candle_count,no_provider_candle_count,invalid_provider_row_count,duplicate_provider_row_count,request_count,provenance_digest,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(operation_id,mint,resolution,window_start,window_end) DO UPDATE SET requested_bucket_count=excluded.requested_bucket_count,returned_valid_candle_count=excluded.returned_valid_candle_count,no_provider_candle_count=excluded.no_provider_candle_count,invalid_provider_row_count=excluded.invalid_provider_row_count,duplicate_provider_row_count=excluded.duplicate_provider_row_count,request_count=excluded.request_count,provenance_digest=excluded.provenance_digest,updated_at=excluded.updated_at",coverage)
        return {"inserted":inserted,"repaired":repaired,"deduplicated":deduplicated}

    def reconstruct(self, operation_id: str, mint: str, entry_timestamp: int, *, end_timestamp: int) -> dict[str, Any]:
        start, end = ceil_bucket(entry_timestamp), int(end_timestamp)
        if end % BUCKET_SECONDS:
            raise ValueError("HISTORICAL_WINDOW_END_MUST_BE_BUCKET_ALIGNED")
        if end <= start: raise ValueError("HISTORICAL_WINDOW_EMPTY")
        all_candles: dict[int, dict[str, float | int]] = {}; invalid = duplicate = 0; requests: list[dict[str, Any]] = []
        for left in range(start, end, MAX_PROVEN_BUCKETS_PER_REQUEST * BUCKET_SECONDS):
            right=min(left + MAX_PROVEN_BUCKETS_PER_REQUEST * BUCKET_SECONDS,end)
            # The provider request builder correctly rejects a zero-length
            # range.  A one-bucket historical job still needs one bounded
            # request, so use its exclusive upper boundary; returned rows are
            # filtered below to the exact [left, right) job window.
            request_end = right - BUCKET_SECONDS if right - BUCKET_SECONDS > left else right
            request=build_birdeye_ohlcv_request(address=mint,interval="15m",time_from=left,time_to=request_end)
            retry_count = 0
            while True:
                if self.before_dispatch: self.before_dispatch(mint,"HISTORICAL_15M")
                request_at=int(self.now())
                outcome=self.binding(request)
                response_at=int(self.now())
                requests.append({**request,"request_at":request_at,"response_at":response_at,"http_status":outcome.status_code})
                if outcome.status_code != 429: break
                if self.on_429: self.on_429(dict(outcome.response_headers), request_at, response_at)
                retry_count += 1
                if retry_count <= self.max_429_retries_per_request and self.wait_for_capacity:
                    self.wait_for_capacity()
                    continue
                raise ProviderCapacityBlocked(dict(outcome.response_headers),request_at=request_at,response_at=response_at)
            if outcome.status_code != 200: raise RuntimeError(f"HISTORICAL_PROVIDER_HTTP_{outcome.status_code}")
            try: rows=_candles(outcome.payload or {})
            except ValueError: invalid += 1; rows=[]
            for candle in rows:
                timestamp=int(candle["timestamp"])
                if timestamp < left or timestamp >= right: invalid += 1; continue
                if timestamp in all_candles: duplicate += 1; continue
                all_candles[timestamp]=candle
        candles=[all_candles[x] for x in sorted(all_candles)]
        gaps=[x for x in range(start,end,BUCKET_SECONDS) if x not in all_candles]
        write=self._persist(operation_id,mint,start,end,candles,gaps,invalid,duplicate,requests)
        completeness = "COMPLETE" if not gaps and candles else "PARTIAL" if candles else "INSUFFICIENT_EVIDENCE"
        return {"start":start,"end":end,"requested_buckets":len(range(start,end,BUCKET_SECONDS)),"provider_requests":len(requests),"candles":candles,"gaps":gaps,"invalid_provider_rows":invalid,"duplicate_provider_rows":duplicate,"requests":requests,"completeness":completeness,**write}
