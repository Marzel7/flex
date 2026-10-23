from pathlib import Path
import sys,os,resource,sqlite3,json,shutil,tempfile,importlib.util,subprocess,socket
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000))
socket.socket.connect=lambda *a,**k:(_ for _ in ()).throw(AssertionError('NETWORK_FORBIDDEN'))
D=Path('/Users/kevinkeaveney/.codex/worktrees/dev004-first-v2-prototype/flex');module=D/'runtime/creator_resolution/src/ops/dev004_core_base_schema.py';spec=importlib.util.spec_from_file_location('bootstrap',module);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
O=Path('/private/tmp/dev004-versioned-core-results');O.mkdir(exist_ok=True);results=[];roots=[]
def fixture(name,source=None):
 root=Path(tempfile.mkdtemp(prefix='dev004-vcore-'+name+'-',dir='/private/tmp'));roots.append(root);shutil.copy2(source or '/private/tmp/dev004-versioned-core-base/core.sqlite',root/'core.sqlite');return root

def run(root,**kw):return m.initialize(root/'core.sqlite',disposable_root=root,allow_disposable_v2=True,**kw)
def schema(root):
 c=sqlite3.connect(root/'core.sqlite');r=c.execute('select type,name,tbl_name,sql from sqlite_schema order by type,name').fetchall();c.close();return r

def expect_hold(root,reason):
 before=schema(root)
 try:run(root)
 except m.BootstrapHold as e:assert reason in str(e),(reason,str(e))
 else:raise AssertionError('HOLD not raised')
 assert schema(root)==before;results.append({'case':reason,'status':'PASS'})
root=fixture('fresh');before=schema(root);assert run(root)['status']=='INITIALIZED';c=sqlite3.connect(root/'core.sqlite');assert [r[1] for r in c.execute('pragma table_info(token_market_cap_peaks)')]==[r[1] for r in m.PEAK_COLUMNS];assert c.execute('select count(*) from token_analysis').fetchone()[0]==0;cookie=c.execute('pragma schema_version').fetchone()[0];uv=c.execute('pragma user_version').fetchone()[0];c.close();after=schema(root);assert set((r[0],r[1]) for r in after)-set((r[0],r[1]) for r in before)=={('table','token_market_cap_peaks'),('index','sqlite_autoindex_token_market_cap_peaks_1')};marker=next(root.glob('*.json'));mark=marker.read_bytes();mtime=marker.stat().st_mtime_ns;assert run(root)['status']=='ALREADY_INITIALIZED' and marker.read_bytes()==mark and marker.stat().st_mtime_ns==mtime and schema(root)==after;results.append({'case':'fresh_exact_delta_repeat_no_history','status':'PASS','user_version':uv})
canonical=root
r=fixture('compatible',canonical/'core.sqlite');before=schema(r);assert run(r)['status']=='INITIALIZED' and schema(r)==before;results.append({'case':'compatible_unmarked_schema_adopted_without_ddl','status':'PASS'})
r=fixture('partial');c=sqlite3.connect(r/'core.sqlite');c.execute('alter table token_analysis add column first_observed_at INTEGER');c.commit();c.close();run(r);results.append({'case':'compatible_partial_schema_completed','status':'PASS'})
for name,ddl,reason in [('wrongtype','alter table token_analysis add column first_observed_at TEXT','INCOMPATIBLE_COLUMN'),('default','alter table token_analysis add column create_tx_signature TEXT DEFAULT "x"','INCOMPATIBLE_COLUMN'),('wrongpeak',m.PEAK_SQL.replace('peak_market_cap REAL DEFAULT 0','peak_market_cap INTEGER DEFAULT 0'),'INCOMPATIBLE_PEAK')]:
 r=fixture(name);c=sqlite3.connect(r/'core.sqlite');c.execute(ddl);c.commit();c.close();expect_hold(r,reason)
