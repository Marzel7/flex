s=open('/private/tmp/dev004-walkback-cases.py').read();exec(compile(s.split("run('no attribution'")[0],'installed-case-loader','exec'))
from unittest.mock import patch
result={}
spam=importlib.import_module('src.ops.known_spam_wallets');spam.ensure_schema(ops);spam.seed_known_spam_wallets(ops);quality=importlib.import_module('src.ops.wallet_quality');quality.ensure_schema(ops);ops.commit();sender=next(iter(spam._SEED_WALLETS));mint,c,a,_=admit('excluded reviewed spam v4');fund=sig('excluded spam funding')
def excluded(method,params):
 if method=='getSignaturesForAddress':return [{'signature':fund,'slot':990,'err':None}]
 if method=='getTransaction':return tx(sender,c,fund,990,2122039000,True)
 if method=='getAccountInfo':raise AssertionError('EXCLUDED_PARENT_MUST_NOT_BE_FOLLOWED')
 raise AssertionError(method)
ww._rpc=excluded;assert ww._mark_running(ops,mint);ww._process_row(ops,ops.execute('select * from wt_walkback_queue where mint=?',(mint,)).fetchone());st=state(mint);assert st['memberships']==0 and st['queue']['intelligence_outcome']=='NO_ATTRIBUTION_FOUND';result['spam_exclusion']={'status':'PASS','state':st,'quality':quality.get_wallet_quality(ops,c),'registry':'exact installed static seed;no inferred spam or production mutation'}
# Real transport adapter exception normalization, no outbound request.
mint,c,a,_=admit('real adapter failure v4');rpc=ww.__dict__['_rpc'];tree=ast.parse((W/'src/core/walkback_worker.py').read_text());ns=ww.__dict__;exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_rpc'],type_ignores=[]),'installed-rpc','exec'),ns)
with patch('urllib.request.urlopen',side_effect=OSError('SYNTHETIC_PROVIDER_OUTAGE')):
 assert ww._mark_running(ops,mint);ww._process_row(ops,ops.execute('select * from wt_walkback_queue where mint=?',(mint,)).fetchone())
st=state(mint);assert st['queue']['status']=='complete' and st['outcome']['outcome_type']=='INSUFFICIENT_EVIDENCE';result['real_rpc_adapter_outage']={'status':'HOLD_AUTOMATIC_RETRY_NOT_DEMONSTRATED','state':st,'reason':'Installed _rpc swallows transport exception to None; signature helper maps None to empty history; worker commits complete NO_ATTRIBUTION_FOUND rather than pending retry. should_retry remains outcome metadata.'}
# Existing Core->OPS accepted CREATE handoff, with actual commit/ACK boundaries.
name='src.ops.walkback_create_handoff';spec=importlib.util.spec_from_file_location(name,P/'src/ops/walkback_create_handoff.py');handoff=importlib.util.module_from_spec(spec);sys.modules[name]=handoff;spec.loader.exec_module(handoff);mint,c,a,_=admit('handoff delivery v4');cp=str(O/'core.sqlite');op=str(O/'ops.sqlite')
with managed(cp) as co:
 co.execute('update token_analysis set migrated_at=? where mint=?',(NOW,mint));co.execute('update wb_create_control set enabled=1');staged=handoff.stage_birth_completion(co,mint,c,a,core_path=cp,ops_path=op,birth_evidence_source='PUMPPORTAL_NEW_TOKEN')
def writer(path,action):
 with managed(path) as co:return action(co)
args={'read_core':lambda:managed(cp),'write_core':lambda action:writer(cp,action),'write_ops':lambda action:writer(op,action),'core_path':cp,'ops_path':op}
def fault(point):
 if point=='after_ops_commit':raise RuntimeError('SYNTHETIC_COMMIT_ACK_GAP')
dispatch_now=max(NOW,int(time.time()))+1;first=handoff.dispatch(**args,now=dispatch_now,fault=fault);second=handoff.dispatch(**args,now=dispatch_now+100)
with managed(cp) as co:delivery=dict(co.execute('select * from wb_create_delivery where mint=?',(mint,)).fetchone())
assert delivery['state']=='ACK';result['create_handoff']={'status':'PASS','stage':staged,'first':first,'restart':second,'state':delivery['state'],'tries':delivery['tries']}
# Conflicting creator stays in Core quarantine; authoritative mint remains untouched.
mint,c,a=M,C,CREATE
with managed(cp) as co:co.execute('update token_analysis set migrated_at=? where mint=?',(NOW,mint))
for t,ddl in json.loads(Path('/private/tmp/dev004-birth-queue-ddls.json').read_text()).items():
 with managed(cp) as co:
  if not co.execute('select 1 from sqlite_master where name=?',(t,)).fetchone():co.execute(ddl)
name='src.core.birth_retry_quarantine';spec=importlib.util.spec_from_file_location(name,L/'src/core/birth_retry_quarantine.py');quarantine=importlib.util.module_from_spec(spec);sys.modules[name]=quarantine;spec.loader.exec_module(quarantine);bad=sig('conflicting admission v4');payload=json.dumps({'signature':bad,'_birth_retry_schema':1,'provenance':'PUMPPORTAL_NEW_TOKEN','mint':mint,'traderPublicKey':wallet('other creator')},sort_keys=True)
with managed(cp) as co:
 quarantine.ensure_schema(co);co.execute("insert into webhook_birth_queue(signature,payload,source,consumed) values(?,?,'pumpportal_retry',0)",(bad,payload));rid=co.execute('select id from webhook_birth_queue where signature=?',(bad,)).fetchone()[0]
for _ in range(2):
 with managed(cp) as co,managed(op) as oo:
  co.execute('BEGIN IMMEDIATE');assert quarantine.quarantine(co,oo,rid,bad,payload,json.loads(payload))
with managed(cp) as co:
 record=dict(co.execute('select quarantine_reason,consumed from webhook_birth_queue where id=?',(rid,)).fetchone());authority=co.execute('select pf_ws_creator,create_tx_signature from token_analysis where mint=?',(mint,)).fetchone()
assert record['quarantine_reason']=='CORE_AUTHORITY_CONFLICT' and tuple(authority)==(c,a);result['quarantine']={'status':'PASS','record':record,'repeat_idempotent':True,'authority_unchanged':True}
(O/'exclusion_handoff.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v['status'] for k,v in result.items()},indent=2))
