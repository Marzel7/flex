"""DEV004 existing installed price interfaces with synthetic transport and fixture writer."""
from __future__ import annotations
import ast,contextlib,dataclasses,hashlib,importlib.util,json,os,resource,socket,sqlite3,subprocess,sys,time,types
from pathlib import Path
from typing import Any,Optional,Callable,Mapping
resource.setrlimit(resource.RLIMIT_FSIZE,(16_000_000,16_000_000))
OUT=Path(sys.argv[1]) if len(sys.argv)>1 else Path('/private/tmp/dev004-e2e/qualified');OPS=OUT/'ops_v2.sqlite';P=Path('/Users/kevinkeaveney/Production/flex-main-workers');W=Path('/Users/kevinkeaveney/Production/flex-walkback-dev002')
result={'tests':[],'sources':{},'mock_transport_calls':0,'real_provider_calls':0,'writer_boundary':'Existing offline injection uses synchronous fixture SQLite transactions; production shared writer/worker wiring not qualified.'}
def deny(*a,**k):raise AssertionError('REAL_NETWORK_FORBIDDEN')
socket.socket.connect=deny;socket.create_connection=deny
original=sqlite3.connect
def connect(db,*args,**kwargs):
 text=str(db);p=Path(text[5:].split('?')[0] if text.startswith('file:') else text).resolve();assert p.is_relative_to(OUT),'OUTSIDE_FIXTURE_DATABASE';kwargs.pop('priority',None);c=original(db,*args,**kwargs)
 if 'mode=ro' not in text:c.execute('pragma max_page_count=2048');c.execute('pragma cache_size=-2048')
 return c
sqlite3.connect=connect
for n,sub in [('src','src'),('src.core','src/core'),('src.ops','src/ops'),('src.utils','src/utils'),('src.evidence','src/evidence')]:
 m=types.ModuleType(n);m.__path__=[str(p/sub) for p in [P,W]];sys.modules[n]=m
def load(name,path):
 result['sources'][str(path)]=hashlib.sha256(path.read_bytes()).hexdigest();spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m
def test(name,fn):
 try:result['tests'].append({'name':name,'status':'PASS','evidence':fn()})
 except Exception as e:result['tests'].append({'name':name,'status':'HOLD','error':type(e).__name__+':'+str(e)[:350]})
load('src.core.db_write_queue',P/'src/core/db_write_queue.py')
stub=types.ModuleType('src.core.db_writer');stub.__dict__['dataclass']=dataclasses.dataclass;sys.modules['src.core.db_writer']=stub
tree=ast.parse((P/'src/core/db_writer.py').read_text());node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='DurableWriteReceipt');exec(compile(ast.Module(body=[node],type_ignores=[]),'installed-DurableWriteReceipt','exec'),stub.__dict__)
def commit(path,item):
 c=connect(path)
 try:
  c.execute('BEGIN IMMEDIATE')
  for sql,params in item.statements:c.execute(sql,params)
  c.commit();return stub.DurableWriteReceipt(True,time.time(),len(item.statements))
 except BaseException:c.rollback();raise
 finally:c.close()
stub.commit_write_and_wait=commit;sys.modules['src.core.db_writer']=stub
locking=types.ModuleType('src.utils.db_locking');locking.db_connect=connect;sys.modules['src.utils.db_locking']=locking
DEV=Path(__file__).resolve().parents[4]/'runtime/creator_resolution'
sys.modules['src.ops'].__path__.insert(0,str(DEV/'src/ops'))
monitor=load('src.ops.operation_monitor_worker',DEV/'src/ops/operation_monitor_worker.py')
binding=load('src.ops.token_data_provider_bindings',P/'src/ops/token_data_provider_bindings.py')
rate=load('src.ops.birdeye_rate_limit',P/'src/ops/birdeye_rate_limit.py')
queue_mod=load('src.evidence.queue',P/'src/evidence/queue.py')
NOW=int(time.time())-1800;M='DEV004_SYNTHETIC_PRICE_TOKEN';transport_requests=[]
def fake_provider(request):
 transport_requests.append(request);result['mock_transport_calls']+=1
 params=request['request_parameters']
 if params['type']=='1s':items=[{'unixTime':NOW,'o':1,'h':1,'l':1,'c':1},{'unixTime':NOW+1,'o':100,'h':100,'l':100,'c':100}]
 else:items=[{'unixTime':NOW+900,'o':100,'h':250,'l':90,'c':150},{'unixTime':NOW+1800,'o':150,'h':150,'l':140,'c':150}]
 return binding.ProviderTransportOutcome(200,{'data':{'items':items}},{},None)
