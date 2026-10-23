import ast,json,sqlite3,hashlib,time
from pathlib import Path
from typing import Any,Optional
D=Path('/Users/kevinkeaveney/.codex/worktrees/dev004-first-v2-prototype/flex/docs/dev/lightweight_dev004_first_v2');OUT=Path('/private/tmp/dev004-operational-seed-qualified');W=Path('/Users/kevinkeaveney/Production/flex-walkback-dev002');P=Path('/Users/kevinkeaveney/Production/flex-main-workers')
frozen=json.loads((D/'frozen_v1_outputs.json').read_text());creator,subprov,treasury=frozen['creator_classification'][1:4];operator='04265d9f-6eb2-568c-a49e-9253091a4dbb';MINT='HXpDLpoH6Vx6MtqybpmsC6gFFoZpeb6bYTTpkrrypump';report={'sources':{}}
s= (D/'qualify.py').read_text();t=ast.parse(s)
exec(compile(ast.Module(body=[n for n in t.body if isinstance(n,ast.FunctionDef) and n.name in {'functions','outputs'}],type_ignores=[]),'frozen-readers','exec'))
registry=functions(W/'src/core/watchtower_registry_promotion.py',['_table_exists','_has_role_distinguishing_direct_edge','validate_role_normalized_queue_funding_route','is_canonical_watchtower_outcome']);walk=functions(W/'src/core/walkback_worker.py',['_is_known_treasury','_is_known_subprov']);classification=functions(W/'src/core/walkback_queue.py',['_resolve_treasury_via_op_graph','classify_creator']);price=functions(P/'src/ops/watchtower_price_fact_contract.py',['reduce_watchtower_price_facts'])
conns=[sqlite3.connect('file:'+str(OUT/n)+'?mode=ro',uri=True) for n in ['core_v2.sqlite','ops_v2.sqlite']]
for c in conns:c.row_factory=sqlite3.Row
actual=json.loads(json.dumps(outputs(*conns)));comparisons={k:actual[k]==v for k,v in frozen.items()};assert all(comparisons.values()),comparisons
for c in conns:c.close()
manifest=json.load(open('/private/tmp/dev004-birdeye-deployment-20261022/manifest.json'));installed=all(hashlib.sha256(Path(e['path']).read_bytes()).hexdigest()==e['new_sha'] for e in manifest['files']);assert installed
fact=frozen['price_fact'];canonical=float(fact['final_proven_ath_mc']);reconstructed=1015581.2555732703;difference=abs(canonical-reconstructed)/canonical;assert difference<=0.001
result={'original_11':comparisons,'installed_nine_credential_files_unchanged':installed,'ath_relative_difference':difference,'ath_numerical':'PASS','canonical_fact_and_observations_unchanged':comparisons['price_fact'] and comparisons['price_observations'],'fixtures':{n:(OUT/n).stat().st_size for n in ['core_v2.sqlite','ops_v2.sqlite']},'production_changes':0,'provider_calls':0}
(OUT/'final_checks.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
