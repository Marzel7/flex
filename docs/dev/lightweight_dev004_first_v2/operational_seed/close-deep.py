import json,sqlite3,time
from pathlib import Path
O=Path('/private/tmp/dev004-operational-seed-qualified');s=sqlite3.connect('file:/Users/kevinkeaveney/Dev/claude/flex/database/wt_ops_v2.db?mode=ro',uri=True);s.row_factory=sqlite3.Row;d=sqlite3.connect(O/'ops_v2.sqlite');out={'added':{},'required_keys':[],'holds':[]};cases=json.load(open(O/'deep_behaviour_results.json'));requirements=set()
for case in cases:
 route=case['v1'].get('route')
 if route:requirements.update([(route['distribution'],100000000000),(route['coordinator'],1000000000000)])
for dest,minimum in sorted(requirements):
 deadline=time.monotonic()+.3;s.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
 try:roles=s.execute('select * from wt_walkback_transaction_roles indexed by ix_wwtr_destination_amount where transfer_destination=? and transfer_lamports>=? limit 129',(dest,minimum)).fetchall()
 except sqlite3.Error as e:out['holds'].append({'destination':dest,'reason':str(e)});continue
 finally:s.set_progress_handler(None,0)
 if len(roles)==129:out['holds'].append({'destination':dest,'reason':'ROLE_CAP'});continue
 for role in roles:
  deadline=time.monotonic()+.3;s.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
  try:edges=s.execute('select * from wt_walkback_edge_candidates where mint=? and signature=? limit 129',(role['mint'],role['signature'])).fetchall()
  except sqlite3.Error as e:out['holds'].append({'mint':role['mint'],'signature':role['signature'],'reason':str(e)});continue
  finally:s.set_progress_handler(None,0)
  assert len(edges)<129
  for t,rows in [('wt_walkback_transaction_roles',[role]),('wt_walkback_edge_candidates',edges)]:
   for row in rows:
    assert len(json.dumps(dict(row),default=str))<262144;before=d.total_changes;d.execute('insert or ignore into '+t+' values('+','.join('?'*len(row))+')',tuple(row));out['added'][t]=out['added'].get(t,0)+d.total_changes-before
    out['required_keys'].append({'table':t,'key':{x[1]:row[x[1]] for x in s.execute('pragma table_info('+t+')') if x[5]}})
idx=s.execute("select sql from sqlite_master where name='ix_wwtr_destination_amount'").fetchone()[0];d.execute(idx.replace('CREATE INDEX ','CREATE INDEX IF NOT EXISTS ',1));d.commit();d.close();s.close();(O/'deep_dependency_extension.json').write_text(json.dumps(out,indent=2)+'\n');print('Deep added',out['added'],'holds',out['holds'])
