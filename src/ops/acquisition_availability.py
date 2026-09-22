"""Failure-isolated, low-volume acquisition availability transitions."""
from __future__ import annotations
import hashlib, json, os, sqlite3, time
from pathlib import Path

STATES={"UNKNOWN","AVAILABLE","DOWN","DEGRADED"}
SOURCES={"BIRTH":"PUMPPORTAL_NEW_TOKEN_WS","MIGRATION":"PUMPSWAP_MIGRATION_LOGS_WS"}

class AcquisitionAvailability:
    def __init__(self, db_path: str, runtime_id: str): self.db_path,self.runtime_id=db_path,runtime_id
    def _connect(self):
        c=sqlite3.connect(self.db_path,timeout=.25); c.execute("PRAGMA busy_timeout=250")
        c.execute("CREATE TABLE IF NOT EXISTS acquisition_availability_transitions (transition_id TEXT PRIMARY KEY, capability TEXT NOT NULL, source TEXT NOT NULL, state TEXT NOT NULL, previous_state TEXT NOT NULL, effective_at INTEGER NOT NULL, reason TEXT NOT NULL, runtime_id TEXT NOT NULL, connection_id TEXT, provenance TEXT NOT NULL, schema_version TEXT NOT NULL)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_acquisition_availability_cap_time ON acquisition_availability_transitions(capability,effective_at)")
        return c
    def transition(self, capability: str, state: str, reason: str, connection_id: str|None=None, now: int|None=None) -> bool:
        """Never raise: instrumentation cannot affect initial acquisition."""
        try:
            if capability not in SOURCES or state not in STATES: return False
            now=int(time.time() if now is None else now); c=self._connect()
            row=c.execute("SELECT state FROM acquisition_availability_transitions WHERE capability=? ORDER BY effective_at DESC,rowid DESC LIMIT 1",(capability,)).fetchone()
            previous=row[0] if row else "UNKNOWN"
            if row is not None and previous==state: c.close(); return True
            key=hashlib.sha256(f"{capability}|{state}|{previous}|{now}|{self.runtime_id}|{reason}".encode()).hexdigest()
            c.execute("INSERT OR IGNORE INTO acquisition_availability_transitions VALUES(?,?,?,?,?,?,?,?,?,?,?)",(key,capability,SOURCES[capability],state,previous,now,reason,self.runtime_id,connection_id,json.dumps({"writer":"listener_control_flow"},sort_keys=True),"v1"));c.commit();c.close();return True
        except Exception: return False

def rolling_summary(db_path: str, capability: str, now: int|None=None) -> dict:
    """Deterministically clip durable state intervals; time before first record is UNKNOWN."""
    now=int(time.time() if now is None else now); start=now-86400; totals={x:0 for x in STATES}; incidents=[]
    try:
        c=sqlite3.connect(f"file:{db_path}?mode=ro",uri=True); rows=c.execute("SELECT state,effective_at,reason,source FROM acquisition_availability_transitions WHERE capability=? AND effective_at<? ORDER BY effective_at,rowid",(capability,now)).fetchall();c.close()
    except Exception: rows=[]
    state="UNKNOWN"; cursor=start; source=SOURCES.get(capability); open_incident=None
    for next_state,at,reason,src in rows:
        source=src; at=int(at)
        if at<=start: state=next_state; continue
        totals[state]+=at-cursor; cursor=at
        if state=="AVAILABLE" and next_state in {"DOWN","DEGRADED"}: open_incident={"start":at,"reason":reason,"source":src}
        if next_state=="AVAILABLE" and open_incident: open_incident.update({"end":at,"duration":at-open_incident["start"]});incidents.append(open_incident);open_incident=None
        state=next_state
    totals[state]+=now-cursor
    if open_incident: open_incident.update({"end":None,"duration":now-open_incident["start"]});incidents.append(open_incident)
    observed=totals["AVAILABLE"]+totals["DOWN"]+totals["DEGRADED"]
    return {"capability":capability,"source":source,"current_state":state,"window_start":start,"window_end":now,"observation_seconds":observed,"available_seconds":totals["AVAILABLE"],"down_seconds":totals["DOWN"],"degraded_seconds":totals["DEGRADED"],"unknown_seconds":totals["UNKNOWN"],"observed_availability_percent":(100*totals["AVAILABLE"]/observed if observed else None),"full_24h_qualified":totals["UNKNOWN"]==0,"incident_count":len(incidents),"recent_incidents":incidents[-10:]}
