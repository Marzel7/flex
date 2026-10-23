import sqlite3,json,time
from pathlib import Path
O=Path('/private/tmp/dev004-operational-seed-qualified');sources={db:sqlite3.connect('file:/Users/kevinkeaveney/Dev/claude/flex/database/'+f+'?mode=ro',uri=True) for db,f in [('ops','wt_ops_v2.db'),('core','flex_complete_database.db')]};d={db:sqlite3.connect(O/(db+'_v2.sqlite')) for db in sources};r={'added':{},'unresolved':[],'encoded_bytes_added':0,'record_keys':[]};budget=0
creators=[x[0] for x in d['ops'].execute('select distinct q.creator from operator_launch_membership m join wt_walkback_queue q using(mint) where q.creator is not null')]
for db,t,column in [('ops','wt_wrap_close_candidates','creator'),('ops','wt_creator_birth_launch','creator'),('ops','wt_candidate_websocket_watches','candidate_wallet'),('core','creator_funders','creator_address')]:
 s=sources[db];s.row_factory=sqlite3.Row;ddl=s.execute('select sql from sqlite_master where name=?',(t,)).fetchone()
 if not ddl:continue
 if not d[db].execute('select 1 from sqlite_master where name=?',(t,)).fetchone():d[db].execute(ddl[0])
 for creator in creators:
  sql='select * from '+t+' where '+column+'=? limit 258';plan=[x[3] for x in s.execute('explain query plan '+sql,(creator,))]
  if any(x.startswith('SCAN ') for x in plan):r['unresolved'].append({'table':t,'creator':creator,'reason':'UNINDEXED_REQUIRED_LOOKUP'});continue
  deadline=time.monotonic()+.2;s.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
  try:rows=s.execute(sql,(creator,)).fetchall()
  except sqlite3.Error:r['unresolved'].append({'table':t,'creator':creator,'reason':'BOUNDED_READ_INTERRUPTED'});continue
  finally:s.set_progress_handler(None,0)
  if len(rows)==258:r['unresolved'].append({'table':t,'creator':creator,'reason':'MORE_THAN_257_REQUIRED_HISTORY_ROWS'});continue
  for row in rows:
   size=len(json.dumps(dict(row),default=str));budget+=size
   if budget>4000000 or size>262144:r['unresolved'].append({'table':t,'creator':creator,'reason':'PAYLOAD_BUDGET'});break
   before=d[db].total_changes;d[db].execute('insert or ignore into '+t+' values('+','.join('?'*len(row))+')',tuple(row));added=d[db].total_changes-before;r['added'][db+'.'+t]=r['added'].get(db+'.'+t,0)+added
   if added:
    r['encoded_bytes_added']+=size;r['record_keys'].append({'database':db,'table':t,'key':{x[1]:row[x[1]] for x in s.execute('pragma table_info('+t+')') if x[5]}})
for c in d.values():c.commit();c.close()
for c in sources.values():c.close()
(O/'creator_dependency_extension.json').write_text(json.dumps(r,indent=2)+'\n');print('added',r['added'],'unresolved',len(r['unresolved']),'bytes',r['encoded_bytes_added'])