transport=monitor.MonitorBirdeyeTransport(fake_provider)
def opening():
 entry,req=transport.acquire_watchtower_entry({'mint':M},{'migration_timestamp':NOW,'time_from':NOW,'time_to':NOW+2});assert entry=={'timestamp':NOW+1,'mc':100.0};return {'entry':entry,'window':[req['request_parameters']['time_from'],req['request_parameters']['time_to']]}
test('Birdeye_synthetic_strict_target_opening',opening)
def missing_target():
 def later(req):return binding.ProviderTransportOutcome(200,{'data':{'items':[{'unixTime':NOW+2,'o':100,'h':100,'l':100,'c':100}]}},{},None)
 t=monitor.MonitorBirdeyeTransport(later)
 revision='71b6e338cfb54057ec4984d41bfbe02477c90dba'
 def retained(path):return subprocess.check_output(['git','-C','/Users/kevinkeaveney/Dev/claude/flex','show',revision+':'+path],text=True)
 normal=ast.parse(retained('src/ops/strict_migration_window.py'));normalizer=next(n for n in normal.body if isinstance(n,ast.FunctionDef) and n.name=='normalize_opening_ohlcv')
 tree=ast.parse(retained('src/ops/operation_monitor_worker.py'));error=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='StrictEntryNormalizationError');cl=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='MonitorBirdeyeTransport');fn=next(n for n in cl.body if getattr(n,'name',None)=='acquire_watchtower_entry')
 ns={'Mapping':Mapping,'Any':Any,'build_birdeye_ohlcv_request':binding.build_birdeye_ohlcv_request}
 exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),normalizer,error,fn],type_ignores=[])),revision,'exec'),ns)
 plan={'migration_timestamp':NOW,'time_from':NOW,'time_to':NOW+2};p={'mint':M};self=types.SimpleNamespace(binding=later,_capture_shadow_opening_response=lambda *a:None)
 accepted,_=ns['acquire_watchtower_entry'](self,p,plan);installed,_=t.acquire_watchtower_entry(p,plan)
 result['opening_comparison']={'accepted_revision':revision,'accepted':accepted,'installed':installed,'classification':'EXACT_MATCH' if installed==accepted else 'REGRESSION'}
 assert installed==accepted,'Installed opening omits accepted plus_one_absent/migration_absent fallback provenance'
 return result['opening_comparison']
test('opening_fallback_provenance_matches_accepted',missing_target)
def opening_matrix():
 from src.ops.dev004_opening_policy import reduce_policy
 revision='71b6e338cfb54057ec4984d41bfbe02477c90dba'
 def retained(path):return subprocess.check_output(['git','-C',str(DEV),'show',revision+':'+path],text=True)
 policy_ns={};exec(retained('src/ops/strict_migration_window.py'),policy_ns)
 tree=ast.parse(retained('src/ops/operation_monitor_worker.py'));error=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='StrictEntryNormalizationError');cl=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='MonitorBirdeyeTransport');fn=next(n for n in cl.body if getattr(n,'name',None)=='acquire_watchtower_entry')
 ns={'normalize_opening_ohlcv':policy_ns['normalize_opening_ohlcv'],'build_birdeye_ohlcv_request':binding.build_birdeye_ohlcv_request};exec(compile(ast.fix_missing_locations(ast.Module(body=[error,fn],type_ignores=[])),revision,'exec'),ns)
 cases={'priority':[0,2,1],'migration_fallback':[0,2],'plus_two':[2],'outside_window':[3],'empty':[],'duplicate_target':[1,1],'duplicate_fallback':[0,0],'malformed':[1]}
 evidence=[]
 for name,offsets in cases.items():
  items=[{'unixTime':NOW+x,'o':100,'h':100,'l':100,'c':100} for x in offsets]
  if name=='malformed':items[0].pop('o')
  def reply(req):return binding.ProviderTransportOutcome(200,{'data':{'items':items}},{},None)
  accepted_self=types.SimpleNamespace(binding=reply,_capture_shadow_opening_response=lambda *a:None)
  plan={'migration_timestamp':NOW,'time_from':NOW,'time_to':NOW+2}
  def invoke(call):
   try:return ('VALUE',call({'mint':M},plan)[0])
   except ValueError:return ('MISSING_OR_INVALID',None)
  a=invoke(lambda p,w:ns['acquire_watchtower_entry'](accepted_self,p,w));b=invoke(monitor.MonitorBirdeyeTransport(reply).acquire_watchtower_entry);assert a==b,(name,a,b)
  if a[0]=='VALUE':assert policy_ns['reduce_policy'](migration_timestamp=NOW,entry=a[1])==reduce_policy(migration_timestamp=NOW,entry=b[1])
  evidence.append({'case':name,'result':a})
 return evidence
