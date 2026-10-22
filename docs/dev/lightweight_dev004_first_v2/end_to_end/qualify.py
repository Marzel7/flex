"""DEV004 provider-free installed-interface qualification. Production is inaccessible."""
from __future__ import annotations
import ast,asyncio,contextlib,hashlib,importlib.util,json,os,resource,shutil,socket,sqlite3,sys,time,types
from pathlib import Path
from typing import Any,Optional
from dataclasses import dataclass,field
from unittest.mock import patch
resource.setrlimit(resource.RLIMIT_FSIZE,(16_000_000,16_000_000))
OUT=Path(sys.argv[1]) if len(sys.argv)>1 else Path('/private/tmp/dev004-e2e/run');OUT.mkdir(exist_ok=False)
SEED=Path('/private/tmp/dev004-first-v2-build/reproducibility-run')
L=Path('/Users/kevinkeaveney/Production/flex-listener');P=Path('/Users/kevinkeaveney/Production/flex-main-workers');W=Path('/Users/kevinkeaveney/Production/flex-walkback-dev002')
CORE=OUT/'core_v2.sqlite';OPS=OUT/'ops_v2.sqlite'
for n in ['core_v2.sqlite','ops_v2.sqlite']:shutil.copyfile(SEED/n,OUT/n)
report={'status':'HOLD','tests':[],'sources':{},'storage':[],'boundary':'SQLite write adapters and noncanonical metadata/SSE/symbol side effects are test doubles; installed decision/receipt/queue bodies unchanged. No full daemon startup qualification.'}
def blocked(*a,**k):raise AssertionError('NETWORK_FORBIDDEN')
socket.socket.connect=blocked;socket.create_connection=blocked
raw_connect=sqlite3.connect
def connect(database,*args,**kwargs):
 text=str(database);path=Path(text[5:].split('?')[0] if text.startswith('file:') else text).resolve()
 assert path.is_relative_to(OUT), 'OUTSIDE_DEV_DATABASE:'+str(path)
 kwargs.pop('priority',None);c=raw_connect(database,*args,**kwargs)
 if 'mode=ro' not in text:c.execute('pragma max_page_count=2048');c.execute('pragma cache_size=-2048')
 return c
sqlite3.connect=connect
for name,sub in [('src','src'),('src.core','src/core'),('src.ops','src/ops'),('src.utils','src/utils'),('src.evidence','src/evidence')]:
 mod=types.ModuleType(name);mod.__path__=[str(p/sub) for p in [P,L,W]];sys.modules[name]=mod
def load(name,path):
 report['sources'][str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
 spec=importlib.util.spec_from_file_location(name,path);mod=importlib.util.module_from_spec(spec);sys.modules[name]=mod;spec.loader.exec_module(mod);return mod
def selected(path,names,ns):
 report['sources'][str(path)]=hashlib.sha256(path.read_bytes()).hexdigest();tree=ast.parse(path.read_text());nodes=[]
 for n in ast.walk(tree):
  if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)) and n.name in names:nodes.append(n)
 assert len(nodes)==len(names)
 exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)]+nodes,type_ignores=[])),str(path),'exec'),ns)
 return ns
def test(name,fn):
 try:value=fn();report['tests'].append({'name':name,'status':'PASS','evidence':value})
 except Exception as e:report['tests'].append({'name':name,'status':'HOLD','error':type(e).__name__+':'+str(e)[:350]})
def storage(stage):
 report['storage'].append({'stage':stage,'core_bytes':CORE.stat().st_size,'ops_bytes':OPS.stat().st_size,'core_wal':Path(str(CORE)+'-wal').stat().st_size if Path(str(CORE)+'-wal').exists() else 0,'ops_wal':Path(str(OPS)+'-wal').stat().st_size if Path(str(OPS)+'-wal').exists() else 0})
def b58(data):
 alphabet='123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';n=int.from_bytes(data,'big');s=''
 while n:n,r=divmod(n,58);s=alphabet[r]+s
 return '1'*(len(data)-len(data.lstrip(b'\0')))+s
