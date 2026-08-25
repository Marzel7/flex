"""Manual registry admission, versioned profiles, and activity snapshots."""
from __future__ import annotations
import json, os, sqlite3, statistics, time, uuid

DISPOSITIONS=frozenset({'ACTIVE_MANUAL','REFERENCE_RETIRED','PENDING_MANUAL_REVIEW','REJECTED','HISTORICAL_ONLY'})
COMPONENT_STATES=frozenset({'OBSERVED','RECURRING','BASELINE','VARIANT','EVOLVED','RETIRED'})
ACTIVITY_STATES=frozenset({'VERY_ACTIVE','ACTIVE','SLOWING','DORMANT','REACTIVATED','ACTIVITY_UNKNOWN'})
DDL='''
CREATE TABLE IF NOT EXISTS operation_registry_dispositions (operator_id TEXT PRIMARY KEY REFERENCES operators(operator_id), disposition TEXT NOT NULL, manual_reviewer TEXT NOT NULL, reason TEXT NOT NULL, source_candidate_id TEXT, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS operation_behavioural_profiles (profile_id TEXT PRIMARY KEY, operator_id TEXT, source_candidate_id TEXT NOT NULL, profile_version INTEGER NOT NULL, status TEXT NOT NULL, provenance_json TEXT NOT NULL, member_mints_json TEXT NOT NULL, created_at INTEGER NOT NULL, reviewed_at INTEGER, reviewer TEXT, UNIQUE(operator_id,profile_version), UNIQUE(source_candidate_id,profile_version));
CREATE TABLE IF NOT EXISTS operation_behavioural_components (component_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL REFERENCES operation_behavioural_profiles(profile_id), component_type TEXT NOT NULL, state TEXT NOT NULL, value_json TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS operation_activity_snapshots (snapshot_id TEXT PRIMARY KEY, operator_id TEXT NOT NULL REFERENCES operators(operator_id), observed_at INTEGER NOT NULL, timestamp_semantics TEXT NOT NULL, metrics_json TEXT NOT NULL, activity_state TEXT NOT NULL, UNIQUE(operator_id,observed_at));
'''
def active(conn:sqlite3.Connection):
 cursor=conn.execute("SELECT o.*,d.disposition FROM operators o JOIN operation_registry_dispositions d USING(operator_id) WHERE d.disposition='ACTIVE_MANUAL' AND o.status!='MERGED' ORDER BY o.updated_at DESC")
 return [dict(zip([x[0] for x in cursor.description], row)) for row in cursor.fetchall()]