test('accepted_opening_selection_and_policy_matrix',opening_matrix)
q=monitor.MonitorQueue(OUT/'monitor_queue_final',enabled=True)
def admission():
 a=q.enqueue_after_assignment(mint=M,operation_id='watchtower',assignment={'assigned_at':NOW,'identity':'DEV004_SYNTHETIC'},canonical_birth={'entry_timestamp':NOW+1,'entry_mc_usd':100});b=q.enqueue_after_assignment(mint=M,operation_id='watchtower',assignment={'assigned_at':NOW,'identity':'DEV004_SYNTHETIC'},canonical_birth={'entry_timestamp':NOW+1,'entry_mc_usd':100});assert a['job_id']==b['job_id'];return {'stable_identity':a['job_id'],'depth':q.queue.depth()}
test('new_price_assignment_and_dedup',admission)
keep=connect(OPS);keep.execute('pragma journal_mode=WAL')
def worker_cycle():
 w=monitor.MonitorWorker(q,transport,db_path=str(OPS),persist=lambda item:commit(OPS,item));assert w.process_once()==1
 c=connect(OPS);r=c.execute('select entry_mc_usd,running_peak_mc_usd,current_multiple,monitor_state from operation_monitor_facts where operation_id=? and mint=?',('watchtower',M)).fetchone();c.close();assert r==(100.0,250.0,1.5,'MONITORING_ACTIVE'),(r,q.queue.depth());return {'fact':r,'depth':q.queue.depth(),'wal_bytes':Path(str(OPS)+'-wal').stat().st_size if Path(str(OPS)+'-wal').exists() else 0}
test('installed_price_worker_candles_lifecycle_projection',worker_cycle)
def stored_opening_fallbacks():
 from unittest.mock import patch
 evidence=[]
 for offset,method,exactness in [(1,'FIRST_FULL_POST_MIGRATION_SECOND_MC','FIRST_FULL_POST_MIGRATION_SECOND_MC'),(0,'MIGRATION_SECOND_MC_FALLBACK','MIGRATION_SECOND_MC_FALLBACK'),(2,'BOUNDED_POST_MIGRATION_MC_FALLBACK','POST_MIGRATION_OFFSET_2S_OBSERVED_MC')]:
  mint='DEV004_PROVENANCE_OFFSET_'+str(offset);mq=monitor.MonitorQueue(OUT/('opening_'+str(offset)),enabled=True)
  mq.enqueue_after_assignment(mint=mint,operation_id='watchtower',assignment={'assigned_at':NOW},canonical_birth={})
  def provider(req):
   if req['request_parameters']['type']=='1s':return binding.ProviderTransportOutcome(200,{'data':{'items':[{'unixTime':NOW+offset,'o':100,'h':100,'l':100,'c':100}]}},{},None)
   return fake_provider(req)
  evaluation={'result':'ENTRY_EVIDENCE_ACQUISITION_DUE','entry_acquisition':{'migration_timestamp':NOW,'time_from':NOW,'time_to':NOW+2}}
  with patch.object(monitor,'_entry_inventory',return_value=evaluation):assert monitor.MonitorWorker(mq,monitor.MonitorBirdeyeTransport(provider),db_path=str(OPS),persist=lambda item:commit(OPS,item)).process_once()==1
  c=connect(OPS);row=c.execute('select entry_method,entry_exactness,entry_timestamp,entry_mc_usd from operation_monitor_facts where mint=?',(mint,)).fetchone();c.close()
  assert row==(method,exactness,NOW+offset,100.0),(row,mq.queue.depth());evidence.append(row)
 return evidence
