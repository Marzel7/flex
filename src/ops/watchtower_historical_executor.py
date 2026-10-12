"""Single-job bounded executor for the qualified Watchtower backfill FSM."""
from __future__ import annotations
import sqlite3
from typing import Any, Callable
from src.ops.historical_15m_reconstruction import BUCKET_SECONDS
from src.ops.token_data_provider_bindings import BirdeyeProductionBinding, build_birdeye_ohlcv_request, validate_birdeye_credential_label
from src.ops.watchtower_historical_backfill import begin_next_range, complete_after_terminal_history, complete_range, fail_range, job_state, ranges
from src.ops.watchtower_terminal_ath_finalizer import _candles
from src.ops.strict_migration_window import normalize_opening_ohlcv, reduce_retained_offsets

MAX_HISTORICAL_RANGES=6
MAX_ATTEMPTS_PER_RANGE=2
MAX_TOTAL_PROVIDER_CALLS_PER_BACKFILL_TOKEN=13
HISTORICAL_BACKFILL_CONCURRENCY=1

class HistoricalExecutor:
    """No discovery, no pagination, no retries outside durable range ownership."""
    def __init__(self, db_path: str, binding: Callable[[dict[str,Any]],Any] | None=None, *, credential_label="BIRDEYE"):
        validate_birdeye_credential_label(credential_label)
        self.db_path=db_path; self.binding=binding or BirdeyeProductionBinding(credential_label=credential_label)
        self.calls=0
    def acquire_opening(self, boundary: dict[str, Any]) -> dict[str, Any]:
        """Durable strict-1s then 1m-open acquisition; never repeats an owned stage."""
        import json
        mint, timestamp = str(boundary["mint"]), int(boundary["migration_timestamp"])
        migration_signature = str(boundary.get("migration_signature") or "")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS watchtower_historical_openings(mint TEXT PRIMARY KEY,state TEXT NOT NULL,result_json TEXT,created_at INTEGER NOT NULL)")
            columns={row[1] for row in conn.execute("PRAGMA table_info(watchtower_historical_openings)")}
            for name in ("strict_state", "fallback_state", "updated_at"):
                if name not in columns: conn.execute(f"ALTER TABLE watchtower_historical_openings ADD COLUMN {name} TEXT")
            prior=conn.execute("SELECT state,result_json,strict_state,fallback_state FROM watchtower_historical_openings WHERE mint=?",(mint,)).fetchone()
            if prior and prior[0] in {"QUALIFIED", "INSUFFICIENT_EVIDENCE"}:
                return json.loads(prior[1])
            if prior and (prior[2] == "IN_FLIGHT" or prior[3] == "IN_FLIGHT"):
                return {"state":"ATTEMPT_OUTCOME_UNPROVEN", "mint":mint}
            if not prior:
                conn.execute("INSERT INTO watchtower_historical_openings(mint,state,result_json,created_at,strict_state,fallback_state,updated_at) VALUES(?,?,?,0,?,?,0)", (mint,"PENDING",None,"PENDING","PENDING"))

        def persist(state: str, *, strict: str | None=None, fallback: str | None=None, result: dict[str, Any] | None=None):
            with sqlite3.connect(self.db_path) as conn:
                values=[state, json.dumps(result,sort_keys=True) if result else None]
                parts=["state=?", "result_json=?", "updated_at=0"]
                if strict is not None: parts.append("strict_state=?"); values.append(strict)
                if fallback is not None: parts.append("fallback_state=?"); values.append(fallback)
                values.append(mint)
                conn.execute(f"UPDATE watchtower_historical_openings SET {','.join(parts)} WHERE mint=?", values)

        with sqlite3.connect(self.db_path) as conn:
            strict_state=conn.execute("SELECT strict_state FROM watchtower_historical_openings WHERE mint=?",(mint,)).fetchone()[0]
        if strict_state == "PENDING":
            persist("STRICT_IN_FLIGHT", strict="IN_FLIGHT")
            outcome=self.binding(build_birdeye_ohlcv_request(address=mint,interval="1s",time_from=timestamp,time_to=timestamp+2)); self.calls += 1
            if outcome.status_code != 200:
                return {"state":"SAFETY_FAILURE", "reason":f"STRICT_HTTP_{outcome.status_code}", "mint":mint}
            candles=normalize_opening_ohlcv(outcome.payload or {})
            offsets={}; duplicates=[]
            for candle in candles:
                offset=int(candle["timestamp"])-timestamp
                if offset in offsets: duplicates.append(offset)
                else: offsets[offset]=candle["close"]
            selected=reduce_retained_offsets(migration_timestamp=timestamp, offsets=offsets, duplicate_offsets=duplicates)
            if selected.get("state") == "QUALIFIED":
                result={**selected,"mint":mint,"migration_timestamp":timestamp,"migration_signature":migration_signature,"interval":"1s","market_cap_field":"close","seconds_after_migration":int(selected["entry_timestamp"])-timestamp,"provider":"BIRDEYE"}
                persist("QUALIFIED", strict="QUALIFIED", result=result); return result
            persist("FALLBACK_PENDING", strict="INSUFFICIENT_EVIDENCE")

        with sqlite3.connect(self.db_path) as conn:
            fallback_state=conn.execute("SELECT fallback_state FROM watchtower_historical_openings WHERE mint=?",(mint,)).fetchone()[0]
        if fallback_state == "PENDING":
            persist("FALLBACK_IN_FLIGHT", fallback="IN_FLIGHT")
            outcome=self.binding(build_birdeye_ohlcv_request(address=mint,interval="1m",time_from=timestamp,time_to=timestamp+300)); self.calls += 1
            if outcome.status_code != 200:
                return {"state":"SAFETY_FAILURE", "reason":f"FALLBACK_HTTP_{outcome.status_code}", "mint":mint}
            candles=normalize_opening_ohlcv(outcome.payload or {})
            valid=[row for row in candles if int(row["timestamp"]) >= timestamp and float(row["open"]) > 0]
            if valid:
                row=min(valid,key=lambda item:int(item["timestamp"]))
                result={"state":"QUALIFIED","mint":mint,"migration_timestamp":timestamp,"migration_signature":migration_signature,"entry_timestamp":int(row["timestamp"]),"entry_mc_usd":float(row["open"]),"entry_method":"FIRST_AVAILABLE_POST_MIGRATION_1M_OPEN_MC","entry_exactness":"OBSERVED_1M_CANDLE_OPEN","interval":"1m","market_cap_field":"open","seconds_after_migration":int(row["timestamp"])-timestamp,"provider":"BIRDEYE"}
                persist("QUALIFIED", fallback="QUALIFIED", result=result); return result
            result={"state":"INSUFFICIENT_EVIDENCE","mint":mint,"migration_timestamp":timestamp,"migration_signature":migration_signature,"reason":"NO_QUALIFYING_1M_OPEN_CANDLE"}
            persist("INSUFFICIENT_EVIDENCE", fallback="INSUFFICIENT_EVIDENCE", result=result); return result
        return {"state":"ATTEMPT_OUTCOME_UNPROVEN", "mint":mint}
    def run(self, job_id: str, mint: str, *, terminal_result: dict[str, Any] | None=None) -> dict[str,Any]:
        if job_state(self.db_path,job_id)!="ACQUIRING": return {"state":job_state(self.db_path,job_id),"provider_calls":0}
        before={r["range_id"] for r in ranges(self.db_path,job_id) if r["state"]=="COMPLETED"}
        while True:
            row=begin_next_range(self.db_path,job_id,now=0)
            if row is None: break
            if self.calls >= MAX_HISTORICAL_RANGES*MAX_ATTEMPTS_PER_RANGE: raise RuntimeError("HISTORICAL_CALL_BOUND_EXCEEDED")
            request=build_birdeye_ohlcv_request(address=mint,interval="15m",time_from=int(row["range_start"]),time_to=int(row["range_end"])-BUCKET_SECONDS)
            self.calls += 1; outcome=self.binding(request)
            if outcome.status_code != 200:
                fail_range(self.db_path,row["range_id"],now=0); continue
            try: candles=[c for c in _candles(outcome.payload or {}) if int(row["range_start"])<=int(c["timestamp"])<int(row["range_end"])]
            except ValueError: candles=[]
            if complete_range(self.db_path,row["range_id"],{"request":request["request_parameters"],"candles":candles},now=0) != "COMPLETED":
                return {"state":"REFUSED_STORAGE_BOUND","provider_calls":self.calls,"completed_range_count":sum(item["state"]=="COMPLETED" for item in ranges(self.db_path,job_id)),"failed_closed":1,"replayed_completed_ranges":len(before)}
        final=ranges(self.db_path,job_id)
        if terminal_result is not None and len(final)==MAX_HISTORICAL_RANGES and all(r["state"]=="COMPLETED" for r in final):
            candles=[]
            for item in final:
                import json
                candles.extend(json.loads(item["checkpoint_json"])["candles"])
            complete_after_terminal_history(self.db_path,job_id,{**terminal_result,"mint":mint,"candles":candles},now=0)
        return {"state":job_state(self.db_path,job_id),"provider_calls":self.calls,"completed_range_count":sum(r["state"]=="COMPLETED" for r in final),"failed_closed":sum(r["state"]=="FAILED_CLOSED" for r in final),"replayed_completed_ranges":len(before)}
