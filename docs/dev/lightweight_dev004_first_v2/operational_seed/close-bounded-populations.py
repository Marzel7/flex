import sqlite3,json,time
from pathlib import Path
O=Path('/private/tmp/dev004-operational-seed-qualified');out={'added':{},'holds':[],'keys':[]}
for db,file in [('ops','wt_ops_v2.db'),('core','flex_complete_database.db')]:
 s=sqlite3.connect('file:/Users/kevinkeaveney/Dev/claude/flex/database/'+file+'?mode=ro',uri=True);s.row_factory=sqlite3.Row;d=sqlite3.connect(O/(db+'_v2.sqlite'));d.execute('pragma max_page_count=10000')
 if db=='ops':selections=[('p3r_v2_candidate_membership','run_id=? and candidate_id=?',(x['run_id'],x['candidate_id']),513) for x in json.load(open(O/'workflow_membership_gaps.json'))]
 else:selections=[('creator_funders','creator_address=?',(x['creator'],),1025) for x in json.load(open(O/'creator_dependency_extension.json'))['unresolved']]
 for t,where,args,cap in selections:
  deadline=time.monotonic()+.3;s.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
  try:rows=s.execute('select * from '+t+' where '+where+' limit '+str(cap),args).fetchall()
  except sqlite3.Error as e:out['holds'].append({'table':t,'args':args,'reason':str(e)});continue
  finally:s.set_progress_handler(None,0)
  if len(rows)==cap:out['holds'].append({'table':t,'args':args,'reason':'SOURCE_POPULATION_EXCEEDS_BOUND','cap':cap});continue
  for row in rows:
   assert len(json.dumps(dict(row),default=str))<262144;before=d.total_changes;d.execute('insert or ignore into '+t+' values('+','.join('?'*len(row))+')',tuple(row));added=d.total_changes-before;out['added'][db+'.'+t]=out['added'].get(db+'.'+t,0)+added
   if added:out['keys'].append({'database':db,'table':t,'key':{x[1]:row[x[1]] for x in s.execute('pragma table_info('+t+')') if x[5]}})
 d.commit();d.close();s.close()
(O/'bounded_population_extension.json').write_text(json.dumps(out,indent=2)+'\n');print('Added',out['added'],'Holds',out['holds'])
