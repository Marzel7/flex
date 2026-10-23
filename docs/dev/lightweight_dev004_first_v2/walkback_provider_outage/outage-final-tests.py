from pathlib import Path
import sqlite3,sys,types,os,resource,socket,urllib.request,shutil,importlib,json
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000));O=Path('/private/tmp/dev004-provider-outage-final');O.mkdir(exist_ok=True);S=Path('/private/tmp/dev004-compact-creator-qualified/classification_closure');W=Path('/Users/kevinkeaveney/.codex/worktrees/dev004-first-v2-prototype/flex/runtime/walkback');D=Path('/Users/kevinkeaveney/.codex/worktrees/dev004-first-v2-prototype/flex')
for k in ['core','ops']:
 if not (O/(k+'.sqlite')).exists():shutil.copy2(S/(k+'_v2.sqlite'),O/(k+'.sqlite'))
os.environ.update(DB_PATH=str(O/'core.sqlite'),WT_OPS_DB_PATH=str(O/'ops.sqlite'),HELIUS_RPC_URL='http://invalid.synthetic',DEV004_CREATOR_HISTORY_PATH='/private/tmp/dev004-compact-creator-qualified/history.json',PYTHONDONTWRITEBYTECODE='1')
def forbidden(*a,**k):raise AssertionError('REAL_NETWORK_FORBIDDEN')
socket.socket.connect=forbidden;urllib.request.urlopen=forbidden
raw=sqlite3.connect

def connect(path,*a,**kw):
 text=str(path);p=Path(text[5:].split('?')[0] if text.startswith('file:') else text).resolve();assert text==':memory:' or p.is_relative_to(O),'NON_FIXTURE_CONNECTION: '+str(p)
 c=raw(path,*a,**kw)
 if 'mode=ro' not in text:c.execute('pragma max_page_count=3840')
 return c
sqlite3.connect=connect
for name,sub in [('src','src'),('src.core','src/core'),('src.ops','src/ops'),('src.utils','src/utils')]:m=types.ModuleType(name);m.__path__=[str(W/sub)];sys.modules[name]=m
ww=importlib.import_module('src.core.walkback_worker');q=importlib.import_module('src.core.walkback_queue');ao=importlib.import_module('src.ops.attribution_outcome');print('BOOTSTRAP_OK',ww.__file__)
ops=connect(O/'ops.sqlite');ops.row_factory=sqlite3.Row
print('schema before',len(ops.execute('pragma table_info(wt_walkback_queue)').fetchall()))
q.ensure_schema(ops);ww.deep_walkback.ensure_schema(ops);print('SCHEMA_OK')

registry=importlib.import_module('src.ops.manual_registry');ops.executescript(registry.DDL);ops.commit()
text=(Path('/Users/kevinkeaveney/Production/flex-listener/src/core/watchtower/create_interceptor.py')).read_text();start=text.index('CREATE TABLE IF NOT EXISTS wt_detected_creates (');end=text.index(');',start)+2
c=connect(O/'core.sqlite');c.execute(text[start:end]);c.commit();c.close()

import ast,contextlib,asyncio,time,hashlib,importlib.util
from typing import Any,Optional
L=Path('/Users/kevinkeaveney/Production/flex-listener');P=Path('/Users/kevinkeaveney/Production/flex-main-workers');NOW=1792747000
# Qualified DEV compact evaluator, other modules remain installed runtime bodies.
for name,file in [('src.ops.dev004_compact_creator_history',D/'runtime/creator_resolution/src/ops/dev004_compact_creator_history.py'),('src.ops.attribution_outcome',D/'runtime/creator_resolution/src/ops/attribution_outcome.py')]:
 spec=importlib.util.spec_from_file_location(name,file);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m)
ao=sys.modules['src.ops.attribution_outcome']
def b58(data):
 alphabet='123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';n=int.from_bytes(data,'big');s=''
 while n:n,r=divmod(n,58);s=alphabet[r]+s
 return '1'*(len(data)-len(data.lstrip(b'\0')))+s
