from pathlib import Path
s=Path('/private/tmp/dev004-test-compact-history.py').read_text().split("for w in manifest['creators']:")[0]
# No live V1 oracle reads: use accepted frozen100 outputs.
a=s.index('src={');b=s.index('\nhelper=',a);s=s[:a]+"src={};v2={k:conn('/private/tmp/dev004-provider-outage-normal/'+k+'.sqlite') for k in ['core','ops']}"+s[b:]
exec(compile(s,'compact-loader','exec'))
NOW=1792747000
baseline=json.load(open(O/'classification_closure/result.json'))['comparisons'];profiles={r['creator']:r['v1_profile'] for r in json.load(open(O/'equivalence.json'))['cases']}
for r in baseline:
 assert classify(dev,v2,r['creator'],True)==r['v1_class'],r['creator']
# Installed role reader against same fresh normal fixture and frozen routes.
names={'_table_exists','_has_role_distinguishing_direct_edge','validate_role_normalized_queue_funding_route'};ns={'sqlite3':sqlite3,'Any':Any}
p=D/'runtime/walkback/src/core/watchtower_registry_promotion.py';tree=ast.parse(p.read_text());nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names];exec(compile(ast.Module(body=nodes,type_ignores=[]),str(p),'exec'),ns)
rows=json.load(open(D/'docs/dev/lightweight_dev004_first_v2/watchtower_roles/all_621_routes.json'));count=0
for r in rows:
 if r['exact_equal']:
  assert ns['validate_role_normalized_queue_funding_route'](v2['ops'],r['mint'])==r['frozen_v1'],r['mint'];count+=1
assert count==616
out={'status':'PASS','creator_classifications':len(baseline),'watchtower_routes':count}
Path('/private/tmp/dev004-provider-outage-qualified/classification_routes.json').write_text(json.dumps(out,indent=2)+'\n');print(out)
