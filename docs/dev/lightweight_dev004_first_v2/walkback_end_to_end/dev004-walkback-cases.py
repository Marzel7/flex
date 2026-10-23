s=open('/private/tmp/dev004-walkback-complete.py').read();exec(compile(s.split('asyncio.run(')[0],'installed-loader','exec'))
# Load the exact synthetic transaction constructor from the successful case.
tree=ast.parse(s);exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='tx'],type_ignores=[]),'synthetic-responses','exec'))
from unittest.mock import patch
results=[]
def admit(label,anchor=True):
 mint=wallet('DEV004 investigation '+label);creator=wallet('DEV004 creator '+label);signature=sig('DEV004 anchor '+label)
 asyncio.run(listener._handle_pumpportal_create_frame({'mint':mint,'signature':signature,'traderPublicKey':creator,'vSolInBondingCurve':1,'marketCapSol':30,'bondingCurveKey':wallet(label+' curve'),'symbol':'DEV004'},signature=signature,mint=mint,receive_utc_ns=time.time_ns(),receive_monotonic_ns=time.monotonic_ns(),tracked_trade_mints=set(),migration_sol_threshold=85,ws=types.SimpleNamespace(send=noop)))
 core=connect(O/'core.sqlite');core.row_factory=sqlite3.Row
 if not anchor:
  # Explicit missing-authority synthetic control; do not mutate an accepted birth.
  mint=wallet('DEV004 no birth '+label);signature=None
 cls=q.enqueue_migration(ops,mint=mint,creator=creator,live_conn=core,create_signature=signature,create_source='PUMPPORTAL_NEW_TOKEN' if anchor else None,create_block_time=NOW if anchor else None);ops.commit();core.close();return mint,creator,signature,cls

def state(mint):
 c=connect(O/'ops.sqlite');c.row_factory=sqlite3.Row;r={'queue':dict(c.execute('select * from wt_walkback_queue where mint=?',(mint,)).fetchone()),'outcome':None,'memberships':c.execute('select count(*) from operator_launch_membership where mint=?',(mint,)).fetchone()[0]};v=c.execute('select * from wt_attribution_outcomes where mint=?',(mint,)).fetchone();r['outcome']=dict(v) if v else None;c.close();return r

def run(label,mode):
 mint,c,anchor,cls=admit(label);account=wallet(label+' account');fund=sig(label+' funding');alt=sig(label+' alternative');calls=[]
 def transport(method,params):
  calls.append((method,params));assert len(calls)<=32
  if mode=='raised_failure':raise RuntimeError('SYNTHETIC_TRANSPORT_FAILURE')
  if mode=='none_failure':return None
  if method=='getSignaturesForAddress':return [{'signature':fund,'slot':990,'err':None}] if mode in ['gap','conflict'] and params[0]==c else []
  if method=='getTransaction':return tx(account,c,fund,990,2122039000,True)
  if method=='getAccountInfo':return {'value':{'owner':ww._SYSTEM_PROGRAM}}
  raise AssertionError(method)
 if mode=='conflict':
  changed=tx(account,c,fund,990,2122039000,True);changed['transaction']['message']['accountKeys'][0]['signer']=False;ww.deep_walkback.persist_decoded_transaction_roles(ops,mint=mint,signature=fund,anchor_signature=anchor,tx=changed);ops.commit()
 ww._rpc=transport;assert ww._mark_running(ops,mint);ww._process_row(ops,ops.execute('select * from wt_walkback_queue where mint=?',(mint,)).fetchone());result=state(mint)
 if mode in ['empty','none_failure']:assert result['queue']['intelligence_outcome']=='NO_ATTRIBUTION_FOUND' and result['outcome']['outcome_type']=='INSUFFICIENT_EVIDENCE' and result['memberships']==0
 if mode=='gap':assert result['queue']['intelligence_outcome']=='LINEAGE_GAP' and result['memberships']==0
 if mode in ['raised_failure','conflict']:assert result['queue']['status']=='pending' and not result['outcome'] and not result['memberships']
 results.append({'name':label,'mode':mode,'state':result,'calls':calls});return mint,c,anchor
run('no attribution','empty');run('unknown disposable lineage','gap');failed,_,_=run('provider raised retry','raised_failure');ww._rpc=lambda *a:[];assert ww._mark_running(ops,failed);ww._process_row(ops,ops.execute('select * from wt_walkback_queue where mint=?',(failed,)).fetchone());assert state(failed)['queue']['attempts']==2
run('provider returns None','none_failure');run('conflicting transaction roles','conflict')
mi,cr,an,cl=admit('missing CREATE',False);assert state(mi)['queue']['status']=='waiting' and not ww._mark_running(ops,mi);results.append({'name':'missing CREATE waits without attribution','state':state(mi)})
mi,cr,an,cl=admit('duplicate migration');before=state(mi);core=connect(O/'core.sqlite');q.enqueue_migration(ops,mint=mi,creator=cr,live_conn=core,create_signature=an);ops.commit();core.close();assert state(mi)==before;results.append({'name':'duplicate migration unchanged','state':before})
assert ww._mark_running(ops,mi);assert not ww._mark_running(ops,mi);ops.execute('update wt_walkback_queue set lease_expires_at=0 where mint=?',(mi,));ops.commit();assert ww._mark_running(ops,mi);assert state(mi)['queue']['attempts']==2;results.append({'name':'expired lease reclaim','state':state(mi)})
(O/'negative_results.json').write_text(json.dumps(results,indent=2)+'\n');print('NEGATIVE_CONTROLS',len(results),'PASS')
