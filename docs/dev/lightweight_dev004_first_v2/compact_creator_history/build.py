import sqlite3,json,time,ast,hashlib,resource
from pathlib import Path
resource.setrlimit(resource.RLIMIT_FSIZE,(8000000,8000000))
D=Path('/Users/kevinkeaveney/.codex/worktrees/dev004-first-v2-prototype/flex');O=Path('/private/tmp/dev004-compact-creator-qualified');O.mkdir(exist_ok=True);P=Path('/Users/kevinkeaveney/Production/flex-walkback-dev002/src/ops/attribution_outcome.py');ns={'Any':object};tree=ast.parse(P.read_text());exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_normalize_timestamp'],type_ignores=[]),str(P),'exec'),ns)
c=sqlite3.connect('file:/Users/kevinkeaveney/Dev/claude/flex/database/flex_complete_database.db?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');columns=[x[1] for x in c.execute('pragma table_info(token_analysis)')];times=[x for x in ['first_observed_at','analyzed_at','created_at','block_time'] if x in columns];manifest={'contract':'dev004-compact-creator-history.v1','source':str(Path('/Users/kevinkeaveney/Dev/claude/flex/database/flex_complete_database.db')),'reader_sha256':hashlib.sha256(P.read_bytes()).hexdigest(),'timestamp_columns':times,'creators':{}};errors=[];plans={}
def read(sql,args):
 plan=[r[3] for r in c.execute('explain query plan '+sql,args)];assert all('SCAN ' not in p for p in plan),(sql,plan);plans[sql]=plan;deadline=time.monotonic()+.5;c.set_progress_handler(lambda:int(time.monotonic()>deadline),1000);return c.execute(sql,args).fetchall()
for item in json.loads((D/'docs/dev/lightweight_dev004_first_v2/operational_seed/creator_launch_history_gaps.json').read_text()):
 w=item['creator'];c.execute('begin')
 try:
  floor=read('select rowid from token_analysis where pf_ws_creator=? order by rowid desc limit 1',(w,));floor=floor[0][0] if floor else 0;cursor=0;ids={};started=int(time.time())
  while cursor<floor:
   rows=read('select rowid,mint,'+','.join(times)+' from token_analysis where pf_ws_creator=? and rowid>? and rowid<=? order by rowid limit 128',(w,cursor,floor))
   if not rows:break
   for r in rows:
    ts=None
    for k in times:
     if r[k] is not None:ts=ns['_normalize_timestamp'](r[k]);break
    ids[r['mint']]=ts
   cursor=rows[-1][0];assert len(ids)<=10000
  count=read('select count(*) from token_analysis where pf_ws_creator=? and rowid<=?',(w,floor))[0][0];assert len(ids)==count
  valid=[x for x in ids.values() if x is not None];manifest['creators'][w]={'cutoff_rowid':floor,'verified_at':started,'launch_count':count,'valid_count':len(valid),'first_seen':min(valid) if len(valid)>=2 else None,'last_seen':max(valid) if len(valid)>=2 else None,'historical_mints':ids}
 except (sqlite3.Error,AssertionError) as e:errors.append({'creator':w,'error':str(e)})
 finally:c.set_progress_handler(None,0);c.rollback()
(O/'history.json').write_text(json.dumps(manifest,sort_keys=True,separators=(',',':'))+'\n');(O/'build.json').write_text(json.dumps({'verified_creators':len(manifest['creators']),'errors':errors,'plans':plans,'history_bytes':(O/'history.json').stat().st_size,'launch_identities':sum(x['launch_count'] for x in manifest['creators'].values())},indent=2)+'\n');print((O/'build.json').read_text())
