"""One-shot, bounded Watchtower terminal ATH finalization.

This is deliberately separate from routine Monitor polling: it may operate only
on an already-collapsed Watchtower fact, has one deterministic identity per
terminal lifecycle, and makes no queue or lifecycle-state transition.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from src.core.db_write_queue import WriteItem
from src.core.db_writer import commit_write_and_wait
from src.ops.token_data_provider_bindings import BirdeyeProductionBinding, build_birdeye_ohlcv_request
from src.ops.watchtower_price_fact_contract import reduce_watchtower_price_facts


TERMINAL_STATE = "PRICE_MONITOR_COMPLETE_COLLAPSED"
FINALIZER_VERSION = "watchtower-terminal-ath.v1"


class ProviderCapacityBackoff(ConnectionError):
    """A single Birdeye 429; queue ownership retains retry/backoff state."""
    def __init__(self, retry_after: int | None, message: str = "HTTP_429") -> None:
        super().__init__(message)
        self.retry_after = retry_after


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _candles(payload: dict[str, Any]) -> list[dict[str, float | int]]:
    items = ((payload.get("data") or {}).get("items") or [])
    result = []
    for item in items:
        try:
            candle = {
                "timestamp": int(item.get("unixTime", item.get("unix_time", item.get("timestamp")))),
                "open": float(item.get("o", item.get("open"))),
                "high": float(item.get("h", item.get("high"))),
                "low": float(item.get("l", item.get("low"))),
                "close": float(item.get("c", item.get("close"))),
            }
        except (TypeError, ValueError):
            raise ValueError("MALFORMED_OHLC_CANDLE")
        if candle["timestamp"] <= 0 or candle["low"] < 0 or candle["high"] < candle["low"] or candle["high"] < candle["open"] or candle["high"] < candle["close"] or candle["low"] > candle["open"] or candle["low"] > candle["close"]:
            raise ValueError("INVALID_OHLC_INVARIANT")
        result.append(candle)
    return sorted(result, key=lambda row: int(row["timestamp"]))


class WatchtowerTerminalAthFinalizer:
    def __init__(self, db_path: str, *, binding: BirdeyeProductionBinding | None = None, now: Any = time.time):
        self.db_path = str(db_path)
        self.binding = binding or BirdeyeProductionBinding()
        self.now = now

    def freeze(self, mint: str) -> dict[str, Any]:
        with sqlite3.connect(f"file:{Path(self.db_path).resolve()}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM operation_monitor_facts WHERE operation_id='watchtower' AND mint=?", (mint,)).fetchone()
            if not row:
                raise ValueError("TERMINAL_FACT_NOT_FOUND")
            fact = dict(row)
            fact["observation_count"] = conn.execute("SELECT count(*) FROM operation_monitor_observations WHERE operation_id='watchtower' AND mint=?", (mint,)).fetchone()[0]
        if fact["monitor_state"] != TERMINAL_STATE or fact["next_observation_at"] is not None:
            raise ValueError("TERMINAL_FACT_REQUIRED")
        if fact.get("cohort_class") != "PROSPECTIVE_MONITOR_COHORT":
            raise ValueError("PROSPECTIVE_MONITOR_COHORT_REQUIRED")
        if not fact.get("entry_timestamp") or not fact.get("monitor_completed_at"):
            raise ValueError("ATH_WINDOW_UNQUALIFIED")
        fact["before_finalization_digest"] = _digest(fact)
        return fact

    @staticmethod
    def logical_job_identity(fact: dict[str, Any]) -> str:
        """Stable per terminal lifecycle; it deliberately excludes wall time/result."""
        return _digest({
            "contract": FINALIZER_VERSION,
            "operation_id": "watchtower",
            "mint": fact["mint"],
            "entry_method": fact["entry_method"],
            "entry_timestamp": int(fact["entry_timestamp"]),
            "entry_mc_usd": float(fact["entry_mc_usd"]),
            "terminal_timestamp": int(fact["monitor_completed_at"]),
            "resolution": "15m",
        })

    def _retained_15m_ohlc(self, mint: str, start: int, end: int) -> list[dict[str, float | int]]:
        """Read compact retained highs; never reconstruct them from close-only rows."""
        with sqlite3.connect(f"file:{Path(self.db_path).resolve()}?mode=ro", uri=True) as conn:
            rows = conn.execute("SELECT observation_timestamp,open_mc_usd,high_mc_usd,low_mc_usd,close_mc_usd FROM operation_monitor_observations WHERE operation_id='watchtower' AND mint=? AND resolution='15m' AND high_mc_usd IS NOT NULL AND open_mc_usd IS NOT NULL AND low_mc_usd IS NOT NULL AND close_mc_usd IS NOT NULL ORDER BY observation_timestamp", (mint,)).fetchall()
        candles=[{'timestamp':int(x[0]),'open':float(x[1]),'high':float(x[2]),'low':float(x[3]),'close':float(x[4])} for x in rows]
        if not candles or candles[0]['timestamp'] > start + 900 or candles[-1]['timestamp'] + 900 < end:
            return []
        if any(int(right['timestamp'])-int(left['timestamp']) > 900 for left,right in zip(candles,candles[1:])):
            return []
        return candles

    def finalize(self, mint: str, *, interval: str = "1m", chunk_seconds: int | None = None, watchtower_price_fact_contract: bool = False) -> dict[str, Any]:
        fact = self.freeze(mint)
        if fact.get("ath_finalization_request_id") and fact.get("final_proven_ath_mc") is not None:
            return {"state": "ALREADY_FINALIZED", "fact": fact, "provider_calls": 0}
        start, end = int(fact["entry_timestamp"]), int(fact["monitor_completed_at"])
        if not start < end:
            raise ValueError("INVALID_ATH_WINDOW")
        seconds = {"1m": 60, "5m": 300, "15m": 900}[interval]
        if chunk_seconds is not None and (chunk_seconds < seconds or chunk_seconds % seconds): raise ValueError("INVALID_CHUNK_SECONDS")
        windows = [(left, min(left + (chunk_seconds or end-start), end)) for left in range(start, end, chunk_seconds or end-start)]
        retained = self._retained_15m_ohlc(mint, start, end) if watchtower_price_fact_contract and interval == '15m' else []
        requests = [] if retained else [build_birdeye_ohlcv_request(address=mint, interval=interval, time_from=left, time_to=right) for left,right in windows]
        request_id = _digest({"version": FINALIZER_VERSION, "operation_id": "watchtower", "mint": mint, "terminal": end, "requests": [r["request_parameters"] for r in requests]})
        candles=list(retained); checkpoint_at=int(self.now())
        for request,(left,right) in zip(requests,windows):
            # Provider work is outside DB connections/transactions.
            outcome = self.binding(request)
            if outcome.status_code != 200:
                if outcome.status_code == 429:
                    raise ProviderCapacityBackoff(outcome.retry_after_seconds)
                payload = outcome.payload if isinstance(outcome.payload, dict) else {}
                raise ConnectionError(json.dumps({"status": outcome.status_code, "code": payload.get("code"), "message": str(payload.get("message", payload.get("msg", "")))[:240]}, sort_keys=True))
            part=_candles(outcome.payload or {})
            if not part or (not watchtower_price_fact_contract and (int(part[0]["timestamp"]) > left or int(part[-1]["timestamp"]) + seconds < right or any(int(b["timestamp"])-int(a["timestamp"])>seconds for a,b in zip(part,part[1:])))):
                raise ValueError("CHUNK_COVERAGE_INCOMPLETE:"+json.dumps({"time_from":left,"time_to":right,"first_bucket":int(part[0]["timestamp"]) if part else None,"last_bucket":int(part[-1]["timestamp"]) if part else None,"candle_count":len(part)},sort_keys=True,separators=(",",":")))
            chunk_id=_digest({"version":FINALIZER_VERSION,"mint":mint,"resolution":interval,"time_from":left,"time_to":right})
            checkpoint_sql='INSERT OR IGNORE INTO operation_monitor_observations(operation_id,mint,observation_timestamp,mc_usd,resolution,source,request_identity,provenance_digest,created_at,open_mc_usd,high_mc_usd,low_mc_usd,close_mc_usd) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)'
            checkpoint=[(checkpoint_sql,("watchtower",mint,int(x['timestamp']),float(x['close']),interval,"BIRDEYE_TERMINAL_ATH_CHUNK",chunk_id,_digest(x),checkpoint_at,float(x['open']),float(x['high']),float(x['low']),float(x['close']))) for x in part]
            receipt=commit_write_and_wait(self.db_path,WriteItem('enrichment','watchtower-terminal-ath-chunk',checkpoint,chunk_id))
            if not receipt.committed: raise RuntimeError('ATH_CHUNK_CHECKPOINT_UNCOMMITTED')
            candles.extend(part)
        candles=sorted({int(x['timestamp']):x for x in candles}.values(),key=lambda x:int(x['timestamp']))
        # Provider buckets are start-aligned: complete coverage means a bucket
        # reaches both bounded endpoints, without claiming sub-bucket precision.
        gaps = [int(right["timestamp"]) - int(left["timestamp"]) for left, right in zip(candles, candles[1:]) if int(right["timestamp"]) - int(left["timestamp"]) > seconds]
        if not candles or (not watchtower_price_fact_contract and (int(candles[0]["timestamp"]) > start or int(candles[-1]["timestamp"]) + seconds < end or gaps)):
            coverage = {"window_start": start, "window_end": end, "first_bucket": int(candles[0]["timestamp"]) if candles else None, "last_bucket": int(candles[-1]["timestamp"]) if candles else None, "resolution": interval, "candle_count": len(candles), "internal_gap_seconds": max(gaps, default=0)}
            raise ValueError("WINDOW_COVERAGE_INCOMPLETE:" + json.dumps(coverage, sort_keys=True, separators=(",", ":")))
        entry = float(fact["entry_mc_usd"]); latest = float(fact["latest_mc_usd"])
        reduced = reduce_watchtower_price_facts(entry, start, candles, current_close=latest)
        ath, bucket_start = float(reduced["peak"]["value"]), int(reduced["peak"]["timestamp"])
        evidence = "ENTRY_REFERENCE" if reduced["peak"]["evidence"] == "ENTRY_REFERENCE" else ("BIRDEYE_15M_OHLC_HIGH" if watchtower_price_fact_contract and interval=="15m" else f"{interval.upper()}_OHLC_HIGH")
        crossings = reduced["crossings"]; drawdown = float(reduced["drawdown_percent"])
        if drawdown < 85:
            raise ValueError("TERMINAL_DRAWDOWN_INVARIANT_FAILED")
        now = int(self.now())
        provenance = _digest({"requests": [r["request_parameters"] for r in requests], "request_id": request_id, "candles": candles, "before": fact["before_finalization_digest"]})
        # Historical Monitor close-only rows share this compact primary key.  A
        # terminal finalizer response is the authoritative OHLC enrichment for
        # that bucket, so it upgrades rather than silently losing h/l/o fields.
        obs_sql = "INSERT INTO operation_monitor_observations(operation_id,mint,observation_timestamp,mc_usd,resolution,source,request_identity,provenance_digest,created_at,open_mc_usd,high_mc_usd,low_mc_usd,close_mc_usd) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(operation_id,mint,observation_timestamp,resolution) DO UPDATE SET mc_usd=excluded.mc_usd,source=excluded.source,request_identity=excluded.request_identity,provenance_digest=excluded.provenance_digest,created_at=excluded.created_at,open_mc_usd=excluded.open_mc_usd,high_mc_usd=excluded.high_mc_usd,low_mc_usd=excluded.low_mc_usd,close_mc_usd=excluded.close_mc_usd"
        observations = [(obs_sql, ("watchtower", mint, int(row["timestamp"]), float(row["close"]), interval, "BIRDEYE_TERMINAL_ATH_FINALIZATION", request_id, _digest(row), now, float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]))) for row in candles]
        update = ("UPDATE operation_monitor_facts SET retained_monitor_peak_mc_usd=COALESCE(retained_monitor_peak_mc_usd,running_peak_mc_usd),retained_monitor_peak_evidence=COALESCE(retained_monitor_peak_evidence,'CLOSE_ONLY_LOWER_BOUND'),final_proven_ath_mc=?,final_ath_multiple=?,final_ath_evidence=?,final_ath_resolution=?,final_ath_bucket_start=?,final_ath_bucket_end=?,ath_finalized_at=?,ath_finalization_request_id=?,ath_finalization_provenance_digest=?,reached_2x=?,reached_5x=?,reached_10x=?,first_2x_timestamp=?,first_5x_timestamp=?,first_10x_timestamp=?,drawdown_percent=?,updated_at=? WHERE operation_id='watchtower' AND mint=? AND monitor_state=? AND next_observation_at IS NULL")
        params = (ath, ath / entry, evidence, interval, bucket_start, bucket_start + seconds, now, request_id, provenance, int(crossings[2] is not None), int(crossings[5] is not None), int(crossings[10] is not None), crossings[2], crossings[5], crossings[10], drawdown, now, mint, TERMINAL_STATE)
        receipt = commit_write_and_wait(self.db_path, WriteItem("enrichment", "watchtower-terminal-ath-finalizer", observations + [(update, params)], request_id))
        if not receipt.committed:
            raise RuntimeError("ATH_FINALIZATION_WRITE_UNCOMMITTED")
        return {"state": "FINALIZED", "provider_calls": len(requests), "retained_ohlc_reused": bool(retained), "request_id": request_id, "requests": requests, "chunks_planned": len(windows), "candle_count": len(candles), "resolution": interval, "first_bucket": candles[0]["timestamp"], "last_bucket": candles[-1]["timestamp"], "final_proven_ath_mc": ath, "final_ath_multiple": ath / entry, "final_ath_evidence": evidence, "ath_bucket_start": bucket_start, "ath_bucket_end": bucket_start + seconds, "final_drawdown_percent": drawdown, "crossings": crossings, "provenance_digest": provenance, "bytes_added": len(json.dumps(candles, separators=(",", ":")))}