def wallet(label):return b58(hashlib.sha256(label.encode()).digest())
def signature(label):return b58(hashlib.sha512(label.encode()).digest())
M=wallet('DEV004 fresh launch');CREATOR=wallet('DEV004 fresh creator');SIG=signature('DEV004 create');MIG=signature('DEV004 migrate');NOW=int(time.time())
handoff=load('src.ops.walkback_create_handoff',P/'src/ops/walkback_create_handoff.py')
receipt=load('src.core.pumpportal_birth_audit',L/'src/core/pumpportal_birth_audit.py')
quarantine=load('src.core.birth_retry_quarantine',L/'src/core/birth_retry_quarantine.py')
deep=load('src.core.deep_walkback',W/'src/core/deep_walkback.py')
@contextlib.contextmanager
def managed(path,**kwargs):
 c=connect(path,timeout=1)
 try:
  if not kwargs.get('read_only'):c.execute('BEGIN IMMEDIATE')
  yield c
  c.commit()
 except BaseException:c.rollback();raise
 finally:c.close()
def writer(path,action):
 with managed(path) as c:return action(c)
storage('original_seed')
c=connect(CORE);c.execute('BEGIN IMMEDIATE');handoff.initialize(c,epoch='DEV004_SYNTHETIC_ONLY',activated_at=NOW-60,core_base=CORE.stat().st_size,ops_base=OPS.stat().st_size);c.execute('UPDATE wb_create_control SET enabled=1');c.commit();c.close()
# Exact required Core queue schemas only, read from V1 metadata through an independent read-only handle.
src=raw_connect('file:/Users/kevinkeaveney/Dev/claude/flex/database/flex_complete_database.db?mode=ro',uri=True);src.execute('pragma query_only=on')
with managed(CORE) as c:
 for name in ['birth_persist_queue','webhook_birth_queue']:
  ddl=src.execute("select sql from sqlite_master where type='table' and name=?",(name,)).fetchone()[0];c.execute(ddl)
src.close();storage('required_fixture_schema')
async def noop(*a,**k):return None
ns={'asyncio':asyncio,'time':time,'Optional':Optional,'managed_db_connect':managed,'DB_PATH':str(CORE),'PRIORITY_P0_CRITICAL_INGESTION':0,'_listener_ops_db_path':lambda:str(OPS),'_birth_telemetry_incr':lambda *a:None,'log_print':lambda *a,**k:None,'TOKEN_PREDICTION_RUNTIME_ENABLED':False,'_spawn_symbol_fetch':lambda *a:None,'_broadcast_to_flask':lambda *a:None,'PUMPSWAP_PROGRAM':'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA'}
selected(L/'src/core/pumpfun_curve_listener.py',['_insert_bonding_curve_token','_handle_pumpportal_create_frame','_record_pp_birth_outcome','_is_migration_transaction','enqueue_prospective_migration'],ns)
listener=types.SimpleNamespace(_portal_vsol={},completed_launches=set(),seen_mints=set(),_byzantine_birth_signal=None,_pumpfun_delivery_latency=types.SimpleNamespace(observe_pumpportal=lambda **k:None),_remember_recent_birth_token=lambda *a:None,_remember_bonding_curve_token=lambda *a:None,_upsert_birth_metadata_cache=noop,_ensure_pf_ws_creator=noop)
listener._eb_birth_audit=receipt.PumpPortalBirthAudit(enabled=False,path=str(OUT/'raw-disabled.jsonl'),compact_path=str(OUT/'receipts'))
for name in ['_insert_bonding_curve_token','_handle_pumpportal_create_frame','_record_pp_birth_outcome','_is_migration_transaction']:setattr(listener,name,types.MethodType(ns[name],listener))
frame={'mint':M,'signature':SIG,'traderPublicKey':CREATOR,'vSolInBondingCurve':1,'marketCapSol':30,'bondingCurveKey':wallet('DEV004 curve'),'symbol':'DEV004'}
async def birth():
 await listener._handle_pumpportal_create_frame(frame,signature=SIG,mint=M,receive_utc_ns=time.time_ns(),receive_monotonic_ns=time.monotonic_ns(),tracked_trade_mints=set(),migration_sol_threshold=85,ws=types.SimpleNamespace(send=noop));await asyncio.sleep(.01)