def wallet(t):return b58(hashlib.sha256(t.encode()).digest())
def sig(t):return b58(hashlib.sha512(t.encode()).digest())
M=wallet('DEV004 complete investigation launch');C=wallet('DEV004 complete investigation creator');FA=wallet('DEV004 distinct funding account');ALT=wallet('DEV004 lower ranked parent');SP='GozgDuzPnLaBECJtDf7cnoVZUnyJQZZvQqMmGsnMDPEr';T='2ghjbHVHi6pTUTapmjgq1WkdnEuPJMMp9dhEL2ffHDry';CREATE=sig('DEV004 complete CREATE');S1=sig('DEV004 FA funds creator');S2=sig('DEV004 SP funds FA');SA=sig('DEV004 competing plain parent')
@contextlib.contextmanager
def managed(path,**kwargs):
 c=connect(path);c.row_factory=sqlite3.Row
 try:yield c;c.commit()
 except BaseException:c.rollback();raise
 finally:c.close()
async def noop(*a,**k):return None
ns={'asyncio':asyncio,'time':time,'Optional':Optional,'managed_db_connect':managed,'DB_PATH':str(O/'core.sqlite'),'PRIORITY_P0_CRITICAL_INGESTION':0,'_listener_ops_db_path':lambda:str(O/'ops.sqlite'),'_birth_telemetry_incr':lambda *a:None,'log_print':lambda *a,**k:None,'TOKEN_PREDICTION_RUNTIME_ENABLED':False,'_spawn_symbol_fetch':lambda *a:None,'_broadcast_to_flask':lambda *a:None}
names={'_insert_bonding_curve_token','_handle_pumpportal_create_frame','_record_pp_birth_outcome'};tree=ast.parse((L/'src/core/pumpfun_curve_listener.py').read_text());nodes=[n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names];exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)]+nodes,type_ignores=[])),'installed-listener','exec'),ns)
spec=importlib.util.spec_from_file_location('src.core.pumpportal_birth_audit',L/'src/core/pumpportal_birth_audit.py');receipt=importlib.util.module_from_spec(spec);sys.modules[spec.name]=receipt;spec.loader.exec_module(receipt);listener=types.SimpleNamespace(_portal_vsol={},completed_launches=set(),seen_mints=set(),_byzantine_birth_signal=None,_pumpfun_delivery_latency=types.SimpleNamespace(observe_pumpportal=lambda **k:None),_remember_recent_birth_token=lambda *a:None,_remember_bonding_curve_token=lambda *a:None,_upsert_birth_metadata_cache=noop,_ensure_pf_ws_creator=noop,_eb_birth_audit=receipt.PumpPortalBirthAudit(enabled=False,path=str(O/'raw-disabled.jsonl'),compact_path=str(O/'receipts')))
for name in names:setattr(listener,name,types.MethodType(ns[name],listener))

from unittest.mock import patch

def admit(label, anchor=True):
    mint = wallet('DEV004 investigation ' + label)
    creator = wallet('DEV004 creator ' + label)
    signature = sig('DEV004 anchor ' + label)
    asyncio.run(listener._handle_pumpportal_create_frame({'mint': mint, 'signature': signature, 'traderPublicKey': creator, 'vSolInBondingCurve': 1, 'marketCapSol': 30, 'bondingCurveKey': wallet(label + ' curve'), 'symbol': 'DEV004'}, signature=signature, mint=mint, receive_utc_ns=time.time_ns(), receive_monotonic_ns=time.monotonic_ns(), tracked_trade_mints=set(), migration_sol_threshold=85, ws=types.SimpleNamespace(send=noop)))
    core = connect(O / 'core.sqlite')
    core.row_factory = sqlite3.Row
    if not anchor:
        mint = wallet('DEV004 no birth ' + label)
        signature = None
    cls = q.enqueue_migration(ops, mint=mint, creator=creator, live_conn=core, create_signature=signature, create_source='PUMPPORTAL_NEW_TOKEN' if anchor else None, create_block_time=NOW if anchor else None)
    ops.commit()
    core.close()
    return (mint, creator, signature, cls)

def state(mint):
    c = connect(O / 'ops.sqlite')
    c.row_factory = sqlite3.Row
    r = {'queue': dict(c.execute('select * from wt_walkback_queue where mint=?', (mint,)).fetchone()), 'outcome': None, 'memberships': c.execute('select count(*) from operator_launch_membership where mint=?', (mint,)).fetchone()[0]}
    v = c.execute('select * from wt_attribution_outcomes where mint=?', (mint,)).fetchone()
    r['outcome'] = dict(v) if v else None
    c.close()
    return r

results=[]
class Response:
 def __init__(self,value):self.value=value
 def read(self):return json.dumps(self.value).encode()