r=fixture('missingparent');c=sqlite3.connect(r/'core.sqlite');c.execute('alter table tracked_tokens rename to unrelated_test_registry');c.commit();c.close();expect_hold(r,'TRACKED_TOKENS_OWNER_REQUIRED')
r=fixture('authorization')
try:m.initialize(r/'core.sqlite',disposable_root=r)
except m.BootstrapHold:results.append({'case':'explicit_opt_in_required','status':'PASS'})
else:raise AssertionError('no authorization guard')
# Process exits during actual transaction and after actual schema commit.
for stage in ['after_column:first_observed_at','before_commit','after_commit','before_marker_replace']:
 r=fixture('crash');child='''import resource,os,importlib.util
from pathlib import Path
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000))
s=importlib.util.spec_from_file_location('b',%r);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
def fault(stage):
 if stage==%r:os._exit(71)
m.initialize(%r,disposable_root=%r,allow_disposable_v2=True,fault=fault)
'''%(str(module),stage,str(r/'core.sqlite'),str(r));p=subprocess.run([sys.executable,'-B','-c',child],capture_output=True,text=True,timeout=10);assert p.returncode==71,(stage,p.stderr);run(r);assert schema(r)==schema(canonical);assert run(r)['status']=='ALREADY_INITIALIZED';results.append({'case':'process_crash_'+stage,'status':'PASS'})
# Existing records survive, including rows predating nullable column addition.
r=fixture('preserve');c=sqlite3.connect(r/'core.sqlite');c.execute("insert into token_analysis(mint,pf_ws_creator,created_at) values('SYNTHETIC_MINT','SYNTHETIC_CREATOR',123)");c.commit();old=c.execute('select mint,pf_ws_creator,created_at from token_analysis').fetchall();c.close();run(r);c=sqlite3.connect(r/'core.sqlite');assert c.execute('select mint,pf_ws_creator,created_at from token_analysis').fetchall()==old;assert c.execute('select first_observed_at,create_tx_signature from token_analysis').fetchone()==(None,None);c.close();results.append({'case':'existing_data_preserved_no_backfill','status':'PASS'})
# SQL preparations only; then synthetic FK/monotonic peak exercise.
c=sqlite3.connect(canonical/'core.sqlite');c.execute('pragma foreign_keys=on');c.execute('EXPLAIN SELECT first_observed_at,create_tx_signature FROM token_analysis WHERE mint=?',('test',));c.execute('EXPLAIN INSERT INTO token_market_cap_peaks(mint,peak_market_cap) values(?,?) ON CONFLICT(mint) DO UPDATE SET peak_market_cap=max(peak_market_cap,excluded.peak_market_cap)',('test',100));c.execute("insert into tracked_tokens(mint,created_at,updated_at) values('SYNTHETIC_PRICE',1,1)");c.execute("insert into token_market_cap_peaks(mint,peak_market_cap,peak_market_cap_at) values('SYNTHETIC_PRICE',100,123)");c.commit();assert c.execute('pragma foreign_key_check').fetchall()==[];plan=[r[3] for r in c.execute('EXPLAIN QUERY PLAN SELECT mint,first_observed_at,analyzed_at,created_at FROM token_analysis WHERE pf_ws_creator=? LIMIT 10001',('synthetic',))];c.close();results.append({'case':'canonical_create_price_sql_and_foreign_key','status':'PASS','creator_detail_plan':plan})
# Symlink/hardlink cannot target a different authority.
r=fixture('hardlink');os.link(r/'core.sqlite',r/'alias.sqlite');expect_hold(r,'DISPOSABLE_FILE_IDENTITY')
result={'status':'PASS','tests':results,'roots':[str(r) for r in roots],'canonical_fixture':str(canonical),'largest_file_bytes':max(p.stat().st_size for r in roots for p in r.iterdir() if p.is_file()),'real_provider_calls':0};(O/'result.json').write_text(json.dumps(result,indent=2)+'\n');print('PASS',len(results),'groups;creator plan',plan)
