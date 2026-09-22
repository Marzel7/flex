"""Durable, bounded DB-write contention incidents for retry-managed writes."""
from __future__ import annotations
import hashlib, sqlite3, time

def _schema(c):
 c.execute("CREATE TABLE IF NOT EXISTS db_write_degradation_incidents (incident_id TEXT PRIMARY KEY, db_path TEXT NOT NULL, blocked_writer TEXT NOT NULL, started_at INTEGER NOT NULL, ended_at INTEGER, retry_count INTEGER NOT NULL, root_cause TEXT NOT NULL, evidence_quality TEXT NOT NULL, ingestion_impact TEXT NOT NULL, recovery_state TEXT NOT NULL)")
def record(db_path, writer, *, recovered=False, now=None):
 """Failure-isolated; repeated retries stay one open incident per writer/DB."""
 try:
  now=int(time.time() if now is None else now);c=sqlite3.connect(db_path,timeout=.1);_schema(c)
  row=c.execute("SELECT incident_id,retry_count FROM db_write_degradation_incidents WHERE db_path=? AND blocked_writer=? AND recovery_state='ACTIVE' ORDER BY started_at DESC LIMIT 1",(db_path,writer)).fetchone()
  if row is None:
   key=hashlib.sha256(f'{db_path}|{writer}|{now}'.encode()).hexdigest();c.execute("INSERT INTO db_write_degradation_incidents VALUES(?,?,?,?,?,?,?,?,?,?)",(key,db_path,writer,now,None,1,'CONCURRENT_WRITER_CONTENTION','PARTIAL','IMPACT_UNRESOLVED','ACTIVE'))
  elif recovered: c.execute("UPDATE db_write_degradation_incidents SET ended_at=?,recovery_state='RECOVERED' WHERE incident_id=?",(now,row[0]))
  else: c.execute("UPDATE db_write_degradation_incidents SET retry_count=retry_count+1 WHERE incident_id=?",(row[0],))
  c.commit();c.close()
 except Exception: pass

def rolling_summary(db_path, now=None):
 now=int(time.time() if now is None else now);start=now-86400
 try:
  c=sqlite3.connect(f'file:{db_path}?mode=ro',uri=True); rows=c.execute("SELECT incident_id,started_at,ended_at,retry_count,blocked_writer,root_cause,evidence_quality,ingestion_impact,recovery_state FROM db_write_degradation_incidents WHERE started_at<? ORDER BY started_at",(now,)).fetchall();c.close()
 except Exception: rows=[]
 intervals=[]; incidents=[]
 for r in rows:
  iid,s,e,retries,writer,cause,quality,impact,recovery=r; end=int(e) if e else now
  incidents.append({'incident_id':iid,'start':s,'end':e,'duration':end-int(s),'retry_count':retries,'blocked_writer':writer,'root_cause':cause,'evidence_quality':quality,'ingestion_impact':impact,'recovery_state':recovery})
  if e: intervals.append((max(start,int(s)),min(now,end)))
 intervals.sort(); total=0; last=None
 for a,b in intervals:
  if last is None or a>last: total+=b-a;last=b
  elif b>last: total+=b-last;last=b
 return {'current_state':'DEGRADED' if any(x['recovery_state']=='ACTIVE' for x in incidents) else 'AVAILABLE','window_start':start,'window_end':now,'degraded_seconds':total,'incident_count':len(incidents),'duration_qualified':False,'recent_incidents':incidents[-10:]}