def step(mint, response=None, error=None):
 # Pass the preclaim row, exactly as drain_batch does.
 row=ops.execute('select * from wt_walkback_queue where mint=?',(mint,)).fetchone()
 assert ww._mark_running(ops,mint)
 with patch('urllib.request.urlopen',side_effect=error,return_value=Response(response)) as transport:
  used=ww._process_row(ops,row)
 assert transport.call_count==1
 return state(mint),used
import urllib.error
for label,error,response in [
 ('timeout',TimeoutError('synthetic'),None),
 ('rate_limit',urllib.error.HTTPError('http://synthetic',429,'synthetic',{},None),None),
 ('server_error',urllib.error.HTTPError('http://synthetic',503,'synthetic',{},None),None),
 ('connection',ConnectionError('synthetic'),None),
 ('malformed_envelope',None,{}),
 ('malformed_history',None,{'result':{}}),
 ('rpc_error',None,{'error':{'code':-32000}}),
]:
 mint,c,a,_=admit(label);st,used=step(mint,response,error)
 assert st['queue']['status']=='pending' and not st['outcome'] and st['memberships']==0
 assert used==1 and st['queue']['rpc_used']==1 and st['queue']['last_error'].startswith('ACQUISITION_UNAVAILABLE:')
 # Reopen durable state, duplicate admission retains attempt/cursor state.
 ops.close();ops=connect(O/'ops.sqlite');ops.row_factory=sqlite3.Row
 admit(label);assert state(mint)['queue']['attempts']==1
 st,used=step(mint,{'result':[]})
 assert st['queue']['status']=='complete' and st['outcome']['outcome_type']=='INSUFFICIENT_EVIDENCE' and st['queue']['attempts']==2
 assert not ww._mark_running(ops,mint)
 results.append({'case':label,'status':'PASS','unavailable_then_genuine_empty':True,'credits':st['queue']['rpc_used']})
mint,c,a,_=admit('exhaustion')
for attempt in range(ww.MAX_ATTEMPTS):
 st,used=step(mint,error=TimeoutError('synthetic'))
 assert st['queue']['attempts']==attempt+1 and not st['outcome']
assert st['queue']['status']=='failed' and st['queue']['intelligence_outcome'] is None
assert not ww._mark_running(ops,mint);ww.finalize_exhausted_pending(ops)
assert state(mint)['outcome'] is None
results.append({'case':'retry_ceiling','status':'PASS','attempts':ww.MAX_ATTEMPTS,'evidence_outcome':None})
# Interrupted lease recovery at the retry ceiling must not turn outage into evidence.
ops.execute("update wt_walkback_queue set status='pending' where mint=?",(mint,));ops.commit()
ww.finalize_exhausted_pending(ops);assert state(mint)['queue']['status']=='failed' and state(mint)['outcome'] is None
results.append({'case':'restart_exhausted_pending','status':'PASS'})
# Explicit null transaction is not a transport failure; malformed payload is.
with patch('urllib.request.urlopen',return_value=Response({'result':None})):assert ww._get_tx('synthetic') is None
for payload in [{'result':[]},{'result':{'transaction':{}}}]:
 with patch('urllib.request.urlopen',return_value=Response(payload)):
  try:ww._get_tx('synthetic')
  except ww.AcquisitionUnavailable:pass
  else:raise AssertionError('malformed transaction accepted')
results.append({'case':'transaction_null_vs_malformed','status':'PASS'})
for method,params,payload in [
 ('getSignaturesForAddress',['synthetic'],{'result':[{}]}),
 ('getAccountInfo',['synthetic'],{'result':{'value':{}}}),
 ('getTransaction',['synthetic'],{'result':{'transaction':{'signatures':['synthetic']}}}),
]:
 with patch('urllib.request.urlopen',return_value=Response(payload)):
  try:ww._rpc(method,params)
  except ww.AcquisitionUnavailable:pass
  else:raise AssertionError('malformed response accepted')
with patch('urllib.request.urlopen',return_value=type('BadJSON',(),{'read':lambda self:b'not json'})()):
 try:ww._rpc('getSignaturesForAddress',['synthetic'])
 except ww.AcquisitionUnavailable:pass
 else:raise AssertionError('bad JSON accepted')
results.append({'case':'malformed_method_payloads_and_json','status':'PASS'})
(O/'outage_results.json').write_text(json.dumps({'status':'PASS','tests':results,'real_provider_calls':0},indent=2)+'\n')
print('PASS',len(results),'outage groups')
