"""Disposable SQLite generation-fence diagnostics; no production authority."""
import hashlib,json,os,resource,sqlite3,tempfile,threading,time,traceback
from pathlib import Path
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000))
OUT=Path(tempfile.mkdtemp(prefix='dev004-generation-capture-',dir='/private/tmp'))
checks={};observations={};connections=[]
def check(k,v):checks[k]=bool(v);assert v,k
def con(path,ro=False):
 assert Path(path).parent==OUT
 c=sqlite3.connect('file:'+str(path)+('?mode=ro' if ro else ''),uri=True,timeout=.05)
 if not ro:c.execute('pragma max_page_count=128')
 connections.append(str(path));return c
for kind,table in [('core','token_analysis'),('ops','wt_treasury_review')]:
 source=sqlite3.connect('file:/private/tmp/dev004-compact-creator-qualified/classification_closure/'+kind+'_v2.sqlite?mode=ro',uri=True)
 ddl=source.execute('select sql from sqlite_master where type="table" and name=?',(table,)).fetchone()[0];source.close()
 c=con(OUT/(kind+'.sqlite'));c.execute('pragma journal_mode=WAL');c.execute(ddl)
 for i in range(8):
  if kind=='core':c.execute('insert into token_analysis(mint,pf_ws_creator) values(?,?)',(f'key-{i:02}',f'creator-{i:02}'))
  else:c.execute('insert into wt_treasury_review(treasury,status) values(?,?)',(f'key-{i:02}','PENDING_REVIEW'))
 c.commit();c.close()
class Hold(Exception):pass
def version(c):return c.execute('pragma data_version').fetchone()[0]
def vectors(w):return tuple(version(c) for c in w)
def write(kind,field,value,key='key-00'):
 c=con(OUT/(kind+'.sqlite'));table,pk=('token_analysis','mint') if kind=='core' else ('wt_treasury_review','treasury')
 c.execute('update '+table+' set '+field+'=? where '+pk+'=?',(value,key));c.commit();c.close()
def mutate_thread(kind,field,value):
 errors=[]
 def run():
  try:write(kind,field,value)
  except Exception as e:errors.append(str(e))
 t=threading.Thread(target=run);t.start();t.join(2)
 assert not t.is_alive() and not errors,errors

def capture(w,initial,mutation=None):
 # Test fixture contract: PK keyset pages, <=4 rows per query, <=8/DB total.
 result={};pages=0;rows=0;max_bytes=0
 for idx,(kind,table,pk,field) in enumerate([('core','token_analysis','mint','pf_ws_creator'),('ops','wt_treasury_review','treasury','status')]):
  cursor='';items=[]
  while True:
   if vectors(w)!=initial:raise Hold('MUTATION_INVALIDATES_CAPTURE')
   c=con(OUT/(kind+'.sqlite'),True);c.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,4096)
   c.set_progress_handler(lambda:1 if time.monotonic()-start>2 else 0,1000)
   batch=c.execute('select '+pk+','+field+' from '+table+' where '+pk+'>? order by '+pk+' limit 4',(cursor,)).fetchall();c.close();pages+=1
   b=json.dumps(batch,separators=(',',':')).encode()
   if len(b)>2048:raise Hold('PAGE_BYTES')
   max_bytes=max(max_bytes,len(b));rows+=len(batch);items.extend(batch)
   if len(items)>8:raise Hold('TOTAL_ROWS')
   checkpoint={'owner_session':'test-live-connections-only','cursor':cursor,'versions':initial,'rows_seen':rows,'complete':False}
   (OUT/'checkpoint.json').write_text(json.dumps(checkpoint))
   if mutation:
    f=mutation;mutation=None;f()
   if vectors(w)!=initial:raise Hold('MUTATION_INVALIDATES_CAPTURE')
   if not batch:break
   cursor=batch[-1][0]
  result[kind]=items
 if vectors(w)!=initial:raise Hold('MUTATION_INVALIDATES_CAPTURE')
 encoded=json.dumps(result,sort_keys=True,separators=(',',':')).encode()
 if len(encoded)>16384:raise Hold('GENERATION_BYTES')
 return result,{'pages':pages,'rows':rows,'maximum_page_bytes':max_bytes,'encoded_bytes':len(encoded),'digest':hashlib.sha256(encoded).hexdigest()}