test('opening_baseline_labels_and_timestamps_persist',stored_opening_fallbacks)

def recovery():
 r=queue_mod.EvidenceIntakeQueue(OUT/'recovery_queue_final',enabled=True,max_messages=10,max_bytes=100000,max_attempts=3);r.enqueue({'mint':M,'test':'recovery'},message_id='DEV004_RESTART');claimed=r.claim(1);assert len(claimed)==1
 reopened=queue_mod.EvidenceIntakeQueue(OUT/'recovery_queue_final',enabled=True,max_messages=10,max_bytes=100000,max_attempts=3);assert reopened.recover()==1;again=reopened.claim(1);assert again[0].message_id==claimed[0].message_id;reopened.nack(again[0],'SYNTHETIC_RETRY');assert reopened.recover()==1;again=reopened.claim(1);assert again[0].payload['attempts']==1;reopened.ack(again[0]);assert not any(reopened.depth().values())
 return {'identity_preserved':True,'attempts_preserved':True,'retry_recovered':True,'completed_not_reopened':True}
test('durable_processing_queue_identity',recovery)
def commit_ack_crash():
 cq=monitor.MonitorQueue(OUT/'price_commit_crash_final',enabled=True);mint='DEV004_PRICE_COMMIT_CRASH_FINAL';cq.enqueue_after_assignment(mint=mint,operation_id='watchtower',assignment={'assigned_at':NOW},canonical_birth={'entry_timestamp':NOW+1,'entry_mc_usd':100})
 def crash():raise RuntimeError('SYNTHETIC_AFTER_COMMIT_BEFORE_ACK')
 before=len(transport_requests);worker=monitor.MonitorWorker(cq,transport,db_path=str(OPS),persist=lambda item:commit(OPS,item),before_ack=crash);worker.process_once();assert cq.queue.depth()['retry']==1
 cq.queue.recover();monitor.MonitorWorker(cq,transport,db_path=str(OPS),persist=lambda item:commit(OPS,item)).process_once()
 c=connect(OPS);n=c.execute('select count(*) from operation_monitor_observations where operation_id=? and mint=?',('watchtower',mint)).fetchone()[0];c.close();assert n==2 and len(transport_requests)-before==2
 return {'observations_deduplicated':True,'mock_transport_executions':2,'limitation':'External execution is at-least-once across commit/ACK ambiguity; existing bounded queue attempts retained. No exactly-once external-call guarantee claimed.'}
test('price_commit_ACK_crash_bounded_replay',commit_ack_crash)
def terminal_finalization():
 tq=monitor.MonitorQueue(OUT/'terminal_price_final',enabled=True);mint='DEV004_TERMINAL_SYNTHETIC_FINAL';tq.enqueue_after_assignment(mint=mint,operation_id='watchtower',assignment={'assigned_at':NOW},canonical_birth={'entry_timestamp':NOW+1,'entry_mc_usd':100})
 def collapse(req):
  response=fake_provider(req);response.payload['data']['items'][-1].update({'c':10,'l':10});return response
 from src.ops.watchtower_terminal_ath_finalizer import WatchtowerTerminalAthFinalizer
 factory=lambda path:WatchtowerTerminalAthFinalizer(path,binding=deny)
 worker=monitor.MonitorWorker(tq,monitor.MonitorBirdeyeTransport(collapse),db_path=str(OPS),persist=lambda item:commit(OPS,item),terminal_finalizer_factory=factory);assert worker.process_once()==1;assert tq.queue.depth()['pending']==1;assert worker.process_once()==1
 first=factory(str(OPS)).finalize(mint,interval='15m',watchtower_price_fact_contract=True);second=factory(str(OPS)).finalize(mint,interval='15m',watchtower_price_fact_contract=True)
 assert first['state']==second['state']=='ALREADY_FINALIZED' and first['fact']['ath_finalization_request_id']==second['fact']['ath_finalization_request_id'];assert first['fact']['final_proven_ath_mc']==250
 return {'ATH':250,'multiple':2.5,'reopen_finalization_identity_stable':True,'additional_provider_calls':0}
