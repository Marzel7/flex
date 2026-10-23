from pathlib import Path
exec(compile(Path('/private/tmp/dev004-schema-bootstrap-child.py').read_text().split('results=[]')[0],'isolation','exec'))
from src.core import database_write_service as owner
checks={};details={};lease=None
# Permit only the named, bounded diagnostic writer thread; no runtime worker starts.
def allow_test_thread(self,*a,**kw):
 if self.name in {'epoch-core-writer','epoch-ops-writer'}:return original_start(self,*a,**kw)
 return start_thread(self,*a,**kw)
threading.Thread.start=allow_test_thread

def check(k,v):checks[k]=bool(v);assert v,k

def other_acquire(kind,timeout=.05):
 result={}
 def work():
  acquired=None;t0=time.monotonic()
  try:
   acquired=owner.acquire_write_lease(kind,str(out/(kind+'.sqlite')),'synthetic-other-'+kind,'epoch-feasibility',timeout=timeout)
   result['acquired']=True
  except BaseException as e:result.update(acquired=False,error=type(e).__name__)
  finally:
   if acquired:owner.release_write_lease(acquired)
   result['elapsed_ms']=round((time.monotonic()-t0)*1000,3)
 t=threading.Thread(target=work,name='epoch-'+kind+'-writer');t.start();t.join(2);assert not t.is_alive();return result
try:
 # Accepted schema definitions from the preceding disposable diagnostic; no production reads.
 for kind,table in [('core','token_analysis'),('ops','wt_treasury_review')]:
  c=sqlite3.connect(str(out/(kind+'.sqlite')));c.execute('pragma journal_mode=WAL')
  definition=Path('/private/tmp/dev004-epoch-'+kind+'-schema.sql').read_text();c.execute(definition)
  if kind=='core':c.execute("insert into token_analysis(mint,pf_ws_creator) values('synthetic-mint','creator-before')")
  else:c.execute("insert into wt_treasury_review(treasury,status) values('synthetic-root','PENDING_REVIEW')")
  c.commit();c.close()
 start=time.monotonic();lease=owner.acquire_write_lease('core',str(out/'core.sqlite'),'synthetic-core-owner','epoch-feasibility',timeout=.005)
 check('core_existing_lease_acquired',lease.owner['database']=='core')
 try:
  nested=owner.acquire_write_lease('ops',str(out/'ops.sqlite'),'synthetic-nested-ops','epoch-feasibility',timeout=.005)
  owner.release_write_lease(nested);raise AssertionError('nested acquisition accepted')
 except owner.NestedDatabaseWriteError:check('supported_cross_database_nesting_rejected',True)
 separate=other_acquire('ops');details['ops_while_core_held']=separate;check('core_lease_does_not_fence_ops_owner',separate.get('acquired'))
 contended=other_acquire('core');details['core_while_core_held']=contended;check('same_database_other_owner_times_out',contended.get('error')=='CrossProcessDatabaseWriteTimeout')
 # A surviving holder is not revoked by the wait timeout. No production stalls.
 details['observed_core_hold_ms']=round((time.monotonic()-start)*1000,3)
 check('acquisition_timeout_not_holder_deadline',details['observed_core_hold_ms']>5 and not lease.file.closed)
 # A native SQLite connection ignores advisory flock unless it participates.
 c=raw(str(out/'core.sqlite'));c.execute("update token_analysis set pf_ws_creator='native-during-advisory-lease' where mint='synthetic-mint'");c.commit();c.close()
 check('advisory_lease_is_not_sqlite_engine_fence',True)
 owner.release_write_lease(lease);lease=None
 check('release_allows_next_core_owner',other_acquire('core').get('acquired'))
 # Reacquisition has process-local token/ownership, not a durable paired epoch.
 first=owner.acquire_write_lease('core',str(out/'core.sqlite'),'synthetic-generation-a','epoch-feasibility',timeout=.01);token1=first.token;owner.release_write_lease(first)
 second=owner.acquire_write_lease('core',str(out/'core.sqlite'),'synthetic-generation-b','epoch-feasibility',timeout=.01)
 check('reacquisition_token_is_new_process_local_authority',second.token is not token1);owner.release_write_lease(second)
 details['completion_authority']='Owner diagnostic metadata is explicitly not lock or durable epoch authority; no paired commit/completion journal exists in this tested owner contract.'
 status='HOLD_EXISTING_OWNER_HAS_NO_PAIRED_DURABLE_PUBLICATION_EPOCH'
except BaseException as e:status='HOLD_DIAGNOSTIC_ERROR';details.update(error=str(e),traceback=traceback.format_exc(limit=7))
finally:
 if lease:owner.release_write_lease(lease)
result={'status':status,'checks':checks,'details':details,'blocked':blocked,'database_attempts':db_attempts,'sizes':size()}
(out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'status':status,'checks':checks,'details':details,'blocked':blocked,'sizes':size()},indent=2))
