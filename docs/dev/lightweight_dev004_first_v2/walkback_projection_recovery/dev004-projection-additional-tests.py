# Reuse isolated case utilities without rerunning or overwriting prior tests.
from pathlib import Path
s=Path('/private/tmp/dev004-projection-recovery-tests.py').read_text();exec(compile(s.split('# Source counterexample')[0].replace('O.mkdir(exist_ok=False)','O.mkdir(exist_ok=True)'),'test-loader','exec'))
# True installed _ops_conn adapter against the disposable database.
root=fixture('tracked_adapter');before=facts(root);r=run(root,phase='tracked');assert r['result']['status']=='PASS' and len(r['membership'])==1 and facts(root)==before;results.append({'name':'actual_worker_connection_adapter','status':'PASS'})
# Correct membership must not be rewritten when selected again.
c=sqlite3.connect(root/'ops.sqlite');rowid=c.execute('select rowid from wt_walkback_queue where mint=?',(mint,)).fetchone()[0];c.execute('update dev004_walkback_projection_cursor set last_rowid=? where id=1',(rowid-1,));c.commit();c.close();member=r['membership'];outbox=r['outbox'];r=run(root);assert r['membership']==member and r['outbox']==outbox and any(x['action']=='ALREADY_PRESENT' for x in r['result']['results']);results.append({'name':'correct_existing_membership_is_noop','status':'PASS'})
root=fixture('different_membership');c=sqlite3.connect(root/'ops.sqlite');other=c.execute("select operator_id from operators where display_name!='WATCHTOWER' limit 1").fetchone()[0];c.execute("insert into operator_launch_membership(mint,operator_id,source_population_id,assigned_at,event_id) values(?,?,'SYNTHETIC_CONFLICT',1,'SYNTHETIC_OTHER_MEMBER')",(mint,other));c.commit();c.close();before=facts(root);r=run(root);assert len(r['membership'])==1 and r['membership'][0]['operator_id']==other and not r['outbox'] and facts(root)==before;assert any(x['action']=='EXISTING_OTHER_OPERATOR' for x in r['result']['results']);results.append({'name':'different_membership_never_moved','status':'PASS'})
# Starting at0, every invocation/restart remains bounded, and the cursor advances.
root=fixture('from_start');c=sqlite3.connect(root/'ops.sqlite');c.execute('create table dev004_walkback_projection_cursor(id integer primary key check(id=1),last_rowid integer not null check(last_rowid>=0))');c.execute('insert into dev004_walkback_projection_cursor values(1,0)');c.commit();c.close();before=facts(root);pages=[]
for i in range(32):
 r=run(root);assert r['result']['status']=='PASS' and r['result']['examined']<=32;pages.append(r['result']['examined'])
 if r['membership']:break
assert len(r['membership'])==1 and facts(root)==before;results.append({'name':'durable_indexed_paging_from_zero','status':'PASS','invocations':len(pages),'rows_per_invocation':pages})
(O/'additional.json').write_text(json.dumps({'status':'PASS','tests':results},indent=2)+'\n');print('PASS',len(results),'additional groups',len(pages),'bounded pages')
