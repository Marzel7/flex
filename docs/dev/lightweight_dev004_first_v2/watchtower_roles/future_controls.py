import sqlite3,ast,json
from pathlib import Path
from typing import Any
O=Path('/private/tmp/dev004-watchtower-roles-qualified');p=Path('/Users/kevinkeaveney/Production/flex-walkback-dev002/src/core/watchtower_registry_promotion.py');t=ast.parse(p.read_text());exec(compile(ast.Module(body=[n for n in t.body if isinstance(n,ast.FunctionDef) and n.name in {'_table_exists','_has_role_distinguishing_direct_edge','validate_role_normalized_queue_funding_route'}],type_ignores=[]),str(p),'exec'));r=json.loads((O/'role_results.json').read_text());out=[]
for i,pair in enumerate([x for x in r['proof_pairs'] if x['selected']]):
 sample=next(x for x in r['cases'] if x['canonical_treasury']==pair['sender'] and x['upstream_session_parent']==pair['recipient']);a=sqlite3.connect('file:'+str(O/'ops_v2.sqlite')+'?mode=ro',uri=True);b=sqlite3.connect(':memory:');b.row_factory=sqlite3.Row;a.backup(b);a.close();mint='DEV004_ROLE_FUTURE_CONTROL_'+str(i)
 for table,key in [('wt_walkback_queue','queue'),('wt_provisioning_sessions','provisioning_session')]:
  row=dict(sample[key]);row['mint' if key=='queue' else 'source_mint']=mint
  if key!='queue':row['session_id']='DEV004_ROLE_FUTURE_SESSION_'+str(i)
  b.execute('insert into '+table+' ('+','.join(row)+') values ('+','.join('?' for _ in row)+')',list(row.values()))
 actual=validate_role_normalized_queue_funding_route(b,mint);assert actual['eligible'];out.append({'synthetic_mint':mint,'existing_role_session':pair['selected']['edge']['session_id'],'result':actual,'scope':'known-pattern role admission only; no new full behavioural or funding investigation claim'});b.close()
(O/'future_role_controls.json').write_text(json.dumps(out,indent=2)+'\n');print('Future known-pattern role controls',len(out),'PASS')