def metrics(times, now=None):
 now=int(now or time.time()); ts=sorted(int(x) for x in times);
 if not ts:return {'total_observed_launches':0,'activity_state':'ACTIVITY_UNKNOWN'}
 gaps=[b-a for a,b in zip(ts,ts[1:])]; day={x//86400 for x in ts}; recent={f'launches_last_{d}d':sum(x>now-d*86400 for x in ts) for d in (1,3,7,14,30)}
 best=max(sum(t-x<=86400 for t in ts) for x in ts)
 state='DORMANT' if now-ts[-1]>30*86400 else 'VERY_ACTIVE' if recent['launches_last_7d']>=7 else 'ACTIVE' if recent['launches_last_7d']>=2 else 'SLOWING'
 return {'total_observed_launches':len(ts),**recent,'active_days':len(day),'launches_per_active_day':len(ts)/len(day),'average_inter_launch_gap_seconds':statistics.mean(gaps) if gaps else None,'median_inter_launch_gap_seconds':statistics.median(gaps) if gaps else None,'max_launches_rolling_24h':best,'last_observed_launch_timestamp':ts[-1],'time_since_last_observed_seconds':now-ts[-1],'activity_state':state}
def refresh_operator_activity_snapshot(conn, operator_id, core_db_path=None, now=None):
 """Project any active operation's established members into the UI.

 This is a projection only: it never admits a mint or changes attribution.
 WATCHTOWER uses its canonical ledger. Other operations use their retained
 behavioural-profile members plus explicit operator membership rows, with
 launch time read from the core token ledger.
 """
 now=int(now or time.time())
 row=conn.execute("SELECT display_name FROM operators WHERE operator_id=?",(operator_id,)).fetchone()
 if not row: raise ValueError('operator not found')
 if row[0]=='WATCHTOWER':
  ledger_rows=conn.execute("SELECT mint,MAX(create_time) FROM wt_watchtower_launches WHERE mint IS NOT NULL AND COALESCE(state,'FIRED_CREATE')!='PENDING_REVIEW' GROUP BY mint").fetchall()
  ledger_times={item[0]:item[1] for item in ledger_rows}
  member_mints=set(ledger_times)
  if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='operator_launch_membership'").fetchone():
   member_mints.update(item[0] for item in conn.execute("SELECT mint FROM operator_launch_membership WHERE operator_id=?",(operator_id,)))
  times=[value for value in ledger_times.values() if value is not None]
  path=core_db_path or os.environ.get('DB_PATH',os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),'database','flex_complete_database.db')))
  missing_timestamps=member_mints-set(ledger_times)
  if missing_timestamps and os.path.exists(path):
   core=sqlite3.connect(path)
   try:
    marks=','.join('?' for _ in missing_timestamps)
    for created_at, in core.execute(f"SELECT created_at FROM token_analysis WHERE mint IN ({marks})",tuple(missing_timestamps)):
     value=core.execute("SELECT strftime('%s',?)",(created_at,)).fetchone()[0]
     if value is not None: times.append(int(value))
   finally: core.close()
  rows=[(mint,None) for mint in sorted(member_mints)]
  source={'contract':'wt_watchtower_launches canonical ledger plus strict confirmed operator membership','cadence_population':f'{len(times)} distinct mints with available launch timestamps','total_population':f'{len(rows)} distinct mints'}
 else:
  profile=conn.execute("SELECT member_mints_json FROM operation_behavioural_profiles WHERE operator_id=? ORDER BY profile_version DESC LIMIT 1",(operator_id,)).fetchone()
  mints=set(json.loads(profile[0])) if profile else set()
  if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='operator_launch_membership'").fetchone():
   mints.update(item[0] for item in conn.execute("SELECT mint FROM operator_launch_membership WHERE operator_id=?",(operator_id,)))
  rows=[(mint,None) for mint in sorted(mints)]
  times=[]
  path=core_db_path or os.environ.get('DB_PATH',os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),'database','flex_complete_database.db'))
  if mints and os.path.exists(path):
   core=sqlite3.connect(path)
   try:
    marks=','.join('?' for _ in mints)
    for created_at, in core.execute(f"SELECT created_at FROM token_analysis WHERE mint IN ({marks})",tuple(mints)):
     value=core.execute("SELECT strftime('%s',?)",(created_at,)).fetchone()[0]
     if value is not None: times.append(int(value))
   finally: core.close()
  source={'contract':'retained behavioural profile members plus operator_launch_membership','total_population':f'{len(rows)} distinct member mints'}
 data=metrics(times,now)
 data.update({'total_observed_launches':len(rows),'timestamp_qualified_launches':len(times),'timestamp_missing_launches':len(rows)-len(times),'source_provenance':source})
 semantics=f'Total: distinct established operation member mints. Cadence/last launch: available launch timestamps on {len(times)} members.'
 conn.execute("INSERT INTO operation_activity_snapshots (snapshot_id,operator_id,observed_at,timestamp_semantics,metrics_json,activity_state) VALUES (?,?,?,?,?,?) ON CONFLICT(operator_id,observed_at) DO UPDATE SET timestamp_semantics=excluded.timestamp_semantics,metrics_json=excluded.metrics_json,activity_state=excluded.activity_state",(str(uuid.uuid4()),operator_id,now,semantics,json.dumps(data,sort_keys=True),data['activity_state']))
 conn.commit()
 return data
def refresh_watchtower_activity_snapshot(conn, operator_id, now=None):
 """Compatibility wrapper for existing WATCHTOWER promotion callers."""
 return refresh_operator_activity_snapshot(conn,operator_id,now=now)
def apply_dispositions(conn, reviewer='migration'):
 rows={r[1]:r[0] for r in conn.execute("SELECT operator_id,display_name FROM operators WHERE display_name IN ('WATCHTOWER','3SW2')")}
 if set(rows)!={'WATCHTOWER','3SW2'}: raise ValueError('registry precondition failed: WATCHTOWER/3SW2 missing')
 now=int(time.time())
 for name,state,reason in [('WATCHTOWER','ACTIVE_MANUAL','manual confirmed registry admission'),('3SW2','REFERENCE_RETIRED','historical reference retained outside active registry')]:
  conn.execute("INSERT INTO operation_registry_dispositions VALUES(?,?,?,?,?,?) ON CONFLICT(operator_id) DO UPDATE SET disposition=excluded.disposition,manual_reviewer=excluded.manual_reviewer,reason=excluded.reason,updated_at=excluded.updated_at",(rows[name],state,reviewer,reason,None,now))
