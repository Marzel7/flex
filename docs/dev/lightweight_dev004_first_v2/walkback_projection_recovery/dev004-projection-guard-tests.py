from pathlib import Path
s=Path('/private/tmp/dev004-projection-recovery-tests.py').read_text();exec(compile(s.split('# Source counterexample')[0].replace('O.mkdir(exist_ok=False)','O.mkdir(exist_ok=True)'),'test-loader','exec'))
for name,sql,args in [
 ('missing_role_proof','update wt_walkback_queue set funder_sig=NULL where mint=?',(mint,)),
 ('conflicting_route_json','update wt_attribution_outcomes set evidence_json=? where mint=?',(json.dumps({'creator':'OTHER','treasuries':[],'subprovisioners':[]}),mint)),
 ('malformed_identity',"update wt_walkback_queue set creator='invalid' where mint=?",(mint,)),
 ('oversized_canonical_evidence','update wt_attribution_outcomes set evidence_json=? where mint=?',('x'*8193,mint))]:
 root=fixture(name);c=sqlite3.connect(root/'ops.sqlite');c.execute(sql,args);c.commit();c.close();before=facts(root);r=run(root);assert not r['membership'] and not r['outbox'] and facts(root)==before;results.append({'name':name,'status':'PASS','result':r['result']})
for phase in ['vm_budget','database_budget','outbox_capacity']:
 root=fixture(phase);before=facts(root);r=run(root,phase=phase);assert r['result']['status']=='HOLD' and not r['membership'] and not r['outbox'] and facts(root)==before
 repaired=run(root);assert len(repaired['membership'])==len(repaired['outbox'])==1 and facts(root)==before;results.append({'name':phase,'status':'PASS','hold':r['result'],'retry_repaired':True})
(O/'guards.json').write_text(json.dumps({'status':'PASS','tests':results},indent=2)+'\n');print('PASS',len(results),'guard/rollback groups')
