from pathlib import Path
s=Path('/private/tmp/dev004-schema-bootstrap-child.py').read_text();exec(compile(s.split('results=[]')[0],'isolated-bootstrap-setup','exec'))
mode=os.environ['SCHEMA_CHECK_MODE'];result={'mode':mode,'blocked':blocked}
try:
 if mode=='compact':
  from src.ops import attribution_outcome
  core=sqlite3.connect(str(out/'core.sqlite'));core.row_factory=sqlite3.Row
  ops=sqlite3.connect(str(out/'ops.sqlite'));ops.row_factory=sqlite3.Row
  history=json.load(open('/private/tmp/dev004-compact-creator-qualified/history.json'));creator=next(iter(history['creators']));cols={r[1] for r in core.execute('pragma table_info(token_analysis)')}
  result['retained_history_timestamp_columns']=history['timestamp_columns'];result['missing_core_timestamp_columns']=[k for k in history['timestamp_columns'] if k not in cols]
  try:
   attribution_outcome.evaluate_launcher_profile(ops,core,creator,now=1792747000);result['status']='PASS'
  except BaseException as e:result.update(status='HOLD_READER_CONTRACT',error=str(e))
  result['core_required_tables']={name:bool(core.execute('select 1 from sqlite_master where name=?',(name,)).fetchone()) for name in ['token_market_cap_peaks','wt_sub_provisioners','watchtower_infra_events','creator_resolution_queue','creator_funding_queue','migration_walkback_handoff','birth_persist_queue']}
  core.close();ops.close()
 elif mode=='resolver':
  from src.core import creator_resolution_worker as worker
  worker._STOP=True;worker.run_loop(once=True);result['status']='PASS_STARTUP_STOP_BEFORE_FIRST_CYCLE'
 elif mode=='walkback':
  from src.core import walkback_worker as worker
  from src.ops import walkback_cycle_trace
  class Ready(BaseException):pass
  real=walkback_cycle_trace.trace_boundary
  def boundary(name,*a,**kw):
   if name=='cycle_started':raise Ready()
   return real(name,*a,**kw)
  walkback_cycle_trace.trace_boundary=boundary
  try:worker.run_loop()
  except Ready:result['status']='PASS_STARTUP_STOP_BEFORE_FIRST_CYCLE'
 else:raise ValueError(mode)
except BaseException as e:result.update(status='HOLD',error=str(e)[:500],traceback=traceback.format_exc(limit=6))
result['database_attempts']=db_attempts
(out/(mode+'-startup-check.json')).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k not in ['database_attempts','traceback']}))
