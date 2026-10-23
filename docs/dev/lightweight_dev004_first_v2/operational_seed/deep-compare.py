import importlib.util,sqlite3,json,time
from pathlib import Path
O=Path('/private/tmp/dev004-operational-seed-qualified');p=Path('/Users/kevinkeaveney/Production/flex-walkback-dev002/src/ops/watchtower_deep_prospective.py');spec=importlib.util.spec_from_file_location('deep',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);cs={'v1':sqlite3.connect('file:/Users/kevinkeaveney/Dev/claude/flex/database/wt_ops_v2.db?mode=ro',uri=True),'v2':sqlite3.connect('file:'+str(O/'ops_v2.sqlite')+'?mode=ro',uri=True)};out=[]
for row in json.load(open(O/'deep_required_records.json')):
 result={}
 for v,c in cs.items():
  deadline=time.monotonic()+.3;c.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
  try:result[v]=m.assess_prospective_route(c,row['mint'],diagnostic_ignore_ownership=True,require_index=True)
  except sqlite3.Error as e:result[v]={'state':'UNQUALIFIED_BOUNDED_READ','reason':str(e)}
  finally:c.set_progress_handler(None,0)
 out.append({'mint':row['mint'],'equal':result['v1']==result['v2'],**result})
(O/'deep_behaviour_results.json').write_text(json.dumps(out,indent=2)+'\n');print('Deep diagnostic matches',sum(x['equal'] for x in out),'/',len(out));assert all(x['equal'] for x in out)
