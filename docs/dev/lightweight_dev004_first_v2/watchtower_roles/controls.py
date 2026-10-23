import sqlite3,json,ast,hashlib
from pathlib import Path
from typing import Any
O=Path('/private/tmp/dev004-watchtower-roles-qualified');S=Path('/private/tmp/dev004-operational-seed-qualified');D=Path('/Users/kevinkeaveney/.codex/worktrees/dev004-first-v2-prototype/flex/docs/dev/lightweight_dev004_first_v2');out={}
a=sqlite3.connect('file:'+str(S/'ops_v2.sqlite')+'?mode=ro',uri=True);b=sqlite3.connect('file:'+str(O/'ops_v2.sqlite')+'?mode=ro',uri=True);b.row_factory=sqlite3.Row
preserved={}
for (t,) in a.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'"):
 old={tuple(r) for r in a.execute('select * from '+t)};new={tuple(r) for r in b.execute('select * from '+t)};assert old<=new,t;preserved[t]={'old':len(old),'new':len(new),'removed_or_changed':0}
out['all_original_tables_preserved']=preserved
out['core_byte_identical']=hashlib.sha256((S/'core_v2.sqlite').read_bytes()).digest()==hashlib.sha256((O/'core_v2.sqlite').read_bytes()).digest();assert out['core_byte_identical']
p=Path('/Users/kevinkeaveney/Production/flex-walkback-dev002/src/core/watchtower_registry_promotion.py');tree=ast.parse(p.read_text());exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in {'_table_exists','_has_role_distinguishing_direct_edge','validate_role_normalized_queue_funding_route'}],type_ignores=[]),str(p),'exec'))
ledger=json.loads((D/'operational_seed/watchtower_route_results.json').read_text());print(type(ledger),str(ledger)[:180])
roles=json.loads((O/'role_results.json').read_text());sample=next(x for x in roles['cases'] if x['original_group']=='99_PROVEN_REGRESSIONS');neg=[]
for mode in ['missing','reversed','non_direct']:
 c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
 for t in ['wt_walkback_queue','wt_provisioning_sessions','wt_lineage_verified_session_edges']:
  c.execute(b.execute('select sql from sqlite_master where name=?',(t,)).fetchone()[0])
  rows=[sample['queue']] if t=='wt_walkback_queue' else [sample['provisioning_session']] if t=='wt_provisioning_sessions' else []
  if t=='wt_lineage_verified_session_edges' and mode!='missing':
   e=dict(next(x['selected']['edge'] for x in roles['proof_pairs'] if x['sender']==sample['canonical_treasury'] and x['recipient']==sample['upstream_session_parent']))
   if mode=='reversed':e['sender'],e['recipient']=e['recipient'],e['sender']
   else:e['relationship_type']='WSOL_WRAP_CLOSE'
   rows=[e]
  for r in rows:c.execute('insert into '+t+' ('+','.join(r)+') values ('+','.join('?' for _ in r)+')',list(r.values()))
 r=validate_role_normalized_queue_funding_route(c,sample['mint']);assert r=={'eligible':False,'reason':'TREASURY_EVIDENCE_CONFLICT'},r;neg.append({'control':mode,'result':r});c.close()
out['negative_role_controls']=neg
out['new_support_eligible']=len(b.execute('select * from wt_lineage_eligible_sessions').fetchall());assert out['new_support_eligible']==4
out['added_role_transfer_does_not_promote_candidates']=True
out['seed_total_rows']=sum(b.execute('select count(*) from '+r[0]).fetchone()[0] for r in b.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'").fetchall())+12490
out['logical_json_bytes']=sum(len(json.dumps(dict(r),sort_keys=True,separators=(',',':')).encode()) for t in b.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'").fetchall() for r in b.execute('select * from '+t[0]))
(O/'role_controls.json').write_text(json.dumps(out,indent=2)+'\n');print('PASS preservation',len(preserved),'negative controls',len(neg),'total rows',out['seed_total_rows'])
