from pathlib import Path
script=Path('/private/tmp/dev004-test-compact-history.py').read_text();exec(compile(script.split('for w in manifest[\'creators\']:')[0],'compact-loader','exec'))
import shutil
C=O/'classification_closure';C.mkdir(exist_ok=False)
for k in ['core','ops']:shutil.copy2(S/(k+'_v2.sqlite'),C/(k+'_v2.sqlite'))
v=sqlite3.connect(C/'ops_v2.sqlite');v.row_factory=sqlite3.Row;v.execute('pragma max_page_count=3840');expected=json.loads((D/'docs/dev/lightweight_dev004_first_v2/creator_history/classification_graph_dependencies.json').read_text());added=[];reads=[]
def indexed(sql,args):
 c=src['ops'];plan=[r[3] for r in c.execute('explain query plan '+sql,args)];assert all('SCAN ' not in p.upper() for p in plan);rows=[dict(r) for r in c.execute(sql,args)];assert len(rows)<65;reads.append({'sql':sql,'plan':plan,'args':args,'rows':rows});return rows
def insert(t,r):
 pk=[x[1] for x in v.execute('pragma table_info('+t+')') if x[5]];old=v.execute('select * from '+t+' where '+' and '.join(k+'=?' for k in pk),[r[k] for k in pk]).fetchone()
 if old:assert dict(old)==r,(t,'preexisting contradiction');return
 v.execute('insert into '+t+' ('+','.join(r)+') values ('+','.join('?' for _ in r)+')',list(r.values()));added.append({'table':t,'row':r})
for case in expected:
 rows=indexed('select * from wt_ops_v2_wallets where wallet=? limit 65',(case['creator'],));assert rows==case['source_graph_rows']
 for r in rows:
  op=indexed('select * from wt_ops_v2 where operation_uuid=?',(r['operation_uuid'],));assert op==case['source_operations'];insert('wt_ops_v2',op[0]);insert('wt_ops_v2_wallets',r)
  root=indexed('select * from wt_ops_v2_wallets where operation_uuid=? and wallet=?',(r['operation_uuid'],op[0]['treasury_root']))
  for t in root:insert('wt_ops_v2_wallets',t)
v.commit();v.close()
# Exactly one new bounded first-match attempt; no index or historical data change.
TARGET='SELECT creator, matched_subprov, matched_treasury FROM watchtower_token_attribution WHERE creator=? LIMIT 1';budget={'max_vm_instructions':100000,'callback_granularity':100,'max_seconds':.5,'instructions_at_last_callback':0}
class Oracle(Bounded):
 def execute(self,sql,args=()):
  if sql!=TARGET:return super().execute(sql,args)
  deadline=time.monotonic()+.5
  def stop():
   budget['instructions_at_last_callback']+=100
   return int(budget['instructions_at_last_callback']>=100000 or time.monotonic()>deadline)
  self.set_progress_handler(stop,100);return sqlite3.Connection.execute(self,sql,args)
src['ops'].close();src['ops']=sqlite3.connect('file:/Users/kevinkeaveney/Dev/claude/flex/database/wt_ops_v2.db?mode=ro',uri=True,factory=Oracle);src['ops'].row_factory=sqlite3.Row;src['ops'].execute('pragma query_only=on');prior=json.loads((O/'equivalence.json').read_text());NOW=prior['anchor_unix'];unknown=next(x for x in prior['cases'] if x['classification_equal'] is None)
try:oracle=classify(legacy,src,unknown['creator'],False);oracle_result={'creator':unknown['creator'],'classification':oracle,'status':'PASS_BOUNDED_EXACT_INSTALLED_QUERY','budget':budget}
except sqlite3.Error as e:oracle_result={'creator':unknown['creator'],'status':'HOLD','error':str(e),'budget':budget}
v2={k:conn(C/(k+'_v2.sqlite')) for k in ['core','ops']};comparisons=[]
for x in prior['cases']:
 w=x['creator'];os.environ['DEV004_CREATOR_HISTORY_PATH']=str(O/'history.json');profile=dev['evaluate_launcher_profile'](v2['ops'],v2['core'],w,now=NOW);assert profile==x['v1_profile'];actual=classify(dev,v2,w,True);ref=oracle_result.get('classification') if w==unknown['creator'] else x.get('v1_class');same=actual==ref if ref is not None else None
 if ref is not None:assert same,(w,actual,ref)
 comparisons.append({'creator':w,'v1_class':ref,'v2_class':actual,'classification_exact':same,'history_profile_exact':True})
result={'status':'PASS' if all(x['classification_exact'] for x in comparisons) else 'PARTIAL_ORACLE_HOLD','added_reference_rows':added,'source_indexed_reads':reads,'oracle':oracle_result,'comparisons':comparisons,'exact_classifications':sum(x['classification_exact'] is True for x in comparisons),'exact_profiles':100,'source_seed_hashes':{n:hashlib.sha256((S/n).read_bytes()).hexdigest() for n in ['core_v2.sqlite','ops_v2.sqlite']},'sizes':{n:(C/n).stat().st_size for n in ['core_v2.sqlite','ops_v2.sqlite']}}
(C/'result.json').write_text(json.dumps(result,indent=2)+'\n');print('status',result['status'],'added rows',len(added),'exact classifications',result['exact_classifications']);print(json.dumps(oracle_result,indent=2))