def check_birth():
 asyncio.run(birth())
 with managed(CORE,read_only=True) as c:r=c.execute('select earliest_tx_creator,pf_ws_creator,create_tx_signature,lifecycle_stage from token_analysis where mint=?',(M,)).fetchone()
 assert r==(CREATOR,CREATOR,SIG,'bonding_curve'),r
 files=list((OUT/'receipts').rglob('*.json'));assert len(files)==1;payload=json.loads(files[0].read_text());assert payload['persistence_outcome']=='COMMITTED',payload
 return {'canonical':True,'receipt_outcome':payload['persistence_outcome'],'receipt_bytes':files[0].stat().st_size}
test('fresh_CREATE_canonical_and_compact_receipt',check_birth);storage('one_synthetic_birth')
test('duplicate_CREATE',lambda:(asyncio.run(birth()),check_birth())[1])
def migration():
 assert listener._is_migration_transaction(['Instruction: Migrate','Instruction: CreatePool'])
 assert not listener._is_migration_transaction(['Instruction: Buy','Instruction: CreatePool'])
 extra={'signature':MIG,'migrated_at_ts':NOW,'source':'SYNTHETIC_PUMPSWAP','mint':M,'DB_PATH':str(CORE),'db_connect':connect}
 selected(L/'src/core/pumpfun_curve_listener.py',['_store_migration_tx'],extra);extra['_store_migration_tx']()
 with managed(CORE) as c:state=ns['enqueue_prospective_migration'](c,M,core_path=str(CORE),ops_path=str(OPS))
 assert state=='ENQUEUED',state
 with managed(CORE,read_only=True) as c:r=c.execute('select migrated_at,migration_tx from token_analysis where mint=?',(M,)).fetchone();q=c.execute('select status from creator_resolution_queue where mint=?',(M,)).fetchone()
 assert r==(NOW,MIG) and q==('pending',)
 return {'migration_persisted':True,'creator_queue':q[0],'scope':'installed detector and persistence closure; full daemon/transaction parsing not executed'}
test('migration_detection_persistence_creator_admission',migration);storage('one_synthetic_migration')
def atomic_proof():
 c=connect(CORE);c.execute('BEGIN IMMEDIATE');proof=handoff.Proof(M,CREATOR,SIG,'PUMPPORTAL','PUMPPORTAL_NEW_TOKEN',SIG,NOW);a=handoff.stage(c,proof,core_path=str(CORE),ops_path=str(OPS),now=NOW);c.rollback();c.close()
 with managed(CORE,read_only=True) as c:assert c.execute('select 1 from wb_create_delivery where mint=?',(M,)).fetchone() is None
 with managed(CORE) as c:a=handoff.stage(c,proof,core_path=str(CORE),ops_path=str(OPS),now=NOW)
 with managed(CORE) as c:b=handoff.stage(c,proof,core_path=str(CORE),ops_path=str(OPS),now=NOW)
 assert a=='PENDING' and b=='DUPLICATE'
 with managed(OPS) as c:c.execute("insert into wt_walkback_queue(mint,status,path_state,enqueued_at) values(?,'waiting','WAITING_FOR_CREATE_ANCHOR',?)",(M,NOW))
 return {'rollback_before_commit':True,'committed':a,'duplicate':b}
