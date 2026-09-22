"""Durable run-state authority for generic token-data executions."""
import sqlite3, time
from pathlib import Path

RUN_STATE_CONTRACT_VERSION="TOKEN_DATA_RUN_STATE_CONTRACT_V1"
ACTIVE="ACTIVE"; ABORTED="ABORTED"; COMPLETED="COMPLETED"; FAILED="FAILED"
DEFAULT_DB=Path("database/token_data_run_state.db")

def _db(path=DEFAULT_DB):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(path); con.execute("create table if not exists token_data_runs (run_id text primary key,state text not null,created_at integer not null,updated_at integer not null,aborted_at integer,terminal_reason text)"); return con
def create_run(run_id,path=DEFAULT_DB):
    now=int(time.time()); con=_db(path)
    try:
        with con: con.execute("insert or ignore into token_data_runs values (?,?,?,?,?,?)",(run_id,ACTIVE,now,now,None,None))
    finally: con.close()
    return get_run_state(run_id,path)
def get_run_state(run_id,path=DEFAULT_DB):
    con=_db(path)
    try: row=con.execute("select state from token_data_runs where run_id=?",(run_id,)).fetchone(); return row[0] if row else None
    finally: con.close()
def transition(run_id,state,reason=None,path=DEFAULT_DB):
    con=_db(path); now=int(time.time())
    try:
        with con:
            cur=con.execute("update token_data_runs set state=?,updated_at=?,aborted_at=case when ?='ABORTED' then ? else aborted_at end,terminal_reason=? where run_id=? and state=?",(state,now,state,now,reason,run_id,ACTIVE))
        return cur.rowcount==1
    finally: con.close()
def abort_run(run_id,reason,path=DEFAULT_DB): return transition(run_id,ABORTED,reason,path)
def complete_run(run_id,path=DEFAULT_DB): return transition(run_id,COMPLETED,None,path)
def fail_run(run_id,reason,path=DEFAULT_DB): return transition(run_id,FAILED,reason,path)
