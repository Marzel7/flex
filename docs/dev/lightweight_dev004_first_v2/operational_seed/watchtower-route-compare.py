import ast,sqlite3,json,time
from pathlib import Path
from typing import Any
O=Path('/private/tmp/dev004-operational-seed-qualified');p=Path('/Users/kevinkeaveney/Production/flex-walkback-dev002/src/core/watchtower_registry_promotion.py');ns={'sqlite3':sqlite3,'Any':Any};nodes=[x for x in ast.parse(p.read_text()).body if isinstance(x,ast.FunctionDef) and x.name in {'_table_exists','_has_role_distinguishing_direct_edge','validate_role_normalized_queue_funding_route'}];exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),str(p),'exec'),ns)
cs={'v1':sqlite3.connect('file:/Users/kevinkeaveney/Dev/claude/flex/database/wt_ops_v2.db?mode=ro',uri=True),'v2':sqlite3.connect('file:'+str(O/'ops_v2.sqlite')+'?mode=ro',uri=True)}
for c in cs.values():c.row_factory=sqlite3.Row
mints=[x[0] for x in cs['v1'].execute('select mint from operator_launch_membership where operator_id=? limit 1501',('04265d9f-6eb2-568c-a49e-9253091a4dbb',))];assert len(mints)<1501;out=[]
for mint in mints:
 results={}
 for v,c in cs.items():
  deadline=time.monotonic()+.2;c.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
  try:results[v]=ns['validate_role_normalized_queue_funding_route'](c,mint)
  except sqlite3.Error as e:results[v]={'error':str(e)}
  finally:c.set_progress_handler(None,0)
 out.append({'mint':mint,'equal':results['v1']==results['v2'],**results})
(O/'watchtower_route_results.json').write_text(json.dumps(out,indent=2)+'\n');print('Watchtower routes',len(out),'matches',sum(x['equal'] for x in out))