test('CREATE_proof_atomic_rollback_and_dedup',atomic_proof)
def dispatch_recovery():
 def fault(stage):
  if stage=='after_ops_commit':raise RuntimeError('SIMULATED_CRASH_AFTER_OPS_COMMIT')
 args={'read_core':lambda:managed(CORE,read_only=True),'write_core':lambda f:writer(CORE,f),'write_ops':lambda f:writer(OPS,f),'core_path':str(CORE),'ops_path':str(OPS)}
 first=handoff.dispatch(**args,now=NOW,fault=fault);second=handoff.dispatch(**args,now=NOW+100)
 with managed(CORE,read_only=True) as c:state=c.execute('select state,tries from wb_create_delivery where mint=?',(M,)).fetchone()
 with managed(OPS,read_only=True) as c:q=c.execute('select creator,create_anchor_signature,status from wt_walkback_queue where mint=?',(M,)).fetchone()
 assert state[0]=='ACK' and q==(CREATOR,SIG,'pending'),(first,second,state,q)
 return {'first':first,'restart':second,'state':state,'ops_anchor_unchanged':True}
test('Core_OPS_commit_then_crash_restart',dispatch_recovery)
def topology():
 treasury,subprov,account=map(wallet,['DEV004 treasury','DEV004 subprovider','DEV004 funding account'])
 with managed(OPS) as c:
  deep.ensure_schema(c)
  for hop,(parent,child) in enumerate([(account,CREATOR),(subprov,account),(treasury,subprov)],1):
   deep.persist_edge_candidate(c,mint=M,wallet=child,parent=parent,signature=signature('DEV004 funding'+str(hop)),block_time=NOW-hop,amount_lamports=1_000_000_000,mechanism='PLAIN_XFER',anchor_signature=SIG,anchor_block_time=NOW,hop_depth=hop,selection_status='SELECTED',evidence_strength='SYNTHETIC_TEST_ONLY')
 with managed(OPS,read_only=True) as c:rs=c.execute('select wallet,candidate_parent from wt_walkback_edge_candidates where mint=? order by hop_depth',(M,)).fetchall()
 assert rs==[(CREATOR,account),(account,subprov),(subprov,treasury)] and subprov!=account
 return {'distinct_roles_preserved':True,'edges':3,'classification':'synthetic persistence control; funding investigation and historical recognition not proved'}
test('distinct_funding_account_topology',topology);storage('synthetic_Walkback_edges')
def conflict():
 bad=signature('DEV004 conflicting CREATE');payload=json.dumps({'signature':bad,'_birth_retry_schema':1,'provenance':'PUMPPORTAL_NEW_TOKEN','mint':M,'traderPublicKey':wallet('other creator')},sort_keys=True)
 with managed(OPS) as c:c.execute("update wt_walkback_queue set status='complete',create_anchor_audit_state='VALID' where mint=?",(M,))
 with managed(CORE) as c:
  quarantine.ensure_schema(c);c.execute("insert into webhook_birth_queue(signature,payload,source,consumed) values(?,?,'pumpportal_retry',0)",(bad,payload));row=c.execute('select id from webhook_birth_queue where signature=?',(bad,)).fetchone()[0]
 with managed(CORE) as c,managed(OPS,read_only=True) as o:assert quarantine.quarantine(c,o,row,bad,payload,json.loads(payload))
 with managed(CORE) as c,managed(OPS,read_only=True) as o:assert quarantine.quarantine(c,o,row,bad,payload,json.loads(payload));r=c.execute('select quarantine_reason,consumed from webhook_birth_queue where id=?',(row,)).fetchone();anchor=c.execute('select create_tx_signature from token_analysis where mint=?',(M,)).fetchone()[0]
 assert r==('CORE_AUTHORITY_CONFLICT',0) and anchor==SIG
 return {'quarantined':True,'restart_idempotent':True,'canonical_unchanged':True}
test('permanent_conflict_quarantine',conflict)
def claim_complete():
 with managed(OPS) as c:a=deep.claim_with_lease(c,M,'DEV004-restart',300)
 assert a is False;return 'completed job not reopened'
test('completed_Walkback_not_reopened',claim_complete)
storage('end_intake_recovery')
(OUT/'phase1.json').write_text(json.dumps(report,indent=2,default=str)+'\n')
print(json.dumps(report['tests'],indent=2));print('SIZES',report['storage'][-1])
