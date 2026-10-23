from pathlib import Path
import sqlite3,json,subprocess,os,shutil,hashlib,resource
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000));O=Path('/private/tmp/dev004-projection-recovery-final');O.mkdir(exist_ok=False);S=Path('/private/tmp/dev004-walkback-crash-qualification/after');prior=json.load(open('/private/tmp/dev004-walkback-crash-qualification/result.json'));mint=prior['cases'][1]['after_restart']['mint'];source_hashes={n:hashlib.sha256((S/n).read_bytes()).hexdigest() for n in ['core.sqlite','ops.sqlite']};results=[]
def fixture(name):
 root=O/name;root.mkdir()
 for n in ['core.sqlite','ops.sqlite']:shutil.copy2(S/n,root/n)
 (root/'target.json').write_text(json.dumps({'mint':mint}));return root

def run(root,phase='normal',expected=0,flag='1'):
 p=subprocess.run(['python3','/private/tmp/dev004-projection-recovery-child.py',str(root),phase,flag],capture_output=True,text=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},timeout=30);assert p.returncode==expected,(phase,p.stdout,p.stderr)
 return json.load(open(root/'last.json')) if expected==0 else p.returncode

def facts(root):
 c=sqlite3.connect('file:'+str(root/'ops.sqlite')+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
 out={t:[dict(r) for r in c.execute('select * from '+t+' where '+('source_mint' if t=='wt_provisioning_sessions' else 'mint')+'=?',(mint,))] for t in ['wt_walkback_queue','wt_attribution_outcomes','wt_walkback_edge_candidates','wt_walkback_transaction_roles','wt_provisioning_sessions']};c.close();return out
# Source counterexample remains immutable: complete/outcome durable, member absent.
c=sqlite3.connect('file:'+str(S/'ops.sqlite')+'?mode=ro',uri=True);assert c.execute('select count(*) from operator_launch_membership where mint=?',(mint,)).fetchone()[0]==0;c.close()
root=fixture('normal');before=facts(root);r=run(root);assert len(r['membership'])==len(r['outbox'])==1 and r['result']['examined']<=32;assert facts(root)==before;results.append({'name':'original_postcanonical_crash_repaired','status':'PASS','evidence':r['result']})
member=r['membership'];outbox=r['outbox']
for _ in range(4):
 r=run(root);assert r['membership']==member and r['outbox']==outbox and facts(root)==before
results.append({'name':'repeated_actual_worker_process_restart','status':'PASS','memberships':1,'outbox':1})
for phase,code in [('before_membership_commit',81),('after_membership_commit',82),('after_ack',83)]:
 root=fixture(phase);before=facts(root);run(root,phase,code);c=sqlite3.connect(root/'ops.sqlite');count=c.execute('select count(*) from operator_launch_membership where mint=?',(mint,)).fetchone()[0];c.close();assert count==(0 if phase=='before_membership_commit' else 1)
 r=run(root);assert len(r['membership'])==len(r['outbox'])==1 and facts(root)==before;results.append({'name':phase,'status':'PASS','actual_exit':code,'membership_at_crash':count,'membership_after_restart':len(r['membership'])})
# Canonical authority is independently mandatory; never create speculative members.
mutations={
 'missing_canonical':('update wt_attribution_outcomes set mint=? where mint=?',('SYNTHETIC_ORPHAN_CANONICAL',mint)),
 'conflicting_operator':("update wt_attribution_outcomes set operator_id='SYNTHETIC_OTHER_OPERATOR' where mint=?",(mint,)),
 'conflicting_terminal':("update wt_attribution_outcomes set terminal_entity='SYNTHETIC_OTHER_TERMINAL' where mint=?",(mint,)),
 'insufficient_evidence':("update wt_attribution_outcomes set outcome_type='INSUFFICIENT_EVIDENCE',confidence='INSUFFICIENT',should_retry=1 where mint=?",(mint,)),
 'quarantined_queue':("update wt_walkback_queue set status='quarantined' where mint=?",(mint,)),
 'stale_canonical_version':('update wt_walkback_queue set updated_at=updated_at+1 where mint=?',(mint,)),
 'missing_CREATE':("update wt_walkback_queue set create_anchor_audit_state='MISSING_OR_MALFORMED' where mint=?",(mint,)),
}
for name,(sql,args) in mutations.items():
 root=fixture(name);c=sqlite3.connect(root/'ops.sqlite');c.execute(sql,args);c.commit();c.close();before=facts(root);r=run(root);assert not r['membership'] and not r['outbox'] and facts(root)==before;results.append({'name':name,'status':'PASS','evidence':r['result']})
root=fixture('flag_off');before=facts(root);r=run(root,flag='0');assert r['result']['status']=='DISABLED' and not r['membership'] and facts(root)==before;results.append({'name':'flag_off_compatibility','status':'PASS'})
assert all(hashlib.sha256((S/n).read_bytes()).hexdigest()==h for n,h in source_hashes.items());result={'status':'PASS','tests':results,'original_counterexample_unchanged':source_hashes,'max_file_bytes':max(p.stat().st_size for p in O.rglob('*') if p.is_file()),'real_provider_calls':0};(O/'result.json').write_text(json.dumps(result,indent=2)+'\n');print('PASS',len(results),'test groups;max file',result['max_file_bytes'])