test('synthetic_terminal_finalization_and_reopen',terminal_finalization)
def bounded_retries():
 calls=[];waits=[]
 r=rate.run_retries(lambda:(calls.append(1) or ('RATE_LIMITED',None)),waits.append)
 assert r['exhausted'] and len(calls)==9 and len(waits)==8 and max(waits)<=60
 return {'mock_attempts':len(calls),'max_retry_limit':rate.MAX_RATE_LIMIT_RETRIES_PER_LOGICAL_WINDOW,'waits':waits}
test('existing_Birdeye_429_retry_bound',bounded_retries)
def credentials():
 from unittest.mock import patch
 from src.utils.birdeye_credentials import birdeye_credential,BirdeyeConfigurationError
 with patch.dict(os.environ,{'BIRDEYE':'synthetic-paid','BIRDEYE_KK':'synthetic-legacy'},clear=True):assert birdeye_credential()=='synthetic-paid'
 with patch.dict(os.environ,{'BIRDEYE':' ','BIRDEYE_KK':'synthetic-legacy'},clear=True):
  try:birdeye_credential();raise AssertionError('blank accepted')
  except BirdeyeConfigurationError:pass
 captured=[]
 def fake_http(req,**kwargs):captured.append(req);return binding.ProviderTransportOutcome(200,{'data':{}},{},None)
 with patch.dict(os.environ,{'BIRDEYE':'synthetic-paid','BIRDEYE_KK':'synthetic-legacy'},clear=True):
  client=binding.BirdeyeProductionBinding(transport=fake_http);client(binding.build_birdeye_ohlcv_request(address=M,interval='15m',time_from=100,time_to=1000))
 assert captured and any(k.lower()=='x-api-key' and v=='synthetic-paid' for k,v in captured[0].header_items())
 return {'authority':'BIRDEYE','blank_rejected':True,'authorization_header':True,'credentials':'synthetic only'}
test('credential_authority_and_authorization',credentials)
candidate=load('src.ops.potential_candidate_matcher',P/'src/ops/potential_candidate_matcher.py')
def candidate_controls():
 sig=((1,'PLAIN_XFER',1000000000),(2,'PLAIN_XFER',2000000000));spec=candidate.PotentialCandidateMatchSpec('DEV004_SYNTHETIC_CANDIDATE',sig)
 assert candidate.match_signature(sig,(spec,)).state=='UNIQUE_MATCH';assert candidate.match_signature(None,(spec,)).state=='INSUFFICIENT_INPUT';assert candidate.match_signature(sig,(spec,candidate.PotentialCandidateMatchSpec('SECOND',sig))).state=='MULTI_MATCH'
 return {'exact_signature':True,'insufficient_input':True,'collision_not_promoted':True,'scope':'pure matcher controls; candidate creation/review/global discovery not qualified'}
test('Potential_candidate_match_and_collision_controls',candidate_controls)
def candidate_population():
 c=connect(OPS)
 try:rows=c.execute('select * from potential_operation_identity limit 1').fetchall()
 finally:c.close()
 return {'rows':len(rows)}
test('Potential_persisted_identity_dependency',candidate_population)
result['storage']={'ops_bytes':OPS.stat().st_size,'observed_wal_bytes':Path(str(OPS)+'-wal').stat().st_size if Path(str(OPS)+'-wal').exists() else 0}
keep.close();result['transport_requests']=transport_requests
(OUT/'phase2.json').write_text(json.dumps(result,indent=2,default=str)+'\n');print(json.dumps(result['tests'],indent=2));print('STORAGE',result['storage'])