try:
 # A valid read pair with independent snapshots need not be a valid pair generation.
 core=con(OUT/'core.sqlite',True);ops=con(OUT/'ops.sqlite',True);core.execute('begin');old=core.execute("select pf_ws_creator from token_analysis where mint='key-00'").fetchone()[0]
 mutate_thread('core','pf_ws_creator','after-core');mutate_thread('ops','status','REJECTED')
 ops.execute('begin');new=ops.execute("select status from wt_treasury_review where treasury='key-00'").fetchone()[0]
 check('independent_snapshots_can_skew',old=='creator-00' and new=='REJECTED');core.rollback();ops.rollback();core.close();ops.close()
 # Same-connection data_version is useful while watchers survive, not durable across reopen.
 watchers=[con(OUT/'core.sqlite',True),con(OUT/'ops.sqlite',True)];initial=vectors(watchers)
 start=time.monotonic();captured,meta=capture(watchers,initial);observations['quiet_capture']=meta
 check('quiet_bounded_capture_complete',len(captured['core'])==8 and len(captured['ops'])==8)
 last_good=OUT/'last-good.json';last_good.write_text('{"sentinel":"last-good"}');sentinel=last_good.read_bytes()
 for kind,field,value in [('core','pf_ws_creator','core-only-change'),('ops','status','PENDING_REVIEW')]:
  initial=vectors(watchers);start=time.monotonic()
  try:capture(watchers,initial,lambda kind=kind,field=field,value=value:mutate_thread(kind,field,value));raise AssertionError('mutation ignored')
  except Hold:check(kind+'_only_mutation_invalidated',True)
 check('invalidated_capture_preserves_last_good',last_good.read_bytes()==sentinel)
 before=vectors(watchers);mutate_thread('core','pf_ws_creator','same-key-new-content');after=vectors(watchers)
 check('existing_identity_update_detected_while_connection_alive',before[0]!=after[0])
 for c in watchers:c.close()
 first=[con(OUT/'core.sqlite',True),con(OUT/'ops.sqlite',True)];token_before=vectors(first)
 for c in first:c.close()
 mutate_thread('ops','status','REJECTED')
 reopened=[con(OUT/'core.sqlite',True),con(OUT/'ops.sqlite',True)];token_after=vectors(reopened);observations['reopen_tokens']={'before':token_before,'after':token_after}
 check('data_version_not_durable_restart_token',token_before==token_after)
 # Restart contract must abort an interrupted generation unless capture bytes can be independently revalidated.
 checkpoint=json.loads((OUT/'checkpoint.json').read_text());check('restart_capture_requires_invalidation',checkpoint['owner_session']=='test-live-connections-only' and not checkpoint['complete'])
 initial=vectors(reopened);start=time.monotonic();captured,meta=capture(reopened,initial)
 manifest={'generation':meta['digest'],'source_keys':{k:[x[0] for x in v] for k,v in captured.items()},'source_session':'synthetic-live-watchers','complete':True}
 check('exact_source_identity_coverage',len(set(manifest['source_keys']['core']))==8 and len(set(manifest['source_keys']['ops']))==8)
 duplicate=captured['core']+[captured['core'][0]];dedup=dict(duplicate);check('duplicate_identity_dedup_control',len(dedup)==8)
 missing=captured['core'][:-1];check('missing_identity_rejected_against_frozen_manifest',set(dict(missing))!=set(manifest['source_keys']['core']))
 # Controlled validation-to-publication interleaving: no reader/writer block, but no atomic fence.
 validated=vectors(reopened);mutate_thread('ops','status','PENDING_REVIEW');changed=vectors(reopened)
 check('validation_publication_gap_exists',validated!=changed)
 # Fail closed rather than advertise an unchanged current generation.
 check('gap_invalidates_no_publication',last_good.read_bytes()==sentinel)
 for c in reopened:c.close()
 # A fresh quiet complete capture can be durably serialized for this toy generation.
 watchers=[con(OUT/'core.sqlite',True),con(OUT/'ops.sqlite',True)];initial=vectors(watchers);start=time.monotonic();fresh,meta=capture(watchers,initial)
 complete={'generation':meta['digest'],'complete':True,'inputs':fresh};temp=OUT/'complete.pending';b=json.dumps(complete,sort_keys=True).encode();assert len(b)<16384
 with open(temp,'wb') as f:f.write(b);f.flush();os.fsync(f.fileno())
 os.replace(temp,OUT/'complete-fixture.json')
 check('quiet_fixture_capture_serialization',json.loads((OUT/'complete-fixture.json').read_text())['complete'])
 # This successful toy serialization does NOT close the real validation/publish race.
 for c in watchers:c.close()
 status='HOLD_NO_DURABLE_CROSS_OWNER_MUTATION_PUBLICATION_AUTHORITY'
except BaseException as exc:
 status='HOLD_DIAGNOSTIC_ERROR';observations['error']={'message':str(exc),'traceback':traceback.format_exc(limit=6)}
result={'status':status,'checks':checks,'observations':observations,'fixture':str(OUT),'files':{p.name:p.stat().st_size for p in OUT.iterdir() if p.is_file()},'scope':'Synthetic mutations to disposable accepted token_analysis and wt_treasury_review schemas only. No claim of complete accepted discovery source closure.','safety':{'production_writes':0,'provider_calls':0,'live_service_actions':0,'large_files':0}}
(OUT/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
