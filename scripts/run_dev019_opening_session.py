"""Bounded DEV019 launcher: one 50-token session, single concurrency."""
from __future__ import annotations
import json, os, sqlite3, sys, time
from pathlib import Path
from src.ops.token_data_provider_bindings import BirdeyeProductionBinding
from src.ops.watchtower_historical_executor import HistoricalExecutor

COHORT=Path("/private/tmp/dev014-watchtower-lifecycle-cohort/journals")
CANONICAL=Path("/Users/kevinkeaveney/Dev/claude/flex/database/flex_complete_database.db")
OPS=Path("/Users/kevinkeaveney/Dev/claude/flex/database/wt_ops_v2.db")
ROOT=Path("/private/tmp/dev019-watchtower-opening-mc")
JOURNAL=ROOT/"journal.db"; SNAPSHOT=ROOT/"eligible_snapshot.json"; MAX_TOKENS=50; MIN_REQUEST_SECONDS=6
GTP5="GTP5PX2pUpwdGV17KnugQh8yQENz1FcVzrjcwvYxpump"

def ro(path): return sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True, timeout=2)

def snapshot():
    cohort=sorted({json.loads(p.read_text())["mint"] for p in COHORT.glob("*/journal.json")})
    marks=','.join('?' for _ in cohort); canonical=ro(CANONICAL)
    anchors={r[0]:{"mint":r[0],"migration_timestamp":int(r[1]),"migration_signature":r[2]} for r in canonical.execute(f"SELECT mint,migrated_at,migration_tx FROM token_analysis WHERE mint IN ({marks}) AND migrated_at IS NOT NULL AND migration_tx IS NOT NULL",cohort)}
    observed={r[0] for r in canonical.execute(f"SELECT mint FROM token_analysis WHERE mint IN ({marks}) AND first_observed_mc>0 AND first_observed_at IS NOT NULL",cohort)}; canonical.close()
    ops=ro(OPS)
    trade={r[0] for r in ops.execute(f"SELECT mint FROM operation_monitor_entry_provenance WHERE mint IN ({marks}) AND operation_id='watchtower' AND entry_policy='WATCHTOWER_FIRST_AVAILABLE_MC_V1' AND entry_quality='FIRST_AVAILABLE_SLOT' AND entry_mc_usd>0",cohort)}
    monitor={r[0] for r in ops.execute(f"SELECT DISTINCT mint FROM operation_monitor_observations WHERE mint IN ({marks}) AND operation_id='watchtower' AND mc_usd>0",cohort)}; ops.close()
    eligible=[anchors[m] for m in sorted(set(anchors)-observed-trade-monitor-{GTP5})]
    if len(eligible) != 461: raise RuntimeError(f"ELIGIBLE_SNAPSHOT_COUNT_{len(eligible)}")
    return eligible

class RateLimited:
    def __init__(self, binding): self.binding=binding; self.last=None; self.calls=0
    def __call__(self, request):
        if self.last is not None:
            delay=MIN_REQUEST_SECONDS-(time.monotonic()-self.last)
            if delay>0: time.sleep(delay)
        self.last=time.monotonic(); self.calls+=1
        return self.binding(request)

def main():
    ROOT.mkdir(parents=True,exist_ok=True); rows=snapshot()
    SNAPSHOT.write_text(json.dumps({"schema":"DEV019_ELIGIBLE_SNAPSHOT_V1","count":len(rows),"tokens":rows},sort_keys=True,separators=(',',':')))
    if SNAPSHOT.stat().st_size >= 1_048_576: raise RuntimeError("SNAPSHOT_SIZE_BOUND")
    con=sqlite3.connect(JOURNAL); con.execute("CREATE TABLE IF NOT EXISTS dev019_session_control(id INTEGER PRIMARY KEY CHECK(id=1),next_eligible_at INTEGER NOT NULL)")
    row=con.execute("SELECT next_eligible_at FROM dev019_session_control WHERE id=1").fetchone(); now=int(time.time())
    if row and now < row[0]: raise RuntimeError("SESSION_COOLDOWN_ACTIVE")
    try: done={r[0] for r in con.execute("SELECT mint FROM watchtower_historical_openings WHERE state IN ('QUALIFIED','INSUFFICIENT_EVIDENCE')")}
    except sqlite3.OperationalError: done=set()
    con.close(); selected=[r for r in rows if r["mint"] not in done][:MAX_TOKENS]
    transport=RateLimited(BirdeyeProductionBinding()); executor=HistoricalExecutor(str(JOURNAL),binding=transport)
    counts={"qualified_1s":0,"qualified_1m":0,"insufficient":0,"safety_failure":0}
    for item in selected:
        result=executor.acquire_opening(item); state=result.get("state")
        if state in {"SAFETY_FAILURE","ATTEMPT_OUTCOME_UNPROVEN"}: counts["safety_failure"]+=1; break
        method=result.get("entry_method")
        if method=="FIRST_AVAILABLE_POST_MIGRATION_1M_OPEN_MC": counts["qualified_1m"]+=1
        elif state=="QUALIFIED": counts["qualified_1s"]+=1
        elif state=="INSUFFICIENT_EVIDENCE": counts["insufficient"]+=1
    con=sqlite3.connect(JOURNAL); con.execute("INSERT INTO dev019_session_control(id,next_eligible_at) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET next_eligible_at=excluded.next_eligible_at",(int(time.time())+60,)); con.commit(); con.close()
    print(json.dumps({"eligible_snapshot_count":len(rows),"tokens_selected":len(selected),"tokens_processed":sum(counts.values()),"provider_requests":transport.calls,**counts},sort_keys=True))

if __name__ == "__main__": main()
