"""Single-job bounded executor for the qualified Watchtower backfill FSM."""
from __future__ import annotations
import sqlite3
from typing import Any, Callable
from src.ops.historical_15m_reconstruction import BUCKET_SECONDS
from src.ops.token_data_provider_bindings import BirdeyeProductionBinding, build_birdeye_ohlcv_request, validate_birdeye_credential_label
from src.ops.watchtower_historical_backfill import begin_next_range, complete_after_terminal_history, complete_range, fail_range, job_state, ranges
from src.ops.watchtower_terminal_ath_finalizer import _candles
from src.ops.strict_migration_window import reduce_policy

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
        """One durable 1s Opening attempt; never retries or widens the window."""
        mint,timestamp=str(boundary["mint"]),int(boundary["migration_timestamp"])
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS watchtower_historical_openings(mint TEXT PRIMARY KEY,state TEXT NOT NULL,result_json TEXT,created_at INTEGER NOT NULL)")
            prior=conn.execute("SELECT state,result_json FROM watchtower_historical_openings WHERE mint=?",(mint,)).fetchone()
            if prior and prior[0]=="QUALIFIED":
                import json; return json.loads(prior[1])
            if prior: raise RuntimeError("OPENING_ATTEMPT_ALREADY_OWNED")
            conn.execute("INSERT INTO watchtower_historical_openings VALUES(?,?,?,?)",(mint,"IN_FLIGHT",None,0))
        request=build_birdeye_ohlcv_request(address=mint,interval="1s",time_from=timestamp,time_to=timestamp+2)
        outcome=self.binding(request)
        if outcome.status_code!=200: raise RuntimeError(f"OPENING_HTTP_{outcome.status_code}")
        candles=_candles(outcome.payload or {}); by={int(c["timestamp"]):c for c in candles}
        entry=by.get(timestamp+1) or by.get(timestamp)
        selected=reduce_policy(migration_timestamp=timestamp,entry={"timestamp":int(entry["timestamp"]),"mc":float(entry["close"]),"plus_one_absent":timestamp+1 not in by}) if entry else {"state":"INSUFFICIENT_EVIDENCE"}
        if selected.get("state")!="QUALIFIED": raise RuntimeError("OPENING_INSUFFICIENT_EVIDENCE")
        import json
        compact={k:selected[k] for k in ("entry_timestamp","entry_mc_usd","entry_method","entry_exactness")}
        with sqlite3.connect(self.db_path) as conn: conn.execute("UPDATE watchtower_historical_openings SET state='QUALIFIED',result_json=? WHERE mint=?",(json.dumps(compact,sort_keys=True),mint))
        return compact
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
            complete_range(self.db_path,row["range_id"],{"request":request["request_parameters"],"candles":candles},now=0)
        final=ranges(self.db_path,job_id)
        if terminal_result is not None and len(final)==MAX_HISTORICAL_RANGES and all(r["state"]=="COMPLETED" for r in final):
            candles=[]
            for item in final:
                import json
                candles.extend(json.loads(item["checkpoint_json"])["candles"])
            complete_after_terminal_history(self.db_path,job_id,{**terminal_result,"mint":mint,"candles":candles},now=0)
        return {"state":job_state(self.db_path,job_id),"provider_calls":self.calls,"completed_range_count":sum(r["state"]=="COMPLETED" for r in final),"failed_closed":sum(r["state"]=="FAILED_CLOSED" for r in final),"replayed_completed_ranges":len(before)}
