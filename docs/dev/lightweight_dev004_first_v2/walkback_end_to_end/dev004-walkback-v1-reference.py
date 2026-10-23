exec(compile(open('/private/tmp/dev004-walkback-v1-bootstrap.py').read(),'bootstrap','exec'))
import ast,contextlib,asyncio,time,hashlib,importlib.util
from typing import Any,Optional
L=Path('/Users/kevinkeaveney/Production/flex-listener');P=Path('/Users/kevinkeaveney/Production/flex-main-workers');NOW=1792747000
# Qualified DEV compact evaluator, other modules remain installed runtime bodies.
os.environ.pop('DEV004_CREATOR_HISTORY_PATH',None)
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
asyncio.run(listener._handle_pumpportal_create_frame({'mint':M,'signature':CREATE,'traderPublicKey':C,'vSolInBondingCurve':1,'marketCapSol':30,'bondingCurveKey':wallet('curve'),'symbol':'DEV004'},signature=CREATE,mint=M,receive_utc_ns=time.time_ns(),receive_monotonic_ns=time.monotonic_ns(),tracked_trade_mints=set(),migration_sol_threshold=85,ws=types.SimpleNamespace(send=noop)))
core=connect(O/'core.sqlite');core.row_factory=sqlite3.Row;birth=dict(core.execute('select mint,pf_ws_creator,create_tx_signature from token_analysis where mint=?',(M,)).fetchone());assert birth['pf_ws_creator']==C and birth['create_tx_signature']==CREATE
cls=q.enqueue_migration(ops,mint=M,creator=C,live_conn=core,create_signature=CREATE,create_slot=1000,create_block_time=NOW,create_source='PUMPPORTAL_NEW_TOKEN');ops.commit();assert cls=='FULL_WALKBACK'
def tx(parent,child,signature,slot,lamports,wrapped):
 ins=[{'programId':'11111111111111111111111111111111','parsed':{'type':'transfer','info':{'source':parent,'destination':child,'lamports':lamports}}}]
 if wrapped:ins.append({'programId':ww._TOKEN_PROGRAM,'parsed':{'type':'closeAccount','info':{'account':wallet(signature+' temp'),'owner':parent,'destination':child}}})
 return {'slot':slot,'blockTime':NOW-(1000-slot),'transaction':{'signatures':[signature],'message':{'accountKeys':[{'pubkey':parent,'signer':True},{'pubkey':child,'signer':False}],'instructions':ins}},'meta':{'err':None,'preBalances':[lamports+10000000,0],'postBalances':[9995000,lamports],'innerInstructions':[],'logMessages':[]}}
transactions={S1:tx(FA,C,S1,990,2122039000,True),S2:tx(SP,FA,S2,980,430000000000,True),SA:tx(ALT,C,SA,995,1000000,False)};calls=[]
def transport(method,params):
 calls.append({'method':method,'params':params});assert len(calls)<=32,'SYNTHETIC_REQUEST_BUDGET'
 if method=='getSignaturesForAddress':
  w=params[0];items=[(S1,990),(SA,995)] if w==C else [(S2,980)] if w==FA else []
  return [{'signature':s,'slot':i,'err':None} for s,i in items]
 if method=='getTransaction':assert params[0] in transactions;return transactions[params[0]]
 if method=='getAccountInfo':return {'value':{'owner':ww._SYSTEM_PROGRAM}}
 raise AssertionError('UNEXPECTED_SYNTHETIC_RPC')
ww._rpc=transport
assert ww._mark_running(ops,M);assert not ww._mark_running(ops,M);row=ops.execute('select * from wt_walkback_queue where mint=?',(M,)).fetchone();used=ww._process_row(ops,row);ops.commit()
result={'birth':birth,'admitted_class':cls,'queue':dict(ops.execute('select * from wt_walkback_queue where mint=?',(M,)).fetchone()),'outcome':dict(ops.execute('select * from wt_attribution_outcomes where mint=?',(M,)).fetchone()) if ops.execute('select 1 from wt_attribution_outcomes where mint=?',(M,)).fetchone() else None,'edges':[dict(r) for r in ops.execute('select * from wt_walkback_edge_candidates where mint=? order by hop_depth,selection_status',(M,))],'rpc_used':used,'synthetic_calls':calls,'membership':[dict(r) for r in ops.execute('select * from operator_launch_membership where mint=?',(M,))]}
(O/'happy.json').write_text(json.dumps(result,indent=2)+'\n');print('RESULT',result['queue']['status'],result['queue']['intelligence_outcome'],result['outcome']['outcome_type'] if result['outcome'] else None,'membership',len(result['membership']));print('EDGE_SELECTION',[(r['wallet'],r['candidate_parent'],r['selection_status']) for r in result['edges']])
